"""Live classifier regression coverage against historical RFQ emails."""

from __future__ import annotations

import os
import re
from email import policy
from email.parser import BytesParser
from functools import lru_cache
from pathlib import Path

import pytest

from allenedwards.cli import get_provider, resolve_provider_name
from allenedwards.parser import CLASSIFY_SYSTEM_PROMPT, classify_rfq


SHARED_PROJECT_ROOT = Path(__file__).resolve().parents[3]
EMAILS_DIR = SHARED_PROJECT_ROOT / "data" / "test-corpus" / "emails"
RUN_INTEGRATION = os.environ.get("RUN_INTEGRATION") == "1"
INTEGRATION_REASON = "Set RUN_INTEGRATION=1 to run live LLM classifier regression tests."

POSITIVE_CASES = [
    (
        "sleeve_rfq",
        "20250206_150218_jhamilton_RFQ.eml",
        "basic sleeve quote request",
    ),
    (
        "girth_weld_rfq",
        "20250211_150802_jhamilton_CNP IM - fabricated weld sleeve.eml",
        "girth weld quote request",
    ),
    (
        "pipe_weights_rfq",
        "20250411_181926_jhamilton_Fw_ LEG TC 30_ Interconnect bag weights.eml",
        "pipe weights / buoyancy product request",
    ),
    (
        "forwarded_external_rfq",
        "20250206_171030_jhamilton_RE_ [EXTERNAL] RE_ Allan Edwards 36_ 3_4_ Gr 65 Sleeves.eml",
        "forwarded external customer RFQ",
    ),
    (
        "omegawrap_rfq",
        "20250210_154021_jhamilton_FW_ Omega wrap training and installation support for Enbridg.eml",
        "OmegaWrap-related service request",
    ),
    (
        "terse_specs_rfq",
        "20250212_132738_jhamilton_Repair wrap.eml",
        "terse product/spec request without RFQ wording in the subject",
    ),
]

NEGATIVE_CASES = [
    (
        "meeting_accept",
        "20250618_141910_backup_Accepted_ Compression Sleeve Discussion.eml",
        "meeting invite acceptance",
    ),
    (
        "quote_notification",
        "20250206_083132_jhamilton_New Quote Has Been Created.eml",
        "internal quote-created notification",
    ),
    (
        "personal_email",
        "20251023_001222_jhamilton_Fwd_ my personal email.eml",
        "personal/unrelated message",
    ),
]


def _strip_html(text: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _decode_part(part) -> str:
    try:
        content = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        return _strip_html(content)
    return str(content)


def _load_email(filename: str) -> tuple[str, str]:
    path = EMAILS_DIR / filename
    with path.open("rb") as handle:
        message = BytesParser(policy=policy.default).parse(handle)

    body_parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() not in {"text/plain", "text/html"}:
                continue
            disposition = str(part.get("Content-Disposition", "")).lower()
            if "attachment" in disposition:
                continue
            text = _decode_part(part).strip()
            if text:
                body_parts.append(text)
    else:
        text = _decode_part(message).strip()
        if text:
            body_parts.append(text)

    subject = str(message.get("Subject", "") or "")
    body = "\n\n".join(body_parts)
    return subject, body


@lru_cache(maxsize=1)
def _provider():
    return get_provider()


def _provider_name() -> str:
    return resolve_provider_name()


@pytest.mark.integration
@pytest.mark.skipif(not RUN_INTEGRATION, reason=INTEGRATION_REASON)
@pytest.mark.parametrize(("case_id", "filename", "description"), POSITIVE_CASES, ids=[case[0] for case in POSITIVE_CASES])
def test_classify_rfq_accepts_historical_positive_cases(case_id: str, filename: str, description: str) -> None:
    assert EMAILS_DIR.exists(), f"Historical corpus directory not found: {EMAILS_DIR}"
    subject, body = _load_email(filename)

    is_rfq, reason = classify_rfq(subject, body, _provider())

    assert is_rfq is True, (
        f"{case_id} should classify as RFQ with provider {_provider_name()} "
        f"({description}); classifier reason was: {reason!r}"
    )


@pytest.mark.integration
@pytest.mark.skipif(not RUN_INTEGRATION, reason=INTEGRATION_REASON)
@pytest.mark.parametrize(("case_id", "filename", "description"), NEGATIVE_CASES, ids=[case[0] for case in NEGATIVE_CASES])
def test_classify_rfq_rejects_historical_negative_cases(case_id: str, filename: str, description: str) -> None:
    assert EMAILS_DIR.exists(), f"Historical corpus directory not found: {EMAILS_DIR}"
    subject, body = _load_email(filename)

    is_rfq, reason = classify_rfq(subject, body, _provider())

    assert is_rfq is False, (
        f"{case_id} should classify as non-RFQ with provider {_provider_name()} "
        f"({description}); classifier reason was: {reason!r}"
    )


def test_classify_system_prompt_mentions_broadened_product_range() -> None:
    prompt = CLASSIFY_SYSTEM_PROMPT.lower()

    assert "weights" in prompt
    assert "omegawrap" in prompt
    assert "false negatives lose business" in prompt
