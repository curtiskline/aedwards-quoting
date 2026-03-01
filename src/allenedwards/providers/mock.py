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


# Pre-defined response for testing multi-quote emails (e.g., Buckeye/Daniel Cullison)
SAMPLE_MULTI_QUOTE_RESPONSE = {
    "customer_name": "Buckeye Partners",
    "contact_name": "Daniel Cullison",
    "contact_email": "daniel.cullison@buckeye.com",
    "contact_phone": "419-555-0123",
    "quotes": [
        {
            "project_line": "XB403CL Line",
            "ship_to": {
                "company": "Buckeye Huntington",
                "attention": "Site Manager",
                "street": "1234 Pipeline Rd",
                "city": "Huntington",
                "state": "IN",
                "postal_code": "46750",
                "country": "United States",
            },
            "po_number": None,
            "items": [
                {
                    "product_type": "sleeve",
                    "quantity": 10,
                    "diameter": "8.625",
                    "wall_thickness": "0.25",
                    "grade": "50",
                    "length_ft": 10,
                    "milling": False,
                    "painting": False,
                    "description": "Sleeve 8-5/8\" ID, 1/4\" w/t, GR50, 10' long",
                    "notes": None,
                }
            ],
            "notes": None,
        },
        {
            "project_line": "HM999A3 Line",
            "ship_to": {
                "company": "Buckeye Elburn",
                "attention": "Site Manager",
                "street": "5678 Terminal Ave",
                "city": "Elburn",
                "state": "IL",
                "postal_code": "60119",
                "country": "United States",
            },
            "po_number": None,
            "items": [
                {
                    "product_type": "sleeve",
                    "quantity": 5,
                    "diameter": "6.625",
                    "wall_thickness": "0.3125",
                    "grade": "65",
                    "length_ft": 8,
                    "milling": True,
                    "painting": False,
                    "description": "Sleeve 6-5/8\" ID, 5/16\" w/t, GR65, 8' long, milled",
                    "notes": None,
                }
            ],
            "notes": None,
        },
        {
            "project_line": "XF001-002XB Line",
            "ship_to": {
                "company": "Buckeye Griffith",
                "attention": "Site Manager",
                "street": "9012 Refinery Blvd",
                "city": "Griffith",
                "state": "IN",
                "postal_code": "46319",
                "country": "United States",
            },
            "po_number": None,
            "items": [
                {
                    "product_type": "sleeve",
                    "quantity": 15,
                    "diameter": "10.75",
                    "wall_thickness": "0.375",
                    "grade": "50",
                    "length_ft": 12,
                    "milling": False,
                    "painting": True,
                    "description": "Sleeve 10-3/4\" ID, 3/8\" w/t, GR50, 12' long, painted",
                    "notes": None,
                }
            ],
            "notes": None,
        },
        {
            "project_line": "ZI165LI-2 Line",
            "ship_to": {
                "company": "Buckeye Lima",
                "attention": "Site Manager",
                "street": "3456 Storage Way",
                "city": "Lima",
                "state": "OH",
                "postal_code": "45801",
                "country": "United States",
            },
            "po_number": None,
            "items": [
                {
                    "product_type": "sleeve",
                    "quantity": 8,
                    "diameter": "12.75",
                    "wall_thickness": "0.5",
                    "grade": "50",
                    "length_ft": 10,
                    "milling": False,
                    "painting": False,
                    "description": "Sleeve 12-3/4\" ID, 1/2\" w/t, GR50, 10' long",
                    "notes": None,
                }
            ],
            "notes": None,
        },
    ],
    "urgency": "normal",
    "notes": "4 separate quotes requested for different project lines",
    "confidence": 0.90,
}
