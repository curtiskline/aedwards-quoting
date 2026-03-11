#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <ec2-host-or-ip>" >&2
  exit 1
fi

HOST="$1"
REGION="${REGION:-us-east-1}"
KEY_PATH="${KEY_PATH:-$HOME/.ssh/NoJobDevKey.pem}"
SSH_USER="${SSH_USER:-ubuntu}"
APP_DIR="${APP_DIR:-/opt/aedwards}"
APP_USER="${APP_USER:-aedwards}"
SERVICE_NAME="aedwards-monitor"

O365_EMAIL="${O365_EMAIL:?O365_EMAIL is required}"
O365_PASSWORD="${O365_PASSWORD:?O365_PASSWORD is required}"
O365_CLIENT_ID="${O365_CLIENT_ID:-d3590ed6-52b3-4102-aeff-aad2292ab01c}"
O365_SCOPES="${O365_SCOPES:-https://graph.microsoft.com/.default}"
LLM_PROVIDER="${LLM_PROVIDER:-claude}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_FILE="${ROOT_DIR}/deploy/aedwards-monitor.service"

if [[ ! -f "${KEY_PATH}" ]]; then
  echo "SSH key not found: ${KEY_PATH}" >&2
  exit 1
fi

if [[ -z "${ANTHROPIC_API_KEY}" && -f "${ROOT_DIR}/.env" ]]; then
  ANTHROPIC_API_KEY="$(sed -n 's/^ANTHROPIC_API_KEY=//p' "${ROOT_DIR}/.env" | head -n1)"
fi
if [[ -z "${ANTHROPIC_API_KEY}" ]]; then
  echo "ANTHROPIC_API_KEY is required (export it or set it in ${ROOT_DIR}/.env)." >&2
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
  -czf "${SRC_TARBALL}" \
  -C "${ROOT_DIR}" .

cat > "${ENV_FILE}" <<EOF
O365_EMAIL=${O365_EMAIL}
O365_PASSWORD=${O365_PASSWORD}
O365_CLIENT_ID=${O365_CLIENT_ID}
O365_SCOPES=${O365_SCOPES}
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
LLM_PROVIDER=${LLM_PROVIDER}
EOF

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

sudo install -m 600 -o "${APP_USER}" -g "${APP_USER}" /tmp/aedwards.env "${APP_DIR}/.env"
sudo install -m 644 /tmp/${SERVICE_NAME}.service /etc/systemd/system/${SERVICE_NAME}.service
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"
sudo systemctl --no-pager status "${SERVICE_NAME}" || true
REMOTE

echo "Deploy complete to ${HOST} (${REGION})."
echo "Tail logs with:"
echo "  ssh -i ${KEY_PATH} ${SSH_USER}@${HOST} 'sudo journalctl -u ${SERVICE_NAME} -f'"
