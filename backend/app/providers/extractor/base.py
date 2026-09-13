"""Extractor interface.

Two implementations satisfy it -- native text and OCR -- and a Google Document AI
adapter would satisfy the same one (ADR-002). Both produce `RawExtraction`, so the
parser downstream does not know or care which ran.
"""

from __future__ import annotations

from typing import BinaryIO, Protocol, runtime_checkable

from app.domain.extraction import RawExtraction


@runtime_checkable
class DocumentExtractor(Protocol):
    name: str

    def supports(self, stream: BinaryIO) -> bool:
        """Can this extractor usefully read the document?"""
        ...

    def extract(self, stream: BinaryIO) -> RawExtraction: ...


class ExtractionError(RuntimeError):
    """The document could not be read at all.

    Distinct from a low-confidence read: one is a failure of our machinery and belongs
    in front of a human, the other is a judgement about the document
    (`MVP_Product_Requirements_and_Build_Plan.md` section 18).
    """
