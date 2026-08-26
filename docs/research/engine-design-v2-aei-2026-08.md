# Engine design v2 — reconciling the corrected AEI model (2026-08)

**Task 438** (parent: epic 347). DESIGN doc — nothing here is implemented by this task.
Reconciles the engine as built (CP-1..CP-5b, `docs/proposal-amazon-engine.html`) and the
original staged design (`docs/research/amazon-engine-end-to-end-design.md`, task 366)
with the corrected understanding of Chip's business from the 2026-08-25 answers:
**D72** (AEI buys/resells; supersedes D71), **I148** (PO/AFE signal, min/max-0
non-stocked parts, unsigned pick sheets, kit/weight-based shipments), **I146** (the
shipment-confirmation signal, still open), **I145** (what is actually built).

---

## 1. The corrected operating model (one page)

**AE Inc (AEI) — the company this tool serves — is a DISTRIBUTOR, not a manufacturer
(D72, supersedes D71).** AE MFG is a *separate company* that makes/bends steel. AEI
buys finished goods from AE MFG *"just like we buy from every other person"* and
resells them. The shop-tour raw-steel operation recorded in D71 belongs to AE MFG,
not AEI.

What that means for every part of the engine:

- **Replenishment = a PURCHASE ORDER to a vendor** (AE MFG or another supplier) —
  never an in-house "Make" work order. The restock sheet's "Make {qty}" framing is
  wrong for AEI.
- **AEI stocks finished goods only.** The two-tier raw-vs-finished inventory model
  proposed in I147 does **not** apply to AEI — there is no raw-material tier to track,
  no BOM explosion, no "a sale decrements both finished and raw." I147's modes
  collapse, for AEI, into exactly two:
  1. **Stocked resale** — AEI keeps it on the shelf (fewer than 50 SKUs total, I143);
     min/max reorder = vendor PO.
  2. **Non-stocked / order-to-order** — AEI never stocks it; min/max = 0; an incoming
     customer order directly triggers a build/purchase order to the vendor carrying
     the customer's details (drop-ship / make-to-order) (I148.2).
- **The customer go-ahead is a document reference, not a click:** customers signal
  "go" with a **PO number or AFE number** (I148.1). Capture it at accept; carry it to
  the invoice.
- **A shipment is a KIT/BOX of multiple components** (e.g. 4 rolls carbon + 2 cans
  part-A resin + 2 cans part-B); the BOL is **weight-based**, not itemized (I148.4).
- **Pick sheets are not signed** (I148.3) — they are just the pack manifest. The
  signed-sheet shipment-confirmation idea is dead; the shipped signal is still an open
  design question (I146, §5 below).
- Chip remains the sole operator (I146 context); the D41 constraint holds everywhere:
  the engine must absorb work, never add admin.

**What survives untouched:** the entire quote-side pipeline (intake → decode → price →
send gate) is company-model-agnostic, and the order/fulfillment/inventory *mechanics*
(immutable snapshots, claim-based idempotency, movement ledger) are all still correct.
The error was in the *vocabulary and direction* of replenishment, not the machinery.

---

## 2. Checkpoint-by-checkpoint reconciliation

### CP-1 — Foundation (Postgres, message ledger, clean restarts)
**Holds as-is.** Nothing in the corrected model touches infra. (D65, C44.)

### CP-2 — Send gate (confidence score, recommend-only → auto-send safe slice)
**Holds as-is.** Quoting is upstream of the buy-vs-make distinction. One small
refinement rides along: RFQs that *arrive* with a customer PO/AFE already on them
(`Quote.po_number` exists today) should surface that number on the quote view so it
pre-fills at accept (§4).

### CP-3 — Orders (accept step, order record)
**Holds, with a refinement.** `create_order_from_acceptance` (src/app/orders.py:96)
already captures `po_number` on AcceptanceEvent and Order — the data thread Chip asked
for exists. Changes needed:
- **Rename/relabel the field "PO / AFE number"** in the accept form and everywhere it
  renders (order queue, order detail, pick sheet header). One column is enough — it is
  one customer reference; an optional `reference_type` (po|afe) tag is nice-to-have,
  not structural.
- **Nudge toward capture:** the accept form should treat an empty PO/AFE as a visible
  "are you sure" (not a hard block — some customers go ahead verbally), because this
  number is what makes the later invoice easy (I148.1).
- **Non-stocked trigger hook:** order acceptance (or pick-list generation, §3) becomes
  the trigger point for the min/max-0 vendor PO. Net-new seam, detailed in §3.

### CP-4 — Fulfillment (pick list + shop ping)
**Mostly holds.** The pick sheet as the pack manifest for a kit/box of components is
exactly what Chip described (I148.3/4) — multi-line pick lists already model the kit.
Changes:
- **Drop the signature lines from the pick sheet** (fulfillment/sheet.html) — pick
  sheets are not signed (I148.3). Replace with nothing, or a plain "packed by" note if
  the shop wants one; it carries no confirmation semantics.
- **The Shipped one-tap stays the interim shipped signal** (I145), but §5 designs its
  replacement since no signed paper closes the loop.
- **Weight-based BOL** is a future outbound-document item
  (allanedwards-outbound-document-requirements); the pick line model should carry (or
  be able to join to) per-item weight when that lands — noted, not designed here.

### CP-5a — Stock decrement (identity bridge, ledger, idempotent claim)
**Holds as-is.** Catalog-id identity, deterministic matcher, movement ledger, and the
UNIQUE-per-pick-list decrement claim (src/app/inventory.py) are all agnostic to who
replenishes. Finished-goods-only stock (D72) actually *simplifies* things: the flat
single-tier StockItem model we built is the **correct** model, not a shortcut.

### CP-5b — Reorder (min/max trigger, restock sheet, mark-received)
**This is where the model correction bites.** The mechanism (trigger on ledger writes,
one OPEN reorder per item, re-arm on receipt) is right; the *framing* is wrong:
- **"Make {qty}" → "Order {qty} from {vendor}"** — the restock sheet
  (templates/stock/reorder_sheet.html:58) becomes a **vendor purchase order**: order
  qty, part number/description, the vendor it goes to, and the trigger context. The
  "Made by / date" signature becomes "Ordered / date" + "Received / date" (a PO is a
  record AEI keeps, not a shop work ticket).
- **Vendor is a new data need:** a `vendor` on the catalog row (or StockItem) — a
  simple text field first (fewer than 50 SKUs; Chip can type "AE MFG"), a Vendor table
  later if/when POs go out by email. Every reorder freezes `vendor_at_trigger` the
  same way it freezes min/max.
- **Reorder lifecycle gains one state:** OPEN → **SENT** (the PO went to the vendor —
  today that's Chip printing/emailing it himself; the print/mark action is the
  transition) → RECEIVED. Mark-received semantics (actual qty, 0-closes, re-arm)
  stay exactly as built.
- **Two-tier raw/finished (I147) is dropped for AEI.** No raw-material StockItems, no
  linked decrement. (If AE MFG ever becomes a tool customer, I147 is *their* model —
  parked, out of scope.)
- **The shop-ping channel generalizes to a vendor-PO channel:** MANUAL_PRINT today;
  a future EMAIL_PO channel is an *external send* and sits behind the same trust-gate
  discipline as quote auto-send (nothing auto-emails a vendor until Devin/Chip flip
  it, mirroring CP-2's ramp).

---

## 3. Non-stocked parts: min/max = 0 → order-triggered vendor PO (net-new)

Chip: *"we sell a lot of parts that are not from inventory. Ideally treat those
exactly the same but min/max set at zero. So if order is received it automatically
sends build order with the customer details."* (I148.2)

Design — "treat them exactly the same," literally:
- **Seeding:** min = max = 0 is a *valid seeded state* meaning "never stock this."
  (Today NULL thresholds = unseeded and `needs_reorder` hard-returns False; 0/0 is
  distinct from NULL/NULL and must stay so.)
- **Trigger point moves upstream for these items.** The stocked path fires the reorder
  on the *shipment decrement*; a never-stocked item can't ship first — the vendor PO
  must fire **when the order lands**. Concretely: at **pick-list generation** (the
  existing ACCEPTED→ORDERED moment, src/app/fulfillment.py:172), each pick line is
  matched to its catalog row (the CP-5a matcher, reused); any line whose stock item
  has min = max = 0 emits a **customer-linked purchase order**: the same Reorder/PO
  record plus `order_id` + the customer's name, PO/AFE number, and ship-to — "the
  customer details" Chip asked to ride along. Idempotency: UNIQUE per (pick_list,
  line) claim, same pattern as everything else.
- **Why pick-list generation, not acceptance:** it is the moment the order becomes a
  physical instruction, it already runs the line-level loop, and it keeps acceptance
  (CP-3) purely a recording act. It also means one human step (Generate pick list)
  fans out *both* the shop manifest and the vendor PO — absorb, don't add (D41).
- **Fulfillment of a non-stocked line:** if the goods come to AEI first, the pick
  list simply waits until the vendor delivery arrives, then the normal
  pick→load→ship flow runs; the shipment decrement + immediate receipt would
  churn the ledger at on_hand 0, so decrement stays as-is (goes -N, receipt on
  PO-received brings it back) — the ledger stays truthful. If the vendor
  **drop-ships direct to the customer**, AEI never touches the box and the pick/ship
  flow is wrong for that line. **Which of these happens (or both, per vendor?) is a
  Chip question (§6).** The design accommodates both: a drop-ship PO can carry a
  "ships direct" flag that excuses its lines from the pick list.

---

## 4. Order-confirmation design: PO/AFE capture → invoice

The thread already exists in the schema; this is wiring and labeling, plus one
net-new future consumer:

1. **Intake:** decode already extracts `Quote.po_number` when an RFQ arrives with
   one. Surface it on the quote view.
2. **Accept:** the accept form's field relabeled **"PO / AFE number"**, pre-filled
   from the quote's value; stored on AcceptanceEvent + Order (already built).
   Empty → soft confirm ("no PO/AFE — accept anyway?").
3. **Order/fulfillment:** the number renders on the order queue, order detail, and
   the pick sheet (already partially there — `po_number` shows in queues).
4. **Invoice (future, net-new):** no invoice exists in the engine today (I145).
   When invoicing is scoped, the invoice reads the Order (which reads the immutable
   QuoteVersion) and the PO/AFE prints in its header — *"would make next step of
   invoice much easier"* (I148.1) is satisfied by this thread with zero re-keying.
   Invoicing itself is a separate scope conversation, not smuggled in here.
5. **Future acceptance sources:** reply-parse / PO-email detection plug into the
   same `create_order_from_acceptance(source=..., po_number=...)` seam — extracting
   the PO/AFE from the customer's email is exactly what the LLM decode layer is good
   at, behind its own confidence gate. Unchanged from the v1 design, now with a
   concrete field to extract.

---

## 5. Shipment-confirmation design (I146 — still open; signed sheets are out)

The system needs to learn "this box left." Today that is a one-tap Shipped in the
shop queue (I145); pick sheets are unsigned (I148.3), so no paper loop exists to
piggyback on. Options:

**Option A — keep the one-tap (status quo), Chip taps it.**
Chip is the sole operator at 10–20 orders/week; the tap already drives decrement,
FULFILLED, and reorder in one transaction. *Cost:* it is pure added admin (against
D41), it gets skipped on busy days, and inventory drifts silently when it does.

**Option B — the BOL is the signal: generating the BOL marks shipped.** Every
shipment already needs a weight-based BOL (I148.4) — a document someone must produce
*anyway* at ship time. Build the BOL generator (already on the outbound-document
roadmap), and make "print/generate BOL" the event that fires the shipped transaction.
The signal comes free with a step that must happen regardless — the D41-clean answer.
*Cost:* depends on the BOL document work landing, and on the BOL actually being
produced from the tool (needs per-item weights in the catalog); interim still needs A.

**Option C — scan-back: QR on the pick sheet, scanned when the box goes out (T432).**
Deterministic, timestamped at the dock. *Cost:* hardware/phone behavior in the
warehouse, and it puts a screen/scanner in the shop — cuts against the
route-around-the-team operating model (I146 context); most likely to just not happen.

**Recommendation: A now, B as the designed end-state.** Keep the one-tap as the
interim signal (it is built and works), and scope the weight-based BOL generator so
that producing the BOL *is* the shipped event — converting a today-manual document
chore into the confirmation signal, absorbing work instead of adding it. C stays
parked unless Chip volunteers that someone else does the shipping. This needs **one
short question to Chip** (§6) to confirm who produces the BOL today and when — if the
answer is "the freight carrier brings it," Option B weakens and A stays long-term.

---

## 6. New vs refinement

| Change | Kind |
|---|---|
| CP-1 foundation, CP-2 send gate | untouched |
| PO/AFE relabel + pre-fill + soft-confirm at accept; surface on quote/order/pick sheet | **refinement** (fields exist) |
| PO/AFE → invoice header | **net-new** (invoicing itself is future/unscoped) |
| Pick sheet: drop signature lines | **refinement** (template edit) |
| Restock sheet → vendor PO ("Order {qty} from {vendor}"; Ordered/Received stamps) | **refinement** (template + copy) |
| Vendor field on catalog/stock item + frozen `vendor_at_trigger` on reorders | **net-new** (small) |
| Reorder lifecycle OPEN → SENT → RECEIVED | **refinement** (one state) |
| Drop two-tier raw/finished (I147) for AEI | **removal** (of a planned complication — the built flat model is correct) |
| min/max = 0 = valid "never stock" seeded state | **refinement** (seeding validation + `needs_reorder` semantics) |
| Order-triggered vendor PO with customer details at pick-list generation | **net-new** (the main new build) |
| Drop-ship flag excusing lines from pick/ship flow | **net-new** (conditional on Chip's answer) |
| BOL generator that doubles as the shipped signal | **net-new** (future; ties to outbound-doc roadmap) |
| EMAIL_PO vendor channel behind a trust gate | **net-new** (future) |

---

## 7. Questions for Chip (short, direct — the few that block design)

1. For parts you never stock (the min/max-zero ones): does the vendor ship them
   straight to your customer, or do they come to you first and you ship them?
2. When a shipment goes out, who fills out the BOL, and at what point — before the
   truck comes, or when it's loaded?
3. When you order from AE MFG or another vendor today, how do you send that order —
   email, phone, something else? What has to be on it for them to build it?
4. Is each part always bought from one vendor, or do you shop the same part around?

(Everything else — placeholder stock numbers, sleeve min/max — stays on the
correct-at-demo plan, D69/D70. Do not ask.)

---

## 8. Sources

Canon: D72, I148, I146, I145, I147 (superseded for AEI), I143, D69/D70, D51, D65,
C43/C44, allanedwards-outbound-document-requirements.
Docs: docs/proposal-amazon-engine.html, docs/research/amazon-engine-end-to-end-design.md,
docs/research/onsite-2026-08-10-structured-notes.md (§4, §8).
Code read: src/app/orders.py, src/app/fulfillment.py, src/app/inventory.py,
src/app/models.py (Order/AcceptanceEvent/Quote.po_number),
src/app/templates/stock/reorder_sheet.html, src/app/templates/fulfillment/sheet.html.
