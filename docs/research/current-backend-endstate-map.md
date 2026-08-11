# Current back-end end-to-end map — what exists today, where the pipeline stops

Task 365 (parent: epic 347). **READ-ONLY** investigation. Deliverable is this doc.
Goal: an honest, file:line-cited map of what the Allan Edwards back end actually does
today, so the "comprehensive end-to-end engine" (D51, epic 347) is scoped against
reality, not against Chip's aspirational "e-responder just handles it."

**Headline:** Today the system is a **one-stage pipeline: inbound email → priced quote
draft.** It stops at "quote." There is **no order object, no fulfillment/pick-list, no
shop notification, and no inventory/min-max/reorder** anywhere in the codebase. The
"e-responder" email address that Chip imagines handling everything currently does
exactly one thing: **generate a quote draft that a human then reviews and sends by hand.**

Sources read: `src/allenedwards/{monitor,parser,pricing,pricing_catalog,db_writer,cli,pdf_generator}.py`,
`src/app/{models,routes,quotes,email_service}.py`, `deploy/aedwards-*.service`,
migrations, and canon (I111/K340 monitor SIGTERM; D51; onsite notes 2026-08-10).

---

## 1. The pipeline, stage by stage (what runs today)

```
[O365 or Gmail inbox]                             ← the "e-responder" address
      │  poll every 5 min (systemd: aedwards-monitor)
      ▼
InboxMonitor.run_once()          monitor.py:139   fetch unread since watermark
      ▼
classify_rfq(subject, body)      parser.py:1095   LLM yes/no: is this an RFQ?
      │  no ─► RejectedEmail row (monitor.py:278) + mark read/moved. DONE.
      ▼ yes
parse_rfq_multi(eml, provider)   parser.py:970    LLM decode → ParsedRFQ(items[...])
      │   (attachments MIME-bridged into the .eml, monitor.py:479)
      ▼
generate_quote(rfq, number)      pricing.py:1422  price each item (formula/catalog)
      ▼
   ┌───────────────── two independent sinks (both optional, env-gated) ─────────────┐
   │                                                                                 │
   ▼  ENABLE_DB_WRITES                          ▼  ENABLE_OUTLOOK_DRAFTS (default on) │
write_quote_to_db()  db_writer.py:346      create_draft(...)  monitor.py:249         │
  → Quote + QuoteLineItem rows                 → PDF in Outlook Drafts folder        │
  → status NEW or NEEDS_PRICING                   (subtotal==0 → [NEEDS PRICING] memo)│
  → customer auto-match/create                                                       │
      │                                                                              │
      └──────────────────────────────┬───────────────────────────────────────────────┘
                                      ▼
                          *** PIPELINE STOPS HERE ***
                                      ▼
                      Human opens web app (aedwards-web / gunicorn)
                      reviews queue, edits line items, clicks Send
                      quote_send()  routes.py:2707  → SENT + retained PDF version
```

**The whole automated system is stages 1–3 above.** Everything past "quote draft" is a
human in the Flask UI. There is no code path from a quote to anything downstream of a
quote.

---

## 2. Stage-by-stage: what EXISTS / what's MISSING

| # | Stage | Exists today? | Where (file:line) | What's missing for the engine |
|---|---|---|---|---|
| 1 | **Intake** | **Email only** — O365 *or* Gmail inbox polled every 5 min | `cli.py:503` (`EMAIL_PROVIDER` o365/gmail), `monitor.py:139` | No fax, no phone, no web form, no API. Chip's "forward a fax to an email that eats it" = not built. Attachments (incl. embedded `.eml`) are handled `monitor.py:501`. |
| 2 | **Classify** | LLM RFQ/not-RFQ gate | `parser.py:1095` `classify_rfq`; reject → `RejectedEmail` `monitor.py:278` | Works; non-deterministic on borderline emails (allanedwards:I58). |
| 3 | **Decode** | LLM extract → `ParsedRFQ` w/ items, ship-to, bill-to, PO | `parser.py:970` `parse_rfq_multi`; product_type list is **dynamic** from `ProductType` table `parser.py:736` | This is the mature part. |
| 4 | **Price** | Formula + catalog lookups per item → `Quote` | `pricing.py:1422` `generate_quote`, `:994` `price_item` | The 80% commodity formula path (onsite §2). Unpriceable → `_tbd_line_item` `pricing.py:980` ($0). No composite calculator. |
| 5 | **Quote persist** | `Quote`+`QuoteLineItem`(+attachments, audit) | `db_writer.py:346` | Gated behind `ENABLE_DB_WRITES`. |
| 6 | **Quote draft out** | PDF built, dropped in **Outlook Drafts** (not sent) | `monitor.py:243`, `pdf_generator.py:596` | Auto-draft, never auto-send. |
| 7 | **Human review gate** | Manual, in web app: queue → edit → Send | `quotes.py`, `routes.py:2707` | **This is the only send path.** No trust-ramp / phased auto-send (onsite §3). |
| 8 | **Send** | Human clicks Send → email via Graph, status→SENT, PDF retained as immutable `QuoteVersion` | `routes.py:2707`–`2867` | Send is 100% human-initiated. |
| 9 | **Order** | **DOES NOT EXIST** | — | No `Order` model, no quote→order transition. See §4. |
| 10 | **Fulfillment / pick-list / shop ping** | **DOES NOT EXIST** | — | No pick list, no "put it on the truck" notification (onsite §4–5). |
| 11 | **Inventory / min-max / reorder** | **DOES NOT EXIST** | — | No stock table, no reorder trigger (onsite §5). |

Absence in stages 9–11 is confirmed by a full-tree search: no `Order`/`order_status`
model, no `pick_list`/`fulfillment`/`inventory`/`reorder`/`min_max` symbols exist in
`src/` (the only matches are `sort_order`, `po_number`, `minimax`, and CSS `minmax`).

---

## 3. Data model — what is persisted vs. what isn't

Persisted (SQLite on prod, `src/app/models.py`):

| Model | models.py | Role |
|---|---|---|
| `Quote` | `:120` | The terminal object. Has `status`, customer/contact/ship-to/bill-to JSON, PO, tax, revision links. |
| `QuoteLineItem` | `:172` | Priced lines. `product_type` (bare string, no FK), `part_number` (single identifier post-refactor), `specs_json` (the basis the price was computed from). |
| `QuoteVersion` | `:203` | Immutable send-time record: retained PDF path + line-item snapshot (`artifact_status="retained"`, `routes.py:2843`). |
| `QuoteAttachment` | `:189` | Original RFQ attachments, inline bytes. |
| `Customer`/`Contact`/`ShipToAddress` | `:74`/`:89`/`:101` | Auto-matched or created from RFQ (`db_writer.py:131` fuzzy match). `ShipToAddress.human_confirmed` gates trust. |
| `PricingTable` | `:222` | `product_type` + `key_fields` JSON + price. The formula/rate inputs. |
| `ProductType` | `:232` | Editable type list (drives decode prompt + UI). |
| `ProductCatalog` | `:242` | Product list; `part_number` nullable (name-only products first-class), `product_type` slug, **no FK to anything**. |
| `ShippingConfig` | `:258` | Freight rate params. |
| `RejectedEmail` | `:269` | Classifier audit trail. |
| `AuditLog` | `:281` | Per-quote actions (`created_from_email`, `sent`). Carries `source_email_id`. |

**Not persisted / not modeled:** orders, fulfillment status, shipments, inventory
levels, reorder points, customer pricing tiers ("pain-in-the-ass tax"), add-on option
catalog. Everything is loosely coupled by **bare lowercase strings**, not foreign keys
(see product-data-model-ground-truth.md) — `QuoteLineItem.product_type`,
`PricingTable.product_type`, `ProductCatalog.product_type` are three independent strings,
and `ProductCatalog` is not referenced by line items (picking a catalog row copies text).

**Quote lifecycle** (`QuoteStatus`, `models.py:18`):
`NEW → IN_REVIEW ⇄ NEEDS_PRICING → SENT`, plus `ARCHIVED`/`REPLACED` (revisions) and
`READY` (settable by hand at `routes.py:1758`, never auto-set). The monitor writes `NEW`,
or `NEEDS_PRICING` when any line is unpriced/$0 (`db_writer.py:371`). **There is no status
past SENT** — the state machine has no "accepted," "ordered," or "fulfilled."

---

## 4. The exact quote→order seam (where "quote" would become "order")

There is no seam in code today — it has to be built. The precise insertion point is the
**send event**, `routes.py:2707` `quote_send()`:

- Today the terminal transition is `quote.status = QuoteStatus.SENT` (`routes.py:2835`),
  which creates an immutable `QuoteVersion` (the retained PDF, `:2839`) and stops.
- **The seam is here:** an accepted quote (customer says "yes," or Chip's auto-flow
  decides) is where an **`Order`** object would be created from the `QuoteVersion`
  snapshot (`line_items_snapshot` already exists, `models.py:214`, and is the natural
  order payload). Nothing consumes a SENT quote today; that is the empty socket.
- Missing pieces the seam requires: (a) an acceptance signal (there is none — no reply
  parsing, no "accept" action, no PO-received event); (b) an `Order` model; (c) a
  quote→order state transition and who/what authorizes it; (d) everything downstream
  (pick list, shop ping, inventory decrement/reorder). Chip's "e-responder just handles
  it" glosses exactly this boundary (onsite §8).

The `QuoteVersion` snapshot is the cleanest handoff artifact: it is immutable, carries
the exact priced lines, and already has `sent_to`/`sent_at`/`sent_by`. Build the order
engine to read from `QuoteVersion`, not from the mutable `Quote`.

---

## 5. Infra & operational constraints for a bigger engine

- **Two systemd services, one box** (`deploy/`): `aedwards-web` (gunicorn, 3 workers,
  `127.0.0.1:8000`, nginx front) and `aedwards-monitor` (the polling CLI,
  `--poll-minutes 5`). Both `EnvironmentFile=/opt/aedwards/.env`, user `aedwards`.
- **DB = SQLite on prod** (`instance/`; monitor opens Flask app context via
  `create_app()` when `ENABLE_DB_WRITES`, `cli.py:569`). A single-writer SQLite file is a
  real constraint for a multi-stage engine with concurrent order/shop/inventory writers —
  flag Postgres before fan-out.
- **Monitor SIGTERM bug is OPEN** (task 340 / allanedwards:I111, still `pending`): the
  monitor blocks in its poll `time.sleep` (`monitor.py:137`) and ignores SIGTERM, so
  systemd SIGKILLs it 90s into every deploy. Because the inbox watermark
  (`.monitor_state.json`) is saved *after* processing (`monitor.py:171`), a kill at the
  wrong moment can reprocess an email and **create a duplicate quote**. Any engine that
  adds auto-send or order creation on this same loop inherits and *amplifies* this
  at-least-once hazard — fix the interruptible-wait + state-ordering before adding
  irreversible downstream actions.
- **State is at-least-once, not exactly-once.** `source_email_id` is populated on every
  quote (`db_writer.py:381`) and is the dedup key the engine should key on, but nothing
  enforces uniqueness on it today.
- **Deploy needs both** `deploy.sh` and `deploy_web.sh`; prod installs the CLI as a wheel
  into a venv (task 340 notes). `deploy_web.sh` also bounces the monitor (doubles the
  SIGKILL exposure per release).
- **Two send paths, two sender identities:** monitor draft path uses the shared O365
  mailbox; the web send path can send *as the logged-in user* (`O365_SEND_AS_USER`,
  `email_service.resolve_quote_sender`, `routes.py:2766`) so replies reach the rep.
- **Guardrails already present** the engine should keep/extend: `SEND_EMAIL_ALLOWLIST`
  recipient gate (`routes.py:2729`), `$0`/unpriced → `NEEDS_PRICING` never auto-sendable
  (`routes.py:2691`), `ShipToAddress.human_confirmed` trust flag.

---

## 6. Gaps the engine must fill (summary for epic 347)

1. **Channel-agnostic intake** — today email-only; add fax/phone/web/API normalizers that
   all produce the same `ParsedRFQ`.
2. **Trust-ramp auto-send** — replace the mandatory human gate (`routes.py:2707`) with a
   phased gate that relaxes over time (onsite §3); one mispriced auto-quote reaches a
   customer, so this is the highest-liability piece.
3. **Acceptance signal** — nothing detects "customer said yes." Needs reply parsing or an
   explicit accept action to fire the quote→order seam.
4. **`Order` object + quote→order transition** — build at the `SENT`/accepted boundary,
   fed by `QuoteVersion.line_items_snapshot`.
5. **Fulfillment: pick list + shop ping** — paper/phone now, structured later (onsite §4).
6. **Inventory: stock table + min-max + auto-reorder** — brand-new subsystem, no source of
   truth exists.
7. **Add-on options catalog** (itemized upcharges) and **customer pricing tiers** — data
   model gaps; both are policy decisions, not just code (onsite §5, §8).
8. **Composite "how much to buy" calculator** — decode/pricing extension for wraps.
9. **Infra hardening first** — fix monitor SIGTERM/dedup (task 340), evaluate Postgres,
   before layering irreversible downstream actions on the at-least-once loop.

---

*Deliverable for epic 347 scoping. Pair with product-data-model-ground-truth.md (the data
model) and onsite-2026-08-10-structured-notes.md (Chip's vision). Canon proposals filed
for the durable facts.*
