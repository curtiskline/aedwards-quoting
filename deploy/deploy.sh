#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [--dry-run] [--env staging|prod] [--env-file <path>] <host-or-ip>" >&2
  echo "  --dry-run   print the resulting host .env diff and apply nothing" >&2
  echo "  --env       assert the deploy target; aborts if it contradicts the known host map" >&2
  echo "  --env-file  read credentials/config from this file instead of <repo>/.env" >&2
  echo "              (deploy worktrees have no .env; without this flag the canonical" >&2
  echo "              checkout's .env is used when one can be located)" >&2
}

DRY_RUN=false
TARGET_ENV=""
ENV_FILE_ARG=""
POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --env) TARGET_ENV="${2:-}"; shift 2 ;;
    --env=*) TARGET_ENV="${1#--env=}"; shift ;;
    --env-file) ENV_FILE_ARG="${2:-}"; shift 2 ;;
    --env-file=*) ENV_FILE_ARG="${1#--env-file=}"; shift ;;
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
SERVICE_NAME="aedwards-monitor"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOTENV_FILE="${ROOT_DIR}/.env"
SERVICE_FILE="${ROOT_DIR}/deploy/aedwards-monitor.service"
MERGE_ENV_FILE="${ROOT_DIR}/deploy/merge_env.sh"
REDACT_ENV_FILE="${ROOT_DIR}/deploy/redact_env.sh"

# Credential env file resolution (task 509). Deploy worktrees have no .env by
# design, and deploying without one must never substitute built-in defaults
# over real host values. --env-file is explicit; otherwise, when the repo has
# no .env, fall back to the canonical checkout's .env (the file a main-checkout
# deploy would read), located via the shared git common dir.
if [[ -n "${ENV_FILE_ARG}" ]]; then
  if [[ ! -f "${ENV_FILE_ARG}" ]]; then
    echo "--env-file not found: ${ENV_FILE_ARG}" >&2
    exit 1
  fi
  DOTENV_FILE="${ENV_FILE_ARG}"
elif [[ ! -f "${DOTENV_FILE}" ]]; then
  GIT_COMMON_DIR="$(git -C "${ROOT_DIR}" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  if [[ -n "${GIT_COMMON_DIR}" && -f "$(dirname "${GIT_COMMON_DIR}")/.env" ]]; then
    DOTENV_FILE="$(dirname "${GIT_COMMON_DIR}")/.env"
    echo "NOTE: no .env in ${ROOT_DIR}; using the canonical checkout's env file ${DOTENV_FILE} (override with --env-file)." >&2
  fi
fi
HAVE_ENV_FILE=false
if [[ -f "${DOTENV_FILE}" ]]; then
  HAVE_ENV_FILE=true
fi

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
QUOTE_ARTIFACT_DIR="${QUOTE_ARTIFACT_DIR:-$(read_from_dotenv QUOTE_ARTIFACT_DIR || true)}"
ENABLE_MONITOR="${ENABLE_MONITOR:-true}"

# Built-in defaults (O365_CLIENT_ID, O365_SCOPES, LLM_PROVIDER,
# QUOTE_ARTIFACT_DIR) are applied host-aware, AFTER the host .env snapshot is
# fetched below: an explicit local value always wins; a key with no local value
# but an existing host value is omitted from the fragment so the host value is
# preserved; a default is written only for a host that has never set the key.
# Missing monitor credentials (O365_EMAIL, and O365_CLIENT_ID absent from any
# env file) abort rather than silently defaulting (task 509).

# DATABASE_URL: the host's current value is authoritative (deploy/merge_env.sh
# preserves it; the Postgres cutover edits it in place on the droplet, runbook
# docs/runbooks/postgres-cutover.md). A value here is only used when the host
# has none, and there is deliberately NO sqlite fallback: a deploy with no
# DATABASE_URL anywhere fails in the preflight, before touching the host.
# SECRET_KEY: the host's value is likewise preserved; when neither side has
# one it is generated ON THE HOST, never here.
# EMAIL_DELIVERY_ENABLED is never written by this script at all — the host
# value governs delivery and merge_env.sh defaults an absent key to false.
# ENABLE_MONITOR is host-authoritative in the merge: the default above applies
# only to hosts that have never set it, so a deploy cannot flip a monitor-off
# host (staging) back to polling a live mailbox.

if [[ ! -f "${KEY_PATH}" ]]; then
  echo "SSH key not found: ${KEY_PATH}" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

SRC_TARBALL="${TMP_DIR}/aedwards-src.tgz"
ENV_FILE="${TMP_DIR}/.env"

SSH_OPTS=(-i "${KEY_PATH}" -o StrictHostKeyChecking=accept-new)

# Fetch the host's current .env BEFORE building the deploy fragment so default
# substitution can be host-aware (and the preflight merge below reuses it).
HOST_ENV_SNAPSHOT="${TMP_DIR}/host.env"
# shellcheck disable=SC2029  # APP_DIR expanding client-side is intentional
ssh "${SSH_OPTS[@]}" "${SSH_USER}@${HOST}" "cat ${APP_DIR}/.env 2>/dev/null || true" > "${HOST_ENV_SNAPSHOT}"

host_env_has() {
  grep -q "^$1=" "${HOST_ENV_SNAPSHOT}"
}

# The host's ENABLE_MONITOR is authoritative in the merge, so the credential
# guard keys off the EFFECTIVE value: a monitor-off host (staging) must not
# demand mailbox credentials the merge would strip anyway.
monitor_enabled() {
  local effective="${ENABLE_MONITOR}"
  local host_value
  host_value="$(sed -n 's/^ENABLE_MONITOR=//p' "${HOST_ENV_SNAPSHOT}" | head -n1)"
  if [[ -n "${host_value}" ]]; then
    effective="${host_value}"
  fi
  [[ "${effective,,}" != "false" && "${effective,,}" != "0" && "${effective,,}" != "no" ]]
}

if monitor_enabled; then
  if [[ -z "${O365_EMAIL}" ]] && ! host_env_has O365_EMAIL; then
    echo "ABORT: O365_EMAIL is required for a monitor deploy and is not set locally, in any env file, or on ${HOST}." >&2
    echo "Pass --env-file <path> (e.g. the canonical checkout's .env), export O365_EMAIL, or set it in ${DOTENV_FILE}." >&2
    exit 1
  fi
  if [[ -z "${O365_CLIENT_ID}" ]] && ! host_env_has O365_CLIENT_ID; then
    if [[ "${HAVE_ENV_FILE}" == true ]]; then
      O365_CLIENT_ID="d3590ed6-52b3-4102-aeff-aad2292ab01c"
    else
      echo "ABORT: O365_CLIENT_ID is not set, no local env file was found, and ${HOST} has no existing value." >&2
      echo "Refusing to deploy the built-in default over a credential-less host. Pass --env-file <path> or export O365_CLIENT_ID." >&2
      exit 1
    fi
  fi
  if [[ -z "${O365_SCOPES}" ]] && ! host_env_has O365_SCOPES; then
    O365_SCOPES="https://graph.microsoft.com/.default"
  fi
fi
if [[ -z "${LLM_PROVIDER}" ]] && ! host_env_has LLM_PROVIDER; then
  LLM_PROVIDER="claude"
fi
if [[ -z "${QUOTE_ARTIFACT_DIR}" ]] && ! host_env_has QUOTE_ARTIFACT_DIR; then
  QUOTE_ARTIFACT_DIR="${APP_DIR}/instance/quote_versions"
fi

{
  echo "ENABLE_MONITOR=${ENABLE_MONITOR}"
  # Empty keys are omitted (not written as empty) so the host's values survive
  # the merge untouched.
  if monitor_enabled; then
    if [[ -n "${O365_EMAIL}" ]]; then
      echo "O365_EMAIL=${O365_EMAIL}"
    fi
    if [[ -n "${O365_PASSWORD}" ]]; then
      echo "O365_PASSWORD=${O365_PASSWORD}"
    fi
    if [[ -n "${O365_CLIENT_ID}" ]]; then
      echo "O365_CLIENT_ID=${O365_CLIENT_ID}"
    fi
    if [[ -n "${O365_SCOPES}" ]]; then
      echo "O365_SCOPES=${O365_SCOPES}"
    fi
  fi
  if [[ -n "${LLM_PROVIDER}" ]]; then
    echo "LLM_PROVIDER=${LLM_PROVIDER}"
  fi
  if [[ -n "${DATABASE_URL}" ]]; then
    echo "DATABASE_URL=${DATABASE_URL}"
  fi
  if [[ -n "${QUOTE_ARTIFACT_DIR}" ]]; then
    echo "QUOTE_ARTIFACT_DIR=${QUOTE_ARTIFACT_DIR}"
  fi
  if [[ -n "${SECRET_KEY}" ]]; then
    echo "SECRET_KEY=${SECRET_KEY}"
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

# Preflight: run the same merge the host will run against the snapshot fetched
# above. This fails BEFORE any remote change when the merge would (e.g. no
# DATABASE_URL anywhere), and gives --dry-run its diff.
MERGED_PREVIEW="${TMP_DIR}/merged-preview.env"
bash "${MERGE_ENV_FILE}" "${HOST_ENV_SNAPSHOT}" "${ENV_FILE}" "${MERGED_PREVIEW}"

# The preview diff must never print secret values (task 527): both sides are
# filtered through redact_env.sh with one per-run salt, so changed secrets
# still show as -/+ KEY=<redacted:...> lines while the values stay out of
# terminal scrollback and session logs.
REDACT_SALT="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
bash "${REDACT_ENV_FILE}" "${REDACT_SALT}" < "${HOST_ENV_SNAPSHOT}" > "${HOST_ENV_SNAPSHOT}.redacted"
bash "${REDACT_ENV_FILE}" "${REDACT_SALT}" < "${MERGED_PREVIEW}" > "${MERGED_PREVIEW}.redacted"

echo ""
echo "Resulting host .env change (current -> after deploy; secret values shown as <redacted:fingerprint>; SECRET_KEY may additionally be generated host-side if absent, and monitor-off hosts get mailbox credentials stripped host-side):"
if diff -u --label current --label after-deploy "${HOST_ENV_SNAPSHOT}.redacted" "${MERGED_PREVIEW}.redacted"; then
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

scp "${SSH_OPTS[@]}" "${SRC_TARBALL}" "${SSH_USER}@${HOST}:/tmp/aedwards-src.tgz"
scp "${SSH_OPTS[@]}" "${MERGE_ENV_FILE}" "${SSH_USER}@${HOST}:/tmp/aedwards-merge-env.sh"
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

sudo mkdir -p "${APP_DIR}/src" "${APP_DIR}/monitor_output" "${APP_DIR}/instance/quote_versions"
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

# The host .env is authoritative for environment-specific values: merge_env.sh
# preserves existing DATABASE_URL / SECRET_KEY / EMAIL_DELIVERY_ENABLED /
# ENABLE_MONITOR (delivery defaults to false when absent, never true), strips
# mail credentials on delivery-off hosts, and hard-fails rather than fall back
# to sqlite when no DATABASE_URL exists anywhere.
sudo bash /tmp/aedwards-merge-env.sh /tmp/aedwards-existing.env /tmp/aedwards.env /tmp/aedwards-merged.env

if grep -qiE '^ENABLE_MONITOR=(false|0|no)$' /tmp/aedwards-merged.env; then
  # Do not retain a live mailbox credential from an earlier deploy on a host
  # whose monitor is intentionally disabled.
  sudo sed -i -E '/^(O365_EMAIL|O365_PASSWORD|O365_CLIENT_SECRET|O365_TENANT_ID|GMAIL_EMAIL|GMAIL_CLIENT_ID|GMAIL_CLIENT_SECRET|GMAIL_REFRESH_TOKEN|GMAIL_SERVICE_ACCOUNT_FILE)=/d' /tmp/aedwards-merged.env
fi

# Generate SECRET_KEY on server if not already set
if ! grep -q '^SECRET_KEY=' /tmp/aedwards-merged.env || [[ -z "$(sed -n 's/^SECRET_KEY=//p' /tmp/aedwards-merged.env)" ]]; then
  sudo sed -i '/^SECRET_KEY=/d' /tmp/aedwards-merged.env
  printf 'SECRET_KEY=%s\n' "$(openssl rand -hex 32)" | sudo tee -a /tmp/aedwards-merged.env >/dev/null
fi

sudo install -m 600 -o "${APP_USER}" -g "${APP_USER}" /tmp/aedwards-merged.env "${APP_DIR}/.env"
sudo install -m 644 /tmp/${SERVICE_NAME}.service /etc/systemd/system/${SERVICE_NAME}.service
sudo systemctl daemon-reload
if grep -qiE '^ENABLE_MONITOR=(false|0|no)$' "${APP_DIR}/.env"; then
  # A staging host must never poll a live mailbox.  Stop and disable the unit
  # explicitly rather than merely omitting its credentials.
  sudo systemctl disable --now "${SERVICE_NAME}" || true
  echo "${SERVICE_NAME} is disabled by ENABLE_MONITOR in ${APP_DIR}/.env."
else
  sudo systemctl enable "${SERVICE_NAME}"
  sudo systemctl restart "${SERVICE_NAME}"
  sudo systemctl --no-pager status "${SERVICE_NAME}" || true
fi
if sudo systemctl is-enabled aedwards-web >/dev/null 2>&1; then
  sudo systemctl restart aedwards-web
  sudo systemctl --no-pager status aedwards-web || true
fi
REMOTE

echo "Deploy complete to ${HOST}."
echo "Tail logs with:"
echo "  ssh -i ${KEY_PATH} ${SSH_USER}@${HOST} 'sudo journalctl -u ${SERVICE_NAME} -f'"
