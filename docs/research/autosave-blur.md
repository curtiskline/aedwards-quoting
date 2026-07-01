# Autosave Quote Fields & Customer Info on Blur (K197)

## Summary
Verified implementation of autosave for quote fields and customer info forms on the quote editor page.

## Files Verified

### `src/app/routes.py` (line 875)
- Response mismatch fixed: `quote_update_customer` now returns `_render_customer_info(quote)` instead of `_render_editor(quote)`
- Matches the `hx-target="#editor-customer-info"` directive in the template

### `src/app/templates/quotes/_quote_fields.html`
- Autosave JS added (focusout + 200ms debounce + 5s idle timer)
- Pattern identical to `_line_items.html` reference implementation
- Manual "Save Quote Fields" button retained

### `src/app/templates/quotes/_customer_info.html`
- Same autosave JS pattern
- Manual "Save Customer Info" button retained

### JS Pattern (shared across all three)
- `focusout` → 200ms debounce → check `form.contains(document.activeElement)` → `htmx.trigger(form, 'submit')`
- `focusin` cancels pending leave timer (handles Chrome transient body focus)
- `htmx:beforeRequest` clears both timers to prevent double-submit
- `_line_items.html` additionally tracks dirty forms with `sendBeacon` for page-unload safety (not replicated in quote fields / customer info)

## Key Routes
- `POST /quotes/<id>/meta` → `quote_update_meta` → `_render_quote_fields(quote)` ✅
- `POST /quotes/<id>/customer` → `quote_update_customer` → `_render_customer_info(quote)` ✅
