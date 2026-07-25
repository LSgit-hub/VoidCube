from __future__ import annotations

import json
import sqlite3

import pytest

from scripts.summarize_memory_recall_shapes import summarize_recall_shapes


pytestmark = [pytest.mark.unit]


def _trace_db(tmp_path):
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE recall_traces (request_source TEXT, query TEXT, "
            "intent TEXT, status TEXT)"
        )
        conn.executemany(
            "INSERT INTO recall_traces VALUES (?, ?, ?, ?)",
            [
                ("api", "刚才缓存间隔改成多久", "recent_conversation", "hit"),
                ("tool", "API 429 retry", "specific_memory", "empty"),
                (
                    "private-user@example.com",
                    "私密 api_key=secret-value private-person@example.com",
                    "customer-secret-intent",
                    "private-status",
                ),
            ],
        )
    return path


def test_recall_shape_summary_retains_only_allowlisted_aggregate_dimensions(tmp_path):
    result = summarize_recall_shapes(_trace_db(tmp_path))
    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)

    assert result["source_trace_count"] == 3
    assert result["privacy"] == {
        "raw_query_text_retained": False,
        "identifiers_retained": False,
        "custom_dimension_values_retained": False,
    }
    assert result["distributions"]["language"] == {"cjk": 1, "latin": 1, "mixed": 1}
    assert result["distributions"]["intent"] == {
        "recent_conversation": 1,
        "specific_memory": 1,
        "unknown": 1,
    }
    for sensitive in (
        "secret-value",
        "private-person@example.com",
        "private-user@example.com",
        "customer-secret-intent",
        "private-status",
    ):
        assert sensitive not in rendered


def test_recall_shape_summary_requires_trace_table(tmp_path):
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()

    with pytest.raises(ValueError, match="recall_traces"):
        summarize_recall_shapes(path)
