# Task 516: Product Catalog authority audit

Date: 2026-10-19. Production was queried read-only; no customer contact data or
message bodies were copied into this report.

## Finding

The LLM does not emit quote prices. For sleeves, girth welds, bags, compression,
accessories, and services, pricing and the persisted part number come from the
deterministic pricing engine. Unknown services/accessories become `$0 / TBD`.
Before this task, however, the catalog was advisory rather than authoritative:

- parser context listed only active catalog rows;
- an LLM `sku` was accepted as text but not persisted as a catalog relationship;
- formula-generated part numbers were persisted whether or not a catalog row
  existed;
- manual creation accepted arbitrary identity text, with autocomplete as the
  only catalog guard;
- quote lines had no `ProductCatalog.id` foreign key or durable match outcome.

There was one genuine latent invention path: an unrecognized `composite`
description defaulted to the Carbon pricing variant in
`pricing._match_omegawrap_key`. It now returns no match, which sends the line to
the existing `$0 / TBD` path. A regression test proves unspecified OmegaWrap is
not silently sold as Carbon. No recent production row could be proven to have
taken that bad fallback: the recent Carbon rows with retained parser snapshots
all explicitly asked for carbon.

## Production counts

Window: non-deleted quotes created in the 120 days ending 2026-10-19; material
lines exclude `note` and `shipping`.

| Measure | Count |
|---|---:|
| Catalog rows | 225 |
| Active catalog rows | 26 |
| Material quote lines | 242 |
| Match to an active row by exact part number or exact description | 50 |
| Match to any row, active or inactive | 56 |
| No match to any catalog row | 186 |
| Uncataloged lines with an explicit operator part-number override | 28 |

Origin split using `Quote.source_email_id` (not a perfect manual-edit marker):

| Origin | Lines | Active-catalog match | No active match |
|---|---:|---:|---:|
| Email intake | 174 | 35 | 139 |
| No source/manual/revision | 68 | 15 | 53 |

Uncataloged lines by type, matching against all 225 rows:

| Type | Lines | Catalog match | Uncataloged | Uncataloged operator part override |
|---|---:|---:|---:|---:|
| sleeve | 86 | 11 | 75 | 4 |
| bag | 66 | 17 | 49 | 14 |
| service | 26 | 0 | 26 | 9 |
| girth_weld | 21 | 2 | 19 | 0 |
| accessory | 18 | 15 | 3 | 0 |
| composite | 13 | 10 | 3 | 1 |
| on_site_fill | 11 | 0 | 11 | 0 |
| compression | 1 | 1 | 0 | 0 |

The misses are not 186 demonstrated inventions:

- **125** are non-overridden sleeve, girth-weld, or bag lines produced by the
  deterministic formula/range pricing paths. They are legitimate variants of
  product families the old catalog did not enumerate.
- **11** are location-specific on-site-fill lines, which are intentionally
  custom priced and not catalog products.
- **28** carry an explicit human `part_number_override`. Report these
  separately: they are operator decisions, not LLM inventions.
- The remaining **22** are service/accessory/composite lines requiring catalog
  cleanup or review. The retained parser evidence does not prove any of them was
  an invented LLM SKU.

The catalog itself also contains import-era spreadsheet identifiers (including
the `G36/58`-style junk called out in the meeting). There are 199 inactive rows;
those historical/imported identities must not be attributed to live quote-time
invention, and inactive status now keeps them out of parser context and editor
lookup.

Immutable parser snapshots exist only for recent quotes. Across those snapshots,
the LLM extracted five nonblank SKU candidates; all five exactly matched a row
in the catalog. There were **zero observed live noncatalog LLM SKU candidates**
and therefore zero proven live LLM part-number inventions in the auditable
snapshot window.

## Implemented policy seam

New and edited lines resolve deterministically by exact normalized part number,
then exact description, then an inclusive diameter/wall-thickness catalog
range, with product type used as a compatibility guard. A unique match stores
`QuoteLineItem.catalog_id` and freezes the catalog part number, description,
and type onto the quote's point-in-time line. Active identities win. An
inactive-only match remains linked but is distinctly flagged for human review;
inactive rows are not shown to the parser or editor lookup.

Outcomes are persisted as `active`, `inactive`, `unmatched`, `ambiguous`,
`type_mismatch`, `unchecked` (historical), or `not_applicable`. Unmatched lines
retain their existing identity and price under the current flag-only policy,
but the editor and confidence signal expose them for human review. The
`CATALOG_ALLOW_INACTIVE_MATCHES` configuration controls whether an inactive
match itself requires review; it defaults to false.

`CATALOG_HARD_TBD_ENFORCEMENT` is the rollout switch for strict enforcement. It
defaults to false as agreed. When enabled, unmatched, ambiguous, and
type-mismatched new writes are forced to `$0 / TBD`; an inactive real identity
is instead retained and flagged. This can be enabled after the range rows have
been seeded and verified on staging.

## Range and source-sheet findings

The October 2026 source sheet confirms three and only three girth-weld families:
2–16 inches at $325/set, 18–30 at $550/set, and 32–44 at $875/set. The old
persisted pricing bands were 2–19 / 20–31 / 32–44, so an 18-inch line could use
the low band. The migration corrects the boundaries and rates. It also updates
the four sheet-backed steel-sleeve rates (1/4, 5/16, 3/8, and 1/2 inch; GR65 is
$0.17/lb above GR50).

The range model stores inclusive diameter and optional wall-thickness bounds.
This lets the seven sheet-backed sleeve families be authoritative without
creating a SKU per diameter. The sheet supplies no steel/girth part numbers, so
the staging seed deliberately stores NULL identifiers. Proposed identifiers
for Chip's approval only (not seeded and not production-authoritative):
`SLV-025-RANGE`, `SLV-03125-RANGE`, `SLV-0375-RANGE`, `SLV-050-RANGE`,
`GTW-02-16`, `GTW-18-30`, and `GTW-32-44`.

The April 2026 sheet supplies real bag part numbers and ranges: GTW 10-12
(10–12), GTW 16 (14–18), GTW 20-24 (20–26), GTW 30-36 (30–38), and GTW 42-48
(40–48), plus Soft Set. Bag price/quantity correction is owned by tasks 517/518
to avoid conflicting migrations; this task seeds only their catalog identities
and dimensional authority on staging.
