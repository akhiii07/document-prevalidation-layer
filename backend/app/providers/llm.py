"""LLM provider.

The LLM's job here is narrow and bounded: when the deterministic parser cannot find a
header field in an unfamiliar layout, it is asked to read that field out of the header
text. It is never shown the transaction ledger, never asked to judge a document, and its
output can never become a verdict (ADR-001).

Three constraints shape the implementation:

* **Data minimisation** (`RESEARCH_REGULATORY.md` section 5). An LLM call is foreign
  processing unless the model is served in-region, so it inherits the localisation and
  24-hour-deletion obligations. It therefore receives the *header block only*, with long
  digit runs masked, and is never asked for the account number.
* **The system must work with it switched off** (ADR-010). No key, no network, no
  problem: the deterministic provider is a no-op and every test passes without
  credentials.
* **It cannot change an outcome.** It returns field *values*, which then go through the
  same deterministic rules as any other extraction. A wrong answer lowers confidence; it
  cannot manufacture a PASS.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Protocol, runtime_checkable

from app.config.settings import get_settings

logger = logging.getLogger("docverify.llm")

#: Fields the LLM may be asked for. The account number is deliberately absent: the
#: deterministic parser handles it, and sending it would be an unnecessary disclosure.
ASSISTABLE_FIELDS = ("account_holder_name", "account_type", "period_start", "period_end")

_LONG_DIGITS = re.compile(r"\b\d{8,}\b")
_MAX_HEADER_CHARS = 2000

SYSTEM_PROMPT = """You read the header block of an Indian bank statement and return \
the requested fields as JSON.

Rules:
- Return ONLY a JSON object. No prose, no markdown fences.
- Use exactly the keys requested. Use null when a field is genuinely not present.
- Dates must be ISO format (YYYY-MM-DD). Indian statements are day-first: 03/04/2026 \
is 3 April 2026, never 4 March.
- account_type must be the literal words printed on the statement (for example \
"CURRENT" or "SAVINGS"), or null. Never infer it.
- Do not guess. A null is far more useful than a plausible invention."""


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    available: bool

    def extract_header_fields(self, header_text: str, fields: list[str]) -> dict[str, str]: ...


class DeterministicLLMProvider:
    """The no-op provider used whenever no credentials are configured.

    It returns nothing rather than a fabricated best guess. A missing field lowers
    confidence and pushes the document toward REVIEW, which is the correct behaviour --
    inventing a value to fill the gap would produce a confident verdict from data that
    was never read.
    """

    name = "deterministic"
    available = True

    def extract_header_fields(self, header_text: str, fields: list[str]) -> dict[str, str]:
        return {}


def redact_for_llm(text: str) -> str:
    """Mask long digit runs and truncate before anything leaves the process."""
    masked = _LONG_DIGITS.sub(lambda m: "X" * (len(m.group()) - 4) + m.group()[-4:], text)
    return masked[:_MAX_HEADER_CHARS]


class AnthropicLLMProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._anthropic = anthropic
        self.model = model
        self.available = True

    def extract_header_fields(self, header_text: str, fields: list[str]) -> dict[str, str]:
        requested = [f for f in fields if f in ASSISTABLE_FIELDS]
        if not requested:
            return {}

        prompt = (
            f"Return these fields: {', '.join(requested)}\n\n"
            f"Statement header:\n---\n{redact_for_llm(header_text)}\n---"
        )

        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=512,
                system=SYSTEM_PROMPT,
                # Reading four labelled fields out of a short block is not a reasoning
                # task; low effort is both cheaper and faster here.
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": prompt}],
            )
        except self._anthropic.RateLimitError:
            logger.warning("LLM rate limited; continuing without assistance")
            return {}
        except self._anthropic.APIConnectionError:
            logger.warning("LLM unreachable; continuing without assistance")
            return {}
        except self._anthropic.APIStatusError as exc:
            logger.warning("LLM returned %s; continuing without assistance", exc.status_code)
            return {}

        if response.stop_reason == "refusal":
            logger.info("LLM declined the request; continuing without assistance")
            return {}

        text = "".join(block.text for block in response.content if block.type == "text")
        return _parse_fields(text, requested)


def _parse_fields(text: str, requested: list[str]) -> dict[str, str]:
    """Parse the model's JSON defensively.

    Anything unexpected -- prose, a fence, a key we did not ask for, a non-string value
    -- is dropped rather than propagated. The extraction pipeline must not be able to
    acquire a field it did not ask for, because every field feeds a rule.
    """
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.MULTILINE)

    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        return {}

    try:
        parsed = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        logger.warning("LLM returned unparseable JSON; ignoring")
        return {}

    if not isinstance(parsed, dict):
        return {}

    return {
        key: str(value).strip()
        for key, value in parsed.items()
        if key in requested and isinstance(value, str | int | float) and str(value).strip()
    }


def get_llm() -> LLMProvider:
    settings = get_settings()
    if not settings.llm_active or settings.anthropic_api_key is None:
        return DeterministicLLMProvider()
    try:
        return AnthropicLLMProvider(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.llm_model,
        )
    except Exception:  # noqa: BLE001 - a missing SDK must not take the pipeline down
        logger.warning("anthropic SDK unavailable; using the deterministic provider")
        return DeterministicLLMProvider()
