# CRM / RAG Knowledge System — Delivery Scope

**Date:** 2026-07-08
**Prepared by:** 918 Software · Devin
**Status:** Internal scoping draft — feeds the client-facing proposal pricing
**Task:** 269

---

## What this is

The client proposal (`proposal-crm-phase1.html`) deliberately stopped short of pricing —
its first step was *"Let me see the data,"* because volume and format are what drive the cost
and guessing would either pad the price or shortchange the work. That data survey is now done
(`docs/research/drive-survey.md`, task 244). This document converts the survey into a **staged,
priceable scope** on top of the stage framework the proposal already established.

**Builds on (do not restate in full):**
- `docs/prd-crm-rag-system.md` — problem, vision, use cases, constraints
- `docs/proposal-crm-phase1.html` — client-facing stage framework + tone
- `docs/research/rag-architecture-options.md` — architecture + Axon production lessons
- `docs/research/drive-survey.md` — the corpus survey this scope is grounded in

Canon: relationship layer / design rules `allanedwards:C21–C23`, `K236`, `K238`.

---

## The corpus we are actually scoping (from the survey)

| Fact | Number | Scope implication |
| --- | ---: | --- |
| Total files, 5 main libraries | ~71,380 | Real ingestion project, not a weekend load |
| Sales, Engineering & Customer Service | 32,231 (45%) | Highest-priority ingestion target — most quote/customer relevant |
| Fabrication & Warehouse | 18,526 | Ops docs; second wave |
| Leadership Team / Document Control / Finance | 8,122 / 7,608 / 4,855 | Mixed value; phased |
| Dominant format | **PDF** | Split born-digital vs scanned — **OCR is the primary cost driver** |
| Secondary | Excel, Word (incl. legacy .xls/.doc) | Mostly clean; legacy needs normalization |
| Long tail | image, mp4/mov, zip, eml, `.sldprt` (CAD) | Separate lanes / carve-outs |

**Known unknown (accepted for now):** SharePoint Search only indexes ~1–12% per library, so
exact by-extension counts and byte-sizes are not yet measured. That precision needs Microsoft
Graph `Sites.Read.All` (Azure admin consent, Jackson Technical) — **deferred per Devin.** The
survey is enough to fix the *shape* of the pipeline and stage the work; the Graph pull refines
the *numbers* inside a stage before it is priced.

---

## Staged delivery

The proposal's principle holds: **month-sized stages, each a complete working deliverable at a
fixed price agreed before it starts.** The customer sees each stage running, uses it, and
decides whether the next is worth it. No long contract.

Stages 1–2 are unchanged from the proposal (structured seed + field team). The survey's new
contribution is **making the ingestion stages concrete** — previously "later stages, scope TBD."

| # | Stage | Delivered | Primary cost driver |
| --- | --- | --- | --- |
| 1 | **Load & Ask** (structured seed) | Server stood up; quote-tool DB + held quote/RFQ archive loaded; relationship map; `ask.allanedwards.io` with dictation + "near me"; query telemetry log from day one | Build, not volume — this is the task-235 seed layer |
| 2 | **The Field Team** | Salesperson logins/roles, long-lived phone sessions, daily coverage dashboard, weekly "what we learned" digest, refinement from real queries | Build + light iteration |
| 3 | **Email/RFQ ingestion lane** | Extends the quote tool's already-live classify→extract→link lane (monitor.py inbox poll, RFQ classifier, parser.py, db_writer.py customer link+dedup): backfill the held archive, wire extracted facts into the ask/knowledge system (not just quote drafts), add confidence gate + coverage view | **Easiest, high value** — engine already proven in prod; start ingestion here |
| 4 | **Shared drive — Tier 1 (born-digital)** | Text-PDF + Office extraction across Sales/Eng/CS + Document Control (quotes, invoices, MTRs, packing slips); dedup + relevance filter before load | Office/legacy normalization; dedup |
| 5 | **Shared drive — Tier 2 (OCR lane)** | OCR fallback for scanned/image-heavy PDFs + images, routed by a confidence gate; the survey's biggest cost line | **OCR volume × quality variance** |
| 6 | **QuickBooks + credit applications** | Customer/transaction records; credit-app trade-reference mining → "warm intro" facts | Connector build; PDF/OCR for scanned apps |
| 7 | **Remaining libraries + carve-outs** | Fabrication & Warehouse, Leadership, Finance remainder; email `.eml` lane, `.zip` recursive unpack; **CAD `.sldprt` = metadata-only**, media = optional transcription | Per-lane build; carve-outs kept cheap |
| — | **Phase 3 — Voice capture** | Field push-to-talk → transcription → same ingestion pipeline; later, native mobile app | Reuses existing voice-input pipeline |

Ordering rationale: prove answer quality on trusted structured data first (1–2), then ingest
in ascending difficulty and descending certainty — email (cleanest) → born-digital drive →
OCR → external systems → long-tail. Every stage widens the *same* questions; none is a
throwaway.

---

## What drives the price (so each stage can be quoted honestly)

1. **OCR share of the PDF estate** — the single biggest unknown. Born-digital PDFs extract for
   near-nothing; scanned ones carry OCR compute + quality-review cost. Sampling in Stage 4/5,
   or the Graph pull, tightens this before Stage 5 is priced.
2. **Volume after dedup/relevance filtering** — 71K raw is not 71K worth ingesting. A large
   share is duplicates, superseded revisions, and non-knowledge assets (branding, templates).
   Filtering down first is real work but shrinks every downstream lane.
3. **Legacy-format normalization** — `.xls`/`.doc` and `.zip`/`.eml` add per-lane parser work.
4. **Per-lane pipeline build** — email, archive, QuickBooks connector, CAD each is a discrete
   build, which is exactly why they are separate stages.
5. **Hosting (monthly)** — the knowledge server needs more RAM than the quote tool's (local
   embedding model + vector store), so it is a larger droplet. Sized once the corpus that
   actually gets embedded is known; billed monthly, same discipline as the quote tool.

**Guardrails carried from the Axon production review (non-negotiable, they protect margin and
trust):** coverage dashboard (received vs classified vs extracted vs embedded, with gap
alerting — silent ingestion gaps were Axon's top failure); provenance + confidence on every
extracted fact; declarative edges only, query-scoped traversal; records+citations first, LLM
synthesis optional; query telemetry from day one. See `rag-architecture-options.md`.

---

## Assumptions & open items before this becomes a quote

- **Historical depth** — Chip's own open question, *"how far back do you want to go?"* Directly
  sets Stage 4–7 volume. Needs a Chip answer.
- **Day-one users** — Chip only, or Chip + salespeople? Affects Stage 2 sizing.
- **Exact counts/sizes** — deferred; Graph `Sites.Read.All` request to Jackson Technical when
  we want precision inside a stage (recommended before Stage 5, the OCR lane).
- **Voice in Phase 1?** — proposal treats it as dictation-into-ask (Stage 1) with full
  push-to-talk field capture as Phase 3. Confirm that split with Chip.
- **Data stays private** — all extraction runs on Allan Edwards's own server; documents are
  never handed to an outside service to index. (Already promised in the proposal; holds here.)

---

## Locked pricing (source of truth)

These are the numbers locked by Devin on 2026-07-09 for the proposal update. Keep pricing here
so later edits flow from one place.

| # | Stage | Delivered | Price |
| --- | --- | --- | --- |
| 1 | **Load & Ask** (structured seed) | Server stood up; quote-tool DB + held quote/RFQ archive loaded; relationship map; `ask.allanedwards.io` with dictation + "near me"; query telemetry log from day one | **$6,000 fixed** |
| 2 | **The Field Team** | Salesperson logins/roles, long-lived phone sessions, daily coverage dashboard, weekly "what we learned" digest, refinement from real queries | **$5,000 fixed** |
| 3 | **Email/RFQ ingestion lane** | Extends the quote tool's already-live classify→extract→link lane: backfill the held archive, wire extracted facts into the ask/knowledge system, add confidence gate + coverage view. Price held — remaining work (archive backfill, knowledge-side linking, guardrails) is genuine | **$6,000 fixed** (includes ingestion inference) |
| 4 | **Shared drive — Tier 1 (born-digital)** | Text-PDF + Office extraction across Sales/Eng/CS + Document Control (quotes, invoices, MTRs, packing slips); dedup + relevance filter before load | **$6,000-$9,000** (includes inference) |
| 5 | **Shared drive — Tier 2 (OCR lane)** | OCR fallback for scanned/image-heavy PDFs + images, routed by a confidence gate; the survey's biggest cost line | **$8,000-$14,000** (includes OCR + inference) |
| 6 | **QuickBooks + credit applications** | Customer/transaction records; credit-app trade-reference mining → "warm intro" facts | **$6,000-$8,000** |
| 7 | **Remaining libraries + carve-outs** | Fabrication & Warehouse, Leadership, Finance remainder; email `.eml` lane, `.zip` recursive unpack; **CAD `.sldprt` = metadata-only**, media = optional transcription | **$5,000-$8,000** |
| — | **Phase 3 — Voice capture** | Field push-to-talk → transcription → same ingestion pipeline; later, native mobile app | Later; indicative **$6,000-$10,000** only, **not priced in this proposal** |

### Monthly

**$200/mo** covering hosting (16 GB DigitalOcean droplet + backups + storage at roughly $130),
ongoing query inference (typically under $5), and a light upkeep cushion.

### Pricing notes that must carry into the client proposal

1. **Fixed-price discipline** — every range in Stages 4–7 becomes a locked fixed price agreed
   before that stage begins. That keeps Chip's preferred model intact: scoped, fixed-price
   chunks rather than open-ended billing.
2. **What drives the higher ranges** — OCR share, dedup/relevance filtering, legacy-format
   normalization, and ingestion inference are the real cost drivers. Ingestion inference should
   be named explicitly alongside OCR and parser work; with a cheap Flash-tier model it stays in
   the low hundreds even in a heavy stage.
   **OCR path is open-weight, on-prem first** (proven in axon: `pdftotext` for born-digital +
   Claude-vision fallback, decision axon:D24). Born-digital PDFs extract free via `pdftotext`;
   scanned material runs through open OCR models in-house (Tesseract/PaddleOCR → Surya/Marker,
   escalating to a local VLM such as olmOCR / Qwen2.5-VL on a burst GPU droplet only when a
   confidence gate demands it). This shifts OCR cost from per-page API fees to bounded compute
   and, more importantly, keeps scanned documents on Allan Edwards's own server. Gemini-vision
   is retained only as an optional last-resort fallback (still cheaper per page than Document
   AI). The $8–14k Stage 5 range is driven by lane-build + confidence-gate + quality-review
   effort, not per-page OCR fees.
3. **SharePoint rebuild timing + structure freeze** (decision D14) — Stage 4–7 numbers firm to a
   final fixed price once the SharePoint rebuild request settles. Jackson Technical / Nick Beals
   flagged on 2026-07-09 that a from-scratch rebuild may happen; the right framing is disciplined
   scoping, not risk. **Hard sequencing constraint:** any rebuild happens FIRST, we ingest against
   the settled structure, then the structure is frozen. Ingestion stores references back to source
   documents; if we key on Microsoft Graph stable `driveItem` IDs, intra-site moves/renames survive,
   but a from-scratch rebuild reassigns every ID and breaks all stored links. Rebuild-then-build-then-freeze.
   (Follow-up sent to Nick 2026-07-09, in-thread "Re: Read only".)
4. **Privacy promise holds** — all extraction runs on Allan Edwards's own server. Documents are
   never handed to an outside service to be indexed.
