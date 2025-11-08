# VPS Monitoring Stack

A complete monitoring and logging solution with Prometheus, Loki, and exporters, integrated with Coolify.

## Architecture

### This Stack (Docker Compose)
- **Prometheus** - Metrics collection and storage
- **Loki** - Log aggregation system
- **Promtail** - Log collection agent

### Existing Coolify Services
- **Grafana** - Visualization and dashboards (with PostgreSQL)
- **cAdvisor** - Container metrics (port 9109)
- **Node Exporter** - Host metrics (ports 9208, 9209)
- **PostgreSQL Exporter** - Database metrics (port 9187)

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

### 2. Configure Loki in Grafana

In your Coolify Grafana instance:
1. Go to **Configuration** → **Data Sources**
2. Click **Add data source**
3. Select **Loki**
4. Set URL to: `http://<your-host-ip>:3100` (use your VPS IP)
5. Click **Save & Test**

### Access Services

- **Grafana**: (Your Coolify instance)
- **Prometheus**: http://localhost:9090
- **Loki**: http://localhost:3100
- **Promtail**: http://localhost:9080/targets

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

## Deployment on Multiple Hosts

For the second host (10.255.0.4), deploy Promtail separately:

### Option 1: Docker Run
```bash
# Create promtail config on remote host
# Update the Loki URL to point to your primary host
# In promtail-config.yml, change:
# clients:
#   - url: http://<primary-host-ip>:3100/loki/api/v1/push

docker run -d \
  --name promtail \
  -p 9080:9080 \
  -v /path/to/promtail-config.yml:/etc/promtail/config.yml \
  -v /var/log:/var/log:ro \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  --restart unless-stopped \
  grafana/promtail:2.9.3 \
  -config.file=/etc/promtail/config.yml
```

### Option 2: Deploy via Coolify
Create a new Docker Compose service in Coolify with Promtail, similar to your cAdvisor setup.

**Important**: Update the `host` label in the Promtail config to identify each host:
- Primary host: `host: 'primary-host'`
- Secondary host: `host: 'secondary-host'`

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

