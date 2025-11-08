# VPS Monitoring Stack

A complete monitoring and logging solution with Prometheus, Loki, and exporters, integrated with Coolify.

## Architecture

All services run in Coolify on the same Docker network, allowing them to communicate via service names.

### Services in This Stack
- **Prometheus** - Metrics collection and storage (`prometheus:9090`)
- **Loki** - Log aggregation system (`loki:3100`)
- **Promtail** - Log collection agent (`promtail:9080`)
- **cAdvisor** - Container metrics (`cadvisor:9109`)

### Other Coolify Services (separate deployments)
- **Grafana** - Visualization and dashboards
- **Node Exporter** - Host metrics
- **PostgreSQL Exporter** - Database metrics

## Quick Start

### 1. Start Monitoring Stack

```bash
# Start Prometheus, Loki, and Promtail
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
- **Promtail**: `http://promtail:9080`
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
│   └── promtail/
│       └── promtail.yml           # Promtail configuration
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

### Promtail
- **Config**: `config/promtail/promtail.yml`
- **Port**: 9080
- **Scrapes**:
  - Docker container logs (via Docker socket)
  - System logs from `/var/log` (optional)
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

## Network Configuration

All services in this stack connect to Coolify's Docker network, allowing service-to-service communication using container names instead of IPs or localhost.

**Example connections:**
- Grafana → Prometheus: `http://prometheus:9090`
- Grafana → Loki: `http://loki:3100`
- Prometheus → cAdvisor: `http://cadvisor:9109`
- Promtail → Loki: `http://loki:3100`

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

