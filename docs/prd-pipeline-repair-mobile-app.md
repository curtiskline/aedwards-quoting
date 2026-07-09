# PRD: Pipeline Repair Field Technician App

**Date:** 2026-07-01
**Source:** Chip Edwards meeting at 6468 N Yale Ave, Jul 1 2026
**Priority:** 2 of 2 (Chip: "we can try to get grants for this one")
**Status:** Scoping

---

## Background

Allan Edwards sells three core product lines for pipeline repair:
1. **Steel sleeves** — three input variables: pipe size, pipe grade, steel grade. Highly repeatable.
2. **Compression sleeves** — steel, heated and welded while hot to squeeze the pipe. Used for cracks.
3. **Composite wraps** — fiberglass/carbon fiber string and adhesive, originally developed for aircraft, repurposed for pipelines. This PRD focuses here.

The composite wrap market is the disruption opportunity. Chip has been trying to crack it for five years.

---

## Problem

Composite pipeline repair is treated by the entire industry as a bespoke engineering service. Every repair is designed from scratch. The workflow today:

1. A pig (inspection device) runs through the pipeline, flags anomalies
2. An inspection company (Shaw, Tulsa-area firms) digs it up and scans it
3. They generate an inspection report — pipe dimensions, corrosion depth, operating pressure
4. That report is sent to a company like Allan Edwards
5. **Engineers spend 2–3 days calculating the repair spec** (how many wrap layers, what product)
6. Material ships to the field
7. The construction crew — who has been sitting with an open hole in the ground — finally installs

The 2–3 day engineering turnaround is pure friction. The crew is idle. The pipeline owner is losing revenue (Chip's example: $50,000/day from reduced operating pressure). The engineering itself is a formula. It is not actually custom.

The real calculation is simple: a small set of input variables (pipe diameter, wall thickness, operating pressure, corrosion depth as % of wall) maps to a required number of wrap layers via a known formula. The industry adds a layer of theater on top of it that costs days and money.

Chip's take: *"I'm pretty sure I can teach that thing to do it in about 15 seconds. An intern would take a year to get that same knowledge."*

His business model insight: sell the product, include the engineering on the front end as a built-in service, eliminate the 2-day turnaround entirely. While competitors bill for engineering services, Chip wants to make the engineering instant and free — then win on speed and simplicity.

---

## Vision

A mobile app for field technicians. The tech is standing in front of a dug-up pipe. They open the app, input a handful of measurements, and within seconds have:

- The recommended repair specification (product selection, number of layers)
- A pre-filled compliance report ready to submit
- The ability to place the material order on the spot

The entire workflow — from inspection data to report filed to order placed — happens in the field, in real time, without an engineer in the loop.

---

## User Flow (Chip's description)

1. Tech opens app at the job site
2. **Inputs repair variables:**
   - Pipe diameter and grade (often already known from pigging data)
   - Operating pressure (derivable from wall thickness)
   - Corrosion depth (% of wall thickness)
   - Repair dimensions (length, width)
3. App calculates the required repair and presents a recommendation (product + layer count)
4. Tech reviews, confirms, and proceeds to the report
5. **Report auto-fills from available data:**
   - GPS coordinates (from phone)
   - Ambient temperature (from phone or manual entry)
   - Product lot numbers (scanned from cans)
   - Before/after photos (prompted step-by-step: *"Take picture here. Next step."*)
6. Tech hits submit — report files into their system, order fires to Allan Edwards
7. Allan Edwards gets a copy for their records

Chip's framing: *"Joe blow doing the work hits submit. Done. The product itself is cheap as shit — it's all the layers of back-and-forth that costs money."*

---

## Key Insight: The "Worst Case" Play

Because the engineering formula is conservative by design, there is a simpler option: always spec the worst-case layer count and skip the depth measurement entirely. The product cost delta between worst-case and optimized is small. The time savings is enormous.

Chip's observation: the industry debates whether a defect is 20% or 75% corrosion depth in order to save a few layers of material. Meanwhile the pipeline owner is losing $50K/day in lost throughput. The optimization is irrational.

A "quick mode" that just asks pipe size and worst-cases the repair may be a viable product by itself.

---

## Compliance Report Requirements

Pipeline repair requires a documented record for regulatory and operator purposes. The report must include at minimum:
- Job site location
- Pipe information (diameter, grade, operating pressure)
- Defect description (depth, dimensions)
- Product used (lot numbers, batch codes)
- Number of layers applied
- Ambient conditions at time of application (temperature, humidity)
- Before and after photos
- Technician identity and date

All of this except the photos is either calculable, scannable, or pullable from the phone's sensors. The app should minimize manual entry to zero.

---

## Business Model

Chip's stated goal: **sell product, not engineering services.** The app is the delivery mechanism for the product — it makes ordering frictionless. Engineering is built into the product price, not billed separately.

Downstream: he envisions selling the app (with the engineering baked in) as a tool tied to the product. If a customer wants to use the app, they buy Allan Edwards product. The app becomes the moat.

Long-term vision: a tech in the field never needs to call anyone. They have the product on their truck. The app tells them how much to use. They apply it, file the report, and move on. Instant.

---

## Related Products (same app, future scope)

The steel sleeve and compression sleeve product lines have a simpler variable set (3 inputs vs 5–6 for composite). The same mobile app framework could serve all three product lines with different calculation engines.

---

## Market Context

Tulsa has a large pigging community. The inspection → repair workflow is well-established. Allan Edwards is already a known vendor in this space. The disruption isn't the product — it's eliminating the 2-day engineering cycle and making the field workflow self-service.

Chip attempted to disrupt this market ~5 years ago but felt he "did it poorly" because he couldn't convey the message that the engineering was already solved. The app makes that argument self-evident.

---

## Open Questions

1. What are the exact input variables required for the calculation? (Chip can provide the calculation sheet.)
2. Which inspection data formats are standard? Can the app ingest a pigging report directly?
3. Is the report format standardized across pipeline operators, or does it vary by customer?
4. Does Allan Edwards want to own the app and license it, or build it as a free tool that drives product sales?
5. Regulatory requirements for self-reported field repairs — any certification or third-party sign-off needed?
6. Is GPS + photo + lot scan sufficient for a compliant report, or are there additional fields per operator specs?

---

## Notes from Chip

- *"I want to sell product. Engineering is already done on the front end."*
- *"I think I could run this as a billion-dollar company."*
- *"You're losing $50,000 a day by having to lower pipeline pressure. You're trying to save 40 bucks. I cannot get people to see past that."*
- *"Take picture here. Next step. Boom boom boom. Push a button. File goes in. We also happen to get a copy."*
