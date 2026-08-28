#!/usr/bin/env bash
# Fixture-only safety harness for deploy/merge_env.sh, deploy/deploy_web.sh
# and deploy/deploy.sh (monitor deploy).
#
# Never touches a real host: the deploy scripts run from a copied fake repo
# with ssh/scp replaced by stubs that serve a fixture "host" directory. Run
# from the repo root:  bash tests/test_deploy_env_safety.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MERGE="${REPO_ROOT}/deploy/merge_env.sh"

PASS=0
FAIL=0

ok() { echo "PASS: $1"; PASS=$((PASS + 1)); }
bad() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

check() { # check <description> <condition...>
  local desc="$1"; shift
  if "$@"; then ok "${desc}"; else bad "${desc}"; fi
}

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

# ---------------------------------------------------------------- merge_env.sh

merge_case() { # merge_case <name> <host-env-content> <deploy-env-content>
  local name="$1"
  local dir="${WORK}/${name}"
  mkdir -p "${dir}"
  printf '%s\n' "$2" > "${dir}/host.env"
  printf '%s\n' "$3" > "${dir}/deploy.env"
  bash "${MERGE}" "${dir}/host.env" "${dir}/deploy.env" "${dir}/out.env" 2>"${dir}/stderr"
  MERGE_RC=$?
  MERGE_OUT="${dir}/out.env"
}

# 1. Host DATABASE_URL wins even when the deploy fragment carries sqlite.
merge_case pg-preserved \
  "DATABASE_URL=postgresql://aedwards@/aedwards?host=/var/run/postgresql" \
  "DATABASE_URL=sqlite:////opt/aedwards/instance/allenedwards.db
LLM_PROVIDER=claude"
check "host DATABASE_URL preserved over deploy sqlite value" \
  grep -q '^DATABASE_URL=postgresql://aedwards@/aedwards?host=/var/run/postgresql$' "${MERGE_OUT}"
check "sqlite URL from deploy fragment absent" \
  bash -c "! grep -q sqlite '${MERGE_OUT}'"
check "non-authoritative key (LLM_PROVIDER) still merged" \
  grep -q '^LLM_PROVIDER=claude$' "${MERGE_OUT}"

# 2. Host EMAIL_DELIVERY_ENABLED=false survives a deploy fragment saying true.
merge_case delivery-false-preserved \
  "DATABASE_URL=postgresql://x
EMAIL_DELIVERY_ENABLED=false" \
  "EMAIL_DELIVERY_ENABLED=true"
check "host EMAIL_DELIVERY_ENABLED=false preserved against deploy true" \
  grep -q '^EMAIL_DELIVERY_ENABLED=false$' "${MERGE_OUT}"
check "no EMAIL_DELIVERY_ENABLED=true anywhere in output" \
  bash -c "! grep -q '^EMAIL_DELIVERY_ENABLED=true$' '${MERGE_OUT}'"

# 3. Missing EMAIL_DELIVERY_ENABLED defaults to false, never true.
merge_case delivery-absent-defaults-false \
  "DATABASE_URL=postgresql://x" \
  "LLM_PROVIDER=claude"
check "absent EMAIL_DELIVERY_ENABLED defaults to false" \
  grep -q '^EMAIL_DELIVERY_ENABLED=false$' "${MERGE_OUT}"

# 4. No DATABASE_URL anywhere: hard error, nothing written (no sqlite fallback).
merge_case no-db-url-hard-error \
  "LLM_PROVIDER=claude" \
  "APP_URL=https://example.invalid"
check "merge exits non-zero when DATABASE_URL is missing everywhere" \
  test "${MERGE_RC}" -ne 0
check "no output file written on missing DATABASE_URL" \
  test ! -f "${MERGE_OUT}"
check "missing-DATABASE_URL error names the refusal" \
  grep -q 'no sqlite fallback' "${WORK}/no-db-url-hard-error/stderr"

# 5. Host SECRET_KEY is preserved.
merge_case secret-key-preserved \
  "DATABASE_URL=postgresql://x
SECRET_KEY=hostsecret" \
  "SECRET_KEY=deploysecret"
check "host SECRET_KEY preserved" grep -q '^SECRET_KEY=hostsecret$' "${MERGE_OUT}"

# 6. Delivery-off hosts get mail credentials stripped.
merge_case cred-strip \
  "DATABASE_URL=postgresql://x
EMAIL_DELIVERY_ENABLED=false
GMAIL_REFRESH_TOKEN=leftover
O365_PASSWORD=leftover" \
  "LLM_PROVIDER=claude"
check "mail credentials stripped on delivery-off host" \
  bash -c "! grep -qE '^(GMAIL_REFRESH_TOKEN|O365_PASSWORD)=' '${MERGE_OUT}'"

# 6b. Host ENABLE_MONITOR=false survives a deploy fragment defaulting it true.
merge_case monitor-off-preserved \
  "DATABASE_URL=postgresql://x
ENABLE_MONITOR=false" \
  "ENABLE_MONITOR=true"
check "host ENABLE_MONITOR=false preserved against deploy true" \
  grep -q '^ENABLE_MONITOR=false$' "${MERGE_OUT}"
check "no ENABLE_MONITOR=true anywhere in output" \
  bash -c "! grep -q '^ENABLE_MONITOR=true$' '${MERGE_OUT}'"

# 6c. A fresh host (no ENABLE_MONITOR key) takes the deploy fragment default.
merge_case monitor-fresh-default \
  "DATABASE_URL=postgresql://x" \
  "ENABLE_MONITOR=true"
check "fresh host takes deploy ENABLE_MONITOR default" \
  grep -q '^ENABLE_MONITOR=true$' "${MERGE_OUT}"

# ------------------------------------------------------------- deploy_web.sh

# Fake repo so ROOT_DIR and the local .env are fully fixture-controlled.
FAKE_REPO="${WORK}/repo"
mkdir -p "${FAKE_REPO}/deploy"
cp "${REPO_ROOT}/deploy/deploy_web.sh" "${REPO_ROOT}/deploy/deploy.sh" \
   "${REPO_ROOT}/deploy/merge_env.sh" \
   "${REPO_ROOT}/deploy/aedwards-web.service" \
   "${REPO_ROOT}/deploy/aedwards-monitor.service" \
   "${REPO_ROOT}/deploy/nginx-aedwards-web.conf" "${FAKE_REPO}/deploy/"

FAKE_HOST_DIR="${WORK}/fakehost/opt/aedwards"
mkdir -p "${FAKE_HOST_DIR}"
cat > "${FAKE_HOST_DIR}/.env" <<'EOF'
DATABASE_URL=postgresql://aedwards@/aedwards?host=/var/run/postgresql
EMAIL_DELIVERY_ENABLED=false
ENABLE_MONITOR=false
SECRET_KEY=stagingsecret
EOF
cp "${FAKE_HOST_DIR}/.env" "${WORK}/host-env-before"

FAKE_KEY="${WORK}/fake_key"
touch "${FAKE_KEY}"

STUBS="${WORK}/stubs"
mkdir -p "${STUBS}"
CALL_LOG="${WORK}/calls.log"
: > "${CALL_LOG}"

cat > "${STUBS}/ssh" <<EOF
#!/usr/bin/env bash
echo "ssh \$*" >> "${CALL_LOG}"
for arg in "\$@"; do
  if [[ "\${arg}" == *"cat /opt/aedwards/.env"* ]]; then
    cat "${FAKE_HOST_DIR}/.env" 2>/dev/null || true
    exit 0
  fi
done
# Anything else (the remote deploy heredoc) must not run in these tests.
echo "ssh stub: unexpected remote command" >&2
cat > /dev/null
exit 97
EOF
cat > "${STUBS}/scp" <<EOF
#!/usr/bin/env bash
echo "scp \$*" >> "${CALL_LOG}"
exit 0
EOF
chmod +x "${STUBS}/ssh" "${STUBS}/scp"

run_deploy() { # run_deploy <expected-note> <args...>
  PATH="${STUBS}:${PATH}" KEY_PATH="${FAKE_KEY}" DATABASE_URL="" \
    EMAIL_DELIVERY_ENABLED="" SECRET_KEY="" \
    bash "${FAKE_REPO}/deploy/deploy_web.sh" "$@" \
    > "${WORK}/deploy-stdout" 2> "${WORK}/deploy-stderr"
  DEPLOY_RC=$?
}

# 7. Dry run: prints target + diff, applies nothing, no scp, host env untouched.
: > "${CALL_LOG}"
run_deploy --dry-run 134.122.29.15
check "dry run exits 0" test "${DEPLOY_RC}" -eq 0
check "dry run prints staging banner" grep -q 'DEPLOY TARGET: 134.122.29.15  \[STAGING\]' "${WORK}/deploy-stdout"
check "dry run announces nothing applied" grep -q 'DRY RUN: nothing was copied or applied' "${WORK}/deploy-stdout"
check "dry run never calls scp" bash -c "! grep -q '^scp ' '${CALL_LOG}'"
check "dry run's only ssh call is the .env read" \
  bash -c "[[ \$(grep -c '^ssh ' '${CALL_LOG}') -eq 1 ]] && grep -q 'cat /opt/aedwards/.env' '${CALL_LOG}'"
check "fixture host .env unchanged by dry run" \
  cmp -s "${FAKE_HOST_DIR}/.env" "${WORK}/host-env-before"
check "dry-run diff keeps host DATABASE_URL (no sqlite regress)" \
  bash -c "! grep -q -- '-DATABASE_URL=postgresql' '${WORK}/deploy-stdout'"
check "dry-run diff never introduces EMAIL_DELIVERY_ENABLED=true" \
  bash -c "! grep -q '+EMAIL_DELIVERY_ENABLED=true' '${WORK}/deploy-stdout'"

# 8. Host with no DATABASE_URL and none provided: preflight hard-fails
#    before anything is copied (the fail-loudly guard itself).
mv "${FAKE_HOST_DIR}/.env" "${FAKE_HOST_DIR}/.env.saved"
printf 'EMAIL_DELIVERY_ENABLED=false\n' > "${FAKE_HOST_DIR}/.env"
: > "${CALL_LOG}"
run_deploy --dry-run 134.122.29.15
check "missing DATABASE_URL everywhere fails the deploy preflight" \
  test "${DEPLOY_RC}" -ne 0
check "preflight failure names the sqlite refusal" \
  grep -q 'no sqlite fallback' "${WORK}/deploy-stderr"
check "preflight failure copied nothing" \
  bash -c "! grep -q '^scp ' '${CALL_LOG}'"
mv "${FAKE_HOST_DIR}/.env.saved" "${FAKE_HOST_DIR}/.env"

# 9. --env cross-check: claiming prod against the known staging IP aborts
#    before any ssh/scp.
: > "${CALL_LOG}"
run_deploy --env prod 134.122.29.15
check "--env prod against staging IP aborts" test "${DEPLOY_RC}" -ne 0
check "abort message names the mismatch" grep -q 'ABORT: --env prod' "${WORK}/deploy-stderr"
check "mismatch abort makes no ssh/scp calls" test ! -s "${CALL_LOG}"

# ----------------------------------------------------- deploy.sh (monitor)

run_monitor() { # run_monitor <args...>
  # ENABLE_MONITOR is deliberately left empty so deploy.sh applies its own
  # default (true): the host's ENABLE_MONITOR=false must survive exactly that.
  PATH="${STUBS}:${PATH}" KEY_PATH="${FAKE_KEY}" DATABASE_URL="" \
    EMAIL_DELIVERY_ENABLED="" SECRET_KEY="" ENABLE_MONITOR="" \
    O365_EMAIL="monitor-stub@example.invalid" \
    bash "${FAKE_REPO}/deploy/deploy.sh" "$@" \
    > "${WORK}/monitor-stdout" 2> "${WORK}/monitor-stderr"
  MONITOR_RC=$?
}

# 10. Dry run against a monitor-off staging host: banner, nothing applied, and
#     the deploy's ENABLE_MONITOR=true default never reaches the host.
: > "${CALL_LOG}"
run_monitor --dry-run 134.122.29.15
check "monitor dry run exits 0" test "${MONITOR_RC}" -eq 0
check "monitor dry run prints staging banner" \
  grep -q 'DEPLOY TARGET: 134.122.29.15  \[STAGING\]' "${WORK}/monitor-stdout"
check "monitor dry run announces nothing applied" \
  grep -q 'DRY RUN: nothing was copied or applied' "${WORK}/monitor-stdout"
check "monitor dry run never calls scp" bash -c "! grep -q '^scp ' '${CALL_LOG}'"
check "monitor dry run's only ssh call is the .env read" \
  bash -c "[[ \$(grep -c '^ssh ' '${CALL_LOG}') -eq 1 ]] && grep -q 'cat /opt/aedwards/.env' '${CALL_LOG}'"
check "fixture host .env unchanged by monitor dry run" \
  cmp -s "${FAKE_HOST_DIR}/.env" "${WORK}/host-env-before"
check "monitor-off host stays off (no +ENABLE_MONITOR=true in diff)" \
  bash -c "! grep -q '+ENABLE_MONITOR=true' '${WORK}/monitor-stdout'"
check "host ENABLE_MONITOR=false not removed by diff" \
  bash -c "! grep -q -- '-ENABLE_MONITOR=false' '${WORK}/monitor-stdout'"
check "monitor diff keeps host DATABASE_URL (no sqlite regress)" \
  bash -c "! grep -q -- '-DATABASE_URL=postgresql' '${WORK}/monitor-stdout'"
check "monitor diff never introduces sqlite" \
  bash -c "! grep -q sqlite '${WORK}/monitor-stdout'"
check "monitor diff never introduces EMAIL_DELIVERY_ENABLED=true" \
  bash -c "! grep -q '+EMAIL_DELIVERY_ENABLED=true' '${WORK}/monitor-stdout'"
check "mailbox credential not added to delivery-off host" \
  bash -c "! grep -q '+O365_EMAIL=' '${WORK}/monitor-stdout'"

# 11. Fresh host (no ENABLE_MONITOR key): the deploy fragment default applies.
mv "${FAKE_HOST_DIR}/.env" "${FAKE_HOST_DIR}/.env.saved"
printf 'DATABASE_URL=postgresql://fresh\nEMAIL_DELIVERY_ENABLED=true\n' > "${FAKE_HOST_DIR}/.env"
: > "${CALL_LOG}"
run_monitor --dry-run 134.122.29.15
check "fresh-host monitor dry run exits 0" test "${MONITOR_RC}" -eq 0
check "fresh host gets ENABLE_MONITOR=true default" \
  grep -q '+ENABLE_MONITOR=true' "${WORK}/monitor-stdout"
check "fresh host keeps its DATABASE_URL" \
  bash -c "! grep -q -- '-DATABASE_URL=postgresql://fresh' '${WORK}/monitor-stdout'"
mv "${FAKE_HOST_DIR}/.env.saved" "${FAKE_HOST_DIR}/.env"

# 12. Host with no DATABASE_URL and none provided: monitor preflight
#     hard-fails before anything is copied — no sqlite fallback.
mv "${FAKE_HOST_DIR}/.env" "${FAKE_HOST_DIR}/.env.saved"
printf 'EMAIL_DELIVERY_ENABLED=false\nENABLE_MONITOR=false\n' > "${FAKE_HOST_DIR}/.env"
: > "${CALL_LOG}"
run_monitor --dry-run 134.122.29.15
check "missing DATABASE_URL everywhere fails the monitor preflight" \
  test "${MONITOR_RC}" -ne 0
check "monitor preflight failure names the sqlite refusal" \
  grep -q 'no sqlite fallback' "${WORK}/monitor-stderr"
check "monitor preflight failure copied nothing" \
  bash -c "! grep -q '^scp ' '${CALL_LOG}'"
mv "${FAKE_HOST_DIR}/.env.saved" "${FAKE_HOST_DIR}/.env"

# 13. --env cross-check for the monitor deploy.
: > "${CALL_LOG}"
run_monitor --env prod 134.122.29.15
check "monitor --env prod against staging IP aborts" test "${MONITOR_RC}" -ne 0
check "monitor abort message names the mismatch" \
  grep -q 'ABORT: --env prod' "${WORK}/monitor-stderr"
check "monitor mismatch abort makes no ssh/scp calls" test ! -s "${CALL_LOG}"

echo ""
echo "${PASS} passed, ${FAIL} failed"
[[ "${FAIL}" -eq 0 ]]
