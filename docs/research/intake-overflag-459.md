# Intake over-flagging / intake misses — root cause and fix (task 459)

**Date:** 2026-09-03
**Umbrella for:** T459 (John Galt "Checking in" over-flags), T437 (Sable intake
miss), T378 fold-in (revision mis-detection — already resolved).

## Symptom 1 — T459: legit RFQs rejected when the subject is generic

Chip (2026-09-02, first semi-automated sale): "Responder was flagging a few
from John Galt. As subject line was just 'checking in' and prospect just
responded with request. Had to override a few."

### Root cause (two stacked bugs, both proven from prod data)

1. **The classifier never saw the email content.** The monitor passed the raw
   Graph `body.content` — Outlook **HTML** — into `classify_rfq`, which
   snippets the FIRST 500 CHARACTERS. For every Outlook message those chars
   are the `<head><style>` CSS block. The classifier judged "FW: Checking in"
   plus CSS soup and rejected. Prod `rejected_email` rows 14–23 all say it
   outright: *"the body snippet contains only HTML/CSS styling metadata with
   no visible product request."* Older rows (11: "Rental Skid", 10: "mears",
   9: "southbow", 8: "Fwd: Duke - Butler") show the same fingerprint — this
   bug predates the John Galt thread and has been rejecting HTML-bodied,
   generic-subject emails all along.
2. **Rejects wrote no idempotency claim.** The reject path recorded a
   `RejectedEmail` row and finalized, but never wrote the
   `ProcessedInboundEmail` claim. The run-loop's crash-recovery check
   ("state file says processed but DB claim is absent") therefore re-classified
   the same message every 5-minute poll. Rows 14–22 are **one** John Galt
   email rejected nine times (created_at spaced exactly 5 minutes apart);
   row 23 is a second one. So "a few from John Galt" = 2 emails × repeated
   rejection.

### Fix

- `_normalize_body` converts HTML to text (`html_to_text`, upgraded
  `_strip_html`: drops style/script/head/comments, maps block tags to
  newlines and table cells to tabs, decodes all entities via `html.unescape`)
  before classification *and* parse. `classify_rfq` also self-defends by
  stripping HTML-looking bodies.
- Classify snippet widened 500 → 2000 chars (`DEFAULT_RFQ_CLASSIFY_BODY_CHARS`)
  so a real request below quoted forward headers is inside the window.
- Classifier prompt now states that generic reply/forward subjects
  ("Checking in", "Following up", …) carry no signal and the body governs,
  and that the request may sit below quoted From:/Sent: headers.
- Deliberate outcomes (reject, no-line-items) now write the
  `ProcessedInboundEmail` claim in the same transaction as their record row —
  one email, one classification, one row. The claim is reversible: delete the
  row and re-run the message with `tools/replay_inbound.py` (added; supports
  `--dry-run`). Transient failures (DB down, LLM truncation) still retry
  unclaimed.

## Symptom 2 — T437: Sable RFQ "didn't catch in system"

Monitor logs 2026-08-25: message `…91hPPAAA=` (4 attachments, the Sable
thread) was fetched, classified as RFQ, then **"parsed but produced no line
items; skipping"** — repeatedly, every 5 minutes from ≥18:52 to 19:18 (same
missing-claim retry loop), then never again once the watermark passed it. No
quote, no `rejected_email`, no `failed_intake` row: a silent drop, the
allanedwards:I130 class of failure. Multi-party threading likely contributed
to the zero-item parse (content buried in quoted layers of raw HTML), but the
architectural gap is that this outcome was invisible.

Chip's later forward ("FW: Revised quote request") did process (126-105), and
he hand-built 126-106.

### Fix

"Classified as RFQ but zero line items" now records a `FailedIntake` row with
`failure_stage="no_line_items"` (visible in the admin failed-intakes queue,
which already exists from T376) and claims the message. The HTML→text change
also directly improves parse odds on layered Outlook threads.

## Symptom 3 — T378 fold-in: RFQ mis-detected as revision

Read `docs/research/rfq-mis-detected-as-revision-378.md` first, per task. Its
verdict stands: the 126-003-01/-02 aliasing was the task-374 quote-number
generator collision (string-max + suffix parsing), fixed in 374 and
consolidated in 377 (`app/quote_numbers.py::generate_quote_number`), locked by
the end-to-end regression test
`tests/test_db_writer.py::test_multi_rfq_email_numbers_never_alias_existing_quote`.
There is no automatic revision-detection path in the responder, and
`_name_similarity` was not the culprit. **No further change made or needed**;
staging scenario (b) below re-verified no folding.

## Verification

Unit: `tests/test_intake_classification_459.py` — 15 tests covering
html_to_text, classify snippet content, `_normalize_body`, reject-path
claim/no-duplicate behavior, claim reversibility, no-line-items FailedIntake,
and transient-failure retry preservation. Every test was negative-tested by
mutating the specific fix line and observing an assertion failure
(4 mutations, all red on the assertion, restored from cp backups).

Staging (134.122.29.15, delivery off, real Claude provider, branch deployed
via deploy_web.sh):

- (a) "FW: Checking in" from jgalt@ with the prospect's RFQ in a quoted BP
  reply → classified RFQ, quote created for **BP Pipelines North America**,
  contact **jose.pedraza2@bp.com** (the prospect in the body, not the broker).
- (b) Two unrelated RFQs (Marathon, Troy) → two independent quotes
  (126-036/126-037), distinct customers, no folding or number nesting.
- (c) Sable-style multi-party thread (first contact not decision-maker) →
  caught; contact resolved to the decision-maker (rgomez@sableoffshore.com).
- (d) Negative: genuine "no need this quarter" reply on the same
  "FW: Checking in" subject → still rejected, **one** rejected row, claim
  written, replay skipped. The gate was not merely widened.
- Parse-layer A/B on staging: old raw-HTML input vs new stripped-text input
  produced identical, correct parses (qty 4 / w/t 0.500), so the change is
  parse-neutral where the old path worked and strictly better where it
  starved the classifier.
- All staging test records (4 quotes, 4 customers, rejection rows, claims)
  deleted after verification; counts confirmed 0.

Note: staging quotes showed bundle rounding (qty 4→5: sleeves ≤24" sell in
5-piece bundles — intended) and placeholder catalog part matching
("[PLACEHOLDER stock numbers — invented, confirm w/ Chip] [task-428]") —
both pre-existing, unrelated to this change.

## Auto-send dials

Untouched, per I158/task instruction. The fix is upstream of scoring: it only
changes what the classifier/parser see. Recommendation only: no dial changes
needed for this fix.

## Prod follow-up after deploy

The two stuck John Galt messages (rejected rows 14–23) and the Sable message
`…91hPPAAA=` can be re-run through the fixed pipeline with
`tools/replay_inbound.py <graph-message-id>` (their claims — none exist for
these, since the bug predates claims — and state entries are cleared by the
tool). Since Chip already handled all three by hand (126-105/106/111), replay
is optional; if run, delete the resulting duplicate quotes or skip replay
entirely.
