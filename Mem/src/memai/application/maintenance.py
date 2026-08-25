"""Scoped maintenance cycles used by the Memory Service."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from time import monotonic
from typing import Any, Awaitable, Callable

from memai.repository.contracts import MemoryRepository
from memai.repository.sqlite import open_memory_sqlite
from memai.application.tier1_to_tier2_bridge import Tier1ToTier2Bridge


def _connect(db_path, repository: MemoryRepository | None):
    if repository is not None:
        return repository.connect()
    return open_memory_sqlite(db_path)


async def _execute_read(db_path, repository: MemoryRepository | None, operation):
    if repository is not None:
        return await repository.execute_read_async(operation)
    conn = _connect(db_path, repository)
    try:
        return operation(conn)
    finally:
        conn.close()


async def _execute_write(db_path, repository: MemoryRepository | None, operation):
    if repository is not None:
        return await repository.execute_write_async(operation)
    conn = _connect(db_path, repository)
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = operation(conn)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def run_tier1_decay_cycle(
    db_path, config: Any, *, now: datetime | None = None, logger: logging.Logger,
    repository: MemoryRepository | None = None,
) -> int:
    """Decay every uncompressed turn while preserving its full scope."""
    rate = float(config.tier1_decay_rate)
    interval_seconds = float(config.decay_interval_hours) * 3600.0
    if interval_seconds <= 0:
        raise ValueError("decay_interval_hours must be greater than zero")
    if not 0.0 <= rate <= 1.0:
        raise ValueError("tier1_decay_rate must be between zero and one")
    local_timezone = datetime.now().astimezone().tzinfo or timezone.utc
    reference_time = now or datetime.now().astimezone()
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=local_timezone)
    reference_utc, reference_iso = reference_time.astimezone(timezone.utc), reference_time.isoformat()
    updates: list[tuple[float, str, str, str, str, str]] = []
    def write(conn):
        rows = conn.execute(
            "SELECT turn_id, relevance_score, timestamp, last_decay_at, "
            "owner_id, workspace_id, memory_domain FROM turns "
            "WHERE compression_status != 'compressed'"
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
                "AND memory_domain = ? AND compression_status != 'compressed'",
                updates,
            )

    await _execute_write(db_path, repository, write)
    if updates:
        logger.debug(
            "Tier 1 decay applied to %d turns (rate=%.3f per %.1f hours)",
            len(updates), rate, config.decay_interval_hours,
        )
    return len(updates)


async def run_tier2_bridge_cycle(
    db_path, config: Any, *, request_factory: Callable[..., Any],
    compress: Callable[[Any], Awaitable[dict[str, Any]]], maintenance_actor: Any,
    logger: logging.Logger, repository: MemoryRepository | None = None,
) -> dict[str, Any]:
    """Schedule independent Tier 1 to Tier 2 work for every active scope."""
    scopes = await _execute_read(
        db_path,
        repository,
        lambda conn: conn.execute(
            "SELECT DISTINCT memory_domain, owner_id, workspace_id "
            "FROM turns WHERE compression_status IN ('pending', 'retry_wait') "
            "GROUP BY memory_domain, owner_id, workspace_id "
            "ORDER BY memory_domain, owner_id, workspace_id",
        ).fetchall(),
    )
    processed = 0
    scope_results: list[dict[str, Any]] = []
    for domain, owner_id, workspace_id in scopes:
        if not domain or not owner_id or not workspace_id:
            logger.warning(
                "Skipping Tier 2 bridge scope with missing identity: "
                "domain=%r owner=%r workspace=%r",
                domain, owner_id, workspace_id,
            )
            continue
        bridge = Tier1ToTier2Bridge(
            db_path, retention_days=config.tier1_retention_days,
            max_turns=config.tier1_max_turns, memory_domain=str(domain),
            owner_id=str(owner_id), workspace_id=str(workspace_id),
            repository=repository,
        )
        candidates = bridge.candidate_health_snapshot()
        if candidates["eligible_count"] == 0:
            continue
        force_oldest = bool(candidates["force_oldest"])
        request = request_factory(
            retention_days=config.tier1_retention_days,
            batch_size=int(getattr(config, "tier2_batch_size", 25)),
            min_relevance=config.tier1_min_relevance, force_oldest=force_oldest,
            memory_actor=maintenance_actor, memory_domain=str(domain),
            owner_id=str(owner_id), workspace_id=str(workspace_id),
        )
        started = monotonic()
        timeout_seconds = float(config.tier2_scope_timeout_seconds)
        try:
            result = await asyncio.wait_for(
                compress(request), timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            elapsed = round(monotonic() - started, 3)
            timeout_message = (
                f"scope compression timed out after {timeout_seconds:g} seconds"
            )
            scope_results.append({
                "memory_domain": str(domain),
                "owner_id": str(owner_id),
                "workspace_id": str(workspace_id),
                "status": "failed",
                "turns_processed": 0,
                "events_generated": 0,
                "elapsed_seconds": elapsed,
                "deadline_exceeded": True,
                "errors": [timeout_message],
            })
            logger.warning(
                "Tier 2 bridge scope timed out after %.3fs: "
                "domain=%s owner=%s workspace=%s",
                elapsed, domain, owner_id, workspace_id,
            )
            continue
        except Exception as exc:
            elapsed = round(monotonic() - started, 3)
            scope_results.append({
                "memory_domain": str(domain),
                "owner_id": str(owner_id),
                "workspace_id": str(workspace_id),
                "status": "failed",
                "turns_processed": 0,
                "events_generated": 0,
                "elapsed_seconds": elapsed,
                "deadline_exceeded": elapsed > timeout_seconds,
                "errors": [str(exc)],
            })
            logger.warning(
                "Tier 2 bridge scope failed: domain=%s owner=%s workspace=%s",
                domain, owner_id, workspace_id, exc_info=True,
            )
            continue
        scope_processed = int(result.get("turns_processed", 0) or 0)
        processed += scope_processed
        elapsed = round(monotonic() - started, 3)
        scope_results.append({
            "memory_domain": str(domain),
            "owner_id": str(owner_id),
            "workspace_id": str(workspace_id),
            "status": str(result.get("status") or "unknown"),
            "turns_processed": scope_processed,
            "events_generated": int(result.get("events_generated", 0) or 0),
            "elapsed_seconds": elapsed,
            "deadline_exceeded": elapsed > timeout_seconds,
            "errors": list(result.get("errors") or []),
            "quality_evidence": result.get("quality_evidence"),
            "eligible_candidates_before": candidates["eligible_count"],
            "oldest_candidate_at": candidates["oldest_candidate_at"],
        })
        if result.get("status") != "no_candidates":
            logger.info(
                "Tier 2 bridge scope: %s - %s turns -> %s events "
                "(domain=%s owner=%s workspace=%s)",
                result.get("status"), scope_processed, result.get("events_generated", 0),
                domain, owner_id, workspace_id,
            )
    return {
        "turns_processed": processed,
        "scope_count": len(scope_results),
        "failed_scope_count": sum(
            item["status"] in {"failed", "quality_rejected", "no_events_generated"}
            for item in scope_results
        ),
        "scopes": scope_results,
    }
