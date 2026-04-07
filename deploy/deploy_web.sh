#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <host-or-ip>" >&2
  exit 1
fi

HOST="$1"
KEY_PATH="${KEY_PATH:-$HOME/.ssh/NoJobDevKey.pem}"
SSH_USER="${SSH_USER:-root}"
APP_DIR="${APP_DIR:-/opt/aedwards}"
APP_USER="${APP_USER:-aedwards}"
SERVICE_NAME="aedwards-web"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOTENV_FILE="${ROOT_DIR}/.env"
SERVICE_FILE="${ROOT_DIR}/deploy/aedwards-web.service"
NGINX_FILE="${ROOT_DIR}/deploy/nginx-aedwards-web.conf"

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

DATABASE_URL="${DATABASE_URL:-sqlite:////opt/aedwards/instance/allenedwards.db}"
if [[ -z "${SECRET_KEY}" ]]; then
  SECRET_KEY="$(openssl rand -hex 32)"
fi
O365_CLIENT_ID="${O365_CLIENT_ID:-d3590ed6-52b3-4102-aeff-aad2292ab01c}"
O365_SCOPES="${O365_SCOPES:-https://graph.microsoft.com/.default}"
LLM_PROVIDER="${LLM_PROVIDER:-claude}"

if [[ ! -f "${KEY_PATH}" ]]; then
  echo "SSH key not found: ${KEY_PATH}" >&2
  exit 1
fi

REMOTE_GMAIL_SERVICE_ACCOUNT_FILE=""
if [[ -n "${LOCAL_GMAIL_SERVICE_ACCOUNT_FILE}" ]]; then
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

tar \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='worktrees' \
  --exclude='.agent-*' \
  -czf "${SRC_TARBALL}" \
  -C "${ROOT_DIR}" .

{
  echo "DATABASE_URL=${DATABASE_URL}"
  echo "SECRET_KEY=${SECRET_KEY}"
  echo "O365_CLIENT_ID=${O365_CLIENT_ID}"
  echo "O365_SCOPES=${O365_SCOPES}"
  echo "LLM_PROVIDER=${LLM_PROVIDER}"
  if [[ -n "${O365_EMAIL}" ]]; then
    echo "O365_EMAIL=${O365_EMAIL}"
  fi
  if [[ -n "${O365_PASSWORD}" ]]; then
    echo "O365_PASSWORD=${O365_PASSWORD}"
  fi
  if [[ -n "${GMAIL_EMAIL}" ]]; then
    echo "GMAIL_EMAIL=${GMAIL_EMAIL}"
  fi
  if [[ -n "${GMAIL_CLIENT_ID}" ]]; then
    echo "GMAIL_CLIENT_ID=${GMAIL_CLIENT_ID}"
  fi
  if [[ -n "${GMAIL_CLIENT_SECRET}" ]]; then
    echo "GMAIL_CLIENT_SECRET=${GMAIL_CLIENT_SECRET}"
  fi
  if [[ -n "${GMAIL_REFRESH_TOKEN}" ]]; then
    echo "GMAIL_REFRESH_TOKEN=${GMAIL_REFRESH_TOKEN}"
  fi
  if [[ -n "${GMAIL_SCOPES}" ]]; then
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

SSH_OPTS=(-i "${KEY_PATH}" -o StrictHostKeyChecking=accept-new)

scp "${SSH_OPTS[@]}" "${SRC_TARBALL}" "${SSH_USER}@${HOST}:/tmp/aedwards-src.tgz"
scp "${SSH_OPTS[@]}" "${SERVICE_FILE}" "${SSH_USER}@${HOST}:/tmp/${SERVICE_NAME}.service"
scp "${SSH_OPTS[@]}" "${NGINX_FILE}" "${SSH_USER}@${HOST}:/tmp/${SERVICE_NAME}.nginx"
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

sudo mkdir -p "${APP_DIR}/src" "${APP_DIR}/instance"
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
sudo -u "${APP_USER}" "${APP_DIR}/venv/bin/pip" install --upgrade "${APP_DIR}/src"

if [[ -f "${APP_DIR}/.env" ]]; then
  sudo cp "${APP_DIR}/.env" /tmp/aedwards-existing.env
else
  sudo touch /tmp/aedwards-existing.env
fi

while IFS= read -r line || [[ -n "${line}" ]]; do
  [[ -z "${line}" ]] && continue
  key="${line%%=*}"
  value="${line#*=}"
  sudo sed -i "/^${key}=/d" /tmp/aedwards-existing.env
  printf '%s=%s\n' "${key}" "${value}" | sudo tee -a /tmp/aedwards-existing.env >/dev/null
done < /tmp/aedwards-web.env

sudo install -m 600 -o "${APP_USER}" -g "${APP_USER}" /tmp/aedwards-existing.env "${APP_DIR}/.env"
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
