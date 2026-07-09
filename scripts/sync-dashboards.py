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
from pathlib import Path

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


def probe_write(grafana, payload):
    """Re-send one POST through grafana-client's own niquests session.

    grafana-client raises on HTTP errors but discards the response headers,
    which are exactly what distinguishes a Grafana 403 (X-Grafana-Version +
    JSON message) from a 403 returned by a reverse proxy / WAF in front of
    Grafana (Server/Via/CF-Ray, empty or HTML body). This probe gets them back.
    """
    try:
        resp = grafana.client.s.post(
            GRAFANA_URL + "/api/dashboards/db",
            json=payload,
            auth=TokenAuth(token=TOKEN),
            timeout=TIMEOUT,
        )
        return resp.status_code, dict(resp.headers), resp.text or ""
    except Exception as e:  # diagnostic only — never crash the run
        return None, {}, f"<probe request failed: {e}>"


def permission_hint(folder_uid):
    """How to read the probe output + the likely fixes."""
    folder_note = (
        f"GRAFANA_FOLDER_UID='{folder_uid}' is set — make sure that folder "
        "grants the service account Edit, or unset it to use General."
        if folder_uid
        else "GRAFANA_FOLDER_UID is unset (General folder)."
    )
    return (
        "Write failed while read still works. grafana-client drops the response\n"
        "headers, so a raw probe was sent too — read 'Probe [...] headers' below:\n"
        "  * Headers contain 'x-grafana-version' + a JSON body  ->  Grafana itself\n"
        "    is denying the write. Then check, in order: " + folder_note + ";\n"
        "    a per-dashboard ACL on dashboards imported by hand earlier (delete\n"
        "    them once so this job recreates them); or the token's role.\n"
        "  * Headers show 'server'/'cf-ray'/'via'/'x-served-by' and the body is\n"
        "    empty or HTML  ->  a REVERSE PROXY / WAF (e.g. Cloudflare in front\n"
        "    of Grafana/Coolify) is blocking the POST. Fix: allowlist the GitHub\n"
        "    Actions IP ranges, bypass the WAF for the API path, or point\n"
        "    GRAFANA_URL at an origin that is not behind the WAF."
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
                code, headers, body = probe_write(grafana, payload)
                print()
                print(permission_hint(FOLDER_UID))
                print(f"Probe [{code}] headers={headers}")
                print(f"Probe body: {body!r}")
            detail = e.response or e.message or "(empty response body)"
            print(f"FAIL  {f.name} [{e.status_code}] ({title}): {detail}")
            continue

        pushed += 1
        version = result.get("version") if isinstance(result, dict) else None
        print(f"OK    {f.name} -> uid={uid} ({title}) v{version}")

    print(f"\nSummary: {pushed} pushed, {skipped} skipped, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
