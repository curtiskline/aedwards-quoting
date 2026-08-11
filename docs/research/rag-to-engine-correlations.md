# RAG/CRM → Amazon-Engine Correlations

**Date:** 2026-08-11
**Task:** 364 (parent: epic 347)
**Type:** READ-ONLY research. No source files modified.
**Question:** The original RAG/CRM proposal "got a little off track." What, if anything, carries
over into the back-end automation engine (epic 347 / D51)? Map feeds-in / off-track /
reusable-retire-reframe, with the pricing/scope constraints that bound the engine's commercials.

> One-line answer: **The engine and the RAG proposal share a spine — the same agentic-ETL
> pipeline (I54) and the same "no work for producers" constraint — but their centers of gravity
> are opposite.** RAG was a *knowledge-retrieval* system ("mine our historical info"); the engine
> is a *transactional-automation* system (intake → quote → order → shop → reorder). At the
> 2026-08-10 onsite Chip ranked the ordering/automation vision **above** the retrieval vision, so
> the transactional half of the proposal is what feeds forward and the whole-drive-ingestion half
> is what "got off track."

---

## Sources read (enumerated)

Canon: D51, D50, D41, D35, D17, D16, D13, I124, I54, I79, I68, I80; task K235.
Docs: `docs/research/onsite-2026-08-10-structured-notes.md`, `docs/scope-crm-rag.md`,
`docs/prd-crm-rag-system.md`, `docs/research/chip-alignment-gap-analysis.md`.
Deep queries: `amazon engine epic`, `research correlations between` (pre-fetched).

---

## The reframe in one picture

| | Original RAG/CRM proposal | Amazon engine (347 / D51) |
| --- | --- | --- |
| **Center of gravity** | Retrieval — "mine buried company memory" | Transaction — intake→quote→order→shop→reorder |
| **Flagship use case** | "Parking lot / who should I see in Bartlesville" (PRD §Primary Use Case 1) | Kill the 40–60 hrs/week of hand-typed sales orders (D51) |
| **Where the ROI is** | Warm leads for non-veteran salespeople | Routing around the one entrenched employee (D41, onsite §4) |
| **Customer-facing surface** | none (internal ask page) | none near-term; storefront **deferred** not dropped (D51) |
| **Operational model** | "Amazon fulfillment pipeline, not Salesforce CRM" (I54) | literally that pipeline (D50/D51) |

The proposal's own operational model (**I54**, written 2026-07) already said "Amazon fulfillment
pipeline, not Salesforce CRM." The 2026-08-10 onsite (**D50→D51**) confirmed that metaphor was the
*actual product* — not a design analogy for a retrieval tool. So the "off track" was never the
architecture; it was **which half of the proposal got the emphasis and the price tag.**

---

## 1. What FEEDS IN (direct carryover into the engine)

Ranked by how load-bearing each is for the engine.

1. **The agentic-ETL pattern — the engine's spine (I54).** Autonomous classify → extract
   structured entities → extend schema → write records → embed source → graph-link, humans see a
   *dashboard not an approval queue*. This is verbatim the engine's operating model (D50/D51:
   intake → auto-quote → auto-order → ping shop → reorder, "I don't want a damn person touching
   it"). **Carry wholesale.** Schema-evolution rules (merge-before-create, conservative typing,
   audit log, never drop columns) carry too.

2. **The already-live intake→quote lane is engine stages 1–2, already built.** The
   classify→extract→link email/RFQ lane (monitor.py, RFQ classifier, parser.py, db_writer.py),
   which the RAG Stage 1 merely *reused* for archive backfill (scope-crm-rag.md Stage 1; K235), is
   in engine terms the **channel-agnostic intake + auto-quote** stages. Onsite §5 confirms:
   "Auto-quote — EXISTS. This is the *only* stage built today." The RAG proposal already leaned on
   this engine; the epic just renames it stage 1–2 of a longer pipeline and builds stages 3–5 on
   top (auto-order, shop ping, min/max reorder).

3. **Phone/RFQ capture as first-class intake (D17, I68).** RAG made phone capture a first-class
   Stage 1 deliverable (not deferred). The engine needs channel-agnostic intake (call/fax/email/
   RFQ → structured order, onsite §5.1). **Reframe, don't retire:** the RAG "ask page + built-in
   dictation" becomes *one channel* of the intake funnel. Critically, **I68 already specced the
   gateway** — input classification/routing ("is this a question or a note? where does it file?"),
   modeled on the axon console/gateway. That gateway generalizes directly to "is this an RFQ, a
   reorder, a note?" — the engine's front door.

4. **The "no work for producers" constraint (D41, PRD constraints, gap-analysis want #6).** Every
   prior CRM (ACT/Salesforce/NetSuite/HubSpot) failed because it *added* admin work to revenue
   producers. D41/D51 make this a hard constraint of the engine: pings must **replace** work, not
   add it; the engine ABSORBS the work. Same rule, same source, unchanged.

5. **The "systematize the 80%, non-Jamie" rule (I79).** RAG's scoping rule ("build for
   non-veterans, systematize the common 80%") maps *exactly* onto the onsite pricing fact: **80%
   standard commodity is formula-priced, 20% custom stays by hand** (onsite §2). This is the same
   rule stated first as a retrieval principle, now as an automation boundary — automate the 80%,
   leave the 20% manual. **Carry as the engine's automation boundary.**

6. **Axon production guardrails — reusable engineering discipline (rag-architecture-options.md;
   K235 design rules).** Coverage dashboard (received vs classified vs extracted vs embedded, with
   gap alerting), provenance + confidence on every fact, records+citations first / LLM synthesis
   optional, declarative-edges-only, query-scoped traversal, telemetry from day one. Two of these
   are load-bearing for the engine's biggest open risk:
   - **"Records + citations first, LLM synthesis optional" + "human sees a dashboard, not an
     approval queue"** is the design skeleton for the **auto-quote trust ramp** (D51 open risk:
     Chip wants priced quotes auto-sent with no human review "by next year"; one mispriced
     auto-quote goes straight to a customer). The phased human-gate that relaxes over time IS a
     confidence-gated dashboard — the exact pattern already designed for RAG's OCR confidence gate.
   - **Coverage dashboard** repurposes into fulfillment observability (received vs quoted vs
     ordered vs shipped vs reordered).

---

## 2. What GOT OFF TRACK (RAG-centric, now secondary or out of near-term scope)

The onsite explicitly re-ranked: Chip put ordering/automation **above** "mine our historical
info." So the retrieval-heavy half of the proposal drops to secondary.

1. **"The Whole Drive" — 71K files / 5 libraries / OCR lanes / CAD metadata / dedup
   (scope-crm-rag.md Stage 2, the $7k checkpoint).** This is the single largest chunk of the RAG
   proposal by build effort and price, and it is a **pure knowledge-retrieval** effort with no
   transactional ROI. The engine's ROI is removing 40–60 hrs/week of hand-typed orders (D51), not
   making a terabyte of PDFs searchable. **Demote to a later, optional companion track.** (Note:
   already schedule-gated behind the SharePoint rebuild D14 — it was never near-term anyway.)

2. **"Parking lot / Bartlesville / who should I see" warm-lead lookup (PRD Use Case 1, RAG
   flagship).** Still genuinely useful and rides on the *same* customer DB, but it is a CRM/
   relationship feature, no longer the headline. **Secondary feature, not a headline deliverable.**

3. **Credit-app trade-reference mining → warm intros (PRD Use Case 2; scope Stage 3).**
   Retrieval-flavored relationship enrichment. Secondary; ride it on the same DB later.

4. **918-generated Stage 2/3 expansion the gap analysis already flagged (drift item #3):** weekly
   "what we learned" digest, salesperson roles + long-lived phone sessions, all-five-libraries as a
   priced deliverable. Not Chip-stated asks; further from his language than the core workflow.
   **Secondary at best; re-justify against the engine before committing.**

5. **QuickBooks connector.** Already deprioritized to unpriced Phase 3 (D16, scope 2026-07-10 note)
   because it was never a Chip ask (one descriptive mention). Stays out.

6. **The framing "Phase 1 = a query interface grounded in structured data" (K235).** The engine
   inverts the deliverable: the primary output is *transaction automation*; the query interface is
   a byproduct that comes nearly free once the relational + edge layer exists. **Reframe the
   headline, keep the layer.**

---

## 3. REUSABLE vs RETIRE vs REFRAME (specific, cited)

| Item | Verdict | Source | Note |
| --- | --- | --- | --- |
| Agentic-ETL pipeline (classify→extract→link→write, dashboard not queue) | **REUSABLE** | I54, K235 | The engine's operating model verbatim |
| Live email/RFQ intake→quote lane (monitor/parser/db_writer) | **REUSABLE** | scope Stage 1, onsite §5.2 | = engine stages 1–2, already built |
| I68 gateway (input classification/routing) | **REUSABLE / REFRAME** | I68 | "question vs note" generalizes to "RFQ vs reorder vs note" |
| Phone/dictation capture (ask page) | **REFRAME** | D17, I68 | Becomes *one channel* of channel-agnostic intake |
| "No work for producers" constraint | **REUSABLE** | D41, PRD | Unchanged hard constraint |
| "Systematize 80% / non-Jamie" rule | **REUSABLE / REFRAME** | I79, onsite §2 | Retrieval principle → automation boundary (80% formula, 20% manual) |
| Axon guardrails (coverage dashboard, provenance, records-first, telemetry) | **REUSABLE** | K235, rag-arch-options | Coverage dashboard → fulfillment observability |
| Confidence-gate + dashboard pattern | **REUSABLE / REFRAME** | K235 (OCR gate) | Becomes the auto-quote **trust ramp** (D51 open risk) |
| Relationship/edge layer + vector embeddings on customers | **REUSABLE (demoted)** | K235 components 1–2 | Powers the secondary "who should I see" lookup |
| "Whole Drive" 71K-file ingestion, OCR/CAD/dedup lanes | **RETIRE from near-term / REFRAME as optional later track** | scope Stage 2 | No transactional ROI; D14-gated regardless |
| Warm-lead "Bartlesville" lookup as flagship | **REFRAME (demote)** | PRD UC1 | Secondary feature, not headline |
| Credit-app trade-ref mining, "what we learned" digest, roles/sessions | **RETIRE from near-term** | scope Stage 3, gap-analysis drift #3 | Not Chip-ranked; re-justify later |
| QuickBooks connector | **RETIRE (already out)** | D16, PRD | Never a Chip ask |
| "Phase 1 = query interface" as the deliverable frame | **REFRAME** | K235 | Headline is automation; query is a byproduct |

---

## 4. Where the pricing/scope commitments (D13/D16/D17) constrain the engine

- **The commercial *pattern* carries; the RAG *number* does not.** D13 (7-stage ~$6k anchor) →
  D16 (3-stage ~$18–19k) → the $19k single fixed price with 3 billing checkpoints
  (scope-crm-rag.md "Locked pricing", task 283). What carries into engine scoping is **Chip's
  preferred model, not the total**: month-sized stages, each a complete working deliverable at a
  **fixed price agreed before the stage starts**, ~$6k/month cadence. The $19k covered a different
  scope (retrieval + whole-drive ingestion) and does not bind the engine's total.

- **D17 constrains intake, not price.** Phone/RFQ capture must be **first-class, not deferred**.
  For the engine this hardens into: the intake front-door (whatever channel) is a stage-1
  deliverable, never a "later" luxury.

- **Reallocation signal.** In the RAG budget the biggest priced chunk (~$7k, checkpoint 2) was
  whole-drive ingestion — now demoted (§2.1). That build-and-spend capacity should **reallocate**
  toward the engine's genuinely new stages: auto-order, shop ping/fulfillment, and min/max
  reorder (onsite §5.3–5.5), which have no proposal coverage yet.

- **Live commercial terms (D35) still govern the relationship.** No proposal is currently owed;
  ball is in Chip's court; task 235 is gated only on Chip re-engaging + Devin green-light. Chip's
  last stated terms: **25% down, remainder net-45** (not net-15). Curtis is no longer advising.
  Any engine engagement inherits these terms and the "paid discovery/deep-dive first" expectation
  (both Chip's offer and Curtis's parting advice). The onsite (D41→D51) effectively satisfied the
  D35 re-engagement gate — Chip re-engaged in person on 2026-08-10.

- **Tone constraint on any engine-facing doc (I80).** State, don't sell — no privacy/automation
  absolutes the architecture contradicts. Directly relevant to how the auto-quote trust ramp is
  described to Chip: promise a gate that relaxes, not "it never makes mistakes."

---

## 5. Recommendation — what to carry into engine scoping

1. **Lead with the transaction pipeline, not retrieval.** Scope the engine as intake → auto-quote
   (built) → auto-order → shop ping → min/max reorder. That is where Chip put the ROI and the
   ranking (D51, onsite §5).
2. **Adopt the agentic-ETL pattern (I54) and the Axon guardrails wholesale** — they were designed
   for exactly this pipeline. Make the coverage dashboard the fulfillment-observability surface.
3. **Design the auto-quote trust ramp as a confidence-gated dashboard** (reuse the OCR-gate +
   records-first patterns). This is the highest-liability open risk (D51): one mispriced auto-quote
   reaches a customer. It needs a phased gate that relaxes, not a flip.
4. **Reframe RAG's phone capture + I68 gateway as the channel-agnostic intake front-door.** Keep it
   first-class (D17); generalize "question vs note" routing to "RFQ vs reorder vs note."
5. **Demote whole-drive ingestion and the warm-lead lookup to a later, optional companion track**
   that rides the same DB. Do not price them into the engine's near-term scope. Reallocate that
   budget to the new auto-order/shop/reorder stages.
6. **Carry the commercial model, not the RAG total:** fixed-price month-sized stages, ~$6k/month
   cadence, 25%-down / net-45 terms (D16/D35), state-don't-sell tone (I80). Expect a paid
   discovery/deep-dive as the first engine engagement.

**Open items the engine must resolve that RAG did not** (from onsite §8, for the scoping task, not
this doc): order-vs-quote boundary (who/what confirms an accepted quote becomes an order);
inventory source-of-truth for min/max; customer-tiering "pain-in-the-ass tax" policy; composite
"how much to buy" calculator scope; auto-responder liability gate.
