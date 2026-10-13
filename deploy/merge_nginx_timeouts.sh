#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <certbot-managed-site> <output-file>" >&2
  exit 2
fi

SITE_FILE="$1"
OUTPUT_FILE="$2"
PROXY_MARKER='proxy_http_version 1.1;'

marker_count="$(grep -Fxc "        ${PROXY_MARKER}" "${SITE_FILE}" || true)"
if [[ "${marker_count}" -lt 1 ]]; then
  echo "ABORT: expected at least one '${PROXY_MARKER}' line in ${SITE_FILE}, found ${marker_count}" >&2
  exit 1
fi

# Certbot rewrites the live site with certificate paths and its HTTP->HTTPS
# redirect. Keep that file intact and update only the proxy timeouts needed by
# the long-running operator re-parse route. Production serves both its legacy
# and canonical TLS names from this file, so every proxy location gets the
# timeout pair. Existing values are removed first, making repeated deploys
# idempotent.
awk '
  /^[[:space:]]*proxy_(read|send)_timeout[[:space:]]/ { next }
  { print }
  /^[[:space:]]*proxy_http_version[[:space:]]+1\.1;[[:space:]]*$/ {
    print "        proxy_read_timeout 180s;"
    print "        proxy_send_timeout 180s;"
  }
' "${SITE_FILE}" > "${OUTPUT_FILE}"

