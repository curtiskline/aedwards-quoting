# Chip Alignment Gap Analysis

**Date:** 2026-07-10
**Task:** 278
**Question:** Does the current CRM/RAG proposal match what Chip Edwards actually asked for in the two Yale Ave meetings?

This report focuses on the CRM/RAG-relevant parts of the two transcripts. Both meetings also contain quote-tool-specific discussion (product taxonomy, quote-vs-bid language, field reporting ideas) that is adjacent but not central to the current CRM/RAG proposal.

## 1. Chip's stated wants

### 1. Central relationship lookup from the field

Chip's opening use case was not abstract AI. It was immediate field retrieval:

- Meeting 2, ~00:29-01:07: "I would like to have a central... if I'm sitting in a ... parking lot, I can look up ... and find out everything our companies know [about] them, communication... as well as people who... work with one of them, people inside the industry." (`6468 N Yale Ave 2.json`)
- Meeting 2, ~10:18-10:37: "I'm ready to get going with a new customer... tell me the history... have we worked with this person before?... I'm going to be in Bartlesville this week. Who should I see?" (`6468 N Yale Ave 2.json`)

Interpretation: the primary user experience is a field rep or Chip asking one question and getting relationship history plus who to contact.

### 2. Phone-based capture is first-class, not optional

Chip described capture as part of the same first-use workflow, not a later add-on:

- Meeting 2, ~01:20-01:49: "be able to walk out of there and just push the talk... Updated, boom... all your notes get pulled in." (`6468 N Yale Ave 2.json`)
- Meeting 2, ~01:31-01:46: "you can provide them from your phone anywhere you are conveniently, anybody who's in the field, it goes into the system, it gets written into the history." (`6468 N Yale Ave 2.json`)
- Meeting 3, ~20:55-21:36: "So do you have to use the email, or can you open it up and then build one in there?... Can we throw that in?" (`6468 N Yale Ave 3.json`)

Devin's follow-up instruction on 2026-07-10 sharpens the implementation intent: simple web page, chat/input button, phone-native dictation, no custom transcription stack required for Phase 1. That matches the transcript emphasis on convenience and immediacy.

### 3. Preserve and mine buried company memory

Chip repeatedly described dormant information that currently dies in drawers, drives, and veteran employees' heads:

- Meeting 2, ~02:26-03:10: credit applications and trade references are "amazing data"... "usually it just gets there and goes in a drawer"... "I can then start calling the people... What more can we work on?" (`6468 N Yale Ave 2.json`)
- Meeting 2, ~03:50-04:45: "I'm really good at tying it together on the fly because I've been here for so long"... "we did a project here 23 years ago"... but that history is not transferable. (`6468 N Yale Ave 2.json`)
- Meeting 3, ~02:06-03:11: "Something's only custom once... if we could upload that and teach our tool... that 20% of things that are custom, every year it gets smaller." (`6468 N Yale Ave 3.json`)

Interpretation: the system should convert past work and ad hoc knowledge into reusable organizational memory.

### 4. New salespeople should get warm intros, not cold-call blind

Chip explicitly validated the "new salesman" use case:

- Meeting 2, ~08:52-09:49: "Say I'm hiring a new salesman... that is exactly the interface that we would need... we have worked with everybody... salespeople think these are their cold calling... no, we have worked with these guys... use that as a relationship... I'm new here, but we worked with you... six years ago." (`6468 N Yale Ave 2.json`)

Interpretation: onboarding and lead warming are not secondary. They are one of the clearest value cases Chip named.

### 5. Shared-drive chaos is a real problem, but the ask is "usable knowledge," not "ingest everything"

Chip did complain hard about the shared drive:

- Meeting 2, ~04:59-05:56: "my biggest frustration is that shared drive... now it's just a terabyte of shit. And nobody knows where it is... you need a single source of truth for everything." (`6468 N Yale Ave 2.json`)

But the pain statement is about retrieval and trust, not a requirement that Phase 1 must ingest the whole drive before value appears.

### 6. Do not create admin work for producers

This is one of Chip's clearest constraints:

- Meeting 2, ~06:48-07:35: "you cannot give the guys that do work more work just so you can have better records." (`6468 N Yale Ave 2.json`)
- Meeting 2, ~07:08-07:35: "we're not going to make the guys that actually do work... You do more work, not them." (`6468 N Yale Ave 2.json`)
- Meeting 2, ~06:02-06:24: off-the-shelf tools failed partly because Salesforce effectively required "a full-time employee to manage it." (`6468 N Yale Ave 2.json`)

Interpretation: passive ingestion and very low-friction capture are mandatory.

### 7. Build for non-experts and systematize the 80%, not every edge case

Meeting 3 added an important scoping constraint:

- Meeting 3, ~00:43-01:14: "I'm trying to build it for non-Jamie's... Jamie has 25 years of knowledge"... "we have tried to shove 100% of everything into every single tool"... "We need 80%... we need to systematize." (`6468 N Yale Ave 3.json`)

Interpretation: the system should capture the common, high-value cases and be usable by non-veterans. It should not overfit to the most complex edge cases.

## 2. Coverage map

| Chip want / constraint | Transcript evidence | Proposal / scope / PRD / task-235 coverage | Assessment |
| --- | --- | --- | --- |
| Field lookup: "parking lot" / "Bartlesville" / "who should I see?" | M2 ~00:29-01:07, ~10:18-10:37 | Strongly covered in [docs/proposal-crm-phase1.html](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:180), [docs/prd-crm-rag-system.md](/home/devin/src/2026/allanedwards/docs/prd-crm-rag-system.md:20), [docs/research/rag-architecture-options.md](/home/devin/src/2026/allanedwards/docs/research/rag-architecture-options.md:9), and task 235. | `COVERED` |
| Customer-history lookup before a visit / new customer | M2 ~10:18-10:26 | Covered in [docs/prd-crm-rag-system.md](/home/devin/src/2026/allanedwards/docs/prd-crm-rag-system.md:35) and implicitly in proposal Stage 1 [180-196](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:180). | `COVERED` |
| Phone-based capture from the field using a simple page | M2 ~01:20-01:49; M3 ~20:55-21:36 | Present, but underweighted, in proposal bullets and Stage 1 table: [189-192](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:189), [236-240](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:236). PRD treats it as an open question at [75-80](/home/devin/src/2026/allanedwards/docs/prd-crm-rag-system.md:75). Scope still says "Confirm that split with Chip" at [109-110](/home/devin/src/2026/allanedwards/docs/scope-crm-rag.md:109). | `PARTIAL GAP` |
| Notes/capture should become company memory automatically | M2 ~01:20-01:49; ~04:10-04:24 | Covered conceptually in PRD institutional memory [38-40](/home/devin/src/2026/allanedwards/docs/prd-crm-rag-system.md:38) and proposal "It listens too" [191-192](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:191). | `PARTIAL` |
| Credit applications / trade references should become warm leads | M2 ~02:26-03:10 | Covered in PRD [35-37](/home/devin/src/2026/allanedwards/docs/prd-crm-rag-system.md:35), proposal Stage 3 / "Where This Goes" [257-258](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:257), [270-272](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:270), and scope Stage 3 [67](/home/devin/src/2026/allanedwards/docs/scope-crm-rag.md:67). | `COVERED BUT LATE` |
| New salesman onboarding / warm intros / stop cold-calling blind | M2 ~08:52-09:49 | Strongly covered in PRD [30-42](/home/devin/src/2026/allanedwards/docs/prd-crm-rag-system.md:30) and proposal Stage 1 [183-186](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:183). | `COVERED` |
| Shared-drive chaos / single source of truth | M2 ~04:59-05:56 | Covered in PRD problem statement [12-16](/home/devin/src/2026/allanedwards/docs/prd-crm-rag-system.md:12) and Stage 2 proposal/scope [244-250](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:244), [66](/home/devin/src/2026/allanedwards/docs/scope-crm-rag.md:66). | `COVERED` |
| No extra work for producers; no CRM admin burden | M2 ~06:48-07:35 | Strongly covered in PRD constraints [57-61](/home/devin/src/2026/allanedwards/docs/prd-crm-rag-system.md:57), proposal "no forms" language [176-178](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:176), and task 235 operational model. | `COVERED` |
| Build for non-Jamies; systematize the 80% | M3 ~00:43-01:14 | Weakly implied by canon and tone, but not clearly surfaced in proposal/scope/PRD. There is no explicit "80% systematize / non-expert usability" statement in the current proposal stack. | `GAP` |
| Teach the tool from prior custom work so repeated work stops being "custom" | M3 ~02:06-03:11 | Only partially reflected in broad RAG language and future ingestion vision. Not clearly named as a user-facing promise or scoping principle. | `PARTIAL GAP` |
| How far back should historical migration go? | M2 ~04:30-04:38 | Correctly preserved as an open question in PRD [75-80](/home/devin/src/2026/allanedwards/docs/prd-crm-rag-system.md:75), proposal [280-284](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:280), and scope [104-105](/home/devin/src/2026/allanedwards/docs/scope-crm-rag.md:104). | `OPEN QUESTION CORRECTLY PRESERVED` |

### Task 235 alignment note

Task 235 is materially narrower than Chip's meeting emphasis. It intentionally limits Phase 1 to structured DB data, one query interface, and no document ingestion yet. That is defensible architecturally, but it means task 235 only partially covers Chip's stronger asks around Stage 1 capture and immediate memory growth from field notes and held communications. The current proposal is broader than task 235; the risk is not just scope creep, but mismatch between implementation sequencing and what Chip expects first.

## 3. Drift items

These are things the current proposal/scope stack emphasizes more strongly than Chip did in the meetings, or sequences in a way that risks misreading his priorities.

### 1. Phone capture is described as both Stage 1 and "later"

This is the clearest mismatch.

- Proposal Stage 1 says "Talk to it" and "It listens too" ([189-192](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:189), [236-240](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:236)).
- But the later paragraph says "Further out is push-to-talk from the field" and prices that as later Phase 3 ([277-278](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:277)).
- Scope repeats the ambiguity by leaving "Voice in Phase 1?" unresolved ([109-110](/home/devin/src/2026/allanedwards/docs/scope-crm-rag.md:109)).

Chip did not present capture as a future luxury. He described it in the first few minutes as part of the core workflow.

### 2. The proposal gives more visual weight to the 71K-file / SharePoint ingestion story than to the capture workflow

The capture workflow that Chip personally described is two bullets and one clause in the Stage 1 row. The survey and stage-pricing machinery receive far more space. That is operationally useful for pricing, but it is not weighted the way Chip weighted the problem in conversation.

### 3. Some Stage 2/3 scope appears to be 918-generated expansion rather than Chip-stated requirement

The following items may be sensible, but they were not clearly requested by Chip in the meetings:

- all five libraries / 71K files as a priced deliverable
- OCR confidence gates and CAD metadata lanes
- weekly "what we learned" digest
- salesperson roles/permissions and long-lived phone sessions
- full QuickBooks connector as a named stage deliverable

These are not necessarily wrong. They are just farther from Chip's direct language than the core "parking lot lookup + easy phone capture + buried relationship memory" ask.

### 4. The proposal stack understates the "non-Jamie / 80%" scoping rule

Chip explicitly warned against trying to stuff 100% of everything into the tool. The current stack preserves the no-admin-overhead rule well, but it does not prominently preserve the separate rule that the system should be built for non-veterans and should systematize the common 80% first.

## 4. Recommended proposal changes

### 1. Highest priority: make phone capture a first-class Stage 1 deliverable everywhere

Why: transcript evidence is direct, repeated, and reinforced by Devin's 2026-07-10 note. This also resolves the open question in scope line 109: the answer is yes, simple phone capture belongs in Phase/Stage 1.

Recommended rewrite locations:

- [docs/proposal-crm-phase1.html:180](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:180) Stage 1 opening paragraph
- [docs/proposal-crm-phase1.html:235](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:235) Stage 1 table row
- [docs/proposal-crm-phase1.html:277](/home/devin/src/2026/allanedwards/docs/proposal-crm-phase1.html:277) "Further out" paragraph
- [docs/scope-crm-rag.md:109](/home/devin/src/2026/allanedwards/docs/scope-crm-rag.md:109) remove the unresolved question

Specific emphasis change:

- Keep Stage 1 as: simple phone web page, ask box, built-in dictation, speak a note into the record, note becomes searchable memory.
- Reframe the later Phase 3 item to mean richer field workflow only: native app, persistent sessions, background capture, heavier field automation.
- Do **not** let the proposal imply that basic phone capture is deferred.

Suggested direction for the proposal sentence:

> Stage 1 includes the phone workflow: open the ask page, tap the mic, dictate a question or note, and it becomes part of the company record immediately.

Then revise the later paragraph so it clearly means:

> Further out is a dedicated field app and deeper push-to-talk workflow, not the basic phone capture already included in Stage 1.

### 2. Elevate the "Bartlesville / new salesman / warm intro" workflow above the ingestion mechanics

Why: this is the clearest concrete workflow Chip validated on the spot. It is the best proof that Stage 1 solves a sales problem, not just a data problem.

Recommended emphasis change:

- Keep the Stage 1 example, but add one explicit line in the proposal that this is the first use case the release is built around.
- In the internal scope, describe Stage 1 success in terms of "new salesperson gets warm leads and relationship history" before discussing archive backfill.

### 3. Add the "non-Jamie / 80%" scoping rule to the internal scope and PRD

Why: without this, later scope can bloat around edge cases and old-file exhaust.

Recommended insertion points:

- [docs/prd-crm-rag-system.md:57](/home/devin/src/2026/allanedwards/docs/prd-crm-rag-system.md:57) under constraints
- [docs/scope-crm-rag.md:78](/home/devin/src/2026/allanedwards/docs/scope-crm-rag.md:78) under pricing/scope drivers

Suggested wording:

> This system is for non-veteran users first. It should systematize the common 80% of relationship lookups and memory capture, not attempt to model every edge case before launch.

### 4. Mark Stage 2/3 expansion items as proposal-driven extensions, not core Chip-stated asks

Why: the current stack risks presenting the whole-drive/OCR/QuickBooks expansion as if Chip asked for all of it directly. The transcripts support the problem; they do not support that exact packaging.

Recommended change:

- Keep the stages, but frame them more explicitly as the expansion path after Stage 1 proves the relationship-memory workflow.

## Bottom line

The proposal is directionally right on the main problem: customer-history retrieval, warm-intro lookup, buried company memory, and low-admin operation. The biggest misalignment is not architecture. It is emphasis and sequencing.

Chip gave phone-based capture first-class weight in the meetings. The current proposal mentions it, but not with the same force, and later wording makes it sound partly deferred. That should be corrected before the proposal advances.
