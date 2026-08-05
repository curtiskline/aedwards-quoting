# Prod data cleanup — signature-derived ship-to addresses (task 338)

**Status: READ-ONLY investigation + proposed change set. Nothing applied.**
Snapshot: prod DB `/opt/aedwards/instance/allenedwards.db` copied 2026-08-05 15:38, corroborated
against the live DB the same session (30 ship_to rows, 0 non-default — identical). Host:
DigitalOcean droplet 157.230.227.28.

Related decisions: D37 (signature address is bill-to; no ship-to → price product only),
D12 (freight auto-calculates from ship-to distance), I106 (country-blind ZIP collision),
K259/K331 (prior fixes). This task does **not** regress 331's tolerant reader.

---

## Bottom line

1. **There is no reliable data discriminator between "auto-created from a signature" and
   "entered by a human."** The `ship_to_address` table has no provenance column, no
   `created_at`, no "human-confirmed" flag, and the audit log tracks *quotes* only, never
   `ship_to_address`. Three different code paths write this one table and none stamp origin
   (see "Why there is no discriminator"). Classification below is **inferred from content**,
   not read from the data, and is tiered by confidence. Per project anti-guidance I do **not**
   claim certainty I cannot support.

2. **3 rows are unambiguously corrupt** (freight term / company name in an address field /
   nonsense geography) and can be corrected with high confidence: STA **#22, #29, #30**.

3. **3 rows are a foreign address stored as `country='US'`** (freight-collision bug, I106):
   STA **#6, #20, #21**. #21 is additionally a different company's address on the wrong
   customer.

4. **The remaining ~24 rows cannot be classified from data.** Each is *plausibly* either a
   real designated jobsite or a signature/bill-to office. They need a human (Chip) to confirm
   per row. The table lists exactly what to confirm for each.

5. **The durable fix for problem (ii) is a positive `human_confirmed` marker on
   `ship_to_address`, gating `_hydrate_quote_ship_to_from_customer`** — recommended, not
   implemented (see last section). Data cleanup alone leaves the hole open for every row we
   cannot classify.

---

## Why there is no discriminator (evidence)

`ship_to_address` columns: `id, customer_id, address_line1, address_line2, city, state,
postal_code, country, is_default`. No origin, no timestamp. All 30 rows have `is_default=1`
(one row per customer, 30 rows across 43 customers).

Three creation paths converge on this table, none recording which one ran:

| Path | Source | Requires | `country` | `address_line2` |
|---|---|---|---|---|
| `db_writer._create_customer_from_rfq` (monitor ingest, new customer) | RFQ `ship_to` (pre-331 = signature) | city+state | model default `US` | never set |
| `routes._sync_customer_ship_to_from_quote` (web editor autosave) | `quote.ship_to_json` | line1+city+state+zip to *create*; field-merge to *update* | copied from quote | set from quote |
| `customers._save_addresses` (customer admin form, **human**) | admin form fields | line1+city+state+zip | model default `US` | set from form |

I attempted to reverse-engineer origin from `country` and could **not**: the field-merge in
the sync path is internally inconsistent with the stored data. E.g. STA #20 (Inter Pipeline)
holds `country='US'` while its only source quote 126-048 has `country='Canada'`; the merge
should have overwritten it to Canada and did not. That inconsistency is itself the proof —
the current rows cannot be mapped back to a single authoritative source event. **Do not treat
any inferred origin here as fact; treat it as a prompt for human confirmation.**

---

## Tier 1 — high-confidence corrupt rows (recommend correcting)

Per D37 a bill-to/garbage default ship-to should not exist: deleting the row makes new quotes
fall through to product-only pricing until a human adds the real destination, which is the
safe default. "Blank fields" is the more conservative alternative where the street itself is
real. Devin chooses.

| STA id | Customer | Current value | Defect | Proposed value | Confidence |
|---|---|---|---|---|---|
| **#30** | 42 Azimuth Energy Sdn Bhd | line1=`No 47-2, Level 2, Jalan Neutron U16/Q, Denai Alam`, line2=`FOB Tulsa`, Shah Alam, Selangor, 40160, **Malaysia** | Requester's own Malaysian office (signature/bill-to per K331) + freight term `FOB Tulsa` in line2 + foreign. The canonical bad case. | **Delete row** (Azimuth RFQ was EX-Works, no destination given → product-only). | Very high |
| **#29** | 41 Buckeye Partners, LP. | line1=`5521 West Lincoln Highway, Suite #305`, **line2=`Buckeye Partners, LP.`**, Crown Point, IN, 46307, US | Company name sitting in `address_line2`; the street is Buckeye's own office (from signature, quotes 126-083/084 `company=Buckeye`). | **Delete row** (bill-to). Minimum: clear line2. | High |
| **#22** | 31 Price Gregory International | line1=``, **line2=`contractor yard`**, **Katy**, **MS**, 77494, **`None`** | Not an address: `contractor yard` placeholder; Katy is in **TX** not MS; ZIP 77494 is Katy TX; literal-string `None` country. Real Price Gregory jobs (deleted quotes 126-064/066) were Butler County **OH**. | **Delete row.** | High |

---

## Tier 2 — foreign address stored as domestic (I106 freight-collision bug)

`is_domestic_ship_to` treats `country='US'` as domestic, so these reach the US ZIP-centroid
freight lookup and can bill a garbage distance-derived charge on any new quote.

| STA id | Customer | Current value | Defect | Proposed value | Confidence |
|---|---|---|---|---|---|
| **#6** | 13 Enbridge | ``, Tupperville, **Ontario**, ``, **`US`** | Ontario = Canada, mislabeled US. Also arbitrary: Enbridge has 5 quotes to different Canadian sites. | Confirm intended job; at minimum `country`→`Canada`. Likely **delete** (last-synced arbitrary). | High (country); med (which addr) |
| **#20** | 28 Inter Pipeline Ltd. | `3200, 215 – 2nd Street, SW`, Calgary, **AB**, T2P 1M4, **`US`** | Canadian address mislabeled US; it is Inter Pipeline's own HQ (bill-to). | `country`→`Canada`, or **delete** (bill-to). | High |
| **#21** | 29 Red Flame Specialty Services | `3200, 215 – 2nd Street, SW`, Calgary, **AB**, T2P 1M4, **`US`** | **Different company's** address: this is Inter Pipeline's Calgary HQ, but the customer is Red Flame (a middleman). Foreign + cross-company. | **Delete row.** | High |

---

## Tier 3 — cannot classify from data; needs Chip's per-row confirmation

Each row below is *plausibly* a legitimate designated jobsite **or** a signature/bill-to
office. There is no signal in the data to decide. **No change proposed without confirmation.**
Grouped by what makes them suspect. For every row the question for Chip is the same:
*"Is this the address Allan Edwards should ship to by default for this customer, or is it the
customer's billing/office address that landed here from an email signature?"*

### 3a. Cross-company / arbitrary default (the stored address belongs to a different entity than the customer, or the customer has many jobs and this is just the last one synced)

| STA id | Customer | Current value | Why suspect | What to confirm |
|---|---|---|---|---|
| #1 | 2 918 Software | 5813 E. 64th Pl, Tulsa, Ok, 74136, `United States` | 918 Software is a test/forwarding account with 20+ quotes to unrelated end-customers; this default is arbitrary. | Is 918 Software a real shipping customer at all? If test, delete. |
| #7 | 14 Kinder Morgan | ``, Heflin, AL | Address is **LE Bell**'s location (quote 126-016), not Kinder Morgan; a later KM quote (126-035) was Peoria, AZ. | Which is KM's real default, if any? |
| #3 | 9 Frazier International | ``, Houston, TX, 77032 | Address came from a **Chevron** quote (126-011), no street. | Real Frazier ship-to? |
| #19 | 27 B & S Equipment | 26406 FM 2100, Huffman, TX, 77336 | Source quote labels ship-to `Enbridge` (fwd by bandsequipment.com); customer is B & S. | Is Huffman the B&S delivery point or the Enbridge jobsite? |

### 3b. Full street that reads as the customer's own office/HQ (likely bill-to under D37)

| STA id | Customer | Current value | What to confirm |
|---|---|---|---|
| #9 | 16 VALPRO International | 1326 E Commercial Blvd, **PMB 3014**, Oakland Park, FL, 33334 | `PMB` = private mailbox → billing address, not a dock. Real ship-to? |
| #18 | 26 Edgen Murray | 3300 Rider Trail South, **Suite 120**, Earth City, MO, 63045 | Suite/office → likely bill-to. |
| #25 | 34 Northern Natural Gas | 1111 South 103rd Street, Omaha, NE, 68124 | NNG corporate HQ → likely bill-to. |
| #8 | 4 Kline Oilfield | 651 McDonald, Odessa, TX, 79761 | Their yard (legit) or office? |
| #5 | 12 Western Supplies | 1090 Rifle Range Road, Iowa Park, TX, 76367 | Yard or office? |
| #13 | 20 Midcontinent Pipeline | 1842 Industrial Blvd, Elk City, OK, 73644 | Yard or office? |
| #14 | 21 Sooner Pipeline | 8801 S Memorial Dr, Tulsa, OK, 74133 | Yard or office? |
| #16 | 23 Panhandle Integrity | 112 E Main St, Woodward, OK, 73801 | Yard or office? |
| #24 | 33 Summit Utilities | 2100 Waldron Rd, Fort Smith, AR, 72903 | Yard or office? (backing quote deleted) |
| #26 | 36 Mears | 6124 Steinhart Road, Nebraska City, NE, 68421 | Yard or office? |
| #27 | 38 Acme Pipeline **(example)** | 5500 S 129th E Ave, Tulsa, OK, 74134 | Customer name says "(example)" → test. Delete? |

### 3c. City/state only, no street or ZIP (partial; freight can't compute anyway)

| STA id | Customer | Current value | Note |
|---|---|---|---|
| #4 | 11 CenterPoint Energy | Pearl, MS | designated? |
| #10 | 17 Associated | Raleigh, NC | **orphan — no backing quote found**; origin unknown |
| #11 | 18 Energy Transfer | Midland, TX | designated? |
| #12 | 19 Willy's Weiner Service | Canton, OH | quote note says "shipping to Canton, OH" → **looks like a genuine designation** |
| #15 | 22 Phillips 66 | Midland, TX | designated? |
| #17 | 24 Martinez Industrial | Tulsa, OK | designated? |
| #23 | 32 Bobs Big Boy | Athens, TX | likely test data |
| #28 | 39 PERC Engineering | `2.25 miles East of Bristow, OK (Creek County)`, Bristow, OK | reads as a **genuine field jobsite**; no ZIP |
| #31 | 43 Tucker Construction | Cushing, Oklahoma | quote note "Delivery to Cushing, Oklahoma" → **genuine designation** |

---

## Problem (i) residue — live quotes that still DISPLAY a wrong ship-to

Separate from the `ship_to_address` rows above, these **non-deleted** quotes carry a
signature/garbage `ship_to_json` that 331's tolerant reader renders safe (no crash, no foreign
freight) but that still shows a wrong ship-to in the editor. Listed for completeness; correcting
them is a per-quote edit, lower urgency than (ii):

| Quote | Customer | ship_to_json | Issue |
|---|---|---|---|
| 126-086 | 42 Azimuth | line2=`FOB Tulsa`, Shah Alam, Malaysia | freight term + foreign signature office |
| 126-085 | 41 Buckeye | line2=`Buckeye Partners, LP.` only | company name only |
| 126-059 | 31 Price Gregory | line2=`contractor yard`, MS, `None` | placeholder + bad geo |
| 126-058 | 30 WHC (Pumpco RFQ) | line2=`contractor yard`, LA | placeholder |
| 126-089 | (unlinked) | attention=`Cushing, Ok` only | city stuffed in attention |

(85 live+deleted quotes carry a non-empty `ship_to_json`; the great majority are just a
`company`-only signature entry that renders harmlessly. The 5 above are the ones that display
visibly wrong.)

---

## Durable fix for problem (ii) — recommend, do not implement yet

Data cleanup alone does not close (ii): every Tier-3 row we cannot classify stays wired to
`_hydrate_quote_ship_to_from_customer` (routes.py:1269), which copies the customer's stored
default onto any new quote that arrives without a ship-to. For a domestic customer whose stored
default is really their office, the new quote then computes freight to the office instead of the
jobsite — 331's country guard does not catch it because the address is US-valid.

**Recommended durable fix (needs PM/Devin sign-off before any implementation):**

- Add `human_confirmed BOOLEAN NOT NULL DEFAULT 0` (equivalently `confirmed_at`/`confirmed_by`)
  to `ship_to_address`.
- Set it to `1` only in the human path `customers._save_addresses`, and via an explicit
  "confirm ship-to" action in the quote editor. The two auto paths
  (`_create_customer_from_rfq`, `_sync_customer_ship_to_from_quote`) leave it `0`.
- Gate `_hydrate_quote_ship_to_from_customer` on `human_confirmed = 1`. Unconfirmed defaults
  no longer hydrate → new quotes without a designated ship-to price product-only (D37),
  instead of silently inheriting a bill-to.

This is a **positive marker**, per task anti-guidance, rather than inferring intent from which
fields happen to be populated. It also makes the Tier-3 ambiguity self-healing: unconfirmed
rows simply stop hydrating until a human confirms one, so we do not have to guess-classify all
24 of them to be safe.

---

## Apply procedure (only after Devin approves the change set — not this pass)

1. Fresh backup immediately before, do not rely on the 02:00 scheduled one:
   `ssh root@157.230.227.28 'sqlite3 /opt/aedwards/instance/allenedwards.db ".backup /opt/aedwards/backups/pre_task338_manual.db"'`
   (daily backups exist at `/opt/aedwards/backups/`, 8 present, ~7-day retention.)
2. Apply only the approved rows. Corroborate each `UPDATE`/`DELETE` rowcount against the
   expected id list; if any statement reports 0 rows affected, stop and re-inspect (project
   anti-guidance: distrust "0 rows" as reassurance).
3. Re-run the Tier-1/Tier-2 SELECTs to confirm the corrupt values are gone and no collateral
   rows changed.
