#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <ec2-host-or-ip>" >&2
  exit 1
fi

HOST="$1"
KEY_PATH="${KEY_PATH:-$HOME/.ssh/NoJobDevKey.pem}"
SSH_USER="${SSH_USER:-ubuntu}"
APP_DIR="${APP_DIR:-/opt/aedwards}"
APP_USER="${APP_USER:-aedwards}"
SERVICE_NAME="aedwards-web"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_FILE="${ROOT_DIR}/deploy/aedwards-web.service"
NGINX_FILE="${ROOT_DIR}/deploy/nginx-aedwards-web.conf"
DATABASE_URL="${DATABASE_URL:-sqlite:////opt/aedwards/instance/allenedwards.db}"
SECRET_KEY="${SECRET_KEY:-change-me-in-production}"

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
  --exclude='worktrees' \
  --exclude='.agent-*' \
  -czf "${SRC_TARBALL}" \
  -C "${ROOT_DIR}" .

cat > "${ENV_FILE}" <<ENVEOF
DATABASE_URL=${DATABASE_URL}
SECRET_KEY=${SECRET_KEY}
ENVEOF

SSH_OPTS=(-i "${KEY_PATH}" -o StrictHostKeyChecking=accept-new)

scp "${SSH_OPTS[@]}" "${SRC_TARBALL}" "${SSH_USER}@${HOST}:/tmp/aedwards-src.tgz"
scp "${SSH_OPTS[@]}" "${SERVICE_FILE}" "${SSH_USER}@${HOST}:/tmp/${SERVICE_NAME}.service"
scp "${SSH_OPTS[@]}" "${NGINX_FILE}" "${SSH_USER}@${HOST}:/tmp/${SERVICE_NAME}.nginx"
scp "${SSH_OPTS[@]}" "${ENV_FILE}" "${SSH_USER}@${HOST}:/tmp/aedwards-web.env"

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

if [[ ! -x "${APP_DIR}/venv/bin/python" ]]; then
  sudo -u "${APP_USER}" python3.13 -m venv "${APP_DIR}/venv"
fi

sudo -u "${APP_USER}" "${APP_DIR}/venv/bin/pip" install --upgrade pip setuptools wheel
sudo -u "${APP_USER}" "${APP_DIR}/venv/bin/pip" install --upgrade "${APP_DIR}/src"

sudo install -m 600 -o "${APP_USER}" -g "${APP_USER}" /tmp/aedwards-web.env "${APP_DIR}/.env"
sudo install -m 644 /tmp/${SERVICE_NAME}.service /etc/systemd/system/${SERVICE_NAME}.service
sudo install -m 644 /tmp/${SERVICE_NAME}.nginx /etc/nginx/sites-available/${SERVICE_NAME}
sudo ln -sf /etc/nginx/sites-available/${SERVICE_NAME} /etc/nginx/sites-enabled/${SERVICE_NAME}
sudo rm -f /etc/nginx/sites-enabled/default

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx
sudo systemctl --no-pager status "${SERVICE_NAME}" || true
REMOTE

echo "Web deploy complete to ${HOST}."
