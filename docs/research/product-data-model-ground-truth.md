# Product data model: ground truth (type vs family vs SKU/part-number vs service)

Task 357 (parent 347). READ-ONLY investigation to give Devin the real model before
he sends Chip the ~24hr terminology sign-off note and scopes a rename. No code or
schema was changed.

Sources: `src/app/models.py`, `src/app/routes.py`, `src/app/templates/...`,
`src/allenedwards/pricing_catalog.py`, `src/allenedwards/db_writer.py`,
`migrations/versions/*`, the live `instance/allenedwards.db`, and the Aug 10 Chip
onsite transcripts (`~/Downloads/Aug 10 at 1-26/1-40/1-49/1-50 PM.txt`).

---

## TL;DR

There are **three unrelated "type"/"category" systems** and **three names for one
identifier**, and no foreign keys tie any of them together. Everything is loosely
coupled by bare lowercase strings.

1. `ProductType` table — the admin "Product Types" page. Free-form, editable list
   (sleeve, bag, girth_weld, compression, accessory, service, shipping). Drives the
   Type dropdown in the line-item editor and section grouping on the pricing page.
2. `ProductFamily` enum — a **hardcoded** 8-value Python enum used ONLY by the
   Product Catalog. This is the "Family" column Chip saw. It is a *different list*
   from the product types above.
3. `QuoteLineItem.product_type` / `PricingTable.product_type` — a **bare string**
   on each line item and price row. Not an FK to `ProductType`; any string is legal.

Identifier: the same concept is stored/labelled as **`sku`**, **"Item Number"**, and
**`part_number`** in three places — and the line-item editor shows a `SKU` box AND an
"Item Number" box (whose form field is literally `part_number`) side by side. That is
the "same thing in there twice" Chip pointed at. His decision: collapse all three to
**part number**.

Devin's hypothesis is essentially correct: **"Family" is a second, redundant
product-type field** that happened to land on the catalog page, and the catalog page
shows Family *instead of* Type because Family is the only categorical column the
`ProductCatalog` model has.

---

## 1. Field-by-field map (current model)

### `QuoteLineItem` (`models.py:183`) — the actual quote lines
| Column | Type | Null | Notes |
|---|---|---|---|
| `product_type` | `str` | NO, indexed | **Bare string**, not an FK. Values: sleeve, bag, girth_weld, compression, omegawrap, accessory, service, flat, shipping, (legacy: oversleeve). |
| `sku` | `String(100)` | yes, indexed | Added 2026-05-12. Free text. Editor shows it as "SKU". |
| `part_number` | `str` | yes | Editor labels this **"Item Number"** (field `name="part_number"`). |
| `description` | `str` | NO | The human line text. |
| `quantity`, `unit_price`, `line_total` | Numeric | NO | |
| `specs_json` | JSON | yes | diameter/wall/grade/length/milling/painting/etc. the price was computed from. |
| `sort_order` | int | NO | |

So a single line carries **both** `sku` and `part_number` — two nullable identifier
columns for one concept.

### `ProductType` (`models.py:244`) — admin "Product Types" page
| Column | Type | Notes |
|---|---|---|
| `name` | str, unique, indexed | machine slug, e.g. `girth_weld` |
| `display_label` | str | e.g. "Girth Weld" |
| `sort_order` | int | ordering in UI |
| `is_active` | bool | soft on/off |

Editable via `/admin/product-types/*` (`routes.py:2458` add, `:2489` update,
`:2507` move). "Add type" takes only a **display label**; the slug is derived by
`re.sub(r"[^a-z0-9]+","_", label.lower())` (`routes.py:2462`). Seeded defaults
(`DEFAULT_PRODUCT_TYPES`, `routes.py:78`): sleeve, bag, girth_weld, compression,
accessory, service, shipping.

### `ProductFamily` (`models.py:28`) — hardcoded enum, catalog only
`sleeve, girth_weld, bag, omegawrap, pipe_jack, backing_strip, compression_sleeve,
other`. Python enum — **changing it requires a code deploy + migration**, unlike
Product Types which are editable in the UI. This is the mismatch behind Chip's "the
product types aren't listed on the catalog page."

### `ProductCatalog` (`models.py:254`) — the "Product Catalog" admin page
| Column | Type | Null | Notes |
|---|---|---|---|
| `sku` | str, **unique, NOT NULL**, indexed | NO | **Every catalog row is forced to have a SKU.** There is no name-only path here. |
| `description` | Text | NO | |
| `product_family` | `ProductFamily` enum | NO, indexed | The "Family" column/dropdown. |
| `is_active` | bool | NO | |

Rendered by `partials/product_catalog_table.html` — columns are **SKU · Description ·
Family · Active**. There is **no Type column**, because the model has no `product_type`
— its only category field is `product_family`. Add/edit routes: `routes.py:2320`,
`:2352`. The Family dropdown is built from the enum (`_product_family_choices`,
`routes.py:273`).

### `PricingTable` (`models.py:234`)
`product_type` (bare string, same namespace as line items) + `key_fields` JSON +
`price`. Grouped into pricing-page sections by matching `product_type` string against
the `ProductType` slugs (`_group_pricing_rows`, `routes.py:137`).

---

## 2. Plain-English: the type/family/SKU tangle

**Why the catalog shows "Family" and not "Type".** Two different categorisation
tables were built at different times:
- `ProductType` (migration 2026-04-10) — the editable admin list, wired to line items
  and pricing.
- `ProductCatalog`/`ProductFamily` (migration 2026-05-11) — a later, separate feature
  with its own hardcoded enum. Whoever added the catalog gave it `product_family`
  instead of reusing `product_type`. So the catalog page can only show Family; it
  literally has no type field to show. Chip's read ("family should probably have been
  product type") and Devin's hypothesis are both right — and note Chip himself landed
  on "I think types and family are kind of the same" and "compression sleeve is a
  sleeve" during the walkthrough, i.e. Family and Type are the same axis expressed
  twice, with two different value lists.

**The two value lists don't even match.** Product Types include `accessory`,
`service`, `shipping`; Family includes `omegawrap`, `pipe_jack`, `backing_strip`,
`compression_sleeve`, `other`. `compression` (type) vs `compression_sleeve` (family)
and `omegawrap` (only a family, but a `product_type` in pricing) are the same things
under different spellings.

**Nothing is joined.** `QuoteLineItem.product_type` is a free string with no FK to
`ProductType`; the editor even synthesises an extra `<option>` when a line's stored
type isn't in the active list (`_line_items.html:15`, `_resolve_product_type`
`routes.py:362`). So historical/odd values (e.g. `oversleeve`, `flat`) survive as
one-off strings. `ProductCatalog.sku` and `QuoteLineItem.sku`/`part_number` are not
linked either — picking a catalog item copies text, it does not reference a row
(`product_catalog_search`/`lookup`, `routes.py:2149`/`2179`).

**SKU vs Item Number vs Part Number = one thing, three names.** In the line-item
editor (`_line_items.html:20-26`) there is a **SKU** input (`name="sku"`) *and* an
**"Item Number"** input whose form field is `name="part_number"`. Pricing rows call it
`part_number` (`pricing_row.html:15`); the PDF prints `part_number` under the header
"Item Number" (`pdf_generator.py:397`); bag pricing seed uses `part_number`
(`pricing_catalog.py`). This is exactly Chip's "SKU / item number / part number all
mean the same thing → standardize on **part number**." (Editable item-number field
was just merged as task 349.)

**"Service type" is not a real thing.** There is no service-type field. `service` is
one **value** of `product_type` (seeded label "Service"), used for line items like
supervisor/training and milling/painting service lines (`pricing_catalog.py:116`,
`pricing.py:1359`). Chip's "a 'service type' whose purpose is unclear" is just the
`service` product-type value sitting in the same list as sleeve/bag.

---

## 3. Counts: name-only vs ID'd products

**No production catalog data exists to count locally, and the checked-in dev DB is
stale — this is the honest state, not a measured ratio.**

- `instance/allenedwards.db` predates this work: it has **no `product_catalog` and no
  `product_type` table**, `quote_line_item` has **no `sku` column**, and
  `alembic_version` is empty. It holds only **6 test line items** (3 sleeve, 2 bag,
  1 girth_weld); exactly **1 of 6** has a `part_number` (`GTW-24`), 5 are blank.
- The `ProductCatalog` schema **forbids** name-only rows: `sku` is `NOT NULL UNIQUE`.
  So in the *current* model a catalog product cannot exist without an identifier —
  the "name-only products" Chip described (and his spreadsheets) have **no home in the
  schema yet**. Today a name-only product would be entered as a free-text line item
  (description filled, `sku`/`part_number` blank), like 5 of the 6 test rows above.
- The pricing seed (`pricing_catalog.py`) is the closest thing to a real product list:
  **only the 5 bag rows carry real part numbers** (GTW 10-12, GTW 16, GTW 20-24,
  GTW 30-36, GTW 42-48). The ~24 sleeve/girth_weld/omegawrap/accessory/service entries
  are keyed by specs or by a name string (`key`), **with no part number at all** —
  i.e. the majority of catalog-ish entries are effectively name-only, matching Chip.

**Recommendation for the deferred ID-fallback decision:** we cannot derive the rule
from local data because there is no populated catalog. To get the real ratio, pull
Chip's spreadsheets (the ones shown on the call) or a production DB export. What the
code *does* tell us: name-only is the common case, and the current unique-NOT-NULL SKU
constraint is the thing that will break when we import Chip's real list.

---

## 4. Recommendation: the clean model + naming

### Terminology (for Chip's sign-off note)
- **Type** = the top-level product category (sleeve, composite, bag, girth weld,
  compression, accessory, service). One editable list. Chip's mental model: "everything
  is a type; part numbers are variations underneath." Keep the word **Type**.
- **Family → retire the word.** "Family" and "Type" are the same axis. Fold the catalog
  onto **Type** and drop "Family" from the UI. (Chip: "the product family, that naming
  is also not good… we're saying the same thing several places.")
- **Part number** = the single identifier. Retire **SKU** and **Item Number** as
  user-facing labels; standardize on **Part Number** everywhere (editor, catalog, PDF,
  pricing). This is Chip's explicit decision.

### Structural (for the rename/refactor scoping — not implemented here)
1. Give `ProductCatalog` a **`product_type`** (FK or slug into `ProductType`) and
   **drop `product_family`/`ProductFamily`**. Migrate existing family values to the
   nearest type (compression_sleeve→compression, omegawrap→composite/omegawrap,
   backing_strip→accessory, etc.). This makes the catalog show Type and kills the
   second list.
2. Add **composite** as a product type (Chip is rebranding Omega-wrap → composite;
   he added a "composite" type live on the call). Omega-wrap becomes a part-number/
   variation under composite, not its own family.
3. **Collapse `sku` + `part_number` into one `part_number` column** on
   `QuoteLineItem`; migrate any `sku` value into `part_number` where `part_number` is
   blank; remove the "SKU" input from the editor. Rename the catalog's `sku` column to
   `part_number` in the UI (label) and ideally in schema.
4. **Relax the catalog identifier to nullable** (or auto-generate a placeholder) so
   **name-only products** are first-class, per Chip. Defer the exact fallback rule
   (blank vs generated stub) until we see Chip's spreadsheet volumes — see §3.
5. Optional: make `QuoteLineItem.product_type` an FK to `ProductType` so the type list
   is truly single-source, instead of a free string.

None of this is implemented. Deliverable is this doc; canon proposals filed for the
durable facts.
