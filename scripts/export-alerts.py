#!/usr/bin/env python3
"""
One-shot: export existing Grafana alert rules into alerts/rules/*.json so git
becomes the source of truth.

Use this when you already have alert rules in the Grafana UI and want to bring
them under CI management WITHOUT creating duplicates (the CI sync upserts by
uid, so adopting the real uids means the same rules get updated, not doubled).

Run it ONCE locally (with the same env as sync-alerts.py), review + commit the
generated files, then let sync-alerts.py own them. After the next sync the rules
are re-tagged provenance "api" (read-only in the UI) -- edit them in git from
then on.

For portability, the Prometheus datasource UID in each rule is rewritten back to
the ${DS_PROMETHEUS} placeholder (sync-alerts.py resolves it again on push), so
the committed files are not tied to a specific datasource UID.

Required env (same as sync-alerts.py):
  GRAFANA_URL                    e.g. https://grafana.example.com
  GRAFANA_SERVICE_ACCOUNT_TOKEN  token with alert.rules:read
Optional env:
  ALERTS_DIR    base alerts dir (default: alerts); rules written to <dir>/rules/
"""
import json
import os
import re
import sys
import warnings
from pathlib import Path

# grafana-client 5.1.0 emits a harmless SyntaxWarning from datasource.py.
warnings.filterwarnings("ignore", category=SyntaxWarning)

from grafana_client import GrafanaApi, TokenAuth
from grafana_client.client import GrafanaException

GRAFANA_URL = os.environ.get("GRAFANA_URL", "").rstrip("/")
TOKEN = os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN", "")
ALERTS_DIR = Path(os.environ.get("ALERTS_DIR", "alerts"))
TIMEOUT = 30

if not GRAFANA_URL or not TOKEN:
    sys.exit("FATAL: set GRAFANA_URL and GRAFANA_SERVICE_ACCOUNT_TOKEN env vars")

# Server-managed fields Grafana populates itself; drop them so the file is a
# clean input that round-trips through sync-alerts.py.
DROP = {"id", "updated", "created", "provenance", "notification_settings", "record"}


def slugify(s):
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "rule"


def main():
    grafana = GrafanaApi.from_url(
        GRAFANA_URL, credential=TokenAuth(token=TOKEN), timeout=TIMEOUT
    )

    # Need datasource UIDs to rewrite the Prometheus uid back to a placeholder.
    try:
        dss = grafana.datasource.list_datasources()
    except GrafanaException as e:
        sys.exit(f"FATAL: could not list datasources [{e.status_code}]: {e.response}")
    except OSError as e:
        sys.exit(
            f"FATAL: cannot reach Grafana at {GRAFANA_URL}: "
            f"{type(e).__name__}: {e}"
        )
    prom_uids = {d.get("uid") for d in dss if d.get("type") == "prometheus"}
    print(
        "Prometheus datasource UIDs: "
        + (", ".join(sorted(prom_uids)) or "(none found)")
    )

    try:
        rules = grafana.alertingprovisioning.get_alertrules_all()
    except GrafanaException as e:
        sys.exit(f"FATAL: could not list alert rules [{e.status_code}]: {e.response}")

    out = ALERTS_DIR / "rules"
    out.mkdir(parents=True, exist_ok=True)

    n = 0
    for r in rules:
        uid = r.get("uid")
        title = r.get("title", "rule")
        clean = {k: v for k, v in r.items() if k not in DROP}
        # Make the file portable: real Prometheus UID -> ${DS_PROMETHEUS}.
        for node in clean.get("data", []):
            if node.get("datasourceUid") in prom_uids:
                node["datasourceUid"] = "${DS_PROMETHEUS}"
        fname = slugify(uid or title) + ".json"
        (out / fname).write_text(json.dumps(clean, indent=2) + "\n")
        print(
            f"OK    {fname} <- uid={uid} ({title}) "
            f"[provenance={r.get('provenance', '?')}]"
        )
        n += 1

    print(f"\nExported {n} rule(s) to {out}/.")
    print(
        "Next: review + `git add alerts/rules`, commit, then run sync-alerts.py "
        "(CI or local). The existing UI rules will be adopted (same uids) and "
        "become read-only in the Grafana UI."
    )


if __name__ == "__main__":
    main()
