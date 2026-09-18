# Task 465: on-site fill verification

On-site fill is a separate manual product type. Its job-site label, city, state,
price source, and price date are columns on `quote_line_item`; joining `quote`
provides the customer and quote identity. This follows the existing-quote-database
seed specified in both CRM/RAG planning documents. No separate history service or
CRM/RAG implementation was introduced.

The quote editor requires a job site, city, state, diameter, fill weight, and a
positive manual price. Same-city/state recall ignores case and extra whitespace,
shows up to 20 recent quotes (including labeled drafts), and excludes the current
quote, deleted quotes, and replaced revisions. It never writes a price. Quote
revision preserves context; duplication to another customer clears the old job
location and price so the new job needs a local estimate.

## Migration and live schema

Migration `20260918_0001` follows `20260827_0002`. Deployed only to staging,
`134.122.29.15`, on 2026-09-18. The following is actual PostgreSQL output from
`psql -d aedwards -c '\d quote_line_item'`, followed by `alembic_version`:

```text
                                             Table "public.quote_line_item"
        Column        |            Type             | Collation | Nullable |                   Default
----------------------+-----------------------------+-----------+----------+---------------------------------------------
 id                   | integer                     |           | not null | nextval('quote_line_item_id_seq'::regclass)
 quote_id             | integer                     |           | not null |
 product_type         | character varying           |           | not null |
 description          | character varying           |           | not null |
 quantity             | numeric(12,2)               |           | not null |
 unit_price           | numeric(12,2)               |           | not null |
 line_total           | numeric(12,2)               |           | not null |
 specs_json           | json                        |           |          |
 part_number          | character varying           |           |          |
 sort_order           | integer                     |           | not null |
 on_site_label        | character varying           |           |          |
 on_site_city         | character varying           |           |          |
 on_site_state        | character varying           |           |          |
 on_site_price_source | text                        |           |          |
 on_site_priced_at    | timestamp without time zone |           |          |
Indexes:
    "quote_line_item_pkey" PRIMARY KEY, btree (id)
    "ix_quote_line_item_fill_location" btree (on_site_city, on_site_state)
    "ix_quote_line_item_product_type" btree (product_type)
    "ix_quote_line_item_quote_id" btree (quote_id)
Foreign-key constraints:
    "quote_line_item_quote_id_fkey" FOREIGN KEY (quote_id) REFERENCES quote(id)

  version_num
---------------
 20260918_0001
(1 row)

```

## Regression and browser evidence

- `test_existing_empty_bag_price_and_pdf_unchanged_after_fill_added` compares the
  **actual PDF bytes** of an existing empty-bag quote before and after adding an
  on-site-fill line to another quote. ReportLab invariant mode freezes its
  otherwise random PDF document ID and creation timestamp. It also compares the
  structured line snapshot and verifies normal diameter pricing, pallet rounding,
  part number, and empty-bag description.
- All 16 new cases were negative-tested after committing the implementation.
  Mutations individually broke generated text, validation, recall order,
  duplicate-price clearing, empty-bag PDF isolation, type conversion, and browser
  price clearing. Every case failed on an assertion; exact file backups were
  restored after each mutation. The byte-comparison test failed when a fill note
  was deliberately leaked onto the existing empty-bag description.
- All 16 new cases passed against real local PostgreSQL, including Chromium.
- Live staging Chromium walkthrough created test-only quotes
  `STAGING-465-HISTORY` (#213) and `STAGING-465-CURRENT` (#214), using only
  `devin@918.software` as their contact. History showed the prior $300 price while
  the new price input stayed blank. Entering $349.50, changing the fill weight to
  12,000 lb, and reloading retained the manual price and generated identifier.
  PDF preview returned 200 and contained the generated product and manual price;
  internal local-estimate source and recall UI text did not appear in the PDF.
  No emails were sent.
- Deployment verification: `EMAIL_DELIVERY_ENABLED=false`, `ENABLE_MONITOR=false`,
  web service active, monitor inactive. No auto-send dials were changed.

## Dashboard test timing investigation

The final repeat exposed `test_dashboard_recommendation_filter_tabs` asserting
`REC-BAD` count 0 while the asynchronous filter response was still pending. Its
preceding wait was for `REC-GOOD`, which was already present before clicking, so
that wait did not establish that a swap had occurred.

A `git archive origin/main` snapshot (b5409f8) was extracted to a separate temporary
directory and run with the same virtualenv and Chromium. **All 10 unmodified
module runs passed**; the spontaneous failure was not reproduced on main.
A controlled comparison then applied the same temporary pytest fixture to both
snapshots: wrap `app.view_functions['quotes.queue']` and sleep 0.2 seconds only
when `rec` is `recommended` or `not_recommended`, then call the original handler.
No application or test source in the main snapshot was edited.

- Unchanged main failed at its original line 199 (`assert 1 == 0`); the correct
  filtered HTTP 200 was logged only during teardown, after the assertion.
- The revised test passed with the identical 200 ms latency.
- Disabling the server's recommended filter caused an assertion failure in the
  revised test, proving it still checks filtering rather than merely waiting.

This establishes a latent timing dependency in the old assertion, not a
spontaneously reproduced main-branch failure. The fix awaits the actual required
behavior: the excluded row disappears. There is no evidence that the fill-field
layout changed dashboard application behavior.

## Text and freight safeguards

Plain hyphens replace em dashes in the **customer-facing generated product
description** (which reaches PDFs), and in the **editor-only product-type label
and recall heading**. The initial migration seed uses the corrected label and
still checks uniqueness by `name`; the already-deployed disposable staging seed
was aligned to that corrected label. Production has not been migrated.

On-site fill is excluded from automatic steel-weight freight even if an older
line being converted carries pipe-wall/length specs. Re-enabling steel freight
for that type caused the conversion test's zero-freight assertion to fail.

## Final check results

- Final full suite: **779 passed, 14 skipped**, 143.92 seconds.
- Final on-site-fill suite on PostgreSQL: **16 passed**, including Chromium.
- New-code Ruff checks and `git diff --check`: passed.
- Final staging browser check passed after the freight and plain-hyphen changes;
  live schema remains at `20260918_0001`, with delivery and monitoring disabled.
