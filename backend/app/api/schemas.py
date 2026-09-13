"""API request and response models.

Two things are deliberately absent from every response: `storage_key` (an internal
location that tells a caller nothing useful and something about our layout) and any
unmasked account number (`PRODUCT_SPEC.md` section 12).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ApplicationCreate(BaseModel):
    external_reference: str = Field(min_length=1, max_length=64)
    borrower_name: str = Field(min_length=1, max_length=200)
    business_name: str = Field(min_length=1, max_length=200)
    phone_number: str = Field(min_length=5, max_length=20)


class RequirementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_type: str
    status: str
    submission_count: int
    first_time_cleared: bool | None
    cleared_at: datetime | None


class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    external_reference: str
    borrower_name: str
    business_name: str
    phone_number: str
    status: str
    created_at: datetime
    requirements: list[RequirementRead] = []


class ValidationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    outcome: str
    primary_reason_code: str | None
    secondary_reason_codes: list[str] | None
    composite_confidence: float | None
    customer_message: str | None
    created_at: datetime


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    requirement_id: str
    submission_index: int
    status: str
    original_filename: str
    file_size: int
    is_encrypted: bool
    detected_mime_type: str | None
    created_at: datetime
    updated_at: datetime
    validation: ValidationRead | None = None


class DocumentStatusRead(BaseModel):
    """Lightweight polling payload (`PRODUCT_SPEC.md` section 22 - polling, no sockets)."""

    id: str
    status: str
    outcome: str | None = None
    reason_code: str | None = None
    customer_message: str | None = None
    awaiting_password: bool = False
    password_attempts_remaining: int | None = None


class PasswordSubmit(BaseModel):
    # SecretStr so the value cannot leak through a model repr, a validation error, or a
    # logged request body.
    password: SecretStr = Field(min_length=1, max_length=128)


class DownloadUrlRead(BaseModel):
    url: str
    expires_at: datetime


class ErrorDetail(BaseModel):
    reason_code: str
    message: str


class ApplicationSummary(BaseModel):
    """List row for the Sales/Operations view."""

    id: str
    external_reference: str
    borrower_name: str
    business_name: str
    status: str
    created_at: datetime
    requirement_status: str | None
    submission_count: int
    first_time_cleared: bool | None
    verified_documents: int


# --------------------------------------------------------------------- operations


class ReviewSummary(BaseModel):
    id: str
    document_id: str
    application_reference: str
    borrower_name: str
    business_name: str
    reason_code: str
    confidence: float | None
    submission_index: int
    original_filename: str
    created_at: datetime


class ReviewDetail(ReviewSummary):
    status: str
    document_status: str
    #: Which rules ran, which failed, and on what evidence.
    rule_results: list[dict] | None = None
    #: Components, not just a number: a reviewer needs to see *which* signal was weak.
    confidence_breakdown: dict | None = None
    extracted: dict | None = None
    customer_message: str | None = None
    download_url: str


class ReviewDecision(BaseModel):
    action: Literal["ACCEPT", "REQUEST_NEW_DOCUMENT"]
    reviewer_id: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=1000)


# --------------------------------------------------------------------- conversation


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    direction: str
    kind: str
    body: str
    context: dict | None
    document_id: str | None
    created_at: datetime


# --------------------------------------------------------------------- LOS handoff


class HandoffDocument(BaseModel):
    """What the product hands to the lender (`PRODUCT_SPEC.md` section 21)."""

    document_id: str
    status: str
    submission_index: int
    verified_at: datetime | None
    bank_name: str | None
    account_holder_name: str | None
    #: Masked. The full number never leaves the document.
    account_number_masked: str | None
    account_type: str | None
    period_start: str | None
    period_end: str | None
    page_count: int | None
    transaction_count: int | None
    validation: dict
    download_url: str


class HandoffRead(BaseModel):
    application_reference: str
    borrower_name: str
    business_name: str
    requirement_status: str
    first_time_cleared: bool | None
    submission_count: int
    documents: list[HandoffDocument]
