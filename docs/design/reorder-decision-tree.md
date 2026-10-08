# Inventory replenishment decision tree — AEI purchases finished goods

Task 484 · 2026-10-08 · Design for Devin's walkthrough with Chip; no application changes.
Code baseline: `d127c4c` (main when investigated). All file:line references below refer to that revision.

AE Inc buys finished goods from AE MFG and other vendors. AE MFG is a separate company. This design has no AEI manufacturing, raw-steel consumption, or bill of materials (D72 supersedes D71). The open business decisions concern how AEI chooses, releases, and follows up purchases. **The proposed defaults below are recommendations, not Chip-approved policy.** Existing behavior remains unchanged until a separately reviewed implementation.

## Questions for Chip — top five that unblock the most

Devin can walk through these in one sitting using one recent sleeve purchase and one short delivery. Each bold question is the spoken question; the notes are for Devin. Answers can be one sentence. Do not ask Chip to choose software designs.

1. **When a sleeve can come from more than one vendor, what makes you choose one over the other?**
   Blocks vendor selection and exception routing. Default: propose the catalog vendor; a buyer chooses any alternative using price, delivery date, and required specification. Never automatically pick the cheapest or silently substitute.
2. **When sleeves run low, how do you decide how many to buy?**
   Blocks quantity calculation, including fixed lots and vendor pack requirements. Default: top up to the confirmed maximum, subtract outstanding purchases, and use a fixed reorder quantity only where Chip has confirmed that practice. Round only to a verified vendor pack size; ask the buyer when that size is unknown. This asks about the rule, not another request for the missing sleeve numbers (D69/D70).
3. **Who gives the final go-ahead before an order goes to AE MFG or another vendor?**
   Blocks release ownership and the boundary between a suggestion and a purchase commitment. Default: Chip or his named buyer reviews every PO and sends it manually; automatic creation produces an internal draft only.
4. **When a vendor delivers fewer sleeves than you ordered, what happens to the missing sleeves?**
   Blocks whether the balance stays on order or needs a replacement purchase. Default: keep the balance open until the buyer confirms cancellation; record only the accepted delivery as stock. Do not create a duplicate purchase just because the delivery was short.
5. **When a customer's sleeve order needs more than is on the shelf, when do you normally place the vendor order?**
   Blocks timing for stocked-item shortages and whether accepted orders must affect purchasing before shipment. Default: flag the shortage for the buyer at customer acceptance; keep ordinary stocked replenishment tied to stock movements until the business rule is agreed. Non-stocked order-triggered purchasing is already established by I148; this question concerns stocked items and extends that rule only if Chip says so.

## Remaining questions — resolve after the top five

6. **When you place an order with AE MFG today, what do you send them and how?**
   Determines the usable PO content and delivery channel. Default: buyer manually sends the reviewed PO sheet by the vendor's established method and records it as sent. No automated email or phone ordering.
7. **Who checks a vendor delivery and tells the office how many usable pieces arrived?**
   Determines who can book receipts and resolve damaged or excess goods. Default: receiver counts and reports; buyer or office records the accepted quantity against the PO. No stock increase from a signature or delivery notice alone.
8. **For parts you never stock, does the vendor deliver to AEI or straight to the customer?**
   Determines whether AEI ever receives physical stock and which address belongs on the PO. Default: delivery to AEI, matching today's sheet. Any direct-shipment case needs a separate fulfillment path before automation.
9. **When quoting or confirming an order, how do you use stock that a vendor has promised but has not delivered?**
   Determines what on-order quantities and dates need to appear on the quote and order screens. Default: show sent-but-outstanding quantities separately with confirmed dates or “date unconfirmed”; never describe them as on hand or guarantee customer delivery from them.

This is the complete first-pass interview list, ranked by impact. Do not re-ask whether AEI makes goods, whether non-stocked goods exist, or whether customer PO/AFE signals go-ahead: D72 and I148 already answer those. Placeholder stock numbers are corrected at the demo on `/stock/seed`, not requested in another email (D69/D70). Devin handles all client contact.

## Current state — verified in code

### Trigger and quantities

- `StockItem.is_seeded` requires **min and max**, not a non-null `reorder_qty` (`src/app/models.py:737`). `min=max=0` means non-stocked (`src/app/models.py:742`). Otherwise `needs_reorder` is true at **on_hand <= min**, including equality; unseeded and non-stocked items never take this path (`src/app/models.py:750`). The older class comment about all three values being required is inaccurate.
- `compute_reorder_qty` uses `reorder_qty` if supplied; otherwise `max(max_qty - on_hand, 1)` (`src/app/inventory.py:198`). There is no vendor-specific pack rounding, lead-time demand, reservation deduction, or outstanding-quantity subtraction in this calculation. The floor of one applies even when on-hand, min, and max are equal and positive.
- `maybe_trigger_reorder` creates the row, freezes counts and catalog vendor, writes a **zero-delta** REORDER movement, and adds a MANUAL_PRINT `ShopPing` (`src/app/inventory.py:207`, `:245`, `:264`). It does not send a vendor message or add physical stock.
- Concrete trigger call sites: shipment decrement (`src/app/inventory.py:189`), changed stock seeding including threshold-only changes (`:539`, `:573`), manual receipt (`:602`), adjustment (`:624`), unmatched-shipment resolution (`:660`), and PO receipt/closure (`:470`). Unchanged seeding returns without a write (`:545`). “Every ledger write” is shorthand for these stock-changing paths, not a recursive trigger on zero-delta REORDER audit rows or unmatched rows with no stock identity.
- The unique partial index allows at most one **min-triggered** OPEN or SENT row per stock item (`src/app/models.py:903`). The savepoint catches its collision and leaves the existing order unchanged (`src/app/inventory.py:239`). More consumption in transit cannot create another min-triggered PO, but also cannot resize the existing PO or alert on its adequacy through this function.
- A second, already-built trigger creates a customer-linked PO for each positive, matched, explicitly non-stocked pick line (`src/app/inventory.py:293`, `:316`). It runs at **Generate pick list**, not merely customer acceptance (`src/app/fulfillment.py:172`, `:221`). It copies the pick-line piece count, catalog vendor, customer name, PO/AFE, quote number, job ship-to, and line specifications. Unmatched/unseeded lines are skipped. `OrderVendorPoClaim` prevents replay per pick-list/line; multiple customers can legitimately have simultaneous POs for the same item (`src/app/inventory.py:330`, `src/app/models.py:878`). These rows are excluded from the min-triggered unique index.
- Customer quote bag quantities are rounded to pallets in `src/app/routes.py:2133`; pick-line pack labels are populated at `src/app/fulfillment.py:155`. This is not a supplier purchasing-pack policy. Min-triggered PO quantities do not invoke that rounding; non-stocked POs copy already-built pick quantities without a new purchasing round.

### Persisted fields and what they cannot yet represent

| Record | Existing fields | Procurement implication |
| --- | --- | --- |
| StockItem | `id`, unique `catalog_id`, `on_hand`, nullable `min_qty`, `max_qty`, `reorder_qty`, inherited timestamps; catalog/movement relationships (`src/app/models.py:696`, `:717`) | No on-order balance, purchase pack, preferred/fallback supplier list, or reserved-demand quantity here. |
| ProductCatalog | Nullable free-text `vendor` (`src/app/models.py:690`) | One stored vendor label; no selection logic between vendor offers. |
| Reorder | `id`, `stock_item_id`, `status`, `qty`, `on_hand_at_trigger`, `min_qty_at_trigger`, `max_qty_at_trigger`, `vendor_at_trigger`; nullable `order_id`, `customer_context`; `sent_at/by`, `received_at/by`; `trigger_movement_id`, `reorder_movement_id`, `receipt_movement_id`; inherited timestamps and relationships (`src/app/models.py:914`) | Has evidence of a PO being sent, but no separate approval, promised date, remaining balance, cancellation status, price/terms, or multiple linked receipt events as a supported lifecycle. |
| StockMovement | Type, signed `qty_delta`, resulting on-hand, item/pick/user links, reason, details, resolution metadata, creation time (`src/app/models.py:760`, `:788`) | Append-only physical-stock history. REORDER has delta zero; sending a PO is not receipt. |

### Existing states, receipt behavior, and printed sheet

`OPEN -> SENT -> RECEIVED`, with `OPEN -> RECEIVED` also allowed (`src/app/models.py:68`). “Mark PO sent” only stamps state/time/user; it does not transmit anything or verify a vendor was filled in (`src/app/inventory.py:406`, `:1093`). There is no distinct approve, cancel, partial, late, or rejected state.

`close_reorder` accepts any nonnegative received quantity, closes the entire PO, adds only a positive actual receipt to on-hand, then evaluates the threshold again (`src/app/inventory.py:424`). A short receipt has no retained balance. Zero closes with no receipt movement, but if the item is still at/below min it immediately opens a fresh min-triggered row. Over-receipts are accepted without a quantity cap here. Non-stocked 0/0 items do not get a fresh min-triggered replacement, even if received short. Manual stock receipts also exist, but do not reconcile a PO's state (`src/app/inventory.py:582`). These are existing semantics, including earlier PM agreements in the comments, not new recommendations.

The Reorders screen already displays “Order [qty] from [vendor],” “PO sheet,” “Mark PO sent,” and “Mark received” (`src/app/templates/stock/reorders.html:59`, `:77`). The sheet already says **“Order,” “Ordered / date,” and “Received / date”**, not “Make” or “Made by” (`src/app/templates/stock/reorder_sheet.html:75`, `:136`, `:140`). It contains AEI as ship-to, PO/Reorder ID, status/date, part/description/quantity, a vendor or blank vendor line, and either trigger counts or customer/job context (`:65`, `:80`, `:95`, `:120`). The customer address is explicitly “Job ship-to (AEI ships onward)” (`:108`). Only line notes render as specifications (`:113`), although fuller specs are stored.

Caveat: the sheet's primary part number and description still come from the **live catalog** (`src/app/templates/stock/reorder_sheet.html:75`, `:88`), despite comments claiming the sheet cannot drift. Quantity, vendor, trigger counts, and customer context are frozen; the whole issued document is not.

There is an **in-flight status concept already**: OPEN and SENT remain active in `open_reorders_query` (`src/app/inventory.py:474`). Stock screens show badges and a PO quantity (`src/app/templates/stock/list.html:60`, `src/app/templates/stock/detail.html:37`). There is no supported remaining-on-order quantity model in these records. The proposed quote/order display needs that model; summing every OPEN/SENT original quantity would mix unsent drafts with commitments and mishandle partial receipts.

## What canon gets wrong or leaves historically stale

These are corrections to present-day summaries, not reasons to discard the historical records.

| Stale claim | Current evidence / correction |
| --- | --- |
| D71 and the two-tier/raw-material implication in I147 describe AEI making finished goods. | **D72 already supersedes this.** AEI buys; the model and lifecycle now explicitly describe vendor POs (`src/app/models.py:68`, `:862`). Retain D71 only as superseded history. |
| I147, D72's original implementation-gap wording, and task 484 say today's sheet prints “Make” / “Made by.” | Already corrected to Order / Ordered / Received (`src/app/templates/stock/reorder_sheet.html:75`, `:136`, `:140`). D72's company boundary remains authoritative; mark the wording fix implemented rather than rewriting that decision. |
| “One open reorder per item” without qualification; older inventory reference says index is OPEN only. | Index covers OPEN **and SENT**, **only where order_id is null** (`src/app/models.py:903`). Customer-specific non-stocked POs coexist; per-line claims guard them (`src/app/inventory.py:330`). |
| “There is no on-order concept” understood as no in-flight state or duplicate protection. | SENT and active-PO suppression already exist (`src/app/inventory.py:406`, `:474`). What is missing is an outstanding-quantity/partial-receipt model and its use in planning/screens. |
| “Whether customer orders trigger replenishment” is entirely undecided. | I148 already establishes non-stocked order purchasing; code creates those POs at pick-list generation (`src/app/fulfillment.py:221`, `src/app/inventory.py:293`). Stocked shortages and exact earlier timing remain open. |
| Old reference says the printable sheet is fully frozen. | Header/line identity still reads the live catalog (`src/app/templates/stock/reorder_sheet.html:75`, `:88`); freeze the whole released artifact in a future design. |

`docs/reference/inventory-stock.md` also retains historical make-only/OPEN-only descriptions. It was read as background, not used to override current code. Graph search returned unresolved I18062/I18063 references; no finding here relies on those nonexistent nodes. PM should amend the historical gap summaries and reference documentation at review without losing their chronology.

### Checked and still true — preserve these claims

- **D72: AEI buys from AE MFG and other vendors; it does not make goods.** The code explicitly models a vendor PO (`src/app/models.py:862`). D71 remains superseded; removing old Make labels does not invalidate D72.
- **I148: non-stocked parts use min/max zero and customer-linked purchasing.** Implemented at pick-list generation (`src/app/models.py:742`, `src/app/fulfillment.py:221`). Preserve the business intent; distinguish internal creation from actual vendor transmission.
- **Original reorder trigger/quantity rule:** eligible stock at or below min triggers; fixed quantity otherwise max top-up remains accurate (`src/app/models.py:750`, `src/app/inventory.py:198`). Qualify it with the unseeded and non-stocked exclusions.
- **Short receipt closes the PO and may re-arm replenishment.** Still true, including zero receipt (`src/app/inventory.py:424`). Partial-balance handling in this document is a proposed change, not a correction to canon about current behavior.
- **Manual-print delivery and zero-delta REORDER audit:** still true (`src/app/inventory.py:245`, `:264`). There is no vendor send in the mark-sent function (`:406`).
- **D69/D70: demo quantities are placeholders and Chip corrects them in-session.** Preserved from the canonical decisions; this research did not inspect staging or verify any live count. Do not replace them with illustrative numbers from this document.
- **I147's process/scope warning and I177's request to settle the tree first:** still relevant; the remaining purchase-management choices above are unresolved business design, even though the original wording gap is fixed.

## Proposed decision tree — pending business confirmation

The tree extends the existing vendor PO flow. A **draft** is a purchasing suggestion; **sent** means a commitment communicated to a vendor. Proposals below deliberately change short-receipt semantics and introduce purchase tracking; they are not descriptions of current behavior.

### 1. Identify the demand and decide whether to propose a purchase

| Condition | Decision / next action |
| --- | --- |
| Product match missing, counts suspect, min/max unconfirmed, or values are marked demo placeholders | Put the item in buyer review. Do not infer a supplier or use invented numbers for a real purchase. Confirm count/setup, then re-evaluate. NULL is unknown, not non-stocked. |
| Non-stocked item (0/0), customer quote only | No purchase. A quote is not customer go-ahead. |
| Non-stocked item, customer go-ahead recorded | Keep established customer-linked draft intent. Initially preserve Generate pick list as the existing creation step; ask whether business timing demands earlier creation. If the same demand is already covered, link to it rather than emit another PO. |
| Stocked item, no new customer shortage, on-hand above min | No new purchase. Keep existing purchases and their exceptions visible. |
| Stocked item at/below min | Inspect any existing draft and sent balance before proposing quantity (step 2). |
| Accepted stocked customer order cannot be filled from uncommitted stock | Show “buyer review: shortage” immediately. Default is a human buying decision; automatic draft creation at acceptance is conditional on question 5. Never auto-send because a customer order arrived. |
| Customer order changed/cancelled | Reconcile its linked demand. Withdraw an unsent draft if unnecessary; buyer must resolve an already-sent PO with the vendor. Customer cancellation alone does not cancel vendor supply or physical stock. |

Customer demand must not be deducted twice. If future reservation/shortage planning is approved, count each unshipped accepted line once, release its reservation as shipment posts, and link customer-specific incoming supply to that same demand. Do not turn the current negative on-hand count into a second copy of the same shortage.

### 2. Check existing supply before choosing vendor and quantity

- **Existing unsent draft:** refresh the recommendation and show the reason; retain one purchasing task. If demand vanished, withdraw it with a reason. No outstanding supplier commitment yet.
- **Existing sent PO with balance due:** keep it active through partial receipt or lateness. Show any new shortage against that purchase. Do not auto-create another min-triggered PO while it is in flight. Buyer can expedite or explicitly revise/replace/add supply only after reconciling the original commitment; never silently resend the original PO.
- **No active supply/draft:** proceed to vendor selection and quantity calculation. Customer-specific POs for other customers do not cover this demand automatically.

This preserves the existing duplicate guard's intent, extends it to partial receipts, and avoids hiding a genuine additional shortage behind a permanent “PO exists” badge. A manual supplemental purchase must identify which uncovered demand it covers and how it differs from the existing PO.

### 3. Choose the vendor

- Exactly one suitable known vendor: propose it, including AE MFG as an ordinary external vendor.
- Several suitable vendors: propose the preferred vendor if Chip has named one; otherwise route to buyer choice. Buyer compares required spec, arrival date, purchase price/freight, and any vendor restrictions. Do not equate “cheapest” with “suitable.”
- Preferred vendor unavailable or too late: buyer chooses an approved fallback or leaves the purchase waiting for sourcing. No automatic vendor substitution, split order, or duplicate order.
- No known vendor/contact: keep draft on hold for buyer completion. Current code permits a blank vendor, but a future release must require the actual supplier and delivery destination.

### 4. Decide how much

Use piece counts as the common stock unit and record vendor pack conversions explicitly.

- **Stocked, normal top-up:** proposed quantity is `max(0, target_max - (on_hand + eligible_outstanding_supply))`. Eligible supply means remaining sent pieces intended for this stock pool, not an unsent draft or supply already assigned to another customer's demand. If the result is zero, retain monitoring without creating a new PO.
- **Confirmed fixed lot:** propose the agreed `reorder_qty` once the reorder condition is met; do not label it an economically optimal quantity or calculate EOQ without business inputs. Existing active-supply handling still applies. If the fixed lot cannot cover a known shortage, buyer reviews it instead of blindly multiplying orders.
- **Non-stocked/customer-specific:** buy the uncovered customer requirement. If already-covered goods or cancelled-order surplus exist, buyer must explicitly allocate them before reducing the purchase; another customer's supply cannot be borrowed silently.
- **If reservation-aware planning is approved:** the stock planning position becomes `on_hand + eligible_outstanding_supply - unshipped_demand_assigned_to_this_pool`. Both trigger and target quantity must use that same definition. Dedicated demand and its dedicated supply stay outside the shared pool. Until this tracking exists, flag shortages for review rather than pretend the formula is implemented.
- **Verified vendor minimum/pack:** if raw proposed quantity is positive, round to `pack * ceil(max(raw_quantity, vendor_minimum) / pack)`; pack is positive and all values use the same unit. Show pieces and packs, including any amount above the stock maximum. Buyer explicitly accepts the excess. Zero demand does not become a minimum purchase automatically.
- **Unknown pack/minimum, implausible count, or insufficient specification:** hold for buyer review. Existing bag sale rounding is evidence to ask about packs, not authority to impose a supplier's pack size. Do not silently round a custom item into extra customer demand.

### 5. Review, release, and send

1. Buyer reviews vendor, quantity/packs, required specification, price/terms if needed by the business, destination, needed date, and customer linkage. Missing information keeps the draft held. Rejection/withdrawal closes a draft with a reason and no physical movement.
2. Default is **human release for every PO**. Automatic drafts are useful; automatic purchase commitments are a separate business/scope decision. Record who approved the exact revision. A changed supplier, quantity, price, or destination requires renewed review.
3. Send a stable, printable PO with AEI identity and delivery address, distinct vendor-PO number/revision (not the customer's PO/AFE), vendor contact, part/specification, pieces and pack unit, requested delivery date, and required purchase terms. Include customer/job details for dedicated purchases as agreed with Chip; the customer's ship-to must not accidentally become a drop-ship instruction.
4. Buyer uses the vendor's established email/printed/phone process. Printing alone is not sending. Record channel, time, sender, and the exact released artifact; phone orders need a recorded reference and quantities. Failed or uncertain delivery stays an explicit follow-up task and must not produce a second PO.
5. Once actually communicated, move to SENT and count the remaining commitment as on order. Vendor confirmation records the promised date; a rejected order moves to buyer resolution. Automatic outbound email would require its own delivery/retry and authorization design.

### 6. Follow up and receive

| Event | Proposed business action | Stock / outstanding effect |
| --- | --- | --- |
| Vendor confirms quantity/date | Buyer records confirmation; mismatches return for review. | No physical change. Preserve committed balance, distinguish confirmed from unconfirmed date. |
| Due date missed, no receipt | Buyer follows up, expedites, or confirms cancellation/replacement. Without a promised date, show “date unconfirmed” instead of inventing a late date. | Keep balance on order; no receipt, automatic closure, or duplicate min-triggered PO. |
| Full accepted delivery to AEI | Receiver counts; office/buyer books it against PO. | Add accepted pieces to on-hand, reduce outstanding by same amount, close when balance reaches zero. |
| Partial accepted delivery; rest still coming | Record receipt and remaining promise/date; leave PO PARTIALLY_RECEIVED. | Add accepted pieces only; balance stays on order and under duplicate protection. |
| Short delivery is final / vendor cannot supply remainder | Buyer confirms cancellation of the balance and records reason. Recalculate uncovered need; create a reviewed replacement only if needed. | Cancelled remainder leaves on-order, not on-hand. No automatic assumption that “short” means cancelled. |
| Zero arrived | Record delay or explicit cancellation, as applicable. | No receipt movement and no “received” status. |
| Damaged, wrong, or excess pieces | Receiver reports exception; buyer chooses acceptance, rejection, or return and records the agreed quantity. | Only accepted usable pieces enter normal stock. Accepted excess needs explicit approval; a later return writes a separate negative movement. |
| Goods arrive while system still shows draft/OPEN | Buyer reconciles actual purchase and receipt evidence, including duplicate checks. | Permit an audited receipt without inventing a sent timestamp; do not block counting real goods. |
| Vendor ships directly to customer | If Chip confirms this practice, buyer verifies vendor delivery against customer order in a separately designed direct-fulfillment path. | Never fake a warehouse receipt and shipment. Existing AEI pick/receive flow is insufficient. |

Proposed operational states: DRAFT (needs buyer), APPROVED (ready to send), SENT (awaiting delivery), PARTIALLY_RECEIVED (balance due), RECEIVED (fully delivered), CANCELLED (no remaining commitment). A partially delivered PO with its remaining balance cancelled retains both its receipt history and cancellation reason. Late, missing-data, and disputed-delivery flags are exceptions on these states, not reasons to lose the outstanding balance.

## Ledger and screen contract to agree before implementation

For each PO line, define `remaining = ordered - accepted_receipts - cancelled_remainder`, never below zero. Accepted over-delivery is recorded explicitly against an approved revision or excess exception, not hidden by a negative balance. Reversal of an erroneous receipt is a linked correction, not deletion. Multiple receipts need separate source references so submitting the same delivery twice cannot add stock twice. Receiving through Stock and receiving through Reorders must use the same PO-linked receipt event.

Physical on-hand remains the sum of stock-ledger deltas: draft, approval, sending, vendor promises, and cancellation have **zero physical delta**. Receipt adds accepted pieces; customer shipment subtracts pieces; counts/returns use reasoned adjustments. Purchase commitments and their receipt/cancellation history provide a separate on-order projection. Do not repurpose REORDER's zero-delta audit row as received stock.

Suggested internal quote/order display, pending question 9: **On hand**, **On order** (sent outstanding), **Expected** (confirmed date or unconfirmed), and **For this order / for other orders** where allocations actually exist. Link to the supplier PO. Show drafts as “awaiting buyer,” outside on-order. On quotes this informs availability discussions; on accepted orders it identifies supply shortfalls and the responsible buyer. Neither screen may promise stock that is only expected or show computed “available” without reservation data.

Illustrative walkthrough only — these numbers are invented, not staging values or Chip's thresholds:

| Scenario | Expected proposal behavior |
| --- | --- |
| On hand 4, min 5, max 20, no supply | Propose 16 pieces before pack rounding. |
| Same item, 16 already sent | Keep that PO; no duplicate draft and no physical increase. |
| That PO delivers 5 of 16 | On hand becomes 9; 11 remain on order under the same PO. |
| Vendor cancels those 11 | On hand stays 9, outstanding becomes 0. Normal threshold alone does not trigger at 9 > 5; buyer reviews any uncovered customer demand separately. |
| PO for 16 delivers only 1, balance 15 still coming | On hand becomes 5; keep 15 on order and no duplicate at the threshold. Current code would close and may re-trigger instead. |
| Two customers each need 3 non-stocked pieces | Separate demand links and 6 total dedicated pieces, unless buyer explicitly consolidates; replay creates nothing extra. |
| Accepted customer requirement disappears before release | Withdraw draft with a reason; no ledger delta or vendor cancellation claim. |

## Scope and next action

The settled correction from “make” to vendor purchasing has already shipped. It is not the remaining scope gap. I147's broader warning still applies: mapping purchasing responsibilities and exceptions is a process-design conversation, not merely editing a sheet label.

| Area | Scope treatment |
| --- | --- |
| This document, ranked questions, walkthrough against existing screens | Task 484; design only. |
| Correcting leftover make/shop language and stale references; confirming real setup values in demo | Narrow follow-up after review; no promise of procurement automation. |
| Multiple vendor choices/offers, preferred/fallback rules, supplier packs/minimums and commercial PO terms | Contract/scope discussion before implementation; today's free-text vendor and fixed/top-up rule do not supply these capabilities. |
| Approval roles, permission enforcement, PO revisions, automated vendor communication | Contract/scope discussion. “Mark sent” alone is not an approval or messaging system. |
| Partial receipts, cancellation, outstanding balances, promised dates, late follow-up, deduplicated receiving | Contract/scope discussion; deliberately replaces earlier close-on-any-receipt behavior and needs data/history handling for existing POs. Do not infer remaining balances for already-closed short POs without reconciliation. |
| Accepted-order shortage triggering, reservations, on-order quantities on quote/order screens | Explicitly in scope as design questions, separately estimated build work once business rules are agreed. Existing non-stocked draft creation is the baseline. |
| Direct vendor-to-customer delivery | Conditional new fulfillment scope if Chip confirms it. |
| AE MFG manufacturing/raw materials/BOM | Outside AEI's model. |
| Customer-facing reorder / “buy it again” | Explicitly excluded; epic 347's separate last step. |

Next: Devin walks Chip through questions 1–5 and records his actual purchasing practice; use 6–9 to settle delivery, receipt, and sales visibility. PM then marks each proposal accepted, changed, or deferred and agrees scope before creating implementation tasks. No vendor sends, migrations, staging/prod actions, or application edits were performed for this research.
