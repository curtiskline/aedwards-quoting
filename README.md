# Allan Edwards RFQ Quoting Tool

Automated quoting system for Allan Edwards (steel pipe distributor). Monitors an O365 mailbox for incoming RFQ emails, classifies them using AI, extracts line items, prices them, generates a quote PDF, and saves it as a draft reply.

## Quick Start (Local)

```bash
python3.13 -m venv venv
source venv/bin/activate
pip install -e .
```

Create a `.env` file with your credentials:

```
O365_EMAIL=<mailbox-to-monitor@yourdomain.com>
O365_PASSWORD=<mailbox-password>
O365_CLIENT_ID=d3590ed6-52b3-4102-aeff-aad2292ab01c
O365_SCOPES=https://graph.microsoft.com/.default
ANTHROPIC_API_KEY=<your-anthropic-api-key>
LLM_PROVIDER=claude
```

Run the monitor once to test:

```bash
allenedwards monitor --once
```

Run continuously (polls every 5 minutes):

```bash
allenedwards monitor --poll-minutes 5
```

## Deploy to DigitalOcean

Deployment scripts in `deploy/`:

### 1) Provision droplet + firewall

Creates a Basic 2 vCPU / 2 GB droplet on DigitalOcean, then configures a cloud firewall for ports 22, 80, and 443.

```bash
# choose the SSH key fingerprint from: doctl compute ssh-key list
export DO_SSH_KEY_FINGERPRINT=<fingerprint>

# optional overrides
export DROPLET_NAME=aedwards
export DO_REGION=nyc1
export DO_SIZE=s-2vcpu-2gb

bash deploy/provision_do.sh
```

If `doctl` is unavailable/auth is missing, `deploy/provision_do.sh` prints manual console steps.

### 2) Deploy monitor service

Pushes updated code and restarts the service on an existing instance.

```bash
export O365_EMAIL=<mailbox@yourdomain.com>
export O365_PASSWORD=<password>
export ANTHROPIC_API_KEY=<key>   # optional if not using Claude
export LLM_PROVIDER=claude        # optional; defaults to claude

bash deploy/deploy.sh <droplet-ip>
```

### 3) Deploy web app (Flask + gunicorn + nginx)

```bash
export DATABASE_URL=sqlite:////opt/aedwards/instance/allenedwards.db
export SECRET_KEY=<strong-random-secret>

bash deploy/deploy_web.sh <droplet-ip>
```

The web deploy runs:
- package install into `/opt/aedwards/venv`
- `flask --app app.wsgi:app db upgrade` (or `alembic upgrade head` fallback)
- `aedwards-web` systemd restart
- nginx config + restart

### Checking logs

```bash
ssh -i ~/.ssh/id_rsa root@<host> 'sudo journalctl -u aedwards-monitor -f'
ssh -i ~/.ssh/id_rsa root@<host> 'sudo journalctl -u aedwards-web -f'
```

### Restarting services

```bash
ssh -i ~/.ssh/id_rsa root@<host> 'sudo systemctl restart aedwards-monitor'
ssh -i ~/.ssh/id_rsa root@<host> 'sudo systemctl restart aedwards-web'
```

## How It Works

1. **Poll** — Checks the monitored mailbox for unread messages
2. **Classify** — Uses AI to determine if each email is a real RFQ (ignores spam, general correspondence, etc.)
3. **Extract** — Parses RFQ details: customer info, line items, shipping destination
4. **Price** — Looks up pricing from the current price list
5. **Generate PDF** — Produces a formatted quote document
6. **Draft Reply** — Saves a draft email with the quote PDF attached in the mailbox's Drafts folder
7. **Archive** — Moves the processed email to a "Processed" subfolder

## Project Structure

```
src/allenedwards/
  cli.py          — CLI entry points (quote, monitor, etc.)
  monitor.py      — Inbox polling and pipeline orchestration
  outlook.py      — Microsoft Graph API client (auth, read, draft)
  providers/      — LLM extraction providers (Claude, MiniMax)
  pricing.py      — Price list lookup
  pdf.py          — Quote PDF generation
deploy/
  provision.sh    — Legacy EC2 provisioning script
  provision_do.sh — DigitalOcean provisioning script
  deploy.sh       — Monitor deployment script
  deploy_web.sh   — Web app deployment script
  aedwards-monitor.service — systemd unit file
  aedwards-web.service     — Web systemd unit file
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `O365_EMAIL` | Yes | Mailbox email address to monitor |
| `O365_PASSWORD` | Yes | Mailbox password (ROPC auth) |
| `O365_CLIENT_ID` | No | Azure AD client ID (default: MS Office public client) |
| `O365_SCOPES` | No | Graph API scopes (default: `https://graph.microsoft.com/.default`) |
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude |
| `LLM_PROVIDER` | No | `claude` or `minimax` (default: `claude`) |
| `MINIMAX_API_KEY` | If using MiniMax | MiniMax API key |
