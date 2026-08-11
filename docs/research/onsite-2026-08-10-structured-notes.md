# Allan Edwards Onsite — 2026-08-10 — Structured Notes

> **Provenance / do not quote.** These notes are a structured *interpretation* of four
> single-mic auto-caption transcripts from Devin's 2026-08-10 onsite with Chip Edwards.
> The raw captions are **noisy and NOT verbatim** — phrases in quotes below are the
> transcript's approximation of what was said, not Chip's exact words. **Do not quote any
> of this back to Chip as his words.** Use it to direct internal work only.
>
> Raw sources: `~/Downloads/Aug 10 at 1-26 PM.txt` (catalog/naming), `~/Downloads/Aug 10
> at 1-40 PM.txt` (pricing + vision), `~/Downloads/Aug 10 at 1-49 PM.txt` (32s shop-floor
> aside), `~/Downloads/Aug 10 at 1-50 PM.txt` (shop floor + more vision).
>
> Related canon: **D51** (amazon-feature-backend-first-storefront-later, authoritative;
> supersedes D50), **D41** (epic seed), **I124** (Uline reference), insight
> `chip-onsite-2026-08-10`, epic **task 347**.

---

## 1. Products & catalog taxonomy (mostly 1-26 PM, physical detail from 1-50)

**Product families Chip walked through:**
- **Sleeves** — "half sole" sleeves sized to pipe OD; variants: oversleeve (fits over pipe +
  existing sleeve, still a sleeve), low-profile (`LP-`), milled / painted options.
- **Girth weld sleeves** — a weld sleeve with a bump/hump to sit over the weld; made in
  joints, rolled.
- **Compression sleeves.**
- **Bags** — geotextile weight bags (rocker/sand) to hold pipe down / keep it floating; sold
  by pipe-size range and max fill weight.
- **Composite / OmegaWrap** (internal brand; **customer-facing word is "wrap"/"composite"**) —
  Carbon, E-Glass, Resin, Putty, Magnum, Isolation Wrap, plus application tools (magnet set,
  porcupine roller, plastic wrap).
- **Backing strips** — ship *with* the compression sleeve + a paperwork packet + install
  process; also sold separately because customers lose them.
- **Pipe jacks, casing spacers, studs, end seals, sheet metal** — accessories.

**Taxonomy decisions (SINCE IMPLEMENTED — tasks 358/360/362, live on prod):**
- **Type** = the product category (sleeve, composite, bag, girth weld, compression, accessory,
  service). One editable list. "Everything is a type; part numbers are variations underneath."
- **Part Number** = the single identifier. SKU / Item Number / Part Number collapsed to one.
- **Family retired** (was a redundant second category list on the catalog page).
- Chip **self-serves the catalog** via admin (add types/products himself).
- **Descriptions don't change, only the sizes/numbers** ("12 inch pipe to 24 inch pipe... the
  words will not change just the numbers").

---

## 2. The pricing model (1-40 PM) — the linchpin fact

- **Pricing is a FORMULA, not bespoke quoting.** "The price is not changing. This is our whole
  company. The only thing that changes is how many." This is what makes automation feasible.
- Mechanic: size → pounds/foot × steel price/lb. **Steel repriced ~monthly**; could be tied to
  a **Chicago market index** ("it's just a formula").
- **80% standard commodity** (formula-priced) / **20% custom** (kept by hand). Automate the 80%.
- Big orders: Chip may "come off the price a little" to stay competitive — a manual override lane.
- **Composite is the complex case:** an engineering calculation (pipe size, wall thickness,
  defect) → "how much should I buy." Customers/engineers ask *us* how much to buy; "can all be
  automated." A candidate for a guided calculator.

---

## 3. The vision — a BACK-END automation engine (1-40, 1-50) → see D51

- **"Amazon" / Uline = an omni-channel engine, NOT a customer storefront.** One system behind
  every channel; the human rep is a thin front-end. (Authoritative: **D51**. Reference site Chip
  pulled up: **Uline / uline.com**, a B2B catalog — "order 24/7 by phone, fax, or online" — **I124**.)
- **The "e-responder" idea (Chip's words, one possible implementation, NOT the only one):**
  "everything can be forwarded to [an e-responder email] and it will just handle it."
  **IMPORTANT current-state gap:** today that email address **only GENERATES QUOTES.** The vision
  needs a **comprehensive end-to-end** back end (intake → quote → order → shop → reorder), of
  which quote generation is just the first stage.
- "I don't want a damn person touching it." Seamless once a request reaches us, whatever the channel.
- **Auto-responder trust ramp (design deliberately):** Chip wants it to *eventually* send priced
  quotes with **no human review** — "by next year we're not even checking it." Needs a phased
  human gate that relaxes over time, not a flip. One mispriced auto-quote goes straight to a customer.
- **Customer behavior is preserved on purpose** — "I don't want to make our customers do anything";
  "they're still going to come to you." Phase-out of old habits is a slow migration ("boil the frog").

---

## 4. Current internal process & the pain (1-40, 1-50) — what the engine must replace

- RFQ arrives → **printed** → goes to **one person** who **hand-types a sales order.**
- **~10–20 orders/week, ~40–60 hrs of her time**, all hand-typed.
- **A single long-tenured employee (~3 decades) is the only one who knows how to get an order/
  process through, and will not teach or change it.** This is the organizational driver: Chip is
  **routing around a person he can't change by persuasion** with software. (Treat as a hard
  constraint, per epic 347.)
- Output the shop needs: a **paper or phone pick instruction** — "put this on the truck."
- Truck drivers still need a **paper copy** for now; phone pings "down the road."

---

## 5. Automation targets — the end-to-end the engine should cover

1. **Channel-agnostic intake** — call, fax, email, RFQ → structured order. (Chip: forward a fax to
   an email that "eats" it and turns it into an order.)
2. **Auto-quote** — EXISTS (decode `parser.py` + pricing). This is the *only* stage built today.
3. **Auto-order** — turn an accepted quote/order into a pick list / shop instruction.
4. **Shop ping / fulfillment** — notify the shop ("put this on the truck"); paper now, phone later.
5. **Min/max inventory + auto-reorder** — "when we sell something, automatically send an order to
   the shop"; auto-reorder; they've cut stock (e.g. "down to 3/8 for 850"). Tracks stock, reorders.
6. **Custom add-on options** — as checkboxes / upcharges (e.g. PO-number stencil on pipe = ~$50);
   **itemize, don't bury in cost** (customers resent hidden markup); "add to our part list... the
   machine starts to learn."
7. **Customer-tiering / "pain-in-the-ass tax"** — model which customers pay more; "teach the machine
   which customers... get the pain in the ass tax." (Handle with care — pricing/relationship policy.)
8. **Composite engineering calculator** — guided "how much to buy" for wraps (defect/size/wall).

---

## 6. Physical / operational facts (1-50, 1-49)

- Compression sleeve **ships as a kit**: sleeve + backing strip + paperwork packet + install process.
- Bags = **rocker/sand** to hold pipe down / keep floating.
- Girth welds have a **bump** to clear the weld; made in **joints, rolled**.
- **Standard lengths:** mostly **30 ft**, some 15 ft (a buyer may not know 2×15 vs 1×30); cordless all 30 ft.
- **No custom cutting** — everything is standard lengths; you buy whole pieces/pallets (e.g. want 20,
  get a pallet of 21, charged for 21).
- A **customer portal exists**: customer types in a steel/heat number and pulls up docs (the "life of
  a steel" number lives on the product). A Shopify-based site exists but is **immature**.
- 1-49 PM (32s aside): walking the floor — "cut and bend metal," raw metal comes in, stacks.

---

## 7. Decisions & artifacts already produced from this onsite

- **Terminology** (Part Number / Type / Composite) — approved by Chip ("PERFECT"), **shipped to prod**
  (tasks 358/360/362; decode prompt now sources types live from the editable table).
- **D51** — engine-first, storefront-later (supersedes D50).
- **I124** — Uline as the reference example.
- Insight `chip-onsite-2026-08-10` — the compressed capture.

---

## 8. Open questions / risks to resolve before/while scoping the engine

- **Auto-responder trust ramp** — the human-in-the-loop gate and how/when it relaxes. Liability of an
  auto-sent mispriced quote.
- **Order vs. quote boundary** — today the pipeline stops at "quote generated." Where does an accepted
  quote become an order, and who/what confirms it? (Chip's e-responder "just handles it" glosses this.)
- **Inventory source of truth** — min/max + auto-reorder needs a stock system; does one exist, or is it
  new? What feeds it?
- **Customer-tiering policy** — the "pain-in-the-ass tax" is a pricing/relationship decision, not just code.
- **Composite calculator scope** — how much of the engineering "how much to buy" to automate vs. assist.
- **Storefront** — deferred (D51); confirm it stays out of near-term scope.
- Earlier still-open items that may intersect: metric rounding policy (D38), backing-strip strip length
  assumption (D40).

---

*This document is the internal reference for scoping the Amazon-Feature back-end engine (epic 347).
When directing research or implementation, point workers here plus D51 / I124 / the raw transcripts.*
