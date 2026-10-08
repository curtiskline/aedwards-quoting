# Existing application inventory

Verified from checkout `d127c4c`, 2026-10-08. No production data queried. Includes all routes in routes.py plus all seven other route modules, and every template. API/actions/partials are not separate screens.

## Screen map

| Current screen | Mockup destination |
|---|---|
| Dashboard; quote queue | index.html, pipeline.html, quotes.html |
| Quote detail/editor, manual create, pricing, send, revision, duplicate | quote.html, new-quote.html, send.html, sent.html, revision.html, duplicate.html |
| Accept quote, order queue/detail | sent.html, orders.html, order.html |
| Shop queue, pick sheet | shop.html, pick-sheet.html, picked.html, loaded.html, shipped.html |
| Stock, detail, reorder queue/sheet | stock.html, stock-item.html, purchasing.html, vendor-order.html |
| Stock seeding/import, unmatched shipments | stock-setup.html (read-only scope overview) |
| Customer list/detail/new/edit | customers.html, customer.html, customer-edit.html |
| Pricing, catalog, product types, shipping defaults, trust ramp | settings.html (read-only example sections) |
| Users, rejected email, failed intake | settings.html, intake.html (read-only examples) |
| Login, bootstrap, waiting, set password | account.html (scope overview) |
| No current outbound docs/storefront | documents.html; guide.html (planned/deferred) |

## Routes

### src/app/routes.py

| Method | Route | Handler |
|---|---|---|
| GET | `/` | `dashboard` (line 114) |
| GET | `/quotes/<int:quote_id>` | `quote_detail` (line 1522) |
| GET | `/quotes/<int:quote_id>/attachments/<int:attachment_id>` | `quote_attachment_download` (line 1545) |
| POST | `/quotes/<int:quote_id>/delete` | `quote_delete` (line 1565) |
| GET | `/quotes/<int:quote_id>/duplicate-form` | `quote_duplicate_form` (line 1597) |
| POST | `/quotes/<int:quote_id>/revise` | `quote_revise` (line 1621) |
| POST | `/quotes/<int:quote_id>/duplicate` | `quote_duplicate` (line 1695) |
| POST | `/quotes/<int:quote_id>/meta` | `quote_update_meta` (line 1773) |
| POST | `/quotes/<int:quote_id>/customer` | `quote_update_customer` (line 1786) |
| POST | `/quotes/<int:quote_id>/confirm-ship-to` | `quote_confirm_ship_to` (line 1797) |
| POST | `/quotes/<int:quote_id>/status` | `quote_update_status` (line 1818) |
| POST | `/quotes/<int:quote_id>/totals` | `quote_update_totals` (line 1843) |
| POST | `/quotes/<int:quote_id>/line-items/add` | `quote_add_line_item` (line 1942) |
| POST | `/quotes/<int:quote_id>/line-items/<int:item_id>/calc-total` | `quote_calc_line_item_total` (line 1995) |
| POST | `/quotes/<int:quote_id>/line-items/<int:item_id>/update` | `quote_update_line_item` (line 2047) |
| GET | `/quotes/<int:quote_id>/on-site-fill-history` | `on_site_fill_history` (line 2251) |
| GET | `/api/product-catalog/search` | `product_catalog_search` (line 2260) |
| GET | `/api/product-catalog/lookup/<string:part_number>` | `product_catalog_lookup` (line 2291) |
| POST | `/quotes/<int:quote_id>/line-items/<int:item_id>/delete` | `quote_delete_line_item` (line 2312) |
| POST | `/quotes/<int:quote_id>/line-items/<int:item_id>/move` | `quote_move_line_item` (line 2327) |
| GET | `/admin/pricing` | `pricing_admin` (line 2352) |
| POST | `/admin/trust-ramp/tier` | `update_trust_ramp_tier` (line 2401) |
| POST | `/admin/trust-ramp/dials` | `update_trust_ramp_dials` (line 2421) |
| POST | `/admin/trust-ramp/holds/add` | `add_send_hold` (line 2446) |
| POST | `/admin/trust-ramp/holds/<int:hold_id>/delete` | `delete_send_hold` (line 2484) |
| POST | `/admin/pricing/<int:row_id>` | `update_pricing_row` (line 2497) |
| POST | `/admin/pricing/add` | `add_pricing_row` (line 2522) |
| POST | `/admin/pricing/<int:row_id>/delete` | `delete_pricing_row` (line 2547) |
| POST | `/admin/catalog/add` | `add_catalog_item` (line 2579) |
| POST | `/admin/catalog/<int:item_id>/update` | `update_catalog_item` (line 2614) |
| POST | `/admin/catalog/<int:item_id>/delete` | `delete_catalog_item` (line 2663) |
| POST | `/admin/shipping-config` | `update_shipping_config` (line 2689) |
| POST | `/admin/product-types/add` | `add_product_type` (line 2721) |
| POST | `/admin/product-types/<int:type_id>/update` | `update_product_type` (line 2754) |
| POST | `/admin/product-types/<int:type_id>/move` | `move_product_type` (line 2772) |
| GET | `/quotes/<int:quote_id>/preview-pdf` | `quote_preview_pdf` (line 2876) |
| GET | `/quotes/<int:quote_id>/send-form` | `quote_send_form` (line 2888) |
| POST | `/quotes/<int:quote_id>/send-preview` | `quote_send_preview` (line 2915) |
| POST | `/quotes/<int:quote_id>/send` | `quote_send` (line 2924) |
| GET | `/healthz` | `healthz` (line 3056) |

40 route declarations.

### src/app/quotes.py

| Method | Route | Handler |
|---|---|---|
| GET | `/quotes/` | `queue` (line 159) |
| GET | `/quotes/badge` | `badge` (line 229) |
| POST | `/quotes/` | `create` (line 239) |
| POST | `/quotes/<int:quote_id>/claim` | `claim` (line 257) |
| POST | `/quotes/<int:quote_id>/release` | `release` (line 276) |

5 route declarations.

### src/app/orders.py

| Method | Route | Handler |
|---|---|---|
| GET | `/quotes/<int:quote_id>/accept-form` | `quote_accept_form` (line 245) |
| POST | `/quotes/<int:quote_id>/accept` | `quote_accept` (line 264) |
| GET | `/orders/` | `queue` (line 346) |
| GET | `/orders/<int:order_id>` | `detail` (line 385) |
| POST | `/orders/<int:order_id>/status` | `transition` (line 436) |

5 route declarations.

### src/app/fulfillment.py

| Method | Route | Handler |
|---|---|---|
| POST | `/orders/<int:order_id>/pick-list` | `generate` (line 338) |
| GET | `/pick-lists/` | `queue` (line 389) |
| POST | `/pick-lists/<int:pick_list_id>/status` | `transition` (line 445) |
| GET | `/pick-lists/<int:pick_list_id>/sheet` | `sheet` (line 471) |

4 route declarations.

### src/app/inventory.py

| Method | Route | Handler |
|---|---|---|
| GET | `/stock/` | `stock_list` (line 730) |
| GET | `/stock/items/<int:item_id>` | `item_detail` (line 754) |
| POST | `/stock/items/<int:item_id>/receipt` | `add_receipt` (line 774) |
| POST | `/stock/items/<int:item_id>/adjustment` | `add_adjustment` (line 788) |
| GET | `/stock/unmatched` | `unmatched_list` (line 807) |
| POST | `/stock/unmatched/<int:movement_id>/resolve` | `resolve` (line 822) |
| GET | `/stock/seed` | `seed_screen` (line 872) |
| POST | `/stock/seed/rows/<int:catalog_id>` | `seed_row_save` (line 877) |
| GET/POST | `/stock/seed/import` | `seed_import` (line 997) |
| GET | `/stock/reorders/` | `reorders_list` (line 1064) |
| GET | `/stock/reorders/<int:reorder_id>/sheet` | `reorder_sheet` (line 1084) |
| POST | `/stock/reorders/<int:reorder_id>/mark-sent` | `reorder_mark_sent` (line 1094) |
| POST | `/stock/reorders/<int:reorder_id>/receive` | `reorder_receive` (line 1117) |

13 route declarations.

### src/app/customers.py

| Method | Route | Handler |
|---|---|---|
| GET | `/customers/api/match` | `api_match` (line 95) |
| GET | `/customers/` | `customer_list` (line 138) |
| GET | `/customers/<int:customer_id>` | `customer_detail` (line 170) |
| GET | `/customers/new` | `customer_new` (line 188) |
| POST | `/customers/` | `customer_create` (line 193) |
| GET | `/customers/<int:customer_id>/edit` | `customer_edit` (line 219) |
| POST | `/customers/<int:customer_id>` | `customer_update` (line 225) |
| DELETE | `/customers/<int:customer_id>` | `customer_delete` (line 255) |
| GET | `/customers/partial/contact-row` | `partial_contact_row` (line 272) |
| GET | `/customers/partial/address-row` | `partial_address_row` (line 278) |

10 route declarations.

### src/app/admin_routes.py

| Method | Route | Handler |
|---|---|---|
| GET | `/admin/users` | `users_page` (line 29) |
| POST | `/admin/users` | `create_user` (line 36) |
| POST | `/admin/users/<int:user_id>/delete` | `delete_user` (line 60) |
| GET | `/admin/rejected-emails` | `rejected_emails` (line 83) |
| GET | `/admin/failed-intakes` | `failed_intakes` (line 100) |
| POST | `/admin/failed-intakes/<int:intake_id>/resolve` | `resolve_failed_intake` (line 124) |

6 route declarations.

### src/app/auth_routes.py

| Method | Route | Handler |
|---|---|---|
| GET | `/auth/login` | `login` (line 53) |
| GET/POST | `/auth/bootstrap` | `bootstrap_user` (line 60) |
| POST | `/auth/magic-link` | `request_magic_link` (line 85) |
| GET | `/auth/waiting` | `waiting` (line 119) |
| GET | `/auth/check-magic-link` | `check_magic_link` (line 126) |
| GET | `/auth/magic/<token>` | `consume_magic_link` (line 151) |
| POST | `/auth/password` | `password_login` (line 167) |
| GET/POST | `/auth/set-password` | `set_password` (line 185) |
| POST | `/auth/logout` | `logout` (line 208) |

9 route declarations.

## Every template

| Template | Kind | Headings / role |
|---|---|---|
| `admin/failed_intakes.html` | Page | Failed Intakes |
| `admin/rejected_emails.html` | Page | Rejected Emails |
| `admin/users.html` | Page | User Admin; Add User; Users |
| `auth/bootstrap.html` | Page | Create First User |
| `auth/login.html` | Page | Sign in; Magic Link; Password |
| `auth/set_password.html` | Page | Set password |
| `auth/waiting.html` | Page | Check Your Email |
| `customers/_address_row.html` | Partial | Shared rendering / content; see mapped parent screen above |
| `customers/_contact_row.html` | Partial | Shared rendering / content; see mapped parent screen above |
| `customers/_table.html` | Partial | Shared rendering / content; see mapped parent screen above |
| `customers/detail.html` | Page | ; Details; Contacts; Ship-To Addresses; Quote History |
| `customers/form.html` | Page | Customer; Company; Contacts; Ship-To Addresses |
| `customers/list.html` | Page | Customers |
| `dashboard.html` | Page | Dashboard |
| `fulfillment/_queue_body.html` | Partial | Shared rendering / content; see mapped parent screen above |
| `fulfillment/_queue_row.html` | Partial | Shared rendering / content; see mapped parent screen above |
| `fulfillment/queue.html` | Page | Shop queue |
| `fulfillment/sheet.html` | Page | PICK SHEET; Customer; Ship to |
| `layout.html` | Layout | Shared rendering / content; see mapped parent screen above |
| `orders/_accept_form.html` | Partial | Mark Quote Accepted |
| `orders/_accept_result.html` | Partial | Order CreatedAlready Accepted; Cannot Accept |
| `orders/_queue_body.html` | Partial | Shared rendering / content; see mapped parent screen above |
| `orders/_status_panel.html` | Partial | Shared rendering / content; see mapped parent screen above |
| `orders/detail.html` | Page | Order for Quote (version ); Acceptance provenance; Ordered lines (frozen at send time); History |
| `orders/queue.html` | Page | Orders |
| `partials/pricing_catalog.html` | Partial | Pricing Catalog;  |
| `partials/pricing_row.html` | Partial | Shared rendering / content; see mapped parent screen above |
| `partials/product_catalog_table.html` | Partial | Product Catalog |
| `partials/product_types_table.html` | Partial | Product Types |
| `partials/shipping_config_form.html` | Partial | Auto-Ship Pricing Defaults |
| `partials/trust_ramp.html` | Partial | Trust Ramp; Auto-send dials (Tier 2); Recent auto-send activity; Send holds |
| `partials/user_rows.html` | Partial | Shared rendering / content; see mapped parent screen above |
| `pricing_admin.html` | Page | Admin |
| `quotes/_attachments.html` | Partial | Attachments |
| `quotes/_customer_info.html` | Partial | Customer Info |
| `quotes/_duplicate_form.html` | Partial | Duplicate Quote |
| `quotes/_editor.html` | Partial | Quote; Revision History |
| `quotes/_fill_fields.html` | Partial | On-site filling |
| `quotes/_fill_history.html` | Partial | Shared rendering / content; see mapped parent screen above |
| `quotes/_line_items.html` | Partial | Line Items; Add Line Item; Totals |
| `quotes/_line_total.html` | Partial | Shared rendering / content; see mapped parent screen above |
| `quotes/_queue_body.html` | Partial | Shared rendering / content; see mapped parent screen above |
| `quotes/_quote_fields.html` | Partial | Quote Fields |
| `quotes/_recommendation.html` | Partial | Recommended for send? &#10003; Recommended Not recommended Confidence % (threshold %) |
| `quotes/_send_form.html` | Partial | Send Quote |
| `quotes/_send_result.html` | Partial | Quote Sent; Send Failed |
| `quotes/_status_bar.html` | Partial | Shared rendering / content; see mapped parent screen above |
| `quotes/detail.html` | Page | Shared rendering / content; see mapped parent screen above |
| `quotes/queue.html` | Page | Quote Queue Dashboard |
| `stock/_seed_row.html` | Partial | Shared rendering / content; see mapped parent screen above |
| `stock/detail.html` | Page | ; Record receipt; Record adjustment; Movement history |
| `stock/list.html` | Page | Stock |
| `stock/reorder_sheet.html` | Page | PURCHASE ORDER; Customer / job |
| `stock/reorders.html` | Page | Reorders; Recently received |
| `stock/seed.html` | Page | Stock seeding |
| `stock/seed_import.html` | Page | CSV import |
| `stock/unmatched.html` | Page | Unmatched shipments |
