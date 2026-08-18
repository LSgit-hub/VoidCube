"""Summarize recall-query shapes without retaining query text or identifiers."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
from typing import Any

from voidcube.infrastructure.runtime.layout import get_runtime_layout


FORMAT = "voidcube.memory.recall-shapes"
VERSION = 1
_KNOWN_INTENTS = {"recent_conversation", "specific_memory"}
_KNOWN_REQUEST_SOURCES = {"api", "auto_prefetch", "tool"}
_KNOWN_STATUSES = {"empty", "failure", "hit"}


def _allowlisted(value: object, allowed: set[str]) -> str:
    normalized = str(value or "").strip()
    return normalized if normalized in allowed else "unknown"


def _query_language(query: str) -> str:
    has_cjk = any("\u4e00" <= character <= "\u9fff" for character in query)
    has_latin = any(character.isascii() and character.isalpha() for character in query)
    if has_cjk and has_latin:
        return "mixed"
    if has_cjk:
        return "cjk"
    if has_latin:
        return "latin"
    return "other"


def _length_bucket(query: str) -> str:
    if len(query) <= 20:
        return "short_1_20"
    if len(query) <= 60:
        return "medium_21_60"
    return "long_61_plus"


def summarize_recall_shapes(db_path: str | Path) -> dict[str, Any]:
    """Return aggregate recall shapes; no source text or identifiers are returned."""
    path = Path(db_path)
    with sqlite3.connect(path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'recall_traces'"
        ).fetchone()
        if not exists:
            raise ValueError("Memory database does not contain recall_traces")
        rows = conn.execute(
            "SELECT request_source, query, intent, status FROM recall_traces"
        ).fetchall()

    shapes: Counter[tuple[str, str, str, str, str]] = Counter()
    for request_source, query, intent, status in rows:
        text = str(query or "")
        shape = (
            _query_language(text),
            _length_bucket(text),
            _allowlisted(intent, _KNOWN_INTENTS),
            _allowlisted(request_source, _KNOWN_REQUEST_SOURCES),
            _allowlisted(status, _KNOWN_STATUSES),
        )
        shapes[shape] += 1

    shape_rows = [
        {
            "language": shape[0],
            "length_bucket": shape[1],
            "intent": shape[2],
            "request_source": shape[3],
            "status": shape[4],
            "count": count,
        }
        for shape, count in sorted(shapes.items())
    ]
    dimensions = (
        "language",
        "length_bucket",
        "intent",
        "request_source",
        "status",
    )
    distributions = {
        dimension: {
            value: sum(
                row["count"] for row in shape_rows if row[dimension] == value
            )
            for value in sorted({row[dimension] for row in shape_rows})
        }
        for dimension in dimensions
    }
    return {
        "format": FORMAT,
        "version": VERSION,
        "source_trace_count": len(rows),
        "privacy": {
            "raw_query_text_retained": False,
            "identifiers_retained": False,
            "custom_dimension_values_retained": False,
        },
        "distributions": distributions,
        "shapes": shape_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=get_runtime_layout().memory_db)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize_recall_shapes(args.db)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
