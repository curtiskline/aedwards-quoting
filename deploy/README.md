# Staging deployment

The permanent staging site is `https://staging.quotes.vectorforgeinteractive.com`.
It runs on a separate DigitalOcean droplet and uses its own SQLite database at
`/opt/aedwards/instance/allenedwards.db`; it must never use production data.

Deploy staging with its safety gates explicitly enabled:

```bash
export KEY_PATH="$HOME/.ssh/id_rsa"
export ENABLE_MONITOR=false
export EMAIL_DELIVERY_ENABLED=false
export SERVER_NAME=staging.quotes.vectorforgeinteractive.com
export APP_URL=https://staging.quotes.vectorforgeinteractive.com
export DATABASE_URL=sqlite:////opt/aedwards/instance/allenedwards.db
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
points at `/opt/aedwards/instance/allenedwards.db` on this host.

For TLS, once the DNS A record resolves, run:

```bash
ssh -i "$KEY_PATH" root@<staging-ip> \
  'certbot --nginx -d staging.quotes.vectorforgeinteractive.com --non-interactive --agree-tos -m devin@918.software'
```
