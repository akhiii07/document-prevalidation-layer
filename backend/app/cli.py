"""Small operator commands that have no place in the HTTP API.

Purging is the first: it is destructive, it is rare, and exposing it as an endpoint would
mean a shared secret standing between an accident and unrecoverable data loss.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import or_, select

from app.db.session import get_sessionmaker
from app.models.document import Document
from app.services.retention import purge_document


def _purge(args: argparse.Namespace) -> int:
    session = get_sessionmaker()()
    try:
        documents = list(
            session.scalars(
                select(Document).where(
                    or_(
                        Document.id.in_(args.identifier),
                        Document.original_filename.in_(args.identifier),
                    )
                )
            )
        )
        if not documents:
            print("no documents matched", file=sys.stderr)
            return 1

        for document in documents:
            print(f"{document.id}  {document.original_filename}")
        if not args.yes:
            # Naming the count rather than asking for a typed confirmation: this runs in
            # a terminal an operator already had to reach, and the list above is printed
            # first precisely so the decision is made against the real targets.
            print(f"\nRe-run with --yes to purge {len(documents)} document(s).")
            return 0

        for document in documents:
            removed = purge_document(session, document, reason=args.reason)
            print(f"purged {document.id}: {removed}")
        session.commit()
    finally:
        session.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    purge = sub.add_parser(
        "purge-document",
        help="Remove a document's content, keeping its audit trail.",
    )
    purge.add_argument("identifier", nargs="+", help="document id or original filename")
    purge.add_argument("--reason", default="operator request")
    purge.add_argument("--yes", action="store_true", help="actually purge")
    purge.set_defaults(func=_purge)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
