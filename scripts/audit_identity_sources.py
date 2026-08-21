"""Read-only audit of identity-memory source categories in the canonical Mem DB."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from memai.repository.paths import get_mem_runtime_layout


def audit(db_path: Path) -> dict[str, object]:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT COALESCE(identity_layer, '<none>'), "
            "COALESCE(origin_type, '<none>'), COUNT(*) "
            "FROM compressed_memories GROUP BY identity_layer, origin_type "
            "ORDER BY identity_layer, origin_type"
        ).fetchall()
        turn_rows = connection.execute(
            "SELECT COUNT(*) FROM turns WHERE metadata LIKE '%identity_experience%'"
        ).fetchone()
        authored_rows = connection.execute(
            "SELECT COUNT(*) FROM turns WHERE metadata LIKE '%self_authored_identity%'"
        ).fetchone()
    finally:
        connection.close()
    return {
        "db_path": str(db_path),
        "compressed_memory_sources": [
            {"identity_layer": row[0], "origin_type": row[1], "count": row[2]}
            for row in rows
        ],
        "turns_marked_identity_experience": int(turn_rows[0] if turn_rows else 0),
        "turns_marked_self_authored_identity": int(
            authored_rows[0] if authored_rows else 0
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=get_mem_runtime_layout().memory_db)
    args = parser.parse_args()
    print(json.dumps(audit(args.db), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
