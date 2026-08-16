"""Persistent SessionSummary and DaySummary timeline index operations."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memai.domain.time_summary import (
    DaySessionSummary,
    DaySnapshotChanged,
    SessionSnapshotChanged,
    SessionTurn,
    TimeSummaryDraft,
    day_bucket_for_timestamp,
    day_period,
    day_source_hash,
    day_source_sort_key,
    json_list,
    session_source_hash,
    turn_sort_key,
)
from memai.repository.sqlite import open_memory_sqlite


def load_session_turns(
    connection,
    *,
    session_id: str,
    owner_id: str,
    workspace_id: str,
    memory_domain: str,
) -> tuple[SessionTurn, ...]:
    """Load one canonical turn stream, falling back to archived originals."""
    active_rows = connection.execute(
        "SELECT turn_id, speaker, text, timestamp FROM turns "
        "WHERE session_id = ? AND owner_id = ? AND workspace_id = ? "
        "AND memory_domain = ?",
        (session_id, owner_id, workspace_id, memory_domain),
    ).fetchall()
    turns = {
        str(row[0]): SessionTurn(
            turn_id=str(row[0]),
            speaker=str(row[1]),
            text=str(row[2]),
            timestamp=str(row[3]),
        )
        for row in active_rows
    }
    archive_rows = connection.execute(
        "SELECT turn_id, speaker, COALESCE(original_text, text_summary, ''), timestamp "
        "FROM turns_archive WHERE session_id = ? AND owner_id = ? AND workspace_id = ? "
        "AND memory_domain = ?",
        (session_id, owner_id, workspace_id, memory_domain),
    ).fetchall()
    for row in archive_rows:
        turn_id = str(row[0])
        turns.setdefault(
            turn_id,
            SessionTurn(
                turn_id=turn_id,
                speaker=str(row[1]),
                text=str(row[2] or ""),
                timestamp=str(row[3]),
            ),
        )
    return tuple(sorted(turns.values(), key=turn_sort_key))


def load_day_session_summaries(
    connection,
    *,
    day_key: str,
    owner_id: str,
    workspace_id: str,
    memory_domain: str,
    timezone_name: str,
) -> tuple[DaySessionSummary, ...]:
    """Load active SessionSummary nodes assigned by their local start date."""
    day_period(day_key, timezone_name=timezone_name)
    rows = connection.execute(
        "SELECT summary_id, bucket_key, period_start, period_end, title, summary, "
        "outcomes, open_questions, content_hash, version FROM time_summaries "
        "WHERE summary_type = 'session' AND status = 'active' AND owner_id = ? "
        "AND workspace_id = ? AND memory_domain = ?",
        (owner_id, workspace_id, memory_domain),
    ).fetchall()
    summaries = []
    for row in rows:
        if day_bucket_for_timestamp(
            str(row[2]),
            timezone_name=timezone_name,
        ) != day_key:
            continue
        summaries.append(
            DaySessionSummary(
                summary_id=str(row[0]),
                session_id=str(row[1]),
                period_start=str(row[2]),
                period_end=str(row[3]),
                title=str(row[4]),
                summary=str(row[5]),
                outcomes=tuple(json_list(row[6])),
                open_questions=tuple(json_list(row[7])),
                content_hash=str(row[8]),
                version=int(row[9]),
            )
        )
    return tuple(sorted(summaries, key=day_source_sort_key))


def get_active_session_summary(
    connection,
    *,
    session_id: str,
    owner_id: str,
    workspace_id: str,
    memory_domain: str,
) -> dict[str, Any] | None:
    return get_active_time_summary(
        connection,
        summary_type="session",
        bucket_key=session_id,
        owner_id=owner_id,
        workspace_id=workspace_id,
        memory_domain=memory_domain,
    )


def get_active_day_summary(
    connection,
    *,
    day_key: str,
    owner_id: str,
    workspace_id: str,
    memory_domain: str,
) -> dict[str, Any] | None:
    return get_active_time_summary(
        connection,
        summary_type="day",
        bucket_key=day_key,
        owner_id=owner_id,
        workspace_id=workspace_id,
        memory_domain=memory_domain,
    )


def get_active_time_summary(
    connection,
    *,
    summary_type: str,
    bucket_key: str,
    owner_id: str,
    workspace_id: str,
    memory_domain: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT summary_id, title, summary, outcomes, open_questions, source_count, "
        "source_hash, content_hash, version, status, supersedes_summary_id, "
        "period_start, period_end, timezone, created_at, updated_at "
        "FROM time_summaries WHERE summary_type = ? AND bucket_key = ? "
        "AND owner_id = ? AND workspace_id = ? AND memory_domain = ? "
        "AND status = 'active'",
        (summary_type, bucket_key, owner_id, workspace_id, memory_domain),
    ).fetchone()
    if not row:
        return None
    return {
        "summary_id": str(row[0]),
        "summary_type": summary_type,
        "bucket_key": bucket_key,
        "title": str(row[1]),
        "summary": str(row[2]),
        "outcomes": json_list(row[3]),
        "open_questions": json_list(row[4]),
        "source_count": int(row[5]),
        "source_hash": str(row[6]),
        "content_hash": str(row[7]),
        "version": int(row[8]),
        "status": str(row[9]),
        "supersedes_summary_id": row[10],
        "period_start": str(row[11]),
        "period_end": str(row[12]),
        "timezone": str(row[13]),
        "created_at": str(row[14]),
        "updated_at": str(row[15]),
        "owner_id": owner_id,
        "workspace_id": workspace_id,
        "memory_domain": memory_domain,
    }


def persist_session_summary(
    db_path: str | Path,
    *,
    session_id: str,
    owner_id: str,
    workspace_id: str,
    memory_domain: str,
    timezone_name: str,
    expected_source_hash: str,
    draft: TimeSummaryDraft,
) -> dict[str, Any]:
    """Atomically publish one immutable summary version for a stable snapshot."""
    connection = open_memory_sqlite(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        turns = load_session_turns(
            connection,
            session_id=session_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            memory_domain=memory_domain,
        )
        if not turns:
            raise ValueError("Cannot persist a session summary without source turns")
        actual_source_hash = session_source_hash(turns)
        if actual_source_hash != expected_source_hash:
            raise SessionSnapshotChanged(
                "Session turns changed while its summary was being generated"
            )
        current = get_active_session_summary(
            connection,
            session_id=session_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            memory_domain=memory_domain,
        )
        if current and current["source_hash"] == actual_source_hash:
            connection.commit()
            return {**current, "write_status": "current"}

        version = int(
            connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM time_summaries "
                "WHERE summary_type = 'session' AND bucket_key = ? AND owner_id = ? "
                "AND workspace_id = ? AND memory_domain = ?",
                (session_id, owner_id, workspace_id, memory_domain),
            ).fetchone()[0]
        )
        now = datetime.now(timezone.utc).isoformat()
        previous_id = str(current["summary_id"]) if current else None
        if previous_id:
            connection.execute(
                "UPDATE time_summaries SET status = 'superseded', updated_at = ? "
                "WHERE summary_id = ?",
                (now, previous_id),
            )
        summary_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "voidcube-time-summary:"
                f"{owner_id}:{workspace_id}:{memory_domain}:session:"
                f"{session_id}:v{version}:{actual_source_hash}",
            )
        )
        connection.execute(
            "INSERT INTO time_summaries "
            "(summary_id, summary_type, owner_id, workspace_id, memory_domain, "
            "bucket_key, period_start, period_end, timezone, title, summary, outcomes, "
            "open_questions, source_count, source_hash, content_hash, version, status, "
            "supersedes_summary_id, created_at, updated_at) "
            "VALUES (?, 'session', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "'active', ?, ?, ?)",
            (
                summary_id,
                owner_id,
                workspace_id,
                memory_domain,
                session_id,
                turns[0].timestamp,
                turns[-1].timestamp,
                timezone_name,
                draft.title,
                draft.summary,
                json.dumps(draft.outcomes, ensure_ascii=False),
                json.dumps(draft.open_questions, ensure_ascii=False),
                len(turns),
                actual_source_hash,
                draft.content_hash,
                version,
                previous_id,
                now,
                now,
            ),
        )
        connection.executemany(
            "INSERT INTO session_summary_sources "
            "(summary_id, turn_id, ordinal, turn_timestamp, evidence_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    summary_id,
                    turn.turn_id,
                    ordinal,
                    turn.timestamp,
                    turn.evidence_hash,
                )
                for ordinal, turn in enumerate(turns)
            ],
        )
        connection.commit()
        stored = get_active_session_summary(
            connection,
            session_id=session_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            memory_domain=memory_domain,
        )
        if stored is None:
            raise RuntimeError("Session summary write did not produce an active version")
        return {**stored, "write_status": "created"}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def persist_day_summary(
    db_path: str | Path,
    *,
    day_key: str,
    owner_id: str,
    workspace_id: str,
    memory_domain: str,
    timezone_name: str,
    expected_source_hash: str,
    draft: TimeSummaryDraft,
) -> dict[str, Any]:
    """Atomically publish one DaySummary for a stable active-session snapshot."""
    connection = open_memory_sqlite(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        summaries = load_day_session_summaries(
            connection,
            day_key=day_key,
            owner_id=owner_id,
            workspace_id=workspace_id,
            memory_domain=memory_domain,
            timezone_name=timezone_name,
        )
        if not summaries:
            raise ValueError("Cannot persist a day summary without session summaries")
        actual_source_hash = day_source_hash(summaries)
        if actual_source_hash != expected_source_hash:
            raise DaySnapshotChanged(
                "Session summaries changed while their day summary was being generated"
            )
        current = get_active_day_summary(
            connection,
            day_key=day_key,
            owner_id=owner_id,
            workspace_id=workspace_id,
            memory_domain=memory_domain,
        )
        if current and current["source_hash"] == actual_source_hash:
            connection.commit()
            return {**current, "write_status": "current"}

        version = int(
            connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM time_summaries "
                "WHERE summary_type = 'day' AND bucket_key = ? AND owner_id = ? "
                "AND workspace_id = ? AND memory_domain = ?",
                (day_key, owner_id, workspace_id, memory_domain),
            ).fetchone()[0]
        )
        now = datetime.now(timezone.utc).isoformat()
        previous_id = str(current["summary_id"]) if current else None
        if previous_id:
            connection.execute(
                "UPDATE time_summaries SET status = 'superseded', updated_at = ? "
                "WHERE summary_id = ?",
                (now, previous_id),
            )
        period_start, period_end = day_period(
            day_key,
            timezone_name=timezone_name,
        )
        summary_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "voidcube-time-summary:"
                f"{owner_id}:{workspace_id}:{memory_domain}:day:"
                f"{day_key}:v{version}:{actual_source_hash}",
            )
        )
        connection.execute(
            "INSERT INTO time_summaries "
            "(summary_id, summary_type, owner_id, workspace_id, memory_domain, "
            "bucket_key, period_start, period_end, timezone, title, summary, outcomes, "
            "open_questions, source_count, source_hash, content_hash, version, status, "
            "supersedes_summary_id, created_at, updated_at) "
            "VALUES (?, 'day', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "'active', ?, ?, ?)",
            (
                summary_id,
                owner_id,
                workspace_id,
                memory_domain,
                day_key,
                period_start,
                period_end,
                timezone_name,
                draft.title,
                draft.summary,
                json.dumps(draft.outcomes, ensure_ascii=False),
                json.dumps(draft.open_questions, ensure_ascii=False),
                len(summaries),
                actual_source_hash,
                draft.content_hash,
                version,
                previous_id,
                now,
                now,
            ),
        )
        connection.executemany(
            "INSERT INTO time_summary_links "
            "(parent_summary_id, child_summary_id, created_at) VALUES (?, ?, ?)",
            [
                (summary_id, summary.summary_id, now)
                for summary in summaries
            ],
        )
        connection.commit()
        stored = get_active_day_summary(
            connection,
            day_key=day_key,
            owner_id=owner_id,
            workspace_id=workspace_id,
            memory_domain=memory_domain,
        )
        if stored is None:
            raise RuntimeError("Day summary write did not produce an active version")
        return {**stored, "write_status": "created"}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def supersede_empty_day_summary(
    db_path: str | Path,
    *,
    day_key: str,
    owner_id: str,
    workspace_id: str,
    memory_domain: str,
    timezone_name: str,
    expected_summary_id: str,
) -> dict[str, Any]:
    """Deactivate a stale day index after its last active child moves away."""
    connection = open_memory_sqlite(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        summaries = load_day_session_summaries(
            connection,
            day_key=day_key,
            owner_id=owner_id,
            workspace_id=workspace_id,
            memory_domain=memory_domain,
            timezone_name=timezone_name,
        )
        if summaries:
            raise DaySnapshotChanged(
                "Session summaries appeared while an empty day was being retired"
            )
        current = get_active_day_summary(
            connection,
            day_key=day_key,
            owner_id=owner_id,
            workspace_id=workspace_id,
            memory_domain=memory_domain,
        )
        if current is None:
            connection.commit()
            return {
                "summary_type": "day",
                "bucket_key": day_key,
                "write_status": "absent",
            }
        if current["summary_id"] != expected_summary_id:
            raise DaySnapshotChanged("The active day version changed before retirement")
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            "UPDATE time_summaries SET status = 'superseded', updated_at = ? "
            "WHERE summary_id = ?",
            (now, expected_summary_id),
        )
        connection.commit()
        return {
            **current,
            "status": "superseded",
            "updated_at": now,
            "write_status": "emptied",
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
