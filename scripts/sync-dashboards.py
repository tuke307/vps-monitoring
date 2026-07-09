#!/usr/bin/env python3
"""
Sync every dashboards/*.json to Grafana via the HTTP API (grafana-client).

Run from CI (GitHub Actions) so a `git push` updates Grafana with no manual
import. Dependencies are managed with `uv` (see pyproject.toml / uv.lock);
in CI: `uv sync --frozen` then `uv run python scripts/sync-dashboards.py`.

Per dashboard it:
  1. Strips the manual-import wrappers (`__inputs`, `__requires`).
  2. Resolves every `${DS_<NAME>}` datasource placeholder to a real datasource
     UID, by matching <NAME> against each datasource's name OR type
     (case-insensitive). So `${DS_LOKI}` -> the `loki`-type datasource and
     `${DS_PROMETHEUS}` -> the `prometheus`-type datasource.
  3. POSTs to /api/dashboards/db with overwrite=true (matched by `uid`).

Dashboards with an unresolved placeholder or no stable `uid` are skipped
(not pushed) so the job never creates broken or duplicate dashboards.

Required env:
  GRAFANA_URL                    e.g. https://grafana.example.com
  GRAFANA_SERVICE_ACCOUNT_TOKEN  token with Editor/Admin role
Optional env:
  DASH_DIR            dashboards dir (default: dashboards)
  GRAFANA_FOLDER_UID  folder UID to place dashboards in (default: "" = General)
"""
import json
import os
import re
import sys
import warnings
from pathlib import Path

# grafana-client 5.1.0 ships a harmless SyntaxWarning in datasource.py;
# silence it so it doesn't clutter CI logs.
warnings.filterwarnings("ignore", category=SyntaxWarning)

from grafana_client import GrafanaApi, TokenAuth
from grafana_client.client import GrafanaException

GRAFANA_URL = os.environ.get("GRAFANA_URL", "").rstrip("/")
TOKEN = os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN", "")
DASH_DIR = Path(os.environ.get("DASH_DIR", "dashboards"))
FOLDER_UID = os.environ.get("GRAFANA_FOLDER_UID", "")
TIMEOUT = 30

if not GRAFANA_URL or not TOKEN:
    sys.exit("FATAL: set GRAFANA_URL and GRAFANA_SERVICE_ACCOUNT_TOKEN env vars")

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


def resolve_placeholder(name, datasources):
    """Resolve a placeholder suffix (e.g. LOKI) to a datasource uid, or None."""
    needle = name.lower()
    for ds in datasources:
        if ds["name"].lower() == needle or ds["type"].lower() == needle:
            return ds["uid"]
    return None


def transform(dash, datasources):
    """Strip import wrappers and resolve ${DS_*} placeholders in place."""
    dash.pop("__inputs", None)
    dash.pop("__requires", None)
    unresolved = []

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str):
                    m = PLACEHOLDER.match(v)
                    if m:
                        uid = resolve_placeholder(m.group(1), datasources)
                        if uid:
                            obj[k] = uid
                        else:
                            unresolved.append(m.group(0))
                else:
                    walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(dash)
    dash["id"] = None  # upsert by uid, let Grafana track the numeric id
    return dash, unresolved


def diagnose(grafana, payload):
    """Run targeted probes to locate the 403.

    The reverse proxy strips X-Grafana-Version from every response, so header
    presence alone can't separate a Grafana 403 from a proxy one. These three
    controls can:
      * GET  /api/user        -> the token's orgRole (is it really Editor?).
      * POST /api/health      -> control: Grafana never answers this with a
                                 bare '403 {}'. If it does, a proxy/WAF is
                                 blocking POST requests before Grafana.
      * POST /api/dashboards/db -> the actual failing write.
    """
    probes = (
        ("GET  /api/user", "GET", "/api/user", {}),
        ("POST /api/health", "POST", "/api/health", {}),
        ("POST /api/dashboards/db", "POST", "/api/dashboards/db",
         {"json": payload}),
    )
    results = []
    for label, method, url, kwargs in probes:
        try:
            resp = grafana.client.s.request(
                method,
                GRAFANA_URL + url,
                auth=TokenAuth(token=TOKEN),
                timeout=TIMEOUT,
                **kwargs,
            )
            results.append(
                (label, resp.status_code, dict(resp.headers), (resp.text or "")[:300])
            )
        except Exception as e:  # diagnostic only — never crash the run
            results.append((label, None, {}, f"<probe request failed: {e}>"))
    return results


def permission_hint(folder_uid):
    """How to read the probe output + the likely fixes."""
    folder_note = (
        f"GRAFANA_FOLDER_UID='{folder_uid}' is set — make sure that folder "
        "grants the service account Edit, or unset it to use General."
        if folder_uid
        else "GRAFANA_FOLDER_UID is unset (General folder)."
    )
    return (
        "Read the three probes above to locate the 403:\n"
        "  * GET /api/user body shows orgRole  ->  if it's Viewer, the token\n"
        "    is NOT Editor (recreate it as Editor / check the org).\n"
        "  * POST /api/health  ->  control. Grafana never answers this with a\n"
        "    bare '403 {}'. If it DOES (same as the dashboard POST), a REVERSE\n"
        "    PROXY / WAF in front of Grafana is blocking POST requests (the\n"
        "    'alt-svc: h3' header points at your Coolify Caddy layer). Fix it\n"
        "    there: check the service's proxy config for a deny/block/rate-limit\n"
        "    on POST, allowlist the GitHub Actions IPs, or point GRAFANA_URL at\n"
        "    an origin not behind the proxy.\n"
        "  * POST /api/health is NOT 403 (e.g. 404/405/200 with a body)  ->\n"
        "    POSTs DO reach Grafana, so the dashboard 403 is Grafana denying\n"
        "    the write. Then check, in order: " + folder_note + "; a per-dashboard\n"
        "    ACL on dashboards imported by hand earlier (delete them once so\n"
        "    this job recreates them); and the token's actual role."
    )


def main():
    grafana = connect()
    datasources = load_datasources(grafana)
    print(
        "Datasources: "
        + ", ".join(f"{d['name']}({d['type']})={d['uid']}" for d in datasources)
    )

    files = sorted(DASH_DIR.glob("*.json"))
    if not files:
        sys.exit(f"FATAL: no dashboards found in {DASH_DIR}")

    pushed = skipped = failed = 0
    printed_probe = False
    for f in files:
        dash = json.loads(f.read_text())
        uid = dash.get("uid")
        title = dash.get("title", f.name)

        if not uid:
            skipped += 1
            print(f"SKIP  {f.name}: no top-level uid (would duplicate on every run)")
            continue

        dash, unresolved = transform(dash, datasources)
        if unresolved:
            skipped += 1
            print(
                f"SKIP  {f.name} ({title}): unresolved placeholder(s) "
                f"{sorted(set(unresolved))}"
            )
            continue

        payload = {
            "dashboard": dash,
            "folderUid": FOLDER_UID,
            "overwrite": True,
            "message": "synced from git by vps-monitoring CI",
        }
        try:
            result = grafana.dashboard.update_dashboard(dashboard=payload)
        except GrafanaException as e:
            failed += 1
            if not printed_probe:
                printed_probe = True
                print()
                print(permission_hint(FOLDER_UID))
                for label, code, headers, body in diagnose(grafana, payload):
                    print(f"Probe {label} [{code}] body={body!r}")
                    if label.startswith("POST /api/dashboards"):
                        print(f"Probe {label} headers={headers}")
            detail = e.response or e.message or "(empty response body)"
            print(f"FAIL  {f.name} [{e.status_code}] ({title}): {detail}")
            continue
        except OSError as e:
            failed += 1
            print(
                f"FAIL  {f.name} ({title}): connection error: "
                f"{type(e).__name__}: {e}"
            )
            continue

        pushed += 1
        version = result.get("version") if isinstance(result, dict) else None
        print(f"OK    {f.name} -> uid={uid} ({title}) v{version}")

    print(f"\nSummary: {pushed} pushed, {skipped} skipped, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
