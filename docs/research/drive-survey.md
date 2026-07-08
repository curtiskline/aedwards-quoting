# Allan Edwards SharePoint Drive Survey

Date: 2026-07-08
Task: 244
Site: `https://allanedwardstulsa.sharepoint.com/sites/AllanEdwards2023`

## Scope

This survey is a read-only intake assessment for ingestion scoping. It focuses on:

- file-type distribution by major SharePoint library
- representative sample files for major formats
- born-digital vs scanned/OCR observations
- likely conversion and ingestion difficulty by format
- mapping each source area to likely pipeline stages

No bulk export was performed. Evidence was gathered from the authenticated SharePoint browser session, Site Contents, and SharePoint Search REST API queries.

## Important Caveat

SharePoint Search materially undercounts the site contents. Site Contents reports about 71,000 items across the main libraries, while indexed search results across those same libraries only expose a small subset. Treat the search API as a distribution signal, not a census.

This matters for planning:

- library item counts below are authoritative for rough volume
- extension counts below are directional distributions from the search index
- exact file counts by extension and exact byte sizes will require Microsoft Graph with `Sites.Read.All` or `Files.Read.All` application permission and Azure admin consent

## Library Volumes

Observed from Site Contents on 2026-07-08:

| Library | SharePoint items |
| --- | ---: |
| Sales, Engineering & Customer Service | 32,231 |
| Fabrication & Warehouse | 18,526 |
| Leadership Team | 8,122 |
| Document Control | 7,608 |
| Finance & Administration | 4,855 |
| Site Assets | 33 |
| Site Pages | 5 |

Approximate total: 71,380 items

## Indexed File-Type Distribution By Library

Observed via `/_api/search/query?...refiners='FileType'` with library-specific `path:` filters on 2026-07-08.

| Library | Indexed rows | Coverage vs library size | Top indexed file types |
| --- | ---: | ---: | --- |
| Sales, Engineering & Customer Service | 659 | 2.0% | `pdf` 357, `xlsx` 40, `docx` 23, `xlsm` 5, `mp4` 5, `jpg` 3, `png` 3, `mov` 1, `zip` 1, `sldprt` 1, `doc` 1, `pptx` 1 |
| Fabrication & Warehouse | 66 | 0.4% | `pdf` 23, `xlsx` 9, `docx` 5, `eml` 1, `lnk` 1, `zip` 1 |
| Leadership Team | 21 | 0.3% | `xlsx` 10, `zip` 1, `jpg` 1 |
| Document Control | 196 | 2.6% | `pdf` 75, `docx` 13, `xlsx` 9, `png` 5, `csv` 2 |
| Finance & Administration | 490 | 10.1% | `pdf` 121, `xlsx` 42, `docx` 20, `doc` 20, `zip` 3, `xls` 3, `png` 2, `mp4` 1, `pptx` 1 |
| Site Assets | 4 | 12.1% | no meaningful indexed breakdown returned |
| Site Pages | 5 | 100% | `aspx` 4 |

### Distribution Takeaways

- PDFs are the dominant indexed format in the operational libraries.
- Excel is the second major class and appears in modern workbook, macro-enabled workbook, and legacy `.xls` forms.
- Word documents remain common, including both `.docx` and legacy `.doc`.
- There are smaller but important long-tail formats: image files, videos, ZIP archives, EML email files, CSV exports, and at least one SolidWorks part (`.sldprt`).
- Leadership Team appears lightly indexed despite large library volume, so its true format mix is probably broader than search shows.

## Representative Samples

These are representative paths returned by SharePoint Search on 2026-07-08.

### PDF samples by major library

| Library | Sample path | OCR/text signal |
| --- | --- | --- |
| Sales, Engineering & Customer Service | `Sales  Marketing/Sales Orders 2026/.../BOL 126-0265 (SIGNED) LTL.pdf` | Empty search summary. Likely image-heavy or poorly OCR'd signed shipping document. |
| Sales, Engineering & Customer Service | `Sales  Marketing/Sales Orders 2026/.../MTR 126-0256 PO# 4302294887 2ND SHIPMENT.pdf` | Strong text extraction. Born-digital packing/shipping document. |
| Fabrication & Warehouse | `Operations/A405209_Complete.pdf` | Minimal extracted text. Likely compiled certification/MTR packet or low-text PDF. |
| Fabrication & Warehouse | `Operations/Warehouse/Training/Omega Certified Installer Tests.pdf` | Search returns title text only. Likely simple PDF, unclear OCR quality. |
| Document Control | `Shared Documents/AE MFG W9.pdf` | Strong text extraction. Standard born-digital tax form PDF. |
| Document Control | `Shared Documents/AE MFG/Invoices/INV-125-0103.pdf` | Strong text extraction. Born-digital invoice PDF. |
| Finance & Administration | `Finance/.../Grand Bank - x7700 - Savings/AEINC BANK STMT GRAND 7700 2026-06.pdf` | Strong text extraction. Born-digital bank statement PDF. |

### Other major file-type samples

| Type | Sample path | Ingestion note |
| --- | --- | --- |
| `xlsx` | `Sales  Marketing/Engineering/MTR Checker.xlsx` | Spreadsheet content indexes cleanly; strong candidate for structured extraction and semantic retrieval. |
| `xlsm` | `Sales  Marketing/Engineering/OmegaWrap/.../OmegaWrap_Calculator_v2.03_072026.xlsm` | Macro-enabled workbook; text is indexable, but macro content and formulas require safe handling. |
| `xls` | `Finance/.../QBO Conversion/Import-Vendors.xls` | Legacy Excel; convertible but usually needs normalization to modern formats. |
| `docx` | `Sales  Marketing/Sales Orders 2026/.../PACKING SLIP 126-0256 2ND SHIPMENT.docx` | Text-rich and straightforward to extract. |
| `doc` | `Finance/Safety/Respirator Test Form.doc` | Legacy Word; searchable, but older binary format increases conversion friction. |
| `csv` | `Shared Documents/ARCHIVE HOLD/Site Inventory/SiteInventory-allanedwardstulsa-CommunicationSite.csv` | Already structured and highly ingestion-friendly. |
| `png` | `Sales  Marketing/.../CSA Z662 Table 10.2 3.png` | Image-only; requires OCR or vision pipeline if content matters. |
| `jpg` | `Sales  Marketing/.../AE_WePledge_Final (1).jpg` | Image asset; same OCR/vision requirement as PNG. |
| `mp4` | `Sales  Marketing/.../ConcreteCoating_PT1` | Binary media; transcript generation needed for content retrieval. |
| `zip` | `Finance/Finance/Pigging Houston/System Volume Information.zip` | Archive container; requires unpacking and secondary classification. |
| `eml` | `Operations/Fabrication/MATEC/RE_ INVOICE FOR Purchase Order #PO-121-0816.eml` | Good fit for email-specific parsing and attachment extraction. |
| `pptx` | `Finance/Finance/CDI Docs/2025 CDI Organizational Chart.pptx` | Modern Office presentation; extractable text and slide metadata. |
| `sldprt` | `Sales  Marketing/.../KWR` | Native CAD format; not text-friendly and likely needs special viewer or metadata-only treatment. |
| `aspx` | `SitePages/Daily-Ops-Brief.aspx` | SharePoint-native page content; can be pulled as HTML/page text rather than as a file conversion job. |

## OCR And Conversion Assessment

### PDF classes

- A mixed PDF estate is already visible.
- Many finance, invoice, W-9, and packing-slip PDFs expose strong text in search results and should ingest cleanly with standard PDF text extraction.
- Some signed logistics PDFs and compiled certification packets expose little or no summary text, which suggests scanned pages, image-heavy content, or poor OCR. Those should be routed through OCR before downstream extraction.

### Office documents

- `.xlsx`, `.xlsm`, `.docx`, and `.pptx` appear largely text-indexable and should convert predictably.
- `.xls` and `.doc` are workable but should be normalized early because legacy binary formats tend to create parser edge cases.
- Macro-enabled spreadsheets should be treated as documents, not executed.

### Images and media

- `.png` and `.jpg` are not meaningfully searchable without OCR or vision classification.
- `.mp4` and `.mov` require transcription if content is to be searchable or embedded.

### Containers and special formats

- `.zip` files are multi-step ingestion inputs: unpack, classify contents, then process by contained file type.
- `.eml` files should preserve headers, body, and attachment relationships.
- `.sldprt` is a special-case engineering asset. Practical ingestion is likely limited to filename, location, and any available CAD metadata unless a dedicated CAD converter is introduced.

## Source-To-Pipeline Mapping

| Source area | Likely dominant content | Recommended pipeline stage |
| --- | --- | --- |
| Sales, Engineering & Customer Service | quotes, sales orders, engineering spreadsheets, certifications, media | mixed-document ingestion with PDF extraction, Office parsing, OCR fallback, media transcription, CAD exception lane |
| Fabrication & Warehouse | operational PDFs, spreadsheets, vendor email, training docs | PDF extraction plus OCR fallback, spreadsheet parsing, email parsing |
| Leadership Team | administrative spreadsheets and ad hoc documents | Office parsing first, then catch-all document OCR fallback |
| Document Control | controlled PDFs, invoices, exports, document images | PDF extraction, OCR fallback, CSV direct load |
| Finance & Administration | bank statements, legacy Office docs, presentations, archives | PDF extraction, Office normalization, ZIP expansion, selective OCR |
| Site Assets | branding/web assets | likely exclude from primary knowledge ingestion unless explicitly needed |
| Site Pages | SharePoint pages | fetch/render page HTML/text directly rather than file conversion |

## Ingestion Difficulty Summary

| Format class | Expected difficulty | Notes |
| --- | --- | --- |
| Text PDFs | Low | High-value and abundant. |
| Scanned/image-heavy PDFs | Medium | OCR required; quality will vary by scan source. |
| Modern Office (`xlsx`, `xlsm`, `docx`, `pptx`) | Low to medium | Good extraction potential; macro-enabled files need safe handling. |
| Legacy Office (`xls`, `doc`) | Medium | Convert early to reduce parser variance. |
| CSV | Low | Structured and easy to ingest. |
| Images (`png`, `jpg`) | Medium | OCR or vision classification needed. |
| Media (`mp4`, `mov`) | Medium to high | Transcript generation and storage cost considerations. |
| Archives (`zip`) | Medium | Recursive unpacking and reclassification required. |
| Email (`eml`) | Medium | Preserve thread and attachment metadata. |
| CAD (`sldprt`) | High | Specialized handling or metadata-only ingestion likely. |
| SharePoint pages (`aspx`) | Low | Treat as CMS/page extraction, not binary file conversion. |

## Recommended Next Step

For proposal scoping, this survey is enough to justify a mixed ingestion pipeline with:

- standard PDF text extraction
- OCR fallback for scanned/image-heavy PDFs and image files
- Office document parsing with legacy-format normalization
- email and archive handling as separate lanes
- optional transcription for video
- CAD handled as a carve-out or metadata-only phase

If exact by-extension counts or total byte sizes are needed for pricing, request Microsoft Graph application access with Azure admin consent from Jackson Technical. The practical minimum appears to be `Sites.Read.All` or `Files.Read.All`.
