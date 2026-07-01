# Manual Quote Creation UX — Design Document

**Date:** 2026-07-01  
**Context:** Chip Edwards asked in the Jul 1 meeting: "Is there a button to open it up and just build one in there?" Currently the only quote entry point is an inbound email to AEResponder@allanedwards.com. This doc covers the design and implementation plan for direct manual creation.

---

## 1. Current Quote Creation Flow (Email-Originated)

### Pipeline Overview

1. An inbound email arrives at `AEResponder@allanedwards.com`.
2. `src/allenedwards/monitor.py` polls the inbox, classifies each message via the RFQ classifier.
3. If classified as an RFQ, `src/allenedwards/db_writer.py::write_quote_to_db()` is called with three objects: the raw `EmailMessage`, a `ParsedRFQ` (customer/line-item data extracted by LLM), and a `PricingQuote` (priced line items).
4. `db_writer.py::_generate_fiscal_quote_number()` generates the quote number. Format: fiscal-year prefix + 3-digit sequence (e.g., `126-001` for FY2026). Sequences reset per fiscal year; the function queries `MAX(quote_number)` for the current prefix to find the next integer.
5. A `Quote` ORM row is inserted with:
   - `quote_number` (generated)
   - `source_email_id`, `sender_email`, `sender_name`, `subject` (from email)
   - `customer_name_raw`, `contact_name`, `contact_email`, `contact_phone`, `po_number`, `ship_to_json` (from ParsedRFQ)
   - `project_name` (from PricingQuote)
   - `status = NEW` (or `NEEDS_PRICING` if all line items have price=0)
   - `customer_id` (matched from existing customers via fuzzy name match, or a new Customer row created)
6. `QuoteLineItem` rows are inserted for each priced item.
7. An `AuditLog` row is inserted with `action="created_from_email"`.

### Key Code Locations

| What | Where |
|------|-------|
| Quote number generation | `src/allenedwards/db_writer.py:65–89` |
| Full email-to-DB write | `src/allenedwards/db_writer.py:289–372` |
| `Quote` model (all fields) | `src/app/models.py:127–160` |
| Quote editor route (`GET /quotes/<id>`) | `src/app/routes.py:825–839` |
| Quote queue route (`GET /quotes/`) | `src/app/quotes.py:102–142` |
| Queue page template | `src/app/templates/quotes/queue.html` |
| Quote editor template | `src/app/templates/quotes/detail.html`, `_editor.html` |
| Layout / nav bar | `src/app/templates/layout.html:289–305` |

### What the Editor Shows

The editor (`/quotes/<id>`) has four sections:

1. **Status Bar** — current status (NEW/IN_REVIEW/etc.), reviewer, action buttons.
2. **Quote Fields** — quote number, project name, customer notes, internal notes. Autosaves on blur.
3. **Customer Info** — company, contact name/email/phone, ship-to address. Autosaves on blur.
4. **Line Items** — products with type, description, qty, unit price. Autosaves per card on focusout.

All fields except `quote_number` are optional. The editor handles a quote with no line items and no customer info gracefully.

---

## 2. Email-Specific Fields — Impact Analysis

The `Quote` model has four email-specific columns:

| Field | Nullable? | Used in editor? | Used in PDF? |
|-------|-----------|-----------------|--------------|
| `source_email_id` | Yes | No | No |
| `sender_email` | Yes | No | No |
| `sender_name` | Yes | No | No |
| `subject` | Yes | No | No |

All four are `nullable=True` and never referenced in the edit UI, the PDF generator, or the quote-send flow. **No special handling is needed for manually created quotes** — these fields simply remain `None`. The queue display falls back to `customer_name_raw or sender_name or "Unknown"`, so a manually created quote with no customer info shows "Unknown" until the user fills it in.

---

## 3. Recommended UX Design

### Decision: Immediate Redirect (No Modal, No Form Gate)

Chip's ask is frictionless: "open it up and just build one in there." The right answer is a single click that lands the user directly on the editor. No modal, no upfront form asking for customer name — just create a blank quote and drop the user on the edit page. Everything is editable in place.

This matches how the editor already works: all fields are optional, autosave handles every change, and the user can fill things in any order. There is no reason to pre-collect anything.

### Button Placement: Queue Page Header

The "New Quote" button belongs in the queue page header (alongside the "Quote Queue Dashboard" heading), not the nav bar. Reasons:
- The nav bar (`layout.html:289–305`) is already dense (Quotes, Customers, Admin, Users, Rejected, Logout).
- The queue page is the natural launching point — the user is looking at their list of quotes and decides to create one.
- A persistent nav button would suggest you can create a quote from *any* page (e.g., mid-edit), which is unnecessary and potentially confusing.

### Wireframe (Prose)

**Queue page header (current):**
```
Quote Queue Dashboard
```

**Queue page header (with button):**
```
Quote Queue Dashboard          [+ New Quote]
```

The button sits right-aligned in the existing `.queue-header` flex container (which already uses `justify-content: space-between`). Clicking it submits a POST to `/quotes/new` and the server redirects to the new quote's edit page.

### UX Flow

```
User on /quotes/
  → clicks "+ New Quote"
  → POST /quotes/new
  → server: generate quote number, insert blank Quote row, AuditLog
  → 302 redirect → GET /quotes/<new_id>
  → user lands on editor, status=NEW, all fields blank
  → fills in customer info, line items, etc. (autosaves on blur)
```

---

## 4. Technical Implementation Plan

All changes are additive. No model changes, no migrations needed.

### Step 1 — Add the route: `POST /quotes/` in `src/app/quotes.py`

Add a new route to the existing `quotes_bp` blueprint. The route generates a quote number, creates the `Quote` row, writes an audit log, and redirects.

```python
# src/app/quotes.py — add after existing imports
from sqlalchemy import func
from .models import Quote, QuoteStatus, AuditLog  # AuditLog is already in models.py

@quotes_bp.post("/")
@login_required
def create_quote():
    """Create a blank quote and redirect to its editor."""
    # Generate quote number (same fiscal-year logic as db_writer.py)
    now = datetime.utcnow()
    prefix = f"1{now.year % 100}"  # 2026 → "126"
    pattern = f"{prefix}-%"
    result = db.session.query(func.max(Quote.quote_number)).filter(
        Quote.quote_number.like(pattern)
    ).scalar()
    if result:
        try:
            seq = int(result.split("-")[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1
    quote_number = f"{prefix}-{seq:03d}"

    quote = Quote(
        quote_number=quote_number,
        status=QuoteStatus.NEW,
    )
    db.session.add(quote)
    db.session.flush()

    audit = AuditLog(
        quote_id=quote.id,
        action="created_manually",
        details={"quote_number": quote_number},
    )
    db.session.add(audit)
    db.session.commit()

    return redirect(f"/quotes/{quote.id}")
```

**Imports to add to `quotes.py`:** `datetime` (from datetime), `redirect` (from flask), `login_required` (from flask_login), `AuditLog` (from .models). Check which are already imported.

**Note on quote number generation:** This duplicates the logic from `db_writer.py:65–89`. The cleaner long-term move is to extract it into a shared helper in `src/app/` (e.g., `src/app/quote_utils.py`), but for the initial implementation inlining it here is fine — the function is small and self-contained.

### Step 2 — Add the button: `src/app/templates/quotes/queue.html`

The `.queue-header` div (line 52–54) already uses `justify-content: space-between`. Add a button as a form POST inside it:

```html
<!-- queue.html:52–54 — replace the existing queue-header div -->
<div class="queue-header">
  <h1 style="margin:0">Quote Queue Dashboard</h1>
  <form method="post" action="/quotes/">
    <button type="submit">+ New Quote</button>
  </form>
</div>
```

No HTMX needed — a plain form POST followed by a redirect is the correct pattern here.

### Step 3 — Import `AuditLog` in `quotes.py`

Check the existing import in `src/app/quotes.py:11`:
```python
from .models import Quote, QuoteLineItem, QuoteStatus, User
```
Add `AuditLog` to this import.

### No Other Changes Required

- No model changes — all `Quote` fields are already nullable except `quote_number` and `status` (which has a default).
- No migration — schema is unchanged.
- No template changes beyond `queue.html`.
- No changes to `db_writer.py`, `monitor.py`, or the PDF generator.
- The existing `quote_detail` route (`routes.py:825`) already handles the first-open transition (sets `reviewed_by`, moves status to `IN_REVIEW`).

---

## 5. Edge Cases and Gotchas

### Quote Number Collision Race

`_generate_fiscal_quote_number()` uses `MAX(quote_number)` without a database lock. Two simultaneous manual-create POSTs could read the same MAX and generate duplicate quote numbers, hitting the `UNIQUE` constraint on `quote_number`. This is the same race as the email pipeline.

**Current mitigation:** The email pipeline runs single-threaded (one monitor process), so this race never occurs in practice. For manual creation, the same race is very unlikely (users rarely create two quotes simultaneously), and the UNIQUE constraint provides a safety net — the second transaction will fail with an IntegrityError.

**Recommendation for the implementation:** Wrap the commit in a try/except for IntegrityError and retry once (regenerate the number). This is the same approach used in other parts of the codebase.

### Status After Manual Creation

Manually created quotes start as `QuoteStatus.NEW`. When the user opens the edit page, `quote_detail` (routes.py:830–834) automatically transitions it to `IN_REVIEW` and sets `reviewed_by` to the current user. This is correct behavior — the person creating the quote is implicitly reviewing it.

An alternative is to start as `QuoteStatus.NEEDS_PRICING` (since there are no priced line items). However, `NEW` is cleaner: it doesn't falsely imply the quote already has line items that just lack prices. The user will naturally add line items once they're on the editor.

### Customer Matching

Email-originated quotes run through a fuzzy customer-matching step (`_match_customer()` in `db_writer.py`) that links quotes to existing Customer rows. Manual quotes skip this entirely — `customer_id` is `None` at creation.

The autosave on the Customer Info section calls `_sync_linked_customer_from_quote()` (routes.py:872, 804–814), which does run customer matching on every customer info save. So as soon as the user types a company name and tabs out, the quote will be linked to an existing customer (or a new one created). This works correctly for manual quotes with no changes required.

### `@login_required`

The existing queue and editor routes all use `@login_required`. The new `POST /quotes/` route must also be decorated with `@login_required` to prevent unauthenticated quote creation. `login_required` is already imported in `routes.py`; check whether it's imported in `quotes.py` (it currently is not — add it from `flask_login`).

### CSRF

The app uses no CSRF tokens (standard Flask-WTF CSRF protection is not in place). The new form POST matches the pattern of existing form POSTs in the app (e.g., line-item delete, status changes). No new CSRF exposure is introduced.
