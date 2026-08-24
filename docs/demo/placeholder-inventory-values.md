# Placeholder inventory values on staging (task 428)

> **ALL NUMBERS BELOW ARE INVENTED.** Chip has not yet supplied his real sleeve
> min/max reorder points or on-hand counts (his 2026-08-21 reply: "we have min max
> somewhere on sleeves, I'll look" — insight I143, still outstanding). These
> placeholders exist only so the inventory demo is complete. Every seeded item is
> labeled **`[PLACEHOLDER stock numbers — invented, confirm w/ Chip] [task-428]`**
> in its catalog description, so the label is visible on the Stock list, the
> seeding screen, and each item's detail page. Replace each value with Chip's real
> figure as he supplies it.

**Where Chip changes min/max (and every other number):** Stock (top nav) →
"Seed counts & thresholds" link → **`/stock/seed`**. Each product is one row with
four inline boxes — **count** (on hand), **min**, **max**, **reorder** — and a
**Save** button per row. That one screen is the single place thresholds are edited;
the item detail page (`/stock/items/<id>`) shows Min/Max read-only and only takes
receipts/adjustments.

Environment: https://staging.quotes.vectorforgeinteractive.com · login
`dev@local.test` / `Demo408-staging`. Seeded 2026-08-24 through the real UI
endpoints (Admin → Catalog "Add" form, then per-row Save on `/stock/seed`); each
save wrote an `initial-seed` ADJUSTMENT to the movement ledger, verified on the
rendered pages afterward.

## The invented numbers

Part numbers and descriptions are REAL — taken from the most-quoted lines in the
historical quote corpus, so the demo matches what the shop actually makes. Only
the quantities are invented. Min = reorder point (auto-reorder fires when on hand
drops to/at/below it); Max = top-up target; Reorder qty was left blank everywhere,
so the system computes "make enough to reach max".

| SKU | Invented on-hand | Invented min | Invented max | Rationale (why plausible) | Exact UI path Chip uses to change it |
|---|---|---|---|---|---|
| S-6.58-38-50-10 | 24 | 8 | 30 | Most-quoted sleeve in the corpus (8 quotes) — fastest mover, so the deepest shelf stock. | `/stock/seed` → row **S-6.58-38-50-10** → count/min/max boxes → Save |
| S-8.58-38-50-10 | 16 | 6 | 24 | Common 8-5/8" line-pipe size, quoted repeatedly — second-tier mover. | `/stock/seed` → row **S-8.58-38-50-10** → count/min/max boxes → Save |
| S-10.34-38-50-10 | 10 | 4 | 16 | Mid-size sleeve, steady but slower than the small diameters. | `/stock/seed` → row **S-10.34-38-50-10** → count/min/max boxes → Save |
| S-12.34-38-50-10 | 18 | 6 | 24 | The 12-3/4" sleeve family Chip referenced; 5 quotes in history — kept well-stocked. | `/stock/seed` → row **S-12.34-38-50-10** → count/min/max boxes → Save |
| S-12.34-12-50-10 | 6 | 2 | 8 | Heavy-wall (1/2") variant of the same family — slower mover, more steel per stick. | `/stock/seed` → row **S-12.34-12-50-10** → count/min/max boxes → Save |
| S-16-38-50-10 | 8 | 3 | 12 | Large diameter, moderate demand (3 quotes) — smaller buffer. | `/stock/seed` → row **S-16-38-50-10** → count/min/max boxes → Save |
| S-16-12-50-M-10 | 4 | 1 | 6 | Milled heavy-wall — near make-to-order, minimal shelf stock. | `/stock/seed` → row **S-16-12-50-M-10** → count/min/max boxes → Save |
| S-24-38-50-10 | 4 | 2 | 6 | Largest common size — bulky to store, low turn. | `/stock/seed` → row **S-24-38-50-10** → count/min/max boxes → Save |
| G-6.58-38-50 | 12 | 4 | 16 | Girth-weld companion to the top-selling 6-5/8" size (4 quotes). | `/stock/seed` → row **G-6.58-38-50** → count/min/max boxes → Save |
| ACC-BACKING_STRIP | 120 | 40 | 160 | Consumable included with every sleeve — stocked in bulk, reorder point set high. | `/stock/seed` → row **ACC-BACKING_STRIP** → count/min/max boxes → Save |
| ACC-PUTTY | 18 | 6 | 24 | Pint consumable sold alongside wrap jobs — carton-level stock. | `/stock/seed` → row **ACC-PUTTY** → count/min/max boxes → Save |

Not in this table: **S-12.34-14-50-1** (stock item #1, `[task-422 demo]`) keeps its
task-422 demo values (5 on hand, min 10 / max 45) and its deliberately OPEN
Reorder #2 — that item is staged for demo script step 9 and was not touched.

## What the demo says about these

In the first demo Devin tells Chip: "we made these inventory numbers up so you
could see the mechanic — here's where you change each one." Then he opens
`/stock/seed`, points at a row, edits min/max, and hits Save. Chip's real numbers
replace the placeholders the same way (or in one shot via the CSV import linked at
the top of `/stock/seed`: columns `part_number, on_hand, min, max, reorder_qty`).

Two behaviors worth knowing before editing live:

- Every Save writes a ledger entry (visible on `/stock/items/<id>` → Movement
  history) — `initial-seed` on first save, `threshold-change` after — so the audit
  trail shows exactly when placeholders became real numbers.
- If a Save leaves on hand at/below the new min, an auto-reorder opens
  immediately. All placeholder values were chosen with on hand above min so no
  spurious reorders exist; entering real numbers may legitimately fire one.

## Staging records created by task 428 (all additive)

- Product catalog rows **3–13** (the 11 SKUs above, each description suffixed
  with the placeholder label).
- Stock items **#2–#12** (one per catalog row, in table order above).
- Stock movements **#7–#17** (one `initial-seed` ADJUSTMENT per item).
- No reorders created; catalog row #2 / stock item #1 / Reorder #2 untouched.
