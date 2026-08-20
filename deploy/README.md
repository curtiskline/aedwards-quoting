# Staging deployment

The permanent staging site is `https://staging.quotes.vectorforgeinteractive.com`.
It runs on a separate DigitalOcean droplet with its own database; it must never
use production data. Since the 2026-08-20 CP-1 rehearsal (task 397) staging runs
droplet-local PostgreSQL (`postgresql://aedwards@/aedwards?host=/var/run/postgresql`);
the old SQLite file remains at `/opt/aedwards/instance/allenedwards.db*` as the
rehearsal's rollback artifact.

Deploy staging with its safety gates explicitly enabled:

```bash
export KEY_PATH="$HOME/.ssh/id_rsa"
export ENABLE_MONITOR=false
export EMAIL_DELIVERY_ENABLED=false
export SERVER_NAME=staging.quotes.vectorforgeinteractive.com
export APP_URL=https://staging.quotes.vectorforgeinteractive.com
export DATABASE_URL='postgresql://aedwards@/aedwards?host=/var/run/postgresql'
bash deploy/deploy.sh <staging-ip>
bash deploy/deploy_web.sh <staging-ip>
```

`ENABLE_MONITOR=false` stops and disables `aedwards-monitor` and prevents the
monitor deploy from copying O365 mailbox credentials. `EMAIL_DELIVERY_ENABLED=false`
blocks both quote delivery and magic-link delivery in the application, even if
mail credentials are later accidentally added to the host.

After each staging deploy, positively verify the isolation boundary:

```bash
ssh -i "$KEY_PATH" root@<staging-ip> \
  'systemctl is-enabled aedwards-monitor; systemctl is-active aedwards-monitor; \
   sudo grep -E "^(O365_EMAIL|O365_PASSWORD|O365_CLIENT_SECRET|GMAIL_EMAIL|GMAIL_REFRESH_TOKEN)=" /opt/aedwards/.env || true; \
   sudo grep -E "^(ENABLE_MONITOR|EMAIL_DELIVERY_ENABLED|DATABASE_URL)=" /opt/aedwards/.env'
```

Expected results: the monitor is `disabled` and `inactive`, there are no live
mailbox credential lines, `EMAIL_DELIVERY_ENABLED=false`, and the database URL
points at the local PostgreSQL socket on this host.

For TLS, once the DNS A record resolves, run:

```bash
ssh -i "$KEY_PATH" root@<staging-ip> \
  'certbot --nginx -d staging.quotes.vectorforgeinteractive.com --non-interactive --agree-tos -m devin@918.software'
```


## PostgreSQL on staging (CP-1 rehearsal, task 397)

Staging is the rehearsal target for the SQLite → PostgreSQL cutover
(`docs/runbooks/postgres-cutover.md`). To set it up:

```bash
bash deploy/provision_pg.sh <staging-ip>       # installs PG, role/db, backup cron
# deploy this branch as above (still on SQLite), then rehearse the cutover:
# run scripts/migrate_sqlite_to_postgres.py on the host and flip DATABASE_URL to
#   postgresql://aedwards@/aedwards?host=/var/run/postgresql
# in /opt/aedwards/.env, restart aedwards-web, and verify the UI.
```

Staging's database is disposable; `--recreate` reloads are always safe there.
