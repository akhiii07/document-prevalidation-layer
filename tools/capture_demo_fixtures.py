"""Record the real pipeline so a static build can replay it.

GitHub Pages serves files, not processes, so a Pages build of this product has no
backend behind it. The tempting shortcut is to hand-write plausible responses. This does
the opposite: it drives the *actual* running system, polls it exactly as the browser
does, and writes down what came back and when.

Every message, reason code, rule result, confidence number and timing in the resulting
fixture is therefore something the product genuinely produced -- which is the only basis
on which a recorded demo can honestly be shown to anyone.

Usage (with the backend running):
    python tools/capture_demo_fixtures.py
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API = os.environ.get("CAPTURE_API", "http://127.0.0.1:8000")
OPS = os.environ.get("OPS_SHARED_SECRET", "dev-ops-secret-change-me")
OUT = Path(__file__).resolve().parents[1] / "frontend" / "src" / "demo" / "fixtures.json"

#: Poll interval, matched to the UI's own so the recorded frames line up with what a
#: browser would actually have seen.
POLL_S = 0.6
TERMINAL = {"PASSED", "NEEDS_FIX", "IN_REVIEW", "SUPERSEDED"}

#: The documents worth putting in front of a visitor. Deliberately not the whole corpus:
#: a demo that offers nineteen chips teaches nothing. These five cover the outcome
#: contract -- a clean pass, a correctable failure, an encrypted file, a wrong document,
#: and a case the system refuses to decide.
SHOWCASE = [
    "valid_hdfc",
    "wrong_period_short_hdfc",
    "password_protected_icici",
    "wrong_document_gst_certificate",
    "identity_mismatch_axis",
    "balance_break_icici",
    "wrong_account_type_savings_hdfc",
    "valid_sbi",
]

#: Application references for this run. Sequential from a per-run base so a capture
#: never collides with the last one and the queue still reads like a real book of work.
RUN_BASE = 10_000 + int(time.time()) % 80_000

BORROWER = {
    "borrower_name": "Rajesh Kumar Sharma",
    "business_name": "Sharma Metal Works Private Limited",
    "phone_number": "+919820000000",
}


def _call(path: str, *, payload: dict | None = None, ops: bool = False) -> Any:
    """Stdlib only, deliberately: a tool that regenerates the demo should run from a
    clean clone without anyone installing anything first."""
    headers = {"Accept": "application/json"}
    if ops:
        headers["X-Ops-Secret"] = OPS
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(f"{API}{path}", data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode() or "null")
    except urllib.error.HTTPError as exc:
        # Naming the path matters: every call here goes through one function, so a bare
        # "404" says nothing about which of the fifteen endpoints was missing.
        detail = exc.read().decode(errors="replace")[:200]
        raise RuntimeError(f"{exc.code} on {path}: {detail}") from None


def get(path: str) -> Any:
    return _call(path)


def ops_get(path: str) -> Any:
    return _call(path, ops=True)


def post(path: str, payload: dict | None = None) -> Any:
    return _call(path, payload=payload or {})


def record(sample_id: str, index: int) -> dict:
    """Submit one sample and write down everything the browser would have seen."""
    # Unique per run -- the reference is unique in the schema, so a second capture against
    # the same database would collide -- but shaped like a real application reference.
    # A queue full of "DEMO-005-39885" reads as test scaffolding, not a product.
    reference = f"FLX-{RUN_BASE + index}"
    application = post("/applications", {"external_reference": reference, **BORROWER})
    app_id = application["id"]
    # The welcome instruction is written at application creation, so it predates the
    # first frame. The replay needs it verbatim rather than a copy of the string kept in
    # the frontend, which would drift the moment the backend's wording changed.
    welcome = get(f"/applications/{app_id}/messages")
    before = {m["id"] for m in welcome}

    started = time.monotonic()
    document = post(f"/applications/{app_id}/documents/sample", {"id": sample_id})
    doc_id = document["id"]

    # The submission returns 202 before its transaction is guaranteed visible to the
    # next connection, so an immediate poll can 404 on a document that certainly exists.
    # The browser never sees this because it waits a poll interval first; a tight loop
    # does. Wait for visibility rather than recording a phantom failure.
    for _ in range(40):
        try:
            get(f"/documents/{doc_id}/status")
            break
        except RuntimeError as exc:
            if "404" not in str(exc):
                raise
            time.sleep(0.1)

    frames: list[dict] = []
    seen: set[str] = set(before)
    password_prompt_at: int | None = None

    while True:
        elapsed = int((time.monotonic() - started) * 1000)
        status = get(f"/documents/{doc_id}/status")
        messages = get(f"/applications/{app_id}/messages")
        fresh = [m for m in messages if m["id"] not in seen]
        seen.update(m["id"] for m in fresh)

        if fresh or not frames or status["status"] != frames[-1]["status"]["status"]:
            frames.append({"t": elapsed, "status": status, "messages": fresh})

        if status["awaiting_password"]:
            # Stop here and let the replay wait for the visitor, exactly as the real
            # product does. The unlock is recorded as a second leg below.
            password_prompt_at = elapsed
            break
        if status["status"] in TERMINAL:
            break
        if elapsed > 240_000:
            raise TimeoutError(f"{sample_id} never settled")
        time.sleep(POLL_S)

    unlock: dict | None = None
    if password_prompt_at is not None:
        unlock = record_unlock(app_id, doc_id, seen)

    requirement = get(f"/applications/{app_id}")["requirements"][0]
    review = None
    if get(f"/documents/{doc_id}/status")["status"] == "IN_REVIEW":
        summary = next(
            (r for r in ops_get("/reviews") if r["application_reference"] == reference),
            None,
        )
        if summary:
            review = ops_get(f"/reviews/{summary['id']}")

    handoff = None
    if requirement["status"] == "CLEARED":
        handoff = ops_get(f"/applications/{app_id}/handoff")

    return {
        "application": application,
        "welcome": welcome,
        "document": document,
        "frames": frames,
        "awaiting_password_at": password_prompt_at,
        "unlock": unlock,
        "requirement": requirement,
        "review": review,
        "handoff": handoff,
    }


def record_unlock(app_id: str, doc_id: str, seen: set[str]) -> dict:
    """The second leg: what happens after the correct password is supplied."""
    password = os.environ.get("SAMPLE_PDF_PASSWORD", "SHAR1503")
    started = time.monotonic()
    post(f"/documents/{doc_id}/password", {"password": password})

    frames: list[dict] = []
    while True:
        elapsed = int((time.monotonic() - started) * 1000)
        status = get(f"/documents/{doc_id}/status")
        messages = get(f"/applications/{app_id}/messages")
        fresh = [m for m in messages if m["id"] not in seen]
        seen.update(m["id"] for m in fresh)

        if fresh or not frames or status["status"] != frames[-1]["status"]["status"]:
            frames.append({"t": elapsed, "status": status, "messages": fresh})
        if status["status"] in TERMINAL:
            break
        if elapsed > 240_000:
            raise TimeoutError("password leg never settled")
        time.sleep(POLL_S)

    return {"password": password, "frames": frames}


def main() -> None:
    samples = {s["id"]: s for s in get("/demo/samples")}
    missing = [s for s in SHOWCASE if s not in samples]
    if missing:
        raise SystemExit(f"not in the corpus: {missing}")

    tapes: dict[str, dict] = {}
    for index, sample_id in enumerate(SHOWCASE, start=1):
        print(f"[{index}/{len(SHOWCASE)}] {sample_id} ...", end=" ", flush=True)
        tape = record(sample_id, index)
        outcome = tape["frames"][-1]["status"]
        final = (tape["unlock"]["frames"][-1]["status"] if tape["unlock"] else outcome)
        print(f"{final['status']} / {final.get('reason_code') or '-'} in {final and tape['frames'][-1]['t']}ms")
        tapes[sample_id] = tape

    fixtures = {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "samples": [samples[s] for s in SHOWCASE],
        "tapes": tapes,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fixtures, indent=1), encoding="utf-8")
    size = OUT.stat().st_size / 1024
    print(f"\nwrote {OUT.relative_to(OUT.parents[3])}  ({size:.0f} KB)")


if __name__ == "__main__":
    main()
