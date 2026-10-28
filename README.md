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
| `ghcr.io/tuke307/kds-mcp-server`        | `kds`         | `mcp-server`   |
| `ghcr.io/tuke307/marienmuehle-api`      | `marienmuehle`| `api`          |
| `ghcr.io/tuke307/marienmuehle-mcp-server` | `marienmuehle`| `mcp-server` |
| `ghcr.io/tuke307/tonyluke-web`          | `tonyluke`    | `web`          |
| `ghcr.io/tuke307/luki-fitti-api`        | `luki-fitti`  | `api`          |
| `ghcr.io/tuke307/luki-fitti-web`        | `luki-fitti`  | `web`          |
| `ghcr.io/tuke307/luki-fitti-email-worker` | `luki-fitti`| `email-worker` |

All dashboards query by these labels, so kds, marienmuehle, tonyluke, and luki-fitti never cross-contaminate.

### Dashboards

| File                          | Scope                                  |
|-------------------------------|----------------------------------------|
| `dashboards/kds-logs.json`        | KDS API / Web / Email-worker / MCP server |
| `dashboards/marienmuehle-logs.json`| Marienmuehle API / Web / Email-worker / MCP server |
| `dashboards/tonyluke-logs.json`    | Tony Luke Portfolio (web)              |
| `dashboards/luki-fitti-logs.json`  | LukiFitti API / Web / Email-worker     |
| `dashboards/luki-fitti-kpis.json`  | LukiFitti product KPIs: signups, onboarding funnel, activation, retention cohorts, engagement (PostgreSQL, see [below](#lukifitti-product-kpis)) |
| `dashboards/application-overview.json`        | All projects, with `$project`/`$service` filters |
| `dashboards/container-metrics.json`| cAdvisor container metrics             |
| `dashboards/vps-overview.json`     | Host metrics (node-exporter): CPU/RAM/disk/network |

Import them in Grafana: **Dashboards → New → Import → upload JSON**. When prompted,
select the Loki datasource.

#### Auto-sync via CI (push → Grafana)

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

##### Auto-sync alerts via CI (push → Grafana)

A second workflow (`.github/workflows/sync-grafana-alerts.yml`) pushes the alert rules under
`alerts/rules/` to Grafana on push to `main`, the same way dashboards sync — so you never click
through the Alerting UI. It uses `scripts/sync-alerts.py` against Grafana's alerting provisioning
API (the `grafana-client` package, via `uv`). `${DS_PROMETHEUS}` is resolved to your Prometheus
datasource UID; rules are upserted by `uid` and marked read-only in the UI (git is the source of
truth). Like dashboards, the sync is upsert-only (no deletes). Full details:
**[Alerting Guide](docs/ALERTS.md)**.

The workflow manages **rules only**. Contact points and the notification policy stay in the
Grafana UI (e.g. your existing email contact point + default policy) — the synced rules route
through your default policy.

One-time setup (in addition to the dashboard secret/variables above):

1. In Grafana: the service account must be **Admin**, or a role granting
   `alert.rules:write` (the dashboard `dashboards:write` scope is not enough — every push
   returns 403). Re-issue the token if you widened the role.
2. In the GitHub repo **Settings → Secrets and variables → Actions**, ensure `GRAFANA_FOLDER_UID`
   is set to a **real** Grafana folder UID (alert rules cannot live in General).
3. Enable SMTP on the Grafana resource (see [docs/ALERTS.md](docs/ALERTS.md)) so emails send.
4. Push to `alerts/rules/` (or run it manually: **Actions → Sync Grafana Alerts → Run workflow**).

> Already have alert rules in the Grafana UI? Adopt them into git with
> `scripts/export-alerts.py` first (see [docs/ALERTS.md](docs/ALERTS.md)) — otherwise the push
> creates duplicate rules and you get double alerts.

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

## LukiFitti Product KPIs

`dashboards/luki-fitti-kpis.json` queries the LukiFitti PostgreSQL database directly (no app
instrumentation needed). "Active" means at least one **completed** workout. Panels:

- **Overview** – total users, signups, email-verified %, 7-day activation rate, WAU/MAU,
  stickiness (DAU/MAU), median time to first workout, users stuck unverified > 48h,
  failed confirmation emails, at-risk users (trained in last 90d but not last 14d)
- **Onboarding funnel** – signup → verified → started workout → completed 1st → came back
  another day → 3+ workouts (counts and % of signups), time-to-first-workout distribution,
  signups vs. activated-within-7-days over time
- **Growth** – cumulative users, active users per bucket
- **Retention** – weekly signup cohort table (% active in week 0/1/2/4/8)
- **Engagement** – workouts by device and type, completion rate, feature adoption among MAU,
  lifetime workouts per user, % of active users hitting their weekly goal

Use the `Bucket` variable to switch between daily and weekly aggregation.

### One-time setup

1. **Create a read-only role** in the LukiFitti database. The column-level grants cover exactly
   what the dashboard reads, so Grafana can never see emails, names, password hashes or tokens:

   ```sql
   -- Set the password from your secret store; never commit it.
   CREATE ROLE grafana_kpi LOGIN PASSWORD :'grafana_kpi_password';
   GRANT CONNECT ON DATABASE lukifittidb TO grafana_kpi;
   GRANT USAGE ON SCHEMA public TO grafana_kpi;
   GRANT SELECT (id, "emailVerified", "createdAt") ON "User" TO grafana_kpi;
   GRANT SELECT ("userId", "weeklyGoalSessions", "createdAt", "updatedAt") ON "UserPreferences" TO grafana_kpi;
   GRANT SELECT (id, "userId", status, "sessionType", "sourceDevice", "startedAt", "completedAt",
                 "healthKitWorkoutUuid") ON "WorkoutSession" TO grafana_kpi;
   GRANT SELECT ("userId", "systemKey") ON "WorkoutTemplate" TO grafana_kpi;
   GRANT SELECT ("userId") ON "Exercise" TO grafana_kpi;
   GRANT SELECT ("emailTemplateId", status, "createdAt") ON "Email" TO grafana_kpi;
   GRANT SELECT (id, type) ON "EmailTemplate" TO grafana_kpi;
   ALTER ROLE grafana_kpi SET default_transaction_read_only = on;
   ```

   Run with `psql -v grafana_kpi_password="$GRAFANA_KPI_PASSWORD" -f ...`. If you add a panel
   that reads a new column, grant that column too.

2. **Add the datasource** in Grafana: **Connections → Data sources → Add → PostgreSQL**
   - **Name:** `luki-fitti` (the CI sync resolves `${DS_LUKI_FITTI}` by this name)
   - **Host:** the LukiFitti Postgres container on the Coolify network (Grafana must share a
     network with it), **Database:** `lukifittidb`, **User:** `grafana_kpi`
   - **TLS/SSL mode:** `require` if the DB is reached over anything other than the internal
     Docker network

3. Push to `main` – the dashboard sync workflow deploys it like the other dashboards.

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

