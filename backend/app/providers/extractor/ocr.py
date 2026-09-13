"""OCR extraction for image-only PDFs.

RapidOCR (ONNX Runtime) rather than Tesseract: it installs from pip with bundled models
and needs no system binary, which keeps the "clone and run" promise intact on a machine
with nothing pre-installed. It also runs entirely locally, which is the requirement that
drove ADR-002 in the first place.

**Measured cost:** roughly 20 seconds per A4 page at 150 dpi on a 12-core laptop. Page
level parallelism was tried and is *counter-productive* (0.61x -- ONNX Runtime already
saturates the cores, so multiple engines just thrash). That latency is the honest price
of keeping Indian financial documents on Indian infrastructure; a managed parser returns
in seconds. It is tolerable because the OCR path is the minority case: bank-issued
e-statements carry a native text layer.
"""

from __future__ import annotations

import logging
import time
from typing import Any, BinaryIO

from app.config.rules import get_rules
from app.domain.enums import TextLayer
from app.domain.extraction import Cell, Page, RawExtraction
from app.providers.extractor.base import ExtractionError

logger = logging.getLogger("docverify.extractor.ocr")

_engine: Any | None = None


def ocr_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _get_engine() -> Any:
    """One process-wide engine. Model load costs ~2s and is pure overhead per call."""
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


class RapidOcrExtractor:
    name = "rapidocr"

    def supports(self, stream: BinaryIO) -> bool:
        return ocr_available()

    def extract(self, stream: BinaryIO) -> RawExtraction:
        import numpy as np
        import pymupdf

        # Imported here rather than at module scope: the parser is a *service*, and
        # a provider that imports one at load time inverts the dependency direction
        # for every other caller of this module.
        from app.services.normalization import probe_table

        rules = get_rules()
        cfg = rules.extraction
        started = time.monotonic()
        engine = _get_engine()

        stream.seek(0)
        try:
            document = pymupdf.open(stream=stream.read(), filetype="pdf")
        except Exception as exc:
            raise ExtractionError(f"could not open the document for OCR: {exc}") from exc

        pages: list[Page] = []
        warnings: list[str] = []
        aborted = False
        total = document.page_count

        try:
            for index in range(min(total, cfg.ocr_max_pages)):
                pixmap = document[index].get_pixmap(dpi=cfg.ocr_dpi)
                image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                    pixmap.height, pixmap.width, pixmap.n
                )
                result, _ = engine(image)
                pages.append(_to_page(index + 1, pixmap.width, pixmap.height, result or []))

                # Stop once the document is demonstrably unreadable. Spending three more
                # minutes to extract text we already know we cannot trust helps nobody:
                # the outcome is REVIEW either way, and the customer waits longer for it.
                if index + 1 >= cfg.ocr_probe_pages:
                    probed = pages[: cfg.ocr_probe_pages]
                    low = sum(
                        p.low_confidence_fraction(rules.readability.min_ocr_word_confidence)
                        for p in probed
                    ) / len(probed)
                    if low > cfg.ocr_abort_low_fraction and index + 1 < total:
                        aborted = True
                        warnings.append(
                            f"stopped after {index + 1} of {total} pages: "
                            f"{low:.0%} of reads on the first {cfg.ocr_probe_pages} pages "
                            f"were below the confidence floor"
                        )
                        logger.info("aborting OCR early: %s", warnings[-1])
                        break

                # The second futility test, and the one that catches a *legible*
                # document we still cannot use. A cropped screenshot of a banking app
                # OCRs cleanly -- high confidence, real dates, real figures -- but a
                # column the statement needs was never in the image. Once the table's
                # layout is established, every later page repeats it: reading six more
                # of them cannot change the verdict, and the customer waits another
                # minute to be told the same thing.
                #
                # The test is deliberately conservative. Rows must have been *parsed*
                # before we conclude anything, so a cover page or a late-starting table
                # reads on rather than being written off.
                if index + 1 >= cfg.ocr_structural_probe_pages and index + 1 < total:
                    probe = probe_table(pages, rules)
                    if probe.table_found and probe.transactions and not probe.complete:
                        aborted = True
                        warnings.append(
                            f"stopped after {index + 1} of {total} pages: {probe.transactions} "
                            f"transaction rows were read and none carried both an amount "
                            f"and a balance, which later pages repeat"
                        )
                        logger.info("aborting OCR early: %s", warnings[-1])
                        break

            if not aborted and total > cfg.ocr_max_pages:
                warnings.append(f"read {cfg.ocr_max_pages} of {total} pages (page cap)")
        finally:
            document.close()

        return RawExtraction(
            provider=self.name,
            text_layer=TextLayer.OCR,
            pages=pages,
            document_page_count=total,
            duration_ms=int((time.monotonic() - started) * 1000),
            warnings=warnings,
            aborted_early=aborted,
        )


def _to_page(number: int, width: int, height: int, result: list) -> Page:
    cells: list[Cell] = []
    for box, text, confidence in result:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        cells.append(
            Cell(
                text=str(text).strip(),
                x0=min(xs),
                x1=max(xs),
                top=min(ys),
                bottom=max(ys),
                confidence=float(confidence),
            )
        )
    return Page(number=number, width=float(width), height=float(height), cells=cells)
