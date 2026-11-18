# Configuration Files

This directory contains all configuration files for the monitoring stack.

## Directory Structure

```
config/
├── prometheus/          # Prometheus metrics collection
│   ├── prometheus.yml   # Main Prometheus configuration
│   └── rules/           # Recording and alerting rules
├── loki/                # Loki log aggregation
│   └── loki.yml        # Loki configuration
├── alloy/               # Alloy log collection (replaces Promtail)
│   └── config.alloy    # Alloy configuration (River syntax)
└── promtail/            # Legacy Promtail config — unused, kept for reference
    └── promtail.yml     # Promtail configuration (reusable for all hosts)
```

## Configuration Details

### Prometheus (`prometheus/prometheus.yml`)

Scrapes metrics from:
- Prometheus itself (9090)
- Grafana (3000)
- cAdvisor (9109)
- Node Exporter (9100)
- PostgreSQL Exporter (9187)
- Loki (3100)
- Promtail (9080)

**Customization:**
- Add new scrape targets under `scrape_configs`
- Adjust `scrape_interval` and `evaluation_interval` in `global` section
- Add alert/recording rules in `rules/` directory

### Loki (`loki/loki.yml`)

Log storage and querying.

**Key Settings:**
- Retention: 31 days
- Storage: Filesystem-based
- Port: 3100

**Customization:**
- Change `retention_period` in `limits_config` section
- Adjust rate limits in `limits_config`
- Modify compaction settings in `compactor` section

### Alloy (`alloy/config.alloy`)

Collects logs from all Docker containers via the Docker socket and ships them to Loki.
Derives `project`/`service` stream labels from the `app.project`/`app.service` Docker
labels on each container.

### Promtail (`promtail/promtail.yml`)

**Unused** — Promtail is deprecated and was replaced by Alloy. The config is kept for
reference only. Same config works for all hosts.

**What it scrapes:**
- All Docker containers (via Docker socket)
- Coolify labels: `coolify_serviceName`, `coolify_application_name`
- Custom `alias` labels
- System logs (optional)

**For local deployment** (same host as Loki):
- Use default URL: `http://loki:3100/loki/api/v1/push`

## Common Modifications

### Add a New Service to Prometheus

Edit `prometheus/prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'my-service'
    static_configs:
      - targets: ['service-host:port']
        labels:
          environment: 'production'
```

### Add Custom Log Paths to Promtail

Edit `promtail/promtail.yml`:

```yaml
scrape_configs:
  - job_name: application
    static_configs:
      - targets:
          - localhost
        labels:
          job: myapp
          __path__: /var/log/myapp/*.log
```

### Add Alert Rules

Create a file in `prometheus/rules/`:

```yaml
# prometheus/rules/alerts.yml
groups:
  - name: example
    interval: 30s
    rules:
      - alert: HighErrorRate
        expr: rate(http_requests_total{status="500"}[5m]) > 0.05
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "High error rate detected"
```

## Validation

After modifying configs, validate before restarting:

```bash
# Validate Prometheus config
docker run --rm -v $(pwd)/config/prometheus:/config prom/prometheus:latest \
  promtool check config /config/prometheus.yml

# Validate Loki config
docker run --rm -v $(pwd)/config/loki:/config grafana/loki:3.7.9 \
  -config.file=/config/loki.yml -verify-config

# Validate Alloy config
docker run --rm -v $(pwd)/config/alloy:/config grafana/alloy:v1.21.3 \
  validate /config/config.alloy
```

## Reload Configs Without Restart

```bash
# Prometheus (uses lifecycle API)
curl -X POST http://localhost:9090/-/reload

# Loki (restart required)
docker-compose restart loki

# Alloy (restart required)
docker-compose restart alloy
```
