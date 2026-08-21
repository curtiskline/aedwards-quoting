# Staging demo dataset — reset & reseed runbook (task 408)

Staging (`https://staging.quotes.vectorforgeinteractive.com`, droplet
134.122.29.15) carries a demo-ready dataset built by running **real RFQ
emails** from Jamee's mailbox (`data/test-corpus/emails`, PST dump) through
the **real pipeline** (classify → LLM decode → deterministic pricing →
db_writer) plus seeded history that makes the CP-2 confidence signals
meaningful. Nothing is mocked: every queue row is a real email the company
actually received, priced by the live engine on the staging box.

## What is on staging after a seed

- **38 demo quotes** from 43 curated corpus emails (per-case rationale in
  `tools/demo_corpus_manifest.json`), mix as of 2026-08-21:
  16 Recommended / 22 held; 31 NEW, 7 NEEDS_PRICING. Every distinct hold
  reason is represented: new customer, unconfirmed ship-to, no price
  history, out-of-tolerance (126-034: $130 vs $388 median, 41 comparables),
  needs-pricing guardrail, missing/blocked recipient, defaults-applied
  decode.
- **79 backdated SENT quotes** as the price-history baseline:
  37 from the ground-truth corpus (`data/test-corpus/ground-truth`, real
  quotes Jamee sent with real prices) + 42 seeded comparables (3 per
  green-target quote at its own engine price −3 %/0/+4 %; the engine's
  per-lb formula derives from the company's real price tables).
- **56 customers** (30 from ground truth, backdated; the rest auto-created
  by the pipeline from the RFQs themselves — those demo the new-customer
  hold), human-confirmed ship-to addresses where the manifest says so.
- **7 rejected emails** in `/admin/rejected-emails` with real classifier
  reasoning (marketing, webinar, delivery notification, invoice thread…).
- Trust ramp stays **Tier 1**; dials at defaults (0.95 / $2,500 / ±20 %).

This dataset **is** the demo — the prod post-deploy test-quote deletion
policy does NOT apply to it.

## Reset + reseed (fresh demo)

The corpus lives on the box under `/opt/aedwards/demo-corpus/` (emails/,
attachments/, ground-truth/) — rsynced there, deliberately never committed.
The runner and manifest are committed in `tools/` and copied to the same
directory. To refresh after editing them:

```bash
scp tools/seed_staging_demo.py tools/demo_corpus_manifest.json \
    root@134.122.29.15:/opt/aedwards/demo-corpus/
```

Full reset + reseed (wipes quote-family + customer tables, keeps
users/pricing/product types/trust ramp; ~5 min, ~40 LLM decodes):

```bash
ssh root@134.122.29.15 'cd /opt/aedwards/demo-corpus && \
  sudo -u aedwards /opt/aedwards/venv/bin/python seed_staging_demo.py all \
    --i-am-staging --manifest demo_corpus_manifest.json'
```

Phases can run individually: `reset`, `seed-history`, `ingest`, `rescore`,
`report`. `ingest` is idempotent (ProcessedInboundEmail claims keyed
`demo-corpus:<filename>`), so re-running it only picks up manifest additions.
`report` prints the current per-quote signal/recommendation table.

**Safety:** the runner refuses to run unless the hostname is
`aedwards-staging`, `EMAIL_DELIVERY_ENABLED=false`, and `--i-am-staging` is
passed (negative-tested; local plumbing tests use `--dev-sandbox`, which only
accepts a sqlite DATABASE_URL). There is no override that reaches prod (D67).

After every reseed, run the deploy README isolation checklist (monitor
disabled+inactive, no mailbox credential lines, delivery false) — verified
passing 2026-08-21.

## Demo walkthrough (verified in the UI 2026-08-21)

- **Queue** (`/quotes/`): 117 quotes across All/New/Needs Pricing/Sent tabs,
  Recommended/Not-recommended filter, six-dot signal pills with scores,
  real company names and dollar totals ($69 backing strips to the $365k
  Enbridge two-state spreadsheet bid 126-030-01/-02, which also demos the
  Tier-2 dollar ceiling).
- **Editor, green path** (126-032-02 Duke Energy): 100 % confidence, all six
  signals pass with bases — "matches human-confirmed stored address #43",
  tolerance table "$69.72 vs median $69.72 (0.0 % off, 3 comparable sent
  quotes)".
- **Editor, held path** (126-034 Red Flame): "Why not" box (new customer +
  out-of-tolerance), per-line tolerance detail including "no comparable
  history" lines.
- **Admin → Trust Ramp**: tier selector at Tier 1, auto-send dials, empty
  send-holds with customer/product-type pickers.
- **Admin → Rejected**: the 5 curated non-RFQs plus 2 classifier judgment
  calls, each with its stated reason.

Login: `dev@local.test` (staging-only test account; password set for the
demo — ask Devin/see task 408 notes — or mint a magic link by inserting an
`auth_token` row on the box and visiting `/auth/magic/<token>`).

## Known quirks surfaced by the real corpus (not fixed here)

- **Quote-number base reuse with multi-quote emails**: suffixed numbers
  (`126-030-01`) are invisible to the generator's `^prefix-(\d+)$` regex, so
  the next email is assigned base `126-030` again. Two multi-quote emails in
  a row would collide on `-01` (UNIQUE violation → failed intake). Reported
  to PM under task 408.
- The classifier rejected "Eco bag RFQ" (buoyancy bags ARE a product) and
  the 36" GR65 W9-request thread — kept in the dataset deliberately: the
  rejected-emails audit trail is the demo story for classifier misses.
