# Enforcement audit: claimed vs. enforced (T515)

**Date:** 2026-10-19. **Baseline:** commit `9a19456` (main). **Scope:** the gap between what the UI/docs/canon claim and what `src/app` actually enforces, plus dead code/cruft. Research only — no code changed. Feeds T514 (full UX rework): the new UI must only show what is real.

Grounding: `docs/design/mockup/README.md` capability table, `docs/design/mockup/inventory.md` (routes/templates at d127c4c; line numbers below re-verified at 9a19456), `axon deep` for trust ramp / quote status / review lock / SOP, and direct code reading of `models.py`, `routes.py`, `quotes.py`, `orders.py`, `fulfillment.py`, `inventory.py`, `send_service.py`, `confidence.py`, `email_service.py`, `__init__.py`, `deploy/`.

**Summary verdict:** the newer subsystems (orders, pick lists, stock ledger, Tier-2 auto-send) are genuinely enforced — claim-in-transaction idempotency, frozen snapshots, strict state machines. The enforcement gaps are concentrated in the OLDEST layer: the quote status machine and the review/ownership model, which are largely cosmetic. Rank-ordered findings below.

---

## HIGH risk

### H1. The quote status machine has no transition rules — SENT is demotable in one click
- **CLAIMED:** Canon/docs describe a lifecycle (`NEW → IN_REVIEW/NEEDS_PRICING → READY → SENT`, with `REPLACED`/`ARCHIVED` terminal-ish). `_LIFECYCLE_LOCKED_STATUSES` (`routes.py:1580`) treats SENT/REPLACED/ARCHIVED as "completed lifecycle events" that must not be erased.
- **ENFORCED:** Partially. `quote_update_status` (`routes.py:1943`) accepts `in_review`, `ready`, `archived` from ANY current status. The only transition guard is "ready" from SENT/REPLACED (`routes.py:1955`). So:
  - **SENT → IN_REVIEW** succeeds, erasing the sent state. The status bar template (`quotes/_status_bar.html:43-54`) shows the *Mark In Review* and *Archive* buttons on sent quotes — this is one accidental click in the real UI, not a crafted request.
  - **SENT → ARCHIVED** succeeds the same way.
  - Compounding: after the demotion the handler calls `_attempt_auto_send` (`routes.py:1992`). IN_REVIEW is a sendable status (`confidence.py:579`), and a human-sent quote has no `AutoSendClaim`, so at Tier 2 a demoted already-sent quote is eligible to auto-send a duplicate email to the customer. Latent today (prod is Tier 1) but it is exactly the class of hole the trust ramp is supposed to make impossible.
- **RISK:** High. Loses the "this went to the customer" state the whole acceptance flow keys off (`acceptable_version` requires status SENT — demoting a sent quote silently makes it unacceptable); duplicate-send hazard at Tier 2.
- **RECOMMENDATION:** Add an explicit transition table for QuoteStatus like `ORDER_TRANSITIONS`/`PICK_LIST_SEQUENCE`; forbid leaving SENT/REPLACED except via Revise; hide the buttons for lifecycle-locked statuses; never call `_attempt_auto_send` on a quote that has any QuoteVersion.

### H2. `quote_send` has no status guard — REPLACED and ARCHIVED quotes can be emailed
- **CLAIMED:** Revision semantics: "a REPLACED (revised) quote is never acceptable — the newer revision is what the customer holds now" (`orders.py:80`); the status bar hides all actions for replaced quotes (`_status_bar.html:43`).
- **ENFORCED:** Only in the template. `POST /quotes/<id>/send` (`routes.py:3091`) checks delivery flag, structure, pricing, allowlist — but never `quote.status`. A direct POST (or any future UI regression) can send a REPLACED quote's stale content to a customer, flip it back to SENT (`routes.py:3185`), and create a QuoteVersion on it — making BOTH the old and new revision separately acceptable (each is "status SENT with a latest version"), i.e. two live orders for one deal. ARCHIVED quotes are likewise sendable.
- **RISK:** High (external side effect + breaks the one-live-revision invariant). Mitigated only by the single-operator reality and the hidden button.
- **RECOMMENDATION:** `quote_send` (and `quote_send_form`) should refuse REPLACED/ARCHIVED outright; decide explicitly whether re-send of a SENT quote stays allowed (it is today, and versioning handles it well — that part looks intentional).

### H3. Review claim/lock is pure display, and the claim endpoint trusts a form-supplied user id
- **CLAIMED:** "15-minute queue lock, reviewer assignment" (mockup README ownership row); queue shows "Reviewing: <name>" when `lock_active`. Canon (engram:document:29553) already concedes it is "team awareness, not an exclusive lock" — the docs are honest, but the UI word "lock" and `REVIEW_LOCK_TIMEOUT_MINUTES` imply enforcement.
- **ENFORCED:** No. Nothing anywhere checks `reviewed_by`/`review_started_at` before allowing edits, status changes, or sends. The 15-minute constant (`quotes.py:29`) is read in exactly one place — the queue display computation (`quotes.py:117-122`). Specifics:
  - `POST /quotes/<id>/claim` (`quotes.py:256`) takes `user_id` from the form with a default of 1 — comment says "placeholder until auth is wired up," but auth IS wired up (global `before_request`, `__init__.py:49`); the endpoint just never got updated to use `current_user`. Anyone can claim as anyone; claim also silently steals an existing claim.
  - `release` (`quotes.py:275`) releases anyone's claim, no ownership check.
  - First-open auto-assignment is a GET side effect (`routes.py:1628-1632`): viewing a quote mutates it. `reviewed_by` never expires — only the *display* of the lock does after 15 min — so a quote opened once stays "assigned" forever unless released/revised/deleted.
  - Neither claim nor release appears in any template — no UI calls them (see cruft C-section).
- **RISK:** High for the T514 rework specifically: the new UI must not render this as a lock or as durable ownership, because concurrent editors can and do overwrite each other (last-write-wins on every quote field). Low as a day-to-day hazard while Chip is the only operator.
- **RECOMMENDATION:** For T514, present "last opened by X" as awareness only, or build real ownership (the mockup's "durable responsibility" proposal). Fix claim to use `current_user`. Decide whether GET-mutates-state survives the rework (it also breaks the "viewing never changes anything" expectation).

---

## MEDIUM risk

### M1. Recipient allowlist and delivery gate are environment-dependent; code defaults fail open
- **CLAIMED:** "a recipient allowlist is enforced" and "$0/unpriced never auto-sends; every auto-send audited" (canon C45 lineage, design §4). Mockup README: "All guards still matter: … allowlist …".
- **ENFORCED:** The gate code exists on both send paths (`send_service.ensure_recipient_allowed`, `send_service.py:90`; signal `confidence.py:466`), but with `SEND_EMAIL_ALLOWLIST` unset the gate allows ALL recipients and the confidence signal scores PASS (`confidence.py:477`). Nothing in `deploy/` sets the variable, so in production the allowlist is almost certainly inert and Tier-2 auto-send would email whatever `contact_email` the parser extracted from the inbound RFQ. Similarly `email_delivery_enabled()` defaults to **true** when `EMAIL_DELIVERY_ENABLED` is missing (`email_service.py:16`); only `deploy/merge_env.sh:62` compensates by writing `false` on hosts that lack it. The $0 guard, by contrast, is genuinely enforced in code (`confidence.py:803` plus `check_send_gates`).
- **RISK:** Medium now (Tier 1, no auto-sends), high the day Tier 2 flips with the current env. Also a trap for any new environment that gets O365 creds but no explicit flags.
- **RECOMMENDATION:** Before any Tier-2 drill: make auto-send (not human send) refuse to run when no allowlist is configured, or at minimum surface "allowlist: NOT CONFIGURED (all recipients allowed)" on the trust-ramp admin panel instead of a passing signal. Flip the code default of `email_delivery_enabled()` to false.

### M2. SENT quotes remain fully editable, silently diverging from the sent record
- **CLAIMED:** Order/version docs: the sent QuoteVersion is the immutable record (true and well-enforced). But the UI presents one "quote" and the mockup README's acceptance semantics assume the sent snapshot is what the quote *is*.
- **ENFORCED:** The snapshot is immutable; the live quote is not. No edit endpoint (`/meta`, `/customer`, `/totals`, line-items add/update/delete/move — `routes.py:1897-2500`) checks lifecycle status; grep confirms the only SENT/REPLACED checks in routes.py are the five listed in H1/H2. The editor template never goes read-only for sent quotes. So after sending, line items/prices/ship-to can drift from what the customer received, while the queue still shows the (now-wrong) totals from the live rows. Pick lists and orders are safe (they read the version snapshot), but the pick *sheet* renders `quote.ship_to_json` live (`fulfillment.py:481`) — a post-send ship-to edit changes the printed shipping address for an existing order.
- **RISK:** Medium. Data shown ≠ data sent; the pick-sheet ship-to is the concrete physical-world leak of the mutable quote into the frozen flow.
- **RECOMMENDATION:** For T514: render sent quotes read-only with an explicit "Revise" escape hatch (the revise flow already exists and is correctly guarded). Freeze ship-to into the order/pick snapshot.

### M3. No CSRF protection on any state-changing endpoint
- **CLAIMED:** Nothing claims it; listed because the rework will touch every form.
- **ENFORCED:** No CSRF tokens anywhere (no flask-wtf / csrf import in `src/app`). Session-cookie auth + plain POSTs means a malicious page in the operator's browser can fire any action, including `/quotes/<id>/send` (external email) and trust-ramp tier changes.
- **RISK:** Medium (small user base, but send + Tier-2 flip are high-consequence endpoints).
- **RECOMMENDATION:** Add CSRF protection during the T514 template rework — cheapest moment to do it.

### M4. Any logged-in user can flip the trust ramp to Tier 2; "signed thresholds + drill" is procedure only
- **CLAIMED:** Canon: "Transitioning to Tier 2 requires signed thresholds and a drill run" (engram:document:18899); code comment at `routes.py:2560`: "flipping it in prod is a Devin/Chip decision."
- **ENFORCED:** The decision is enforced by nothing but the comment: `POST /admin/trust-ramp/tier` (`routes.py:2555`) is one form field behind `@login_required`; there is no role model (every user row is equivalent), no confirmation step, no requirement that dials be non-NULL before Tier 2 activates. The dials endpoint validates ranges properly, and `auto_send_evaluation` does fall back to conservative defaults (0.95 / $2,500) when dials are NULL, so a bare Tier-2 flip isn't unguarded — but it is a one-click live-fire switch.
- **RISK:** Medium. Consistent with the "no multi-seat permission model" reality the mockup documents, but the new Settings UI should not imply any approval workflow exists.
- **RECOMMENDATION:** T514: label the tier control as immediate-effect; consider a type-the-word confirmation for Tier 2 and a visible "dials are defaults, not signed values" notice when NULL.

### M5. Legacy `_current_user()` fallbacks: header-spoofable attribution and first-user defaulting
- **CLAIMED:** Audit rows record who did what.
- **ENFORCED:** When authenticated, yes. But `routes.py:407-415` falls back to an `X-User-Id` header / `user_id` query param, then to the first user in the DB; `orders.py:224`, `fulfillment.py:329`, `inventory.py:702` fall back to the first user. The global auth `before_request` makes these unreachable today (except `main.healthz`/auth endpoints, which don't call them), so this is dead-but-dangerous code: any future route exemption or blueprint added outside the guard silently mis-attributes actions to user 1. The claim endpoint (H3) shows how such placeholders rot.
- **RISK:** Medium-low (latent).
- **RECOMMENDATION:** Delete the fallbacks; let `_current_user()` return `current_user` or None.

### M6. `Customer.discount_pct` is collected but affects nothing
- **CLAIMED:** The customer create/edit form offers a "discount %" field (`customers.py:196,228`), and the customer detail displays it — implying customer-level discounts apply to that customer's quotes.
- **ENFORCED:** No. Nothing reads it for pricing: `price_adjustments` uses only `Quote.price_adjustment_pct`, which is deliberately "never automatically inherited from a customer" (`models.py:226`), `db_writer.py:249` hard-codes 0 at intake, and duplicate/revise reset or copy the per-quote value only. The field is write-and-display-only.
- **RISK:** Medium. An operator setting a customer discount reasonably expects their next quote to carry it; it silently won't. The D87/D90 duplicate-semantics decision ("customer-specific percentage adjustment resets") suggests per-customer percentage was intended to live somewhere — today it lives nowhere.
- **RECOMMENDATION:** Either wire it (prefill `price_adjustment_pct` from the customer on quote create/link, clearly labeled) or remove the field in T514. Don't keep collecting a dead value.

---

## LOW risk / working as designed (negative tests that PASSED)

These were checked adversarially and held up — the T514 UI can rely on them:

- **Acceptance-from-sent-snapshot:** enforced end to end. `acceptable_version` requires SENT + latest version (`orders.py:68`); the accept form posts the version id it displayed and a re-send invalidates the open form (`orders.py:272-281`); `AcceptanceEvent.quote_version_id` UNIQUE + IntegrityError recovery makes double-accept impossible (`orders.py:171`); orders read only `line_items_snapshot`.
- **Order machine:** ACCEPTED→ORDERED→FULFILLED strictly enforced (`orders.py:41`, `advance_order`); FULFILLED refused from the web route (`orders.py:444`) and only driven by SHIPPED.
- **Pick lists:** one per order ever (UNIQUE + no regenerate path), lines frozen at creation, strictly one-step transitions with replay-as-no-op (`fulfillment.py:265-274`), SHIPPED drives order FULFILLED in-transaction.
- **Stock decrement:** idempotent via `StockDecrementClaim` savepoint (`inventory.py:154`); unmatched lines go to triage, never guessed; ledger self-audits (`verify_stock_integrity`); reorder triggers double-gated (decrement claim + partial unique index); order-triggered vendor POs claimed per (pick_list, line).
- **Tier-2 auto-send:** tier read live on every attempt (kill-switch real, `send_service.py:306`); one `AutoSendClaim` per quote ever, committed with the version BEFORE the external send; blocked/failed claims surfaced in the queue and recommendation panel; price adjustment or custom message forces human send; $0 and over-ceiling refused; required signals must PASS (unknown ≠ pass). Holds (customer/product-type) are genuinely consulted by both the recommendation and the auto-send evaluation.
- **Recommendation rule:** single source (`quote_recommendation`) consumed by queue, editor, send form, and the auto-send gate — display and enforcement cannot drift. The send form's Tier-1 verdict is display-only *by design* and labeled as such in the template.
- **Mark Ready fingerprint:** approval is bound to a content hash; any customer-facing edit revokes it with an audit row (`routes.py:1559`); auto-send never honors it (`send_service.py:55-69` — override applies to human path only).
- **Immutability listeners:** source snapshots, source attachments, and reparse attempts reject ORM mutation (`models.py:449-540`). (Note: ORM-level only — raw SQL bypasses them, acceptable at this scale.)
- **Revise:** double-revise blocked (`routes.py:1721`); old quote correctly becomes REPLACED (modulo H2's direct-send bypass).
- **Delete:** refused when any order exists (`routes.py:1667`); soft-delete everywhere via `_get_active_quote_or_404`.
- **Delivery staging posture:** `merge_env.sh` treats `EMAIL_DELIVERY_ENABLED` as host-authoritative and defaults missing hosts to false (deploy layer compensates for M1's code default).

---

## Cruft / dead code

(See also M5 — the `_current_user` fallbacks are cruft with teeth.)

### C1. `/quotes/<id>/claim` and `/release` endpoints are unreferenced
No template, script, or JS calls them (grep across `src/app/templates`, `scripts/`, `tools/`); only tests do. The only reviewer assignment that actually happens is the first-open GET side effect. Either wire these into the T514 UI as real actions or delete them with the fake-lock model (H3).

### C2. Reserved enum seams — documented, dormant, correctly inert
`ShopPingChannel.EMAIL/SMS/SCREEN`, `AcceptanceSource.REPLY_PARSE/PO_RECEIVED`: no code path writes them; their docstrings say exactly that. Not a problem — but T514 must not render UI implying email/SMS shop notification or reply-parsing acceptance exists.

### C3. `POST /orders/<id>/status` is a dead route; `next_statuses` is computed but never rendered
No template references the order transition route (grep of `src/app/templates/orders/` finds nothing; the status panel only offers "Generate pick list", which is the real ACCEPTED→ORDERED action). `_detail_context` still computes `next_statuses` (`orders.py:405`) and passes it to templates that ignore it. The route itself correctly refuses FULFILLED (`orders.py:444`) and enforces the machine, so it is safe cruft — but it is exactly the kind of "capability" an inventory-based rework could mistakenly resurface. Delete or keep deliberately as an API.

### C4. Stale canon claims
`allanedwards:C39` ("backend stops at quote drafts, no order/fulfillment/inventory objects", as-of 2026-08-11) and the older reference docs it echoes are superseded by CP-3/4/5 — the mockup README already notes this. Canon should be amended so future agents don't design against the stale claim.

### C5. Dead-code sweep results (full fan-out over templates, src/, tools/, scripts/, deploy/)

**Dead routes (zero template/JS/tool references; tests only):**
- `POST /quotes/<id>/claim` and `/release` (`quotes.py:256,275`) — see C1/H3. Redundant with the first-open auto-assign (`routes.py:1628`) and the clears at `routes.py:1679/1763/1988`.
- `GET /customers/api/match` (`customers.py:94`) — intake uses `auto_match()` directly, never the HTTP endpoint. Referenced only by tests and a stale line in `docs/reference/current-offering.md:113`.
- `POST /orders/<id>/status` (see C3).

**Dead template:** `templates/dashboard.html` — never rendered (`main.dashboard` is `redirect("/quotes/")`, `routes.py:116`), never included/extended. All 58 other templates are reachable.

**Dead functions/columns:**
- `User.issue_magic_link_token()` + `User.magic_link_token` column (`models.py:122,130`) — zero callers anywhere including tests; the live magic-link flow uses the separate `AuthToken` model. Remove together.
- `pricing_catalog.default_pricing_rows()` (`src/allenedwards/pricing_catalog.py:67`) — orphaned; the seeding migration carries its own private copy. `docs/reference/current-offering.md:90` documents it as live — stale doc.
- Test-only shims: `fulfillment.pick_list_for_order` (`fulfillment.py:246`), `OutlookClient.list_inbox_messages`/`mark_as_read` (`outlook.py:191,275`).
- Relationships never accessed: `QuoteReparseAttempt.failed_intake`/`.created_by_user`, `PickList.pings`, `Reorder.pings`/`.reorder_movement`.

**Env vars with no reader:** `CLAUDE_API_KEY` in `.env` (provider reads `ANTHROPIC_API_KEY`) — stale. `QUOTE_EMAIL_CC`, `MONITOR_STATE_FILE`, `MONITOR_OUTPUT_DIR`, `PROCESSED_FOLDER_NAME` are read only by `tools/replay_inbound.py`, never by the deployed monitor (systemd passes CLI flags). Verified NOT dead: `ENABLE_MONITOR` (deploy-shell by design), `ENABLE_FAILURE_ACK`/`ACK_SKIP_DOMAINS`, `O365_SEND_AS_USER`, `CONFIDENCE_RECOMMEND_THRESHOLD`, `CLAUDE_JSON_MAX_TOKENS`.

**Write-only audit columns (stored, never displayed — keep, but T514 could surface them):** `PickList.picked_by/loaded_by/shipped_by` (the `*_at` timestamps are shown, the people are not — relevant to the mockup's "physical owner shown separately from app recorder"), `AcceptanceEvent.actor_user_id`, `Order.acceptance_event_id` (uniqueness-only), `StockMovement.resolved_by/resolution_movement_id`, `Reorder.trigger_movement_id/received_by/receipt_movement_id`, `QuoteVersion.email_cc`, `QuoteConfidence.computed_at`, `QuoteSourceSnapshot.parser_schema_version`.

**Reserved-seam enums, confirmed inert:** `ShopPingChannel.EMAIL/SMS/SCREEN` never written — and `ShopPing.channel` is never read anywhere, so the whole discriminator is currently write-only. `AcceptanceSource.REPLY_PARSE/PO_RECEIVED` never written; `AcceptanceEvent.source` written, never read back. `StockMovementType.REORDER` is written (`inventory.py:247,372`) but never filtered/read (unlike `UNMATCHED_SHIPMENT`, which has real readers).

**Highest-confidence removals:** `dashboard.html`, `quotes.claim`/`release`, `customers.api_match`, `User.issue_magic_link_token` + column, `default_pricing_rows`, the `CLAUDE_API_KEY` env line. (`pick_list_for_order` and the two OutlookClient methods need test edits alongside.)

---

## What this means for T514

1. Build the new UI on the enforced layer (versions, orders, pick lists, ledger) — it is trustworthy.
2. Do NOT render: an exclusive review lock, a quote status machine with ordered transitions, allowlist/approval ceremony around Tier 2, or shop-notification channels. None of those exist.
3. The quote status machine and send endpoint need transition guards (H1/H2) *before or with* the rework — the rework's "only show what is real" goal is otherwise undermined by endpoints that accept what the UI hides.
4. Sent quotes should become read-only in the new editor (M2), with Revise as the only mutation path.
