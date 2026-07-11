#!/usr/bin/env python3
"""
Sync alerts/*.json to Grafana's alerting provisioning HTTP API (grafana-client).

Run from CI (GitHub Actions) so a `git push` updates Grafana alert rules -- and,
optionally, the email contact point and notification policy -- with no manual UI
work. Dependencies are managed with `uv`; in CI: `uv sync --frozen` then
`uv run python scripts/sync-alerts.py`.

What it syncs (relative to ALERTS_DIR, default `alerts`):
  - rules/*.json              -> alert rules (upsert by `uid`). ALWAYS synced.
  - contact-points.json       -> email contact point (upsert by `uid`). ONLY when
                                 ALERT_EMAIL_TO is set.
  - notification-policy.json  -> root notification policy (PUT, overwrites). ONLY
                                 when ALERT_EMAIL_TO is set.

This is the alerting analogue of sync-dashboards.py. Like that script it is
UPSERT ONLY -- it never deletes (remove a rule from git and it stays in Grafana
until deleted by hand). Resources are pushed with provenance "api", so they are
read-only in the Grafana UI (git is the source of truth).

Reusing a contact point you already created in the Grafana UI? Leave ALERT_EMAIL_TO
UNSET. Then only the rules are synced, your existing contact points and
notification policy are left untouched, and the new rules route through whatever
default policy you already have.

Placeholders resolved before push (whole-value match, same scheme as dashboards):
  ${DS_<NAME>}      -> real datasource uid (matched by datasource name OR type)
  ${ALERT_EMAIL_TO} -> comma-separated inbox(es) from env (contact points only)

Required env:
  GRAFANA_URL                    e.g. https://grafana.example.com
  GRAFANA_SERVICE_ACCOUNT_TOKEN  token with alert.rules:write (+ optionally
                                 alert.notifications:write if you also let CI
                                 manage the contact point / policy). Admin, or a
                                 role granting those scopes. dashboards:write
                                 alone is NOT enough -- every call returns 403.
  GRAFANA_FOLDER_UID             a REAL Grafana folder UID. Used as the folder for
                                 any rule file that does not specify its own
                                 folderUID (e.g. the greenfield rules/*.json). Can
                                 be empty only if every rule file carries its own
                                 folderUID.
Optional env:
  ALERTS_DIR        base alerts dir (default: alerts)
  ALERT_EMAIL_TO    recipients for the CI-managed email contact point. If SET, CI
                    also provisions the contact point and points the root policy
                    at it (greenfield setup). If UNSET, CI manages ONLY the alert
                    rules and leaves your existing contact points / notification
                    policy untouched.
"""
import json
import os
import re
import sys
import warnings
from pathlib import Path

# grafana-client 5.1.0 emits a harmless SyntaxWarning from datasource.py;
# silence it so it doesn't clutter CI logs.
warnings.filterwarnings("ignore", category=SyntaxWarning)

from grafana_client import GrafanaApi, TokenAuth
from grafana_client.client import GrafanaException

GRAFANA_URL = os.environ.get("GRAFANA_URL", "").rstrip("/")
TOKEN = os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN", "")
FOLDER_UID = os.environ.get("GRAFANA_FOLDER_UID", "")
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", "").strip()
ALERTS_DIR = Path(os.environ.get("ALERTS_DIR", "alerts"))
TIMEOUT = 30

if not GRAFANA_URL or not TOKEN:
    sys.exit("FATAL: set GRAFANA_URL and GRAFANA_SERVICE_ACCOUNT_TOKEN env vars")

# Whole-value placeholder: ${ANY_CAPS_TOKEN}
PLACEHOLDER = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


def connect():
    return GrafanaApi.from_url(
        GRAFANA_URL, credential=TokenAuth(token=TOKEN), timeout=TIMEOUT
    )


def load_datasources(grafana):
    try:
        dss = grafana.datasource.list_datasources()
    except GrafanaException as e:
        sys.exit(f"FATAL: could not list datasources [{e.status_code}]: {e.response}")
    except OSError as e:
        sys.exit(
            f"FATAL: cannot reach Grafana at {GRAFANA_URL}: "
            f"{type(e).__name__}: {e}"
        )
    return [
        {"name": d.get("name", ""), "type": d.get("type", ""), "uid": d.get("uid", "")}
        for d in dss
    ]


def resolve_datasource(name, datasources):
    """Resolve a DS suffix (e.g. PROMETHEUS) to a datasource uid, or None."""
    needle = name.lower()
    for ds in datasources:
        if ds["name"].lower() == needle or ds["type"].lower() == needle:
            return ds["uid"]
    return None


def resolve_token(tok, datasources):
    """Map a placeholder body (e.g. DS_PROMETHEUS / ALERT_EMAIL_TO) to its value."""
    if tok.startswith("DS_"):
        return resolve_datasource(tok[3:], datasources)
    if tok == "ALERT_EMAIL_TO":
        return ALERT_EMAIL_TO or None
    return None


def resolve_placeholders(obj, datasources):
    """Replace whole-value ${...} placeholders in place; return list of unresolved."""
    unresolved = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, str):
                    m = PLACEHOLDER.match(v)
                    if m:
                        val = resolve_token(m.group(1), datasources)
                        if val is not None:
                            o[k] = val
                        else:
                            unresolved.append(m.group(0))
                else:
                    walk(v)
        elif isinstance(o, list):
            for item in o:
                walk(item)

    walk(obj)
    return unresolved


def hint(e):
    """Append a permission hint to 401/403 failures (missing alerting scopes)."""
    if e.status_code in (401, 403):
        return (
            "  [hint: token lacks alert.rules:write / alert.notifications:write -- "
            "use an Admin service account token, or grant the alerting write scopes; "
            "dashboards:write alone is not enough]"
        )
    return ""


def sync_rules(grafana, datasources, counts):
    rule_dir = ALERTS_DIR / "rules"
    files = sorted(rule_dir.glob("*.json")) if rule_dir.is_dir() else []
    if not files:
        print(f"FATAL: no alert rules found in {rule_dir}")
        counts["failed"] += 1
        return

    try:
        existing = {
            r.get("uid")
            for r in grafana.alertingprovisioning.get_alertrules_all()
            if r.get("uid")
        }
    except GrafanaException as e:
        print(
            f"FATAL: could not list alert rules [{e.status_code}]: "
            f"{e.response}{hint(e)}"
        )
        counts["failed"] += 1
        return

    for f in files:
        rule = json.loads(f.read_text())
        uid = rule.get("uid")
        title = rule.get("title", f.name)
        if not uid:
            counts["skipped"] += 1
            print(f"SKIP  {f.name}: no top-level uid (cannot upsert)")
            continue

        # Greenfield rule files omit folderUID -> take it from env. Exported rules
        # already carry their own folderUID and keep it (setdefault does not overwrite).
        if not rule.get("folderUID"):
            if not FOLDER_UID:
                counts["skipped"] += 1
                print(
                    f"SKIP  {f.name} ({title}): no folderUID in file and "
                    f"GRAFANA_FOLDER_UID is unset (alert rules need a real folder)"
                )
                continue
            rule["folderUID"] = FOLDER_UID

        unresolved = resolve_placeholders(rule, datasources)
        if unresolved:
            counts["skipped"] += 1
            print(
                f"SKIP  {f.name} ({title}): unresolved placeholder(s) "
                f"{sorted(set(unresolved))}"
            )
            continue

        try:
            if uid in existing:
                grafana.alertingprovisioning.update_alertrule(uid, rule)
                action = "updated"
            else:
                grafana.alertingprovisioning.create_alertrule(rule)
                action = "created"
        except GrafanaException as e:
            counts["failed"] += 1
            detail = e.response or e.message or "(empty response body)"
            print(f"FAIL  {f.name} [{e.status_code}] ({title}): {detail}{hint(e)}")
            continue
        except OSError as e:
            counts["failed"] += 1
            print(
                f"FAIL  {f.name} ({title}): connection error: "
                f"{type(e).__name__}: {e}"
            )
            continue

        counts["pushed"] += 1
        print(f"OK    {f.name} -> uid={uid} ({title}) [{action}]")


def sync_contactpoints(grafana, datasources, counts):
    f = ALERTS_DIR / "contact-points.json"
    if not f.is_file():
        print(f"NOTE  {f} not found -- skipping contact points")
        return
    if not ALERT_EMAIL_TO:
        print(
            "NOTE  ALERT_EMAIL_TO unset -- skipping contact points "
            "(reusing the contact point already configured in Grafana)"
        )
        return

    cps = json.loads(f.read_text())
    try:
        existing = {
            c.get("uid")
            for c in grafana.alertingprovisioning.get_contactpoints()
            if c.get("uid")
        }
    except GrafanaException as e:
        print(f"FAIL  contact-points [{e.status_code}]: {e.response}{hint(e)}")
        counts["failed"] += 1
        return

    for cp in cps:
        uid = cp.get("uid")
        name = cp.get("name", uid)
        if not uid:
            counts["skipped"] += 1
            print(f"SKIP  contact point {name or '?'}: no uid")
            continue
        unresolved = resolve_placeholders(cp, datasources)
        if unresolved:
            counts["skipped"] += 1
            print(
                f"SKIP  contact point {name}: unresolved placeholder(s) "
                f"{sorted(set(unresolved))}"
            )
            continue
        try:
            if uid in existing:
                grafana.alertingprovisioning.update_contactpoint(uid, cp)
                action = "updated"
            else:
                grafana.alertingprovisioning.create_contactpoint(cp)
                action = "created"
        except GrafanaException as e:
            counts["failed"] += 1
            detail = e.response or e.message or "(empty response body)"
            print(f"FAIL  contact point {name} [{e.status_code}]: {detail}{hint(e)}")
            continue
        except OSError as e:
            counts["failed"] += 1
            print(
                f"FAIL  contact point {name}: connection error: "
                f"{type(e).__name__}: {e}"
            )
            continue
        counts["pushed"] += 1
        print(f"OK    contact point {name} ({cp.get('type')}) [{action}]")


def sync_policy(grafana, counts):
    f = ALERTS_DIR / "notification-policy.json"
    if not f.is_file():
        print(f"NOTE  {f} not found -- skipping notification policy")
        return
    if not ALERT_EMAIL_TO:
        print(
            "NOTE  ALERT_EMAIL_TO unset -- skipping notification policy "
            "(your existing policy is left intact)"
        )
        return
    policy = json.loads(f.read_text())
    try:
        grafana.alertingprovisioning.set_notification_policy_tree(policy)
    except GrafanaException as e:
        counts["failed"] += 1
        detail = e.response or e.message or "(empty response body)"
        print(f"FAIL  notification policy [{e.status_code}]: {detail}{hint(e)}")
        return
    except OSError as e:
        counts["failed"] += 1
        print(
            f"FAIL  notification policy: connection error: "
            f"{type(e).__name__}: {e}"
        )
        return
    counts["pushed"] += 1
    print(f"OK    notification policy -> receiver={policy.get('receiver', '?')} [set]")


def main():
    grafana = connect()
    datasources = load_datasources(grafana)
    print(
        "Datasources: "
        + ", ".join(f"{d['name']}({d['type']})={d['uid']}" for d in datasources)
    )

    counts = {"pushed": 0, "skipped": 0, "failed": 0}
    sync_rules(grafana, datasources, counts)
    sync_contactpoints(grafana, datasources, counts)
    sync_policy(grafana, counts)

    print(
        f"\nSummary: {counts['pushed']} pushed, {counts['skipped']} skipped, "
        f"{counts['failed']} failed"
    )
    if counts["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
