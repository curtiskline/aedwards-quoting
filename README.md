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

## Deploy to AWS (EC2)

Two scripts are provided in `deploy/`:

### Fresh Instance (provision + deploy)

Provisions a new t3.nano EC2 instance, installs Python 3.13, deploys the app, and starts a systemd service.

```bash
# Set required env vars
export O365_EMAIL=<mailbox@yourdomain.com>
export O365_PASSWORD=<password>
export ANTHROPIC_API_KEY=<key>

# Optional overrides (these are the defaults)
export REGION=us-east-1
export INSTANCE_TYPE=t3.nano
export KEY_NAME=NoJobDevKey
export KEY_PATH=~/.ssh/NoJobDevKey.pem

bash deploy/provision.sh
```

### Redeploy to Existing Instance

Pushes updated code and restarts the service on an existing instance.

```bash
export O365_EMAIL=<mailbox@yourdomain.com>
export O365_PASSWORD=<password>
export ANTHROPIC_API_KEY=<key>

bash deploy/deploy.sh <ec2-host-or-ip>
```

### Checking Logs

```bash
ssh -i ~/.ssh/NoJobDevKey.pem ubuntu@<host> 'sudo journalctl -u aedwards-monitor -f'
```

### Restarting the Service

```bash
ssh -i ~/.ssh/NoJobDevKey.pem ubuntu@<host> 'sudo systemctl restart aedwards-monitor'
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
  provision.sh    — EC2 provisioning script
  deploy.sh       — Code deployment script
  aedwards-monitor.service — systemd unit file
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
