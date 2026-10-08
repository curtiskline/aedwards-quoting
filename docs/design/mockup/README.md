# Quote-to-ship UX proposal

**Open [index.html](index.html) first.** Double-click it in a browser. All HTML pages contain their own CSS; no server, build, JavaScript, external font, CDN, or network request is required. Copy this entire directory to share it. All customers, contacts, prices and counts are fictional. No production database was queried.

## Recommendation

Make home a **work queue**, initially “Waiting on me.” A record exposes its next action, responsible person/team, blocker and age before the user opens it. Keep Quotes → Orders → Shop & shipping in persistent navigation. Stock & purchasing and Customers support that chain; configuration moves to Settings. Documents has a visible **Planned** destination.

Compare [pipeline.html](pipeline.html), a second fully linked home using the same eight records. The board communicates stage distribution well, but requires scanning columns to find personal work. Recommend the queue as the default and the board as a secondary view. The PM concurred with this direction before page construction.

Switch the seat between **Chip / sales**, **Shop**, and **Shipping** on home. The pending rows change; the shared initial dataset does not. Customer and team/vendor waiting have their own filters. Blockers use explicit words alongside color. Age is visible without opening a record. The five-day follow-up and two-day pricing examples are illustrative priorities, not existing SLA rules.

Record pages repeat the workflow, customer/job, status, responsible person and next action. “Save” retains a draft; “Send” moves to customer waiting; acceptance creates an order from the sent snapshot; a pick list queues physical work. No new assignment wizard or confirmation dialog is proposed. Existing optional detail is kept in native disclosures. Fields are deliberately read-only sample blocks; links show fixed result states, not saved edits.

## Click paths

- **Full example:** home → 126-084 → save filling price → review email → send result → record acceptance → order → generate pick list → picked → loaded → shipped → stock or planned invoice handoff.
- **Manual quote:** New quote → create example draft. No email prerequisite. The example re-enters the main walkthrough after its detail state.
- **By whom:** home → Shop seat → pick instruction; home → Shipping seat → loaded order; home → Waiting on customer → overdue follow-up; team/vendor filter → AE MFG order.
- **Copy semantics:** quote → Revise (same deal, linked version) or Duplicate (another customer, same project bid). Detail examples rejoin the main walkthrough, visibly labeled.
- **Replenishment:** Stock & purchasing → Vendor orders → restock instruction → mark sent → record receipt. A separate custom-job vendor example waits for delivery at AEI.
- **Planned shipping alternative:** Documents → document-led shipping view → compare current manual shipment view. We recommend keeping the actual departure signal explicit; printing a BOL may precede departure.

Links demonstrate states. Returning home restores the initial queue, not a persistent simulation. Sample alternate records have their own matching detail pages; some secondary continuations explicitly rejoin 126-084. Browser Print works for quote/pick/vendor document examples. Every visible navigation destination works; unavailable editing, account, upload and document-generation actions say so visibly.

## Grounding and scope

[Inventory](inventory.md) enumerates **every route declaration in `src/app/routes.py`, all seven additional route modules, and all 57 templates**, including partials. It separates screens from action/API endpoints. Baseline: `d127c4c`, October 8, 2026. These are code capabilities, not an assertion that every subsystem is operationally adopted in production.

| Area | What exists in current checkout | This proposal |
|---|---|---|
| Intake | Email classification/draft creation; failed and rejected intake views | Failed intake promoted to actionable work |
| Quotes | Queue/editor; manual creation; prices, shipping, fill history, adjustments; sent PDF/email; revisions and duplicates | Frame around forms; grouped next-action view; existing details retained or explicitly scoped |
| Ownership | Reviewer fields, automatic first-open review assignment, claim/release endpoints, 15-minute queue lock display | Durable responsibility and role views are proposed, not a permission system; current review awareness is not an exclusive lock |
| Orders | Human acceptance of sent version, PO/AFE, immutable order lines | Acceptance inline with its context; omit extra empty-reference confirmation while preserving visible missing-reference notice |
| Fulfillment | Pick sheets; queued → picked → loaded → shipped; stock decrement and order fulfillment | Physical owner shown separately from app recorder; no automatic truck/carrier detection |
| Stock / vendors | Seed/import, movements, unmatched lines, min/max reorder, never-stocked vendor instructions, sent/received | Vendor waiting and shortages exposed; no automatic vendor email |
| Customers / settings | Customer/contact/address CRUD, pricing/catalog/types/shipping defaults, trust ramp, users/auth | Supporting screens retained; low-value form mechanics explicitly outside mockup scope |
| Outbound documents | Not built | Visible planned BOL, packing list, MTR, conditional SDS, invoice — distinct documents per T431 |
| Storefront | Deferred by epic 347 / D51 | Described here and in guide; no daily-work nav slot because engine comes first |

Physical responsibility is **sales quotes, customer gives go-ahead, vendor supplies/builds, shop picks/loads, shipping dispatches**. AEI is a distributor, not AE MFG. Vendors deliver to AEI first. **Chip is the known app operator**; evidence does not establish named shop/shipping/accounting app users or a multi-seat permission model. Persona views propose what those seats would see, without requiring them to log in. Shipping/receiving recorder assignment and the accounting handoff remain questions for implementation, not facts invented by this mockup.

Auto-send stays **off / Tier 1 assisted**. Settings explains Tier 0 kill switch and Tier 2 eligibility. Displayed thresholds are code defaults (90% recommendation; 95% auto-send; $2,500 ceiling; 20% price tolerance), not queried production settings. All guards still matter: priced lines, known customer, confirmed ship-to, allowlist, confidence, ceiling, tolerance, no holds, audited lineage.

Sources consulted: requested `axon deep SOP`, `workflow roles`, `epic 347`, `quote status`, `trust ramp`; canon K347, D51, D72, D82, D87/D90 via duplicate-482 retrieval, I145, I146, I148, I150, I153; task 431; `docs/reference/fulfillment-pick-lists.md`, `docs/reference/inventory-stock.md`, the onsite notes and engine-v2 design; current route/template source. Generic SOP/roles retrieval produced unrelated cross-project results, so it was not treated as evidence of AEI personnel. Current code and later decisions resolve stale “no orders/fulfillment” claims in older reference docs.

**D82 has a later exception:** D87/D90 and T482 preserve job-specific filling price/location on duplicate without confirmation; customer-specific percentage adjustment resets. Revision retains the same deal. This proposal does not reintroduce the rejected carried-price confirmation guard.

Only this documentation directory is changed. Nothing in `src/app`, staging, production, database schemas or outbound communications is touched.

## Verification

- Chromium rendered `index.html` directly from `file://`.
- Browser audit rendered all 62 pages at 1440 px and 390 px (124 renders); no document-level horizontal overflow after fixes.
- Clicked the 14-step main quote → shipment → vendor receipt path; checked all three persona queues (4 / 1 / 1 pending rows) and their “Waiting on me” links.
- Verified queue and pipeline contain the same eight initial records; all local links and fragment targets resolve; no external dependencies, scripts, forms or network calls are embedded.
- Visually reviewed desktop queue, quote editor and pipeline plus mobile role queue. Browser console reported no errors.

This is documentation-only design work, so no application regression tests were added or run. The temporary HTTP server used by the browser-control tool was for QA only; opening the deliverable requires none.
