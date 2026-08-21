"""Read-only audit of identity-memory source categories in the canonical Mem DB."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from memai.repository.paths import get_mem_runtime_layout


_TURN_ID_RE = re.compile(r"^turn:(.+)$")
_SELF_FIELDS = ("self_claim", "what_changed", "continuity_impact")


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _turn_ids(record: dict[str, Any]) -> list[str]:
    candidates = [record.get("origin_id"), *_json_list(record.get("source_turns")),
                  *_json_list(record.get("evidence_refs"))]
    result: list[str] = []
    for candidate in candidates:
        match = _TURN_ID_RE.match(str(candidate or ""))
        if match and match.group(1) not in result:
            result.append(match.group(1))
    return result


def classify_identity_record(record: dict[str, Any], turns: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Classify one legacy identity record without mutating the database."""
    metadata = _json_object(record.get("identity_metadata"))
    turn_rows = turns or []
    speakers = sorted({str(row.get("speaker") or "<none>") for row in turn_rows})
    turn_metadata = [
        _json_object(row.get("metadata"))
        for row in turn_rows
    ]
    merged = {}
    for item in turn_metadata:
        merged.update(item)
    verified = metadata.get("verified") is True or merged.get("verified") is True
    self_authored = (
        metadata.get("self_authored_identity") is True
        or merged.get("self_authored_identity") is True
    )
    authored_by = metadata.get("authored_by") or merged.get("verified_by")
    fields = {
        field: bool(str(metadata.get(field) or merged.get(field) or "").strip())
        for field in _SELF_FIELDS
    }
    first_person = all(fields.values())
    layer = str(record.get("identity_layer") or "")
    origin_type = str(record.get("origin_type") or "")
    if layer == "founding" or origin_type in {"founding", "identity_anchor"}:
        classification = "founding"
        reasons = ["externally established identity anchor"]
    elif layer == "governance_history" or origin_type == "identity_revision":
        classification = "governance_history_candidate"
        reasons = ["identity governance or revision event"]
    elif (
        speakers == ["agent"]
        and verified
        and self_authored
        and first_person
        and authored_by == "stellar_companion"
    ):
        classification = "self_experience_candidate"
        reasons = ["agent evidence", "verified", "first-person fields complete", "stellar_companion author"]
    elif layer or origin_type:
        classification = "relationship_history_candidate" if "user" in speakers else "reject/unknown"
        reasons = ["identity source lacks complete self-authored evidence"]
    else:
        classification = "ordinary_memory"
        reasons = ["no identity source markers"]
    return {
        "memory_id": record.get("memory_id"),
        "identity_layer": record.get("identity_layer"),
        "origin_type": record.get("origin_type"),
        "speaker": speakers,
        "verified": verified,
        "self_authored_identity": self_authored,
        "authored_by": authored_by,
        "agency": metadata.get("agency") or merged.get("agency"),
        "self_fields": fields,
        "classification": classification,
        "reasons": reasons,
    }


def audit(db_path: Path) -> dict[str, object]:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT COALESCE(identity_layer, '<none>'), "
            "COALESCE(origin_type, '<none>'), COUNT(*) "
            "FROM compressed_memories GROUP BY identity_layer, origin_type "
            "ORDER BY identity_layer, origin_type"
        ).fetchall()
        identity_turn_count = connection.execute(
            "SELECT COUNT(*) FROM turns WHERE metadata LIKE '%identity_experience%'"
        ).fetchone()
        authored_rows = connection.execute(
            "SELECT COUNT(*) FROM turns WHERE metadata LIKE '%self_authored_identity%'"
        ).fetchone()
        raw_records = connection.execute(
            "SELECT memory_id, identity_layer, origin_type, origin_id, evidence_refs, "
            "source_turns, identity_metadata FROM compressed_memories "
            "WHERE identity_layer IS NOT NULL OR origin_type IS NOT NULL "
            "ORDER BY memory_id"
        ).fetchall()
        records = []
        for row in raw_records:
            record = dict(zip(
                ("memory_id", "identity_layer", "origin_type", "origin_id",
                 "evidence_refs", "source_turns", "identity_metadata"), row
            ))
            turn_ids = _turn_ids(record)
            linked_turn_rows = []
            if turn_ids:
                placeholders = ",".join("?" for _ in turn_ids)
                linked_turn_rows = [
                    dict(zip(("turn_id", "speaker", "metadata"), item))
                    for item in connection.execute(
                        f"SELECT turn_id, speaker, metadata FROM turns WHERE turn_id IN ({placeholders})",
                        turn_ids,
                    ).fetchall()
                ]
            records.append(classify_identity_record(record, linked_turn_rows))
    finally:
        connection.close()
    return {
        "db_path": str(db_path),
        "compressed_memory_sources": [
            {"identity_layer": row[0], "origin_type": row[1], "count": row[2]}
            for row in rows
        ],
        "turns_marked_identity_experience": int(
            identity_turn_count[0] if identity_turn_count else 0
        ),
        "turns_marked_self_authored_identity": int(
            authored_rows[0] if authored_rows else 0
        ),
        "identity_records": records,
        "classification_counts": {
            classification: sum(
                item["classification"] == classification for item in records
            )
            for classification in sorted({item["classification"] for item in records})
        },
        "migration_policy": {
            "automatic_migration": False,
            "allowed_candidates": ["self_experience_candidate"],
            "manual_review": [
                "relationship_history_candidate",
                "governance_history_candidate",
                "reject/unknown",
            ],
            "preserve_as_is": ["founding", "ordinary_memory"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=get_mem_runtime_layout().memory_db)
    args = parser.parse_args()
    print(json.dumps(audit(args.db), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
