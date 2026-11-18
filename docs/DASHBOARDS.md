# Dashboards

All dashboards live as JSON under `dashboards/` and are synced to Grafana by CI — you never
import them by hand.

## Included dashboards

| File | Scope |
|---|---|
| `dashboards/kds-logs.json` | KDS API / Web / Email-worker / MCP server |
| `dashboards/marienmuehle-logs.json` | Marienmuehle API / Web / Email-worker / MCP server |
| `dashboards/tonyluke-logs.json` | Tony Luke Portfolio (web) |
| `dashboards/luki-fitti-logs.json` | LukiFitti API / Web / Email-worker |
| `dashboards/luki-fitti-kpis.json` | LukiFitti product KPIs via Postgres — see [LUKI-FITTI-KPIS.md](LUKI-FITTI-KPIS.md) |
| `dashboards/application-overview.json` | All projects, with `$project`/`$service` filters |
| `dashboards/container-metrics.json` | cAdvisor container metrics |
| `dashboards/vps-overview.json` | Host metrics (node-exporter): CPU/RAM/disk/network |

Manual import (if ever needed): **Dashboards → New → Import → upload JSON** in Grafana, then
map the `DS_LOKI` / `DS_PROMETHEUS` variables to your datasources.

## Auto-sync via CI (push → Grafana)

A GitHub Actions workflow (`.github/workflows/sync-grafana-dashboards.yml`) pushes every
`dashboards/*.json` to Grafana automatically on push to `main`, so you never import by hand.
It strips the manual-import wrappers and resolves the `${DS_LOKI}` / `${DS_PROMETHEUS}`
placeholders to your real datasource UIDs (`scripts/sync-dashboards.py`, using the
`grafana-client` package via `uv`). Dashboards with an unresolved placeholder or no stable
`uid` are skipped, never overwritten.

One-time setup:

1. In Grafana: **Administration → Users and access → Service accounts → Add service account**
   (role **Admin**, or Editor with the `dashboards:write` permission) → **Add service account
   token**. Copy the token.
2. In the GitHub repo **Settings → Secrets and variables → Actions**:
   - **Variables** (*New repository variable*): `GRAFANA_URL` (e.g. `https://grafana.yourdomain.com`)
     and, optionally, `GRAFANA_FOLDER_UID` (folder UID; default: General)
   - **Secrets** (*New repository secret*): `GRAFANA_SERVICE_ACCOUNT_TOKEN` — the token from step 1
3. Push a dashboard (or run it manually: **Actions → Sync Grafana Dashboards → Run workflow**).

> Alert rules sync the same way through a second workflow — see [ALERTS.md](ALERTS.md).
