# Inventory: stock table + movement ledger + auto-reorder (CP-5a + CP-5b)

Task 417 built the Chip-independent half of design Stage F (stock table,
ledger, idempotent decrement); task 419 completed it (seeding UI, auto-
reorder, reorder lifecycle) after Chip's D68 answer (I143: <50 SKUs, counts
scattered across spreadsheets/QuickBooks/hand counts — seeding is manual
data entry, not an integration). Prod frozen (D67); staging only.

## What exists

- **`StockItem`** — one row per `ProductCatalog` product (`catalog_id`
  UNIQUE FK). Product identity is the catalog's surrogate `id`: rename-safe,
  survives part-number and description edits. `on_hand` starts at 0 and may
  go **negative** before the initial import (CP-5b) — shipments are recorded
  honestly against an unseeded count, badged in the UI, never hidden.
  `min_qty` / `max_qty` / `reorder_qty` are **NULL = unseeded**.
- **`StockMovement`** — append-only ledger. Every `on_hand` change is a row:
  signed `qty_delta`, `resulting_on_hand` after applying it, source
  (`pick_list_id` for shipment decrements, user + required `reason` for
  manual RECEIPT/ADJUSTMENT). `on_hand` is always derivable from the ledger;
  `verify_stock_integrity()` checks the running chain and final sum.
- **`StockDecrementClaim`** — `pick_list_id` UNIQUE. The claim-in-transaction
  guard (§12.1): the decrement consumer claims the pick list inside the
  SHIPPED transaction; a replayed/double-fired shipped event hits the
  constraint and applies **nothing**. Phantom decrement → phantom reorder is
  THE Stage-F hazard.

## The identity bridge (shipped line → stock item)

`app.inventory.match_catalog_row` is **deterministic** — it never guesses:

1. **Pass 1 — part number**: the frozen line's normalized (casefold,
   whitespace-collapsed) `part_number` equals exactly one active catalog
   row's part number. Ambiguity (>1 rows) goes straight to triage.
2. **Pass 2 — type + description**: normalized `product_type` +
   `description` equal exactly one active row. This matches on generated
   prose, so it is the weaker guess: every decrement records
   `details.matched_by`, and pass-2 (`type_description`) matches render a
   **low-confidence marker** in the movement history for human audit.

Zero or ambiguous matches produce an **`UNMATCHED_SHIPMENT`** ledger row
(`stock_item_id` NULL, `qty_delta` 0, frozen line in `details`) — never a
silent skip. The `/stock/unmatched` triage view resolves each one by
assigning a catalog product; resolution writes the real decrement movement
and stamps `resolved_at` / `resolution_movement_id` on the unmatched row.

The full FK refactor of `QuoteLineItem.product_type` was deliberately NOT
done here (free strings + synthesized legacy options span editor, pricing
and monitor) — this matcher is the minimal viable identity bridge.

## Decrement flow

`fulfillment.emit_pick_list_shipped` (the single CP-5 hook, inside the
SHIPPED transaction) calls `inventory.consume_shipped_event`. The payload is
the frozen `PickList.lines_snapshot` — the same lines the `shipped_event`
audit row carries. Matched catalog rows without a stock item get one
auto-created (on_hand 0, unseeded) and then decremented.

## Seeding (CP-5b, task 419)

Sized for <50 SKUs: `/stock/seed` is one inline-editable table over every
active catalog product (stock items are created on first save), save-per-row
via htmx. Each save writes counted `on_hand` + `min_qty`/`max_qty`/
`reorder_qty` and lands in the ledger as an `ADJUSTMENT`: the save that
first seeds an item gets reason `initial-seed`, every later edit gets
`threshold-change` (a threshold-only edit is a zero-delta row; details carry
old/new values either way). Validation: min and max come together, min <=
max, reorder_qty >= 1 or blank. A save that leaves the row at/below min
fires the reorder trigger immediately.

`/stock/seed/import` is the optional CSV path (`part_number, on_hand, min,
max, reorder_qty`; paste or upload): dry-run preview with per-row errors
(part-number matching uses the same normalization as the shipment matcher —
misses and ambiguity are errors, never guesses), apply is all-or-nothing.

## Auto-reorder (CP-5b, task 419)

`maybe_trigger_reorder` runs after EVERY ledger write (shipment decrement,
manual receipt/adjustment, triage resolution, seeding save). It fires when
`StockItem.needs_reorder` is True — NULL thresholds hard-return False
(CP-5a regression-tested), so unseeded items can never fire.

Firing creates a `Reorder` row with qty + on_hand/min/max FROZEN at trigger
time (the printable sheet can never drift), a zero-delta `REORDER` ledger
movement, and a `ShopPing` (MANUAL_PRINT — CP-4's real channel; `shop_ping`
now points at a pick list OR a reorder, exactly one, CHECK-enforced). NO
outbound anything.

**Qty rule**: `reorder_qty` when set, else `max(max_qty - on_hand, 1)` —
the fallback tops the item back up to max, floored at 1.

**Idempotency (the design's named hazard — phantom decrement -> phantom
reorder)**: a partial UNIQUE index on `reorder.stock_item_id WHERE
status='OPEN'` is the claim — at most one open reorder per item, ever; a
second trigger while one is open is a no-op. Composed with CP-5a's
decrement claim, a replayed shipped event decrements nothing AND cannot
open a second reorder (negative-tested end-to-end).

**Lifecycle**: open reorders appear on `/stock/reorders/` (linked as a
Reorders tab from the shop queue and badged on `/stock/`), each with a
printable restock sheet ("Make [qty] to restock [item]" + frozen trigger
context). Mark-received books a `RECEIPT` movement for the qty the shop
ACTUALLY made (may differ from ordered; 0 closes with no receipt — the
false-fire escape hatch) and closes the reorder. No partial tracking: a
short receipt closes the reorder and the still-at/below-min state re-fires
a fresh one in the same transaction (PM agreement, task 419). That same
re-evaluation is the re-arm: any later drop to/below min opens a new one.

## UI

`/stock/` list (on-hand, unseeded/negative/reorder-open badges, triage
banner, seeding + reorders links), `/stock/items/<id>` detail (movement
history with match-confidence markers, manual receipt/adjustment forms,
open-reorder banner), `/stock/unmatched` triage, `/stock/seed` +
`/stock/seed/import` seeding, `/stock/reorders/` lifecycle +
`/stock/reorders/<id>/sheet` printable. The shop queue (`/pick-lists/`)
carries a Reorders tab with the open count.
