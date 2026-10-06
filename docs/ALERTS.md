# Alerting

Alerts are managed by **Grafana**, and the alert **rules** are synced as code from this repo.
Grafana's built-in alert scheduler queries Prometheus directly, evaluates rules, and routes
firing alerts to a **contact point** (email). No extra Alertmanager container is needed.

```
node-exporter ──> Prometheus ──> Grafana alert rule ──> contact point ──> your inbox
                                         ▲
                          alerts/rules/*.json  └── pushed by CI (see below)
```

`scripts/sync-alerts.py` (run by `.github/workflows/sync-grafana-alerts.yml` on every push to
`main` under `alerts/**`) upserts the alert rules via Grafana's alerting provisioning API. Rules
are matched by `uid` and tagged `provenance: api`, so they are **read-only in the Grafana UI** —
edit them in git, not in Grafana. The sync is **upsert-only**: it never deletes, so removing a
rule from git leaves the old one in Grafana until you delete it by hand.

The sync manages **rules only**. Contact points and the notification policy stay in the Grafana
UI (e.g. an existing email contact point and the default policy) — the synced rules route through
your default policy.

---

## 1. Enable email (SMTP) on the Grafana resource

Grafana sends mail through its own SMTP config, set via environment variables on the
**Grafana Coolify resource** (it is a separate deployment from this stack). The app services
already use SMTP, so reuse those credentials.

In Coolify → the Grafana resource → **Environment Variables**, add:

| Variable | Value |
|---|---|
| `GF_SMTP_ENABLED` | `true` |
| `GF_SMTP_HOST` | `<SMTP_HOST>:<SMTP_PORT>` (e.g. `smtp.example.com:587`) |
| `GF_SMTP_USER` | `<SMTP_USER>` |
| `GF_SMTP_PASSWORD` | `<SMTP_PASS>` |
| `GF_SMTP_FROM_ADDRESS` | `<EMAIL_FROM>` (e.g. `grafana@example.com`) |
| `GF_SMTP_FROM_NAME` | `Grafana Alerts` |
| `GF_SMTP_SKIP_VERIFY` | `false` (set `true` only for a self-signed SMTP cert) |

Then **Redeploy** the Grafana resource. Verify: **Alerting → Contact points → <yours> → Test**
should send a test mail within a few seconds.

---

## 2. Give the CI service account alert permissions

The dashboard sync token only needs `dashboards:write`. The alerting provisioning endpoints
additionally require **`alert.rules:write`** — otherwise every push returns 403.

In Grafana: **Administration → Users and access → Service accounts** → open the account used for
the dashboard sync → set role **Admin** (simplest), or grant `alert.rules:write` → **Add service
account token** (or re-issue the existing one) → copy it.

Then in the GitHub repo **Settings → Secrets and variables → Actions**:
- **Variables** (*New repository variable*): `GRAFANA_URL`; `GRAFANA_FOLDER_UID` (a **real**
  folder UID — alert rules cannot live in General; can be unset only if every rule file carries
  its own `folderUID`).
- **Secrets** (*New repository secret*): `GRAFANA_SERVICE_ACCOUNT_TOKEN` (the token above; reuse
  the dashboard secret if you widened that account's role).

---

## 3. Alert rules

The metrics come from node-exporter (`job="node_exporter"`), so rules only have data once
node-exporter is scraped (see `docker-compose.yml`).

| Rule (file) | Expression (query) | Threshold | `for` |
|---|---|---|---|
| **Host down** (`host-down.json`) | `up{job="node_exporter"}` | `< 1` | `2m` |
| **High CPU** (`high-cpu.json`) | `100 * (1 - avg(rate(node_cpu_seconds_total{job="node_exporter",mode="idle"}[5m])))` | `> 85` | `10m` |
| **High RAM** (`high-ram.json`) | `100 * (1 - (node_memory_MemAvailable_bytes{job="node_exporter"} / node_memory_MemTotal_bytes{job="node_exporter"}))` | `> 90` | `5m` |
| **Low disk (/)** (`low-disk.json`) | `100 * (1 - (node_filesystem_avail_bytes{job="node_exporter",mountpoint="/"} / node_filesystem_size_bytes{job="node_exporter",mountpoint="/"}))` | `> 85` | `10m` |
| **High load** (`high-load.json`) | `node_load1{job="node_exporter"}` | `> 4` | `5m` |
| **Disk fills within 24h** (`disk-fills-within-24h.json`) | Aggregated `predict_linear` for `/`, guarded by free space `< 20%` | `< 0` | `30m` |

Tuning notes:

- **Host down**: `noDataState` is `Alerting`, so an unreachable exporter (no series at all) fires,
  not just `up == 0`.
- **High load**: tune the `4` threshold to your CPU core count — load equal to core count is
  saturation. Check cores with `nproc` on the VPS.
- **Disk fills within 24h**: linearly extrapolates the last six hours of available bytes and
  aggregates filesystem label variants per host, so duplicate root-mount series do not create
  duplicate notifications. It fires before you actually run out. Drop the rule if it's too noisy.

To change a rule, edit the file in `alerts/rules/` and push — the workflow syncs it. The
`${DS_PROMETHEUS}` placeholder is resolved to your Prometheus datasource UID at sync time, so the
JSON stays portable (same scheme as the `${DS_*}` placeholders in `dashboards/`).

---

## 4. Adopting UI-created rules (avoid duplicates)

If you ever create rules by hand in the Grafana UI and then want them under CI management,
export them into git so the same `uid`s are managed — the sync upserts by uid, so adopting the
real uids means the same rules get updated, not doubled:

```bash
export GRAFANA_URL=https://grafana.example.com
export GRAFANA_SERVICE_ACCOUNT_TOKEN=<token with alert.rules:read>
uv run python scripts/export-alerts.py     # writes one file per rule into alerts/rules/
```

Review + commit the generated files, then run `sync-alerts.py` — the existing UI rules are
adopted (same uids, updated) and become read-only in the UI.

---

## 5. Run the sync

- **Automatically**: any push to `main` under `alerts/**` triggers it
  (`.github/workflows/sync-grafana-alerts.yml`).
- **Manually**: GitHub **Actions → Sync Grafana Alerts → Run workflow**.
- **Locally** (with env vars exported): `uv run python scripts/sync-alerts.py`.

The script prints one line per rule (`OK/created`, `OK/updated`, `SKIP`, `FAIL`) and a summary.
A 403 means the token lacks `alert.rules:write` (see §2); a 400 on one rule usually means a
`uid`/provenance clash with a UI-created rule (see §4).
