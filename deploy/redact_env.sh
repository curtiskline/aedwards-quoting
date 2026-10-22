#!/usr/bin/env bash
# Redact secret values in a .env stream for display (deploy diff previews).
#
# Usage: redact_env.sh [salt] < in.env > out.env
#
# KEY=value lines whose key is secret-bearing are rewritten as
# KEY=<redacted:xxxxxxxx>, where xxxxxxxx is a salted fingerprint of the
# value. Within one deploy run both sides of the preview diff are filtered
# with the same salt, so an unchanged secret produces no diff line and a
# changed secret produces -/+ lines with differing markers — the diff still
# says WHICH keys change without ever printing a value. The salt is random
# per run so markers are not comparable across runs (no offline guessing
# against the fingerprint). All other lines pass through untouched.
set -euo pipefail

SALT="${1:-}"

fingerprint() {
  printf '%s\n%s' "${SALT}" "$1" | sha256sum | cut -c1-8
}

while IFS= read -r line || [[ -n "${line}" ]]; do
  if [[ "${line}" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    # Case-insensitive, substring-liberal on secret-ish words so near-miss
    # names (ApiKey, PASSWD, X_TOKEN_2) don't slip through; _KEY only as a
    # suffix so ordinary KEY-containing words aren't swallowed.
    ukey="${key^^}"
    if [[ -n "${value}" ]] && \
       [[ "${ukey}" =~ API_?KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|CLIENT_ID|PRIVATE || "${ukey}" =~ _KEY$|^DATABASE_URL$ ]]; then
      printf '%s=<redacted:%s>\n' "${key}" "$(fingerprint "${value}")"
      continue
    fi
  fi
  printf '%s\n' "${line}"
done
