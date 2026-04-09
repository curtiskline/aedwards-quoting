# Chip Edwards Feedback — Resolution Tracker

**Date:** 2026-04-09
**Source:** Chip Edwards feedback (2026-03-29), confirmed via test run 2026-04-09
**Status:** Complete — all 9 items resolved

---

## 1. Nominal-to-Actual OD Mapping

**Chip's Issue:** Customers request pipe by nominal size (e.g., "8 inch") but actual pipe OD differs (8" nominal = 8-5/8" OD). The system needs to translate nominal sizes to actual OD for correct pricing and part number generation. Over-sleeve OD must also be computed (pipe OD + 2× wall thickness).

**Task:** 123 — Nominal OD mapping + sleeve part number generation
**Agent:** codex-allenedwards-21
**Branch:** `nominal-od-partnums`

### Resolution
**Resolved** in commit `cc26ea9` (merged via `72fe83c`). Added a `NOMINAL_OD_MAP` lookup table in `pricing.py` that translates nominal pipe sizes (e.g., 8" → 8.625" OD). The mapping covers standard sizes from 2" through 48". Over-sleeve OD is computed as pipe OD + (2 × wall thickness). Both the automated pipeline and the web editor now resolve nominal sizes before pricing.

---

## 2. Part Number Generation

**Chip's Issue:** Sleeves need auto-generated part numbers following the encoding scheme: `{Type}-{SleeveID}-{WallThickness}-{Grade}-{Milled}-{Painted}`. These should appear on quote line items and PDFs.

- Type: S = Standard Half Sole, G = Girth Weld, CS = Compression
- Sleeve ID: decimal OD (e.g., 6.58 = 6-5/8")
- Wall Thickness coded: 316=3/16", 14=1/4", 38=3/8", 12=1/2", 58=5/8", 34=3/4", 78=7/8", 1=1"
- Grade: 50 = A572 Gr 50, 65 = A572 Gr 65
- Suffixes: M = milled (backing strip), P = painted

**Task:** 123 (same as above — tightly coupled with OD mapping)

### Resolution
**Resolved** in commit `cc26ea9`. Part numbers are now auto-generated following Chip's encoding scheme: `{Type}-{SleeveID}-{WallCode}-{Grade}[-M][-P]`. For example, a 6-5/8" OD, 1/4" wall, Gr50 milled sleeve generates `S-6.58-14-50-M`. The part number appears on quote line items in both the web editor and PDF output. Wall thickness codes, grade codes, and milling/painting suffixes all follow the spec exactly.

---

## 3. Over-Sleeve Part Numbers

**Chip's Issue:** Over-sleeves (sleeve that goes over another sleeve) use a different part number scheme. The OD is computed as pipe OD + (sleeve wall thickness × 2).

**Task:** 123 (included in OD mapping scope)

### Resolution
**Resolved** in commit `cc26ea9`. Over-sleeve part numbers use the `OS-` prefix (instead of `S-`). The OD is computed as pipe OD + (sleeve wall thickness × 2), then mapped to the nearest standard OD for part number encoding. The web editor's update route applies the same logic when `product_type` is `oversleeve`.

---

## 4. Pallet Quantity Rounding (Bags)

**Chip's Issue:** Bags are sold by the pallet. When a customer requests a piece count, the system must round up to whole pallets and display the rounding details.

Pallet sizes:
| GTW Size | Diameter Range | Pcs/Pallet | Price/Pallet |
|----------|---------------|------------|-------------|
| GTW 10-12 | 10-12" | 48 | $2,500 |
| GTW 16 | 14-18" | 52 | $4,200 |
| GTW 20-24 | 20-26" | 34 | $4,700 |
| GTW 30-36 | 30-38" | 30 | $4,650 |
| GTW 42-48 | 40-48" | 21 | $4,500 |
| Soft Set | any | 420 | $6,500 |

**Task:** 124 — Pallet and bundle quantity rounding
**Agent:** claude-dev-allenedwards-22
**Branch:** `pallet-bundle-rounding`

### Resolution
**Resolved** in commit `5495232`. Added `pallet_round()` function in `pricing.py` that rounds bag quantities up to whole pallets using the pcs/pallet table from Chip's spec. For example, 10 pcs of GTW 30-36 (30 pcs/pallet) → 1 pallet = 30 pcs. The original requested quantity is preserved in `specs_json["original_qty"]` and displayed as a rounding indicator in the web editor (e.g., "Rounded to 30 pcs (1 pallet)"). Applied in both the automated pipeline and the quote editor update route.

---

## 5. Bundle Quantity Rounding (Sleeves)

**Chip's Issue:** Sleeves are sold in bundles of 5. If a customer orders fewer than 5, quote the 5-piece bundle price. Do not mention the minimum on the quote — simply quote the bundle price.

**Task:** 124 (same as above)

### Resolution
**Resolved** in commit `5495232`. Added `bundle_round()` function in `pricing.py` that rounds sleeve quantities up to multiples of 5 (for standard sleeves ≤24" at 10' length). Rounding is silent per Chip's instruction — the quote simply shows the bundle quantity and price without mentioning the minimum. The `STANDARD_BUNDLE_PIECES = 5` constant is used in both the pipeline and the web editor. Original qty preserved in specs for the rounding indicator display.

---

## 6. Girth Weld Sleeve Pricing

**Chip's Issue:** Girth weld sleeves are priced per SET by diameter range, not by weight. The system was incorrectly requiring wall thickness and dropping girth weld items.

Pricing per set:
- 2-18" = $300/set
- 20-30" = $500/set
- 32-44" = $800/set

**Test failure (2026-04-09):** `WARNING: Dropping girth_weld item — missing diameter or wall_thickness: 20" Girth Weld Sleeves`

**Task:** 125 — Fix girth weld pricing, backing strips, and missing accessories
**Agent:** claude-dev-allenedwards-20
**Branch:** `fix-pricing-gaps`

### Resolution
**Resolved** in commit `ec0ca93`. Girth weld sleeves no longer require `wall_thickness` — the pricing engine now prices them per SET by diameter range only ($300 for 2-18", $500 for 20-30", $800 for 32-44"). The `WARNING: Dropping girth_weld item` error is eliminated. Three new tests confirm correct pricing for girth welds without wall thickness.

---

## 7. Backing Strip Bundling

**Chip's Issue:** Backing strips are included with a bundle of sleeves. Some customers always ask for them. The system should recognize backing strips as a product and consider auto-bundling with sleeve orders.

**Test failure (2026-04-09):** `WARNING: Cannot match accessory from description: 6" Backing Strips`

**Task:** 125 (grouped with other accessory fixes)

### Resolution
**Resolved** in commit `ec0ca93`. Added `backing_strip` to the accessory matcher in `pricing.py` with a price of $10/ea. The keyword matcher now recognizes "backing strip", "backing strips", and "back strip" descriptions. Added to the `pricing_catalog.py` accessory table. Test confirms `6" Backing Strips` no longer triggers the "Cannot match accessory" warning.

---

## 8. Missing Accessory Types (Weld Caps)

**Chip's Issue:** Weld caps are not recognized by the accessory matcher.

**Test failure (2026-04-09):** `WARNING: Cannot match accessory from description: 12" Weld Caps`

**Task:** 125 (grouped with other accessory fixes)

### Resolution
**Resolved** in commit `ec0ca93`. Added `weld_cap` to the accessory matcher in `pricing.py` with a price of $15/ea. The keyword matcher recognizes "weld cap", "weld caps", and "welding cap" descriptions. Added to the `pricing_catalog.py` accessory table. Test confirms `12" Weld Caps` is now correctly priced.

---

## 9. Small Diameter Bag Pricing (6")

**Chip's Issue:** Test showed no bag pricing for 6" diameter. Pallet pricing in the spec starts at GTW 10-12 (10-12"). Need to either add 6" pricing or ensure the system flags it clearly as NEEDS_PRICING.

**Test failure (2026-04-09):** `WARNING: No bag pricing for diameter 6.0: 6" Bag Weights`

**Task:** 125 (grouped fix)

### Resolution
**Resolved** in commit `ec0ca93`. Confirmed that 6" bags correctly return NEEDS_PRICING / TBD status since Chip's pallet pricing table starts at GTW 10-12 (10" minimum). The system no longer errors — it flags the item as needing manual pricing, which is the correct behavior until Chip provides 6" bag pricing. The "No bag pricing for diameter 6.0" warning is now handled gracefully.
