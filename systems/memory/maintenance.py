"""Scoped maintenance cycles used by the Memory Service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Awaitable, Callable

from systems.memory.database import open_memory_sqlite


async def run_tier1_decay_cycle(
    db_path, config: Any, *, now: datetime | None = None, logger: logging.Logger
) -> int:
    """Decay every uncompressed turn while preserving its full scope."""
    conn = open_memory_sqlite(db_path)
    rate = float(config.tier1_decay_rate)
    interval_seconds = float(config.decay_interval_hours) * 3600.0
    if interval_seconds <= 0:
        conn.close()
        raise ValueError("decay_interval_hours must be greater than zero")
    if not 0.0 <= rate <= 1.0:
        conn.close()
        raise ValueError("tier1_decay_rate must be between zero and one")
    local_timezone = datetime.now().astimezone().tzinfo or timezone.utc
    reference_time = now or datetime.now().astimezone()
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=local_timezone)
    reference_utc, reference_iso = reference_time.astimezone(timezone.utc), reference_time.isoformat()
    updates: list[tuple[float, str, str, str, str, str]] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT turn_id, relevance_score, timestamp, last_decay_at, "
            "owner_id, workspace_id, memory_domain FROM turns "
            "WHERE compressed_to_tier2 = 0"
        ).fetchall()
        for turn_id, score, timestamp, last_decay_at, owner_id, workspace_id, domain in rows:
            anchor_value = last_decay_at or timestamp
            try:
                anchor = datetime.fromisoformat(anchor_value)
            except (TypeError, ValueError):
                logger.warning(
                    "Skipping Tier 1 decay for turn %s: invalid decay anchor %r",
                    turn_id, anchor_value,
                )
                continue
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=local_timezone)
            elapsed_seconds = (reference_utc - anchor.astimezone(timezone.utc)).total_seconds()
            if elapsed_seconds > 0:
                updates.append((
                    float(score or 0.0) * rate ** (elapsed_seconds / interval_seconds),
                    reference_iso, turn_id, owner_id, workspace_id, domain,
                ))
        if updates:
            conn.executemany(
                "UPDATE turns SET relevance_score = ?, last_decay_at = ? "
                "WHERE turn_id = ? AND owner_id = ? AND workspace_id = ? "
                "AND memory_domain = ? AND compressed_to_tier2 = 0",
                updates,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if updates:
        logger.debug(
            "Tier 1 decay applied to %d turns (rate=%.3f per %.1f hours)",
            len(updates), rate, config.decay_interval_hours,
        )
    return len(updates)


async def run_tier2_bridge_cycle(
    db_path, config: Any, *, request_factory: Callable[..., Any],
    compress: Callable[[Any], Awaitable[dict[str, Any]]], maintenance_actor: Any,
    logger: logging.Logger,
) -> int:
    """Schedule independent Tier 1 to Tier 2 work for every active scope."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=config.tier1_retention_days)).isoformat()
    conn = open_memory_sqlite(db_path)
    try:
        scopes = conn.execute(
            "SELECT memory_domain, owner_id, workspace_id, COUNT(*), "
            "SUM(CASE WHEN timestamp < ? THEN 1 ELSE 0 END) "
            "FROM turns WHERE compressed_to_tier2 = 0 "
            "GROUP BY memory_domain, owner_id, workspace_id "
            "ORDER BY memory_domain, owner_id, workspace_id",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()
    processed = 0
    for domain, owner_id, workspace_id, active_count, expired_count in scopes:
        force_oldest = int(active_count or 0) >= config.tier1_max_turns
        if not force_oldest and int(expired_count or 0) == 0:
            continue
        request = request_factory(
            retention_days=config.tier1_retention_days, batch_size=50,
            min_relevance=config.tier1_min_relevance, force_oldest=force_oldest,
            memory_actor=maintenance_actor, memory_domain=str(domain),
            owner_id=str(owner_id), workspace_id=str(workspace_id),
        )
        try:
            result = await compress(request)
        except Exception:
            logger.warning(
                "Tier 2 bridge scope failed: domain=%s owner=%s workspace=%s",
                domain, owner_id, workspace_id, exc_info=True,
            )
            continue
        scope_processed = int(result.get("turns_processed", 0) or 0)
        processed += scope_processed
        if result.get("status") != "no_candidates":
            logger.info(
                "Tier 2 bridge scope: %s - %s turns -> %s events "
                "(domain=%s owner=%s workspace=%s)",
                result.get("status"), scope_processed, result.get("events_generated", 0),
                domain, owner_id, workspace_id,
            )
    return processed
