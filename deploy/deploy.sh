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
SERVICE_NAME="aedwards-monitor"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOTENV_FILE="${ROOT_DIR}/.env"

read_from_dotenv() {
  local key="$1"
  [[ -f "${DOTENV_FILE}" ]] || return 1
  sed -n "s/^${key}=//p" "${DOTENV_FILE}" | head -n1
}

O365_EMAIL="${O365_EMAIL:-$(read_from_dotenv O365_EMAIL || true)}"
O365_PASSWORD="${O365_PASSWORD:-$(read_from_dotenv O365_PASSWORD || true)}"
O365_CLIENT_ID="${O365_CLIENT_ID:-$(read_from_dotenv O365_CLIENT_ID || true)}"
O365_SCOPES="${O365_SCOPES:-$(read_from_dotenv O365_SCOPES || true)}"
LLM_PROVIDER="${LLM_PROVIDER:-$(read_from_dotenv LLM_PROVIDER || true)}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-$(read_from_dotenv ANTHROPIC_API_KEY || true)}"
MINIMAX_API_KEY="${MINIMAX_API_KEY:-$(read_from_dotenv MINIMAX_API_KEY || true)}"
MINIMAX_BASE_URL="${MINIMAX_BASE_URL:-$(read_from_dotenv MINIMAX_BASE_URL || true)}"
DATABASE_URL="${DATABASE_URL:-$(read_from_dotenv DATABASE_URL || true)}"
SECRET_KEY="${SECRET_KEY:-$(read_from_dotenv SECRET_KEY || true)}"
APP_URL="${APP_URL:-$(read_from_dotenv APP_URL || true)}"

O365_CLIENT_ID="${O365_CLIENT_ID:-d3590ed6-52b3-4102-aeff-aad2292ab01c}"
O365_SCOPES="${O365_SCOPES:-https://graph.microsoft.com/.default}"
LLM_PROVIDER="${LLM_PROVIDER:-claude}"
DATABASE_URL="${DATABASE_URL:-sqlite:////opt/aedwards/instance/allenedwards.db}"
if [[ -z "${SECRET_KEY}" ]]; then
  SECRET_KEY="$(openssl rand -hex 32)"
fi

if [[ -z "${O365_EMAIL}" ]]; then
  echo "O365_EMAIL is required (export it or set it in ${DOTENV_FILE})." >&2
  exit 1
fi
if [[ -z "${O365_PASSWORD}" ]]; then
  echo "O365_PASSWORD is required (export it or set it in ${DOTENV_FILE})." >&2
  exit 1
fi

SERVICE_FILE="${ROOT_DIR}/deploy/aedwards-monitor.service"

if [[ ! -f "${KEY_PATH}" ]]; then
  echo "SSH key not found: ${KEY_PATH}" >&2
  exit 1
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
  --exclude='data' \
  --exclude='worktrees' \
  --exclude='logs' \
  --exclude='monitor_output' \
  --exclude='.agent-*' \
  --exclude='*.db' \
  --exclude='*.mkv' \
  --exclude='*.mp4' \
  --exclude='*.mp3' \
  --exclude='*.xcf' \
  --exclude='*.png' \
  --exclude='*.jpg' \
  --exclude='*.jpeg' \
  --exclude='*.pdf' \
  --exclude='.monitor_state.json' \
  --exclude='drafts' \
  --exclude='node_modules' \
  --exclude='.env' \
  -czf "${SRC_TARBALL}" \
  -C "${ROOT_DIR}" .

{
  echo "O365_EMAIL=${O365_EMAIL}"
  echo "O365_PASSWORD=${O365_PASSWORD}"
  echo "O365_CLIENT_ID=${O365_CLIENT_ID}"
  echo "O365_SCOPES=${O365_SCOPES}"
  echo "LLM_PROVIDER=${LLM_PROVIDER}"
  echo "DATABASE_URL=${DATABASE_URL}"
  echo "SECRET_KEY=${SECRET_KEY}"
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
scp "${SSH_OPTS[@]}" "${ENV_FILE}" "${SSH_USER}@${HOST}:/tmp/aedwards.env"

ssh "${SSH_OPTS[@]}" "${SSH_USER}@${HOST}" bash <<'REMOTE'
set -euo pipefail

APP_DIR="/opt/aedwards"
APP_USER="aedwards"
SERVICE_NAME="aedwards-monitor"

if ! command -v python3.13 >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y software-properties-common
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt-get update
  sudo apt-get install -y python3.13 python3.13-venv python3.13-dev
fi

if ! id "${APP_USER}" >/dev/null 2>&1; then
  sudo useradd --system --create-home --home-dir "${APP_DIR}" --shell /bin/bash "${APP_USER}"
fi

sudo mkdir -p "${APP_DIR}/src" "${APP_DIR}/monitor_output"
sudo tar -xzf /tmp/aedwards-src.tgz -C "${APP_DIR}/src"
sudo chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

if [[ ! -x "${APP_DIR}/venv/bin/python" ]]; then
  sudo -u "${APP_USER}" python3.13 -m venv "${APP_DIR}/venv"
fi

sudo -u "${APP_USER}" "${APP_DIR}/venv/bin/pip" install --upgrade pip setuptools wheel
sudo -u "${APP_USER}" "${APP_DIR}/venv/bin/pip" install --upgrade "${APP_DIR}/src"

sudo tee /usr/local/bin/aedwards >/dev/null <<'WRAP'
#!/usr/bin/env bash
exec /opt/aedwards/venv/bin/allenedwards "$@"
WRAP
sudo chmod 755 /usr/local/bin/aedwards

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
done < /tmp/aedwards.env

sudo install -m 600 -o "${APP_USER}" -g "${APP_USER}" /tmp/aedwards-existing.env "${APP_DIR}/.env"
sudo install -m 644 /tmp/${SERVICE_NAME}.service /etc/systemd/system/${SERVICE_NAME}.service
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"
sudo systemctl --no-pager status "${SERVICE_NAME}" || true
if sudo systemctl is-enabled aedwards-web >/dev/null 2>&1; then
  sudo systemctl restart aedwards-web
  sudo systemctl --no-pager status aedwards-web || true
fi
REMOTE

echo "Deploy complete to ${HOST}."
echo "Tail logs with:"
echo "  ssh -i ${KEY_PATH} ${SSH_USER}@${HOST} 'sudo journalctl -u ${SERVICE_NAME} -f'"
