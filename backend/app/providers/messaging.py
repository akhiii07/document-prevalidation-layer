"""Messaging provider.

`MessagingProvider` is the seam where WhatsApp would plug in. The MVP ships
`SimulatedMessagingProvider`, which records the message and delivers nothing — the
frontend reads the thread back over HTTP.

Simulating this is a deliberate scope decision, and the regulatory research sharpened it
beyond convenience: a WhatsApp Business API deployment puts a third-party processor
(Meta) in the data path, with media retained on infrastructure the lender does not
control. Under the RBI Digital Lending Directions 2025 that needs a documented
assessment, and it is genuinely unresolved (`RESEARCH_REGULATORY.md` §4). Building the
integration now would not have proved anything about the product hypothesis, and would
have put synthetic financial documents through a processor we have not assessed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("docverify.messaging")


@dataclass
class OutboundMessage:
    channel: str
    recipient: str
    body: str
    context: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class MessagingProvider(Protocol):
    name: str

    def send(self, message: OutboundMessage) -> bool: ...


class SimulatedMessagingProvider:
    """Records the message; delivers nothing.

    The recipient is logged but the body is not: a customer message can quote a
    statement period, a bank name and a page count, and there is no reason for any of
    that to sit in application logs when it is already stored, auditable, and readable
    over the API.
    """

    name = "simulated"

    def send(self, message: OutboundMessage) -> bool:
        logger.info(
            "message queued channel=%s recipient=%s kind=%s",
            message.channel,
            _mask_recipient(message.recipient),
            message.context.get("kind"),
        )
        return True


def _mask_recipient(recipient: str) -> str:
    """Phone numbers are personal data; the last four digits are enough to correlate."""
    digits = [c for c in recipient if c.isdigit()]
    if len(digits) < 4:
        return "***"
    return f"***{''.join(digits[-4:])}"


def get_messaging() -> MessagingProvider:
    return SimulatedMessagingProvider()
