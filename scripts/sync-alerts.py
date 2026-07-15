#!/usr/bin/env python3
"""
Sync alerts/rules/*.json to Grafana's alerting provisioning HTTP API (grafana-client).

Run from CI (GitHub Actions) so a `git push` updates Grafana alert rules with no
manual UI work. Dependencies are managed with `uv`; in CI: `uv sync --frozen` then
`uv run python scripts/sync-alerts.py`.

This is the alerting analogue of sync-dashboards.py. It is RULES ONLY -- contact
points and notification policies are managed in the Grafana UI (an existing email
contact point and default policy); the synced rules route through whatever default
policy is already configured there. Like sync-dashboards.py it is UPSERT ONLY --
it never deletes (remove a rule from git and it stays in Grafana until deleted by
hand). Rules are pushed with provenance "api", so they are read-only in the Grafana
UI (git is the source of truth).

The ${DS_<NAME>} placeholder is resolved to a real datasource uid (matched by
datasource name OR type) before push, so the JSON stays portable -- same scheme as
the ${DS_*} placeholders in dashboards/.

Required env:
  GRAFANA_URL                    e.g. https://grafana.example.com
  GRAFANA_SERVICE_ACCOUNT_TOKEN  token with alert.rules:write (Admin, or a role
                                 granting that scope). dashboards:write alone is
                                 NOT enough -- every call returns 403.
  GRAFANA_FOLDER_UID             a REAL Grafana folder UID. Used as the folder for
                                 any rule file that does not specify its own
                                 folderUID. Can be empty only if every rule file
                                 carries its own folderUID.
Optional env:
  ALERTS_DIR        base alerts dir (default: alerts); rules read from <dir>/rules/
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
ALERTS_DIR = Path(os.environ.get("ALERTS_DIR", "alerts"))
TIMEOUT = 30

if not GRAFANA_URL or not TOKEN:
    sys.exit("FATAL: set GRAFANA_URL and GRAFANA_SERVICE_ACCOUNT_TOKEN env vars")

# Whole-value placeholder: ${DS_<NAME>}
PLACEHOLDER = re.compile(r"^\$\{DS_([A-Z0-9_]+)\}$")


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


def resolve_placeholders(obj, datasources):
    """Replace whole-value ${DS_*} placeholders in place; return list of unresolved."""
    unresolved = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, str):
                    m = PLACEHOLDER.match(v)
                    if m:
                        uid = resolve_datasource(m.group(1), datasources)
                        if uid is not None:
                            o[k] = uid
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
    """Append a permission hint to 401/403 failures (missing alert.rules:write)."""
    if e.status_code in (401, 403):
        return (
            "  [hint: token lacks alert.rules:write -- use an Admin service account "
            "token, or grant the alerting rules write scope; dashboards:write alone "
            "is not enough]"
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
        # already carry their own folderUID and keep it (do not overwrite).
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


def main():
    grafana = connect()
    datasources = load_datasources(grafana)
    print(
        "Datasources: "
        + ", ".join(f"{d['name']}({d['type']})={d['uid']}" for d in datasources)
    )

    counts = {"pushed": 0, "skipped": 0, "failed": 0}
    sync_rules(grafana, datasources, counts)

    print(
        f"\nSummary: {counts['pushed']} pushed, {counts['skipped']} skipped, "
        f"{counts['failed']} failed"
    )
    if counts["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
