#!/usr/bin/env python3
"""
Sync every dashboards/*.json to Grafana via the HTTP API.

Run from CI (GitHub Actions) so a `git push` updates Grafana with no manual
import. Standard library only — nothing to install in the runner.

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
import urllib.error
import urllib.request
from pathlib import Path

GRAFANA_URL = os.environ.get("GRAFANA_URL", "").rstrip("/")
TOKEN = os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN", "")
DASH_DIR = Path(os.environ.get("DASH_DIR", "dashboards"))
FOLDER_UID = os.environ.get("GRAFANA_FOLDER_UID", "")

if not GRAFANA_URL or not TOKEN:
    sys.exit("FATAL: set GRAFANA_URL and GRAFANA_SERVICE_ACCOUNT_TOKEN env vars")

PLACEHOLDER = re.compile(r"^\$\{DS_([A-Z0-9_]+)\}$")


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        GRAFANA_URL + path,
        method=method,
        data=data,
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"error": raw}


def load_datasources():
    status, data = api("GET", "/api/datasources")
    if status != 200:
        sys.exit(f"FATAL: could not list datasources ({status}): {data}")
    return [
        {"name": d.get("name", ""), "type": d.get("type", ""), "uid": d.get("uid", "")}
        for d in data
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


def main():
    datasources = load_datasources()
    print(
        "Datasources: "
        + ", ".join(f"{d['name']}({d['type']})={d['uid']}" for d in datasources)
    )

    files = sorted(DASH_DIR.glob("*.json"))
    if not files:
        sys.exit(f"FATAL: no dashboards found in {DASH_DIR}")

    pushed = skipped = failed = 0
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

        body = {
            "dashboard": dash,
            "folderUid": FOLDER_UID,
            "overwrite": True,
            "message": "synced from git by vps-monitoring CI",
        }
        status, resp = api("POST", "/api/dashboards/db", body)
        if status == 200 and resp.get("status") == "success":
            pushed += 1
            print(f"OK    {f.name} -> uid={uid} ({title}) v{resp.get('version')}")
        else:
            failed += 1
            print(f"FAIL  {f.name} [{status}] ({title}): {resp}")

    print(f"\nSummary: {pushed} pushed, {skipped} skipped, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
