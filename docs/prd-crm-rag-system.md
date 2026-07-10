# PRD: CRM / RAG Knowledge System

**Date:** 2026-07-01
**Source:** Chip Edwards meeting at 6468 N Yale Ave, Jul 1 2026
**Priority:** 1 of 2 (Chip's words: "easier to get funding")
**Status:** Scoping

---

## Problem

Allan Edwards has decades of institutional knowledge — customer histories, past projects, credit relationships, pricing context — scattered across a shared drive nobody can navigate, a QuickBooks installation, and individual employees' memory. When a veteran like Chip can mentally connect a current prospect to a job done 23 years ago, that's a competitive advantage. Nobody else can do it, and it dies when they leave.

They've tried ACT, Salesforce, NetSuite, and HubSpot. All failed for the same reason: the tools required too much admin overhead from the people who actually produce revenue. Chip's rule: **you cannot give the guys who produce work more work just so overhead can have better records.**

The result: QuickBooks only. No CRM. No searchable history. Salespeople cold-call accounts the company has worked with for years.

---

## Vision

A centralized knowledge base — a RAG (Retrieval-Augmented Generation) system — that ingests everything Allan Edwards knows about its customers, past jobs, and relationships, and makes that knowledge available through a natural-language interface. Any employee, from anywhere, can ask a question and get a useful answer.

Chip's own description: *"I'm sitting in a Walmart parking lot. I look somebody up and I can see our entire history with them — communication, all our past work, people inside the industry who know them."*

---

## Primary Use Cases

### 1. Warm Lead Lookup (highest priority)
A salesperson preparing to visit Bartlesville types: *"Who should I see this week?"* The system returns a list of contacts in that area with relationship context — last contact date, what was sold, who inside the company knows them, past job history.

This replaces cold calling with warm outreach. Allan Edwards has worked with nearly everyone in their market. New salespeople don't know that.

### 2. Customer History on Demand
Before any sales call, an employee can pull up a full relationship summary: every quote, every job, every contact, every communication thread. Chip's example: a credit application arrives with three trade references. Instead of filing those references, the system mines them — *"Here are three companies you now have a warm intro to. Here's what we know about them."*

### 3. Institutional Memory Capture
Voice memos, meeting notes, emails, and field observations all feed into the system. In the first release, anyone in the field can open the ask page on a phone, tap the mic, and dictate an update into the record without a separate transcription stack. Richer push-to-talk field workflow can come later. The goal is that Chip's 20+ years of pattern recognition becomes partially transferable.

### 4. Relationship Context for New Hires
A new salesperson should be able to onboard to the company's full relationship graph. Not just a contact list — the actual history of why a relationship exists, who built it, and what's been done together.

---

## Data Sources (input)

- **Historical job records** — already digital, on their shared drive; currently unsearchable
- **QuickBooks** — customer records, transaction history
- **Credit applications and trade references** — currently going into a drawer
- **Email history** — inbound/outbound customer communications
- **Voice capture** — Stage 1 phone dictation into the ask page, with richer field push-to-talk later if it earns it
- **Quote history** — from the existing Allan Edwards quote tool (already structured)

---

## Key Constraints

- Must not require production employees to do extra data entry. Any capture mechanism must be passive or trivially easy (push-to-talk, auto-pull from existing systems).
- Admin overhead must be near-zero. The Salesforce failure happened because it required a full-time employee to manage.
- The existing quote tool's database (contacts, addresses, quote history) is the natural seed — start there.
- This system is for non-veteran users first. It should systematize the common 80% of relationship lookups and memory capture, not attempt to model every edge case before launch.

---

## What "Done" Looks Like (Phase 1)

A working RAG interface that can answer questions about the company's customer base and quote history. A new salesperson can get warm leads and relationship history, then dictate a note back into the company record from a phone. Seeded with:
- All existing quote tool data (contacts, line items, quote history)
- Historical records migrated from the shared drive (scope TBD — Chip's question: *"how far back do you want to go?"*)

An employee types or speaks a question. The system returns a relevant, sourced answer. No training required.

---

## Open Questions

1. How far back does Chip want to go on historical data migration? (He asked this himself — no answer yet.)
2. When does a dedicated field app become worth doing beyond the Stage 1 phone web UI?
3. Does this live inside the existing quote tool, or is it a separate product?
4. Who are the day-one users? Chip + salespeople, or Chip only at first?

---

## Notes from Chip

- *"Jamie has so much gold she doesn't even realize it — it just goes into spaces and dies."* (Jamie = veteran employee with 25 years of institutional knowledge)
- *"I'm really good at tying it together on the fly because I've been here so long. But we can't teach that to somebody else."*
- *"I'm going to be in Bartlesville this week — who should I see?"* — called this a concrete entry point, said we could build something just around that chunk as a starting point.
