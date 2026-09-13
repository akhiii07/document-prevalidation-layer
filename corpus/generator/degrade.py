"""Controlled defect injection.

Each function introduces exactly one defect so that a corpus document tests one rule.
Documents with several overlapping problems would tell us the pipeline reached *a*
verdict, not that it reached the right one for the right reason.
"""

from __future__ import annotations

import dataclasses
import io
import random
from pathlib import Path

import pymupdf
from PIL import Image, ImageEnhance, ImageFilter
from pypdf import PdfReader, PdfWriter

from corpus.generator.ledger import Statement


def encrypt_pdf(src: Path, dst: Path, password: str) -> None:
    """Password-protect a PDF, as Indian banks do with e-statements.

    AES-256 is used rather than the legacy RC4 that some tools still default to, so the
    decryption path is exercised against real modern encryption.
    """
    reader = PdfReader(str(src))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_metadata(reader.metadata or {})
    writer.encrypt(user_password=password, owner_password=None, algorithm="AES-256")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as fh:
        writer.write(fh)


def remove_pages(src: Path, dst: Path, drop: set[int]) -> None:
    """Drop pages (1-indexed) from the middle of a statement.

    This is the *incomplete document* case: page numbering will show a gap AND the
    balance will fail to carry across the seam. Two agreeing signals, which is what
    lets R-CMP-003 issue a confident, customer-actionable FIX.
    """
    reader = PdfReader(str(src))
    writer = PdfWriter()
    for index, page in enumerate(reader.pages, start=1):
        if index not in drop:
            writer.add_page(page)
    writer.add_metadata(reader.metadata or {})
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as fh:
        writer.write(fh)


def corrupt_file(src: Path, dst: Path, *, seed: int = 0) -> None:
    """Produce a PDF that no parser can recover -- an interrupted or garbled transfer.

    Naive truncation is not good enough. PDF parsers are deliberately tolerant and will
    rebuild a damaged cross-reference table by scanning for objects, so a truncated file
    often still opens; whether it does is NOT monotonic in how much was kept, because it
    depends on where object boundaries happen to fall, and it differs between parsers
    (PyMuPDF recovered truncations that pypdf rejected outright). A fraction tuned by
    hand would silently stop working the next time the corpus is regenerated.

    So: keep the `%PDF-` signature -- the file must still reach the *parse* check
    (R-FILE-003) rather than be rejected by the *type* check (R-FILE-001) -- and replace
    everything after it with deterministic noise. No recoverable objects, no page tree,
    no ambiguity.
    """
    raw = src.read_bytes()
    newline = raw.find(b"\n", 0, 64)
    header_len = newline + 1 if newline != -1 else 16
    rng = random.Random(seed)
    body = bytes(rng.randrange(256) for _ in range(min(len(raw) - header_len, 8192)))

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(raw[:header_len] + body)

    _assert_unparseable(dst)


def _assert_unparseable(path: Path) -> None:
    """Fail the build if the 'corrupt' document is in fact readable.

    A corpus document that does not contain the defect it claims would make the
    validation engine appear to pass a test it never actually took.
    """
    import pymupdf as _pymupdf

    for name, opener in (
        ("pymupdf", lambda: [p.get_text() for p in _pymupdf.open(str(path))]),
        ("pypdf", lambda: PdfReader(str(path)).pages[0].extract_text()),
    ):
        try:
            opener()
        except Exception:  # noqa: BLE001,S112 - rejection is the desired outcome here
            continue
        raise AssertionError(
            f"{path.name} was meant to be unparseable but {name} read it successfully"
        )


def rasterise(
    src: Path,
    dst: Path,
    *,
    dpi: int = 200,
    degrade: bool = False,
    seed: int = 0,
) -> None:
    """Convert a native-text PDF into an image-only PDF (a 'scan').

    With `degrade=True` the pages get the artefacts of a phone photo of a printout that
    has then been forwarded through a messaging app: rotation, blur, washed-out contrast,
    sensor noise, downscaling and heavy JPEG re-encoding. The target is a page a human
    can still read but OCR cannot read *reliably* -- that is what REVIEW means.

    The degradation is deliberately calibrated between two failure modes. Too light and
    the page OCRs cleanly, making the low-confidence scenario a fiction. Too heavy and
    it becomes unreadable, which is a different reason code entirely
    (READABILITY_NO_TEXT, a FIX) and would test the wrong rule.
    """
    rng = random.Random(seed)
    doc = pymupdf.open(str(src))
    images: list[Image.Image] = []

    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        if degrade:
            img = img.rotate(
                rng.uniform(-1.5, 1.5), resample=Image.BICUBIC, fillcolor="white"
            )
            img = img.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.9, 1.2)))
            img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.70, 0.80))
            img = ImageEnhance.Brightness(img).enhance(rng.uniform(1.02, 1.07))
            img = _add_noise(img, rng, amount=34)
            img = img.resize(
                (int(img.width * 0.62), int(img.height * 0.62)), resample=Image.LANCZOS
            )
            # A messaging app re-encodes aggressively. JPEG ringing around glyph
            # edges is what actually defeats OCR on forwarded photos, and no amount
            # of blur alone reproduces it.
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=rng.randint(36, 44))
            buffer.seek(0)
            img = Image.open(buffer)
            img.load()

        images.append(img.convert("RGB"))

    doc.close()
    dst.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        str(dst),
        save_all=True,
        append_images=images[1:],
        format="PDF",
        resolution=float(dpi if not degrade else dpi * 0.62),
    )


def _add_noise(img: Image.Image, rng: random.Random, amount: int) -> Image.Image:
    pixels = img.load()
    assert pixels is not None
    width, height = img.size
    # Sparse sampling: enough speckle to hurt OCR without a per-pixel loop.
    for _ in range((width * height) // 40):
        x = rng.randrange(width)
        y = rng.randrange(height)
        r, g, b = pixels[x, y]
        delta = rng.randint(-amount, amount)
        pixels[x, y] = (
            max(0, min(255, r + delta)),
            max(0, min(255, g + delta)),
            max(0, min(255, b + delta)),
        )
    return img


def break_balance(st: Statement, *, index: int, delta_paise: int) -> Statement:
    """Alter one stated balance while leaving every page present.

    This is the *edited document* case, and it is deliberately indistinguishable from
    missing content by arithmetic alone -- which is exactly why R-CMP-002 routes it to
    REVIEW rather than telling the customer to re-upload (ADR-008).

    Only the single stated balance changes; subsequent balances keep their original
    values, so the break is a one-row discontinuity rather than a shifted tail. That is
    what a hand-edited PDF looks like.
    """
    txns = list(st.transactions)
    target = txns[index]
    txns[index] = dataclasses.replace(
        target, balance_paise=target.balance_paise + delta_paise
    )
    return dataclasses.replace(st, transactions=txns)


def strip_producer_metadata(src: Path, dst: Path, producer: str) -> None:
    """Rewrite the producer/creator strings to those of a consumer PDF editor.

    Feeds R-INT-001. On its own this proves nothing -- customers legitimately re-save
    and split PDFs -- which is why a single integrity signal never triggers REVIEW.
    """
    reader = PdfReader(str(src))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_metadata({"/Producer": producer, "/Creator": producer})
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as fh:
        writer.write(fh)
