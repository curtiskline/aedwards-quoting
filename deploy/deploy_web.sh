#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [--dry-run] [--env staging|prod] <host-or-ip>" >&2
  echo "  --dry-run   print the resulting host .env diff and apply nothing" >&2
  echo "  --env       assert the deploy target; aborts if it contradicts the known host map" >&2
}

DRY_RUN=false
TARGET_ENV=""
POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --env) TARGET_ENV="${2:-}"; shift 2 ;;
    --env=*) TARGET_ENV="${1#--env=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "Unknown option: $1" >&2; usage; exit 1 ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done

if [[ ${#POSITIONAL[@]} -ne 1 ]]; then
  usage
  exit 1
fi

HOST="${POSITIONAL[0]}"
KEY_PATH="${KEY_PATH:-$HOME/.ssh/NoJobDevKey.pem}"
SSH_USER="${SSH_USER:-root}"
APP_DIR="${APP_DIR:-/opt/aedwards}"
APP_USER="${APP_USER:-aedwards}"
SERVICE_NAME="aedwards-web"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOTENV_FILE="${ROOT_DIR}/.env"
SERVICE_FILE="${ROOT_DIR}/deploy/aedwards-web.service"
NGINX_FILE="${ROOT_DIR}/deploy/nginx-aedwards-web.conf"
MERGE_ENV_FILE="${ROOT_DIR}/deploy/merge_env.sh"

# Known host map (docs/runbooks/postgres-cutover.md). Anything else is "unknown"
# and deploys with a loud warning; --env only cross-checks, it cannot re-label.
detect_env() {
  case "$1" in
    134.122.29.15|staging.quotes.vectorforgeinteractive.com) echo staging ;;
    157.230.227.28|quotes.allanedwards.io) echo prod ;;
    *) echo unknown ;;
  esac
}

DETECTED_ENV="$(detect_env "${HOST}")"
if [[ -n "${TARGET_ENV}" ]]; then
  if [[ "${TARGET_ENV}" != "staging" && "${TARGET_ENV}" != "prod" ]]; then
    echo "--env must be 'staging' or 'prod', got: ${TARGET_ENV}" >&2
    exit 1
  fi
  if [[ "${DETECTED_ENV}" != "unknown" && "${DETECTED_ENV}" != "${TARGET_ENV}" ]]; then
    echo "ABORT: --env ${TARGET_ENV} but ${HOST} is the known ${DETECTED_ENV} host." >&2
    exit 1
  fi
  EFFECTIVE_ENV="${TARGET_ENV}"
else
  EFFECTIVE_ENV="${DETECTED_ENV}"
fi

echo "=============================================================="
echo "  DEPLOY TARGET: ${HOST}  [${EFFECTIVE_ENV^^}]$( [[ "${DRY_RUN}" == true ]] && echo '  (DRY RUN — nothing will be applied)' )"
echo "=============================================================="
if [[ "${EFFECTIVE_ENV}" == "unknown" ]]; then
  echo "WARNING: ${HOST} is not in the known host map (staging=134.122.29.15, prod=157.230.227.28)." >&2
  echo "WARNING: pass --env staging|prod to assert the target." >&2
fi

read_from_dotenv() {
  local key="$1"
  [[ -f "${DOTENV_FILE}" ]] || return 1
  sed -n "s/^${key}=//p" "${DOTENV_FILE}" | head -n1
}

DATABASE_URL="${DATABASE_URL:-$(read_from_dotenv DATABASE_URL || true)}"
SECRET_KEY="${SECRET_KEY:-$(read_from_dotenv SECRET_KEY || true)}"
O365_EMAIL="${O365_EMAIL:-$(read_from_dotenv O365_EMAIL || true)}"
O365_PASSWORD="${O365_PASSWORD:-$(read_from_dotenv O365_PASSWORD || true)}"
O365_CLIENT_ID="${O365_CLIENT_ID:-$(read_from_dotenv O365_CLIENT_ID || true)}"
O365_SCOPES="${O365_SCOPES:-$(read_from_dotenv O365_SCOPES || true)}"
GMAIL_EMAIL="${GMAIL_EMAIL:-$(read_from_dotenv GMAIL_EMAIL || true)}"
GMAIL_CLIENT_ID="${GMAIL_CLIENT_ID:-$(read_from_dotenv GMAIL_CLIENT_ID || true)}"
GMAIL_CLIENT_SECRET="${GMAIL_CLIENT_SECRET:-$(read_from_dotenv GMAIL_CLIENT_SECRET || true)}"
GMAIL_REFRESH_TOKEN="${GMAIL_REFRESH_TOKEN:-$(read_from_dotenv GMAIL_REFRESH_TOKEN || true)}"
GMAIL_SCOPES="${GMAIL_SCOPES:-$(read_from_dotenv GMAIL_SCOPES || true)}"
LOCAL_GMAIL_SERVICE_ACCOUNT_FILE="${GMAIL_SERVICE_ACCOUNT_FILE:-$(read_from_dotenv GMAIL_SERVICE_ACCOUNT_FILE || true)}"
EMAIL_PROVIDER="${EMAIL_PROVIDER:-$(read_from_dotenv EMAIL_PROVIDER || true)}"
ENABLE_DB_WRITES="${ENABLE_DB_WRITES:-$(read_from_dotenv ENABLE_DB_WRITES || true)}"
ENABLE_OUTLOOK_DRAFTS="${ENABLE_OUTLOOK_DRAFTS:-$(read_from_dotenv ENABLE_OUTLOOK_DRAFTS || true)}"
LLM_PROVIDER="${LLM_PROVIDER:-$(read_from_dotenv LLM_PROVIDER || true)}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-$(read_from_dotenv ANTHROPIC_API_KEY || true)}"
MINIMAX_API_KEY="${MINIMAX_API_KEY:-$(read_from_dotenv MINIMAX_API_KEY || true)}"
MINIMAX_BASE_URL="${MINIMAX_BASE_URL:-$(read_from_dotenv MINIMAX_BASE_URL || true)}"
APP_URL="${APP_URL:-$(read_from_dotenv APP_URL || true)}"
QUOTE_ARTIFACT_DIR="${QUOTE_ARTIFACT_DIR:-$(read_from_dotenv QUOTE_ARTIFACT_DIR || true)}"
SERVER_NAME="${SERVER_NAME:-_}"

# EMAIL_DELIVERY_ENABLED is NEVER written to the host by this script (the
# merge in deploy/merge_env.sh ignores it and the host value governs; absent
# means false). Setting it =true locally only bundles mail credentials into
# the deploy — it cannot turn delivery on.
BUNDLE_MAIL_CREDS=false
if [[ -n "${EMAIL_DELIVERY_ENABLED:-}" && "${EMAIL_DELIVERY_ENABLED,,}" != "false" && "${EMAIL_DELIVERY_ENABLED,,}" != "0" && "${EMAIL_DELIVERY_ENABLED,,}" != "no" ]]; then
  BUNDLE_MAIL_CREDS=true
  echo "NOTE: EMAIL_DELIVERY_ENABLED=${EMAIL_DELIVERY_ENABLED} bundles mail credentials but does NOT enable" >&2
  echo "NOTE: delivery on the host — only the host's own .env governs that (edit it manually to enable)." >&2
fi

# DATABASE_URL: the host's current value is authoritative (deploy/merge_env.sh
# preserves it; the Postgres cutover edits it in place on the droplet, runbook
# docs/runbooks/postgres-cutover.md). A value here is only used when the host
# has none, and there is deliberately NO sqlite fallback: a deploy with no
# DATABASE_URL anywhere fails before touching the host.
QUOTE_ARTIFACT_DIR="${QUOTE_ARTIFACT_DIR:-${APP_DIR}/instance/quote_versions}"
O365_CLIENT_ID="${O365_CLIENT_ID:-d3590ed6-52b3-4102-aeff-aad2292ab01c}"
O365_SCOPES="${O365_SCOPES:-https://graph.microsoft.com/.default}"
LLM_PROVIDER="${LLM_PROVIDER:-claude}"

if [[ ! -f "${KEY_PATH}" ]]; then
  echo "SSH key not found: ${KEY_PATH}" >&2
  exit 1
fi


REMOTE_GMAIL_SERVICE_ACCOUNT_FILE=""
if [[ "${BUNDLE_MAIL_CREDS}" == true && -n "${LOCAL_GMAIL_SERVICE_ACCOUNT_FILE}" ]]; then
  if [[ ! -f "${LOCAL_GMAIL_SERVICE_ACCOUNT_FILE}" ]]; then
    echo "GMAIL_SERVICE_ACCOUNT_FILE not found: ${LOCAL_GMAIL_SERVICE_ACCOUNT_FILE}" >&2
    exit 1
  fi
  REMOTE_GMAIL_SERVICE_ACCOUNT_FILE="${APP_DIR}/secrets/gmail-service-account.json"
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

SRC_TARBALL="${TMP_DIR}/aedwards-src.tgz"
ENV_FILE="${TMP_DIR}/.env"
RENDERED_NGINX_FILE="${TMP_DIR}/${SERVICE_NAME}.nginx"

{
  # No EMAIL_DELIVERY_ENABLED line here, ever: the host value governs delivery
  # and merge_env.sh would ignore it anyway (defaulting an absent key to false).
  if [[ -n "${DATABASE_URL}" ]]; then
    echo "DATABASE_URL=${DATABASE_URL}"
  fi
  echo "QUOTE_ARTIFACT_DIR=${QUOTE_ARTIFACT_DIR}"
  if [[ -n "${SECRET_KEY}" ]]; then
    echo "SECRET_KEY=${SECRET_KEY}"
  fi
  echo "O365_CLIENT_ID=${O365_CLIENT_ID}"
  echo "O365_SCOPES=${O365_SCOPES}"
  echo "LLM_PROVIDER=${LLM_PROVIDER}"
  if [[ "${BUNDLE_MAIL_CREDS}" == true && -n "${O365_EMAIL}" ]]; then
    echo "O365_EMAIL=${O365_EMAIL}"
  fi
  if [[ "${BUNDLE_MAIL_CREDS}" == true && -n "${O365_PASSWORD}" ]]; then
    echo "O365_PASSWORD=${O365_PASSWORD}"
  fi
  if [[ "${BUNDLE_MAIL_CREDS}" == true && -n "${GMAIL_EMAIL}" ]]; then
    echo "GMAIL_EMAIL=${GMAIL_EMAIL}"
  fi
  if [[ "${BUNDLE_MAIL_CREDS}" == true && -n "${GMAIL_CLIENT_ID}" ]]; then
    echo "GMAIL_CLIENT_ID=${GMAIL_CLIENT_ID}"
  fi
  if [[ "${BUNDLE_MAIL_CREDS}" == true && -n "${GMAIL_CLIENT_SECRET}" ]]; then
    echo "GMAIL_CLIENT_SECRET=${GMAIL_CLIENT_SECRET}"
  fi
  if [[ "${BUNDLE_MAIL_CREDS}" == true && -n "${GMAIL_REFRESH_TOKEN}" ]]; then
    echo "GMAIL_REFRESH_TOKEN=${GMAIL_REFRESH_TOKEN}"
  fi
  if [[ "${BUNDLE_MAIL_CREDS}" == true && -n "${GMAIL_SCOPES}" ]]; then
    echo "GMAIL_SCOPES=${GMAIL_SCOPES}"
  fi
  if [[ -n "${REMOTE_GMAIL_SERVICE_ACCOUNT_FILE}" ]]; then
    echo "GMAIL_SERVICE_ACCOUNT_FILE=${REMOTE_GMAIL_SERVICE_ACCOUNT_FILE}"
  fi
  if [[ -n "${EMAIL_PROVIDER}" ]]; then
    echo "EMAIL_PROVIDER=${EMAIL_PROVIDER}"
  fi
  if [[ -n "${ENABLE_DB_WRITES}" ]]; then
    echo "ENABLE_DB_WRITES=${ENABLE_DB_WRITES}"
  fi
  if [[ -n "${ENABLE_OUTLOOK_DRAFTS}" ]]; then
    echo "ENABLE_OUTLOOK_DRAFTS=${ENABLE_OUTLOOK_DRAFTS}"
  fi
  if [[ -n "${ANTHROPIC_API_KEY}" ]]; then
    echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}"
  fi
  if [[ -n "${MINIMAX_API_KEY}" ]]; then
    echo "MINIMAX_API_KEY=${MINIMAX_API_KEY}"
  fi
  if [[ -n "${MINIMAX_BASE_URL}" ]]; then
    echo "MINIMAX_BASE_URL=${MINIMAX_BASE_URL}"
  fi
  if [[ -n "${APP_URL}" ]]; then
    echo "APP_URL=${APP_URL}"
  fi
} > "${ENV_FILE}"

sed "s|__SERVER_NAME__|${SERVER_NAME}|g" "${NGINX_FILE}" > "${RENDERED_NGINX_FILE}"

SSH_OPTS=(-i "${KEY_PATH}" -o StrictHostKeyChecking=accept-new)

# Preflight: fetch the host's current .env and run the same merge the host
# will run. This fails BEFORE any remote change when the merge would (e.g. no
# DATABASE_URL anywhere), and gives --dry-run its diff.
HOST_ENV_SNAPSHOT="${TMP_DIR}/host.env"
MERGED_PREVIEW="${TMP_DIR}/merged-preview.env"
# shellcheck disable=SC2029  # APP_DIR expanding client-side is intentional
ssh "${SSH_OPTS[@]}" "${SSH_USER}@${HOST}" "cat ${APP_DIR}/.env 2>/dev/null || true" > "${HOST_ENV_SNAPSHOT}"
bash "${MERGE_ENV_FILE}" "${HOST_ENV_SNAPSHOT}" "${ENV_FILE}" "${MERGED_PREVIEW}"

echo ""
echo "Resulting host .env change (current -> after deploy; SECRET_KEY may additionally be generated host-side if absent):"
if diff -u "${HOST_ENV_SNAPSHOT}" "${MERGED_PREVIEW}"; then
  echo "(no .env changes)"
fi
echo ""

if [[ "${DRY_RUN}" == true ]]; then
  echo "DRY RUN: nothing was copied or applied to ${HOST}."
  exit 0
fi

tar \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='worktrees' \
  --exclude='.agent-*' \
  --exclude='data' \
  --exclude='logs' \
  --exclude='drafts' \
  --exclude='examples' \
  --exclude='dist' \
  --exclude='work' \
  --exclude='canon' \
  --exclude='monitor_output' \
  --exclude='*.mkv' \
  --exclude='*.mp3' \
  --exclude='*.xcf' \
  --exclude='.monitor_state.json' \
  -czf "${SRC_TARBALL}" \
  -C "${ROOT_DIR}" .

scp "${SSH_OPTS[@]}" "${SRC_TARBALL}" "${SSH_USER}@${HOST}:/tmp/aedwards-src.tgz"
scp "${SSH_OPTS[@]}" "${MERGE_ENV_FILE}" "${SSH_USER}@${HOST}:/tmp/aedwards-merge-env.sh"
scp "${SSH_OPTS[@]}" "${SERVICE_FILE}" "${SSH_USER}@${HOST}:/tmp/${SERVICE_NAME}.service"
scp "${SSH_OPTS[@]}" "${RENDERED_NGINX_FILE}" "${SSH_USER}@${HOST}:/tmp/${SERVICE_NAME}.nginx"
scp "${SSH_OPTS[@]}" "${ENV_FILE}" "${SSH_USER}@${HOST}:/tmp/aedwards-web.env"
if [[ -n "${REMOTE_GMAIL_SERVICE_ACCOUNT_FILE}" ]]; then
  scp "${SSH_OPTS[@]}" "${LOCAL_GMAIL_SERVICE_ACCOUNT_FILE}" "${SSH_USER}@${HOST}:/tmp/gmail-service-account.json"
fi

ssh "${SSH_OPTS[@]}" "${SSH_USER}@${HOST}" bash <<'REMOTE'
set -euo pipefail

APP_DIR="/opt/aedwards"
APP_USER="aedwards"
SERVICE_NAME="aedwards-web"

sudo apt-get update
sudo apt-get install -y software-properties-common nginx
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install -y python3.13 python3.13-venv python3.13-dev

if ! id "${APP_USER}" >/dev/null 2>&1; then
  sudo useradd --system --create-home --home-dir "${APP_DIR}" --shell /bin/bash "${APP_USER}"
fi

# Wipe the old source tree first: extracting over it leaves stale build/ and
# egg-info artifacts that poison the wheel build with old module versions.
sudo rm -rf "${APP_DIR}/src"
sudo mkdir -p "${APP_DIR}/src" "${APP_DIR}/instance/quote_versions"
sudo tar -xzf /tmp/aedwards-src.tgz -C "${APP_DIR}/src"
sudo chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

if [[ -f /tmp/gmail-service-account.json ]]; then
  sudo mkdir -p "${APP_DIR}/secrets"
  sudo install -m 600 -o "${APP_USER}" -g "${APP_USER}" /tmp/gmail-service-account.json "${APP_DIR}/secrets/gmail-service-account.json"
fi

if [[ ! -x "${APP_DIR}/venv/bin/python" ]]; then
  sudo -u "${APP_USER}" python3.13 -m venv "${APP_DIR}/venv"
fi

sudo -u "${APP_USER}" "${APP_DIR}/venv/bin/pip" install --upgrade pip setuptools wheel
sudo -u "${APP_USER}" "${APP_DIR}/venv/bin/pip" install --upgrade --no-cache-dir "${APP_DIR}/src"

if [[ -f "${APP_DIR}/.env" ]]; then
  sudo cp "${APP_DIR}/.env" /tmp/aedwards-existing.env
else
  sudo touch /tmp/aedwards-existing.env
fi

# The host .env is authoritative for environment-specific values: merge_env.sh
# preserves existing DATABASE_URL / SECRET_KEY / EMAIL_DELIVERY_ENABLED
# (delivery defaults to false when absent, never true), strips mail
# credentials on delivery-off hosts, and hard-fails rather than fall back to
# sqlite when no DATABASE_URL exists anywhere.
sudo bash /tmp/aedwards-merge-env.sh /tmp/aedwards-existing.env /tmp/aedwards-web.env /tmp/aedwards-merged.env

if grep -qiE '^EMAIL_DELIVERY_ENABLED=(false|0|no)$' /tmp/aedwards-merged.env; then
  sudo rm -f "${APP_DIR}/secrets/gmail-service-account.json"
fi

# Generate SECRET_KEY on server if not already set
if ! grep -q '^SECRET_KEY=' /tmp/aedwards-merged.env || [[ -z "$(sed -n 's/^SECRET_KEY=//p' /tmp/aedwards-merged.env)" ]]; then
  sudo sed -i '/^SECRET_KEY=/d' /tmp/aedwards-merged.env
  printf 'SECRET_KEY=%s\n' "$(openssl rand -hex 32)" | sudo tee -a /tmp/aedwards-merged.env >/dev/null
fi

sudo install -m 600 -o "${APP_USER}" -g "${APP_USER}" /tmp/aedwards-merged.env "${APP_DIR}/.env"
sudo install -m 644 /tmp/${SERVICE_NAME}.service /etc/systemd/system/${SERVICE_NAME}.service
if grep -q ssl_certificate /etc/nginx/sites-enabled/${SERVICE_NAME} 2>/dev/null; then
  echo "Skipping nginx config — certbot SSL config already in place"
else
  sudo install -m 644 /tmp/${SERVICE_NAME}.nginx /etc/nginx/sites-available/${SERVICE_NAME}
  sudo ln -sf /etc/nginx/sites-available/${SERVICE_NAME} /etc/nginx/sites-enabled/${SERVICE_NAME}
  sudo rm -f /etc/nginx/sites-enabled/default
fi

echo "Running Alembic migrations..."
sudo -u "${APP_USER}" bash -c "set -a; source ${APP_DIR}/.env; set +a; cd ${APP_DIR}/src && ${APP_DIR}/venv/bin/alembic upgrade head"

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx
sudo systemctl --no-pager status "${SERVICE_NAME}" || true
REMOTE

echo "Web deploy complete to ${HOST}."
