# Inventory: stock table + movement ledger (CP-5a)

Task 417, design Stage F — the half that does NOT depend on Chip's answer to
DECISION-NEEDED #2 (D68: where do stock counts live today, what seeds
min/max). Prod frozen (D67); staging only.

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

## The CP-5b seam (gated on Chip's D68 answer)

Everything below plugs in WITHOUT schema changes:

- **Threshold seeding**: write `min_qty` / `max_qty` / `reorder_qty` on
  existing `StockItem` rows. Until then `StockItem.is_seeded` is False.
- **Initial on_hand import**: an `ADJUSTMENT` movement per item (reason:
  "initial count import"), keeping the ledger invariant intact.
- **Auto-reorder trigger**: `StockItem.needs_reorder` is THE seam — it
  hard-returns False while thresholds are NULL (tested), so nothing can fire
  pre-seeding. CP-5b watches it after each decrement and writes a `REORDER`
  movement (the enum member already exists — no PG enum migration needed)
  plus a shop ping through CP-4's channel.

## UI

`/stock/` list (on-hand, unseeded + negative badges, triage banner),
`/stock/items/<id>` detail (movement history with match-confidence markers,
manual receipt/adjustment forms, reason required), `/stock/unmatched`
triage with resolve-and-decrement.
