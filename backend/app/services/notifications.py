"""Sending and recording messages.

Everything the system says to anyone goes through here, so the conversation is a single
auditable record rather than something reconstructed from validation rows after the fact.

Two rules hold for every message body:

* it never contains a password, an unmasked account number, a reason code, a confidence
  number, or any other internal (`PRODUCT_SPEC.md` §9 and §12);
* a FIX always carries a specific reason *and* a specific next action.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import EventType, MessageChannel, MessageDirection, MessageKind
from app.models.application import Application, DocumentRequirement
from app.models.document import Document
from app.models.message import Message
from app.providers.messaging import MessagingProvider, OutboundMessage, get_messaging
from app.services.audit import record_event

logger = logging.getLogger("docverify.notifications")

#: What Sales tells the customer to do. The product's entry point
#: (`PRODUCT_SPEC.md` §5): the customer sends the document *directly* to this number.
WELCOME_INSTRUCTION = (
    "Hello! To continue with your loan application, please send your "
    "6-month current-account bank statement here as a PDF.\n\n"
    "I'll check it straight away and tell you if anything needs fixing."
)


def _context(application: Application) -> str:
    return application.phone_number


def record_message(
    session: Session,
    application: Application,
    *,
    body: str,
    kind: MessageKind,
    channel: MessageChannel = MessageChannel.CUSTOMER,
    direction: MessageDirection = MessageDirection.OUTBOUND,
    document: Document | None = None,
    requirement_id: str | None = None,
    context: dict[str, Any] | None = None,
    provider: MessagingProvider | None = None,
) -> Message:
    provider = provider or get_messaging()

    # Stamp the document a message is *about* into its context, here rather than at each
    # call site, so no future message can be written without it.
    #
    # A thread holds more than one submission, and a verdict read against the wrong one
    # is worse than no verdict: the customer sees "this is a savings account" under a
    # document they know is a current account and concludes the system is broken. The
    # filename is the only handle they have on which file we mean.
    if document is not None:
        context = {
            "filename": document.original_filename,
            "submission_index": document.submission_index,
            **(context or {}),
        }

    message = Message(
        application_id=application.id,
        requirement_id=requirement_id or (document.requirement_id if document else None),
        document_id=document.id if document else None,
        channel=channel,
        direction=direction,
        kind=kind,
        body=body,
        context=context,
        provider=provider.name,
    )
    session.add(message)
    session.flush()

    if direction is MessageDirection.OUTBOUND:
        recipient = (
            _context(application)
            if channel is MessageChannel.CUSTOMER
            else f"sales:{application.external_reference}"
        )
        provider.send(
            OutboundMessage(
                channel=channel.value,
                recipient=recipient,
                body=body,
                context={"kind": kind.value},
            )
        )

    record_event(
        session,
        event_type=(
            EventType.SALES_NOTIFIED if channel is MessageChannel.SALES else EventType.MESSAGE_SENT
        ),
        document_id=message.document_id,
        requirement_id=message.requirement_id,
        application_id=application.id,
        application_reference=application.external_reference,
        # The body is deliberately not in the audit payload: it is already stored, and
        # the event log has the longest retention of anything in the system.
        payload={"channel": channel.value, "kind": kind.value, "direction": direction.value},
    )
    return message


def send_welcome(session: Session, application: Application, requirement_id: str) -> Message:
    return record_message(
        session,
        application,
        body=WELCOME_INSTRUCTION,
        kind=MessageKind.INSTRUCTION,
        requirement_id=requirement_id,
    )


def record_inbound_document(
    session: Session, application: Application, document: Document
) -> Message:
    """The customer's side of the thread.

    Recorded as a message so the conversation reads as a conversation, and so the
    Operations view can see what the customer actually sent, in order.
    """
    return record_message(
        session,
        application,
        body=document.original_filename,
        kind=MessageKind.DOCUMENT_RECEIVED,
        direction=MessageDirection.INBOUND,
        document=document,
        context={
            "filename": document.original_filename,
            "size_bytes": document.file_size,
            "submission_index": document.submission_index,
        },
    )


def notify_superseded(
    session: Session,
    application: Application,
    superseded: list[Document],
    replacement: Document,
) -> list[Message]:
    """Tell the customer we stopped working on their previous file.

    Silence here is how a customer ends up reading a verdict about one document as
    though it were about another. In testing, someone uploaded their statement, grew
    impatient while it processed, tried a sample instead, and then read the sample's
    verdict -- "this is a savings account", with dates from a different statement -- as
    a judgement on their own document. Every word of it was true about a file they had
    not sent. The system had not lied; it had simply never mentioned that it had moved
    on, and that is the same thing from where the customer is sitting.
    """
    messages: list[Message] = []
    for document in superseded:
        messages.append(
            record_message(
                session,
                application,
                body=(
                    f"I've stopped checking “{document.original_filename}” "
                    f"because you sent “{replacement.original_filename}” after it. "
                    "I'm checking the newer one now — send the earlier file again if "
                    "you'd rather I looked at that."
                ),
                kind=MessageKind.SUBMISSION_SUPERSEDED,
                document=document,
                context={
                    "superseded_filename": document.original_filename,
                    "superseded_by_filename": replacement.original_filename,
                    "superseded_by_document_id": replacement.id,
                },
            )
        )
    return messages


def notify_sales(
    session: Session,
    application: Application,
    document: Document,
    summary: dict[str, Any],
) -> Message:
    """The PASS handoff (`PRODUCT_SPEC.md` §19).

    Sales gets the application reference, the status and the structured data — enough to
    act without opening the document. The full payload, including a signed download link,
    is available at `/applications/{id}/handoff`.
    """
    lines = [
        f"Bank statement verified for {application.external_reference}.",
        "",
        f"Borrower: {application.borrower_name}",
    ]
    if summary.get("bank_name"):
        lines.append(f"Bank: {summary['bank_name']}")
    if summary.get("period_start") and summary.get("period_end"):
        lines.append(f"Period: {summary['period_start']} to {summary['period_end']}")
    if summary.get("account_number_masked"):
        lines.append(f"Account: {summary['account_number_masked']}")
    lines += ["", "The verified document is ready to download."]

    return record_message(
        session,
        application,
        body="\n".join(lines),
        kind=MessageKind.SALES_NOTIFICATION,
        channel=MessageChannel.SALES,
        document=document,
        context={"outcome": "PASS", "submission_index": document.submission_index},
    )


def application_for(
    session: Session, document: Document
) -> tuple[Application, DocumentRequirement]:
    requirement = session.get(DocumentRequirement, document.requirement_id)
    if requirement is None:
        raise LookupError(f"requirement {document.requirement_id} not found")
    application = session.get(Application, requirement.application_id)
    if application is None:
        raise LookupError(f"application {requirement.application_id} not found")
    return application, requirement


def thread(session: Session, application_id: str, channel: MessageChannel) -> list[Message]:
    return list(
        session.scalars(
            select(Message)
            .where(Message.application_id == application_id, Message.channel == channel)
            .order_by(Message.created_at, Message.id)
        ).all()
    )
