# Quote database schema reference (for audit SQL)

Reference for writing ad-hoc SQL against the production Postgres database
(quote-related tables). Written because audit agents have twice guessed wrong
against this schema (axon T5030/T5053): one used a JSONB operator on a JSON
column, one queried a `quote.sent_at` column that does not exist.

Source of truth: `src/app/models.py`. If this document and the models disagree,
the models win — update this file.

## Two things that WILL bite you

### 1. `specs_json` is `json`, not `jsonb`

`quote_line_item.specs_json` (and every other JSON column here: `ship_to_json`,
`bill_to_json`, `line_items_snapshot`, `components_json`, `parsed_rfq_json`) is
declared with SQLAlchemy `db.JSON`, which creates a plain Postgres **`json`**
column. JSONB-only operators fail on it:

```sql
-- FAILS: operator does not exist: json ? unknown
SELECT * FROM quote_line_item WHERE specs_json ? 'grade';
```

Use the text-extraction operator instead, which works on both `json` and `jsonb`:

```sql
-- key existence
SELECT * FROM quote_line_item WHERE specs_json ->> 'grade' IS NOT NULL;

-- value comparison
SELECT * FROM quote_line_item WHERE specs_json ->> 'grade' = 'X52';

-- nested access: -> returns json, ->> returns text
SELECT specs_json -> 'dims' ->> 'diameter' FROM quote_line_item;
```

If you genuinely need JSONB semantics (`?`, `?|`, `@>`, `jsonb_each`, ...),
cast: `specs_json::jsonb ? 'grade'`. Prefer `->>` for simple audits.

### 2. `quote` has no `sent_at` — send facts live on `quote_version`

A send creates an immutable `quote_version` row carrying `sent_at`, `sent_by`,
`sent_to`, the archived PDF path, and a frozen `line_items_snapshot`. The live
`quote` row only flips `status` to `'sent'`. To ask "when was this quote sent":

```sql
SELECT q.quote_number, qv.version_number, qv.sent_at, qv.sent_to
FROM quote q
JOIN quote_version qv ON qv.quote_id = q.id
ORDER BY q.id, qv.version_number;
```

Also note for audits:

- **Soft delete**: exclude `quote.deleted_at IS NOT NULL` rows unless auditing
  deletions.
- **Revisions**: a revised quote gets a NEW `quote` row whose
  `replaces_quote_id` points at the old one; the old row's status becomes
  `'replaced'`. Count distinct quotes accordingly.
- **`quote.status`** is the Postgres enum `quote_status`:
  `new | in_review | needs_pricing | ready | sent | archived | replaced`.

## Table: `quote` (`models.py` class `Quote`)

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | integer (PK) | no | |
| `quote_number` | varchar | no | unique, indexed |
| `customer_id` | integer FK → `customer.id` | yes | NULL until matched |
| `status` | enum `quote_status` | no | see values above |
| `reviewed_by` | integer FK → `user.id` | yes | |
| `review_started_at` | timestamp | yes | |
| `ready_override_at` | timestamp | yes | Mark Ready approval time |
| `ready_override_by` | integer FK → `user.id` | yes | |
| `ready_override_fingerprint` | varchar | yes | content hash the approval binds to |
| `project_name` | varchar | yes | |
| `notes_customer` | text | yes | |
| `notes_internal` | text | yes | |
| `email_message` | text | yes | |
| `price_adjustment_pct` | numeric(5,2) | no | default 0, per-quote only |
| `source_email_id` | varchar | yes | inbound provenance |
| `sender_email` / `sender_name` / `subject` | varchar | yes | inbound provenance |
| `customer_name_raw` | varchar | yes | as parsed from the RFQ |
| `contact_name` / `contact_email` / `contact_phone` | varchar | yes | |
| `po_number` | varchar | yes | |
| `ship_to_json` / `bill_to_json` | **json** | yes | address dicts |
| `tax_amount` | numeric(12,2) | no | default 0 |
| `deleted_at` | timestamp | yes | soft delete, indexed |
| `replaces_quote_id` | integer FK → `quote.id` | yes | unique — revision chain |
| `revision_number` | integer | no | default 0 |
| `created_at` / `updated_at` | timestamp | no | |

There is **no** `sent_at`, `sent_by`, `sent_to`, or `pdf_path` on `quote`.

## Table: `quote_version` (class `QuoteVersion`)

Immutable send-time record; created only when a quote is sent.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | integer (PK) | no | |
| `quote_id` | integer FK → `quote.id` | no | indexed |
| `version_number` | integer | no | per-quote sequence |
| `pdf_path` | varchar | no | archived customer-facing PDF |
| `artifact_status` | varchar | no | `'retained'` or `'missing'` (pre-archive rows) |
| `line_items_snapshot` | **json** | yes | frozen list of line dicts |
| `sent_at` | timestamp | yes | |
| `sent_by` | integer FK → `user.id` | yes | |
| `sent_to` | varchar | yes | recipient email |
| `tax_amount` | numeric(12,2) | yes | frozen; NULL on legacy sends |
| `email_body` / `email_subject` / `email_cc` | text | yes | exact outgoing text; NULL on historical versions |

## Table: `quote_line_item` (class `QuoteLineItem`)

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | integer (PK) | no | |
| `quote_id` | integer FK → `quote.id` | no | indexed |
| `product_type` | varchar | no | bare string, no FK; indexed |
| `description` | varchar | no | |
| `quantity` | numeric(12,2) | no | |
| `unit_price` | numeric(12,2) | no | |
| `line_total` | numeric(12,2) | no | |
| `specs_json` | **json** (NOT jsonb) | yes | pricing basis: diameter/wall/grade/length/etc. Older rows may be `{}` or lack keys (e.g. no saved `grade`) |
| `part_number` | varchar | yes | |
| `catalog_id` | integer FK → `product_catalog.id` | yes | historical rows NULL |
| `catalog_match_status` | varchar | no | default `'unchecked'`; indexed |
| `sort_order` | integer | no | default 0 |
| `on_site_label` / `on_site_city` / `on_site_state` | varchar | yes | on-site fill lines only |
| `on_site_price_source` | text | yes | |
| `on_site_priced_at` | timestamp | yes | |

## Table: `customer` (class `Customer`)

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | integer (PK) | no | |
| `company_name` | varchar | no | indexed |
| `discount_pct` | numeric(5,2) | no | default 0 |
| `notes` | text | yes | |
| `created_at` | timestamp | no | from TimestampMixin (no `updated_at`) |

Contact emails/phones are NOT on `customer` — join `contact`
(`customer_id`, `name`, `email`, `phone`). Shipping addresses are in
`ship_to_address` (`customer_id`, address fields, `is_default`,
`human_confirmed`).

## Related tables (briefly)

- `quote_attachment` — inbound files, immutable (`quote_id`, `filename`,
  `content_type`, `size_bytes`, `content_bytes`).
- `quote_source_snapshot` — immutable inbound-email provenance
  (`quote_id` unique, `original_body`, `normalized_body_text`,
  `parsed_rfq_json` json).
- `quote_reparse_attempt` — append-only operator re-parse trail
  (`quote_id` and/or `failed_intake_id`, `input_text`, `succeeded`,
  `parsed_rfq_json` json).
- `quote_confidence` — one row per quote (`score` numeric(4,3), six tri-state
  signal columns, `components_json` json).
- `acceptance_event` — acceptance record; binds to `quote_version_id`
  (unique), not to `quote`.
- `auto_send_claim` — one-shot auto-send idempotency claim per quote
  (`status`: claimed/blocked/failed/sent).
- `audit_log` — per-quote audit rows (`quote_id`, `action`, `user_id`,
  `details` json, `created_at`).
