# RAG Architecture Options: Allan Edwards CRM Knowledge System

**Date:** 2026-07-02  
**Author:** PM research session  
**Purpose:** Inform architecture decision for the Allan Edwards CRM/RAG system (see PRD: docs/prd-crm-rag-system.md)

---

## The Core Problem

The highest-value query Chip described: *"I'm in a Walmart parking lot. Who should I see in Bartlesville this week?"*

This is not a document search query. It requires:
- Geographic lookup (who is in Bartlesville?)
- Relationship history (how warm is the relationship?)
- Recency (when did we last contact them?)
- Deal context (what have we sold them, what's open?)

This is a **graph query**, not a vector similarity query. The architecture choice flows from this distinction.

---

## 2026 RAG Landscape: What Changed

The field has fragmented into three distinct retrieval patterns, with the 2026 consensus being an **adaptive hybrid** that routes each query to the right backend.

### Pattern 1: Vector RAG
Embed documents as dense vectors, retrieve by cosine similarity. The classic approach.

**Best for:** Semantic search over unstructured text ("what did we say about X in this email chain?")  
**Weak at:** Multi-hop reasoning, relationship queries, recency-aware retrieval  
**Accuracy on multi-hop tasks:** ~23%

### Pattern 2: Graph RAG (Microsoft GraphRAG + derivatives)
Extract entities and relationships from documents, build a knowledge graph, traverse the graph at query time.

**Best for:** "Who knows who", cross-document synthesis, relationship reasoning  
**Accuracy on multi-hop tasks:** ~87%  
**Cost concern:** Full GraphRAG indexing is expensive (LLM calls per document). 2026 alternatives — LightRAG, LazyGraphRAG, Fast GraphRAG — cut indexing cost 50–6,000x while preserving accuracy on relationship queries.

### Pattern 3: Adaptive/Hybrid RAG
A query classifier routes each request to the cheapest retrieval path that can answer it:
- ~80% of enterprise queries → vector search (simple semantic lookup)
- ~15% → graph traversal (relationship/multi-hop)
- ~5% → agentic (multi-step planning + tool use)

This is the 2026 production consensus for enterprises with heterogeneous data.

---

## The Google Option: Gemini File Search

**What it is:** A fully managed RAG backend built into the Gemini API. You upload files (PDF, Docx, email exports, JSON, images); Google handles chunking, embedding with Gemini Embedding 2, vector indexing, retrieval, and citations.

**Cost:** Storage free. Query-time embeddings free. Initial indexing: $0.15/M tokens. Only pay Gemini model costs at query time.

**What's new (May 2026):** Multimodal support (images + text), custom metadata, page-level citations.

**Strengths:**
- Zero infrastructure. No vector DB to provision, no embedding pipeline to maintain.
- Built-in citations linking answers to source documents/pages.
- Handles the document ingestion problem (emails, PDFs, shared drive exports) with minimal code.
- Fast time to value — good for prototyping and for use cases dominated by document lookup.

**Limitations:**
- No control over chunking strategy, embedding model, or retrieval ranking.
- Cannot inspect embeddings or intermediate scores — black box retrieval.
- No relationship modeling. Knows what's *in* documents, not how entities *relate*.
- Vendor lock-in to Google's choices. If their retrieval quality degrades, you cannot tune it.
- Not well-suited to structured data (SQL tables, QuickBooks records) — designed for document corpora.

**Verdict for Allan Edwards:** Good fit for Phase 2 document ingestion (historical records, email archives, shared drive). Not a substitute for the relationship graph needed for the "Bartlesville" query.

---

## The Axon-Inspired Option

Devin has built and operated a system (Axon) over the past year that combines:
- **SQLite with vector embeddings** (no separate vector DB — SQLite-VSS/HNSW works well at this scale)
- **Canon** (structured claims, decisions, insights with confidence and provenance)
- **Knowledge graph** with access-frequency tracking for relevance reranking
- **Temporal decay** — older unverified claims lose weight; summarization commands compact stale data
- **Project-namespaced repositories** — clean separation between domains

This is effectively a production-proven Adaptive RAG implementation. The retrieval quality is good because the graph weights reflect real usage, not just semantic similarity.

**Strengths:**
- Proven at the scale and usage pattern this system needs.
- SQLite means zero infra — same deployment story as the existing quote tool.
- The relationship/graph layer directly supports the "Bartlesville" query.
- The quote tool's existing DB (contacts, quotes, customers) is a natural seed — structured data already present.
- Temporal decay and summarization solve the "information overload / recency vs relevance" problem that flat MD files don't.

**Limitations:**
- Requires building and maintaining the system for a client.
- The user interface for salespeople needs to be built — Axon's interface is agent-facing, not human-facing.
- Voice capture pipeline needs to be wired in (though Devin has done this in other projects).

---

## Lessons from a Year of Axon in Production (2026-07-02 review)

Before committing to this architecture, we audited Axon itself — its canon/engram/knowledge-graph internals, retrieval pipeline code, and a year of recorded insights, postmortems, and failure telemetry. The findings below are what that audit adds to (or changes about) the design. References like I277/K2411 are Axon canon node IDs.

### What Axon proved (keep these)

1. **The curated structured layer carries almost all the value.** Axon's canon is 16MB (202 active claims); its engram embedding store is 2.3GB (176k chunks). Canon answers most real questions. Structure-per-byte wins by orders of magnitude — the strongest possible endorsement of building the structured seed (Phase 1) before document embeddings (Phase 2).
2. **Hybrid keyword + semantic search works.** BM25+semantic fusion beat either alone (K268); short keyword queries beat natural language 68% vs 52% hit@3 (I279). SQLite handles all of it at multi-GB scale.
3. **Curation gates catch a lot.** 39% of agent-proposed canon claims (97 of 251) were rejected as wrong, duplicate, or stale. Assume a comparable extraction error rate in any autonomous pipeline.
4. **Knowledge rots; the system needs a metabolism.** Axon was forced to add: a 30-day verification horizon, staleness views, dead-evidence checks, a supersession ledger (facts deprecated, never deleted), and maintenance agents. None of this was in the original design; all of it became load-bearing.

### What failed in Axon (defend against these)

1. **The learned graph never became a knowledge graph (I277).** Axon's KG edges were learned from usage (`co_retrieved` co-occurrence — 190k of 469k edges). Its own insight is blunt: *"Knowledge graph became a popularity reranker instead of a navigable knowledge structure... score() is collaborative filtering, not graph walking."* Graph re-ranking demoted relevant results in favor of well-connected ones and was made opt-in, i.e. effectively turned off (K2411).
   **Crucial distinction:** this condemns *emergent* graphs, not graph-first retrieval. The Allan Edwards graph is *declarative* — customer→contact, quote→customer, customer→location are real foreign keys and extracted facts. Axon's diagnosis says it needed exactly that. **Design rule: every edge is a declared or extracted relationship with a type and provenance; no edge is ever inferred from usage patterns. Traversal is query-scoped (start from entities the query names); never rank by connectivity** — a big customer with 500 quotes would otherwise swamp every query the way Axon's hub nodes did.
2. **Score blending across heterogeneous backends is where hybrid systems rot.** Axon merges RRF scores (~0.016 for a rank-one hit), raw BM25 (5–15+), and match counts into one sort with an ad-hoc cap and a floor that silently drops single-ranker hits — acknowledged in code comments as fiat, not principle. **Design rule: route each query to one backend (the adaptive routing already in this doc); never blend scores across backends.** That blend is the tempting "improvement" that degraded Axon.
3. **Embedding infrastructure churns.** Axon went hash → sentence-transformers (384-dim) with an OpenAI path (256-dim); needed a daemon to fix 4.7s cold starts; purged 630k stale cache entries; and a dimension mismatch makes cosine silently return 0 — semantic search collapses to keyword-only with no warning. **Design rule: tag every stored embedding with model+dim from day one; treat reindexing as a routine operation; alert when semantic scores flatline.**
4. **Ingestion gaps are silent.** One postmortem found 142 completed tasks with zero sessions captured (I205) — the pipeline was broken and nothing noticed. **Design rule: the dashboard must show coverage (received vs classified vs extracted vs embedded), not just activity, with alerting on gaps.** A silently dropped email is a customer fact that never exists.
5. **Degraded modes go unnoticed.** Axon's synthesis layer can fail (missing API key, daemon down) and quietly falls back to raw dumps — with an error message that names the wrong provider. **Design rule: retrieval returns records + citations as the primary payload; LLM synthesis is decoration on top; degraded modes are loud on the dashboard.**
6. **Retrieval quality needs telemetry from day one.** Axon logs every query, its results, and whether results were used; nightly analysis classifies thrashing, recurring gaps, and true gaps into auto-created fix tasks. This loop is what made Axon's problems visible enough to fix. Build the query log in Phase 1.
7. **Complexity accrues.** Axon grew four stores, three embedding backends, two scoring blends, and five maintenance roles. Its most load-bearing component (canon) is its simplest; its most complex (the learned graph) got turned off. **For a client system, deliberately build less: the relational DB, one embeddings table, one link table, one router.**

### The autonomy tension, resolved

Chip's Amazon-model "no human approvals" appears to conflict with Axon's 39% proposal-rejection rate. It doesn't kill autonomy: Axon's proposals were free-text claims from heterogeneous agents, while ETL extraction against a known schema is far more constrained. But the design must assume a meaningful extraction error rate:
- **Confidence thresholds** — low-confidence extractions land in a *flagged review queue on the dashboard* (flagged, not blocking; consistent with the "I flag" model).
- **Supersession semantics** — wrong facts get corrected via supersede-and-deprecate, never silently overwritten.
- **Provenance on every extracted fact** — source document, extracting agent, timestamp (Axon's evidence-table pattern; also feeds the audit log).
Autonomy for the pipeline, auditability for the facts.

---

## SQLite as Vector Store: Is It Viable?

Yes, at this scale. SQLite-Vector and SQLite-VSS (with HNSW indexing) support embedding storage and similarity queries within a standard SQLite file. No separate service, no Pinecone/Weaviate account, no infra overhead.

The quote tool already uses SQLite in production. The RAG layer can live in the same file or a sibling file on the same droplet. This keeps the deployment story simple and avoids vendor dependency for the retrieval layer.

At Allan Edwards' data volume (thousands of customers, tens of thousands of quotes/emails over time), SQLite handles this comfortably. A dedicated vector DB (Pinecone, Qdrant, Weaviate) would be premature optimization.

---

## The TrueVi Comparison

TrueVi uses a separate vector store with a local tokenizer/embedder. The embeddings are generated locally (no API cost per document), stored in the vector DB, and queried for similarity at runtime. This is a solid, well-understood pattern.

**Key difference from Axon:** TrueVi's vector store has no graph layer and no temporal decay. It answers "find documents similar to this query" well, but doesn't inherently support relationship reasoning or relevance that improves over time with usage.

For Allan Edwards, the relationship layer is more important than document similarity. A salesperson asking "who should I call" needs relationship context, not just "here are documents that mention Bartlesville."

The local embedder approach (TrueVi's model) remains attractive for cost and privacy — no per-token API cost for indexing, data never leaves the server. This is worth preserving regardless of which retrieval architecture is chosen.

---

## The Agentic ETL / Progressive Schema Layer

This is the architectural idea that distinguishes this system from a conventional RAG deployment, and it was the missing piece in the 2007 "Next Gen Data" vision (the technology simply didn't exist then).

### The Core Insight

Relational databases are still the right home for structured data. The problem is that most of a company's information arrives in unstructured form (emails, PDFs, voice memos, shared drive documents), and the costly, failure-prone step has always been getting a human to extract structure from it and enter it into a database.

The resolution: **agents do the extraction, autonomously and continuously.**

### How It Works

Every document that enters the system goes through an ingestion pipeline:

1. **Classify** — an agent reads the document and determines what kind of information it contains. Is this a credit application? A quote follow-up? A meeting note with product specs? A trade reference?

2. **Match to schema** — for each entity or relationship found, check whether it maps to an existing table. A customer name found in an email maps to the `customers` table. A trade reference found in a credit application may have no home yet.

3. **Extend schema if needed** — if the entity type has no existing table, the agent creates one. It infers column names and types from the document, creates the migration, and runs it. No human approves this. The schema evolves the same way an e-commerce order pipeline evolves: autonomously, with the process driving the structure.

4. **Write structured records** — extract field values and insert into the appropriate table(s). Normalize where possible (e.g. "Microsoft Corporation", "MSFT", "Microsoft" all resolve to the same customer record).

5. **Embed the source document** — always store the raw document as a vector embedding regardless of how much structure was extracted. The embedding is the fallback for queries that don't hit structured tables.

6. **Create graph edges** — link the source document to every structured record it produced or referenced. A credit application PDF becomes an edge from the document node to three customer nodes (the trade references it named). Those customers are now warm leads retrievable from a "who should I call" query.

### The Operational Model: Amazon, Not Salesforce

The system runs like an order fulfillment pipeline, not a CRM. Documents flow in, processes execute, records are written, schema extends — with no human in the loop on individual items. The human interface is a **dashboard** showing what's been ingested, what schema changes were made, what records were created, and flagging anything that failed classification or had low confidence.

Chip's constraint — *you cannot give the guys who produce revenue more work just so overhead can have better records* — is fully honored. No one fills out a form. No one approves a migration. The system observes, structures, and learns.

This is also why every previous CRM implementation at Allan Edwards failed (ACT, Salesforce, NetSuite, HubSpot): they required humans to maintain structure. This system inverts that — it extracts structure from what humans already produce.

### Schema Evolution Principles

Since agents extend the schema autonomously, a few guardrails keep it from sprawling:

- **Conservative column typing** — prefer text over strongly-typed columns on first creation; normalize types in a later consolidation pass
- **Merge before create** — before creating a new table, check semantic similarity to existing tables (is "vendor_contact" the same as "contact"?); merge if threshold exceeded
- **Audit log** — every schema change is written to a `schema_evolution_log` table with the source document, the agent that made the change, and the SQL that ran. Visible on the dashboard.
- **Soft deletes, never hard** — no agent drops a column or table; only humans can shrink the schema
- **Provenance on every extracted fact** — source document, extracting agent, timestamp, confidence (Axon evidence-table pattern)
- **Fact lifecycle** — `last_verified_at` on extracted facts, staleness views, supersede-and-deprecate instead of overwrite; CRM facts age worse than code facts (people change jobs)
- **Coverage monitoring** — dashboard shows received vs classified vs extracted vs embedded counts with gap alerting (Axon lesson 4)

---

## Recommended Architecture

A three-layer hybrid, built incrementally:

### Layer 1: Structured Seed (Phase 1 — build first)
Use the existing quote tool's DB as the knowledge base. Contacts, customers, quotes, line items, addresses are already structured and queryable. Add:
- A relationship layer linking customers → contacts → quotes → locations. **The Phase 1 "graph" is mostly the relational schema itself** — SQL joins over existing FKs plus one thin typed `edges` table for links that don't fit the schema (and, in Phase 2, document→entity links). Do not build a graph engine (see Axon lesson 1: declarative edges only, query-scoped traversal, never rank by connectivity).
- Geographic tagging on customer records
- Simple query interface (web or voice-to-text) that answers "who should I see near X"
- **A query telemetry log from day one** (query, route taken, results, result used) — Axon lesson 6
- **Embedding hygiene**: model+dim tag on every stored embedding — Axon lesson 3

This delivers the highest-value use case (Bartlesville query) with the least new infrastructure. The data already exists.

### Layer 2: Agentic ETL + Document Corpus (Phase 2)
The ingestion pipeline described above, running continuously against:
- Email archives
- Shared drive documents
- Credit applications and trade references
- QuickBooks exports
- Historical job records

Each document is classified, structured records are extracted and written, schema is extended as needed, source document is embedded and graph-linked. Two sub-options for the embedding backend:
- **Google Gemini File Search** for fast time-to-value, low maintenance, good citation quality
- **Local embedder + SQLite-VSS** for cost control and data privacy (mirrors TrueVi approach)

The choice depends on data sensitivity and Chip's appetite for Google dependency. Both are viable.

### Layer 3: Voice Capture (Phase 3)
Field push-to-talk → transcription → automatic ingestion into the knowledge base. Devin has built this pipeline for other projects (voice-input project). Wire it in after the core retrieval layer is proven. Voice memos go through the same ingestion pipeline as documents — classify, extract, embed, link.

---

## Open Questions (for scoping conversation with Chip)

1. **How far back on historical data?** (Chip asked this himself.) The answer determines Phase 2 scope and cost.
2. **Data privacy:** Is Chip comfortable with documents going through Google's API, or does local embedding matter?
3. **Day-one users:** Chip only, or salespeople from the start? This determines how polished the query interface needs to be.
4. **QuickBooks integration:** Export only (periodic CSV dump) or live API? Live API is harder but keeps data fresh.
5. **Voice on mobile:** Is push-to-talk on a phone the primary capture method, or is a web form acceptable for Phase 1?

---

## Sources

- [Introducing the File Search Tool in Gemini API](https://blog.google/innovation-and-ai/technology/developers-tools/file-search-gemini-api/)
- [Gemini API File Search is now multimodal](https://blog.google/innovation-and-ai/technology/developers-tools/expanded-gemini-api-file-search-multimodal-rag/)
- [Why Google's File Search could displace DIY RAG stacks](https://venturebeat.com/ai/why-googles-file-search-could-displace-diy-rag-stacks-in-the-enterprise/)
- [Microsoft GraphRAG](https://microsoft.github.io/graphrag/)
- [GraphRAG in 2026: Practical Buyer's Guide](https://medium.com/@tongbing00/graphrag-in-2026-a-practical-buyers-guide-to-knowledge-graph-augmented-rag-43e5e72d522d)
- [Vector vs. Graph RAG: How to Actually Architect Your AI Memory](https://optimumpartners.com/insight/vector-vs-graph-rag-how-to-actually-architect-your-ai-memory/)
- [GraphRAG vs Vector RAG: Knowledge Graph AI Guide 2026](https://www.buildmvpfast.com/blog/graphrag-vs-vector-rag-knowledge-graph-ai-2026)
- [Building a RAG on SQLite](https://blog.sqlite.ai/building-a-rag-on-sqlite)
- [Hybrid RAG: Graph RAG vs. Vector RAG for Enterprise](https://medium.com/@ajaysrinivasan87/graph-rag-vs-vector-rag-choosing-the-right-architecture-for-enterprise-use-cases-f3f6205f959f)
- [RAG Techniques Compared: Best Practices 2026](https://blog.starmorph.com/blog/rag-techniques-compared-best-practices-guide)
