# Quote percentage adjustment

A quote has an explicit `price_adjustment_pct` from -100 to 100, at most two
fractional digits. Zero is the default and removes the adjustment. No customer
default or adjustment-reason field is introduced.

`quote_line_item.unit_price` and `line_total` remain the editable base. The
shared `app.price_adjustments` functions apply the quote percentage AFTER a
manual price override. Positive percentages round each adjusted unit price to
cents (half up), then extend by quantity. Repeated saves never compound. The
editor shows base and customer prices together. Negative percentages produce
one rounded discount against the full merchandise subtotal. Freight and entered
tax are excluded. The entered tax is not automatically recomputed.

Customer PDFs display a negative percentage as `5% volume discount`. A positive
percentage produces exactly the same PDF text as ordinary pricing at the final
prices: no percentage, label, original subtotal, or reason. Existing provenance
notes still use `allenedwards.line_notes`' fail-closed allowlist. Email bodies
contain no generated pricing metadata. No new internal reason field is offered.

Revisions retain the percentage. Duplicating to another customer resets it to
zero and copies the stored base prices. Any nonzero percentage blocks Tier-2
auto-send; no trust-ramp settings are changed.

## Frozen orders and future outbound documents

`send_service.quote_line_items_snapshot` freezes the final customer unit and
extended prices. Positive adjustments strip pricing provenance from snapshot
specs, retaining physical specifications and allowlisted customer notes. It
never stores the base price or positive percentage beside a customer price.
Discounts are frozen as a negative `discount` row; fulfillment excludes that
financial row from picking.
The exact sent PDF and email remain retained under the existing version archive.

All future outbound documents (task 431, including invoices) must use the
accepted `QuoteVersion` prices and discount row, never the mutable
Quote's base prices. Quote PDF projection, editor totals, queue totals,
confidence totals, sent snapshots, and accepted order totals use the same price
calculation. There is no automatic repricing or invoice implementation here.

## Verification

`tests/test_price_adjustments.py` exercises real rendered PDF text, every-line
manual overrides, repeated saves, invalid percentages, duplicate/reset versus
revision/retain, send-time snapshots, and sent snapshots.
`tests/test_auto_send_tier2.py::test_percentage_requires_manual_send` checks both
signs against an otherwise eligible Tier-2 quote. All external send calls in
these tests are mocked.
