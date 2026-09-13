"""SQLAlchemy models.

Importing this package registers every model on `Base.metadata`, which is what Alembic
autogeneration reads.
"""

from app.models.application import Application, DocumentRequirement
from app.models.document import Document, DocumentExtraction
from app.models.event import DocumentEvent
from app.models.job import Job
from app.models.message import Message
from app.models.review import Review
from app.models.validation import ValidationResult

__all__ = [
    "Application",
    "Document",
    "DocumentEvent",
    "DocumentExtraction",
    "DocumentRequirement",
    "Job",
    "Message",
    "Review",
    "ValidationResult",
]
