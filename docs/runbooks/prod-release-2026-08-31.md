# Prod release — Monday 2026-08-31 (full engine rollout)

Approved: Chip picked full rollout (D76, email 2026-08-28); Devin authorized the
package via gateway request 736. **This runbook deploys nothing by itself** —
D67's freeze holds until Devin executes it hands-on.

- Prod: `157.230.227.28` (`quotes.allanedwards.io`), Postgres 16 (unix socket, peer auth)
- Currently running: `d7f7acd` + hotfix `bd5c31f`
- Deploying: `main` @ `e433ca2` (34 commits)
- Run everything below from a clean checkout of `main` on the dev box, with the
  project `.env` present at the repo root (deploy scripts read it):
  `cd ~/src/2026/allanedwards && git fetch origin && git checkout main && git pull`

---

## 1. Pre-flight

### 1.1 What ships (`git log d7f7acd..e433ca2`), by area

- **Engine CP-2 (confidence + send tiers):** per-quote confidence score
  (`a7035b6`), Tier-1 assisted send / recommend-only queue (`c10b6d9`,
  `dde3e8a`), Tier-2 confidence-gated auto-send with hard guardrails +
  kill-switch (`e470364`, `b340c77`, `0769bc3`), reusable tier-2 drill
  (`17c3de8`).
- **Engine CP-3 (orders):** Order object + quote-to-order transition (`4765067`).
- **Engine CP-4 (fulfillment):** pick lists, shop pings, printable pick sheet,
  shop queue (`97a3fc7`).
- **Engine CP-5 (inventory):** StockItem + movement ledger + idempotent
  decrement-on-shipped (`f913721`), stock seeding UI + auto-reorder lifecycle
  (`3853619`).
- **Engine v2 refinements (Chip's corrected buy/resell model):** design
  reconciliation (`eb672b2`), PO/AFE capture + unsigned pick sheet + restock
  sheet → vendor PO with OPEN→SENT→RECEIVED lifecycle (`8164ba2`), min/max-0
  never-stock parts → order-triggered vendor PO with frozen customer details
  (`290faad`), real AE-MFG vendor examples (`752f316`).
- **Bug fixes:** quote-number generator suffix handling (`ed16478` — already on
  prod as hotfix `bd5c31f`), order-detail 500 on string prices (`ffc3b6a`),
  stale "Pricing TBD, contact sales" never renders on a priced line
  (`f9dde28` — Chip's gray-notes bug).
- **Deploy safety (T449/T452):** host `.env` authoritative via `merge_env.sh`,
  no sqlite fallback ever, delivery flag never written by deploy, `--dry-run` /
  `--env` guards, env banner (`527134c`, `42de782`, `7c4ea49`, `e433ca2`).
- **Staging/demo-only (inert on prod):** demo-dataset seeder + manifest
  (`b1ad8ce`, `51abe19`, `11e0f6a`, `e968ec6`), QuoteVersion backfill tool
  (`f5c5009`), demo pass + docs (`de1fd76`, `d42b119`, `c0db869`), PG reference
  docs (`b089494`, `0690aac`).

### 1.2 Migrations prod needs

Verified against live prod PG 2026-08-28: `alembic_version = 20260813_0001`,
exactly the expected pre-CP-3 baseline. Nine migrations apply, in this chain
order (`alembic upgrade head` inside `deploy_web.sh` applies all of them):

1. `20260820_0001_add_quote_confidence` — new table + index
2. `20260820_0002_add_send_holds_and_trust_ramp` — 2 new tables (trust_ramp_config starts empty; missing row = Tier 1)
3. `20260820_0003_add_auto_send_claim_and_dials` — new table + 3 nullable dial columns
4. `20260821_0001_add_orders` — 3 new tables
5. `20260821_0002_add_pick_lists` — 3 new tables
6. `20260821_0003_add_inventory_stock` — 3 new tables
7. `20260821_0004_add_reorders` — new table; `shop_ping.pick_list_id` → nullable + nullable `reorder_id`
8. `20260827_0001_vendor_po_refinements` — enum `ADD VALUE 'SENT'`, nullable columns, unique-index predicate widened
9. `20260827_0002_order_triggered_vendor_po` — nullable columns + new table, unique-index predicate widened

**All nine are additive** (verified by reading each `upgrade()`): new tables,
nullable columns, index rebuilds. The only `DROP INDEX` calls immediately
recreate the same partial-unique index with a wider predicate. No column or
table is dropped, no data rewritten. Old code never touches the new objects →
**code-only rollback is sufficient** (§6).

### 1.3 Prod `.env` expected state (verified live 2026-08-28)

```
DATABASE_URL=postgresql://aedwards@/aedwards?host=/var/run/postgresql   # postgres, NOT sqlite
EMAIL_DELIVERY_ENABLED=true    # prod REALLY SENDS — human-triggered sends only at Tier 1
ENABLE_MONITOR=true
EMAIL_PROVIDER=o365            # AEResponder@allanedwards.com
SEND_EMAIL_ALLOWLIST=          # empty = unrestricted (normal for prod)
APP_URL=https://quotes.allanedwards.io
```

`merge_env.sh` treats `DATABASE_URL`, `SECRET_KEY`, `EMAIL_DELIVERY_ENABLED`,
`ENABLE_MONITOR` as host-authoritative — the deploy cannot change any of them
(D75). **What the dry-run diff SHOULD show: key reordering only, zero value
changes.** It did — see §2.

---

## 2. Dry-run diffs (ACTUAL output, run against prod 2026-08-28, secrets redacted)

### 2.1 `bash deploy/deploy_web.sh --dry-run --env prod 157.230.227.28` — exit 0

```diff
--- host.env (current)
+++ merged-preview.env (after deploy)
@@ -7,14 +7,14 @@
 ENABLE_MONITOR=true
 SECRET_KEY=<redacted>
 DATABASE_URL=postgresql://aedwards@/aedwards?host=/var/run/postgresql
-QUOTE_ARTIFACT_DIR=/opt/aedwards/instance/quote_versions
-O365_CLIENT_ID=af931642-3ecc-43c7-bf34-813f168d411f
-O365_SCOPES=https://graph.microsoft.com/.default
-LLM_PROVIDER=claude
 EMAIL_DELIVERY_ENABLED=true
 O365_EMAIL=AEResponder@allanedwards.com
 GMAIL_EMAIL=devin@918.software
 GMAIL_SERVICE_ACCOUNT_FILE=/opt/aedwards/secrets/gmail-service-account.json
+QUOTE_ARTIFACT_DIR=/opt/aedwards/instance/quote_versions
+O365_CLIENT_ID=af931642-3ecc-43c7-bf34-813f168d411f
+O365_SCOPES=https://graph.microsoft.com/.default
+LLM_PROVIDER=claude
 EMAIL_PROVIDER=o365
 ENABLE_DB_WRITES=true
 ENABLE_OUTLOOK_DRAFTS=false
```

### 2.2 `bash deploy/deploy.sh --dry-run --env prod 157.230.227.28` — exit 0

```diff
@@ -7,16 +7,16 @@
 ENABLE_MONITOR=true
 SECRET_KEY=<redacted>
 DATABASE_URL=postgresql://aedwards@/aedwards?host=/var/run/postgresql
-QUOTE_ARTIFACT_DIR=/opt/aedwards/instance/quote_versions
-O365_CLIENT_ID=af931642-3ecc-43c7-bf34-813f168d411f
-O365_SCOPES=https://graph.microsoft.com/.default
-LLM_PROVIDER=claude
 EMAIL_DELIVERY_ENABLED=true
-O365_EMAIL=AEResponder@allanedwards.com
 GMAIL_EMAIL=devin@918.software
 GMAIL_SERVICE_ACCOUNT_FILE=/opt/aedwards/secrets/gmail-service-account.json
 EMAIL_PROVIDER=o365
 ENABLE_DB_WRITES=true
 ENABLE_OUTLOOK_DRAFTS=false
+O365_EMAIL=AEResponder@allanedwards.com
+O365_CLIENT_ID=af931642-3ecc-43c7-bf34-813f168d411f
+O365_SCOPES=https://graph.microsoft.com/.default
+LLM_PROVIDER=claude
 ANTHROPIC_API_KEY=<redacted>
 MINIMAX_API_KEY=<redacted>
```

**Verdict: nothing scary.** Both diffs are pure line reordering — every key
keeps its current value. `DATABASE_URL` stays postgres, delivery stays true,
monitor stays true, no key added or removed, no sqlite anywhere. Re-run both
dry-runs Monday before deploying; expect this same shape. **If any line shows a
changed VALUE (not just moved), stop and investigate before proceeding.**

---

## 3. Deploy steps (Monday, in order)

```bash
cd ~/src/2026/allanedwards && git checkout main && git pull
git log --oneline -1        # expect e433ca2 (or a reviewed later tip)
```

```bash
# 0. Re-run the dry-runs; expect the §2 reorder-only diffs
bash deploy/deploy_web.sh --dry-run --env prod 157.230.227.28
bash deploy/deploy.sh      --dry-run --env prod 157.230.227.28

# 1. Stop the monitor (no intake churn during migrate; also keeps the tier-2
#    drill window in §5.2 airtight)
ssh -i ~/.ssh/NoJobDevKey.pem root@157.230.227.28 'systemctl stop aedwards-monitor'

# 2. Fresh pre-deploy backup (nightly cron dump also exists in pg_backups)
ssh -i ~/.ssh/NoJobDevKey.pem root@157.230.227.28 \
  'sudo -u aedwards pg_dump -Fc -h /var/run/postgresql aedwards > /opt/aedwards/pg_backups/aedwards-pre-release-2026-08-31.dump && ls -la /opt/aedwards/pg_backups/aedwards-pre-release-2026-08-31.dump'

# 3. Deploy web — installs code, runs ALL nine migrations (alembic upgrade
#    head), restarts aedwards-web + nginx
bash deploy/deploy_web.sh --env prod 157.230.227.28

# 4. Tier-2 drill (§5.2) — run NOW, while the monitor is still stopped

# 5. Deploy monitor — installs service, restarts aedwards-monitor
bash deploy/deploy.sh --env prod 157.230.227.28
```

Notes:
- `deploy_web.sh` prints the same env diff before applying — read it once more.
- nginx config is skipped automatically (certbot SSL config already in place).
- Restart order ends up: web (step 3) → monitor (step 5). The monitor must come
  back LAST, after the drill.

---

## 4. Tier-2 state: auto-send stays DARK

- `trust_ramp_config` is created EMPTY by migration `20260820_0002`; the code
  treats a missing row as **Tier 1** (`confidence.py::active_trust_tier`), i.e.
  assisted/recommend-only. **No migration, seed, or deploy step sets tier 2.**
  Post-deploy, zero quotes auto-send.
- The drill in §5.2 proves the gates live on prod and restores Tier 1 itself.
- The dials (0.95 / $2500 / 20%) sign-off with Chip is a separate, later step —
  **NOT part of this release**. Flipping to Tier 2 requires Devin/Chip-signed
  thresholds plus a fresh drill run (I141).

---

## 5. Post-deploy verification

### 5.1 Env + services (lesson 21: healthz alone passes on the wrong database)

```bash
ssh -i ~/.ssh/NoJobDevKey.pem root@157.230.227.28 '
  grep -E "^(DATABASE_URL|EMAIL_DELIVERY_ENABLED|ENABLE_MONITOR)=" /opt/aedwards/.env
  systemctl is-active aedwards-web nginx
  curl -s -o /dev/null -w "healthz %{http_code}\n" http://127.0.0.1:8000/healthz
  sudo -u aedwards psql -h /var/run/postgresql aedwards -tAc "SELECT version_num FROM alembic_version;"'
```

Expect: `DATABASE_URL=postgresql://...` (**no sqlite string anywhere**),
`EMAIL_DELIVERY_ENABLED=true`, `ENABLE_MONITOR=true`, both services `active`,
healthz `200`, alembic `20260827_0002`.

### 5.2 Tier-2 drill (monitor still stopped, BEFORE step 5 of §3)

The drill was written for delivery-off staging: scenario A proves an eligible
tier-2 quote is blocked *inside* the send machinery by
`EMAIL_DELIVERY_ENABLED=false`. Prod's host flag is true, so run the drill with
the flag overridden **in the drill process only** (the flag is read live via
`os.getenv` per attempt; the web service and host `.env` are untouched):

```bash
ssh -i ~/.ssh/NoJobDevKey.pem root@157.230.227.28 \
  'sudo -u aedwards bash -c "set -a; source /opt/aedwards/.env; set +a; export EMAIL_DELIVERY_ENABLED=false; cd /opt/aedwards/src && /opt/aedwards/venv/bin/python tools/tier2_drill.py"'
```

Expect: 12 `OK` lines, `DRILL PASSED: 12 ok, 0 failed`, exit 0 — including
`cleanup: no drill quotes remain` and `cleanup: tier restored to 1`. The drill
briefly sets the global tier to 2, which is why the monitor stays stopped until
it finishes. If it FAILS: check whether cleanup ran (look for `DRILL-%` quotes
and `active_tier`), restore Tier 1 manually if needed
(`UPDATE trust_ramp_config SET active_tier=1 WHERE id=1;`), then investigate
before starting the monitor.

### 5.3 Test RFQs (post-deploy policy — run, verify, DELETE; no approval needed)

After the monitor is back (step 5 of §3):

```bash
# Sends [TEST]-prefixed historical RFQs from devin@918.software to AEResponder
python3 tools/send_test_rfqs.py --limit 2
```

Verify each in the UI: quote created, priced lines, confidence panel renders,
queue shows recommend-only (Tier 1). Then delete the test records. Lesson 23:
`audit_log` FK-references quote — child-first order or the whole delete rolls
back. Working order:

```
shop_ping, order_vendor_po_claim, (null reorder movement FKs), stock_movement,
reorder, pick_list_audit_log, pick_list, order_audit_log, customer_order,
acceptance_event, stock_item, audit_log, quote_version, quote
```

(Test RFQs that never became orders only need: `audit_log` → `quote_version`
→ `quote`, plus `auto_send_claim`/`quote_confidence`/`quote_line_item` rows.)

### 5.4 Feature spot-checks (in the UI)

- **Chip's gray-notes bug:** quote **126-107** (prod id 116) →
  `https://quotes.allanedwards.io/quotes/116/preview-pdf` — no stale
  "Pricing TBD, contact sales" note on priced lines.
- Pages load: `/orders/`, `/pick-lists/`, `/reorders/`, `/stock/`, quote queue.
- Monitor alive: `journalctl -u aedwards-monitor -n 20 --no-pager` shows fresh
  polling; test RFQs from §5.3 arrived through it.
- Delivery flags: prod `.env` has `EMAIL_DELIVERY_ENABLED=true`; staging
  (`134.122.29.15`) still `false`:
  `ssh -i ~/.ssh/NoJobDevKey.pem root@134.122.29.15 'grep EMAIL_DELIVERY /opt/aedwards/.env'`

---

## 6. Rollback

### 6.1 Code-only (the likely-sufficient path — all migrations are additive)

Old code ignores the new tables/columns entirely, so roll back code and leave
the schema in place. Lesson 21: the rollback branch MUST carry the current
deploy scripts, or the deploy itself reintroduces the sqlite/delivery footguns:

```bash
cd ~/src/2026/allanedwards
git checkout -B rollback-2026-08-31 d7f7acd
git cherry-pick bd5c31f                  # quote-number hotfix already on prod
git checkout e433ca2 -- deploy/          # current safe deploy scripts
git commit -m "Rollback branch: prod baseline + safe deploy scripts"
bash deploy/deploy_web.sh --dry-run --env prod 157.230.227.28   # diff should again be value-identical
bash deploy/deploy_web.sh --env prod 157.230.227.28
bash deploy/deploy.sh     --env prod 157.230.227.28
```

Then re-verify §5.1 (especially `DATABASE_URL` still postgres).

### 6.2 Database restore (only if a migration corrupted data — none should)

```bash
ssh -i ~/.ssh/NoJobDevKey.pem root@157.230.227.28 '
  systemctl stop aedwards-web aedwards-monitor
  sudo -u aedwards pg_restore --clean --if-exists -h /var/run/postgresql \
    -d aedwards /opt/aedwards/pg_backups/aedwards-pre-release-2026-08-31.dump'
```

The restore rewinds `alembic_version` to `20260813_0001` as part of the dump.
**Any RFQs/quotes that arrived after the backup are lost** — check
`monitor_output`/mailbox and re-drive if needed. Follow with the §6.1 code
rollback (restored schema + new code must not run together), then start
services and re-verify §5.1.

---

## 7. Staging note (after the prod release)

Staging (`134.122.29.15`) carries manual scp hotpatches of the task-442 files
on top of `c0db869`, with `.bak-t442` copies beside them (I155). Once prod is
out, redeploy staging cleanly with the now-safe script so
**staging == main == prod**:

```bash
bash deploy/deploy_web.sh --dry-run --env staging 134.122.29.15   # review; delivery must stay false
bash deploy/deploy_web.sh --env staging 134.122.29.15
```

`merge_env.sh` keeps staging's `EMAIL_DELIVERY_ENABLED=false` and
`ENABLE_MONITOR` (off) — host values are authoritative. Verify after:
`grep -E "EMAIL_DELIVERY|ENABLE_MONITOR" /opt/aedwards/.env` on staging.
Optionally clean the leftover hotpatch backups:
`find /opt/aedwards/venv -name "*.bak-t442" -delete`.
