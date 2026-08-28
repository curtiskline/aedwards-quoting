# Inventory values on staging: what's real vs placeholder (tasks 428 → 435)

> **UPDATE (task 435, 2026-08-28):** Chip sent his real sleeve min/max
> (email 2026-08-24, attachment `Book1 (002).xlsx`). Those numbers are now
> loaded on staging for all 12 sleeve SKUs below — **min and max are REAL**.
> Chip's sheet gives min/max in **bundles**; per his note "5 pcs per bundle"
> (sheet confirms ≤30 in OD = 5 pcs/bundle, which covers every SKU he listed),
> the loaded values are **bundles × 5 = pieces**.
>
> **On-hand is still a placeholder.** Chip supplied min/max only — no current
> count. Every real-threshold SKU was seeded with **on-hand = max** so nothing
> sits at/below min (no spurious auto-reorders) until a real count replaces it
> (task 433). Each of those catalog descriptions carries the note
> `[on-hand not yet counted]`; the old
> `[PLACEHOLDER stock numbers — invented, confirm w/ Chip]` label was removed
> from the five SKUs that had it, since their thresholds are no longer invented.

**Where Chip changes min/max (and every other number):** Stock (top nav) →
"Seed counts & thresholds" link → **`/stock/seed`**. Each product is one row with
four inline boxes — **count** (on hand), **min**, **max**, **reorder** — and a
**Save** button per row. That one screen is the single place thresholds are edited;
the item detail page (`/stock/items/<id>`) shows Min/Max read-only and only takes
receipts/adjustments.

Environment: https://staging.quotes.vectorforgeinteractive.com · login
`dev@local.test` / `Demo408-staging`. All loads went through the real UI
endpoints (Admin → Catalog "Add"/row-update forms, then per-row Save on
`/stock/seed`); each save wrote an ADJUSTMENT to the movement ledger
(`initial-seed` on first save, `threshold-change` after), verified on the
rendered pages afterward.

## REAL sleeve min/max (Chip's numbers, converted bundles → pieces ×5)

Loaded 2026-08-28 (task 435). Min = reorder point (auto-reorder fires when on
hand drops to/at/below it); Max = top-up target; Reorder qty left blank
everywhere, so the system computes "make enough to reach max". **On-hand
column here is NOT real** — it was set equal to max as an uncounted
placeholder.

| SKU | Real min (pcs) | Real max (pcs) | Chip's sheet (bundles, min/max) | On-hand seeded (placeholder) | Staging ids (catalog / stock item) |
|---|---|---|---|---|---|
| S-6.58-38-50-10 | 20 | 60 | 4 / 12 | 60 | 3 / 2 |
| S-7.38-38-50-10 | 5 | 15 | 1 / 3 | 15 | 15 / 14 (new) |
| S-8.58-38-50-10 | 50 | 120 | 10 / 24 | 120 | 4 / 3 |
| S-9.38-38-50-10 | 10 | 25 | 2 / 5 | 25 | 16 / 15 (new) |
| S-10.34-38-50-10 | 35 | 70 | 7 / 14 | 70 | 5 / 4 |
| S-11.12-38-50-10 | 5 | 15 | 1 / 3 | 15 | 17 / 16 (new) |
| S-12.34-38-50-10 | 50 | 120 | 10 / 24 | 120 | 6 / 5 |
| S-13.12-38-50-10 | 5 | 35 | 1 / 7 | 35 | 18 / 17 (new) |
| S-16-38-50-10 | 35 | 80 | 7 / 16 | 80 | 8 / 7 |
| S-16.34-38-50-10 | 5 | 25 | 1 / 5 | 25 | 19 / 18 (new) |
| S-20-38-50-10 | 10 | 30 | 2 / 6 | 30 | 20 / 19 (new) |
| S-22-38-50-10 | 5 | 15 | 1 / 3 | 15 | 21 / 20 (new) |

The seven rows marked **(new)** were added by task 435 (catalog Add form +
first seed save). The other five had task-428 placeholder values, replaced
in-place; their movement history shows the `threshold-change` entry at the
moment placeholders became real numbers.

## Still INVENTED placeholders (task-428 values, unchanged)

These keep the `[PLACEHOLDER stock numbers — invented, confirm w/ Chip]
[task-428]` label in their catalog descriptions. Chip has not supplied numbers
for them (girth-weld/GWS numbers still pending; heavy-wall and accessory
counts never provided).

| SKU | Invented on-hand | Invented min | Invented max |
|---|---|---|---|
| S-12.34-12-50-10 | 6 | 2 | 8 |
| S-16-12-50-M-10 | 4 | 1 | 6 |
| S-24-38-50-10 | 4 | 2 | 6 |
| G-6.58-38-50 | 12 | 4 | 16 |
| ACC-BACKING_STRIP | 120 | 40 | 160 |
| ACC-PUTTY | 18 | 6 | 24 |

Not in either table: **S-12.34-14-50-1** (stock item #1, `[task-422 demo]`)
keeps its task-422 demo values (5 on hand, min 10 / max 45) and its
deliberately OPEN Reorder #2 — that item is staged for demo script step 9 and
was not touched by task 428 or 435 (verified byte-identical before/after the
435 load; Reorder #2 still the only reorder, still OPEN).

## What the demo says about these

For the real-threshold sleeves Devin can now tell Chip: "these min/max are the
numbers you sent, converted to pieces at 5 per bundle — here's where you change
any of them." The on-hand counts on those rows are still stand-ins (set to max)
until the shop does a real count. The six placeholder SKUs above stay
"we made these up so you could see the mechanic."

Two behaviors worth knowing before editing live:

- Every Save writes a ledger entry (visible on `/stock/items/<id>` → Movement
  history) — `initial-seed` on first save, `threshold-change` after — so the audit
  trail shows exactly when placeholders became real numbers.
- If a Save leaves on hand at/below the new min, an auto-reorder opens
  immediately. On-hand was seeded = max everywhere precisely so the real
  min/max load could not fire one; entering a real count later may
  legitimately fire reorders (that's the system working).

## Staging records touched by task 435

- Catalog rows **3, 4, 5, 6, 8**: description updated (placeholder label
  removed, `[on-hand not yet counted]` added); part number/type unchanged.
- Catalog rows **15–21** created (the 7 new sleeve SKUs, type `sleeve`).
- Stock items **#14–#20** created (one per new catalog row; stock item id 13
  does not exist on staging — pre-existing id gap, not from this task).
- Seed saves on all 12 sleeve SKUs: `threshold-change` ADJUSTMENT on the 5
  existing items, `initial-seed` ADJUSTMENT on the 7 new ones.
- No reorders created or changed; catalog row #2 / stock item #1 / Reorder #2
  untouched. G-6.58, S-12.34-12-50-10, S-16-12-50-M-10, S-24-38-50-10, and
  both ACC items untouched.
