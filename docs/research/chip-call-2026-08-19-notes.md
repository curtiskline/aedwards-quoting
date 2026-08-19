# Chip call — 2026-08-19 (proposal approval) — structured notes

**Provenance / quality:** Devin ran mic transcription during a phone call with Chip on
2026-08-19 (16:39–16:43). Source: `~/transcripts/2026-08-19_1639_mic.md`, preserved
verbatim at the bottom of this file. It is **noisy single-mic auto-caption** — both sides
are muddled into one channel, and there is a hallucination run in the middle
(`I have. I have. I have…`). **Do NOT quote it back to Chip.** Treat everything below as
paraphrase/notes, not verbatim. Same caution as the 2026-08-10 onsite transcripts.

Context: this call is Chip verbally approving the back-end automation engine proposal
(docs/proposal-amazon-engine.html, sent 2026-08-19). Ties to epic 347 / D51.

---

## 1. Decision confirmed
- Chip approved the proposal and wants to get started.
- Timeline expectation set by Devin: **not tomorrow — roughly two to three weeks**, built
  iteratively with **back-and-forth** as Devin sketches the whole system and hits gaps
  ("we've been doing that anyway on the last one; just setting expectations").

## 2. Trust ramp — Chip's own framing (reinforces D51/I80)
- Chip understands the engine is taught over time: "you're going to be teaching the thing
  how to do the work."
- His mental model of the ramp: watch for the things that go right and **increase a weight**
  as confidence builds; get comfortable before relaxing the gate. Consistent with the
  confidence-gated send stage already in the design ("a gate that relaxes, not a flip").

## 3. NEW scope surfaced on this call (NOT in the approved proposal)
This is the substantive new material. The approved proposal covers the transaction engine
(intake → auto-quote → send gate → order → fulfillment → inventory). On the call Chip added
a **customer-facing document/output layer** as a future direction:

- **Base engine stays transaction-level** (confirmed): "strictly on the transaction level —
  here's the price, here's how much, what you're buying." That is the simpler thing to
  build first, and matches the approved scope.
- **Customers increasingly want output in THEIR formats / THEIR systems.** Customers want
  the quotes/invoices delivered as their own documents and, in some cases, **uploaded
  directly into the customer's own system (SAP was named)** — "now it's like SAP, I have to
  log into your system; you do all the work for them." Chip has been resisting this pressure
  ("I've been fighting this stuff") but sees his customer base "all going that way."
- **Metal-purchase paperwork** ("the paperwork the metal comes with") is a related category
  to build toward later — the docs that accompany a steel transaction.
- **Variability across customers:** open question whether this is a one-off for a couple of
  customers or every customer has different needs. Chip's guidance: **"take on those
  challenges as they come," plan for the worst case** (actually uploading into a customer's
  system).
- **Likely mechanism (Chip + Devin agreed direction):** for pushing into an external
  customer system that has no clean API, use a **"running agent" — a browser-automation
  handoff (Claude with browser permissions)** that logs into the customer's system and does
  the entry, for cases that aren't easy to repeat exactly. This is an explicit
  browser-driving-agent direction, not a normal integration.

## 4. How this affects the build we're starting
- Does **not** change Checkpoint 1 (foundation/infra hardening) or the near-term transaction
  engine — build that first as proposed.
- **Keep the output side pluggable.** The customer-document/customer-system-upload layer is a
  DEFERRED, per-customer expansion on top of the transaction engine. Design the quote/order
  output so a customer-specific document renderer and an external-system push (API or
  browser-automation agent) can attach later without reworking the core.
- Surfaces a future work item: a browser-automation "running agent" for customer-system
  entry (e.g. SAP) where no API exists.

---

## Raw transcript (verbatim, NOISY — do not quote to Chip)

```
# Transcription

- Started: 2026-08-19T16:39:13-05:00
- Mode: mic
- Host: devin-MS-7B98

[16:39:17] Yeah.
[16:39:19] Well?
[16:39:21] Watch up to it in the northeast.
[16:39:38] Let's try to sell shit. So I can tell you. Nice. Well, good. How are you feeling about this proposal? You want to get started on it? Yeah, okay. I do. So
[16:39:41] There's a few things that I mean.
[16:39:47] It's fine. It's like what I was kind of curious though, I think you spelled it out like
[16:40:18] As we're doing human interactions, like you're going to be teaching the thing how to do the work. Yeah, and that's that's correct. And at first, it's just going to be more or less, I'm going to be looking for certain things to go right, like looking for all of the things that go right, and adding up an additional weight, or basically increasing a weight. And I think we still have to at first, you know, really get comfortable with it. it. it down.
[16:40:46] But yeah, there's no reason we can't get to the point where we're comfortable with it but you know, we'll just we'll just keep working with it. Does that sound okay to you? Yeah, cool. Can you have it done tomorrow or? I probably can't have it done tomorrow, but I probably have it done within a, you know, two or three weeks.
[16:40:47] on 10th year.
[16:40:49] So part of
[16:41:01] What has happened is our customers have gotten to where the quotes need they want them to be in their documents and I've been fighting this stuff they keep wanting all this shit attitude and like that's not happening.
[16:41:09] I'm talking, this is strictly on the transaction level, like here's the price, here's how much it's in its spirit, what you're buying.
[16:41:19] and that stuff that's going to be a simpler thing. Okay. I think a lot of the other stuff is it's going to be like the
[16:41:25] When you buy a piece of metal there's a lot of paperwork to deal with it. Okay.
[16:41:42] That's their buying paperwork. The middle comes with it is the best way I can explain it. Okay. We can work on all that stuff. I think you keep building on it. This is what I want you to keep that mine as we're building. Like right now I need to be able to.
[16:42:12] just start selling easier like not true everything like it's magic. I got you well it and there's definitely going to be some back and forth we're going to have to do here as soon as I get to start to sketching out the whole system and I start to fill in the gaps I'm going to run into questions and I'm so it's going to have to be kind of a back and forth on this one. We've been doing that anyway on the last one but just to set expectations to set expectations. [hallucination run removed]
[16:42:35] I don't want to stop them. It's like some of these are customers or get a little more complicated like we're typically we just we would just make an invoice and send it to them. Mm-hmm. And that's now it's like the SAP I should have to log into your system. You do all the work for them. Oh, I see.
[16:42:56] do you think that different customers will have different needs? But it's, anyway, I'm just, there are a few things that are new options that are weird. And then our company, our customers are all going that way. So, just Do we have to, do you think that'll be a one-off type of thing for a couple of customers? Or do you think that different customers will have different needs?
[16:43:12] Well, let's just take on those challenges as they come. Yeah. Yeah. And I think we just, we plan for the worst. Okay. And deal for the worst, like saying we're actually uploading into their system. It would be a lot of
[16:43:44] Yeah, we'll probably have to have like, for something like that, you would have to have kind of a running agent. And just like you said, you can get Claude to do it. If it's going to be something that's not easy to repeat exactly the way. Yeah, absolutely. That we might just have to have a process that automatically hands it off like you would to Claude, and give it the permissions to open the browser, whatever you need to be able to get in there.
<!-- transcription stopped: 2026-08-19T16:43:44-05:00 -->
```
