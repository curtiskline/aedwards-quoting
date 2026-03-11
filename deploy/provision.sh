#!/usr/bin/env bash
set -euo pipefail

REGION="${REGION:-us-east-1}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.nano}"
KEY_NAME="${KEY_NAME:-NoJobDevKey}"
KEY_PATH="${KEY_PATH:-$HOME/.ssh/NoJobDevKey.pem}"
SECURITY_GROUP_NAME="${SECURITY_GROUP_NAME:-aedwards-monitor-ssh}"
TAG_NAME="${TAG_NAME:-aedwards-monitor}"
SSH_USER="${SSH_USER:-ubuntu}"
APP_DIR="${APP_DIR:-/opt/aedwards}"
APP_USER="${APP_USER:-aedwards}"
SERVICE_NAME="aedwards-monitor"
AMI_SSM_PARAM="${AMI_SSM_PARAM:-/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id}"

O365_EMAIL="${O365_EMAIL:?O365_EMAIL is required}"
O365_PASSWORD="${O365_PASSWORD:?O365_PASSWORD is required}"
O365_CLIENT_ID="${O365_CLIENT_ID:-d3590ed6-52b3-4102-aeff-aad2292ab01c}"
O365_SCOPES="${O365_SCOPES:-https://graph.microsoft.com/.default}"
LLM_PROVIDER="${LLM_PROVIDER:-claude}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_FILE="${ROOT_DIR}/deploy/aedwards-monitor.service"

for cmd in aws ssh scp tar mktemp; do
  command -v "${cmd}" >/dev/null || { echo "Missing required command: ${cmd}" >&2; exit 1; }
done

if [[ ! -f "${KEY_PATH}" ]]; then
  echo "SSH key not found: ${KEY_PATH}" >&2
  exit 1
fi

if [[ ! -f "${SERVICE_FILE}" ]]; then
  echo "Service file not found: ${SERVICE_FILE}" >&2
  exit 1
fi

if [[ -z "${ANTHROPIC_API_KEY}" && -f "${ROOT_DIR}/.env" ]]; then
  ANTHROPIC_API_KEY="$(sed -n 's/^ANTHROPIC_API_KEY=//p' "${ROOT_DIR}/.env" | head -n1)"
fi
if [[ -z "${ANTHROPIC_API_KEY}" ]]; then
  echo "ANTHROPIC_API_KEY is required (export it or set it in ${ROOT_DIR}/.env)." >&2
  exit 1
fi

echo "Resolving Ubuntu 24.04 AMI in ${REGION}..."
AMI_ID="$(
  aws ssm get-parameter \
    --region "${REGION}" \
    --name "${AMI_SSM_PARAM}" \
    --query 'Parameter.Value' \
    --output text
)"
if [[ -z "${AMI_ID}" || "${AMI_ID}" == "None" ]]; then
  echo "Could not resolve AMI from SSM parameter ${AMI_SSM_PARAM}" >&2
  exit 1
fi
echo "Using AMI ${AMI_ID}"

VPC_ID="$(aws ec2 describe-vpcs --region "${REGION}" --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"
if [[ -z "${VPC_ID}" || "${VPC_ID}" == "None" ]]; then
  echo "No default VPC found in ${REGION}. Set up networking first." >&2
  exit 1
fi

SG_ID="$(
  aws ec2 describe-security-groups \
    --region "${REGION}" \
    --filters "Name=group-name,Values=${SECURITY_GROUP_NAME}" "Name=vpc-id,Values=${VPC_ID}" \
    --query 'SecurityGroups[0].GroupId' \
    --output text
)"
if [[ -z "${SG_ID}" || "${SG_ID}" == "None" ]]; then
  echo "Creating security group ${SECURITY_GROUP_NAME}..."
  SG_ID="$(
    aws ec2 create-security-group \
      --region "${REGION}" \
      --group-name "${SECURITY_GROUP_NAME}" \
      --description "SSH-only access for aedwards monitor" \
      --vpc-id "${VPC_ID}" \
      --query 'GroupId' \
      --output text
  )"
fi

RULE_EXISTS="$(
  aws ec2 describe-security-groups \
    --region "${REGION}" \
    --group-ids "${SG_ID}" \
    --query "SecurityGroups[0].IpPermissions[?FromPort==\`22\` && ToPort==\`22\` && IpProtocol=='tcp'] | length(@)" \
    --output text
)"
if [[ "${RULE_EXISTS}" == "0" ]]; then
  aws ec2 authorize-security-group-ingress \
    --region "${REGION}" \
    --group-id "${SG_ID}" \
    --ip-permissions IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges='[{CidrIp=0.0.0.0/0,Description="SSH"}]'
fi

echo "Launching ${INSTANCE_TYPE} instance..."
INSTANCE_ID="$(
  aws ec2 run-instances \
    --region "${REGION}" \
    --image-id "${AMI_ID}" \
    --instance-type "${INSTANCE_TYPE}" \
    --key-name "${KEY_NAME}" \
    --security-group-ids "${SG_ID}" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${TAG_NAME}}]" \
    --query 'Instances[0].InstanceId' \
    --output text
)"
echo "Created instance ${INSTANCE_ID}. Waiting for running state..."
aws ec2 wait instance-running --region "${REGION}" --instance-ids "${INSTANCE_ID}"

PUBLIC_IP="$(
  aws ec2 describe-instances \
    --region "${REGION}" \
    --instance-ids "${INSTANCE_ID}" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text
)"
if [[ -z "${PUBLIC_IP}" || "${PUBLIC_IP}" == "None" ]]; then
  echo "Instance ${INSTANCE_ID} has no public IP." >&2
  exit 1
fi
echo "Instance ready at ${PUBLIC_IP}"

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

echo "Waiting for SSH on ${PUBLIC_IP}..."
for _ in {1..30}; do
  if ssh "${SSH_OPTS[@]}" "${SSH_USER}@${PUBLIC_IP}" 'echo SSH ready' >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

scp "${SSH_OPTS[@]}" "${SRC_TARBALL}" "${SSH_USER}@${PUBLIC_IP}:/tmp/aedwards-src.tgz"
scp "${SSH_OPTS[@]}" "${SERVICE_FILE}" "${SSH_USER}@${PUBLIC_IP}:/tmp/${SERVICE_NAME}.service"
scp "${SSH_OPTS[@]}" "${ENV_FILE}" "${SSH_USER}@${PUBLIC_IP}:/tmp/aedwards.env"

ssh "${SSH_OPTS[@]}" "${SSH_USER}@${PUBLIC_IP}" bash <<'REMOTE'
set -euo pipefail

APP_USER="aedwards"
APP_DIR="/opt/aedwards"
SERVICE_NAME="aedwards-monitor"

sudo apt-get update
sudo apt-get install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install -y python3.13 python3.13-venv python3.13-dev

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

echo
echo "Provisioning complete."
echo "Instance ID: ${INSTANCE_ID}"
echo "Public IP: ${PUBLIC_IP}"
echo "Check logs with:"
echo "  ssh -i ${KEY_PATH} ${SSH_USER}@${PUBLIC_IP} 'sudo journalctl -u ${SERVICE_NAME} -f'"
