"""Native text-layer extraction via pdfplumber.

This is the default path because a bank-issued e-statement is a native-text PDF. It is
fast, exact, and free -- which is most of why local-first extraction was the right call
(ADR-002).
"""

from __future__ import annotations

import time
from typing import BinaryIO

import pdfplumber

from app.domain.enums import TextLayer
from app.domain.extraction import Cell, Page, RawExtraction
from app.providers.extractor.base import ExtractionError

#: Below this many characters a page is treated as having no usable text layer, which
#: routes the document to OCR. Scanned PDFs often carry a few stray characters from a
#: header stamp, so "any text at all" is the wrong test.
MIN_CHARS_FOR_TEXT_LAYER = 50


class PdfPlumberExtractor:
    name = "pdfplumber"

    def supports(self, stream: BinaryIO) -> bool:
        """True when the document carries a usable native text layer."""
        stream.seek(0)
        try:
            with pdfplumber.open(stream) as pdf:
                for page in pdf.pages[:3]:
                    if len((page.extract_text() or "").strip()) >= MIN_CHARS_FOR_TEXT_LAYER:
                        return True
            return False
        except Exception:  # noqa: BLE001 - an unreadable file is simply unsupported here
            return False
        finally:
            stream.seek(0)

    def extract(self, stream: BinaryIO) -> RawExtraction:
        started = time.monotonic()
        stream.seek(0)
        pages: list[Page] = []

        try:
            with pdfplumber.open(stream) as pdf:
                total = len(pdf.pages)
                for index, page in enumerate(pdf.pages, start=1):
                    words = page.extract_words(
                        keep_blank_chars=False,
                        use_text_flow=False,
                        # Words are merged into cells below; splitting on these keeps
                        # long slash-delimited UPI narration from fusing with the
                        # neighbouring column.
                        extra_attrs=[],
                    )
                    cells = [
                        Cell(
                            text=w["text"],
                            x0=float(w["x0"]),
                            x1=float(w["x1"]),
                            top=float(w["top"]),
                            bottom=float(w["bottom"]),
                            confidence=1.0,
                        )
                        for w in words
                    ]
                    pages.append(
                        Page(
                            number=index,
                            width=float(page.width),
                            height=float(page.height),
                            cells=cells,
                        )
                    )
        except Exception as exc:
            raise ExtractionError(f"pdfplumber could not read the document: {exc}") from exc

        return RawExtraction(
            provider=self.name,
            text_layer=TextLayer.NATIVE,
            pages=pages,
            document_page_count=total,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
