#!/usr/bin/env bash
# Merge a deploy-generated .env fragment into the host's existing .env.
#
# Usage: merge_env.sh <host-env> <deploy-env> <output>
#
# The host file is authoritative for environment-specific values: keys in
# HOST_AUTHORITATIVE_KEYS are never overwritten once the host defines them.
# EMAIL_DELIVERY_ENABLED is never taken from the deploy fragment at all —
# enabling delivery must be a manual edit of the host .env, never a deploy
# side effect — and defaults to false when the host does not define it.
# The merge fails (exit 2) and writes nothing when neither side provides
# DATABASE_URL: there is no sqlite fallback.
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 <host-env> <deploy-env> <output>" >&2
  exit 1
fi

HOST_ENV="$1"
DEPLOY_ENV="$2"
OUT="$3"

HOST_AUTHORITATIVE_KEYS=(DATABASE_URL SECRET_KEY EMAIL_DELIVERY_ENABLED)

TMP_OUT="$(mktemp)"
trap 'rm -f "${TMP_OUT}"' EXIT
if [[ -f "${HOST_ENV}" ]]; then
  cat "${HOST_ENV}" > "${TMP_OUT}"
fi

host_has() {
  grep -q "^$1=" "${TMP_OUT}"
}

is_authoritative() {
  local k
  for k in "${HOST_AUTHORITATIVE_KEYS[@]}"; do
    [[ "$1" == "${k}" ]] && return 0
  done
  return 1
}

while IFS= read -r line || [[ -n "${line}" ]]; do
  [[ -z "${line}" || "${line}" == \#* ]] && continue
  key="${line%%=*}"
  value="${line#*=}"
  if [[ "${key}" == "EMAIL_DELIVERY_ENABLED" ]]; then
    echo "merge_env: ignoring EMAIL_DELIVERY_ENABLED from the deploy fragment (the host value governs delivery)" >&2
    continue
  fi
  if is_authoritative "${key}" && host_has "${key}"; then
    continue
  fi
  sed -i "/^${key}=/d" "${TMP_OUT}"
  printf '%s=%s\n' "${key}" "${value}" >> "${TMP_OUT}"
done < "${DEPLOY_ENV}"

if ! host_has EMAIL_DELIVERY_ENABLED; then
  echo "EMAIL_DELIVERY_ENABLED=false" >> "${TMP_OUT}"
fi

db_url="$(sed -n 's/^DATABASE_URL=//p' "${TMP_OUT}" | head -n1)"
if [[ -z "${db_url}" ]]; then
  echo "merge_env: FATAL: the host .env has no DATABASE_URL and the deploy provided none." >&2
  echo "merge_env: refusing to write an .env without a database URL — there is no sqlite fallback." >&2
  exit 2
fi

delivery="$(sed -n 's/^EMAIL_DELIVERY_ENABLED=//p' "${TMP_OUT}" | head -n1)"
if [[ "${delivery,,}" == "false" || "${delivery,,}" == "0" || "${delivery,,}" == "no" ]]; then
  # Delivery-off hosts (staging) must not retain mail credentials left over
  # from a prior deploy of the same droplet.
  sed -i -E '/^(O365_EMAIL|O365_PASSWORD|O365_CLIENT_SECRET|O365_TENANT_ID|GMAIL_EMAIL|GMAIL_CLIENT_ID|GMAIL_CLIENT_SECRET|GMAIL_REFRESH_TOKEN|GMAIL_SERVICE_ACCOUNT_FILE)=/d' "${TMP_OUT}"
fi

cat "${TMP_OUT}" > "${OUT}"
