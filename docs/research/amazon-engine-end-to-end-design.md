# Amazon-Engine — end-to-end back-end architecture (staged design)

**Task 366** (parent: epic 347 / D51). ARCHITECT/DESIGN doc. **Read-only on source — nothing here is implemented.**
This synthesizes the completed research into a staged, buildable design for the Allan Edwards back-end
automation engine, so concrete build tasks can be scoped against it.

**One-line frame:** the engine is *not* a new system — it is the existing single-stage email→quote
pipeline (C39) extended, on hardened infra, into intake → auto-quote → **order** → fulfillment →
inventory, invisible to the customer (D51). The near-term ROI is removing 40–60 hrs/week of hand-typed
sales orders and routing around the entrenched employee (D41), **not** a storefront (deferred, D51).

---

## Sources read (enumerated)

**Research docs (this repo):**
- `docs/research/onsite-2026-08-10-structured-notes.md` — Chip's vision; automation targets (§5); open questions (§8).
- `docs/research/current-backend-endstate-map.md` — what EXISTS today, the quote→order seam, infra constraints.
- `docs/research/rag-to-engine-correlations.md` — what carries from the RAG proposal; the commercial model.
- `docs/research/product-data-model-ground-truth.md` — the data model (Type/part_number/no-FK reality).

**Canon:** D51 (engine-first/storefront-later, authoritative), D50 (superseded framing), D41 (epic seed),
I124 (Uline omni-channel reference), I54 (agentic-ETL pattern), I68 (gateway), I80 (state-don't-sell tone),
I111 (monitor SIGTERM/dup-quote hazard), C36–C42 (canonized findings), D13/D16 (pricing model), D17 (intake
first-class), D35 (live commercial terms).

I did **not** re-read source; the current-state map is file:line-cited and I trust it as ground truth
(C39–C42 are canonized from it). Where this doc names a file:line, it is quoting that map.

---

## 0. Design principles (carried wholesale, not re-litigated)

These are settled by prior research/canon and bound every stage below:

1. **Agentic-ETL is the spine (I54, C36).** Classify → extract structured entities → extend schema
   conservatively → write records → embed/link source. **Humans see a dashboard, not an approval queue.**
   Schema-evolution rules carry: merge-before-create, conservative typing, `schema_evolution_log` audit,
   never drop columns autonomously.
2. **Automate the 80%, leave the 20% by hand (I79, onsite §2).** 80% standard commodity is formula-priced;
   20% custom stays manual. This is the automation boundary at every stage, not just pricing.
3. **The engine ABSORBS work; it never adds admin to revenue producers (D41).** Every ping must *replace* a
   step a human does today, not add one. This is the hard constraint that killed ACT/Salesforce/NetSuite/HubSpot.
4. **State, don't sell (I80).** Any Chip-facing description of the trust ramp promises "a gate that relaxes,"
   never "it never makes mistakes."
5. **Customer behavior is preserved on purpose (D51).** No customer is asked to change channels. Storefront
   is deferred, not dropped — keep the engine channel-agnostic so a storefront can bolt on later as one more
   front-door.
6. **At-least-once is the default delivery semantics of this system, and it will stay that way.** Every
   irreversible action (send, order, reorder) must be idempotent on a stable key. See Milestone 1.

---

## 1. Sequencing / dependency diagram

Milestone 1 is a **gate**: nothing auto/irreversible ships until it lands. After it, the stages layer onto
the existing intake→quote lane. Dashed boxes = net-new subsystems.

```
                    ┌─────────────────────────────────────────────────────────────┐
                    │  MILESTONE 1 — INFRA HARDENING (GATE, Devin-directed)          │
                    │  • monitor SIGTERM interruptible-wait + state-before-boundary  │
                    │  • whole-message idempotency ledger — task 340                 │
                    │    (NOT a Quote.source_email_id UNIQUE constraint — see note)   │
                    │  • Postgres-vs-SQLite decision BEFORE concurrent writers       │
                    │  Nothing below auto-fires until this is green.                 │
                    └───────────────────────────────┬─────────────────────────────┘
                                                     │ (hard dependency)
        ┌────────────────────────────────────────────┼────────────────────────────────────────┐
        ▼                                             ▼                                          │
┌──────────────────┐   already      ┌────────────────────────┐   already                        │
│ STAGE A          │   built        │ STAGE B                │   built                           │
│ Intake           │──────────────► │ Auto-quote             │  (stages 1–2 of C39)             │
│ (email today)    │   ParsedRFQ    │ decode+price → Quote   │                                   │
│ + fax/phone/web/ │                │ draft in Outlook       │                                   │
│   API normalizers│                └───────────┬────────────┘                                   │
│  → same ParsedRFQ│  (net-new: the             │                                                │
│   via I68 gateway│   normalizers)             ▼                                                │
└──────────────────┘                ┌────────────────────────┐                                   │
                                     │ STAGE C  (net-new)     │  replaces the mandatory human     │
                                     │ TRUST-RAMP AUTO-SEND   │  gate at routes.py:2707           │
                                     │ confidence-gated       │  ← highest-liability piece        │
                                     │ dashboard, phased      │                                   │
                                     └───────────┬────────────┘                                   │
                                                 │ SENT + immutable QuoteVersion                  │
                                                 ▼                                                │
                          ═══════ ACCEPTANCE SIGNAL (DECISION-NEEDED #1) ═══════                  │
                                                 │  "customer said yes"                           │
                                                 ▼                                                │
                              ┌─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐                                 │
                              │ STAGE D  (net-new)              │  reads QuoteVersion             │
                              │ ORDER object + quote→order      │  .line_items_snapshot (C40)     │
                              │ transition at the SENT/accepted │                                 │
                              │ seam (routes.py:2707)           │                                 │
                              └─ ─ ─ ─ ─ ─ ─ ─ ┬ ─ ─ ─ ─ ─ ─ ─ ┘                                 │
                                               ▼                                                  │
                              ┌─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐                                 │
                              │ STAGE E  (net-new)              │  paper/phone now,               │
                              │ FULFILLMENT: pick-list +        │  structured later               │
                              │ shop ping ("put on the truck")  │                                 │
                              └─ ─ ─ ─ ─ ─ ─ ─ ┬ ─ ─ ─ ─ ─ ─ ─ ┘                                 │
                                               ▼ stock decrement                                  │
                              ┌─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐                                 │
                              │ STAGE F  (net-new subsystem)    │  no source of truth exists      │
                              │ INVENTORY: stock table +        │  today (DECISION-NEEDED #2)     │
                              │ min/max + auto-reorder          │◄────────────────────────────────┘
                              └─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘        (reorder pings the shop)
```

**Ordering rationale:** A depends on nothing new (exists); C depends on Milestone 1 (auto-send on an
at-least-once loop is the amplification hazard); D depends on C's SENT event *and* the acceptance signal;
E depends on D (no order → nothing to fulfill); F depends on E's decrement event to know what sold, and F's
reorder path reuses E's shop-ping channel. **A and the calculators (composite, add-ons) can proceed in
parallel** with C/D because they don't fire irreversible actions.

---

## 2. Milestone 1 — Infra hardening FIRST (the gate) — WHY it is milestone 1

**This is Devin-directed and non-negotiable as the first milestone.** Everything Chip wants (auto-send,
auto-order, auto-reorder) is an **irreversible, customer-or-shop-facing side effect fired on the monitor's
poll loop.** Today that loop is at-least-once and has a live duplication bug. Layering irreversible actions
on it doesn't just inherit the hazard — it *amplifies* it from "a rare duplicate quote draft a human would
catch in the queue" to "a duplicate quote **auto-sent to a customer**, a duplicate **order** to the shop, a
duplicate **reorder** to a supplier." No human is in the loop to catch it by design (D51).

Three concrete items, in order:

1. **Monitor SIGTERM / interruptible wait (task 340, I111, C39).** The monitor blocks in a `time.sleep`
   poll (`monitor.py:137`) and ignores SIGTERM, so systemd SIGKILLs it 90s into every deploy — and
   `deploy_web.sh` bounces it too, so a two-script deploy takes the risk twice. Fix: wait on an event with
   timeout so SIGTERM exits promptly.
2. **State-before-boundary + whole-message idempotency (C39/C40).** The watermark
   (`.monitor_state.json`) is saved *after* processing (`monitor.py:171`), so a kill at the wrong moment
   reprocesses an email → duplicate quote.
   > **CORRECTION (PM, 2026-08-11, reconciling with task 340 / codex-allanedwards-2):** The original draft
   > recommended a `Quote.source_email_id` **UNIQUE constraint** as the dedup key. That is WRONG — one email
   > can legitimately produce **multiple** RFQs/quotes, so a uniqueness constraint on `Quote.source_email_id`
   > would break correct behavior. The right mechanism is **whole-message idempotency**: a per-message
   > processed-ledger (claimed → done) keyed on the email **message id**, so reprocessing after a crash is a
   > no-op rather than a duplicate, WITHOUT forbidding multiple quotes per email. Task 340 is implementing
   > this correct approach. **Fail toward reprocessing, not dropping** — mark a message fully-processed only
   > after side effects complete, so a crash mid-processing re-drives the message instead of silently losing
   > the RFQ.
   Downstream generalization still holds: every irreversible action (order-create, shop-ping, reorder) keys
   idempotency off a stable lineage (the QuoteVersion / order id, and the message-processed ledger) so a
   replay is a no-op — just **not** via a `source_email_id` unique constraint.
3. **Postgres-vs-SQLite decision BEFORE any concurrent writer (C39, DECISION-NEEDED #6).** Prod is
   single-file SQLite; the monitor opens a Flask app context to write (`cli.py:569`). Stages D/E/F add
   **concurrent writers** (order creation, shop-ping status, inventory decrement) racing the monitor and the
   web app on one file. Single-writer SQLite is a real constraint at fan-out. Recommend evaluating/committing
   the DB before the first concurrent writer, not after it deadlocks in prod.

**Exit criteria for the gate:** monitor exits cleanly on SIGTERM; `source_email_id` UNIQUE in place and
proven idempotent under replay; DB decision made and (if Postgres) migrated. Only then does any auto-fire
stage ship.

---

## 3. The stages — exists vs net-new, dependencies, data-model additions, primary risk

### Stage A — Channel-agnostic intake expansion
- **Exists:** Email-only intake (O365 *or* Gmail, polled every 5 min; `monitor.py:139`, `cli.py:503`), full
  attachment handling incl. embedded `.eml` (C41). Classify (`parser.py:1095`) and decode →`ParsedRFQ`
  (`parser.py:970`) are the mature part.
- **Net-new:** fax / phone / web-form / API **normalizers**, each producing the **same `ParsedRFQ`** so
  everything downstream is channel-blind. Reuse the **I68 gateway** (input classification/routing — "is this
  an RFQ, a reorder, or a note?"), which was already specced for exactly this and has in-house reference
  implementations (axon console/gateway). Chip's phone-dictation "ask page" (D17, I68) becomes *one channel*,
  first-class per D17.
- **Depends on:** nothing new (can start immediately, parallel to C). No irreversible side effect, so it does
  **not** gate on Milestone 1 for its own correctness — but its *outputs* feed the auto-send/order stages that do.
- **Data-model additions:** a `channel` / `source` field on the intake record (email|fax|phone|web|api) and a
  normalized inbound-request row so non-email channels have a home before they become a `Quote`.
- **Primary risk:** each normalizer is a new fidelity-loss surface (a garbled fax OCR → wrong `ParsedRFQ`).
  Keep the classifier's reject→`RejectedEmail` audit pattern per channel.

### Stage B — Auto-quote (already built — the only stage that exists)
- **Exists (C39, onsite §5):** decode → price (`pricing.py:1422` `generate_quote`, formula + catalog
  lookups) → `Quote`+`QuoteLineItem` (gated `ENABLE_DB_WRITES`) → PDF draft dropped in **Outlook Drafts**
  (never sent). `$0`/unpriceable → `NEEDS_PRICING`, never auto-sendable (`_tbd_line_item`, `db_writer.py:371`).
- **Net-new:** the **composite "how much to buy" calculator** (onsite §2/§5.8, DECISION-NEEDED #5) is a
  decode/pricing extension for wraps (pipe size / wall / defect → quantity). The **add-on options catalog**
  (itemized upcharges, e.g. PO-stencil ~$50; onsite §5.6) is a data-model + pricing addition. Both plug into
  this stage; both can proceed independent of the auto-fire gate.
- **Primary risk:** the composite calc is an engineering calculation — wrong output is a wrong quote. Scope
  it as **assist first** (surface the number for a human), automate later (mirrors the trust ramp).

### Stage C — Trust-ramp auto-send (net-new; highest-liability)
- **Exists:** the mandatory **human** send gate — `quote_send()` at `routes.py:2707`, 100% human-initiated
  (C42). Guardrails already present to keep/extend: `SEND_EMAIL_ALLOWLIST` recipient gate
  (`routes.py:2729`), `$0`/unpriced → never auto-sendable, `ShipToAddress.human_confirmed` trust flag.
- **Net-new:** replace the *mandatory* gate with a **phased, confidence-gated dashboard** (C38, D51) that
  relaxes over time — **not a flip**. This is the direct reframe of the RAG OCR-confidence-gate + "records
  first, human sees a dashboard not a queue" pattern. Design detail in §4.
- **Depends on:** **Milestone 1** (this is *the* stage that amplifies the at-least-once hazard into an
  auto-sent-to-customer event). Hard gate.
- **Data-model additions:** a per-quote **confidence score** and its component signals (decode confidence,
  all-lines-priced, customer known / `human_confirmed`, price within tolerance of history); an
  **auto-send audit** (what tier, what threshold, who/what released it) extending `AuditLog`
  (`source_email_id` already carried).
- **Primary risk:** **one mispriced auto-quote reaches a customer** (D51's named open risk). This is the
  single highest-liability piece in the whole engine. The ramp exists precisely to bound that.

### Stage D — Order object + quote→order transition (net-new)
- **Exists:** nothing (C39). No `Order` model, no status past `SENT`. The **seam is `routes.py:2707`**
  (`quote_send()`), which today sets `SENT` and creates an immutable `QuoteVersion` (retained PDF +
  `line_items_snapshot`, `models.py:214`) and stops. Nothing consumes a SENT quote — that empty socket is
  the insertion point (C40).
- **Net-new:** an **`Order`** created from `QuoteVersion.line_items_snapshot` (immutable, already carries
  `sent_to`/`sent_at`/`sent_by` — the natural order payload; build the order engine to read `QuoteVersion`,
  **not** the mutable `Quote`). Plus the state transition and who/what authorizes it.
- **Depends on:** Stage C's SENT event **and** the **acceptance signal (DECISION-NEEDED #1)** — the order
  half cannot fire until "customer said yes" is detectable (reply-parse / explicit accept / PO-received).
  Today there is *no* acceptance signal anywhere.
- **Data-model additions:** `Order` (FK to originating `QuoteVersion`, `status`, timestamps), an
  `OrderStatus` state machine picking up where `QuoteStatus` stops (`…SENT → ACCEPTED → ORDERED → FULFILLED`),
  and the acceptance-event record. Key idempotency off the QuoteVersion + `source_email_id` lineage so a
  replayed acceptance doesn't double-create an order.
- **Primary risk:** double-order on replay (Milestone-1-dependent) and mis-detected acceptance (an
  auto-reply or "thanks" parsed as a "yes"). The acceptance signal's precision is the whole risk.

### Stage E — Fulfillment: pick-list + shop ping (net-new)
- **Exists:** nothing (C39). No pick list, no shop notification.
- **Net-new:** generate a **pick-list / shop instruction** from the `Order` and **ping the shop** ("put this
  on the truck"). **Paper or phone now** (truck drivers still need a paper copy; onsite §4), structured
  pings "down the road." This is the ABSORB-the-work stage (D41): it replaces the one person hand-typing the
  sales order.
- **Depends on:** Stage D (no order → nothing to fulfill).
- **Data-model additions:** a `PickList`/fulfillment record (FK to `Order`, `status`: queued → picked →
  loaded → shipped), and a ship/pack unit model that respects the physical facts (standard 30 ft / some 15 ft
  lengths; no custom cutting; sold in whole pieces/pallets — onsite §6, so quantities round to pack units).
- **Primary risk:** the pick instruction must match the immutable order exactly; any drift between quoted
  lines and picked goods is a fulfillment error the customer sees. Drive it off the same snapshot, not a
  re-derivation.

### Stage F — Inventory: stock table + min/max + auto-reorder (net-new subsystem)
- **Exists:** nothing. **No stock table, no reorder trigger, no source of truth exists** (C39, onsite §5.5).
  This is the most greenfield stage.
- **Net-new:** a **stock table** with per-item on-hand, **min/max** thresholds, a **decrement** on
  fulfillment (Stage E), and **auto-reorder** when on-hand crosses min ("when we sell something, automatically
  send an order to the shop"). Reorder reuses Stage E's shop-ping channel.
- **Depends on:** Stage E (needs the decrement event to know what sold) **and** resolution of
  **DECISION-NEEDED #2** (is there any existing inventory source of truth, or is this net-new? what feeds
  initial stock levels?). Cannot build the reorder math without knowing what stock levels come from.
- **Data-model additions:** `StockItem` (keyed to a real product identity — see the note below), `on_hand`,
  `min_qty`, `max_qty`, `reorder_qty`, a stock-movement ledger (decrement/reorder/receipt) for auditability.
- **Primary risk:** **auto-reorder is an irreversible supplier-facing action** — a phantom decrement (double
  fulfillment on replay) auto-orders steel that isn't needed. Milestone-1 idempotency is load-bearing here.
  Second risk: without a clean product identity, stock can't be keyed reliably (see the cross-cutting note).

### Cross-cutting: the product-identity debt bites Stages D–F
The data model today is **loosely coupled by bare lowercase strings, no foreign keys**
(product-data-model-ground-truth.md): `QuoteLineItem.product_type`, `PricingTable.product_type`, and
`ProductCatalog.product_family` are three independent strings; picking a catalog row *copies text*, it does
not reference a row. `sku`/`part_number` are two nullable identifier columns for one concept. **Orders,
fulfillment, and especially inventory need a stable product identity to key on** — you cannot reliably
decrement stock for "sleeve" as a free string. The Type/part_number cleanup already shipped (tasks
358/360/362) is the foundation; the recommended next step from that research — **make
`QuoteLineItem.product_type` an FK to `ProductType`, and give `ProductCatalog` a `product_type` + a stable
identifier** — should be treated as a prerequisite (or at least a co-requisite) of Stage F, not an optional
cleanup. Flag this in Stage D scoping so it isn't discovered mid-inventory-build.

---

## 4. The trust-ramp design (Stage C, in detail)

The trust ramp is the reframe of the RAG confidence-gate + dashboard pattern (C38) onto auto-send. It is the
highest-liability piece (D51), so it is designed as a **gate that relaxes in tiers**, each tier releasing a
*wider* slice of quotes to auto-send, with a human watching a **dashboard, not clearing a queue** (I54).

**The signals a quote carries (the confidence score):**
- decode confidence (did the LLM cleanly extract every line?),
- all lines priced by formula/catalog (any `NEEDS_PRICING`/`$0` line → never auto-sendable, existing guardrail),
- customer known + `ShipToAddress.human_confirmed` (existing trust flag),
- price within tolerance of historical quotes for the same product/specs (a "does this look normal" check),
- recipient passes `SEND_EMAIL_ALLOWLIST` (existing).

**The tiers (the ramp — specifics are DECISION-NEEDED #3, this is the shape, not the committed thresholds):**
1. **Tier 0 — today:** everything is human-sent (`routes.py:2707`). Baseline.
2. **Tier 1 — assisted:** engine pre-fills and *recommends* send for high-confidence quotes; human clicks.
   Dashboard shows the confidence score and *why*. (This is the "records + citations first" pattern —
   the human sees the basis, not a black box.)
3. **Tier 2 — auto-send the safe slice:** quotes above a high confidence threshold **to known/confirmed
   customers within price tolerance** auto-send; everything else falls to the human. Human watches the
   dashboard and can set a per-customer or per-product-type hold.
4. **Tier 3 — default auto, exception-gated:** most quotes auto-send; only low-confidence / new-customer /
   out-of-tolerance / high-dollar quotes hold for a human. ("By next year we're not even checking it" — D51 —
   lands here, *as an exception gate, never a blind flip*.)

**Non-negotiable guardrails at every tier:** `$0`/unpriced never auto-sends; recipient allowlist enforced;
every auto-send is audited (tier, threshold, signals) off the `source_email_id` lineage; a global kill-switch
drops all tiers back to Tier 0 instantly. **What relaxes** as the ramp advances: the confidence threshold, the
"customer must be confirmed" requirement, and the dollar ceiling — each is a dial, tracked, reversible.

**Tone for Chip (I80):** describe this as "a gate that starts fully manual and relaxes as the engine earns
trust, with a kill-switch," **never** "it won't make mistakes." One mispriced auto-quote reaching a customer
is the failure this design exists to bound.

---

## 5. DECISIONS-NEEDED (Devin/Chip to resolve — NOT invented here)

These are genuine open questions from onsite §8 and the task brief. I give a recommendation where the
research supports one, but each is flagged as a human decision, not a design fait accompli.

1. **Acceptance signal — how "customer said yes" is detected.** Options: reply-parse (LLM reads the reply
   thread), explicit "Accept" action (rep or customer clicks), or PO-received event. Fires the entire order
   half (Stage D). *Recommendation to consider:* start with an **explicit accept action** (deterministic, no
   false-positive risk) and add reply-parsing later behind its own confidence gate — but this is Chip's call
   because it shapes the customer interaction.
2. **Inventory source of truth (Stage F).** Does a stock system exist anywhere (spreadsheet, QuickBooks,
   shop-floor tally), or is it fully net-new? What feeds initial min/max levels? **Cannot build reorder math
   without this.** No source exists in the codebase; needs a factual answer from Chip.
3. **Trust-ramp specifics (Stage C).** The exact tier definitions, confidence thresholds, dollar ceilings,
   and what relaxes when. §4 gives the shape; the numbers are policy Chip/Devin set.
4. **Customer pricing tiers ("pain-in-the-ass tax") + add-on-options catalog.** Policy, not just code
   (onsite §5.6/§5.7): which customers pay more, and how add-ons are itemized (Chip: **itemize, don't bury** —
   customers resent hidden markup). Model design waits on the policy.
5. **Composite "how much to buy" calculator scope — automate vs. assist.** The engineering calc (pipe
   size/wall/defect → quantity). *Recommendation:* assist first (surface the number, human confirms), automate
   behind a confidence gate later — same philosophy as the trust ramp. Extent is Chip's call.
6. **Postgres vs. SQLite (Milestone 1).** *Recommendation: move to Postgres before the first concurrent
   writer (Stage D)* — single-writer SQLite is a real constraint once order/shop/inventory writers race the
   monitor and web app (C39). Flagged explicitly as **Devin's call**, not a unilateral architecture change.

---

## 6. Commercials — suggested stage → billing mapping (proposal starting point, NOT a commitment)

Carries the model from 364 / the RAG C-notes: **fixed-price, month-sized stages**, each a complete working
deliverable at a price **agreed before the stage starts**, ~**$6k/month** cadence, **25% down / net-45**
(D16/D35), **state-don't-sell** tone (I80). This is **NOT** the old ~$19k RAG total — that covered a different
scope (retrieval + whole-drive ingestion). The reallocation signal from 364 applies: the budget that was going
to whole-drive ingestion (now demoted, C37) redirects to the genuinely new auto-order/shop/inventory stages.

Suggested checkpoints (a starting point for Devin to price, not a quote):

| Billing checkpoint | Covers | Why it's a clean deliverable |
|---|---|---|
| **CP-0 — Infra hardening** (Milestone 1) | monitor SIGTERM fix, `source_email_id` idempotency, DB decision/migration | A complete, shippable reliability deliverable; the gate everything else depends on. Possibly folded into paid discovery/deep-dive (Chip expects a paid deep-dive first, D35). |
| **CP-1 — Intake + trust-ramp foundation** (Stages A + C tier 1–2) | channel normalizers (start with the highest-value channel), confidence-gated **assisted** send + dashboard | Visible value (auto-send starts helping) while liability stays bounded to assisted/safe-slice tiers. |
| **CP-2 — Order seam** (Stage D) | `Order` object, quote→order transition, acceptance signal (per DECISION #1) | The quote→order boundary is a discrete, demoable milestone. Depends on CP-0. |
| **CP-3 — Fulfillment** (Stage E) | pick-list + shop ping (paper/phone) | Directly removes the hand-typed-sales-order pain (D41 ROI). |
| **CP-4 — Inventory** (Stage F) | stock table + min/max + auto-reorder | Net-new subsystem; gated on DECISION #2. Naturally last (needs E's decrement). |

Add-on calculators (composite, add-on options, pricing tiers) are **parallel, independently priceable**
increments that can attach to CP-1/CP-2 rather than forming their own checkpoint, since they don't fire
irreversible actions.

**Sequencing note for the proposal:** CP-0 first is non-negotiable engineering-wise; frame it to Chip as the
foundation the auto-flows sit on, not as overhead. The trust ramp (CP-1) advancing through its tiers is the
"by next year we're not even checking it" outcome Chip wants — sold as a gate that relaxes, never a flip (I80).

---

## 7. Summary

- **The engine is the existing pipeline extended, not a rebuild.** Stages A/B exist (C39); C/D/E/F are the
  net-new work, in that dependency order.
- **Milestone 1 (infra hardening) is a hard gate** because every auto-flow amplifies the current
  at-least-once duplicate hazard from "a stray draft" to "an irreversible customer/shop/supplier action."
- **The trust ramp (Stage C) is the highest-liability piece** and is designed as a confidence-gated
  dashboard that relaxes in tiers with a kill-switch — the reframe of the RAG OCR gate (C38).
- **The order seam is `routes.py:2707`**, fed by the immutable `QuoteVersion.line_items_snapshot` (C40).
- **Product-identity debt** (bare strings, no FKs) is a co-requisite of the order/inventory stages, not an
  optional cleanup.
- **Six decisions are Chip's/Devin's, not the architect's** (§5) — surfaced, not invented.
- **Commercials:** fixed-price month-sized stages, ~$6k/mo, 25%-down/net-45, state-don't-sell — a suggested
  CP-0…CP-4 mapping as a starting point, not a committed quote.

*Deliverable for epic 347. Canon proposals filed for the durable architectural decisions.*
