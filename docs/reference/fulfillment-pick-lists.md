# Fulfillment: pick lists + shop ping (CP-4, design Stage E)

Task 415, building on CP-3 orders (task 409). Staging only — prod frozen (D67).

## What exists

- **`PickList`** (`pick_list` table, `src/app/models.py`): one per order,
  `order_id` UNIQUE. Status machine `queued → picked → loaded → shipped`,
  strictly one step at a time — no skips, no backwards. Each transition stamps
  `<status>_at` / `<status>_by` and writes a `pick_list_audit_log` row.
  Replay safety: re-posting the current (or an earlier) status is a visible
  no-op, never an error — a one-tap shop UI gets mis-taps.
- **`PickList.lines_snapshot`**: the pick lines, materialized **at creation**
  from the frozen `QuoteVersion.line_items_snapshot` — never from the live
  Quote or catalog (§12.8: quoted-vs-picked drift is the primary fulfillment
  hazard). Pack-unit math is frozen in at the same moment: pieces always;
  bundle counts for standard sleeves (≤24" dia, 10 ft → bundles of 5); pallet
  counts for bags (pallet size is the one catalog read, done once at creation
  and stored, so later pricing-table edits cannot change an existing sheet).
  Non-material lines (shipping) are excluded.
- **`ShopPing`** (`shop_ping` table): channel-pluggable notification record.
  CP-4 writes exactly one `MANUAL_PRINT` row per pick list — the "ping" IS the
  printed sheet plus the shop queue. `EMAIL`/`SMS`/`SCREEN` are reserved enum
  members; wiring one means an outbound delivery gate first (I136).
- **Printable pick sheet** (`/pick-lists/<id>/sheet`): print-CSS page with
  customer/ship-to, lines in pack units, per-line checkboxes, driver signature
  line. This is the paper copy Chip said the truck drivers need.
- **Shop queue** (`/pick-lists/`, "Shop" in the nav): pick lists by status,
  touch-friendly one-tap progression.

## Order wiring

- **Generate pick list** (button on order detail, `POST
  /orders/<id>/pick-list`) replaced CP-3's bare "Mark Ordered": it creates the
  PickList + ShopPing **and** advances the Order `ACCEPTED → ORDERED` in one
  transaction. Idempotent per order (unique claim, hazard §12.1).
- The **`shipped`** pick-list transition advances the Order
  `ORDERED → FULFILLED` in the same transaction.

## No regeneration — deliberate v1 constraint

`order_id` UNIQUE means a pick list can never be regenerated or replaced.
The escape hatch for a botched order is at the **order level**: revise the
quote, send, and accept the revision — the new order gets its own pick list.
Pick lists are never edited; the printed sheet always re-prints identically.

## CP-5 hook

`emit_pick_list_shipped()` (`src/app/fulfillment.py`) runs inside the shipped
transaction, exactly once per pick list (the strictly-ordered machine cannot
re-enter `shipped`). It writes a `pick_list_audit_log` row with action
`shipped_event` whose `details` carry the frozen pick lines. Stage F's stock
decrement attaches here — it should consume that event, not re-read anything
mutable.
