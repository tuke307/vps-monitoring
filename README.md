# VPS Monitoring Stack

A complete monitoring and logging solution with Prometheus, Loki, and exporters, integrated with Coolify.

> 📖 **[Operations & Deployment Guide](docs/OPERATIONS.md)** — how logs/metrics flow, the `project`/`service` label scheme, the Coolify deploy workflow, image-tag conventions, common errors, and how to add a new app to monitoring.
>
> 🔔 **[Alerting Guide](docs/ALERTS.md)** — enable email alerts (SMTP on the Grafana resource), contact points, and the host alert rules (CPU/RAM/disk/host-down).

## Architecture

All services run in Coolify on the same Docker network, allowing them to communicate via service names.

### Services in This Stack
- **Prometheus** - Metrics collection and storage (`prometheus:9090`)
- **Loki** - Log aggregation system (`loki:3100`)
- **Alloy** - Log collection agent (`alloy:12345`)
- **cAdvisor** - Container metrics (`cadvisor:9109`)
- **Node Exporter** - Host metrics: CPU, RAM, disk, network (`node-exporter:9100`)

### Other Coolify Services (separate deployments)
- **Grafana** - Visualization and dashboards (see [docs/ALERTS.md](docs/ALERTS.md) for SMTP + alert setup)
- **PostgreSQL Exporter** - Database metrics

## Quick Start

### 1. Start Monitoring Stack

```bash
# Start Prometheus, Loki, and Alloy
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### 2. Configure Data Sources in Grafana

In your Coolify Grafana instance, add these data sources:

#### Prometheus
1. Go to **Configuration** → **Data Sources** → **Add data source**
2. Select **Prometheus**
3. Set URL to: `http://prometheus:9090`
4. Click **Save & Test** ✅

#### Loki
1. Go to **Configuration** → **Data Sources** → **Add data source**
2. Select **Loki**
3. Set URL to: `http://loki:3100`
4. Click **Save & Test** ✅

### Access Services

All services communicate via Docker service names on the Coolify network:
- **Prometheus**: `http://prometheus:9090`
- **Loki**: `http://loki:3100`
- **Alloy**: `http://alloy:12345`
- **cAdvisor**: `http://cadvisor:9109`

## Project Structure

```
vps-monitoring/
├── docker-compose.yml              # Main stack orchestration
├── config/
│   ├── prometheus/
│   │   ├── Dockerfile             # Prometheus container build
│   │   ├── prometheus.yml         # Prometheus configuration
│   │   └── rules/                 # Alert & recording rules
│   ├── loki/
│   │   └── loki.yml              # Loki configuration
│   └── alloy/
│       └── config.alloy           # Alloy configuration (River syntax)
├── check-health.sh                 # Health check script
├── SETUP_GUIDE.md                  # Detailed setup instructions
└── README.md
```

## Configuration

### Prometheus
- **Config**: `config/prometheus/prometheus.yml`
- **Rules**: `config/prometheus/rules/*.yml`
- **Port**: 9090

### Loki
- **Config**: `config/loki/loki.yml`
- **Retention**: 31 days
- **Port**: 3100

### Alloy
- **Config**: `config/alloy/config.alloy` (River syntax)
- **Port**: 12345
- **Scrapes**:
  - Docker container logs (via Docker socket)
  - Derives `project`/`service` from the image name (see Per-Application Logging)
  - Coolify labels (service, application, alias)

### Grafana
- Auto-provisioned datasources for Prometheus and Loki
- Default admin credentials: admin/admin (change after first login)

## Log Queries in Grafana

Example LogQL queries:

```logql
# All logs from a specific container
{container_name="my-app"}

# Logs containing "error"
{container_name="my-app"} |= "error"

# Logs by service (Coolify label)
{service="api-service"}

# Rate of error logs
rate({container_name="my-app"} |= "error" [5m])
```

## Per-Application Logging (kds + marienmuehle)

Promtail tails **every** running container on the host via the Docker socket, so both
`kds` and `marienmuehle` services are collected automatically — no per-app allowlist is
needed, and network isolation between the app networks does not matter.

### `project` and `service` labels

Because Coolify names both projects' containers the same way (`api-<uuid>-<n>`,
`web-<uuid>-<n>`, …), selecting logs by `container_name` mixes the two projects together.
Instead, Promtail derives two stable labels from the **image** name:

| Image                                   | `project`     | `service`      |
|-----------------------------------------|---------------|----------------|
| `ghcr.io/tuke307/kds-api:latest`        | `kds`         | `api`          |
| `ghcr.io/tuke307/kds-web:latest`        | `kds`         | `web`          |
| `ghcr.io/tuke307/kds-email-worker`      | `kds`         | `email-worker` |
| `ghcr.io/tuke307/marienmuehle-api`      | `marienmuehle`| `api`          |
| `ghcr.io/tuke307/tonyluke-web`          | `tonyluke`    | `web`          |

All dashboards query by these labels, so kds, marienmuehle, and tonyluke never cross-contaminate.

### Dashboards

| File                          | Scope                                  |
|-------------------------------|----------------------------------------|
| `dashboards/kds-logs.json`        | KDS API / Web / Email-worker           |
| `dashboards/marienmuehle-logs.json`| Marienmuehle API / Web / Email-worker  |
| `dashboards/tonyluke-logs.json`    | Tony Luke Portfolio (web)              |
| `dashboards/overview.json`        | All projects, with `$project`/`$service` filters |
| `dashboards/container-metrics.json`| cAdvisor container metrics             |
| `dashboards/vps-overview.json`     | Host metrics (node-exporter): CPU/RAM/disk/network |

Import them in Grafana: **Dashboards → New → Import → upload JSON**. When prompted,
select the Loki datasource.

> Note: `db-migrate` containers run once and exit (`restart: 'no'`) and Promtail only
> tails `running` containers, so their logs are not captured. That is expected.

### Example queries

```logql
# All kds logs
{project="kds"}

# kds API errors only
{project="kds", service="api"} |= "error"

# Everything across all projects (kds/marienmuehle are JSON; tonyluke is plain text)
{project=~"kds|marienmuehle|tonyluke"} | json
```

## Network Configuration

All services in this stack connect to Coolify's Docker network, allowing service-to-service communication using container names instead of IPs or localhost.

**Example connections:**
- Grafana → Prometheus: `http://prometheus:9090`
- Grafana → Loki: `http://loki:3100`
- Prometheus → cAdvisor: `http://cadvisor:9109`
- Alloy → Loki: `http://loki:3100`

## Troubleshooting

### Check Loki is receiving logs
```bash
curl -G -s "http://localhost:3100/loki/api/v1/label" | jq
```

### Check Promtail targets
```bash
curl http://localhost:9080/targets
```

### View Promtail metrics
```bash
curl http://localhost:9080/metrics
```

