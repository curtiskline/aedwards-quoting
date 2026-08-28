# Staging deployment

The permanent staging site is `https://staging.quotes.vectorforgeinteractive.com`.
It runs on a separate DigitalOcean droplet with its own database; it must never
use production data. Since the 2026-08-20 CP-1 rehearsal (task 397) staging runs
droplet-local PostgreSQL (`postgresql://aedwards@/aedwards?host=/var/run/postgresql`);
the old SQLite file remains at `/opt/aedwards/instance/allenedwards.db*` as the
rehearsal's rollback artifact.

## Deploy `.env` safety (tasks 449, 452)

As of task 449 (`deploy_web.sh`) and task 452 (`deploy.sh`, the monitor
deploy), both deploy scripts treat the **host's `/opt/aedwards/.env` as
authoritative** for environment-specific values. The merge lives in
`deploy/merge_env.sh` (the same code runs in the local preflight and on the
host) and guarantees:

- an existing host `DATABASE_URL` is never overwritten, and there is **no
  sqlite fallback**: a deploy with no `DATABASE_URL` on the host and none
  provided fails before touching the host;
- `EMAIL_DELIVERY_ENABLED` is **never written by a deploy**. The host's value
  is preserved; if absent it is written as `false`. Enabling delivery is only
  ever a manual edit of the host `.env` followed by a service restart.
  (Setting `EMAIL_DELIVERY_ENABLED=true` locally only bundles mail
  credentials into the deploy; it cannot turn delivery on.)
- an existing host `SECRET_KEY` is preserved (generated host-side only when
  absent everywhere); delivery-off hosts get any leftover mail-credential
  lines stripped;
- an existing host `ENABLE_MONITOR` is preserved: `deploy.sh` defaults it to
  `true` for fresh hosts, but a deploy can never flip a monitor-off host
  (staging) back to polling a live mailbox;
- `--dry-run` prints the resulting host `.env` diff and applies **nothing**;
- the target is printed loudly (staging `134.122.29.15` / prod
  `157.230.227.28` are auto-detected) and `--env staging|prod` aborts on a
  mismatch.

Note for fresh monitor-on hosts: the delivery-off credential strip means
mailbox credentials only persist on a host whose `.env` already says
`EMAIL_DELIVERY_ENABLED=true`. On a brand-new prod host, set that manually
in `/opt/aedwards/.env` before (or after) the first `deploy.sh` run —
enabling delivery is always a manual host edit, never a deploy side effect.

Guard rails are fixture-tested in `tests/test_deploy_env_safety.sh` (run
`bash tests/test_deploy_env_safety.sh`; it never contacts a real host).

Deploy staging:

```bash
export KEY_PATH="$HOME/.ssh/id_rsa"
export ENABLE_MONITOR=false
export SERVER_NAME=staging.quotes.vectorforgeinteractive.com
export APP_URL=https://staging.quotes.vectorforgeinteractive.com
bash deploy/deploy.sh --dry-run --env staging <staging-ip>       # inspect the .env diff first
bash deploy/deploy.sh --env staging <staging-ip>
bash deploy/deploy_web.sh --dry-run --env staging <staging-ip>   # inspect the .env diff first
bash deploy/deploy_web.sh --env staging <staging-ip>
```

`ENABLE_MONITOR=false` stops and disables `aedwards-monitor` and prevents the
monitor deploy from copying O365 mailbox credentials. Since task 452 the
export is only needed for a **fresh** host: once the host `.env` says
`ENABLE_MONITOR=false`, that value survives every subsequent `deploy.sh` run
by construction (a plain deploy defaults the key to `true` only for hosts
that have never set it).
Staging's `EMAIL_DELIVERY_ENABLED=false` and PostgreSQL `DATABASE_URL` now
survive both deploy scripts without any exports: the host values are
preserved by construction, and `EMAIL_DELIVERY_ENABLED=false` blocks both
quote delivery and magic-link delivery in the application even if mail
credentials are later accidentally added to the host.

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
