# Production cutover: SQLite → PostgreSQL

Status: **runbook only — do not execute without Devin's go-ahead** (CP-1, task
397; prod cutover is a separate scheduled follow-up gated on the staging
rehearsal passing).

Prod host: 157.230.227.28 (`quotes.allanedwards.io`).
Staging rehearsal host: 134.122.29.15 (`staging.quotes.vectorforgeinteractive.com`).

## Why droplet-local PostgreSQL (and how to change the answer later)

Recommendation: **droplet-local PostgreSQL from apt** (installed by
`deploy/provision_pg.sh`), not DigitalOcean Managed PostgreSQL.

- Scale: one small business app; three gunicorn workers plus the monitor. A
  local instance on the existing 2 GB droplet handles this with headroom.
- Cost: managed PG starts at ~$15/mo on top of the droplet; local adds $0.
- Latency/simplicity: unix-socket peer auth, no TLS/network hop, no separate
  credential to rotate; DATABASE_URL has no password in it.
- Backups: nightly `pg_dump -Fc` with 7-day retention (cron installed by
  `provision_pg.sh`) replaces the old sqlite3 backup cron like-for-like.

What a later move to managed DO PG would take (the choice stays reversible):

1. Create the managed cluster + database + user in the DO console; allow the
   droplet in its trusted sources.
2. `pg_dump -Fc` the local database, `pg_restore` into the managed cluster.
3. Point `DATABASE_URL` in `/opt/aedwards/.env` at the managed connection
   string (TLS: append `?sslmode=require`), restart both services.
4. Disable the local backup cron and the local postgresql service.

Nothing in the app or migrations is coupled to where PostgreSQL runs; only
`DATABASE_URL` changes. Final call on local-vs-managed is Devin's at cutover
review.

## Pre-cutover (days before, no downtime)

1. Deploy the Postgres-clean release (this branch merged) to prod as usual —
   prod stays on SQLite; nothing changes yet.
2. Provision PostgreSQL on the prod droplet:
   `bash deploy/provision_pg.sh 157.230.227.28`
3. Confirm the staging rehearsal passed (see "Staging rehearsal" below) on the
   same commit you are about to cut over.

## Cutover (order matters; ~5 minutes of intake pause, web stays up until step 4)

All commands over SSH as root unless noted. `$APP` = `/opt/aedwards`.

1. **Stop the writers.**
   ```bash
   systemctl stop aedwards-monitor aedwards-web
   ```
2. **Final backup of the SQLite file** (this is also the rollback artifact):
   ```bash
   cp $APP/instance/allenedwards.db $APP/instance/allenedwards.db.pre-pg-$(date +%F)
   ```
3. **Final sync into PostgreSQL** (idempotent; `--recreate` drops any
   rehearsal load):
   ```bash
   sudo -u aedwards bash -c "cd $APP/src && set -a && source $APP/.env && set +a && \
     $APP/venv/bin/python scripts/migrate_sqlite_to_postgres.py \
       --sqlite $APP/instance/allenedwards.db \
       --postgres 'postgresql://aedwards@/aedwards?host=/var/run/postgresql' \
       --recreate"
   ```
   The script verifies itself (row counts, attachment BLOB sha256, status and
   JSON spot checks) and exits non-zero on any mismatch. **Do not proceed past
   a non-zero exit — run the rollback.**
4. **Switch the env.** In `$APP/.env` replace the `DATABASE_URL=` line with:
   ```
   DATABASE_URL=postgresql://aedwards@/aedwards?host=/var/run/postgresql
   ```
5. **Restart and verify.**
   ```bash
   systemctl start aedwards-web && sleep 3 && curl -fsS localhost:8000/healthz
   systemctl start aedwards-monitor
   systemctl status aedwards-web aedwards-monitor --no-pager
   ```
6. **Functional verification** (per standing post-deploy policy): log into
   quotes.allanedwards.io, open the dashboard and an existing quote (line
   items, attachments download, PDF preview), then send test RFQs, verify each
   arrives as a quote, and delete the test records.
7. **Retire the SQLite backup cron** (leave the DB file in place for a week):
   remove the sqlite3 line from the host crontab; the pg_dump cron from
   `provision_pg.sh` is already active.

## Rollback (any failure in steps 3–6)

The SQLite file was never modified — rollback is pointing back at it:

1. `systemctl stop aedwards-web aedwards-monitor`
2. Restore `DATABASE_URL=sqlite:////opt/aedwards/instance/allenedwards.db` in
   `$APP/.env`.
3. `systemctl start aedwards-web aedwards-monitor` and re-run the health check.
4. Intake downtime is bounded by steps 1–3; any RFQs that arrived while the
   monitor was stopped are picked up on its next poll (idempotency claims
   prevent duplicates).

## Staging rehearsal

Staging's DB is disposable and isolated (no monitor, no email). Rehearse the
full sequence there first:

```bash
bash deploy/provision_pg.sh 134.122.29.15
# deploy this branch to staging (deploy/README.md), still on SQLite
# then run the cutover steps 1-6 above on staging (no monitor there;
# functional verification via the staging UI)
```

Record the rehearsal result (pass/fail + any surprises) before scheduling the
prod cutover.

## Post-cutover follow-ups

- After a quiet week, archive and remove `$APP/instance/allenedwards.db*`.
- Restore drill: `pg_restore --list` on a nightly dump to confirm backups are
  usable.
