"""Mock LLM provider for testing."""

import json
from typing import Any

from .base import LLMProvider


class MockProvider(LLMProvider):
    """Mock provider that returns pre-defined responses for testing."""

    def __init__(self, response: dict[str, Any] | None = None):
        self.response = response or {}

    def complete(self, prompt: str, system: str | None = None) -> str:
        return json.dumps(self.response)

    def complete_json(self, prompt: str, system: str | None = None) -> dict[str, Any]:
        return self.response


# Pre-defined response for the "Mail Attachment.eml" test file
SAMPLE_RFQ_RESPONSE = {
    "customer_name": "FHR Pipeline and Terminals",
    "contact_name": "Evan Bohlman",
    "contact_email": "evan.bohlman@fhr.com",
    "contact_phone": "612-615-3517",
    "ship_to": {
        "company": "Cottage Grove Terminal",
        "attention": None,
        "street": "6483 85th St S",
        "city": "Cottage Grove",
        "state": "MN",
        "postal_code": "55016",
        "country": "United States",
    },
    "po_number": "PO-2026-1042",
    "items": [
        {
            "product_type": "sleeve",
            "quantity": 30,
            "diameter": "6.625",
            "wall_thickness": "0.25",
            "grade": "50",
            "length_ft": 10,
            "milling": False,
            "painting": False,
            "description": "Sleeve, Sealing, 6-5/8\" ID reg. half sole, 1/4\" w/t, A572 GR50, 10' long",
            "notes": None,
        }
    ],
    "urgency": "normal",
    "notes": None,
    "confidence": 0.95,
}
