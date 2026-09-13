"""Choosing an extractor.

The rule is simple and deliberately conservative: use the native text layer when the
document has one, otherwise OCR. Preferring native is not just a performance choice --
native text is exact, while OCR introduces a confidence penalty that propagates all the
way to the verdict.
"""

from __future__ import annotations

import logging
from typing import BinaryIO

from app.domain.enums import TextLayer
from app.domain.extraction import RawExtraction
from app.providers.extractor.base import DocumentExtractor, ExtractionError
from app.providers.extractor.native import PdfPlumberExtractor
from app.providers.extractor.ocr import RapidOcrExtractor, ocr_available

logger = logging.getLogger("docverify.extractor")


def get_extractors() -> list[DocumentExtractor]:
    return [PdfPlumberExtractor(), RapidOcrExtractor()]


def extract_document(stream: BinaryIO) -> RawExtraction:
    native = PdfPlumberExtractor()
    if native.supports(stream):
        return native.extract(stream)

    if not ocr_available():
        # Degrade honestly rather than pretending the document is empty. An empty
        # extraction would flow on and produce a confident, wrong verdict.
        raise ExtractionError(
            "document has no text layer and no OCR engine is installed; "
            "install the 'ocr' extra to read scanned documents"
        )

    logger.info("no native text layer; falling back to OCR")
    return RapidOcrExtractor().extract(stream)


def text_layer_of(raw: RawExtraction) -> TextLayer:
    return raw.text_layer
