# Operations & Deployment Guide

Operational reference for the VPS monitoring stack. Covers how logs/metrics flow,
how to deploy changes, the image-tag conventions, common (benign) errors, and how
to add a new application to monitoring.

## Architecture at a glance

```
app containers (kds / marienmuehle / tonyluke)  ──stdout──┐
                                                          ▼
                                              Alloy (loki.source.docker)
                                                          │  derives project/service
                                                          ▼
                                                       Loki  ──►  Grafana (Explore + dashboards)

all containers ──cAdvisor──► Prometheus ──► Grafana (container-metrics dashboard)
```

- **Logs:** Alloy tails every Docker container via the socket and ships to Loki. Streams are
  labeled `project` / `service` (see below), so dashboards query `{project="kds"}` etc.
- **Metrics:** cAdvisor exposes per-container CPU/mem/net/disk; Prometheus scrapes it.

## How `project` / `service` labels get onto log streams

Each application service carries Docker labels in its own `compose.yml`:

```yaml
labels:
  - 'app.project=kds'
  - 'app.service=api'   # api | web | email-worker | mcp-server
```

Alloy's `config/alloy/config.alloy` reads them via `discovery.relabel`:

```alloy
rule { source_labels = ["__meta_docker_container_label_app_project"] target_label = "project" }
rule { source_labels = ["__meta_docker_container_label_app_service"] target_label = "service" }
```

> Why labels and not the image name? Alloy's `discovery.docker` does **not** reliably expose
> the container image meta label, so deriving `project`/`service` from the image produced empty
> labels. Docker container labels *are* reliably exposed, so they are the source of truth.

Stream labels you'll see in Loki: `project`, `service`, `container_name`, `container_id`,
`environment`, `host`, and the auto-added `service_name`.

## Deployment workflow (Coolify)

**Coolify deploys each resource from its git repository.** This is the single most important
operational rule — editing files locally has no effect until the change is committed, pushed,
and redeployed.

1. Make the change locally.
2. `git commit` + `git push` (this repo, **and** the app repos if you changed a `compose.yml`).
3. In Coolify, click **Redeploy** on the resource.
4. **Verify on the box** that the change actually landed before debugging anything else:
   ```bash
   docker ps --format '{{.Names}}\t{{.Image}}'
   docker inspect <container> --format '{{json .Config.Labels}}' | jq      # labels present?
   docker exec <container> cat <mounted-config>                            # config deployed?
   ```

> Docker labels set in `compose.yml` only apply when the container is **recreated**
> (Redeploy), not on a plain restart.

### Container networking
Services reach each other by **Docker service name**, never `localhost`:
- Grafana → Loki: `http://loki:3100`
- Grafana → Prometheus: `http://prometheus:9090`
- Alloy/Prometheus → Loki: `http://loki:3100`
- Prometheus → cAdvisor: `http://cadvisor:9109`
- Prometheus → node-exporter: `http://node-exporter:9100`

(`localhost` inside a container refers to that container itself, so it fails.)

### Host port access
The published ports (Prometheus 9090, Loki 3100, cAdvisor 9109, Alloy 12345) are bound to
`127.0.0.1` in `docker-compose.yml`. These endpoints have **no authentication**, so they must
never be exposed publicly — they are reachable from the host itself (for debugging) and by
service name on the Docker network (for Grafana and each other), nothing else.

## Image tag conventions

Different projects use different tag formats — guessing causes `manifest unknown` errors:

| Image | Tag format | Example |
|-------|------------|---------|
| Grafana (loki, alloy, grafana-oss) | **no `v`** | `grafana/loki:3.7.9`, `grafana/alloy:v1.21.3`* |
| Prometheus | **with `v`** | `prom/prometheus:v3.16.0` |
| cAdvisor | **no `v`, on ghcr** | `ghcr.io/google/cadvisor:0.60.6` |

\* Alloy tags *do* use a `v` prefix (`grafana/alloy:v1.21.3`) — the exception among Grafana images.

Always verify a tag exists (GitHub Releases / Docker Hub) before pinning. **Promtail is
deprecated** by Grafana; Alloy is its replacement (used here).

## Per-application logging

Alloy tails **every** running container on the host via the Docker socket, so all apps are
collected automatically — no per-app allowlist is needed, and network isolation between the
app networks does not matter.

### `project` and `service` labels

Because Coolify names containers the same way across projects (`api-<uuid>-<n>`,
`web-<uuid>-<n>`, …), selecting logs by `container_name` mixes projects together. Instead,
each app's `compose.yml` sets `app.project` / `app.service` Docker labels (see above), which
Alloy turns into Loki stream labels:

| App | Services (`service` label) |
|---|---|
| `kds` | `api`, `web`, `email-worker`, `mcp-server` |
| `marienmuehle` | `api`, `mcp-server` |
| `tonyluke` | `web` |
| `luki-fitti` | `api`, `web`, `email-worker`, `mcp-server` |

For metrics, `config/prometheus/prometheus.yml` derives the same `project`/`service` labels
from the cAdvisor `image` label (e.g. `ghcr.io/tuke307/luki-fitti-api` → `project=luki-fitti`,
`service=api`), so both signals line up. All dashboards query by these labels, so the
projects never cross-contaminate.

### Example LogQL queries

```logql
# All logs from a specific container
{container_name="my-app"}

# Logs containing "error"
{container_name="my-app"} |= "error"

# All luki-fitti logs
{project="luki-fitti"}

# luki-fitti API errors only
{project="luki-fitti", service="api"} |= "error"

# Everything across all projects (JSON logs)
{project=~"kds|marienmuehle|tonyluke|luki-fitti"} | json

# Rate of error logs
rate({container_name="my-app"} |= "error" [5m])
```

> Note: `db-migrate` containers run once and exit (`restart: 'no'`) and Alloy only tails
> `running` containers, so their logs are not captured. That is expected.

## Configuration reference

| Service | Config file | Container port |
|---|---|---|
| Prometheus | `config/prometheus/prometheus.yml` (+ `rules/*.yml`) | 9090 |
| Loki | `config/loki/loki.yml` (retention: 31 days) | 3100 |
| Alloy | `config/alloy/config.alloy` (River syntax) | 12345 |
| cAdvisor | flags in `docker-compose.yml` | 9109 |
| node-exporter | flags in `docker-compose.yml` | 9100 (not published) |

Alloy scrapes Docker container logs via the Docker socket, derives `project`/`service` from
the `app.*` container labels (see above), and reads Coolify labels as well. See
[`config/README.md`](../config/README.md) for per-file details and validation commands.

## Loki 3.x notes

The `config/loki/loki.yml` was adapted for Loki 3.x:
- `reject_old_samples: false` — accept older/out-of-order samples (Alloy re-reads existing
  container logs on first run / after a position loss).
- `enforce_metric_name` **removed** (dropped in Loki 3.x; leaving it fails config validation).
- `compactor.delete_request_store: filesystem` — **required** by Loki 3.x when
  `retention_enabled: true` (retention = 31 days via `retention_period: 744h`).

Alloy persists log-read positions on the `alloy-data` volume (`--storage.path`), so restarts
resume from the last offset instead of re-reading container history.

## Common (benign) log noise

These errors look alarming but are expected and safe to ignore:

- **Alloy / Promtail: `could not inspect container info: No such container`** — a short-lived
  container (Coolify one-off job, or `db-migrate` which exits immediately) was discovered, then
  vanished before inspection. Transient race.
- **Alloy: `failed to register collector with remote server: noop client`** — Alloy trying to
  phone home to a Grafana Cloud remote-config server that isn't configured. Cosmetic.

## Adding a new application to monitoring

1. In the app's `compose.yml`, add labels to each service you want collected:
   ```yaml
   labels:
     - 'app.project=<short-name>'
     - 'app.service=<api|web|...>'
   ```
2. Commit + push the app repo, then **Redeploy** it in Coolify (recreates the containers).
3. That's it — Alloy discovers it automatically (refresh every 5s). Confirm in Grafana:
   ```logql
   {project="<short-name>"}
   ```
4. Optionally add a Loki dashboard under `dashboards/` and import it in Grafana
   (map the `DS_LOKI` variable to your Loki datasource).

## Troubleshooting

All published ports are bound to `127.0.0.1`, so run these on the host itself.

```bash
# Check Loki is receiving logs
curl -G -s "http://localhost:3100/loki/api/v1/label" | jq

# Check Loki readiness
curl -s http://localhost:3100/ready

# Prometheus targets / health
curl -s http://localhost:9090/-/healthy
curl -s http://localhost:9090/api/v1/targets | jq

# Alloy metrics + UI (component graph at http://localhost:12345)
curl -s http://localhost:12345/metrics
```

## Known follow-ups

- **Metrics label consistency:** the container-metrics dashboard derives `project`/`service`
  from cAdvisor's `image` metric (`prometheus.yml`), while logs use the `app.*` Docker labels.
  cAdvisor runs with `--store_container_labels=false`, so it doesn't currently expose `app.*`.
  To unify, whitelist `app.project`/`app.service` in cAdvisor and switch the `prometheus.yml`
  relabel to read them.
- **Dead config:** `config/promtail/` is unused (replaced by Alloy) and can be deleted.
