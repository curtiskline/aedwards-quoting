# RFQ "mis-detected as a revision of an unrelated quote" — root cause (task 378)

**Date:** 2026-08-13 / investigated 2026-08-14
**Verdict:** This is a downstream manifestation of the task-374 quote-number
generator collision (already fixed). It is **not** name-similarity over-matching,
and there is **no** automatic revision-detection path in the e-responder.

## Reported symptom

On 2026-08-13 a new multi-item RFQ appeared to be folded into an unrelated
existing quote **126-003**, reading as revisions of it. The task framing
suspected the duplicate/revision heuristic (`db_writer._name_similarity`,
`_NAME_MATCH_THRESHOLD ≈ 0.85`, and the revision path) was over-matching distinct
RFQs.

## What actually happened (production evidence)

Quotes on prod (`/opt/aedwards/instance/allenedwards.db`):

| id  | quote_number | customer                       | subject                                | replaces_quote_id | source_email_id | created_at          |
|-----|--------------|--------------------------------|----------------------------------------|-------------------|-----------------|---------------------|
| 3   | 126-003      | Permian Basin Pipeline LLC     | Need a quick quote - 20" and 24" sleeves | 0               | 19d72a6a…       | 2026-04-09 14:34 |
| 100 | 126-003-01   | The Troy Companies             | Fwd: RFQ for Form Coating 24-Inch Pipe | 0                 | AAMk…1UFvAAA3…  | 2026-08-13 14:39 |
| 101 | 126-003-02   | The Troy Companies             | Fwd: RFQ for Form Coating 24-Inch Pipe | 0                 | AAMk…1UFvAAA3…  | 2026-08-13 14:39 |

Key facts:

- Quotes **100 and 101 share one `source_email_id`** — a single email carrying
  **two** RFQs (a multi-RFQ message from `cedwards@allanedwards.com`).
- Their `replaces_quote_id = 0` and `revision_number = 0`. **They are not linked
  as revisions of anything.** The only real revision on prod is `126-074-R1`
  (`replaces_quote_id = 74`), created by hand.
- The base number the generator handed the email was **`126-003`**, which already
  existed as the unrelated Permian Basin standalone quote. Because the email had
  two RFQs, the monitor suffixed them `126-003-01` / `126-003-02`
  (`monitor.py:241`: `f"{base_quote_number}-{idx + 1:02d}"`). Those children
  slotted into the **number namespace of quote 126-003** and *look* like its
  revisions — the reported symptom.

The task title/description mixed a few details (Marathon vs. Troy vs. Permian,
and the "20/24 sleeves" subject belongs to quote 3, not the new RFQ), but the
mechanism is unambiguous from the source-email grouping and the numbers.

## Why it is not the matching heuristic

- **No auto-revision path exists.** Revisions are created only by the human-only
  endpoint `POST /quotes/<id>/revise` (`routes.py:1560`). The monitor →
  `write_quote_to_db` path always inserts a fresh row and never sets
  `replaces_quote_id`.
- **Customer name-similarity did not fire.** Token-sorted `SequenceMatcher`:
  `Marathon Petroleum` vs `The Troy Companies` = 0.44, vs `Permian Basin
  Pipeline` = 0.40, `Troy` vs `Permian` = 0.30 — all far below the 0.85
  threshold. In the actual records, Troy → a correctly-created **new** customer,
  and later Marathon RFQs (126-098/099) → correctly matched the existing Marathon
  customer (id 25). Customer matching behaved correctly throughout.

## Root cause

The old `_generate_fiscal_quote_number` computed the next sequence by string-max
of existing numbers and reading `split("-")[-1]`. Once revision/child suffixes
existed, the string max was a suffixed number and `[-1]` grabbed the suffix, so
the generator regenerated an already-used base (the 2026-08-13 outage). For a
**single-RFQ** email this tripped the `UNIQUE` constraint and blocked all
auto-quotes; for a **multi-RFQ** email the `-NN` suffix dodged the exact-dup
constraint and instead produced the "child of an unrelated quote" numbers seen in
100/101.

This generator bug was **fixed in task 374** (commit `f0f4302`): the sequence is
now parsed from the sequence segment (`parts[1]`) and the numeric max is taken,
which is immune to revision/child suffixes. With the fix, a multi-RFQ email's
base is always strictly greater than every existing sequence, so its `-NN`
children cannot alias any existing quote.

## Change made in task 378

Per PM direction (generator consolidation and the collision-retry guard are owned
by **task 377** — no second guard added here), task 378 delivers:

1. This report.
2. An **end-to-end regression test**,
   `tests/test_db_writer.py::test_multi_rfq_email_numbers_never_alias_existing_quote`,
   that drives the real monitor path (generator → inline `-NN` suffixing → DB
   write) for a two-RFQ email against a DB pre-seeded with the exact hazard
   (`126-001..126-003` plus a prior child `126-003-01`) and asserts the resulting
   numbers neither equal nor nest under any existing quote number.

**Negative test performed:** reverting `_generate_fiscal_quote_number` to the old
string-max/`split("-")[-1]` logic makes the test fail *on the assertion* — the
buggy generator yields base `126-002`, children `126-002-01/-02` that nest under
the unrelated standalone `126-002`. The fixed generator yields `126-004-01/-02`,
aliasing nothing.

## Recommendation

No further e-responder change is needed for this symptom beyond task 377's
consolidated generator. The regression test locks in the invariant so a future
generator regression that reintroduces base aliasing is caught before it can fold
new RFQs into an unrelated quote's number namespace.
