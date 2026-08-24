# Staging demo pass — full engine flow, verified end-to-end (task 422)

**Date of pass:** 2026-08-24 · **Environment:** https://staging.quotes.vectorforgeinteractive.com
(staging only; prod frozen under D67) · **Login:** `dev@local.test` / `Demo408-staging`
· **Code:** main tip `3853619` + hotpatched `ffc3b6a` (see Findings)

This document is two things: (1) the verified result of a full intake → auto-quote →
order → fulfillment → inventory → reorder pass on the staging demo dataset, and
(2) the ordered script Devin drives when showing Chip the system.

---

## Part 1 — Pass results (what was verified, with evidence)

| Stage | Checkpoint | Result | Evidence seen |
|---|---|---|---|
| Intake → auto-quote queue | CP-1/CP-2 | **PASS** | Queue shows 117 quotes: 31 New / 7 Needs Pricing / 79 Sent, 16 Recommended. Confidence pills with per-signal scores on every row. Header: "Trust ramp: Tier 1 (assisted — every send stays human-clicked)". |
| Confidence detail, green path | CP-2a | **PASS** | Quote 126-032-02 (Duke Energy): ✓ Recommended, 100% confidence, all six signals pass with stated bases ("matches human-confirmed stored address #43"; "$69.72 vs median $69.72, 0.0% off, 3 comparable sent quotes"). |
| Confidence detail, held path | CP-2b | **PASS** (verified 2026-08-21, unchanged) | Quote 126-034 (Red Flame): held with "why not" reasons (new customer + out-of-tolerance $130 vs $388 median). |
| Tier-2 auto-send | CP-2c | **DARK by design** | Trust ramp pinned at Tier 1; auto_send_claim table empty. Nothing sends without a human click. |
| Quote → Order | CP-3 | **PASS** | Quote H126-032-01-1 v1 accepted (PO-DEMO-422) → Order #1. Order page shows acceptance provenance (who/when/how, quote version bound), frozen line snapshot, $2,028.40 total. Rows: acceptance_event 1, customer_order 1. |
| Fulfillment | CP-4 | **PASS** | Pick list #1 generated from Order #1; shop ping recorded (MANUAL_PRINT); printable pick sheet renders (Duke Energy, ship-to 619 Rhyne Rd Charlotte NC, 40 pcs, signature lines); shop queue walked Queued → Picked → Loaded → Shipped. |
| Ship → stock decrement | CP-5a | **PASS** | Mark Shipped wrote ledger row: `SHIPMENT_DECREMENT −40 → on hand 5` bound to pick list #1, plus stock_decrement_claim row (idempotency — a re-ship cannot decrement twice). Order auto-advanced ORDERED → FULFILLED in the same transaction. |
| Auto-reorder trigger | CP-5b | **PASS** (fired twice, two distinct paths) | On hand 5 ≤ min 10 → Reorder #1 opened automatically, qty 40 (top-up to max 45), with `REORDER` ledger row "auto-reorder: on hand 5 at/below min 10". Second trigger fired from a manual adjustment (see staged demo state). |
| Reorder lifecycle | CP-5b | **PASS** | Restock sheet renders ("Make 40 — S-12.34-14-50-1", trigger table, signature lines). Mark received 40 → Reorder #1 RECEIVED, `RECEIPT +40 → on hand 45` ledger row, banner "Received 40 — on hand is now 45", row moved to "Recently received". |

The 38 demo quotes and 79 SENT history entries were left untouched
(post-pass counts verified: 31 NEW / 7 NEEDS_PRICING / 79 SENT).

### Records created on staging by this pass (all labeled, all kept)

- QuoteVersion v1 on SENT quote **H126-032-01-1** (pdf_path `backfill:task-422:H126-032-01-1`, artifact_status `missing` — the model's honest marker for backdated history with no archived PDF).
- Product catalog row #2 **S-12.34-14-50-1** "…[task-422 demo]" + stock item #1 (seeded 45 on hand, min 10 / max 45).
- Acceptance event #1 + **Order #1** (PO `PO-DEMO-422`, note "task-422 demo pass record").
- Pick list #1 (SHIPPED) + shop ping #1.
- Stock movements #1–#6 (seed, shipment decrement, reorder, receipt, demo-setup adjustment, reorder).
- Reorder #1 (RECEIVED) and **Reorder #2 (OPEN — deliberately staged for the live demo, see step 9)**.

---

## Part 2 — Demo script for the Chip walkthrough

Preconditions: log in at https://staging.quotes.vectorforgeinteractive.com as
`dev@local.test`. Reorder #2 must be OPEN on the Reorders page (it is, as of this
pass — if someone received it, re-stage per the note at the end).

One caveat to know before you click: **opening any New quote marks it
"In Review by you"** (the team-awareness lock) and moves it out of the New tab.
That's expected behavior, not a bug — but it shifts the tab counts as you demo.

**Inventory numbers are placeholders (task 428).** The Stock pages now show 12
seeded SKUs, but every count and min/max except item #1's demo values is INVENTED
(each is labeled "PLACEHOLDER … confirm w/ Chip" in the UI). Part of the demo is
telling Chip exactly that — "we made these up; here's where you change each one"
— and showing him the edit boxes on `/stock/seed`. The full table of invented
values, the rationale for each, and the exact edit path per SKU is in
[placeholder-inventory-values.md](placeholder-inventory-values.md).

### Step 1 — The queue: every quote request, already priced

- **Go to:** `/quotes/` (Quotes in the top nav)
- **You'll see:** the Quote Queue Dashboard — 117 quotes, tabs for New (31) / Needs Pricing (7) / Sent (79), green "Recommended" badges, and a row of colored dots with a confidence percent on every quote.
- **Say:** "Every one of these came in as a real email. The system read each email, priced it from your price history, and scored how confident it is. Green badge means it's ready to send as-is."

### Step 2 — A quote the engine trusts

- **Go to:** click quote **126-032-02** (Duke Energy / Piedmont Natural Gas), or `/quotes/155`
- **You'll see:** ✓ Recommended, Confidence 100%. Six named checks, each with its reason — including "matches human-confirmed stored address #43" and a price-tolerance line: "$69.72 vs median $69.72 (0.0% off, 3 comparable sent quotes)".
- **Say:** "It's not a black box. It shows exactly why it trusts this one: the customer is known, the address is confirmed, and the price matches what you actually charged the last three times."

### Step 3 — A quote the engine holds back

- **Go to:** back to Quotes, click **126-034** (Red Flame), or `/quotes/158`
- **You'll see:** "Not recommended" with the specific reasons: new customer, and price out of tolerance ($130 vs the $388 median).
- **Say:** "When something looks off, it holds the quote and tells you why. A person decides. Nothing goes out on a guess."

### Step 4 — Nothing sends itself

- **Go to:** Admin → Trust Ramp
- **You'll see:** tier selector at Tier 1, the auto-send dials (95% confidence / $2,500 / ±20%) present but inactive.
- **Say:** "Today, every send is a human click. These dials exist for later — if you ever want small, safe quotes to go out automatically, we turn that on together and set the limits."

### Step 5 — What it refused to quote

- **Go to:** Rejected (top nav)
- **You'll see:** 7 rejected emails — marketing blasts, a webinar invite, an invoice thread — each with the classifier's stated reason.
- **Say:** "It also filters the junk, and it keeps a record of everything it turned away so you can check its judgment."

### Step 6 — From quote to order

- **Go to:** Orders (top nav), open **Order for Quote H126-032-01-1**, or `/orders/1`
- **You'll see:** Status FULFILLED with the full trail: accepted 2026-08-24 (PO-DEMO-422), ordered, pick list SHIPPED. "Acceptance provenance" box shows who accepted, when, and exactly which version of the quote the customer said yes to. Line items frozen at $2,028.40.
- **Say:** "When Duke says 'go', one click turns the quote into an order. The order is locked to the exact version they agreed to — if the quote changes later, this order doesn't."

### Step 7 — The shop gets a pick sheet

- **On the order page click "Print pick sheet"**, or `/pick-lists/1/sheet`
- **You'll see:** a printable PICK SHEET — customer, PO, ship-to (619 Rhyne Rd, Charlotte), 40 pieces of S-12.34-14-50-1, checkboxes and signature lines.
- **Say:** "The shop doesn't need the computer. This prints and goes on the wall, same as today — except nobody had to retype anything."

### Step 8 — Shipping updates the count automatically

- **Go to:** Shop (top nav), or `/pick-lists/?status=all`, then **Stock**, click the item, or `/stock/items/1`. (The other 11 stock rows you'll see are the task-428 placeholder set — invented numbers, labeled as such; see [placeholder-inventory-values.md](placeholder-inventory-values.md).)
- **You'll see:** the shipped pick list in the queue; on the stock page, the item's Movement history ledger: seeded 45 → shipped −40 → 5 on hand → reorder opened → received +40 → 45 → adjusted −40 → 5.
- **Say:** "When the truck left, the system took those 40 pieces out of stock by itself — and every change to the count is written down with who, when, and why. No mystery numbers."

### Step 9 — LIVE: stock ran low, the system already asked for more

- **Go to:** `/stock/reorders/` (from Stock → "1 open reorder", or Shop → Reorders)
- **You'll see:** an open reorder: **"Make 40 — S-12.34-14-50-1"**, on hand 5, min 10 / max 45. A "Restock sheet" button prints the shop's make-list.
- **Do it live:** type **40** in "qty made", click **Mark received**.
- **You'll see:** green banner "Received 40 — S-12.34-14-50-1 on hand is now 45", and the reorder moves to "Recently received".
- **Say:** "Stock dropped below the minimum you set, so it opened a work order for the shop on its own. The shop makes the pieces, someone types in what was actually made, and the count is right again. That's the whole loop: email in, quote out, order, ship, restock — with your numbers checked at every step."

**Re-staging step 9** (only if the open reorder was consumed): open `/stock/items/1`,
record an adjustment of **−40** with a reason like "demo setup", and a fresh reorder
opens automatically.

---

## Part 3 — Findings from the pass (for Devin, not for the demo)

1. **Staging's engine tables were empty before this pass.** The task-408 seeder builds the quote layer only; quote_version, customer_order, pick_list, stock_*, and reorder tables had zero rows until this pass exercised them. The demo dataset alone does not demo CP-3..CP-5b.
2. **There is no way to create a QuoteVersion without emailing.** Versions are minted only inside the real send flow, which staging (correctly) blocks with `EMAIL_DELIVERY_ENABLED=false`. That's why `tools/backfill_sent_quote_version.py` exists (commit `f5c5009`): it mints v1 on a seeded SENT quote with the honest `artifact_status="missing"` marker, staging-gated, no prod path. **Capability gap to consider post-freeze:** a "record as sent / import historical quote" path in the product itself.
3. **Staging is running a manual hotpatch.** The order-detail 500 fix (`ffc3b6a`, string prices vs `%.2f`) was applied by copying files into site-packages and restarting `aedwards-web` — staging has diverged from what `deploy_web.sh` would produce. Fine for the demo; reconcile at the next real release.
4. **`deploy_web.sh` is a staging footgun (do not run it there).** It defaults `EMAIL_DELIVERY_ENABLED=true` and reverts the host `DATABASE_URL` to sqlite (lessons 21/22). Until it grows a staging mode, all staging fixes go commit → scp → restart, leaving `.env` untouched.
5. **Opening a New quote auto-claims it** (NEW → In Review by you), and releasing the lock does not return it to NEW. Harmless in the demo (caveat noted in Part 2), but the tab counts drift as quotes are opened; the two quotes touched during this pass were restored.
6. **Shop-queue tab counts don't refresh on Mark Shipped** — the row updates to SHIPPED immediately but the Loaded/Shipped tab counters are stale until page reload. Cosmetic; reload fixes it.
