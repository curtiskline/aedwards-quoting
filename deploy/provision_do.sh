#!/usr/bin/env bash
set -euo pipefail

DROPLET_NAME="${DROPLET_NAME:-aedwards}"
DO_REGION="${DO_REGION:-nyc1}"
DO_SIZE="${DO_SIZE:-s-2vcpu-2gb}"
DO_IMAGE="${DO_IMAGE:-ubuntu-24-04-x64}"
DO_SSH_KEY_FINGERPRINT="${DO_SSH_KEY_FINGERPRINT:-}"
FIREWALL_NAME="${FIREWALL_NAME:-${DROPLET_NAME}-fw}"

if ! command -v doctl >/dev/null 2>&1; then
  cat >&2 <<'EOF'
doctl is not installed.

Manual DigitalOcean provisioning steps:
1. Create droplet:
   Name: aedwards
   Region: nyc1 (or nearest)
   Image: Ubuntu 24.04 x64
   Size: Basic / 2 vCPU / 2 GB (s-2vcpu-2gb)
   SSH key: NoJobDevKey
2. Create cloud firewall and attach droplet:
   - Inbound TCP: 22 from 0.0.0.0/0
   - Inbound TCP: 80 from 0.0.0.0/0
   - Inbound TCP: 443 from 0.0.0.0/0
3. Deploy monitor and web:
   bash deploy/deploy.sh <droplet-ip>
   bash deploy/deploy_web.sh <droplet-ip>
EOF
  exit 1
fi

if ! doctl account get >/dev/null 2>&1; then
  cat >&2 <<'EOF'
doctl is not authenticated.
Run one of:
  doctl auth init -t <token>
or set:
  export DIGITALOCEAN_ACCESS_TOKEN=<token>
EOF
  exit 1
fi

if [[ -z "${DO_SSH_KEY_FINGERPRINT}" ]]; then
  echo "DO_SSH_KEY_FINGERPRINT not set. Available SSH keys:"
  doctl compute ssh-key list
  cat >&2 <<'EOF'

Set the key fingerprint and rerun:
  export DO_SSH_KEY_FINGERPRINT=<fingerprint>
  bash deploy/provision_do.sh
EOF
  exit 1
fi

existing_line="$(
  doctl compute droplet list --format ID,Name,PublicIPv4,Status --no-header \
    | awk -v name="${DROPLET_NAME}" '$2 == name { print $0; exit }'
)"

if [[ -n "${existing_line}" ]]; then
  DROPLET_ID="$(echo "${existing_line}" | awk '{print $1}')"
  DROPLET_IP="$(echo "${existing_line}" | awk '{print $3}')"
  echo "Using existing droplet ${DROPLET_NAME} (id=${DROPLET_ID}, ip=${DROPLET_IP})"
else
  echo "Creating droplet ${DROPLET_NAME} (${DO_SIZE}, ${DO_REGION}, ${DO_IMAGE})..."
  created_line="$(
    doctl compute droplet create "${DROPLET_NAME}" \
      --image "${DO_IMAGE}" \
      --size "${DO_SIZE}" \
      --region "${DO_REGION}" \
      --ssh-keys "${DO_SSH_KEY_FINGERPRINT}" \
      --wait \
      --format ID,Name,PublicIPv4,Status \
      --no-header
  )"
  DROPLET_ID="$(echo "${created_line}" | awk '{print $1}')"
  DROPLET_IP="$(echo "${created_line}" | awk '{print $3}')"
  echo "Created droplet id=${DROPLET_ID}, ip=${DROPLET_IP}"
fi

existing_fw_id="$(
  doctl compute firewall list --format ID,Name --no-header \
    | awk -v fw="${FIREWALL_NAME}" '$2 == fw { print $1; exit }'
)"

if [[ -n "${existing_fw_id}" ]]; then
  echo "Firewall ${FIREWALL_NAME} already exists (${existing_fw_id}); ensuring droplet is attached."
  doctl compute firewall add-droplets "${existing_fw_id}" --droplet-ids "${DROPLET_ID}" >/dev/null
else
  echo "Creating firewall ${FIREWALL_NAME}..."
  doctl compute firewall create \
    --name "${FIREWALL_NAME}" \
    --droplet-ids "${DROPLET_ID}" \
    --inbound-rules "protocol:tcp,ports:22,address:0.0.0.0/0 protocol:tcp,ports:80,address:0.0.0.0/0 protocol:tcp,ports:443,address:0.0.0.0/0" \
    --outbound-rules "protocol:tcp,ports:1-65535,address:0.0.0.0/0 protocol:udp,ports:1-65535,address:0.0.0.0/0 protocol:icmp,address:0.0.0.0/0" \
    --format ID,Name,Status
fi

echo
echo "Provisioning complete."
echo "Droplet IP: ${DROPLET_IP}"
echo
echo "Deploy commands:"
echo "  bash deploy/deploy.sh ${DROPLET_IP}"
echo "  bash deploy/deploy_web.sh ${DROPLET_IP}"
