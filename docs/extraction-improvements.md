# Extraction & Pricing Improvement Plan

Based on production feedback from the primary user ("John" = the AI quoting assistant).

## Known Issues

### 1. Fabricated Part Numbers

The pricing engine generates part numbers from formulas (e.g., `S-{dia}-{wt}-{grade}-{len}`). These are synthetic and don't correspond to real catalog SKUs.

**Root cause:** No real part number catalog exists in the system.

**Fix:** The user needs to provide a complete SKU/part number list. Once available, pricing can look up real part numbers instead of generating them. This is a data problem, not an AI problem.

### 2. Description Rewrites Changing Quantity/Price

The LLM rephrases customer language when populating the `description` field. Because pricing uses keyword matching on descriptions (`_match_omegawrap_key`, `_match_other_pricing_key` in `pricing.py`), rewritten descriptions can land items in the wrong pricing category or misinterpret quantities.

**Root cause:** The extraction prompt doesn't distinguish between the customer's original wording and normalized structured fields. The LLM "cleans up" descriptions, which downstream keyword matching then misreads.

**Fix:**
- Instruct the extraction prompt to preserve the customer's original wording verbatim in the `description` field.
- Use the structured fields (`product_type`, `diameter`, `wall_thickness`, etc.) for pricing decisions, not the description text.
- Long term: switch to tool use (see below) so the LLM fills constrained fields and doesn't need to rewrite anything.

### 3. Unit-of-Measure Confusion (e.g., "150 feet of carbon" → "150 rolls")

The extraction schema defines `quantity` as a bare integer with no unit field. When customers specify quantities in feet, sets, pallets, etc., the LLM has nowhere to put the unit and jams the number into `quantity` using the wrong unit.

**Root cause:** Missing `quantity_unit` field in the `ParsedItem` schema (`parser.py`).

**Fix:** Add a `quantity_unit` field (enum: `each`, `feet`, `rolls`, `sets`, `pallets`, `bundles`) to the extraction schema. Add conversion logic in `pricing.py` to handle unit normalization before pricing. For example, "150 feet of carbon OmegaWrap" at 50 ft/roll = 3 rolls.

### 4. Long/Noisy Emails Get Ignored

Customers send emails with lots of extraneous content. The RFQ details may be buried well below the top of the email.

**Root cause:** The classification step in `parser.py:classify_rfq` only sends the first 500 characters of the body to the LLM:

```python
snippet = (body or "")[:DEFAULT_RFQ_CLASSIFY_BODY_CHARS]
```

If the actual RFQ content is below that cutoff, the classifier never sees it and may reject the email. Extraction never runs.

**Fix options (pick one):**
- Increase `DEFAULT_RFQ_CLASSIFY_BODY_CHARS` to 2000+ characters.
- Send the full body to the classifier (classification is cheap — one short JSON response).
- Bypass classification entirely and attempt extraction on every email. Let the `$0 TBD` path handle non-RFQs. This eliminates false negatives at the cost of more LLM calls.

---

## Recommended Improvements

### Tool Use for Structured Extraction

**Problem:** The current approach asks the LLM to return free-form JSON and hopes the schema matches. The `complete_json` method in `claude.py` just appends "respond with valid JSON" and parses whatever comes back. This leads to schema drift — wrong field names, wrong types, creative values for enums like `product_type`.

**Solution:** Replace the free-form JSON prompt with Anthropic's [tool use](https://docs.anthropic.com/en/docs/build-with-claude/tool-use) feature. Define the extraction output as a tool call with a strict JSON schema. This gives:

- **`product_type` as a constrained enum** — the model must pick from `sleeve`, `oversleeve`, `girth_weld`, `compression`, `bag`, `omegawrap`, `accessory`, `service` rather than inventing categories.
- **`quantity_unit` as a constrained enum** — `each`, `feet`, `rolls`, `sets`, `pallets`, `bundles`.
- **Required fields enforced** — `diameter`, `wall_thickness`, `grade` can't be silently omitted.
- **Typed parameters** — numbers stay numbers, booleans stay booleans.

Example tool definition (simplified):

```python
EXTRACT_RFQ_TOOL = {
    "name": "extract_rfq",
    "description": "Extract structured RFQ data from an email",
    "input_schema": {
        "type": "object",
        "properties": {
            "customer_name": {"type": "string"},
            "contact_name": {"type": "string"},
            "contact_email": {"type": "string"},
            "quotes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "project_line": {"type": "string"},
                        "ship_to": { ... },
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "product_type": {
                                        "type": "string",
                                        "enum": ["sleeve", "oversleeve", "girth_weld",
                                                 "compression", "bag", "omegawrap",
                                                 "accessory", "service"]
                                    },
                                    "quantity": {"type": "integer"},
                                    "quantity_unit": {
                                        "type": "string",
                                        "enum": ["each", "feet", "rolls", "sets",
                                                 "pallets", "bundles"]
                                    },
                                    "diameter": {"type": "number"},
                                    "wall_thickness": {"type": "number"},
                                    "grade": {"type": "integer", "enum": [50, 65]},
                                    "length_ft": {"type": "number"},
                                    "original_description": {"type": "string"},
                                    "milling": {"type": "boolean"},
                                    "painting": {"type": "boolean"}
                                },
                                "required": ["product_type", "quantity",
                                             "quantity_unit", "original_description"]
                            }
                        }
                    }
                }
            }
        }
    }
}
```

This is the single highest-impact change. It eliminates an entire class of extraction errors (wrong types, missing fields, hallucinated enums) without changing the pricing engine at all.

### Few-Shot Examples

**Problem:** The current system prompt in `parser.py` is ~150 lines of instructions with zero examples. LLMs respond better to concrete input/output pairs than to paragraphs of rules, especially for domain-specific terminology.

**Solution:** Add 3-5 real (anonymized) RFQ email → expected JSON extraction pairs to the prompt. These should cover:

1. **Simple single-item sleeve RFQ** — the happy path
2. **Multi-item with mixed product types** — sleeves + girth welds + accessories
3. **Regional/slang terminology** — "style 3", "hump sleeves", "wedding bands" all → `girth_weld`
4. **Quantity with units** — "150 feet of carbon" → quantity: 3, quantity_unit: rolls
5. **Long noisy email** — lots of signature/disclaimer fluff with RFQ buried in the middle

Example format to include in the prompt:

```
### Example 1: Simple sleeve RFQ

Email:
From: mike.jones@pipelineco.com
Subject: Quote request - 6" sleeves

Hi, need a quote on 30 pcs of 6-5/8 x 1/4 wall GR50 half soles, 10 ft long.
Ship to our yard in Houston TX.

Thanks,
Mike Jones
Pipeline Co.
(713) 555-1234

Expected extraction:
{
  "customer_name": "Pipeline Co.",
  "contact_name": "Mike Jones",
  "contact_email": "mike.jones@pipelineco.com",
  "contact_phone": "(713) 555-1234",
  "quotes": [{
    "ship_to": {"company": "Pipeline Co.", "city": "Houston", "state": "TX"},
    "items": [{
      "product_type": "sleeve",
      "quantity": 30,
      "quantity_unit": "each",
      "diameter": 6.625,
      "wall_thickness": 0.25,
      "grade": 50,
      "length_ft": 10,
      "original_description": "30 pcs of 6-5/8 x 1/4 wall GR50 half soles, 10 ft long",
      "milling": false,
      "painting": false
    }]
  }]
}

### Example 2: Regional terminology

Email:
Subject: need pricing on wedding bands

Can you quote me 20 sets of 12-3/4 wedding bands, 3/8 wall?

Expected extraction:
{
  "quotes": [{
    "items": [{
      "product_type": "girth_weld",
      "quantity": 20,
      "quantity_unit": "sets",
      "diameter": 12.75,
      "wall_thickness": 0.375,
      "grade": 50,
      "length_ft": 6,
      "original_description": "20 sets of 12-3/4 wedding bands, 3/8 wall"
    }]
  }]
}
```

The user mentioned that customers use many regional terms for the same products. A terminology mapping section combined with examples would help:

```
Regional terminology (map to product_type):
- "half sole", "reg half sole", "repair sleeve" → sleeve
- "ovsz", "oversleeve", "over sleeve" → oversleeve
- "style 3", "weld sleeve", "hump sleeve", "wedding band", "GTW" → girth_weld
- "geo bag", "geotextile", "GTW bag" → bag
- "carbon wrap", "composite wrap", "CF wrap" → omegawrap
```

Few-shot examples are additive — they work alongside tool use and don't require code changes beyond updating the prompt text.

---

## Implementation Priority

| Priority | Change | Effort | Impact |
|----------|--------|--------|--------|
| 1 | Add `quantity_unit` to extraction schema | Small | Fixes unit confusion bugs |
| 2 | Switch to tool use for extraction | Medium | Eliminates schema drift, constrains enums |
| 3 | Add few-shot examples with regional terms | Small | Improves product classification accuracy |
| 4 | Increase classification body snippet size | Trivial | Fixes long-email rejection |
| 5 | Preserve original description verbatim | Small | Prevents keyword match failures |
| 6 | Part number catalog integration | Depends on data | Fixes fabricated part numbers |
