<div align="center">

# vps-monitoring

**Metrics, logs and alerts for a single VPS, deployed via Coolify.**

[Docs](docs/OPERATIONS.md) ·
[Dashboards](docs/DASHBOARDS.md) ·
[Alerting](docs/ALERTS.md)

</div>

A Docker Compose stack that collects host and container metrics (Prometheus, cAdvisor,
node-exporter) and container logs (Loki + Alloy) from every app running on the box.
Grafana itself runs as a separate Coolify resource and reaches the stack over the shared
Docker network; dashboards and alert rules live in this repo and are pushed to Grafana by CI.

## What's inside

- **Metrics:** Prometheus scrapes cAdvisor (per-container) and node-exporter (host CPU, RAM,
  disk, network), 30-day retention.
- **Logs:** Grafana Alloy tails every Docker container on the host and ships to Loki
  (31-day retention).
- **Per-app labels:** every log stream and container metric carries `project`/`service`
  labels, so each app's data stays cleanly separated (see
  [docs/OPERATIONS.md](docs/OPERATIONS.md)).
- **Dashboards as code:** pre-built host/container/app dashboards under `dashboards/`,
  auto-synced to Grafana on push (see [docs/DASHBOARDS.md](docs/DASHBOARDS.md)).
- **Alerts as code:** host-down / CPU / RAM / disk rules under `alerts/`, synced the same
  way (see [docs/ALERTS.md](docs/ALERTS.md)).

## Quick start

```bash
docker compose up -d
```

Then in your Grafana instance add two datasources (same Docker network, so service names work):

- **Prometheus** → `http://prometheus:9090`
- **Loki** → `http://loki:3100`

Host ports are bound to `127.0.0.1` — services are reachable on localhost or by service
name on the Docker network, not from the public internet.

## Example: LukiFitti

The fitness app [LukiFitti](https://github.com/tuke307/luki-fitti) tags each container in its
`compose.yml`, and Alloy turns those Docker labels into log labels:

```yaml
labels:
  - 'app.project=luki-fitti'
  - 'app.service=api'   # web, email-worker, mcp-server, ...
```

Container metrics get the same `project`/`service` labels from the image name
(`ghcr.io/tuke307/luki-fitti-api` → `luki-fitti` / `api`).

That powers two dashboards: [`dashboards/luki-fitti-logs.json`](dashboards/luki-fitti-logs.json)
(`{project="luki-fitti"}` logs per service) and
[`dashboards/luki-fitti-kpis.json`](dashboards/luki-fitti-kpis.json), which also queries the
app's Postgres directly for product KPIs — see
[docs/LUKI-FITTI-KPIS.md](docs/LUKI-FITTI-KPIS.md).

## Repository layout

```
├── docker-compose.yml       # the stack: prometheus, loki, alloy, cadvisor, node-exporter
├── config/                  # prometheus.yml, loki.yml, config.alloy (+ legacy promtail)
├── dashboards/              # Grafana dashboards as JSON, synced by CI
├── alerts/rules/            # Grafana alert rules as JSON, synced by CI
├── scripts/                 # sync-dashboards.py / sync-alerts.py / export-alerts.py (uv)
└── docs/                    # operations, dashboards, alerting, LukiFitti KPIs
```

## Docs

- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** — how logs/metrics flow, the label scheme,
  Coolify deploy workflow, per-app logging, troubleshooting, adding a new app.
- **[docs/DASHBOARDS.md](docs/DASHBOARDS.md)** — dashboard list and the CI auto-sync setup.
- **[docs/ALERTS.md](docs/ALERTS.md)** — SMTP, contact points, alert rules and their CI sync.
- **[docs/LUKI-FITTI-KPIS.md](docs/LUKI-FITTI-KPIS.md)** — the read-only Postgres KPI
  datasource, a worked example of monitoring beyond logs/metrics.
