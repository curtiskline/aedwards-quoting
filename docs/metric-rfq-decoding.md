# Metric RFQ decoding (mm / metres)

Allan Edwards quotes, part numbers, and price-per-pound tables are entirely
imperial. RFQs from outside North America state every dimension in millimetres
or metres. This document records how that conversion works and, in particular,
how a metric **length** maps to a quoted length.

Implementation: `src/allenedwards/units.py`, used by `parser.py` (decode) and
re-exported through `pricing.py`.

## Why this exists

An unconverted metric dimension does not read as *wrong* to the pricing layer —
it reads as *absent*. `float("12.7 mm")` raises, the field becomes `None`, and
the missing-dimension default fires.

That is the task 330 incident. A fully metric RFQ from Azimuth Energy Sdn Bhd
(2026-07-29, NPS 36 Type B split sleeve) stated a 12.7 mm carrier pipe wall —
exactly 1/2". Prod quote 126-086 quoted a **3/8"** sleeve and stamped the line
`wall thickness defaulted to 3/8"`. The dimension was present the whole time.

## How dimensions are resolved

Resolution happens once, at decode time, in `parser._resolve_item_dimensions`:

1. **Unit-bearing values convert.** `"12.7 mm"`, `"1.27 cm"`, `"3.0 metres"`,
   `'1/2"'`, `"6-5/8"` all resolve. Previously only bare decimals did.
2. **Converted inches snap to a catalog fraction.** Source documents round metric
   to one decimal, so 12.6 mm and 12.8 mm must both land on 1/2" rather than
   carrying a raw decimal into a part number and inventing a size that does not
   exist. `snap_to_catalog_fraction` is the numeric twin of `decimal_to_fraction`
   and mirrors its branch structure, so the stored number and the printed label
   can never disagree.
3. **A dropped dimension is recovered from the description.** When the LLM returns
   `null` for a metric value it could not map, the labelled spec text is scanned
   (`find_metric_thickness`, `find_metric_length`, `find_metric_diameter`). These
   match only *labelled* measurements, so the diameter (`914.4 mm`) can never be
   read as the wall thickness (`12.7 mm`) in the same block, and a non-dimensional
   figure like `6,895 kPag` is never mistaken for either.
4. **Bare implausible values are read as metric.** A wall thickness above 2 or a
   diameter above 60 with no unit is millimetres — both thresholds sit far above
   anything AE makes (max wall 1", max diameter 48").
5. **Every conversion is recorded** on `ParsedItem.unit_notes` and surfaced on the
   quote line, so a converted dimension is never silent.

The LLM prompt was changed to *stop* converting metric itself: it now passes
metric values through verbatim with their units (`"wall_thickness": "12.7 mm"`).
Conversion policy lives in code, in one place, rather than being asserted in both
the prompt and the pricing layer where the two copies could drift.

## Metric length policy — INTERIM

**Constant: `units.METRIC_LENGTH_POLICY` (currently `"round_to_standard"`).**

A metric length rarely lands on a standard stock piece: 3,000 mm is 9.843 ft.

| Converted length | Behaviour | Line note |
|---|---|---|
| Within 1 ft of a standard piece | Quote the standard piece length | `length 3,000 mm (9.843 ft) quoted as standard 10 ft piece` |
| Not within 1 ft | Quote the converted length literally | `length 5000 mm converted to 16.404 ft — verify` |

Standard piece lengths are 10 ft for sleeves and 6 ft for girth welds
(`parser.STANDARD_PIECE_LENGTH_FT`). Tolerance is
`METRIC_LENGTH_ROUNDING_TOLERANCE_FT`.

Rounding to 10 ft was chosen as the interim behaviour because it is a **no-op on
the price that already shipped** for the Azimuth quote — it only adds the
explanatory note — so it cannot make production worse while the commercial
question is open. A metric length that is *not* close to a stock piece is never
reshaped; it is quoted literally and flagged.

### Status: awaiting Chip Edwards' confirmation

This is a commercial call, not a technical one, and Chip has **not** confirmed it.
The alternatives are:

- `"round_to_standard"` — current interim default.
- `"literal"` — always quote the converted length (9.843 ft), odd part numbers
  and odd pricing included.
- Flag the line for manual review instead of auto-quoting the length.

Switching is a one-line change to `METRIC_LENGTH_POLICY`. Do not add a second
copy of this rule elsewhere.

## What this deliberately does not touch

**Grade.** The Azimuth RFQ's `API 5L X Series (PSL2)` is the **carrier pipe**
grade. AE sleeves are A572 GR50/GR65 — a different material spec for a different
part. The API 5L → A572 mapping is asserted by the LLM prompt at
`parser.py` (the "API 5L grades must be mapped to A572 equivalents" block), which
is what produced GR65 on quote 126-086. That mapping translates a carrier-pipe
spec into a sleeve material grade; it happened to be defensible here, but whether
it should exist at all is an open question and out of scope for task 330.

**The task 326 default.** A genuinely absent sleeve wall thickness still defaults
to 3/8" GR50 with a visible line note. Task 330 only stops that default from
firing when the thickness *is* present in metric.

## Deploying a change to this code

Decode and pricing changes ship in **two** services. `deploy_web.sh` alone does
not update RFQ decoding — `deploy.sh` (the monitor) must run too. Prod installs
as a wheel, so verify with:

    venv/bin/python -c "import allenedwards.units, allenedwards.pricing"
