# LukiFitti Product KPIs

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

## One-time setup

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

3. Push to `main` – the dashboard sync workflow deploys it like the other dashboards
   (see [DASHBOARDS.md](DASHBOARDS.md)).
