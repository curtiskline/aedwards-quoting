# CURRENT OFFERING — authoritative reference (as of 2026-08-19)

Task 394 (parent: epic 347). Audience: AI agents about to build engine features.
This describes what the system **actually is today**, verified line-by-line against the
source at commit `4bb4109` (branch `main`, 2026-08-19). Where canon or older research
docs disagree with the code, **the code wins** and the discrepancy is noted. The
aspirational engine design lives in `docs/research/amazon-engine-end-to-end-design.md`
— cross-referenced, not duplicated here.

Anything not confirmed in code is explicitly marked **unverified**.

## Table of contents

1. [System overview & purpose](#1-system-overview--purpose)
2. [Component architecture: two processes, one database](#2-component-architecture)
3. [Code layout & module responsibilities](#3-code-layout--module-responsibilities)
4. [Data model](#4-data-model)
5. [Web routes & the quote editor](#5-web-routes--the-quote-editor)
6. [Pipeline stages end-to-end](#6-pipeline-stages-end-to-end)
7. [Pricing model](#7-pricing-model)
8. [Terminology in effect](#8-terminology-in-effect)
9. [Env flags & runtime gates](#9-env-flags--runtime-gates)
10. [Infrastructure & deploy](#10-infrastructure--deploy)
11. [Testing](#11-testing)
12. [Known constraints & hazards](#12-known-constraints--hazards)
13. [What does NOT exist yet; engine attach points](#13-what-does-not-exist-yet-engine-attach-points)
14. [Suggested canon claims](#14-suggested-canon-claims)

---

## 1. System overview & purpose

The live product is a **quote system for Allan Edwards, Inc.** (pipeline products
manufacturer — sleeves, girth weld bands, geotextile bags, OmegaWrap composites,
accessories, services):

- A shared mailbox (**AEResponder@allanedwards.com**, the "e-responder") is polled
  every 5 minutes by a monitor process.
- Each unread email is LLM-classified as RFQ / not-RFQ, LLM-decoded into structured
  line items, priced by a deterministic pricing engine, and written to the web app's
  database as a **draft quote** (and optionally dropped as a PDF draft in Outlook).
- A **human reviews every quote** in a Flask web app (queue → editor → Send). Send is
  100% human-initiated; the email goes out via Microsoft Graph with the PDF attached,
  and the quote is frozen as an immutable `QuoteVersion`.

**The pipeline stops at "quote sent."** There is no order, fulfillment, or inventory
subsystem anywhere in the codebase (verified again 2026-08-19; see §13). That is the
gap the approved back-end automation engine (Chip approved verbally 2026-08-19) will
fill.

## 2. Component architecture

Two long-running processes on one box, sharing one SQLite database:

| Process | Entry | What it is |
|---|---|---|
| `aedwards-monitor` | `aedwards monitor --poll-minutes 5` → `allenedwards.cli:monitor` (`src/allenedwards/cli.py:486`) | Headless polling pipeline: inbox → classify → parse → price → DB write + Outlook draft. Imports the Flask app purely for DB access (`cli.py:573-576`). |
| `aedwards-web` | gunicorn, 3 workers, `127.0.0.1:8000`, `app.wsgi:app` (`deploy/aedwards-web.service`) | The Flask review/edit/send UI (htmx server-rendered partials) plus admin screens. |

Coupling points between them:

- **The database.** When `ENABLE_DB_WRITES` is on, the monitor calls
  `app.create_app()` and writes ORM rows through `src/allenedwards/db_writer.py`
  under a Flask app context (`monitor.py:322`).
- **The quote-number generator** — both mint numbers through the single canonical
  generator `src/app/quote_numbers.py:30` (consolidated under task 377 after the
  2026-08-13 collision outage).
- **The pricing engine** — the web app imports pricing functions from
  `allenedwards.pricing` for spec-driven re-pricing (`src/app/routes.py:34-53`), and
  pricing reads the web app's `PricingTable`/`ProductType`/`ProductCatalog` tables
  when an app context exists (`pricing.py:316-341`, `parser.py:912-941`).
- **The PDF generator** — both paths render quote PDFs via
  `allenedwards.pdf_generator.generate_quote_pdf` (`pdf_generator.py:596`).

Package layout note: the installed distribution is `allenedwards` (yes, spelled with
"en") exposing console scripts `allenedwards` (CLI) and `allenedwards-web`
(`pyproject.toml:44-46`); prod wraps the CLI as `/usr/local/bin/aedwards`
(`deploy/deploy.sh:157-161`).

## 3. Code layout & module responsibilities

### `src/allenedwards/` — the pipeline package

| File | Lines | Owns |
|---|---|---|
| `monitor.py` | 758 | `InboxMonitor` polling loop, watermark + processed-ID state file (`ProcessedState`, `:28`), idempotency claims, failed-intake quarantine, Outlook draft creation, MIME bridge into the parser (`_parse_message_to_rfqs`, `:705`). |
| `parser.py` | 1416 | LLM classify (`classify_rfq`, `:1271`) and decode (`parse_rfq_multi`, `:1146`); the classify/parse system prompts (`:99`, `:116`); attachment text extraction (PDF `:374`, XLSX `:463`, CSV `:547`); ship-to/bill-to gating (`:1051-1117`); quote-number & PO-number extraction (`:1316-1396`); dataclasses `ParsedItem`/`ShipTo`/`ParsedRFQ` (`:290/:312/:325`). |
| `pricing.py` | 1571 | Deterministic pricing engine: `generate_quote` (`:1422`), `price_item` (`:994`), sleeve weight/price formula (`:425-486`), bundle/pallet/pack rounding, part-number & description generation, DB-backed `PricingSnapshot` with 5 s cache (`:292-422`). Dataclasses `QuoteLineItem` (`:226`) and `Quote` (`:256`) — distinct from the ORM classes of the same names. |
| `pricing_catalog.py` | 138 | Hardcoded default price tables (per-lb, girth weld tiers, bag tiers, flat/other rates) used as fallback and DB seed (`default_pricing_rows()`, `:67`). |
| `db_writer.py` | 470 | Bridge from `pricing.Quote` → ORM rows: `write_quote_to_db` (`:324`), inbound-email claim (`:344-353`), customer fuzzy auto-match (`_match_customer`, `:109`), attachment persistence (15 MB cap, `:40`), specs_json persistence (`:409-445`), audit row (`:447`). |
| `cli.py` | 606 | Click CLI: `parse`, `quote`, `batch`, `monitor` commands; env loading (`:21`); LLM provider selection (`:47-87`); email-provider selection and all monitor env gates (`:503-576`). Contains a legacy timestamp quote-number POC (`:109`) used only by the offline `quote`/`batch` commands. |
| `pdf_generator.py` | 625 | ReportLab quote PDF (`QuotePDFBuilder`, `:74`; `generate_quote_pdf`, `:596`), optional warning banner (`:99`), customer-facing note filtering via `line_notes` (`:403`). |
| `line_notes.py` | 159 | Audience split for per-line provenance notes: editor shows raw notes; the customer PDF only prints clauses matched by an explicit allowlist (`customer_note`, `:152`) — fails closed. |
| `units.py` | 499 | Metric↔imperial conversion shared by parser and pricing (task 330: "12.7 mm" must decode as 1/2", not fall to a default). Fraction tables, metric finders. |
| `ship_to.py` | 75 | Canonical 8-key ship-to dict (`SHIP_TO_KEYS`, `:14`; `normalize_ship_to`, `:47`) collapsing the two historical shapes; `is_domestic_ship_to` (`:70`) gates the freight calc to US addresses. |
| `email_provider.py` | 36 | Abstract `EmailProvider` + provider-agnostic `EmailMessage` dataclass. |
| `outlook.py` | 344 | O365 Graph client: client-credentials auth (`:92`, preferred) or ROPC password auth (`:108`); `fetch_messages` with `receivedDateTime gt <since>` filter (`:153`); `get_attachments` (`:195`); `create_draft` (`:259`); `send_mail` (`:295`); folder move (`:332`). |
| `gmail.py` | 187 | Gmail alternative provider (service-account or OAuth refresh token). Implements only `fetch_messages`/`mark_read` — **no attachments, no drafts, no send** (the monitor feature-detects with `hasattr`, e.g. `monitor.py:235`, `:273`). |
| `providers/` | — | `base.py` — `LLMProvider` ABC + `LLMResponseTruncated` (`:7`); `claude.py` — `ClaudeProvider`, model `claude-sonnet-4-6`, JSON max tokens 16384 with one doubled retry (`:15-29`); `minimax.py` — `MiniMaxProvider`, model `MiniMax-M2`; `mock.py` — canned response for tests. |
| `data/us_zip_lat_lon.csv` | — | ZIP centroid table for the freight distance calc (loaded at `routes.py:409`). |
| `assets/` | — | Company logo for the PDF. |

### `src/app/` — the Flask web app

| File | Lines | Owns |
|---|---|---|
| `__init__.py` | 58 | App factory (`create_app`, `:21`); global auth gate: every endpoint except `static`, `main.healthz`, and `auth.*` requires login, with an `HX-Redirect` for htmx requests (`:43-52`). |
| `config.py` | 29 | `Config`: SQLite default at `instance/allenedwards.db`, `QUOTE_ARTIFACT_DIR` (retained PDFs live outside the deploy-replaced tree), cookie/magic-link TTLs, `APP_URL`. |
| `models.py` | 336 | All ORM models — see §4. |
| `routes.py` | 2881 | The main blueprint: quote editor (detail/line items/customer/totals/status), freight auto-calc, spec-driven re-pricing, revisions/duplicates, PDF preview & send, pricing/catalog/product-type/shipping admin, health check. See §5. |
| `quotes.py` | 211 | Queue dashboard (`/quotes/`), status tabs and search, NEW-count badge, blank-quote create, 15-min review lock claim/release (`:19`, `:183-211`). |
| `customers.py` | 339 | Customer CRUD + `auto_match` scoring (`:19`) + JSON match endpoint (`/customers/api/match`, `:94`). |
| `admin_routes.py` | 133 | User management (`/admin/users`), classifier audit trail (`/admin/rejected-emails`, `:81`), failed-intake queue with resolve action (`/admin/failed-intakes`, `:98-133`). |
| `auth_routes.py` | 211 | Password login, magic-link flow with cross-device polling (`:84-163`), first-run bootstrap user (`:59`), set-password, logout. |
| `email_service.py` | 92 | `email_delivery_enabled()` (`:14`), `send_as_user_enabled()` (`:19`), `resolve_quote_sender()` (`:24`), magic-link email sender (`:54`). |
| `quote_numbers.py` | 57 | **The** canonical quote-number generator (`:30`); fiscal prefix `1YY` (2026 → `126`), sequence = numeric max over `^prefix-(\d+)$` (revision suffixes excluded by design — the 2026-08-13 outage fix, task 377). |
| `extensions.py` | 16 | `db` (SQLAlchemy) + `login_manager` singletons. |
| `wsgi.py` | 15 | `app = create_app()` for gunicorn. |
| `templates/` | — | Jinja + htmx: `quotes/` (queue, detail, editor partials, send form), `partials/` (admin tables), `admin/`, `auth/`, `customers/`, `pricing_admin.html`, `layout.html`, `dashboard.html`. |

Other top-level dirs: `migrations/` (Alembic, 25 versions through
`20260813_0001_add_failed_intake_table.py`), `deploy/` (§10), `tests/` (§11),
`scripts/` (`seed_product_catalog.py`, `build_macos.sh`), `docs/research/` (prior
investigation docs).

## 4. Data model

All in `src/app/models.py`. SQLite in prod. **Line numbers here supersede the stale
ones in `docs/research/current-backend-endstate-map.md` (task 365)** — two tables
(`ProcessedInboundEmail`, `FailedIntake`) were added after that doc was written.

| Model | models.py | Role |
|---|---|---|
| `QuoteStatus` | `:18` | Enum: `NEW, IN_REVIEW, NEEDS_PRICING, READY, SENT, ARCHIVED, REPLACED`. No status past SENT. |
| `User` | `:32` | Reviewer accounts; password hash + magic-link token. |
| `AuthToken` | `:53` | Magic-link tokens for the cross-device polling login flow. |
| `Customer` | `:74` | Company + `discount_pct` (stored, **not applied anywhere in pricing** — verified: no pricing code reads it) + notes. |
| `Contact` | `:89` | Person on a customer (name/email/phone). |
| `ShipToAddress` | `:101` | Stored customer address; `human_confirmed` (`:115`) gates trust — RFQ-inferred addresses stay unconfirmed until a human confirms in the editor (`routes.py:1732`). |
| `Quote` | `:120` | The central object. `quote_number` (unique), nullable `customer_id` FK, `status`, reviewer lock fields, contact/PO fields, `ship_to_json` + `bill_to_json` (canonical 8-key dicts), `tax_amount`, soft delete (`deleted_at`), revision links (`replaces_quote_id` unique FK + `revision_number`), `source_email_id`. |
| `ProcessedInboundEmail` | `:172` | **Durable message-level idempotency claim** for the monitor — unique `source_email_id`. Separate from `Quote.source_email_id` because one email may create several quotes (docstring `:173-177`). Added in migration `20260811_0002`. |
| `QuoteLineItem` | `:185` | Priced line: `product_type` (**bare string**, no FK), `description`, `quantity`, `unit_price`, `line_total`, `specs_json`, `part_number` (single identifier post-refactor), `sort_order`. |
| `QuoteAttachment` | `:202` | Original RFQ attachments, bytes inline in the DB (`content_bytes`); >15 MB stored as metadata only (`db_writer.py:40`, `:394-407`). |
| `QuoteVersion` | `:216` | **Immutable send-time record**: `pdf_path` (read-only archived file), `artifact_status` (`"retained"` vs `"missing"` for pre-archive rows, `:223-226`), `line_items_snapshot` JSON (`:227`), `sent_at/sent_by/sent_to`. Created only in `quote_send` (`routes.py:2848`). |
| `PricingTable` | `:235` | Editable price rows: `product_type` + `key_fields` JSON + `price`. Overlays the hardcoded defaults at pricing time (`pricing.py:344-407`). |
| `ProductType` | `:245` | Editable type list (`name` slug, `display_label`, `sort_order`, `is_active`). Drives both the decode prompt (`parser.py:912`) and the editor dropdown (`routes.py:354`). |
| `ProductCatalog` | `:255` | Product list. `part_number` **nullable** (name-only products are first-class), `product_type` nullable slug, `is_active`. **No FK to anything**; the surrogate `id` is the rename-safe key. |
| `ShippingConfig` | `:271` | Freight params: `default_rate_per_lb_mile` (0.0006), `default_length_ft` (10), `origin_zip_codes_json` (default `["74103"]` = Tulsa), `rate_overrides_json` per product type. Singleton row id=1 (`routes.py:452`). |
| `RejectedEmail` | `:282` | Classifier audit trail (written on non-RFQ, `monitor.py:367`). |
| `FailedIntake` | `:294` | **Never-fail-silently record** (Chip's 2026-08-13 report, I130): any exception in the processing path quarantines the message here with `failure_stage` ∈ {`parse_truncated`, `db_write`, `processing`} (`monitor.py:563`), visible at `/admin/failed-intakes` with a resolve action. Migration `20260813_0001`. |
| `AuditLog` | `:327` | Per-quote actions: `created_from_email`, `created_manually`, `sent`, `deleted`, `revised_by/from`, `duplicated_from/to`. |

### Product-identity reality (debt the engine inherits)

Everything is loosely coupled by **bare lowercase strings, not foreign keys**:
`QuoteLineItem.product_type`, `PricingTable.product_type`,
`ProductCatalog.product_type`, and `ProductType.name` are four independent string
columns with no referential integrity. `ProductCatalog` is never referenced by a line
item — picking a catalog row in the editor copies text. See
`docs/research/product-data-model-ground-truth.md` for the full analysis; it remains
accurate on this point.

### Quote lifecycle

- Monitor writes `NEW`, or `NEEDS_PRICING` when any line is unpriced/$0
  (`db_writer.py:364-371`).
- Opening a quote auto-claims it: sets `reviewed_by` and moves `NEW → IN_REVIEW`
  (`routes.py:1485-1489`). Review locks time out after 15 minutes (`quotes.py:19`).
- `NEEDS_PRICING` is auto-synced from line state on every edit
  (`_sync_quote_pricing_status`, `routes.py:1464`): TBD markers or unpriced lines
  force it; fixing them returns the quote to `IN_REVIEW`.
- `READY` is settable by hand only (`routes.py:1756-1763`); nothing automates it.
- `SENT` is set only by `quote_send` (`routes.py:2844`).
- Revisions: `quote_revise` (`routes.py:1560`) creates `<root-number>-R<n>`, copies
  lines, marks the source `REPLACED`. The chain is single-successor
  (`replaces_quote_id` is unique).

## 5. Web routes & the quote editor

All app routes require login except `/healthz` and `/auth/*` (`app/__init__.py:43`).
The UI is server-rendered Jinja with htmx partial swaps.

### Queue & quote CRUD

| Route | routes/quotes.py | Notes |
|---|---|---|
| `GET /quotes/` | `quotes.py:108` | Queue with status tabs, search, NEW badge; excludes deleted and REPLACED. |
| `POST /quotes/` | `quotes.py:165` | Blank manual quote (retries once on number collision). |
| `GET /quotes/<id>` | `routes.py:1476` | Editor page; auto-claims review; hydrates ship-to from customer default if blank (`:1295`). |
| `POST /quotes/<id>/delete` | `routes.py:1517` | Soft delete (`deleted_at`). |
| `POST /quotes/<id>/revise` | `routes.py:1560` | Linked revision, source → REPLACED. |
| `POST /quotes/<id>/duplicate` | `routes.py:1632` | Copy to another customer; refreshes auto freight. |
| `POST /quotes/<id>/meta` / `/customer` / `/status` / `/totals` | `routes.py:1710/:1723/:1751/:1775` | Section autosave endpoints, each re-rendering its own partial. |
| `POST /quotes/<id>/confirm-ship-to` | `routes.py:1732` | Sets `ShipToAddress.human_confirmed=True`. |

### Line items & spec-driven re-pricing

| Route | routes.py | Notes |
|---|---|---|
| `POST .../line-items/add` | `:1868` | Zero-price non-shipping adds are flagged `manual_no_charge` (deliberate free line, insight I95). |
| `POST .../line-items/<id>/calc-total` | `:1913` | Live preview with bundle/pallet rounding (no commit). |
| `POST .../line-items/<id>/update` | `:1965` | The main autosave target. Regenerates part numbers from specs unless a human typed one (`part_number_override`); applies bundle/pallet rounding (`original_qty` retained in specs); then `_apply_spec_driven_pricing` (`:1004`). |
| `POST .../line-items/<id>/delete`, `/move` | `:2196`, `:2210` | |
| `GET /api/product-catalog/search`, `/lookup/<pn>` | `:2144`, `:2175` | Catalog typeahead for the editor. |

Re-pricing precedence (task 329, `_apply_spec_driven_pricing` docstring
`routes.py:1013-1024`): (1) a human-typed unit price wins and sets a sticky
`price_override`; (2) `manual_no_charge` lines are never auto-priced; (3) otherwise
a changed pricing spec re-runs pricing (`_auto_pricing_from_specs`, `:910`) and
persists price basis; (4) if specs are too incomplete to price, the line is flagged
`price_stale` rather than guessed.

### Editor autosave (htmx) — post-T386 behavior

Templates: `src/app/templates/quotes/_line_items.html`, `_quote_fields.html`,
`_customer_info.html`. Each section is a form that autosaves via a shared JS pattern:
`focusout` → 200 ms debounce → submit only if focus left the whole card; `focusin`
cancels; `htmx:beforeRequest` clears timers to prevent double-submits
(`_line_items.html:196-236`). Quantity/unit-price fields additionally fire a
`calc-total` preview on `input changed delay:300ms` (`_line_items.html:44-48`).
The T386 Chip-reported bug (Save reverting an edit — an autosave/re-render race
against a detached form) is fixed and covered by browser-level Playwright regression
tests (see §11). Stale-form protection also exists server-side: the update route
compares against a `unit_price_baseline` hidden field so a pre-recalc form resubmit
can't silently revert a price or masquerade as a human override
(`routes.py:1990-2002`).

### Freight auto-calculation (web-side only)

The monitor/pricing path never prices freight (`pricing.py:1546` sets
`shipping_amount=None`). Freight is computed in the web app:

- `_shipping_breakdown` (`routes.py:532`): needs a **domestic** ship-to with a valid
  ZIP (`ship_to.py:70`; foreign postcodes are refused because they collide with US
  ZIPs); distance = haversine from nearest configured origin ZIP centroid
  (`:431-439`, data `src/allenedwards/data/us_zip_lat_lon.csv`); weight = steel
  formula over line specs (`_steel_weight_for_item`, `:514`); cost = weight × miles ×
  rate (per-type overrides supported).
- `_apply_auto_shipping_line_item` (`:599`) maintains a single `shipping`-type line
  item: auto lines refresh on every relevant edit; a manual typed freight figure sets
  `manual_override` and always wins (D12); a stale auto line is dropped when the
  ship-to disappears.
- Shipping lines are excluded from the product subtotal and shown separately
  (`_quote_totals`, `:1140`).

### PDF, send, and the immutable version record

- `GET /quotes/<id>/preview-pdf` (`routes.py:2675`) renders inline; unpriced quotes
  get a "NEEDS PRICING — NOT FOR CUSTOMER SEND" banner (`:2611`).
- `POST /quotes/<id>/send` (`routes.py:2707`) — the **only** send path:
  1. Gates: `EMAIL_DELIVERY_ENABLED` (`:2713`), needs-pricing check (`:2722`),
     `SEND_EMAIL_ALLOWLIST` recipient allowlist (`:2738`).
  2. Sender resolution: `O365_SEND_AS_USER` sends as the logged-in user's mailbox
     (same-domain + client-credentials only), else the shared AEResponder mailbox
     (`email_service.resolve_quote_sender`, called at `:2776`).
  3. **Before** the external send, the version number, line-items snapshot, and the
     read-only archived PDF are reserved (`:2805-2807`); the archive uses an
     `os.link` publish so a duplicate version can never overwrite an existing record
     (`_archive_sent_quote_pdf`, `:2645-2672`). On send failure the archive is
     removed (`:2818-2819`).
  4. On success: optional courtesy copy in Drafts (failure ignored, `:2830-2840`),
     `status=SENT`, `QuoteVersion(artifact_status="retained")` row (`:2848-2858`),
     audit row.

### Admin screens

`/admin/pricing` (tabs: shipping config, pricing tables, product catalog, product
types — `routes.py:2234-2552`; pricing edits clear the engine's snapshot cache
`:2266-2271`), `/admin/users`, `/admin/rejected-emails`, `/admin/failed-intakes`
(`admin_routes.py`). Customers CRUD at `/customers/` (`customers.py`).

## 6. Pipeline stages end-to-end

```
[O365 (default) or Gmail inbox]                    EMAIL_PROVIDER, cli.py:503
      │  poll every 5 min (systemd aedwards-monitor; interruptible wait, monitor.py:167)
      ▼
InboxMonitor.run_once()              monitor.py:169
      │  watermark advance :180 · dedup: state file + DB claim check :182-194
      ▼
classify_rfq(subject, body[:500])    parser.py:1271  LLM gate, biased to RFQ=true
      │  non-RFQ → RejectedEmail row (monitor.py:367) + finalize. DONE.
      ▼ RFQ
fetch attachments (O365 only)        monitor.py:235  → MIME-bridged into a temp .eml
parse_rfq_multi(eml, provider)       parser.py:1146  LLM decode → [ParsedRFQ]
      │  PDF/XLSX/CSV text extraction with size/row caps  parser.py:374/:463/:547
      │  ship-to only if body designates a destination    parser.py:1051-1110
      │  metric dims converted, never defaulted           units.py (task 330)
      ▼
generate_quote(rfq, number)          pricing.py:1422  deterministic pricing
      │  number from app.quote_numbers under app ctx      monitor.py:504-517
      │  (timestamp fallback only when DB writes off      monitor.py:602)
      ▼
   ┌──────────── two independent, env-gated sinks ────────────────────────┐
   │ ENABLE_DB_WRITES (default OFF)     ENABLE_OUTLOOK_DRAFTS (default ON)│
   │ _write_to_db()  monitor.py:309     drafts loop  monitor.py:270-304   │
   │  → one atomic txn: claim + all     → priced: PDF draft in Outlook    │
   │    quotes from the email           → $0 quote: [NEEDS PRICING] memo  │
   │  → Quote+lines+attachments+audit     draft, no PDF (:284-289, :527)  │
   │  → status NEW / NEEDS_PRICING                                       │
   └──────────────────────────────┬───────────────────────────────────────┘
                                  ▼
      ANY exception on the way → FailedIntake quarantine row + optional
      sender acknowledgment (monitor.py:199-212, :397-502). Never silent.
                                  ▼
                    *** AUTOMATED PIPELINE STOPS HERE ***
                                  ▼
              Human in the web app: queue → edit → Send
              quote_send()  routes.py:2707  → SENT + retained QuoteVersion
```

Stage notes verified in code:

- **Intake is email-only.** No fax, phone, web form, or API intake exists.
- **Classification** biases to false positives: provider errors and low-confidence
  non-RFQ verdicts (<0.5) are accepted as RFQs (`parser.py:1287-1303`).
- **Multi-quote emails**: one email can decode to several `ParsedRFQ`s (different
  ship-tos/project lines); numbers become `base-01`, `base-02`… (`monitor.py:254-257`).
- **Attachments**: fetched via Graph and re-attached as MIME parts, including
  embedded `message/rfc822` emails (`monitor.py:727-749`); the parser extracts
  bounded text from PDF (10 pages / 30k chars), XLSX (200 rows/sheet, noise-sheet
  drop for wide asset dumps, `parser.py:69-77`), and CSV. Gmail provider fetches no
  attachments.
- **Idempotency** is two-layer: the local state file (processed IDs + watermark,
  atomically fsync-replaced, `monitor.py:83-102`) plus, when DB writes are on, the
  `ProcessedInboundEmail` unique claim committed **in the same transaction** as the
  quote rows (`db_writer.py:344-353`, `monitor.py:322-334`). A state-file entry with
  no matching DB claim is retried (`monitor.py:182-193`); a replayed claim raises
  `InboundEmailAlreadyProcessed` and the message is skipped (`monitor.py:265-268`).
- **Failed intake / never-fail-silently**: every per-message exception is caught,
  logged loudly, and recorded as a `FailedIntake` row; recording itself is guarded so
  bookkeeping can't crash the loop (`monitor.py:397-444`). Optional sender
  acknowledgment is opt-in (`ENABLE_FAILURE_ACK`) with loop-avoidance guards
  (own mailbox, noreply addresses, skip-domains — `monitor.py:487-502`).

## 7. Pricing model

Formula + table lookups; **the 80/20 boundary**: commodity sleeves (~80% of volume,
per the 2026-08-10 onsite) price fully automatically; everything unpriceable becomes
an explicit $0 "Pricing TBD, contact sales" line (`_tbd_line_item`,
`pricing.py:980`) that forces `NEEDS_PRICING` — never silently dropped, never
auto-sendable.

- **Sleeves/oversleeves** (per-pound formula, `pricing.py:425-486`):
  `weight_per_ft = 10.69 × ((ID + wt) × wt) / 2`; unit price =
  `price_per_lb(wt_tier, grade) × weight_per_ft × length`; milling +$35 / painting
  +$45 flat (`pricing_catalog.py:61`). Defaults when the RFQ omits a spec: grade
  GR50, wall 3/8", length 10 ft (6 ft girth weld) — every default is recorded as a
  note on the line (`_apply_item_defaults`, `:863`). Oversleeve ID = pipe OD + 2×wt
  (`:158`). Nominal pipe sizes map to actual OD (`NOMINAL_OD_MAP`, `:106`).
- **Bundle rule**: sleeves ≤24" OD × 10 ft sell in bundles of 5; quantities round up
  with a note (`:59-62`, `:489-513`, `:778-808`). Total-footage requests ("150 LF")
  are deterministically normalized to 10-ft pieces even when the LLM mis-mapped them
  (`_normalize_sleeve_footage`, `:675-723`).
- **Girth weld**: per-set price by diameter tier (`:516-527`; defaults 2-19"=$300,
  20-31"=$500, 32-44"=$800).
- **Bags**: per-bag price by pipe-size tier with pallet round-up (`:1249-1284`);
  empty-bags + on-site-fill requests are split into two lines, fill deliberately
  unpriced without a weight basis (`parser.py:796-853`).
- **Backing strip** (accessory): billed **only** by the $400 pack of 10 (Chip,
  2026-07-28); linear-foot requests convert at an **interim, inferred** 5 ft/strip
  with the conversion always spelled out in a note ("confirm with Chip")
  (`pricing.py:726-775`, `pricing_catalog.py:51-54`).
- **Composite (OmegaWrap)**: flat per-roll rates by variant keyword (carbon $680 /
  eglass $470 / magnum $390), defaulting to carbon (`:930-967`, `:1306-1326`). No
  "how much to buy" calculator exists.
- **Compression / accessories / services**: flat keyword-matched rates from
  `DEFAULT_OTHER_PRICING` (`pricing_catalog.py:34-59`); unmatched → TBD.
- **Live overrides**: all of the above defaults are overlaid by `PricingTable` rows
  edited at `/admin/pricing`, snapshot-cached for 5 s (`pricing.py:301-422`); admin
  edits clear the cache immediately in-process (`routes.py:2266`).
- **Note rows**: every generated quote appends non-priced note lines — shipping
  instruction ("*Ship LTL Prepay & Add" or the RFQ's "Ship:" line), RFQ contact,
  quantity warnings, and a "*Defaults applied:" summary (`pricing.py:1449-1502`).
  The customer PDF prints only allowlisted note clauses (`line_notes.py`).
- **Customer discount**: `Customer.discount_pct` is stored and shown in match
  results but is **not applied by any pricing path** (verified — no reads outside
  `customers.py`/templates). Customer pricing tiers remain policy work for the engine.

## 8. Terminology in effect

Post terminology refactor (tasks 358/360/362, migrations `20260810_0002` and
`20260811_0001`):

- **Part Number** — the single line-item identifier (`QuoteLineItem.part_number`).
  The legacy separate `sku` column is gone; the pipeline folds a catalog-match SKU
  into `part_number` when pricing didn't generate one (`db_writer.py:441-443`).
  Pricing dataclasses still carry an internal `sku` field for catalog matches
  (`pricing.py:242`), but it lands in `part_number`.
- **Type** — the product-type slug (`QuoteLineItem.product_type`), sourced from the
  **editable `ProductType` table**. The decode prompt's valid-type enum is built
  live from that table at parse time (`parser.py:896-962`), so a type Chip adds in
  the UI (he added "composite" live) reaches the LLM without a deploy. Fallback
  defaults when no DB context: sleeve, bag, girth_weld, compression, accessory,
  service, shipping, composite (`parser.py:900-909`, mirrored `routes.py:77-86`).
- **Composite** — the type for OmegaWrap products (formerly inconsistent), reconciled
  by migration `20260811_0001`.
- "Oversleeve" is a sleeve variant, not a type: the parser maps `oversleeve` →
  `sleeve` (`parser.py:761`), though the pricing layer retains an oversleeve pricing
  path for OD math (`pricing.py:1144`).

## 9. Env flags & runtime gates

Enumerated from code (grep of `os.environ`/`os.getenv` over `src/`, 2026-08-19).

### Behavior gates

| Flag | Default | Effect | Read at |
|---|---|---|---|
| `ENABLE_DB_WRITES` | **off** (truthy: `1/true/yes`) | Monitor writes quotes/rejections/failed-intakes to the DB and uses the canonical quote-number generator. | `cli.py:565` |
| `ENABLE_OUTLOOK_DRAFTS` | **on** (falsy: `0/false/no`) | Monitor drops PDF/review drafts in Outlook; also gates the post-send courtesy draft copy. | `cli.py:566`, `routes.py:2796` |
| `EMAIL_DELIVERY_ENABLED` | **on** (falsy: `0/false/no`) | Master outbound-email kill switch for the web app: quote send and magic-link delivery. Staging sets `false`. | `email_service.py:14-16` (used `routes.py:2713`, `email_service.py:56`) |
| `ENABLE_FAILURE_ACK` | **off** | Failed-intake acknowledgment email back to the sender. | `cli.py:568` |
| `ACK_SKIP_DOMAINS` | empty | Comma list of sender domains never acknowledged. | `cli.py:569` |
| `SEND_EMAIL_ALLOWLIST` | empty = allow all | When set, quote send only permits listed recipient addresses. | `routes.py:2738` |
| `O365_SEND_AS_USER` | **off** | Web send goes out as the logged-in user's mailbox (same domain + client-credentials auth only), else shared sender. | `email_service.py:19-21` |
| `EMAIL_PROVIDER` | `o365` | `o365` or `gmail` inbox provider for the monitor. | `cli.py:503` |
| `ENABLE_MONITOR` | `true` | **Deploy-script gate only** (not read by app code): `false` stops/disables `aedwards-monitor` and skips mailbox creds. | `deploy/deploy.sh:37,177-190` |

### Credentials / providers

| Flag | Notes |
|---|---|
| `O365_EMAIL`, `O365_CLIENT_SECRET`+`O365_TENANT_ID` (preferred) or `O365_PASSWORD` (ROPC), `O365_CLIENT_ID` (defaults to a Microsoft public client id), `O365_SCOPES` | Graph auth (`cli.py:509-529`, `outlook.py:87-125`, `routes.py:2758-2764`). |
| `GMAIL_EMAIL` + (`GMAIL_SERVICE_ACCOUNT_FILE` or `GMAIL_CLIENT_ID`/`GMAIL_CLIENT_SECRET`/`GMAIL_REFRESH_TOKEN`), `GMAIL_SCOPES` | Gmail provider (`cli.py:530-561`). |
| `LLM_PROVIDER` (`mock`/`claude`/`minimax`), `ANTHROPIC_API_KEY`, `CLAUDE_JSON_MAX_TOKENS` (default 16384), `MINIMAX_API_KEY`, `MINIMAX_BASE_URL` | Provider selection: explicit `LLM_PROVIDER` wins; else Claude if `ANTHROPIC_API_KEY` set; else MiniMax (`cli.py:47-87`, `providers/claude.py:22-29`, `providers/minimax.py:45`). |

### Web app config

| Flag | Default | Read at |
|---|---|---|
| `DATABASE_URL` | `sqlite:///<repo>/instance/allenedwards.db` (prod today: `sqlite:////opt/aedwards/instance/allenedwards.db`; post-cutover: `postgresql://aedwards@/aedwards?host=/var/run/postgresql`). Both engines fully supported (task 397); cutover per `docs/runbooks/postgres-cutover.md` | `config.py:21`, `deploy/deploy_web.sh:50` |
| `SECRET_KEY` | `dev-secret-key` (deploy generates one if absent, `deploy_web.sh:228`) | `config.py:20` |
| `QUOTE_ARTIFACT_DIR` | `<repo>/instance/quote_versions` | `config.py:26` |
| `APP_URL` | unset (magic links fall back to `_external=True`) | `config.py:29` |
| `REMEMBER_COOKIE_DURATION_DAYS` / `MAGIC_LINK_TTL_SECONDS` | 30 / 1800 | `config.py:27-28` |

Env loading in the CLI: `~/.env` → shared project `.env` → worktree `.env`, never
overriding existing process env (`cli.py:21-44`).

**Production flag state (unverified from this repo):** what prod's
`/opt/aedwards/.env` actually sets (notably whether `ENABLE_DB_WRITES=true` and
`ENABLE_OUTLOOK_DRAFTS` are on) lives on the droplet, not in git. Canon
(allanedwards:D4, I101) and the running system's behavior indicate DB writes ON and
drafts OFF ("DB writes exclusively, no Outlook drafts going forward", D4
2026-04-08) — TODO confirm on the box before relying on it.

## 10. Infrastructure & deploy

### Production

- **Domain**: `https://quotes.allanedwards.io` → DigitalOcean droplet
  **157.230.227.28**. *Not hardcoded in the repo* — nginx `server_name` is injected
  at deploy time (`deploy/deploy_web.sh:47,158`; template
  `deploy/nginx-aedwards-web.conf`). Verified live 2026-08-19: DNS resolves to
  157.230.227.28 and `GET /healthz` returns `{"status":"ok"}`. Matches canon
  I45/I47 (DNS on GoDaddy for allanedwards.io; SSL via certbot).
- **Two systemd units**, both `User=aedwards`,
  `EnvironmentFile=/opt/aedwards/.env`:
  - `aedwards-monitor.service`: `/usr/local/bin/aedwards monitor --poll-minutes 5
    --state-file /opt/aedwards/.monitor_state.json --output-dir
    /opt/aedwards/monitor_output`, `Restart=always`, hardening
    (`NoNewPrivileges`, `ProtectSystem=full`, `ReadWritePaths=/opt/aedwards`).
  - `aedwards-web.service`: gunicorn 3 workers on `127.0.0.1:8000` behind nginx.
- **Deploy scripts** (both required for a full release):
  - `deploy/deploy.sh` (monitor): tars the source, pip-installs it into
    `/opt/aedwards/venv`, writes the `/usr/local/bin/aedwards` wrapper
    (`:157-161`), merges env, honors `ENABLE_MONITOR=false` by stopping/disabling
    the service.
  - `deploy/deploy_web.sh` (web): same install, renders nginx config (skipped if
    certbot already manages it, `:236`), **runs `alembic upgrade head`** (`:244`),
    restarts the service. When `EMAIL_DELIVERY_ENABLED=false` it **strips all
    mailbox credential lines from the host env** (`:220-223`).
  - `provision.sh` / `provision_do.sh`: first-time host setup.
- **Database**: prod currently runs the single SQLite file
  `/opt/aedwards/instance/allenedwards.db` (single-writer). **PostgreSQL is now a
  fully supported target** (task 397, per decision D65: move before the first
  concurrent writer): the alembic chain runs clean on empty PostgreSQL, the whole
  test suite passes against it (`TEST_DATABASE_URL`, §11), and
  `scripts/migrate_sqlite_to_postgres.py` copies a live SQLite DB verbatim with
  self-verification (row counts, BLOB sha256, status/JSON spot checks).
  `deploy/provision_pg.sh` provisions droplet-local PostgreSQL (role/db + nightly
  `pg_dump` cron, 7-day retention). The prod cutover is a gated runbook —
  `docs/runbooks/postgres-cutover.md` (stop monitor → final sync → switch
  `DATABASE_URL` → verify → rollback path) — rehearsed on staging first. Until it
  runs: daily 2 AM sqlite3 backups with 7-day retention under `/opt/aedwards/`
  per canon I47 — **unverified in repo** (the cron lives on the host).
- **Retained quote PDFs** live in `QUOTE_ARTIFACT_DIR` beside the DB, deliberately
  outside the deploy-replaced source tree (`config.py:23-26`).

### Staging (task 385)

Per `deploy/README.md` and canon I133: isolated staging at
`https://staging.quotes.vectorforgeinteractive.com`, separate DO droplet
**134.122.29.15**, own SQLite DB, monitor disabled, no mailbox credentials,
`EMAIL_DELIVERY_ENABLED=false` + `ENABLE_MONITOR=false`. DNS for
`vectorforgeinteractive.com` is authoritatively on **AWS Route53** (the DO DNS zone
for the same domain exists but is not delegated — a decoy; canon I134). The README
includes the post-deploy isolation verification checklist — run it after every
staging deploy. Post-deploy test-quote policy (prod): run test RFQs, verify each,
delete the test records.

### Email / O365 integration & credentials model

- Shared pipeline mailbox: **AEResponder@allanedwards.com** (canon I47; the address
  itself is env config, not code).
- Graph auth prefers **client-credentials** (`O365_CLIENT_SECRET` + tenant;
  `outlook.py:92-106`) and falls back to **ROPC username/password**
  (`outlook.py:108-125`). ROPC can only authenticate the shared mailbox itself,
  which is why send-as-user requires client credentials
  (`email_service.py:35-43`).
- Two sender identities: the monitor's drafts always belong to the shared mailbox;
  the web send path can send as the logged-in rep (`O365_SEND_AS_USER`, §5).
- All secrets live in `/opt/aedwards/.env` on the hosts; none are committed.

## 11. Testing

- Layout: flat `tests/` directory, 32 `test_*.py` files. `tests/conftest.py` only
  pins `sys.path` to the worktree `src/` so tests never import a stale installed
  copy. Individual test files build their own fixtures (in-memory Flask apps, mock
  LLM provider `providers/mock.py`, `fixtures_product_catalog_prod.json`).
- Run: `pytest` from the repo root (`[tool.pytest.ini_options] testpaths=["tests"]`,
  `pyproject.toml:63-64`). Default DB is SQLite (fast); set `TEST_DATABASE_URL`
  to a PostgreSQL maintenance URL (role needs CREATEDB) to run the same suite on
  PostgreSQL — each test gets its own disposable database via the shared
  `db_url` fixture (`tests/conftest.py`). `tests/test_postgres_support.py` holds
  the CP-1 acceptance tests (alembic-on-empty-PG, SQLite→PG copy round-trip) and
  skips unless `TEST_DATABASE_URL` is set. Migration tests that exercise SQLite
  internals on purpose stay on SQLite regardless. Dev deps: `pip install -e '.[dev]'`; browser-level
  regression tests additionally need
  `pip install playwright && playwright install chromium-headless-shell`
  (`pyproject.toml:33-41`) — these cover the T386 editor autosave race.
- Coverage spans the parser (incl. metric decode, dynamic type prompt), pricing,
  monitor attachments, db_writer, quote numbers/collisions, editor save race,
  failed intake, email delivery gate, PDFs, auth, customers, end-to-end.
- **Known defect — do not chase (task 382, still `pending`):**
  `tests/test_send_as_user.py` has an isolation defect — a stale `quote_versions`
  archive file causes `FileExistsError`, failing ~5 tests on full/isolated runs and
  masking real failures. If a full run fails there, it is almost certainly this,
  not your change.

## 12. Known constraints & hazards

Agents building engine features MUST respect these:

1. **At-least-once delivery semantics remain the design assumption.** The historical
   monitor SIGTERM bug (I111 / task 340) is **fixed in code** — the task-365 map's
   "OPEN" status is stale. Today: SIGINT/SIGTERM handlers are installed
   (`monitor.py:155-157`), the poll wait is an interruptible `Event.wait`
   (`:167`), the state file is written atomically with fsync+rename (`:83-102`),
   and DB writes are protected by the `ProcessedInboundEmail` claim committed
   atomically with the quote rows (`db_writer.py:344-353`). Residual hazards:
   - With `ENABLE_DB_WRITES` **off**, only the state file dedups; a kill between
     `state.add` (`monitor.py:194`) and draft creation can drop a draft (state says
     processed, no DB claim exists to trigger the retry path — the claim check
     returns `True` unconditionally in no-DB mode, `monitor.py:358-359`).
   - With DB writes on, a kill after commit but before draft creation/finalize can
     duplicate the *Outlook draft* on retry-adjacent paths, never the DB quote.
   - Any engine step with irreversible external effects (auto-send, order
     creation) must add its own idempotency claim in the same transaction as its
     side-effect record — key on `source_email_id` / the claim table pattern.
2. **Quote-number generation is centralized — keep it that way** (task 374/377).
   Only `app.quote_numbers.generate_quote_number` (`quote_numbers.py:30`) may mint
   numbers. Its regex deliberately ignores revision suffixes; the monitor surfaces
   residual UNIQUE collisions loudly and does not retry the batch
   (`monitor.py:335-351`); web-side callers retry once
   (`quotes.py:169-179`, `routes.py:1649-1707`). Do not reintroduce a second
   sequence reader.
3. **Product-identity debt** (§4): types and part numbers are strings without FKs;
   `ProductCatalog` is copy-on-pick. Engine features needing real product identity
   must either join on the surrogate `ProductCatalog.id` or fix the schema first —
   do not add a fourth independent string column.
4. **Never make an unpriced quote customer-visible.** `$0`/TBD lines force
   `NEEDS_PRICING`, block send (`routes.py:2722`), and banner the PDF (`:2611`).
   Preserve `manual_no_charge` (deliberate human zero) vs unpriced (defect)
   semantics (`routes.py:1424-1437`).
5. **Ship-to trust model**: parser only emits a ship-to when the body designates a
   destination; signature addresses are bill-to; freight terms are scrubbed from
   address fields (`parser.py:36-62`, `:1051-1110`). Stored addresses stay
   `human_confirmed=False` until a human confirms. Freight math is US-only. An
   invented ship-to silently prices freight to the wrong place — fail closed.
6. **Outbound email is gated everywhere** (`EMAIL_DELIVERY_ENABLED`,
   `SEND_EMAIL_ALLOWLIST`, `ENABLE_FAILURE_ACK` default-off with loop guards).
   New outbound paths must respect the same gates, and staging must stay
   credential-free (deploy strips them, `deploy_web.sh:220-223`).
7. **Single-writer SQLite in prod — for now** (§10). The Postgres evaluation is
   done: the codebase, migrations and tests are Postgres-clean (task 397) and the
   cutover runbook exists (`docs/runbooks/postgres-cutover.md`). Concurrent
   engine writers (orders, inventory, shop notifications) remain blocked until
   that cutover has actually run in prod.
8. **Send-time records are immutable.** `QuoteVersion` rows and their archived PDFs
   are write-once (`os.link` publish + chmod 444, `routes.py:2645-2672`). Never
   mutate them; build downstream objects *from* them (§13).
9. **Editor autosave races**: any new editor field must follow the baseline-field
   pattern (`unit_price_baseline`/`part_number_baseline`, `routes.py:1990-2011`)
   so stale htmx forms can't revert recalculated values.

## 13. What does NOT exist yet; engine attach points

Verified absent by full-tree search (2026-08-19; only `sort_order`/`po_number` and
CSS `minmax` match): **no `Order` model, no order status, no acceptance signal, no
pick list, no fulfillment, no shop notification, no inventory/stock/min-max/reorder,
no customer pricing tiers applied, no add-on options catalog, no composite
quantity calculator, no non-email intake, no auto-send.**

The engine's attach points in today's code:

- **The quote→order seam is the send event** — `quote_send`, `routes.py:2707`. The
  terminal transition today is `status=SENT` + `QuoteVersion` creation
  (`:2844-2858`). Nothing consumes a SENT quote; that is the empty socket.
- **Build orders from `QuoteVersion.line_items_snapshot`** (`models.py:227`,
  written at `routes.py:2806`/`2625-2642`), not from the mutable `Quote`: it is
  immutable, carries the exact priced lines with full `specs_json`, and already has
  `sent_to`/`sent_at`/`sent_by`.
- **Missing pieces the seam requires**: (a) an acceptance signal (reply parsing or
  explicit accept action — none exists); (b) an `Order` model; (c) an authorized
  quote→order transition; (d) everything downstream (pick list, shop ping,
  inventory decrement/reorder).
- **Intake generalization point**: everything downstream of the parser consumes
  `ParsedRFQ` (`parser.py:325`); new channels (fax/phone/web/API) should normalize
  into `ParsedRFQ` and reuse classify→parse→price unchanged. The monitor already
  bridges arbitrary content into the `.eml` path (`monitor.py:705`).
- **Trust-ramp auto-send point**: the mandatory human gate is the pair of checks at
  `routes.py:2713-2728` plus the absence of any send call in the monitor. A phased
  auto-send would replace/parameterize that gate; it inherits hazard §12.1.
- The full engine design (phases, data model, trust ramp) is in
  `docs/research/amazon-engine-end-to-end-design.md`; scoping context in
  `docs/research/current-backend-endstate-map.md` (task 365 — note its §5 monitor
  status and models.py line numbers are now stale) and
  `docs/research/onsite-2026-08-10-structured-notes.md`.

## 14. Suggested canon claims

Durable, load-bearing facts verified here that are not (or are wrongly) in canon —
for the PM to file, not filed by this task:

1. **I111 is fixed**: monitor handles SIGTERM via interruptible wait
   (`monitor.py:155-167`) and DB-claim idempotency (`db_writer.py:344`); task 340
   completed. Canon I111 and the task-365 map still describe it as open.
2. `ProcessedInboundEmail` (migration `20260811_0002`) is the message-level
   idempotency claim; engine features with irreversible effects must follow the
   same claim-in-transaction pattern.
3. `FailedIntake` (migration `20260813_0001`) + `/admin/failed-intakes` is the
   never-fail-silently guarantee; `ENABLE_FAILURE_ACK` (default off) gates sender
   acknowledgments.
4. `Customer.discount_pct` is stored but applied nowhere in pricing — customer
   tiers are still policy/engine work.
5. Freight is computed only web-side (`routes.py:532`), US-only, from ZIP
   centroids + steel weight; the monitor/pricing path never prices freight.
6. LLM providers in prod code: Claude `claude-sonnet-4-6` (JSON budget 16384,
   retry-on-truncation) or MiniMax `MiniMax-M2`; selection order
   `LLM_PROVIDER` → `ANTHROPIC_API_KEY` → MiniMax (`cli.py:47-60`).

---

*Written for task 394 by claude-dev-allanedwards-108. Every `file:line` was read in
the source at commit `4bb4109` on 2026-08-19. Facts marked unverified could not be
confirmed from the repo and must be checked on the host before an agent acts on
them.*
