# Duke–Butler bad quote investigation

## Conclusion

Quote **126-064** is not a priced quote.  It correctly preserved the requested
quantity (20) and broadly classified the request as a bag request, but it
emitted a single `TBD` line at **$0.00**.  It omitted the 16-inch product
identification and both requested price alternatives (empty and filled on
site).  The primary cause is deterministic: the monitor fetches PDF
attachments, but the parser never extracts PDF text before sending the email
body to the LLM.  The requested 16-inch value is in the attached RFP, not the
email body.

There is also an independent safety-gap: the database writer marks a $0/TBD
quote as `needs_pricing`, but the UI preview and send routes do not enforce that
status.  A reviewer can therefore render or send a zero-dollar PDF.  The
available evidence does not establish whether this PDF was only previewed or
actually sent externally.

## Evidence compared

### Customer input

The supplied RFP attachment, `data/investigations/duke-butler/26-58-sub-rfp-form.pdf`,
identifies the project as **Duke – Butler County Phase 2 & Line A000a**, says
the project installs 5.1 miles of **16-inch** pipe, and sets a proposal due
date of **2026-07-24**.  The original forwarded email (Gmail message
`19f7fcac8e3a2e7d`) supplies the operative request:

- quantity: 20;
- price the bags empty; and
- separately price filling them on site.

The forwarded email calls this “another bag request.”  It does **not** itself
state the 16-inch size; that is only inferable from the attached RFP's project
description.  The SharePoint link may have further scope, but it was not
available to this investigation.

### Quote 126-064

`data/investigations/duke-butler/bad-quote.pdf` has one line:

| Field | Rendered value | Assessment |
| --- | --- | --- |
| Item number | `TBD` | No catalog/part-number match was made. |
| Description | “Geotextile bag weights, quantity 20. Price as empty and include separate pricing to have them filled on site.” | Retains the quantity and the request as free text, but not a quoted product or two price options. |
| Quantity | 20 | Correct. |
| Unit price / total | $0.00 / $0.00 | Incorrect for a customer-facing quote; this is an unpriced fallback. |
| Product size | Not present | The 16-inch requirement was missed. |
| Empty vs. filled | One unsplit line | Both requested alternatives are missing. |
| Additional items | None | No material hallucinated; the issue is an incomplete, unpriced line rather than extra products. |

The PDF also lists only “Butler” as ship-to and expires on July 27, after the
customer's July 24 due date.  The latter is a secondary configuration/review
issue, not the pricing root cause.

## Root cause chain

1. **PDF content was not part of the LLM input — confirmed.**  The monitor
   obtains attachments and converts them to MIME parts
   ([`monitor.py:192`](../../src/allenedwards/monitor.py:192) and
   [`monitor.py:500`](../../src/allenedwards/monitor.py:500)).  The parser then
   extracts only `text/plain`, `text/html`, and embedded `message/rfc822`
   content ([`parser.py:257`](../../src/allenedwards/parser.py:257)).
   `application/pdf` has no extraction branch and contributes an empty string.
   The LLM prompt contains only the resulting body
   ([`parser.py:564`](../../src/allenedwards/parser.py:564)).  This explains the
   missing 16-inch field without attributing it to LLM reasoning.

2. **Bag catalog coverage exists — confirmed.**  The default catalog covers
   pipe sizes 14–19 as `GTW 16` at $80.77 per bag
   ([`pricing_catalog.py:24`](../../src/allenedwards/pricing_catalog.py:24)).
   A parsed `diameter=16` would match it.  This was not a missing 16-inch bag
   pricing range.

3. **The missing diameter turns the parsed item into the exact $0/TBD fallback
   seen in the PDF — confirmed.**  A bag with no diameter returns
   `_tbd_line_item` ([`pricing.py:1197`](../../src/allenedwards/pricing.py:1197)).
   This matches the rendered `TBD`, 20, $0.00 line.

4. **“Filled on site” is not a supported quote option — confirmed.**  There is
   a `bag_fill` rate of $0.02 **per pound**
   ([`pricing_catalog.py:33`](../../src/allenedwards/pricing_catalog.py:33)),
   but the parsed item model and bag-pricing branch have no fill method, bag
   weight, or linked empty/filled alternatives.  Treating “fill” as a generic
   accessory would incorrectly multiply a per-pound rate by the bag count.

5. **The intended monitor safety behavior catches $0, but the UI does not
   enforce it — confirmed.**  The monitor makes a manual-review draft instead
   of attaching a PDF when subtotal is zero
   ([`monitor.py:236`](../../src/allenedwards/monitor.py:236)), and DB writing
   assigns `needs_pricing` to $0/TBD quotes
   ([`db_writer.py:317`](../../src/allenedwards/db_writer.py:317)).  In contrast,
   the preview endpoint renders any active quote
   ([`routes.py:1673`](../../src/app/routes.py:1673)), and the send endpoint has
   no status or non-zero validation before `send_mail`
   ([`routes.py:1698`](../../src/app/routes.py:1698),
   [`routes.py:1764`](../../src/app/routes.py:1764)).

The existing PDF-attachment test only asserts that parsing returns a result; it
does not assert that PDF text is included in the LLM prompt
([`test_monitor_attachments.py:336`](../../tests/test_monitor_attachments.py:336)).

## Production-record and log check

The checked local SQLite database is a development database: it has three
quotes, the newest dated 2026-04-06, no `rejected_email` table, and no
126-064 record.  This session has no configured production `DATABASE_URL` or
O365 credentials.  Gmail contains Chip's forwarding message and the two
supplied PDFs, but no 126-064 message.  Therefore the production quote row,
audit trail, and rejected-email log could not be inspected; no claim is made
about whether the zero-dollar PDF left the review workflow.

## Can a corrected quote be generated from existing data?

**Not safely as an automatic final quote.**  The existing forwarded email and
stored RFP are sufficient to recover a 20-count, 16-inch, empty-bag line and
to place it in manual review.  They are insufficient to calculate the separate
on-site-fill option because the system has neither a bag-fill weight nor a
data model/calculation for that option.  The existing attachment can be used
to create a corrected **draft** after a human supplies the approved fill basis
(weight per bag and any site/service assumptions); it should not be regenerated
as an auto-send quote.

If the current pallet-rounding policy is used unchanged, a 20-bag request would
also be rounded to the 52-piece GTW 16 pallet
([`pricing.py:1213`](../../src/allenedwards/pricing.py:1213)).  That policy needs
explicit reviewer confirmation for this RFQ rather than silently changing the
requested quantity.

## Recommended fixes, ranked

1. **P0 — hard-stop customer send for `needs_pricing`, zero totals, or TBD
   lines.**  Permit an internal preview marked “NOT A QUOTE” if useful, but
   block the send route until a reviewer prices every material line and records
   the approval.  This closes the safety-net bypass regardless of extraction
   quality.

2. **P0 — extract and label PDF attachment text before classification/parsing.**
   Use a bounded PDF text extractor/OCR fallback, pass the text and source
   filename into the prompt, and retain the extracted text/a hash with the
   quote.  Treat inaccessible SharePoint links as missing scope rather than
   asking the model to infer their contents.

3. **P1 — model a bag request as two explicitly linked options.**  Require bag
   diameter, requested quantity, empty-unit price, fill basis (pounds per bag),
   fill-unit rate, and any site service charge.  The quote renderer should show
   “Option A: Empty” and “Option B: Filled on site,” not bury the distinction in
   a description.

4. **P1 — validate bag extraction and quantity transformations.**  For bags,
   require a diameter that matches a known catalog range before pricing; emit
   `needs_pricing` with a specific missing-field reason otherwise.  Require
   explicit reviewer approval before converting a requested quantity to a
   pallet quantity.

5. **P1 — add an end-to-end regression fixture.**  Use this email body plus
   `26-58-sub-rfp-form.pdf`; assert that 16-inch is extracted, `GTW 16` is
   selected for the empty option, a missing fill basis prevents finalization,
   and a `needs_pricing` quote cannot be sent.  Update the current attachment
   test to assert prompt content, not merely non-empty parser output.
