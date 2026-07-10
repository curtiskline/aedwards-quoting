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

**Restructured 2026-07-10 (task 276):** the original 7-stage plan totaled $42–56k — 5–6× the
$9k quote system, a comparison that reads as gouging from Chip's chair. Collapsed to **three
stages at ~$6k each (~$18–19k total, ~$6k/month cadence)**, matching Devin's original retainer
framing while keeping Chip's fixed-price-stage model. Two structural facts made this possible:
the email lane's engine is already live in prod (backfill is compute + wiring, not a month of
build), and OCR/parsing/document-fetch now runs on 918's local hardware (no per-page fees, no
metered cloud OCR). The old 7-lane breakdown below the table survives as internal work
structure inside the three stages.

**Reframed 2026-07-10 (task 283):** client-facing framing is now **one fixed-price deliverable
($19,000) with three usage checkpoints** — Chip asked for a fixed-price project; the per-stage
"use it, then decide" opt-out framing undercut confidence and is gone. The three stages below
survive as internal delivery structure and billing milestones ($6k / $7k / $6k, billed as each
checkpoint lands). Usable from checkpoint 1; full deployment at checkpoint 3.

| # | Stage | Delivered (absorbs) | Primary cost driver |
| --- | --- | --- | --- |
| 1 | **Load & Ask** | Server stood up; quote-tool DB loaded; **full held email/RFQ archive backfilled** through the already-live classify→extract→link lane (monitor.py, RFQ classifier, parser.py, db_writer.py), facts wired into the knowledge layer; relationship map; `ask.allanedwards.io` with dictation + "near me"; query telemetry from day one. Success here is a new salesperson getting warm leads and relationship history fast, then dictating a note back into the company record from a phone. Also rolls in the quote-tool delete fix Chip asked for by email 2026-07-09 (no separate charge). *(absorbs old S1 + S3)* | Build, not volume — task-235 seed layer; email engine proven |
| 2 | **The Whole Drive** | All 5 libraries (~71K files): text-PDF + Office extraction; OCR lane with confidence gate for scanned material; dedup + relevance filter before load; `.eml` lane + `.zip` recursive unpack; **CAD `.sldprt` = metadata-only**, media = optional transcription; coverage view. **Starts only after SharePoint rebuild settles (D14).** *(absorbs old S4 + S5 + S7)* | Dedup/relevance filtering + quality review; OCR compute is local/bounded |
| 3 | **The Field Team** | Salespeople added as users — users/roles already exist in the tool, the new build is the admin screen to manage them; long-lived phone sessions, daily coverage dashboard, weekly "what we learned" digest; credit-app trade-reference mining → "warm intro" facts. *(absorbs old S2; old S6 QuickBooks moved to Phase 3)* | Dashboard + digest + credit-app mining; user admin is light — auth/roles infra exists |
| — | **Phase 3 — Voice capture & the books** | Richer field workflow beyond the Stage 1 phone web page: dedicated push-to-talk → transcription → same ingestion pipeline; later, native mobile app. QuickBooks connector (customer/transaction records) — moved here 2026-07-10: Chip mentioned QuickBooks once, descriptively (M2 ~7:52), never as an ask | Reuses existing voice-input pipeline; not priced here |

Ordering rationale: Stage 1 proves answer quality on trusted data (structured DB + the proven
email lane) **and** gives the first workflow Chip explicitly validated: a new salesperson asking
who to see in Bartlesville, getting warm leads plus relationship history, and speaking a note
back into the record from a phone. Stage 2 is the bulk-ingestion expansion path, gated behind
the SharePoint rebuild (rebuild → ingest → freeze). Stage 3 rolls it out to the field more
broadly. Every stage widens the *same* questions; none is a throwaway.

This system is for non-veteran users first. It should systematize the common 80% of
relationship lookups and memory capture, not attempt to model every edge case before launch.

---

## What drives the price (so each stage can be quoted honestly)

1. **OCR share of the PDF estate** — the single biggest unknown. Born-digital PDFs extract for
   near-nothing; scanned ones carry OCR compute + quality-review cost. A sample crawl, or the
   Graph pull, tightens this before Stage 2's price locks.
2. **Volume after dedup/relevance filtering** — 71K raw is not 71K worth ingesting. A large
   share is duplicates, superseded revisions, and non-knowledge assets (branding, templates).
   Filtering down first is real work but shrinks every downstream lane.
3. **Legacy-format normalization** — `.xls`/`.doc` and `.zip`/`.eml` add per-lane parser work.
4. **Per-lane pipeline build** — email, archive, CAD each is a discrete build; they remain
   separate internal lanes even though they now ship grouped into three stages. (QuickBooks
   connector lane moved to unpriced Phase 3, 2026-07-10 — not a Chip ask.)
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

- **Historical depth** — RESOLVED 2026-07-10: Chip wants all of it. Full history, priced in at
  the $19k single price (top of the old range). The volume valve is gone — absorbed by pricing.
- **Day-one users** — RESOLVED 2026-07-10: Chip first; field team joins at checkpoint 3.
- **Exact counts/sizes** — deferred; Graph `Sites.Read.All` request to Jackson Technical when
  we want precision inside a stage (recommended before locking Stage 2's price).
- **Data handling (stated plainly, 2026-07-10)** — the proposal no longer claims documents
  never leave 918-controlled infrastructure: source data syncs from SharePoint, and
  classification/answer synthesis calls commercial AI APIs. What holds: OCR + embeddings run
  on 918's local hardware; the system and its DB live on the 918-managed droplet. Per I80,
  Chip-facing docs state this rather than sell privacy absolutes.

---

## Locked pricing (source of truth)

Locked by Devin on **2026-07-10** (task 283) — supersedes the same-day three-price lock (task
276) and the 2026-07-09 seven-stage lock. **Single fixed price: $19,000**, billed per
checkpoint ($6,000 / $7,000 / $6,000). Rationale: Chip asked for a fixed-price project — one
number, one deliverable; $19k is the top of the old $18–19k range because "all the historical
data" resolves checkpoint 2's volume at maximum, which removes the pre-stage re-lock lever;
checkpoint billing keeps the ~$6k/month cash rhythm Devin originally floated and avoids
carrying the build unpaid through a D14 (SharePoint rebuild) stall. Keep pricing here so later
edits flow from one place.

| # | Checkpoint | Delivered | Billed at checkpoint |
| --- | --- | --- | --- |
| 1 | **Load & Ask** | Server stood up; quote-tool DB loaded; full held email/RFQ archive backfilled via the already-live lane, facts wired into the knowledge layer; relationship map; `ask.allanedwards.io` with dictation + "near me"; query telemetry from day one; quote-delete fix rolled in | **$6,000** (includes ingestion inference) |
| 2 | **The Whole Drive** | All 5 libraries (~71K files), full historical depth: born-digital extraction + local OCR lane with confidence gate; dedup + relevance filter; `.eml`/`.zip` lanes; CAD metadata-only; coverage view. Schedule-gated behind SharePoint rebuild settling (D14) | **$7,000** (includes OCR + inference) |
| 3 | **The Field Team** | Salespeople added as users + user-admin screen (auth/roles infra exists), phone sessions, daily dashboard, weekly digest; credit-app trade-reference mining. Full system deployed | **$6,000** |
| | **Total** | One fixed-price deliverable | **$19,000** |
| — | **Phase 3 — Voice capture & the books** | Field push-to-talk → transcription → same ingestion pipeline; later, native mobile app; QuickBooks connector | Later; indicative **$6,000-$10,000** only, **not priced in this proposal** |

*2026-07-10 (task 281): QuickBooks moved out of Stage 3 to Phase 3 — transcript check showed it
was never a Chip ask (one descriptive mention, M2 ~7:52). Stage 3 stays $6,000 fixed: the month
is filled by the dashboard, digest, credit-app mining, and user-admin screen.*

**Margin note (internal):** at these numbers 918 absorbs OCR/review risk on local hardware and
LLM leverage. The old protection levers (historical-depth volume valve, pre-stage price
re-lock) are gone with the single price — absorbed by pricing checkpoint 2 at the top of the
old range ($7k). Remaining levers if the drive runs heavy: aggressive dedup/relevance
filtering before load, and the coverage dashboard keeping review targeted rather than
exhaustive. D14 remains a schedule gate, not a price contingency.

### Monthly

**$200/mo** covering hosting (16 GB DigitalOcean droplet + backups + storage at roughly $130),
ongoing query inference (typically under $5), and a light upkeep cushion.

### Pricing notes that must carry into the client proposal

1. **Fixed-price discipline** — Stage 2's range becomes a locked fixed price agreed before the
   stage begins. That keeps Chip's preferred model intact: scoped, fixed-price chunks rather
   than open-ended billing — one range, one re-lock, instead of four.
2. **What drives Stage 2's range** — OCR share, dedup/relevance filtering, legacy-format
   normalization, and ingestion inference are the real cost drivers. Ingestion inference stays
   in the low hundreds with a cheap Flash-tier model even at full volume.
   **OCR path is open-weight, on local 918 hardware** (proven in axon: `pdftotext` for
   born-digital + vision fallback, decision axon:D24). Born-digital PDFs extract free via
   `pdftotext`; scanned material runs through open OCR models on Devin's local machines
   (Tesseract/PaddleOCR → Surya/Marker, escalating to a local VLM such as olmOCR / Qwen2.5-VL
   only when the confidence gate demands it). No per-page fees, bounded compute, and documents
   never go to an outside indexing service. Gemini-vision is retained only as an optional
   last-resort fallback. Stage 2's range is driven by dedup/filtering + confidence-gate +
   quality-review effort, not OCR fees.
3. **SharePoint rebuild timing + structure freeze** (decision D14) — Stage 2 firms to a final
   fixed price once the SharePoint rebuild request settles. Jackson Technical / Nick Beals
   flagged on 2026-07-09 that a from-scratch rebuild may happen; the right framing is disciplined
   scoping, not risk. **Hard sequencing constraint:** any rebuild happens FIRST, we ingest against
   the settled structure, then the structure is frozen. Ingestion stores references back to source
   documents; if we key on Microsoft Graph stable `driveItem` IDs, intra-site moves/renames survive,
   but a from-scratch rebuild reassigns every ID and breaks all stored links. Rebuild-then-build-then-freeze.
   (Follow-up sent to Nick 2026-07-09, in-thread "Re: Read only".)
4. **Privacy promise, updated wording** — extraction runs on hardware 918 controls (the Allan
   Edwards server plus Devin's local machines for OCR/parsing); documents are **never handed to
   an outside service to be indexed**. The proposal wording was softened from "nothing scanned
   leaves the machine" to "never handed to an outside service" to stay truthful about local
   processing on 918 hardware.
