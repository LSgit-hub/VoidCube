from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.memory.memory_service import (
    MemoryService,
    MemoryServiceConfig,
    SessionCreate,
    TurnCreate,
)


def _make_service(tmp_path: Path) -> MemoryService:
    cfg = MemoryServiceConfig(db_path=str(tmp_path / "mem.db"))
    return MemoryService(cfg)


# ── 4-6.1: memory_active must reflect real write work, not "a rule ran" ──

def test_rule_effective_count_across_return_shapes():
    f = MemoryService._rule_effective_count
    assert f(0) == 0
    assert f(7) == 7
    assert f(-3) == 0
    assert f({"escalated": 2, "purged": 3}) == 5
    assert f({"turns_processed": 4}) == 4
    assert f({"deleted": 6}) == 6
    assert f({"error": "boom"}) == 0
    assert f(None) == 0


@pytest.mark.asyncio
async def test_noop_cycle_does_not_stamp_effective_activity(tmp_path):
    # Empty DB → every rule is a no-op (0 rows). last_run advances, but the
    # effective-activity marker must stay None so the UI does not show active.
    svc = _make_service(tmp_path)
    result = await svc._run_all_rules_internal()
    assert result["_effective_work"] == 0
    assert svc._last_effective_activity_at is None
    # last_run still advances (the rule did execute)
    assert svc._last_rule_run.get("tier1_decay") is not None


@pytest.mark.asyncio
async def test_effective_activity_stamped_when_decay_writes_rows(tmp_path):
    # Seed a live turn so tier1_decay actually updates a row → effective work.
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="s1", metadata={}))
    await svc.add_turn("s1", TurnCreate(speaker="user", text="hello", metadata={}))

    result = await svc._run_all_rules_internal()
    assert result["_effective_work"] >= 1
    assert svc._last_effective_activity_at is not None


@pytest.mark.asyncio
async def test_rules_status_exposes_effective_activity_and_llm_check_marker(tmp_path):
    svc = _make_service(tmp_path)
    status = await svc.rules_status()
    assert "effective_activity_at" in status
    assert "llm_health_checked_at" in status
    assert "llm_healthy" in status
