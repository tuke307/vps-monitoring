# Alerting

Alerts are managed by **Grafana** (not Prometheus). Grafana's built-in alert
scheduler queries Prometheus directly, evaluates rules, and routes firing alerts
to a **contact point** (here: email). No extra Alertmanager container is needed.

Flow:

```
node-exporter ──> Prometheus ──> Grafana alert rule ──> contact point ──> your inbox
```

---

## 1. Enable email (SMTP) on the Grafana resource

Grafana sends mail through its own SMTP config, set via environment variables on
the **Grafana Coolify resource** (it is a separate deployment from this stack).
The app services already use SMTP, so reuse those credentials.

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

Then **Redeploy** the Grafana resource so the new env vars are picked up.

> Verify in Grafana: **Alerting → Contact points → (email) → Test**. A test mail
> should arrive within a few seconds. If it never arrives, SMTP env vars are the
> usual culprit.

---

## 2. Create an email contact point

Grafana → **Alerting → Contact points → + Add contact point**

- **Name**: `email`
- **Integration**: `Email`
- **Addresses**: your inbox(es)
- Leave "Disable resolved message" off so you also get the all-clear.

---

## 3. Point the default notification policy at it

Grafana → **Alerting → Notification policies**

- Open the **default policy** (root).
- Set **Contact point**: `email`.
- Save.

Every alert rule without a specific label override now lands in your inbox.

---

## 4. Add the alert rules

For each rule below: **Alerting → Alert rules → + New alert rule**.
Set the **datasource** to **Prometheus**, paste the expression into query **A**
(leave it as an instant/range query as shown), set the threshold, and set the
**Pending** (`for`) duration. Then choose the default notification policy.

The metrics come from node-exporter (`job="node_exporter"`), so these rules only
have data once node-exporter is scraped (see `docker-compose.yml`).

| Rule | Expression (query A) | Threshold | `for` |
|---|---|---|---|
| **Host down** | `up{job="node_exporter"}` | `< 1` | `2m` |
| **High CPU** | `100 * (1 - avg(rate(node_cpu_seconds_total{job="node_exporter",mode="idle"}[5m])))` | `> 85` | `10m` |
| **High RAM** | `100 * (1 - (node_memory_MemAvailable_bytes{job="node_exporter"} / node_memory_MemTotal_bytes{job="node_exporter"}))` | `> 90` | `5m` |
| **Low disk (/)** | `100 * (1 - (node_filesystem_avail_bytes{job="node_exporter",mountpoint="/"} / node_filesystem_size_bytes{job="node_exporter",mountpoint="/"}))` | `> 85` | `10m` |
| **High load** | `node_load1{job="node_exporter"}` | `> 4` | `5m` |
| **Disk fills within 24h** | `predict_linear(node_filesystem_avail_bytes{job="node_exporter",mountpoint="/"}[1h], 24 * 3600)` | `< 0` | `5m` |

Notes:

- **Host down**: also enable **No data** state handling on the rule so an
  unreachable exporter (no series at all) fires, not just `up == 0`.
- **High load**: tune the `4` threshold to your CPU core count — load equal to
  core count is saturation. Check cores with `nproc` on the VPS.
- **Disk fills within 24h**: linearly extrapolates the last hour of available
  bytes; fires before you actually run out. Drop the rule if it's too noisy.

---

## (Optional) As-code provisioning

If you prefer not to click through the UI, Grafana can load contact points and
rules from files under `/etc/grafana/provisioning/alerting/*.yaml` inside the
Grafana container. Because Grafana is a separate Coolify deployment, you would
mount that directory into the Grafana resource. The UI path above is the simpler
default.
