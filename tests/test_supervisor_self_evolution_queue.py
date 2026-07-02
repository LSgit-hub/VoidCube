from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import json
from unittest.mock import AsyncMock
from unittest.mock import patch
from fastapi import HTTPException

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.supervisor.supervisor import Supervisor, SupervisorConfig, SupervisorExecutionConfig
from systems.supervisor.endogenous_drive import (
    EndogenousTaskCandidate,
    EndogenousDriveEngine,
    DriveAdaptivePolicy,
    DrivePerceptionSnapshot,
    DriveReflection,
    DriveWorldModel,
)
from systems.config import load_config_from_env


def _make_supervisor_config(tmp_path: Path) -> SupervisorConfig:
    return SupervisorConfig(
        execution=SupervisorExecutionConfig(git_repo_path=str(tmp_path))
    )


def _make_supervisor(tmp_path: Path) -> Supervisor:
    return Supervisor(_make_supervisor_config(tmp_path))


def _seed_current_lm_reasoning_state(
    supervisor: Supervisor,
    state: dict,
) -> None:
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={"endogenous_drive_lm_task_generation_enabled": True}
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    supervisor._endogenous_drive_engine._latest_lm_task_generation_context = dict(state)
    supervisor._endogenous_drive_engine._llm_task_proposals = lambda **_: []  # type: ignore[method-assign]


class _FakeLLMClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _self_learning_outcome(
    title: str,
    status: str,
    *,
    quality_score: float,
    recorded_at: str | None = None,
    recorded_hour: int | None = None,
) -> dict:
    row = {
        "title": title,
        "event_type": "decision",
        "status": status,
        "task_family": "self_learning",
        "governance_task_type": "self_learning",
        "quality_score": quality_score,
    }
    if recorded_at is None and recorded_hour is not None:
        recorded_at = f"2026-06-28T{recorded_hour:02d}:00:00+00:00"
    if recorded_at:
        row["recorded_at"] = recorded_at
    return row


def _endogenous_idle_window_payload(
    *,
    quality_score: float = 0.46,
    completed_title: str = "Recent learning",
    completed_at: str = "2026-06-28T00:00:00+00:00",
    error_count: int = 0,
    uncertainty_count: int = 0,
    user_idle: int = 900,
    agent_idle: int = 900,
    memory_idle: int = 900,
    memory_planning: bool = True,
    memory_execution: bool = True,
    self_learning_planning: bool = True,
    self_learning_execution: bool = True,
    self_evolution_planning: bool = True,
    self_evolution_execution: bool = False,
) -> dict:
    return {
        "checks": {
            "has_user_idle": True,
            "has_agent_idle": True,
            "has_memory_idle": True,
            "in_execution_window": True,
        },
        "idle_seconds": {
            "user": user_idle,
            "agent": agent_idle,
            "memory": memory_idle,
        },
        "activity": {
            "active_sessions": 0,
            "counts": {
                "error_count": error_count,
                "uncertainty_high_count": uncertainty_count,
            },
        },
        "completed_learning_tasks": [
            {
                "title": completed_title,
                "quality_score": quality_score,
                "completed_at": completed_at,
            }
        ],
        "task_family_decisions": {
            "memory_maintenance": {
                "eligible_for_planning": memory_planning,
                "eligible_for_execution": memory_execution,
            },
            "self_learning": {
                "eligible_for_planning": self_learning_planning,
                "eligible_for_execution": self_learning_execution,
            },
            "general_self_evolution": {
                "eligible_for_planning": self_evolution_planning,
                "eligible_for_execution": self_evolution_execution,
            },
        },
        "governance_task_type_decisions": {
            "memory_maintenance": {
                "eligible_for_planning": memory_planning,
                "eligible_for_execution": memory_execution,
            },
            "self_learning": {
                "eligible_for_planning": self_learning_planning,
                "eligible_for_execution": self_learning_execution,
            },
            "self_evolution": {
                "eligible_for_planning": self_evolution_planning,
                "eligible_for_execution": self_evolution_execution,
            },
        },
    }


def _drive_cycle_failure_replay_evaluation(
    *,
    context: str,
    key: str,
    focus: str,
    self_regulation: dict | None = None,
) -> dict:
    user_mode, posture, constraint = context.split("|")
    if focus not in {"truthfulness", "queue_hygiene"}:
        raise AssertionError(f"Unsupported fake drive focus: {focus}")
    truthfulness_focus = focus == "truthfulness"
    candidate_kind = "truthfulness_review" if truthfulness_focus else "queue_hygiene_review"
    governance_type = "self_learning" if truthfulness_focus else "self_evolution"
    task_family = "self_learning" if truthfulness_focus else "general_self_evolution"
    execution_kind = None if truthfulness_focus else "general_self_evolution"
    need_type = "repair_truthfulness" if truthfulness_focus else "stabilize_memory_continuity"
    intent_type = "review_truthfulness_signals" if truthfulness_focus else "review_queue_hygiene"
    observation_target = "truthfulness" if truthfulness_focus else "queue_hygiene"
    deliberation = {
        "perception": {
            "user_mode": user_mode,
            "system_posture": posture,
            "active_sessions": 0,
            "correction_signals": 3 if truthfulness_focus else 0,
            "active_queue_count": 0,
        },
        "reflection": {
            "dominant_constraint": constraint,
            "source_evidence": [f"context_key={context}"],
        },
        "adaptive_policy": {
            "preferred_focus": focus,
            "candidate_budget": 1,
            "source_evidence": [f"context_key={context}"],
        },
        "needs": [
            {
                "need_type": need_type,
                "severity": 0.78,
                "confidence": 0.84,
                "rationale": f"{focus} need",
            }
        ],
        "intents": [
            {
                "intent_type": intent_type,
                "source_needs": [need_type],
                "output_channel": "task_candidate",
                "candidate_kind": candidate_kind,
                "rationale": f"{focus} intent",
            }
        ],
        "signals": [
            {
                "signal_type": "drive_posture_signal",
                "priority": 0.8,
                "message": f"{focus} posture",
                "rationale": f"{focus} posture",
                "payload": {
                    "preferred_focus": focus,
                    "candidate_budget": 1,
                },
            },
            {
                "signal_type": "observation_signal",
                "priority": 0.82,
                "message": f"{focus} observation",
                "rationale": f"{focus} observation",
                "payload": {"observation_target": observation_target},
            },
        ],
    }
    governance_channels = {
        "posture": deliberation["signals"][0],
        "observation_requests": [deliberation["signals"][1]],
        "governance_review_requests": [],
        "truthfulness_alerts": [],
        "autonomy_alignment_requests": [],
    }
    if truthfulness_focus:
        governance_channels["truthfulness_alerts"] = [
            {
                "signal_type": "truthfulness_alert",
                "priority": 0.82,
                "message": "truthfulness observation",
                "rationale": "truthfulness observation",
                "payload": {"observation_target": "truthfulness"},
            }
        ]
    else:
        governance_channels["governance_review_requests"] = [
            {
                "signal_type": "governance_review_suggestion",
                "priority": 0.7,
                "message": "queue hygiene review",
                "rationale": "queue context switched",
                "payload": {"queue_health": "review"},
            }
        ]
    return {
        "status": "evaluated",
        "idle_window": {
            "checks": {"in_execution_window": True},
            "task_family_decisions": {
                task_family: {"eligible_for_planning": True, "eligible_for_execution": True},
            },
            "governance_task_type_decisions": {
                governance_type: {"eligible_for_planning": True, "eligible_for_execution": True},
            },
        },
        "deliberation": deliberation,
        "drive_posture": deliberation["signals"][0],
        "governance_channels": governance_channels,
        "governance_event_stream": {"events": []},
        "self_regulation": dict(self_regulation or {}),
        "candidates": [
            {
                "title": f"{focus.title()} candidate",
                "summary": f"{focus} candidate summary",
                "rationale": f"{focus} candidate rationale",
                "source": "endogenous_drive",
                "priority": "high",
                "governance_task_type": governance_type,
                "task_family": task_family,
                "execution_kind": execution_kind,
                "metadata": {
                    "endogenous_drive_key": key,
                    "drive_judgement": deliberation,
                    "score_breakdown": {"candidate_kind": candidate_kind},
                },
                "evidence": {"endogenous_drive": {}},
                "constraints": {},
            }
        ],
    }


async def _plan_and_write_back_endogenous_cycle(
    supervisor: Supervisor,
    *,
    outcome_status: str,
    reason: str,
    allow_empty_candidates: bool = False,
) -> dict:
    evaluation = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    candidates = list(evaluation.get("candidates") or [])
    if not candidates:
        if allow_empty_candidates:
            evaluation["writeback_skipped"] = True
            return evaluation
        assert candidates

    planned = await supervisor.plan_self_evolution_task(candidates[0])
    task_id = planned["tasks"][0]["task_id"]

    if outcome_status == "deferred":
        await supervisor.decide_self_evolution_task(
            task_id,
            {"decision": "deferred", "reason": reason},
        )
    elif outcome_status == "completed":
        await supervisor.decide_self_evolution_task(
            task_id,
            {"decision": "approved", "reason": f"{reason}: approved"},
        )
        supervisor._update_task_status(  # type: ignore[attr-defined]
            task_id,
            status="running",
            reason=f"{reason}: running",
            actor="test",
            event_type="execution",
        )
        supervisor._update_task_status(  # type: ignore[attr-defined]
            task_id,
            status="completed",
            reason=reason,
            actor="test",
            event_type="execution",
        )
    elif outcome_status == "failed":
        await supervisor.decide_self_evolution_task(
            task_id,
            {"decision": "approved", "reason": f"{reason}: approved"},
        )
        supervisor._update_task_status(  # type: ignore[attr-defined]
            task_id,
            status="running",
            reason=f"{reason}: running",
            actor="test",
            event_type="execution",
        )
        supervisor._update_task_status(  # type: ignore[attr-defined]
            task_id,
            status="failed",
            reason=reason,
            actor="test",
            event_type="execution",
        )
    else:
        raise AssertionError(f"Unsupported outcome_status: {outcome_status}")

    evaluation["writeback_skipped"] = False
    return evaluation


@pytest.mark.asyncio
@pytest.mark.unit
async def test_planning_self_evolution_task_creates_planned_queue_entry(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    result = await supervisor.plan_self_evolution_task(
        {
            "title": "Evaluate memory compaction heuristics",
            "summary": "Review whether current memory compression thresholds should be adjusted.",
        }
    )

    assert result["status"] == "planned"
    assert result["count"] == 1
    task = result["tasks"][0]
    assert task["status"] == "planned"
    assert task["title"] == "Evaluate memory compaction heuristics"
    assert task["governance_task_type"] == "self_evolution"
    assert task["task_family"] == "general_self_evolution"
    assert task["execution_kind"] == "general_self_evolution"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_cycle_generates_value_backed_tasks_without_duplicate_queue_spam(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={"endogenous_drive_max_candidates": 10}
            )
        }
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "recent_errors": 1,
                    "high_uncertainty": 2,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]

    first = await supervisor._run_endogenous_drive_cycle()
    second = await supervisor._run_endogenous_drive_cycle()
    queued = await supervisor.list_self_evolution_tasks()
    timeline = supervisor._recent_supervisor_ui_activity(limit=10)

    assert first["status"] == "planned"
    assert first["planned"] == 3
    assert second["status"] == "idle"
    assert queued["count"] == 3
    tasks_by_key = {
        task["metadata"]["endogenous_drive_key"]: task for task in queued["tasks"]
    }
    assert "continuity:memory_maintenance_sweep" in tasks_by_key
    assert "truthfulness:review_correction_signals" in tasks_by_key
    assert "continuity:queue_hygiene_review" not in tasks_by_key
    assert any(task["governance_task_type"] == "self_learning" for task in queued["tasks"])
    scheduled_tokens = [task.get("scheduled_for") for task in queued["tasks"]]
    assert all(isinstance(token, str) and token for token in scheduled_tokens)
    assert len(set(scheduled_tokens)) == len(scheduled_tokens)
    memory_task = tasks_by_key["continuity:memory_maintenance_sweep"]
    assert memory_task["source"] == "endogenous_drive"
    assert memory_task["governance_task_type"] == "memory_maintenance"
    assert memory_task["task_family"] == "memory_maintenance"
    assert memory_task["execution_kind"] == "memory_maintenance"
    assert memory_task["evidence"]["endogenous_drive"]["core_values"] == ["continuity"]
    event_types = [event["event_type"] for event in timeline]
    assert "endogenous_drive_evaluated" not in event_types
    assert "endogenous_drive_planned" in event_types
    assert "endogenous_drive_idle" in event_types


@pytest.mark.asyncio
@pytest.mark.unit
async def test_drive_queue_task_summaries_include_constraints_for_runtime_cooldown_checks(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    await supervisor.plan_self_evolution_task(
        {
            "title": "Improve shell body after learning",
            "task_family": "body_upgrade",
            "execution_kind": "body_improvement",
            "constraints": {
                "target_slot_id": "slot-B",
                "worktree_path": str(tmp_path / ".body-slots" / "slot-B" / "worktree"),
            },
            "evidence": {
                "learning_quality_score": 88.0,
                "recent_learning_topics": ["Study scheduler backpressure"],
                "source": "history",
            },
        }
    )

    summaries = supervisor._drive_queue_task_summaries(limit=5)

    assert summaries[0]["constraints"]["target_slot_id"] == "slot-B"
    assert summaries[0]["evidence"]["learning_quality_score"] == 88.0
    assert summaries[0]["evidence"]["source"] == "history"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_evaluate_endogenous_drive_exposes_deliberation_report(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 2,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]

    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    deliberation = result["deliberation"]

    assert deliberation["perception"]["correction_signals"] == 3
    assert "reflection" in deliberation
    assert deliberation["reflection"]["autonomy_readiness"] >= 0
    assert "adaptive_policy" in deliberation
    assert deliberation["adaptive_policy"]["preferred_focus"]
    assert any(signal["signal_type"] == "drive_posture_signal" for signal in deliberation["signals"])
    assert any(need["need_type"] == "repair_truthfulness" for need in deliberation["needs"])
    assert any(intent["candidate_kind"] == "truthfulness_review" for intent in deliberation["intents"])
    assert any(signal["signal_type"] == "observation_signal" for signal in deliberation["signals"])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_evaluation_persists_judgement_history(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 2,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    history = supervisor._load_endogenous_drive_history()

    assert history["judgements"]
    candidate = result["candidates"][0]
    assert candidate["metadata"]["endogenous_judgement_id"]
    assert candidate["metadata"]["endogenous_evaluation_id"]
    assert candidate["metadata"]["endogenous_preferred_focus"]
    assert "adaptive_policy" in candidate["metadata"]["drive_judgement"]
    assert history["judgements"][0]["judgement_id"] == candidate["metadata"]["endogenous_judgement_id"]
    assert history["judgements"][0]["preferred_focus"] == candidate["metadata"]["endogenous_preferred_focus"]
    assert history["judgements"][0]["context_key"] == candidate["metadata"]["endogenous_context_key"]
    assert history["strategy_memory"]["focus_stats"][candidate["metadata"]["endogenous_preferred_focus"]]["judged"] >= 1
    assert history["strategy_memory"]["contextual_focus_stats"][candidate["metadata"]["endogenous_context_key"]][candidate["metadata"]["endogenous_preferred_focus"]]["judged"] >= 1
    assert history["strategy_memory"]["agenda_topic_stats"]["stabilize_memory_continuity"]["seen"] >= 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_preview_evaluation_does_not_persist_runtime_state(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 2, "uncertainty_high_count": 1},
            },
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive(
        {"record_activity": False, "persist_evaluation": False}
    )
    history = supervisor._load_endogenous_drive_history()
    governance_events = supervisor._load_endogenous_governance_events()
    cognition_snapshot = supervisor._load_endogenous_cognition_state()

    assert result["candidates"]
    assert result["cognition_state"]["identity"]["role"] == "endogenous_supervisory_core"
    assert history["judgements"] == []
    assert history["strategy_memory"]["focus_stats"] == {}
    assert governance_events["events"] == []
    assert cognition_snapshot["state"]["status"] == "uninitialized"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_persistent_evaluation_rolls_back_history_when_later_persist_fails(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 2, "uncertainty_high_count": 1},
            },
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    def fail_cognition_persist(_state):
        raise RuntimeError("cognition persist failed")

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    supervisor._persist_endogenous_cognition_state = fail_cognition_persist  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="cognition persist failed"):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    history = supervisor._load_endogenous_drive_history()
    governance_events = supervisor._load_endogenous_governance_events()

    assert history["judgements"] == []
    assert history["strategy_memory"]["focus_stats"] == {}
    assert governance_events["events"] == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_evaluate_endogenous_drive_exposes_non_task_signals(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 4,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    await supervisor.plan_self_evolution_task(
        {
            "title": "Revisit deferred governance note",
            "task_family": "general_self_evolution",
            "execution_kind": "general_self_evolution",
        }
    )
    task_id = supervisor._self_evolution_queue.list_tasks()[0].task_id
    supervisor._self_evolution_queue.update_status(
        task_id,
        status="deferred",
        actor="test",
        reason="keep for later governance review",
    )

    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    signal_types = {signal["signal_type"] for signal in result["deliberation"]["signals"]}
    governance_channels = result["governance_channels"]
    governance_event_stream = result["governance_event_stream"]

    assert "observation_signal" in signal_types
    assert "governance_review_suggestion" in signal_types
    assert governance_channels["observation_requests"]
    assert governance_channels["governance_review_requests"]
    assert governance_event_stream["events"]
    assert any(event["event_type"] == "observation_request" for event in governance_event_stream["events"])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_preserves_truthfulness_channel_under_observe_before_acting(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 3,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": False,
                    "eligible_for_execution": False,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": False,
                    "eligible_for_execution": False,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]

    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    needs = list(result["deliberation"]["needs"])
    intents = list(result["deliberation"]["intents"])
    signals = list(result["deliberation"]["signals"])
    governance_channels = result["governance_channels"]

    assert any(need["need_type"] == "repair_truthfulness" for need in needs)
    truthfulness_intent = next(
        intent for intent in intents if intent["intent_type"] == "review_truthfulness_signals"
    )
    assert truthfulness_intent["output_channel"] == "task_candidate"
    assert truthfulness_intent["source_needs"] == ["repair_truthfulness"]
    assert any(need["need_type"] == "observe_before_acting" for need in needs)
    assert any(
        signal["signal_type"] == "observation_signal"
        and signal.get("related_intent") == "review_truthfulness_signals"
        and signal.get("source_needs") == ["repair_truthfulness"]
        and dict(signal.get("payload") or {}).get("observation_target") == "truthfulness"
        for signal in signals
    )
    assert governance_channels["truthfulness_alerts"]
    assert any(
        dict(item.get("payload") or {}).get("observation_target") == "truthfulness"
        for item in governance_channels["observation_requests"]
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_evaluate_endogenous_drive_exposes_cognition_state(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "model_role": "governance_reasoner",
            "proposal_count": 2,
            "charter": {
                "core_mission": "Use evidence and self-understanding to propose structured self-iteration work.",
                "task_generation_policy": [
                    "Prefer observation when evidence is weak.",
                ],
            },
            "task_type_priors": {
                "top_priority_task_type": "observation",
                "top_priority_score": 0.81,
                "priors": [
                    {
                        "task_type": "observation",
                        "score": 0.81,
                        "reasons": ["evidence gaps remain active"],
                    }
                ],
            },
            "proposal_drift_memory": {
                "available": True,
                "average_score": 0.46,
                "drift_state": "correcting",
                "quality_counts": {"strong": 0, "partial": 1, "weak": 1},
                "summary": "Recent proposal alignment is correcting.",
            },
            "recent_reference_alignment": {
                "available": True,
                "average_alignment_score": 0.62,
                "weak_or_partial_count": 2,
                "summary": "Reference alignment is not yet stable.",
            },
            "summary": "LM cognition status=completed.",
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 1200,
                "agent": 1200,
                "memory": 1200,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 4,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    cognition = result["cognition_state"]

    assert cognition["identity"]["role"] == "endogenous_supervisory_core"
    assert cognition["identity"]["execution_chain_coupled"] is False
    assert cognition["perception"]["user_mode"] == "idle_window"
    assert cognition["world_model"]["system_posture"] in {"strained", "degrading", "growth_window", "stable"}
    assert cognition["self_model"]["reflection"]["dominant_constraint"]
    assert cognition["self_model"]["adaptive_policy"]["preferred_focus"]
    assert "corrective_mode" in cognition["self_model"]
    assert cognition["governance"]["posture"]["signal_type"] == "drive_posture_signal"
    assert cognition["governance"]["channel_counts"]["observation_requests"] >= 1
    assert "candidate_count" not in cognition["governance"]
    assert "task_type_priors" not in cognition["proposal_cognition"]
    assert cognition["proposal_cognition"]["active_cognitive_posture_profile"]["name"]
    assert cognition["proposal_cognition"]["summary"] == (
        f"posture={cognition['proposal_cognition']['active_cognitive_posture_profile']['name']}; "
        f"drift={cognition['proposal_cognition']['auxiliary_memory']['proposal_drift_memory']['drift_state']}."
    )
    assert "current_count=" not in cognition["proposal_cognition"]["summary"]
    assert "summary" not in cognition["proposal_cognition"]["meta_cognition_profile"]
    auxiliary_memory = cognition["proposal_cognition"]["auxiliary_memory"]
    for folded_key in (
        "recent_reference_alignment",
        "proposal_drift_memory",
        "cognitive_evolution_trace",
        "self_iteration_hypotheses",
        "self_iteration_trend_memory",
        "switch_self_regulation_memory",
        "post_task_effect_memory",
        "cognitive_assessment_memory",
    ):
        assert folded_key not in cognition["proposal_cognition"]
        assert "summary" not in auxiliary_memory[folded_key]
    assert "secondary_task_shape_hint" not in cognition["proposal_cognition"]["meta_cognition_profile"]
    assert auxiliary_memory["proposal_drift_memory"]["drift_state"] == "correcting"
    assert cognition["proposal_cognition"]["lm_trace"]["charter_core_mission"]
    assert "top_priority_task_type" not in cognition["proposal_cognition"]["lm_trace"]
    assert cognition["proposal_cognition"]["current_candidates"]["count"] >= 1
    assert "entries" not in cognition["proposal_cognition"]["current_candidates"]
    assert "task_type_counts" not in cognition["proposal_cognition"]["current_candidates"]
    assert "quality_counts" not in cognition["proposal_cognition"]["current_candidates"]
    assert cognition["attention_agenda"]["active_count"] >= 1
    assert cognition["attention_agenda"]["entries"][0]["topic"]
    assert "candidate_kind" not in cognition["attention_agenda"]["entries"][0]
    assert "candidate_count" not in cognition["attention_agenda"]["entries"][0]
    assert cognition["uncertainty_ledger"]["active_count"] >= 1
    assert cognition["uncertainty_ledger"]["entries"][0]["hypothesis"]
    assert cognition["observation_program"]["active_count"] >= 1
    assert cognition["observation_program"]["entries"][0]["target"]
    assert cognition["meta_governance"]["mode"] in {"observe", "correct", "expand", "conserve"}
    assert cognition["meta_governance"]["confidence"] >= 0
    assert "entries" not in cognition["meta_governance"]
    assert "context" in cognition["meta_governance"]
    assert cognition["judgement_core"]["primary_need"]["need_type"]
    assert cognition["judgement_core"]["primary_intent"]["intent_type"]
    assert cognition["judgement_core"]["governance_outputs"]["preferred_focus"] == (
        cognition["self_model"]["adaptive_policy"]["preferred_focus"]
    )
    assert cognition["judgement_core"]["summary"]
    assert "primary_need=" in cognition["judgement_core"]["summary"]
    assert "focus=" in cognition["judgement_core"]["summary"]
    assert "agenda_top=" not in cognition["judgement_core"]["summary"]
    assert "focus_stats" in cognition["strategy_memory"]
    assert cognition["recent_events"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_evaluate_endogenous_drive_exposes_alignment_signal_when_reflection_detects_blockage(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {},
            },
            "completed_learning_tasks": [
                {
                    "title": "Low-yield learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.2,
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    await supervisor.plan_self_evolution_task(
        {
            "title": "Stale endogenous queue item",
            "task_family": "general_self_evolution",
            "execution_kind": "general_self_evolution",
            "source": "endogenous_drive",
            "metadata": {
                "endogenous_drive_key": "continuity:queue_hygiene_review",
            },
        }
    )
    task_id = supervisor._self_evolution_queue.list_tasks()[0].task_id
    supervisor._self_evolution_queue.update_status(
        task_id,
        status="deferred",
        actor="test",
        reason="still blocked",
    )

    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    signal_types = {signal["signal_type"] for signal in result["deliberation"]["signals"]}

    assert "autonomy_alignment_signal" in signal_types
    assert any(need["need_type"] == "observe_before_acting" for need in result["deliberation"]["needs"])
    assert result["governance_channels"]["autonomy_alignment_requests"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_history_records_planned_and_decision_outcomes(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 2,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    cycle = await supervisor._run_endogenous_drive_cycle()
    task_id = cycle["tasks"][0]["task_id"]

    await supervisor.decide_self_evolution_task(
        task_id,
        {
            "decision": "defer",
            "reason": "hold for later",
        },
    )
    history = supervisor._load_endogenous_drive_history()

    assert any(outcome["event_type"] == "planned" for outcome in history["outcomes"])
    assert any(
        outcome["task_id"] == task_id
        and outcome["event_type"] == "decision"
        and outcome["status"] == "deferred"
        for outcome in history["outcomes"]
    )
    assert any(outcome.get("preferred_focus") for outcome in history["outcomes"])
    preferred_focus = next(
        outcome["preferred_focus"]
        for outcome in history["outcomes"]
        if outcome.get("preferred_focus")
    )
    context_key = next(
        outcome["context_key"]
        for outcome in history["outcomes"]
        if outcome.get("context_key")
    )
    assert history["strategy_memory"]["focus_stats"][preferred_focus]["judged"] >= 1
    assert history["strategy_memory"]["focus_stats"][preferred_focus]["dragging"] == 1
    assert history["strategy_memory"]["contextual_focus_stats"][context_key][preferred_focus]["judged"] >= 1
    assert history["strategy_memory"]["contextual_focus_stats"][context_key][preferred_focus]["dragging"] == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_outcome_dedup_scans_full_retained_history_window(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return _endogenous_idle_window_payload(error_count=2, uncertainty_count=1)

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    cycle = await supervisor._run_endogenous_drive_cycle()
    task_id = cycle["tasks"][0]["task_id"]
    task = supervisor._self_evolution_queue.get_task(task_id)
    assert task is not None

    await supervisor.decide_self_evolution_task(
        task_id,
        {
            "decision": "defer",
            "reason": "hold for later",
        },
    )
    history = supervisor._load_endogenous_drive_history()
    deferred_outcome = next(
        outcome
        for outcome in history["outcomes"]
        if outcome.get("task_id") == task_id
        and outcome.get("event_type") == "decision"
        and outcome.get("status") == "deferred"
    )
    preferred_focus = str(deferred_outcome.get("preferred_focus") or "").strip()
    context_key = str(deferred_outcome.get("context_key") or "").strip()
    assert preferred_focus
    assert context_key

    filler = [
        {
            "outcome_id": f"filler-{index}",
            "recorded_at": f"2026-06-28T00:{index:02d}:00+00:00",
            "event_type": "decision",
            "task_id": f"filler-task-{index}",
            "decision_id": f"filler-decision-{index}",
            "status": "completed",
            "preferred_focus": "learning_expansion",
            "context_key": "idle_window|stable|none",
        }
        for index in range(30)
    ]
    history["outcomes"] = filler + [deferred_outcome]
    history["strategy_memory"]["focus_stats"][preferred_focus]["dragging"] = 1
    history["strategy_memory"]["contextual_focus_stats"][context_key][preferred_focus]["dragging"] = 1
    supervisor._persist_endogenous_drive_history(history)

    task = supervisor._self_evolution_queue.get_task(task_id)
    assert task is not None
    supervisor._record_endogenous_drive_outcome(task, event_type="decision")

    after = supervisor._load_endogenous_drive_history()
    matching = [
        outcome
        for outcome in after["outcomes"]
        if outcome.get("task_id") == task_id
        and outcome.get("event_type") == "decision"
        and outcome.get("status") == "deferred"
    ]

    assert len(matching) == 1
    assert after["strategy_memory"]["focus_stats"][preferred_focus]["dragging"] == 1
    assert (
        after["strategy_memory"]["contextual_focus_stats"][context_key][preferred_focus]["dragging"]
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_planned_outcomes_do_not_count_as_dragging_before_any_real_decision_or_execution(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 2,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    cycle = await supervisor._run_endogenous_drive_cycle()
    history = supervisor._load_endogenous_drive_history()

    assert cycle["tasks"]
    preferred_focus = next(
        outcome["preferred_focus"]
        for outcome in history["outcomes"]
        if outcome.get("preferred_focus")
    )
    context_key = next(
        outcome["context_key"]
        for outcome in history["outcomes"]
        if outcome.get("context_key")
    )
    assert history["strategy_memory"]["focus_stats"][preferred_focus]["judged"] >= 1
    assert history["strategy_memory"]["focus_stats"][preferred_focus]["dragging"] == 0
    assert history["strategy_memory"]["contextual_focus_stats"][context_key][preferred_focus]["dragging"] == 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_single_evaluation_counts_focus_judged_once_even_with_multiple_candidates(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 2,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    history = supervisor._load_endogenous_drive_history()

    assert len(result["candidates"]) >= 2
    preferred_focus = result["deliberation"]["adaptive_policy"]["preferred_focus"]
    context_key = next(
        outcome["context_key"]
        for outcome in history["judgements"]
        if outcome.get("context_key")
    )
    assert history["strategy_memory"]["focus_stats"][preferred_focus]["judged"] == 1
    assert history["strategy_memory"]["contextual_focus_stats"][context_key][preferred_focus]["judged"] == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_history_outcome_persists_reference_alignment(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    task = supervisor._self_evolution_queue.create_task(
        title="Audit evidence alignment",
        summary="Persist alignment details for the next endogenous cognition loop.",
        source="endogenous_drive",
        metadata={
            "endogenous_drive_key": "lm:truthfulness:review:audit-evidence-alignment",
            "reference_alignment": {
                "matched_evidence_nodes": ["external_research"],
                "missing_agenda_nodes": ["focus:truthfulness"],
                "alignment_score": 0.62,
                "alignment_quality": "partial",
            },
        },
        evidence={},
    )

    supervisor._record_endogenous_drive_outcome(task, event_type="planned")
    history = supervisor._load_endogenous_drive_history()
    recorded = next(
        outcome
        for outcome in history["outcomes"]
        if outcome["task_id"] == task.task_id and outcome["event_type"] == "planned"
    )

    assert recorded["reference_alignment"]["matched_evidence_nodes"] == ["external_research"]
    assert recorded["reference_alignment"]["missing_agenda_nodes"] == ["focus:truthfulness"]
    assert recorded["reference_alignment"]["alignment_quality"] == "partial"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_history_outcome_persists_cognitive_alignment(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    task = supervisor._self_evolution_queue.create_task(
        title="Audit proposal drift",
        summary="Persist cognitive alignment details for later self-correction.",
        source="endogenous_drive",
        metadata={
            "endogenous_drive_key": "lm:truthfulness:review:audit-proposal-drift",
            "cognitive_alignment": {
                "score": 0.41,
                "quality": "weak",
                "top_priority_task_type": "observation",
                "reasons": ["task_type_is_not_favored_by_current_program_priors"],
            },
        },
        evidence={},
    )

    supervisor._record_endogenous_drive_outcome(task, event_type="planned")
    history = supervisor._load_endogenous_drive_history()
    recorded = next(
        outcome
        for outcome in history["outcomes"]
        if outcome["task_id"] == task.task_id and outcome["event_type"] == "planned"
    )

    assert recorded["cognitive_alignment"]["quality"] == "weak"
    assert recorded["cognitive_alignment"]["top_priority_task_type"] == "observation"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_history_outcome_persists_lm_posture_reasoning(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    task = supervisor._self_evolution_queue.create_task(
        title="Record posture reasoning",
        summary="Persist LM posture alignment and priority basis for future cognition loops.",
        source="endogenous_drive",
        metadata={
            "endogenous_drive_key": "lm:truthfulness:review:record-posture-reasoning",
            "llm_posture_alignment": [
                "follows_truthfulness_first by prioritizing reference repair",
            ],
            "llm_priority_basis": [
                "recent correction signals are elevated",
            ],
        },
        evidence={},
    )

    supervisor._record_endogenous_drive_outcome(task, event_type="planned")
    history = supervisor._load_endogenous_drive_history()
    recorded = next(
        outcome
        for outcome in history["outcomes"]
        if outcome["task_id"] == task.task_id and outcome["event_type"] == "planned"
    )

    assert recorded["llm_posture_alignment"] == [
        "follows_truthfulness_first by prioritizing reference repair",
    ]
    assert recorded["llm_priority_basis"] == [
        "recent correction signals are elevated",
    ]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_history_outcome_persists_lm_cognitive_assessment(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    task = supervisor._self_evolution_queue.create_task(
        title="Record LM cognitive assessment",
        summary="Persist batch-level LM judgement for future endogenous cognition loops.",
        source="endogenous_drive",
        metadata={
            "endogenous_drive_key": "lm:truthfulness:review:record-cognitive-assessment",
            "llm_cognitive_assessment": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak self structure grounding",
                "primary_grounding_gaps": [
                    "missing_evidence:self_structure",
                ],
                "why_this_task_type_now": [
                    "review is the safest way to repair the evidence graph",
                ],
                "why_not_improvement_now": [
                    "improvement would run ahead of self-understanding",
                ],
            },
        },
        evidence={},
    )

    supervisor._record_endogenous_drive_outcome(task, event_type="planned")
    history = supervisor._load_endogenous_drive_history()
    recorded = next(
        outcome
        for outcome in history["outcomes"]
        if outcome["task_id"] == task.task_id and outcome["event_type"] == "planned"
    )

    assert recorded["llm_cognitive_assessment"]["current_judgement"] == (
        "review should dominate until grounding is repaired"
    )
    assert recorded["llm_cognitive_assessment"]["dominant_constraint"] == (
        "weak self structure grounding"
    )
    assert recorded["llm_cognitive_assessment"]["why_not_improvement_now"] == [
        "improvement would run ahead of self-understanding",
    ]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_endogenous_drive_cycle_exposes_drive_posture(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 4,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    cycle = await supervisor._run_endogenous_drive_cycle()

    assert cycle["drive_posture"]["signal_type"] == "drive_posture_signal"
    assert cycle["drive_posture"]["payload"]["preferred_focus"]
    assert "governance_channels" in cycle
    assert cycle["governance_channels"]["posture"]["signal_type"] == "drive_posture_signal"
    assert "governance_event_stream" in cycle
    assert cycle["governance_event_stream"]["events"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_governance_events_persist_to_runtime_file(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 4,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    events_path = supervisor._get_endogenous_governance_events_path()
    events_snapshot = supervisor._load_endogenous_governance_events()

    assert events_path.exists()
    assert result["governance_event_stream"]["events"]
    assert events_snapshot["events"]
    assert any(event["event_type"] == "drive_posture" for event in events_snapshot["events"])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_cognition_state_persists_to_runtime_file(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "model_role": "governance_reasoner",
            "proposal_count": 1,
            "charter": {
                "core_mission": "Evolve through evidence-backed structured proposals.",
            },
            "task_type_priors": {
                "top_priority_task_type": "review",
                "top_priority_score": 0.73,
                "priors": [
                    {"task_type": "review", "score": 0.73, "reasons": ["truthfulness pressure is elevated"]}
                ],
            },
            "proposal_drift_memory": {
                "available": True,
                "average_score": 0.58,
                "drift_state": "stable",
                "quality_counts": {"strong": 1, "partial": 0, "weak": 0},
                "summary": "Recent proposal alignment is stable.",
            },
            "recent_reference_alignment": {
                "available": True,
                "average_alignment_score": 0.78,
                "weak_or_partial_count": 0,
                "summary": "Reference alignment is stable.",
            },
            "summary": "LM cognition status=completed.",
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 3,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    path = supervisor._get_endogenous_cognition_state_path()
    snapshot = supervisor._load_endogenous_cognition_state()

    assert path.exists()
    assert snapshot["state"]["identity"]["role"] == "endogenous_supervisory_core"
    assert snapshot["state"]["governance"]["posture"]["signal_type"] == "drive_posture_signal"
    assert snapshot["state"]["attention_agenda"]["active_count"] >= 1
    assert snapshot["state"]["uncertainty_ledger"]["active_count"] >= 1
    assert snapshot["state"]["observation_program"]["active_count"] >= 1
    assert snapshot["state"]["meta_governance"]["mode"] in {"observe", "correct", "expand", "conserve"}
    assert snapshot["state"]["self_model"]["adaptive_policy"]["preferred_focus"] == result["cognition_state"]["self_model"]["adaptive_policy"]["preferred_focus"]
    assert "task_type_priors" not in snapshot["state"]["proposal_cognition"]
    assert "recent_reference_alignment" not in snapshot["state"]["proposal_cognition"]
    assert "recent_reference_alignment" in snapshot["state"]["proposal_cognition"]["auxiliary_memory"]
    assert "proposal_drift_memory" not in snapshot["state"]["proposal_cognition"]
    assert "proposal_drift_memory" in snapshot["state"]["proposal_cognition"]["auxiliary_memory"]
    assert "recent_cognitive_alignment" not in snapshot["state"]["proposal_cognition"]
    assert "recent_cognitive_alignment" in snapshot["state"]["proposal_cognition"]["auxiliary_memory"]
    assert "cognitive_evolution_trace" not in snapshot["state"]["proposal_cognition"]
    assert "cognitive_evolution_trace" in snapshot["state"]["proposal_cognition"]["auxiliary_memory"]
    assert snapshot["state"]["proposal_cognition"]["lm_trace"]["charter_core_mission"] == "Evolve through evidence-backed structured proposals."
    assert "top_priority_task_type" not in snapshot["state"]["proposal_cognition"]["lm_trace"]
    assert snapshot["state"]["proposal_cognition"]["current_candidates"]["count"] >= 1
    assert "task_type_counts" not in snapshot["state"]["proposal_cognition"]["current_candidates"]
    assert "quality_counts" not in snapshot["state"]["proposal_cognition"]["current_candidates"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognitive_self_regulation_tightens_adaptive_policy_when_lm_drift_and_weak_evidence_accumulate(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Drifting proposal one",
            "event_type": "planned",
            "status": "deferred",
            "cognitive_alignment": {
                "score": 0.31,
                "quality": "weak",
                "top_priority_task_type": "observation",
                "reasons": ["task_type_is_not_favored_by_current_program_priors"],
            },
        },
        {
            "title": "Drifting proposal two",
            "event_type": "planned",
            "status": "paused",
            "cognitive_alignment": {
                "score": 0.38,
                "quality": "weak",
                "top_priority_task_type": "review",
                "reasons": ["reference_alignment_is_weak"],
            },
        },
    ]
    supervisor._persist_endogenous_drive_history(history)
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "model_role": "governance_reasoner",
            "proposal_count": 2,
            "task_type_priors": {
                "top_priority_task_type": "observation",
                "top_priority_score": 0.84,
                "priors": [
                    {"task_type": "observation", "score": 0.84, "reasons": ["evidence gaps remain active"]},
                ],
            },
            "proposal_drift_memory": {
                "available": True,
                "average_score": 0.36,
                "drift_state": "drifting",
                "quality_counts": {"strong": 0, "partial": 0, "weak": 2},
                "summary": "Recent proposal alignment is drifting.",
            },
            "recent_reference_alignment": {
                "available": True,
                "average_alignment_score": 0.42,
                "weak_or_partial_count": 3,
                "summary": "Reference alignment is weak.",
            },
            "evidence_basis": {
                "self_iteration_readiness_score": 0.34,
                "self_understanding_gaps": [
                    "missing_external_research_support",
                    "reference_alignment_is_unstable",
                ],
                "weak_or_missing_channels": [
                    "external_research",
                    "shell_body_profile",
                ],
            },
            "summary": "LM cognition status=completed.",
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 1500,
                "agent": 1500,
                "memory": 1500,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 1,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    regulation = result["self_regulation"]
    adaptive_policy = result["deliberation"]["adaptive_policy"]
    proposal_cognition = result["cognition_state"]["proposal_cognition"]

    assert result["cognitive_self_regulation"]["dynamic_observation_bias_boost"] > 0.2
    assert result["cognitive_self_regulation"]["dynamic_candidate_throttle_boost"] > 0.2
    assert result["cognitive_self_regulation"]["dynamic_learning_expansion_suppression"] > 0.1
    assert regulation["dynamic_observation_bias_boost"] >= result["cognitive_self_regulation"]["dynamic_observation_bias_boost"]
    assert adaptive_policy["preferred_focus"] == "observation"
    assert adaptive_policy["candidate_budget"] <= 2
    assert "proposal_drift_is_active" in str(regulation["last_reason"] or "")
    assert "recent_cognitive_alignment" not in proposal_cognition
    assert (
        proposal_cognition["auxiliary_memory"]["recent_cognitive_alignment"]["average_score"]
        < 0.5
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognitive_self_regulation_stays_light_when_lm_alignment_and_evidence_are_strong(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Aligned proposal one",
            "event_type": "planned",
            "status": "completed",
            "cognitive_alignment": {
                "score": 0.83,
                "quality": "strong",
                "top_priority_task_type": "learning",
                "reasons": ["reference_alignment_is_strong"],
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "model_role": "governance_reasoner",
            "proposal_count": 1,
            "task_type_priors": {
                "top_priority_task_type": "learning",
                "top_priority_score": 0.78,
                "priors": [
                    {"task_type": "learning", "score": 0.78, "reasons": ["evidence quality is strong"]},
                ],
            },
            "proposal_drift_memory": {
                "available": True,
                "average_score": 0.82,
                "drift_state": "stable",
                "quality_counts": {"strong": 1, "partial": 0, "weak": 0},
                "summary": "Recent proposal alignment is stable.",
            },
            "recent_reference_alignment": {
                "available": True,
                "average_alignment_score": 0.86,
                "weak_or_partial_count": 0,
                "summary": "Reference alignment is strong.",
            },
            "evidence_basis": {
                "self_iteration_readiness_score": 0.81,
                "self_understanding_gaps": [],
                "weak_or_missing_channels": [],
            },
            "summary": "LM cognition status=completed.",
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 1200,
                "agent": 1200,
                "memory": 1200,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 0,
                    "uncertainty_high_count": 0,
                },
            },
            "completed_learning_tasks": [
                {
                    "title": "Strong learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.9,
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    adaptive_policy = result["deliberation"]["adaptive_policy"]
    cognitive_regulation = result["cognitive_self_regulation"]

    assert cognitive_regulation["dynamic_candidate_throttle_boost"] <= 0.01
    assert cognitive_regulation["dynamic_observation_bias_boost"] <= 0.01
    assert cognitive_regulation["dynamic_learning_expansion_suppression"] <= 0.01
    assert adaptive_policy["preferred_focus"] in {"learning_expansion", "body_growth", "memory_continuity"}
    assert adaptive_policy["candidate_budget"] >= 2


@pytest.mark.unit
def test_cognition_charter_control_policy_can_be_overridden_from_env(monkeypatch):
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_REFERENCE_ALIGNMENT_MIN_SCORE",
        "0.77",
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_WEAK_ALIGNMENT_THROTTLE_BOOST",
        "0.21",
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_WEAK_ALIGNMENT_COUNT_TRIGGER",
        "3",
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_AUTO_EXPLANATION_REPAIR_MISSING_THRESHOLD",
        "4",
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_EXPLANATION_INCONSISTENT_TRUTHFULNESS_BOOST",
        "0.19",
    )

    config = load_config_from_env()
    policy = (
        config.supervisor.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy
    )

    assert policy.reference_alignment_min_score == 0.77
    assert policy.weak_alignment_throttle_boost == 0.21
    assert policy.weak_alignment_count_trigger == 3
    assert policy.auto_explanation_repair_missing_threshold == 4
    assert policy.explanation_inconsistent_truthfulness_boost == 0.19


@pytest.mark.unit
def test_cognition_charter_context_layering_policy_can_be_overridden_from_env(monkeypatch):
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTEXT_DECISION_CORE_FIELDS",
        '["current_judgement","primary_evidence_nodes","decision_summary"]',
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTEXT_SUPPORTING_DETAIL_FIELDS",
        '["grounding_gaps","why_not_improvement_now"]',
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTEXT_LONG_TAIL_FIELDS",
        '["external_research_titles","long_tail_summary"]',
    )

    config = load_config_from_env()
    policy = (
        config.supervisor.service_runtime.endogenous_drive_cognition_charter.context_layering_policy
    )

    assert policy.decision_core_fields == [
        "current_judgement",
        "primary_evidence_nodes",
        "decision_summary",
    ]
    assert policy.supporting_detail_fields == [
        "grounding_gaps",
        "why_not_improvement_now",
    ]
    assert policy.long_tail_context_fields == [
        "external_research_titles",
        "long_tail_summary",
    ]


@pytest.mark.unit
def test_cognition_charter_prompt_attention_policy_can_be_overridden_from_env(monkeypatch):
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_PROMPT_ATTENTION_MAX_CHARS",
        "4200",
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_PROMPT_ATTENTION_PRIORITY_ORDER",
        '["decision_core","queue_state_snapshot","identity"]',
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_PROMPT_ATTENTION_STRUCTURE_KEYS",
        '["decision_core","queue_state_snapshot"]',
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_PROMPT_ATTENTION_TRIM_STAGE_ORDER",
        '["graph_compaction","primary_context_compaction"]',
    )

    config = load_config_from_env()
    policy = (
        config.supervisor.service_runtime.endogenous_drive_cognition_charter.prompt_attention_policy
    )

    assert policy.max_chars == 4200
    assert policy.priority_order == [
        "decision_core",
        "queue_state_snapshot",
        "identity",
    ]
    assert policy.structure_keys == [
        "decision_core",
        "queue_state_snapshot",
    ]
    assert policy.trim_stage_order == [
        "graph_compaction",
        "primary_context_compaction",
    ]


@pytest.mark.unit
def test_cognition_charter_evidence_attention_policy_can_be_overridden_from_env(monkeypatch):
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_EVIDENCE_ATTENTION_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_EVIDENCE_ATTENTION_CONFIDENCE_WEIGHT",
        "0.41",
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_EVIDENCE_ATTENTION_AGENDA_RELEVANCE_WEIGHT",
        "0.33",
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_EVIDENCE_ATTENTION_DECISION_CORE_TOPIC_LIMIT",
        "2",
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_EVIDENCE_ATTENTION_LONG_TAIL_ITEM_LIMIT",
        "1",
    )

    config = load_config_from_env()
    policy = (
        config.supervisor.service_runtime.endogenous_drive_cognition_charter.evidence_attention_policy
    )

    assert policy.enabled is True
    assert policy.confidence_weight == 0.41
    assert policy.agenda_relevance_weight == 0.33
    assert policy.decision_core_topic_limit == 2
    assert policy.long_tail_item_limit == 1


@pytest.mark.unit
def test_cognition_charter_cognitive_feedback_policy_can_be_overridden_from_env(monkeypatch):
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITIVE_FEEDBACK_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITIVE_FEEDBACK_ADAPTATION_STRENGTH",
        "0.37",
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITIVE_FEEDBACK_CONFLICT_WEIGHT_STEP",
        "0.12",
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITIVE_FEEDBACK_SELF_RELEVANCE_WEIGHT_STEP",
        "0.09",
    )

    config = load_config_from_env()
    policy = (
        config.supervisor.service_runtime.endogenous_drive_cognition_charter.cognitive_feedback_policy
    )

    assert policy.enabled is True
    assert policy.adaptation_strength == 0.37
    assert policy.conflict_weight_step == 0.12
    assert policy.self_relevance_weight_step == 0.09


@pytest.mark.unit
def test_cognition_charter_cognitive_strategy_delta_policy_can_be_overridden_from_env(monkeypatch):
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITIVE_STRATEGY_DELTA_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITIVE_STRATEGY_DELTA_PROPOSAL_THRESHOLD",
        "0.07",
    )
    monkeypatch.setenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITIVE_STRATEGY_DELTA_MAX_RECOMMENDED_CHANGES",
        "4",
    )

    config = load_config_from_env()
    policy = (
        config.supervisor.service_runtime.endogenous_drive_cognition_charter.cognitive_strategy_delta_policy
    )

    assert policy.enabled is True
    assert policy.proposal_threshold == 0.07
    assert policy.max_recommended_changes == 4


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognitive_self_regulation_uses_charter_control_policy_thresholds(tmp_path):
    config = _make_supervisor_config(tmp_path)
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.reference_alignment_min_score = 0.9
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.weak_reference_observation_boost = 0.22
    supervisor = Supervisor(config)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Mostly aligned proposal",
            "event_type": "planned",
            "status": "completed",
            "cognitive_alignment": {
                "score": 0.74,
                "quality": "strong",
                "top_priority_task_type": "learning",
                "reasons": ["reference_alignment_is_strong"],
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "proposal_drift_memory": {
                "available": True,
                "average_score": 0.74,
                "drift_state": "stable",
                "quality_counts": {"strong": 1, "partial": 0, "weak": 0},
            },
            "recent_reference_alignment": {
                "available": True,
                "average_alignment_score": 0.84,
                "weak_or_partial_count": 0,
            },
            "evidence_basis": {
                "self_iteration_readiness_score": 0.8,
                "self_understanding_gaps": [],
                "weak_or_missing_channels": [],
            },
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 1200,
                "agent": 1200,
                "memory": 1200,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 0,
                    "uncertainty_high_count": 0,
                },
            },
            "completed_learning_tasks": [
                {
                    "title": "Strong learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.88,
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["cognitive_self_regulation"]["dynamic_observation_bias_boost"] >= 0.22
    assert result["cognition_state"]["proposal_cognition"]["cognitive_control_policy"]["reference_alignment_min_score"] == 0.9


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognitive_posture_profile_observe_first_amplifies_observation_bias(tmp_path):
    config = _make_supervisor_config(tmp_path)
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.posture_selection_mode = "manual"
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.active_posture_profile = "observe_first"
    supervisor = Supervisor(config)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Partial proposal",
            "event_type": "planned",
            "status": "deferred",
            "cognitive_alignment": {
                "score": 0.47,
                "quality": "partial",
                "top_priority_task_type": "observation",
                "reasons": ["reference_alignment_is_weak"],
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "proposal_drift_memory": {
                "available": True,
                "average_score": 0.46,
                "drift_state": "correcting",
                "quality_counts": {"strong": 0, "partial": 1, "weak": 0},
            },
            "recent_reference_alignment": {
                "available": True,
                "average_alignment_score": 0.6,
                "weak_or_partial_count": 1,
            },
            "evidence_basis": {
                "self_iteration_readiness_score": 0.5,
                "self_understanding_gaps": ["reference_alignment_is_unstable"],
                "weak_or_missing_channels": ["external_research"],
            },
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 1200,
                "agent": 1200,
                "memory": 1200,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 0,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["cognition_state"]["proposal_cognition"]["active_cognitive_posture_profile"]["name"] == "observe_first"
    assert result["cognitive_self_regulation"]["dynamic_observation_bias_boost"] > result["cognitive_self_regulation"]["dynamic_candidate_throttle_boost"]
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "observation"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognitive_posture_profile_truthfulness_first_amplifies_truthfulness_bias(tmp_path):
    config = _make_supervisor_config(tmp_path)
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.posture_selection_mode = "manual"
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.active_posture_profile = "truthfulness_first"
    supervisor = Supervisor(config)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Truthfulness issue proposal",
            "event_type": "planned",
            "status": "deferred",
            "cognitive_alignment": {
                "score": 0.51,
                "quality": "partial",
                "top_priority_task_type": "review",
                "reasons": ["reference_alignment_is_weak"],
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "proposal_drift_memory": {
                "available": True,
                "average_score": 0.52,
                "drift_state": "stable",
                "quality_counts": {"strong": 0, "partial": 1, "weak": 0},
            },
            "recent_reference_alignment": {
                "available": True,
                "average_alignment_score": 0.58,
                "weak_or_partial_count": 2,
            },
            "evidence_basis": {
                "self_iteration_readiness_score": 0.66,
                "self_understanding_gaps": [],
                "weak_or_missing_channels": ["external_research"],
            },
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 1200,
                "agent": 1200,
                "memory": 1200,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 2,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["cognition_state"]["proposal_cognition"]["active_cognitive_posture_profile"]["name"] == "truthfulness_first"
    assert result["cognitive_self_regulation"]["dynamic_truthfulness_bias_boost"] >= 0.08
    assert "reference_alignment_is_not_stable" in str(result["self_regulation"]["last_reason"] or "")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognitive_posture_profile_auto_switches_to_conservative_under_service_pressure(tmp_path):
    config = _make_supervisor_config(tmp_path)
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.posture_selection_mode = "auto"
    supervisor = Supervisor(config)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "proposal_drift_memory": {"available": True, "average_score": 0.78, "drift_state": "stable"},
            "recent_reference_alignment": {"available": True, "average_alignment_score": 0.82, "weak_or_partial_count": 0},
            "evidence_basis": {"self_iteration_readiness_score": 0.76, "self_understanding_gaps": [], "weak_or_missing_channels": []},
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 2,
                "counts": {
                    "error_count": 0,
                    "uncertainty_high_count": 0,
                },
            },
            "completed_learning_tasks": [
                {
                    "title": "Strong learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.9,
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    profile = result["cognition_state"]["proposal_cognition"]["active_cognitive_posture_profile"]
    assert profile["name"] == "conservative"
    assert profile["selection_reason"] == "service_pressure_requires_conservative_posture"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognitive_posture_profile_auto_switches_to_evidence_repair_first_when_evidence_pressure_rises(tmp_path):
    config = _make_supervisor_config(tmp_path)
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.posture_selection_mode = "auto"
    supervisor = Supervisor(config)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "proposal_drift_memory": {"available": True, "average_score": 0.62, "drift_state": "stable"},
            "recent_reference_alignment": {"available": True, "average_alignment_score": 0.55, "weak_or_partial_count": 3},
            "evidence_basis": {
                "self_iteration_readiness_score": 0.64,
                "self_understanding_gaps": ["reference_alignment_is_unstable"],
                "weak_or_missing_channels": ["external_research", "shell_body_profile", "recent_learning"],
            },
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 1000,
                "agent": 1000,
                "memory": 1000,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 1,
                    "uncertainty_high_count": 0,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    profile = result["cognition_state"]["proposal_cognition"]["active_cognitive_posture_profile"]
    assert profile["name"] == "evidence_repair_first"
    assert profile["selection_reason"] == "evidence_repair_pressure_is_elevated"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognitive_posture_profile_auto_switches_to_truthfulness_first_when_correction_signals_rise(tmp_path):
    config = _make_supervisor_config(tmp_path)
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.posture_selection_mode = "auto"
    supervisor = Supervisor(config)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "proposal_drift_memory": {"available": True, "average_score": 0.7, "drift_state": "stable"},
            "recent_reference_alignment": {"available": True, "average_alignment_score": 0.78, "weak_or_partial_count": 0},
            "evidence_basis": {"self_iteration_readiness_score": 0.7, "self_understanding_gaps": [], "weak_or_missing_channels": []},
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 3,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    profile = result["cognition_state"]["proposal_cognition"]["active_cognitive_posture_profile"]
    assert profile["name"] == "truthfulness_first"
    assert profile["selection_reason"] == "truthfulness_signals_are_elevated"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognitive_posture_profile_auto_switches_to_observe_first_when_explanation_memory_is_missing(tmp_path):
    config = _make_supervisor_config(tmp_path)
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.posture_selection_mode = "auto"
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.auto_explanation_repair_missing_threshold = 2
    supervisor = Supervisor(config)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "proposal_drift_memory": {
                "available": True,
                "average_score": 0.63,
                "drift_state": "stable",
                "quality_counts": {"strong": 1, "partial": 0, "weak": 0},
                "missing_posture_alignment_count": 2,
                "missing_priority_basis_count": 2,
                "posture_alignment_health": "missing",
                "priority_basis_health": "missing",
            },
            "recent_reference_alignment": {"available": True, "average_alignment_score": 0.82, "weak_or_partial_count": 0},
            "evidence_basis": {"self_iteration_readiness_score": 0.73, "self_understanding_gaps": [], "weak_or_missing_channels": []},
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {"error_count": 0, "uncertainty_high_count": 0}},
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    profile = result["cognition_state"]["proposal_cognition"]["active_cognitive_posture_profile"]
    assert profile["name"] == "observe_first"
    assert profile["selection_reason"] == "missing_explanation_memory_requires_observation"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognitive_posture_profile_auto_switches_to_truthfulness_first_when_explanation_conflicts_reference_truth(tmp_path):
    config = _make_supervisor_config(tmp_path)
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.posture_selection_mode = "auto"
    supervisor = Supervisor(config)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "proposal_drift_memory": {
                "available": True,
                "average_score": 0.58,
                "drift_state": "stable",
                "quality_counts": {"strong": 0, "partial": 1, "weak": 0},
                "missing_posture_alignment_count": 0,
                "missing_priority_basis_count": 0,
                "posture_alignment_health": "inconsistent",
                "priority_basis_health": "strong",
                "dominant_posture_conflict_reason": "reference_alignment_is_weak",
            },
            "recent_reference_alignment": {"available": True, "average_alignment_score": 0.77, "weak_or_partial_count": 0},
            "evidence_basis": {"self_iteration_readiness_score": 0.72, "self_understanding_gaps": [], "weak_or_missing_channels": []},
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {"error_count": 0, "uncertainty_high_count": 0}},
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    profile = result["cognition_state"]["proposal_cognition"]["active_cognitive_posture_profile"]
    assert profile["name"] == "truthfulness_first"
    assert profile["selection_reason"] == "explanation_conflict_requires_truthfulness_repair"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognitive_self_regulation_tightens_when_proposal_explanations_are_inconsistent(tmp_path):
    config = _make_supervisor_config(tmp_path)
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.posture_selection_mode = "manual"
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.active_posture_profile = "observe_first"
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.explanation_inconsistent_observation_boost = 0.11
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.explanation_inconsistent_truthfulness_boost = 0.09
    supervisor = Supervisor(config)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Conflicted explanation",
            "event_type": "planned",
            "status": "deferred",
            "cognitive_alignment": {
                "score": 0.44,
                "quality": "partial",
                "top_priority_task_type": "review",
                "reasons": ["task_shape_conflicts_with_current_cognitive_posture"],
            },
            "llm_posture_alignment": ["claims improvement is acceptable under observe_first"],
            "llm_priority_basis": ["prioritize action despite unresolved evidence"],
        }
    ]
    supervisor._persist_endogenous_drive_history(history)
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "proposal_drift_memory": {
                "available": True,
                "average_score": 0.44,
                "drift_state": "stable",
                "quality_counts": {"strong": 0, "partial": 1, "weak": 0},
                "missing_posture_alignment_count": 0,
                "missing_priority_basis_count": 0,
                "posture_alignment_health": "inconsistent",
                "priority_basis_health": "inconsistent",
                "dominant_posture_conflict_reason": "task_shape_conflicts_with_current_cognitive_posture",
            },
            "recent_reference_alignment": {
                "available": True,
                "average_alignment_score": 0.71,
                "weak_or_partial_count": 0,
            },
            "evidence_basis": {
                "self_iteration_readiness_score": 0.66,
                "self_understanding_gaps": [],
                "weak_or_missing_channels": [],
            },
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 1200, "agent": 1200, "memory": 1200},
            "activity": {"active_sessions": 0, "counts": {"error_count": 0, "uncertainty_high_count": 0}},
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    regulation = result["cognitive_self_regulation"]
    assert regulation["dynamic_observation_bias_boost"] >= 0.11
    assert regulation["dynamic_truthfulness_bias_boost"] >= 0.09
    assert "proposal_explanation_conflict:task_shape_conflicts_with_current_cognitive_posture" in str(
        regulation["last_reason"] or ""
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_self_evolution_cycle_consumes_governance_review_events(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    events_snapshot = supervisor._endogenous_governance_events_default()
    events_snapshot["events"] = [
        {
            "event_id": "evt-1",
            "event_type": "governance_review_request",
            "channel": "governance_review_requests",
            "recorded_at": "2026-06-28T00:00:00+00:00",
            "context_key": "idle_window|stable|none",
            "preferred_focus": "queue_hygiene",
            "priority": 0.7,
            "message": "Queue state suggests a governance review pass.",
            "rationale": "review debt exists",
            "payload": {"queue_health": "busy"},
        }
    ]
    supervisor._persist_endogenous_governance_events(events_snapshot)

    async def fake_review(request=None):
        return {"count": 0, "tasks": [], "decision": "approved", "reviewed_statuses": [], "idle_window": {}}

    supervisor.review_self_evolution_tasks = fake_review  # type: ignore[method-assign]
    supervisor._fetch_gateway_active_cli_executor = AsyncMock(return_value={})  # type: ignore[method-assign]

    result = await supervisor._run_self_evolution_cycle()
    updated_snapshot = supervisor._load_endogenous_governance_events()

    assert result["governance_consumption"]["count"] == 1
    assert result["governance_consumption"]["consumed"][0]["event_id"] == "evt-1"
    assert updated_snapshot["events"][0]["consumed_action"] == "trigger_review_pass"
    assert updated_snapshot["events"][0]["consumed_at"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_self_evolution_cycle_consumes_alignment_events_into_self_regulation(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    events_snapshot = supervisor._endogenous_governance_events_default()
    events_snapshot["events"] = [
        {
            "event_id": "evt-align-1",
            "event_type": "autonomy_alignment_request",
            "channel": "autonomy_alignment_requests",
            "recorded_at": "2026-06-28T00:00:00+00:00",
            "context_key": "idle_window|stable|weak_learning_yield",
            "preferred_focus": "observation",
            "priority": 0.8,
            "message": "Autonomous output should be aligned and throttled.",
            "rationale": "weak readiness",
            "payload": {"dominant_constraint": "weak_learning_yield"},
        }
    ]
    supervisor._persist_endogenous_governance_events(events_snapshot)

    async def fake_review(request=None):
        return {"count": 0, "tasks": [], "decision": "approved", "reviewed_statuses": [], "idle_window": {}}

    supervisor.review_self_evolution_tasks = fake_review  # type: ignore[method-assign]
    supervisor._fetch_gateway_active_cli_executor = AsyncMock(return_value={})  # type: ignore[method-assign]

    result = await supervisor._run_self_evolution_cycle()
    regulation = supervisor._load_endogenous_self_regulation()
    updated_events = supervisor._load_endogenous_governance_events()

    assert result["alignment_consumption"]["count"] == 1
    assert regulation["dynamic_candidate_throttle_boost"] > 0.0
    assert regulation["dynamic_observation_bias_boost"] > 0.0
    assert updated_events["events"][0]["consumed_action"] == "increase_self_regulation"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_duplicate_governance_event_ids_are_deduped_before_consumption(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    events_snapshot = supervisor._endogenous_governance_events_default()
    duplicated_event = {
        "event_id": "evt-align-dup",
        "event_type": "autonomy_alignment_request",
        "channel": "autonomy_alignment_requests",
        "recorded_at": "2026-06-28T00:00:00+00:00",
        "context_key": "idle_window|stable|weak_learning_yield",
        "preferred_focus": "observation",
        "priority": 0.8,
        "message": "Autonomous output should be aligned and throttled.",
        "rationale": "weak readiness",
        "payload": {"dominant_constraint": "weak_learning_yield"},
    }
    events_snapshot["events"] = [
        dict(duplicated_event),
        {
            **dict(duplicated_event),
            "recorded_at": "2026-06-28T00:01:00+00:00",
            "message": "Duplicate alignment event should not double-count.",
        },
    ]
    supervisor._persist_endogenous_governance_events(events_snapshot)

    async def fake_review(request=None):
        return {"count": 0, "tasks": [], "decision": "approved", "reviewed_statuses": [], "idle_window": {}}

    supervisor.review_self_evolution_tasks = fake_review  # type: ignore[method-assign]
    supervisor._fetch_gateway_active_cli_executor = AsyncMock(return_value={})  # type: ignore[method-assign]

    result = await supervisor._run_self_evolution_cycle()
    regulation = supervisor._load_endogenous_self_regulation()
    updated_events = supervisor._load_endogenous_governance_events()

    assert len(updated_events["events"]) == 1
    assert result["alignment_consumption"]["count"] == 1
    assert result["alignment_consumption"]["consumed"][0]["event_id"] == "evt-align-dup"
    assert regulation["dynamic_candidate_throttle_boost"] == 0.08
    assert regulation["dynamic_observation_bias_boost"] == 0.06
    assert updated_events["events"][0]["consumed_action"] == "increase_self_regulation"


@pytest.mark.unit
def test_governance_events_without_event_ids_are_preserved_during_semantic_trim(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    events_snapshot = supervisor._endogenous_governance_events_default()
    event_without_id = {
        "event_type": "autonomy_alignment_request",
        "channel": "autonomy_alignment_requests",
        "recorded_at": "2026-06-28T00:00:00+00:00",
        "context_key": "idle_window|stable|weak_learning_yield",
        "preferred_focus": "observation",
        "priority": 0.8,
        "message": "Autonomous output should be aligned and throttled.",
        "rationale": "weak readiness",
        "payload": {"dominant_constraint": "weak_learning_yield"},
    }
    events_snapshot["events"] = [
        dict(event_without_id),
        {
            **dict(event_without_id),
            "recorded_at": "2026-06-28T00:01:00+00:00",
        },
    ]

    supervisor._persist_endogenous_governance_events(events_snapshot)
    updated_events = supervisor._load_endogenous_governance_events()["events"]

    assert len(updated_events) == 2
    assert all("event_id" not in item for item in updated_events)


@pytest.mark.unit
def test_repeated_governance_event_generation_keeps_unconsumed_semantic_events_stable(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    deliberation = {
        "perception": {
            "user_mode": "idle_window",
            "system_posture": "strained",
        },
        "reflection": {
            "dominant_constraint": "none",
        },
        "adaptive_policy": {
            "preferred_focus": "truthfulness",
        },
    }
    governance_channels = {
        "truthfulness_alerts": [
            {
                "priority": 0.88,
                "message": "Truthfulness pressure should suppress expansion.",
                "rationale": "correction pressure",
                "payload": {"observation_target": "truthfulness"},
            }
        ],
        "autonomy_alignment_requests": [
            {
                "priority": 0.82,
                "message": "Alignment should tighten before autonomous work.",
                "rationale": "weak readiness",
                "payload": {"dominant_constraint": "weak_learning_yield"},
            }
        ],
        "posture": {
            "priority": 0.73,
            "message": "Drive posture should stay corrective.",
            "rationale": "truthfulness pressure is active",
            "payload": {"preferred_focus": "truthfulness"},
        },
    }

    supervisor._record_endogenous_governance_events(
        deliberation=deliberation,
        governance_channels=governance_channels,
        candidate_items=[],
    )
    first_events = supervisor._load_endogenous_governance_events()["events"]
    first_ids = {
        item.get("event_type"): item.get("event_id")
        for item in first_events
    }

    supervisor._record_endogenous_governance_events(
        deliberation=deliberation,
        governance_channels=governance_channels,
        candidate_items=[],
    )
    second_events = supervisor._load_endogenous_governance_events()["events"]
    second_ids = {
        item.get("event_type"): item.get("event_id")
        for item in second_events
    }

    assert len(first_events) == 3
    assert len(second_events) == 3
    assert second_ids == first_ids

    consumed_snapshot = supervisor._load_endogenous_governance_events()
    for row in consumed_snapshot["events"]:
        if row.get("event_type") in {
            "autonomy_alignment_request",
            "truthfulness_alert",
        }:
            row["consumed_at"] = "2026-06-28T00:10:00+00:00"
            row["consumed_action"] = "test_consumed"
    supervisor._persist_endogenous_governance_events(consumed_snapshot)

    supervisor._record_endogenous_governance_events(
        deliberation=deliberation,
        governance_channels=governance_channels,
        candidate_items=[],
    )
    final_events = supervisor._load_endogenous_governance_events()["events"]
    alignment_events = [
        item
        for item in final_events
        if item.get("event_type") == "autonomy_alignment_request"
    ]
    truthfulness_events = [
        item
        for item in final_events
        if item.get("event_type") == "truthfulness_alert"
    ]
    posture_events = [
        item
        for item in final_events
        if item.get("event_type") == "drive_posture"
    ]

    assert len(alignment_events) == 2
    assert len(truthfulness_events) == 2
    assert len(posture_events) == 1
    assert sum(1 for item in alignment_events if not item.get("consumed_at")) == 1
    assert sum(1 for item in truthfulness_events if not item.get("consumed_at")) == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_self_evolution_cycle_consumes_truthfulness_alerts_into_corrective_mode(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    events_snapshot = supervisor._endogenous_governance_events_default()
    events_snapshot["events"] = [
        {
            "event_id": "evt-truth-1",
            "event_type": "truthfulness_alert",
            "channel": "truthfulness_alerts",
            "recorded_at": "2026-06-28T00:00:00+00:00",
            "context_key": "idle_window|strained|none",
            "preferred_focus": "truthfulness",
            "priority": 0.85,
            "message": "Correction pressure is rising.",
            "rationale": "recent errors increased",
            "payload": {"observation_target": "truthfulness"},
        }
    ]
    supervisor._persist_endogenous_governance_events(events_snapshot)

    async def fake_review(request=None):
        return {"count": 0, "tasks": [], "decision": "approved", "reviewed_statuses": [], "idle_window": {}}

    supervisor.review_self_evolution_tasks = fake_review  # type: ignore[method-assign]
    supervisor._fetch_gateway_active_cli_executor = AsyncMock(return_value={})  # type: ignore[method-assign]

    result = await supervisor._run_self_evolution_cycle()
    regulation = supervisor._load_endogenous_self_regulation()
    updated_events = supervisor._load_endogenous_governance_events()

    assert result["truthfulness_consumption"]["count"] == 1
    assert regulation["dynamic_truthfulness_bias_boost"] > 0.0
    assert regulation["dynamic_learning_expansion_suppression"] > 0.0
    assert updated_events["events"][0]["consumed_action"] == "increase_truthfulness_correction"


@pytest.mark.unit
def test_repeated_alignment_events_accumulate_self_regulation_but_respect_configured_caps(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    events_snapshot = supervisor._endogenous_governance_events_default()
    events_snapshot["events"] = [
        {
            "event_id": f"evt-align-{idx}",
            "event_type": "autonomy_alignment_request",
            "channel": "autonomy_alignment_requests",
            "recorded_at": "2026-06-28T00:00:00+00:00",
            "context_key": "idle_window|stable|weak_learning_yield",
            "preferred_focus": "observation",
            "priority": 0.8,
            "message": f"Alignment warning {idx}",
            "rationale": "weak readiness",
            "payload": {"dominant_constraint": "weak_learning_yield"},
        }
        for idx in range(6)
    ]
    supervisor._persist_endogenous_governance_events(events_snapshot)

    result = supervisor._consume_endogenous_alignment_events()
    regulation = supervisor._load_endogenous_self_regulation()

    assert result["count"] == 6
    assert regulation["dynamic_candidate_throttle_boost"] == 0.35
    assert regulation["dynamic_observation_bias_boost"] == 0.30


@pytest.mark.unit
def test_repeated_truthfulness_alerts_accumulate_corrective_mode_but_respect_configured_caps(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    events_snapshot = supervisor._endogenous_governance_events_default()
    events_snapshot["events"] = [
        {
            "event_id": f"evt-truth-{idx}",
            "event_type": "truthfulness_alert",
            "channel": "truthfulness_alerts",
            "recorded_at": "2026-06-28T00:00:00+00:00",
            "context_key": "idle_window|strained|none",
            "preferred_focus": "truthfulness",
            "priority": 0.85,
            "message": f"Truthfulness warning {idx}",
            "rationale": "recent errors increased",
            "payload": {"observation_target": "truthfulness"},
        }
        for idx in range(6)
    ]
    supervisor._persist_endogenous_governance_events(events_snapshot)

    result = supervisor._consume_endogenous_truthfulness_alerts()
    regulation = supervisor._load_endogenous_self_regulation()

    assert result["count"] == 6
    assert regulation["dynamic_truthfulness_bias_boost"] == 0.30
    assert regulation["dynamic_learning_expansion_suppression"] == 0.25


@pytest.mark.unit
def test_loaded_self_regulation_decay_from_peak_releases_all_boosts_toward_rest_proportionally(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    snapshot = supervisor._endogenous_self_regulation_default()
    snapshot["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    snapshot["dynamic_candidate_throttle_boost"] = 0.35
    snapshot["dynamic_observation_bias_boost"] = 0.30
    snapshot["dynamic_truthfulness_bias_boost"] = 0.30
    snapshot["dynamic_learning_expansion_suppression"] = 0.25
    snapshot["last_reason"] = "peak corrective mode"
    supervisor._get_endogenous_self_regulation_path().write_text(
        json.dumps(snapshot),
        encoding="utf-8",
    )

    loaded = supervisor._load_endogenous_self_regulation()

    assert loaded["dynamic_candidate_throttle_boost"] == 0.175
    assert loaded["dynamic_observation_bias_boost"] == 0.15
    assert loaded["dynamic_truthfulness_bias_boost"] == 0.15
    assert loaded["dynamic_learning_expansion_suppression"] == 0.125


@pytest.mark.asyncio
@pytest.mark.unit
async def test_decayed_persistent_self_regulation_does_not_keep_runtime_stuck_in_maximally_guarded_posture(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    snapshot = supervisor._endogenous_self_regulation_default()
    snapshot["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    snapshot["dynamic_candidate_throttle_boost"] = 0.35
    snapshot["dynamic_observation_bias_boost"] = 0.30
    snapshot["dynamic_truthfulness_bias_boost"] = 0.30
    snapshot["dynamic_learning_expansion_suppression"] = 0.25
    snapshot["last_reason"] = "peak corrective mode"
    supervisor._get_endogenous_self_regulation_path().write_text(
        json.dumps(snapshot),
        encoding="utf-8",
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {"error_count": 0, "uncertainty_high_count": 0}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    adaptive_policy = result["deliberation"]["adaptive_policy"]

    assert adaptive_policy["candidate_budget"] >= 2
    assert adaptive_policy["preferred_focus"] != "observation"
    assert adaptive_policy["observation_bias"] < 0.75


@pytest.mark.unit
def test_endogenous_self_regulation_decays_when_loaded(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    snapshot = supervisor._endogenous_self_regulation_default()
    snapshot["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    snapshot["dynamic_candidate_throttle_boost"] = 0.2
    snapshot["dynamic_observation_bias_boost"] = 0.12
    snapshot["last_reason"] = "temporary alignment pressure"
    supervisor._get_endogenous_self_regulation_path().write_text(
        json.dumps(snapshot),
        encoding="utf-8",
    )

    loaded = supervisor._load_endogenous_self_regulation()

    assert 0.0 < loaded["dynamic_candidate_throttle_boost"] < 0.2
    assert 0.0 < loaded["dynamic_observation_bias_boost"] < 0.12
    assert loaded["last_reason"] == "temporary alignment pressure"


@pytest.mark.unit
def test_endogenous_self_regulation_can_decay_back_to_rest(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    snapshot = supervisor._endogenous_self_regulation_default()
    snapshot["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
    snapshot["dynamic_candidate_throttle_boost"] = 0.05
    snapshot["dynamic_observation_bias_boost"] = 0.04
    snapshot["last_reason"] = "old pressure"
    supervisor._get_endogenous_self_regulation_path().write_text(
        json.dumps(snapshot),
        encoding="utf-8",
    )

    loaded = supervisor._load_endogenous_self_regulation()

    assert loaded["dynamic_candidate_throttle_boost"] == 0.0
    assert loaded["dynamic_observation_bias_boost"] == 0.0
    assert loaded["last_reason"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_endogenous_governance_state_aggregates_cognition_events_and_regulation(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 4,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    await supervisor.evaluate_endogenous_drive({"record_activity": False})
    state = await supervisor.get_endogenous_governance_state()
    regulation_view = await supervisor.get_endogenous_self_regulation()
    events_view = await supervisor.get_endogenous_governance_events()
    cognition_view = await supervisor.get_endogenous_cognition_state()

    assert state["status"] == "ok"
    assert state["cognition_state"]["identity"]["role"] == "endogenous_supervisory_core"
    assert state["cognition_state"]["judgement_core"]["summary"]
    assert state["cognition_state"]["attention_agenda"]["entries"]
    assert state["cognition_state"]["uncertainty_ledger"]["entries"]
    assert state["cognition_state"]["observation_program"]["entries"]
    assert state["cognition_state"]["meta_governance"]["mode"] in {"observe", "correct", "expand", "conserve"}
    assert "agenda_topic_stats" in state["cognition_state"]["strategy_memory"]
    assert state["governance_event_stream"]["events"]
    assert "corrective_mode" in state
    assert regulation_view["corrective_mode"]["mode"] in {"rest", "guarded", "corrective"}
    assert events_view["governance_event_stream"]["events"]
    assert cognition_view["cognition_state"]["governance"]["posture"]["signal_type"] == "drive_posture_signal"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_ui_state_reads_wrapped_cognition_state_lm_trace(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={"endogenous_drive_lm_task_generation_enabled": True}
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    supervisor._latest_drive_candidate_snapshot = lambda: [{"title": "cached drive"}]  # type: ignore[method-assign]
    supervisor._fetch_tier1_stats = AsyncMock(return_value={})  # type: ignore[method-assign]
    supervisor._recent_supervisor_observation_timeline = AsyncMock(return_value=[])  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return _endogenous_idle_window_payload()

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    cognition_state = supervisor._endogenous_cognition_state_default()["state"]
    cognition_state["perception"] = {
        "system_posture": "strained",
        "active_queue_count": 3,
        "recent_errors": 1,
        "learning_quality": 0.42,
        "correction_signals": 2,
    }
    cognition_state["world_model"] = {
        "queue_health": "dragging",
        "memory_pressure": 0.3,
        "truthfulness_pressure": 0.7,
        "learning_momentum": 0.2,
    }
    cognition_state["proposal_cognition"]["lm_trace"] = {
        "available": True,
        "status": "completed",
        "model_role": "endogenous_drive_task_generation",
        "charter_core_mission": "Govern from evidence.",
        "proposal_count": 2,
    }
    cognition_state["uncertainty_ledger"] = {
        "recent_nodes": [
            {
                "node_id": "evidence:self_structure",
                "title": "Self structure",
                "summary": "Grounding still needs repair.",
            }
        ]
    }
    supervisor._persist_endogenous_cognition_state(cognition_state)

    ui_state = await supervisor.get_supervisor_ui_state()

    assert ui_state["lm_input"]["generation_enabled"] is True
    assert ui_state["lm_input"]["status"] == "completed"
    assert ui_state["lm_input"]["model_role"] == "endogenous_drive_task_generation"
    assert ui_state["lm_input"]["proposal_count"] == 2
    assert "prompt_estimate" not in ui_state["lm_input"]
    assert ui_state["lm_input"]["recent_evidence_nodes"][0]["node"] == "evidence:self_structure"
    assert ui_state["cognition"]["perception"]["system_posture"] == "strained"
    assert ui_state["cognition"]["world_model"]["queue_health"] == "dragging"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognition_state_attention_agenda_prioritizes_observe_before_acting_when_blocked(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {},
            },
            "completed_learning_tasks": [
                {
                    "title": "Low-yield learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.2,
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    await supervisor.plan_self_evolution_task(
        {
            "title": "Stale endogenous queue item",
            "task_family": "general_self_evolution",
            "execution_kind": "general_self_evolution",
            "source": "endogenous_drive",
            "metadata": {
                "endogenous_drive_key": "continuity:queue_hygiene_review",
            },
        }
    )
    task_id = supervisor._self_evolution_queue.list_tasks()[0].task_id
    supervisor._self_evolution_queue.update_status(
        task_id,
        status="deferred",
        actor="test",
        reason="still blocked",
    )

    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    agenda = result["cognition_state"]["attention_agenda"]

    assert agenda["entries"]
    assert agenda["entries"][0]["topic"] == "observe_before_acting"
    assert agenda["entries"][0]["observation_required"] is True
    assert agenda["entries"][0]["persistence_state"] in {"emerging", "persistent", "dragging"}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cognition_state_uncertainty_ledger_tracks_truthfulness_queue_and_learning_risks(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 4,
                    "uncertainty_high_count": 2,
                },
            },
            "completed_learning_tasks": [
                {
                    "title": "Low-yield learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.2,
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    ledger = result["cognition_state"]["uncertainty_ledger"]
    domains = {entry["domain"] for entry in ledger["entries"]}

    assert ledger["active_count"] >= 3
    assert "truthfulness" in domains
    assert "governance_backlog" in domains or ledger["highest_risk_domain"] == "truthfulness"
    assert "learning_yield" in domains


@pytest.mark.asyncio
@pytest.mark.unit
async def test_observation_program_is_generated_from_uncertainty_ledger_and_requests(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 4,
                    "uncertainty_high_count": 2,
                },
            },
            "completed_learning_tasks": [
                {
                    "title": "Low-yield learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.2,
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    observation_program = result["cognition_state"]["observation_program"]
    strategy_memory = result["cognition_state"]["strategy_memory"]
    targets = {entry["target"] for entry in observation_program["entries"]}

    assert observation_program["active_count"] >= 1
    assert observation_program["highest_priority_target"] in targets
    assert any(entry["linked_request_signal"] == "observation_signal" for entry in observation_program["entries"])
    assert "truthfulness" in targets or "learning_yield" in targets
    assert "observation_target_stats" in strategy_memory
    for target in targets:
        assert strategy_memory["current_observation_target_stats"][target]["recommended"] >= 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_observation_program_builds_cross_cycle_target_memory(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 4,
                    "uncertainty_high_count": 2,
                },
            },
            "completed_learning_tasks": [
                {
                    "title": "Low-yield learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.2,
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    await supervisor.evaluate_endogenous_drive({"record_activity": False})
    await supervisor.evaluate_endogenous_drive({"record_activity": False})
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    observation_program = result["cognition_state"]["observation_program"]
    memory = result["cognition_state"]["strategy_memory"]["observation_target_stats"]

    assert observation_program["entries"]
    top_target = observation_program["entries"][0]["target"]
    assert memory[top_target]["recommended"] >= 3
    assert observation_program["entries"][0]["persistence_state"] in {"persistent", "stalled"}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_repeated_observation_history_does_not_saturate_observation_bias_without_new_outcomes(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {"status": "deferred", "task_family": "self_learning", "title": "a"},
        {"status": "failed", "task_family": "self_learning", "title": "b"},
        {"status": "deferred", "task_family": "memory_maintenance", "title": "c"},
        {"status": "completed", "task_family": "self_learning", "title": "d"},
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 1,
                    "uncertainty_high_count": 0,
                },
            },
            "completed_learning_tasks": [
                {
                    "title": "Mixed learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.46,
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    first = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    second = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    first_policy = first["deliberation"]["adaptive_policy"]
    second_policy = second["deliberation"]["adaptive_policy"]

    assert first["deliberation"]["reflection"]["dominant_constraint"] == "historical_underdelivery"
    assert first_policy["preferred_focus"] == "observation"
    assert second_policy["preferred_focus"] == "observation"
    assert first["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"
    assert second["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"
    assert second_policy["observation_bias"] < 0.9
    assert second_policy["observation_bias"] <= first_policy["observation_bias"] + 0.12


@pytest.mark.asyncio
@pytest.mark.unit
async def test_strategy_memory_memory_focus_history_does_not_override_learning_primary_after_historical_underdelivery_clears(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
    ]
    history["strategy_memory"] = {
        "focus_stats": {
            "memory_continuity": {"judged": 12, "completed": 10, "failed": 0, "dragging": 0},
            "learning_expansion": {"judged": 8, "completed": 2, "failed": 3, "dragging": 3},
        },
        "contextual_focus_stats": {
            "idle_window|stable|none": {
                "memory_continuity": {"judged": 8, "completed": 7, "failed": 0, "dragging": 0},
                "learning_expansion": {"judged": 6, "completed": 1, "failed": 2, "dragging": 3},
            }
        },
    }
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {"error_count": 0, "uncertainty_high_count": 0}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "none"
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "memory_continuity"
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "expand_learning_frontier"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_strategy_memory_observation_history_does_not_reenter_observation_primary_after_historical_underdelivery_clears(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
    ]
    history["strategy_memory"] = {
        "focus_stats": {
            "observation": {"judged": 12, "completed": 10, "failed": 0, "dragging": 0},
            "learning_expansion": {"judged": 8, "completed": 5, "failed": 1, "dragging": 2},
        },
        "contextual_focus_stats": {
            "idle_window|stable|none": {
                "observation": {"judged": 9, "completed": 8, "failed": 0, "dragging": 0},
                "learning_expansion": {"judged": 6, "completed": 3, "failed": 1, "dragging": 2},
            }
        },
        "observation_target_stats": {
            "learning_yield": {
                "seen": 9,
                "recommended": 9,
                "resolved": 8,
                "stalled": 0,
                "last_priority": 0.7,
                "last_risk": 0.2,
                "last_status": "resolved",
                "last_context_key": "idle_window|stable|none",
            }
        },
    }
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {"error_count": 0, "uncertainty_high_count": 0}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "none"
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] != "observation"
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "expand_learning_frontier"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_contextual_focus_history_does_not_leak_stable_truthfulness_bias_into_degrading_queue_context(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
    ]
    history["strategy_memory"] = {
        "focus_stats": {
            "truthfulness": {"judged": 10, "completed": 8, "failed": 0, "dragging": 0},
            "queue_hygiene": {"judged": 10, "completed": 9, "failed": 0, "dragging": 0},
            "learning_expansion": {"judged": 8, "completed": 5, "failed": 1, "dragging": 2},
        },
        "contextual_focus_stats": {
            "idle_window|stable|none": {
                "truthfulness": {"judged": 9, "completed": 9, "failed": 0, "dragging": 0},
            },
            "idle_window|degrading|none": {
                "queue_hygiene": {"judged": 9, "completed": 9, "failed": 0, "dragging": 0},
                "memory_continuity": {"judged": 9, "completed": 9, "failed": 0, "dragging": 0},
            },
        },
    }
    supervisor._persist_endogenous_drive_history(history)

    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Queue debt probe",
            "summary": "probe",
        }
    )
    task_id = planned["tasks"][0]["task_id"]
    supervisor._self_evolution_queue.update_status(task_id, status="deferred", reason="probe")

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 0, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert "context_key=idle_window|degrading|none" in result["deliberation"]["adaptive_policy"]["source_evidence"]
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "truthfulness"
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "stabilize_memory_continuity"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.stability_core
async def test_contextual_focus_history_allows_strained_truthfulness_context_to_take_priority_when_truthfulness_threshold_is_real(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
    ]
    history["strategy_memory"] = {
        "focus_stats": {
            "truthfulness": {"judged": 10, "completed": 8, "failed": 0, "dragging": 0},
            "queue_hygiene": {"judged": 10, "completed": 9, "failed": 0, "dragging": 0},
            "learning_expansion": {"judged": 8, "completed": 5, "failed": 1, "dragging": 2},
        },
        "contextual_focus_stats": {
            "idle_window|stable|none": {
                "truthfulness": {"judged": 9, "completed": 9, "failed": 0, "dragging": 0},
            },
            "idle_window|strained|none": {
                "queue_hygiene": {"judged": 9, "completed": 9, "failed": 0, "dragging": 0},
            },
        },
    }
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 3, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert "context_key=idle_window|strained|none" in result["deliberation"]["adaptive_policy"]["source_evidence"]
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "repair_truthfulness"
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "truthfulness"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.stability_core
async def test_observe_first_posture_strategy_memory_and_persistent_self_regulation_do_not_keep_cleared_historical_window_stuck_in_observation(
    tmp_path,
):
    config = _make_supervisor_config(tmp_path)
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.posture_selection_mode = "manual"
    config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.active_posture_profile = "observe_first"
    supervisor = Supervisor(config)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
    ]
    history["strategy_memory"] = {
        "focus_stats": {
            "observation": {"judged": 10, "completed": 9, "failed": 0, "dragging": 0},
            "learning_expansion": {"judged": 8, "completed": 5, "failed": 1, "dragging": 2},
        },
        "contextual_focus_stats": {
            "idle_window|stable|none": {
                "observation": {"judged": 8, "completed": 8, "failed": 0, "dragging": 0},
            }
        },
    }
    supervisor._persist_endogenous_drive_history(history)

    regulation_snapshot = supervisor._endogenous_self_regulation_default()
    regulation_snapshot["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    regulation_snapshot["dynamic_candidate_throttle_boost"] = 0.2
    regulation_snapshot["dynamic_observation_bias_boost"] = 0.18
    regulation_snapshot["dynamic_learning_expansion_suppression"] = 0.14
    regulation_snapshot["last_reason"] = "carryover"
    supervisor._get_endogenous_self_regulation_path().write_text(
        json.dumps(regulation_snapshot),
        encoding="utf-8",
    )

    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "proposal_drift_memory": {
                "available": True,
                "average_score": 0.46,
                "drift_state": "correcting",
                "quality_counts": {"strong": 0, "partial": 1, "weak": 0},
            },
            "recent_reference_alignment": {
                "available": True,
                "average_alignment_score": 0.6,
                "weak_or_partial_count": 1,
            },
            "evidence_basis": {
                "self_iteration_readiness_score": 0.5,
                "self_understanding_gaps": ["reference_alignment_is_unstable"],
                "weak_or_missing_channels": ["external_research"],
            },
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 1200, "agent": 1200, "memory": 1200},
            "activity": {"active_sessions": 0, "counts": {"error_count": 0, "uncertainty_high_count": 1}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    adaptive_policy = result["deliberation"]["adaptive_policy"]

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "none"
    assert adaptive_policy["preferred_focus"] != "observation"
    assert adaptive_policy["observation_bias"] < 0.9
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "expand_learning_frontier"
    assert result["cognitive_self_regulation"]["dynamic_observation_bias_boost"] == 0.0
    assert result["cognitive_self_regulation"]["dynamic_candidate_throttle_boost"] == 0.0
    assert result["cognitive_self_regulation"]["dynamic_learning_expansion_suppression"] == 0.0
    assert result["self_regulation"]["dynamic_observation_bias_boost"] > 0.0
    assert result["self_regulation"]["dynamic_truthfulness_bias_boost"] > 0.0
    assert (
        "cleared_historical_window_releases_composite_observation_carryover"
        in str(result["cognitive_self_regulation"]["last_reason"] or "")
    )


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.stability_longcycle
async def test_multicycle_continuity_writeback_does_not_block_truthfulness_takeover_when_review_threshold_becomes_real(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def stable_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {"error_count": 0, "uncertainty_high_count": 0}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = stable_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})
    wrote_back_cycles = 0
    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        for index in range(3):
            cycle = await _plan_and_write_back_endogenous_cycle(
                supervisor,
                outcome_status="completed",
                reason=f"continuity cycle {index + 1}",
                allow_empty_candidates=True,
            )
            assert cycle["deliberation"]["adaptive_policy"]["preferred_focus"] in {
                "memory_continuity",
                "observation",
                "truthfulness",
            }
            if not cycle.get("writeback_skipped"):
                wrote_back_cycles += 1

    history_after_cycles = supervisor._load_endogenous_drive_history()
    continuity_bucket = history_after_cycles["strategy_memory"]["focus_stats"].get("memory_continuity", {})
    assert wrote_back_cycles >= 2
    assert continuity_bucket.get("judged", 0) >= 1

    async def strained_truthfulness_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {"error_count": 3, "uncertainty_high_count": 1}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = strained_truthfulness_idle_window  # type: ignore[method-assign]
    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "truthfulness"
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "repair_truthfulness"
    assert result["deliberation"]["adaptive_policy"]["truthfulness_bias"] > result["deliberation"]["adaptive_policy"]["memory_continuity_bias"]


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.stability_longcycle
async def test_multicycle_memory_writeback_does_not_keep_learning_recovery_stuck_in_memory_primary(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Recovered self-learning A",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.72,
        },
        {
            "title": "Recovered self-learning B",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.77,
        },
    ]
    history["strategy_memory"] = {
        "focus_stats": {
            "memory_continuity": {"judged": 6, "completed": 6, "failed": 0, "dragging": 0},
            "learning_expansion": {"judged": 4, "completed": 1, "failed": 1, "dragging": 2},
        },
        "contextual_focus_stats": {
            "idle_window|stable|none": {
                "memory_continuity": {"judged": 5, "completed": 5, "failed": 0, "dragging": 0},
                "learning_expansion": {"judged": 3, "completed": 1, "failed": 0, "dragging": 2},
            }
        },
    }
    supervisor._persist_endogenous_drive_history(history)

    async def stable_memory_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {"error_count": 0, "uncertainty_high_count": 0}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.52,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = stable_memory_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})
    observed_focuses = []
    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        for index in range(2):
            cycle = await _plan_and_write_back_endogenous_cycle(
                supervisor,
                outcome_status="completed",
                reason=f"memory cycle {index + 1}",
            )
            observed_focuses.append(cycle["deliberation"]["adaptive_policy"]["preferred_focus"])

        recovery = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert "memory_continuity" in observed_focuses
    assert recovery["deliberation"]["reflection"]["dominant_constraint"] == "none"
    assert recovery["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "expand_learning_frontier"
    assert recovery["deliberation"]["adaptive_policy"]["preferred_focus"] == "memory_continuity"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.stability_longcycle
async def test_mixed_multicycle_writeback_and_context_switch_do_not_lock_primary_axis_to_stale_focus(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    async def stable_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {"error_count": 0, "uncertainty_high_count": 0}},
            "completed_learning_tasks": [
                {
                    "title": "Strong learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.88,
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    async def degrading_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {"error_count": 1, "uncertainty_high_count": 1}},
            "completed_learning_tasks": [
                {
                    "title": "Low learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.24,
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    async def strained_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {"error_count": 3, "uncertainty_high_count": 1}},
            "completed_learning_tasks": [
                {
                    "title": "Mixed learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.46,
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    sequence = [
        (stable_idle_window, "completed"),
        (stable_idle_window, "deferred"),
        (degrading_idle_window, "failed"),
        (strained_idle_window, "completed"),
        (stable_idle_window, "completed"),
        (degrading_idle_window, "failed"),
    ]

    observed_contexts: set[str] = set()
    observed_meta_modes: set[str] = set()
    observed_focuses: set[str] = set()

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        for idle_window_fn, outcome_status in sequence:
            supervisor.evaluate_idle_window = idle_window_fn  # type: ignore[method-assign]
            cycle = await _plan_and_write_back_endogenous_cycle(
                supervisor,
                outcome_status=outcome_status,
                reason=f"mixed cycle {outcome_status}",
                allow_empty_candidates=True,
            )
            if cycle.get("writeback_skipped"):
                continue
            observed_contexts.add(
                next(
                    item.split("=", 1)[1]
                    for item in cycle["deliberation"]["adaptive_policy"]["source_evidence"]
                    if item.startswith("context_key=")
                )
            )
            observed_meta_modes.add(cycle["cognition_state"]["meta_governance"]["mode"])
            observed_focuses.add(cycle["deliberation"]["adaptive_policy"]["preferred_focus"])

        supervisor.evaluate_idle_window = strained_idle_window  # type: ignore[method-assign]
        final = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    history = supervisor._load_endogenous_drive_history()
    focus_stats = history["strategy_memory"]["focus_stats"]
    meta_stats = history["strategy_memory"]["meta_governance_stats"]
    observation_stats = history["strategy_memory"]["observation_target_stats"]

    assert len(observed_contexts) >= 3
    assert len(observed_meta_modes) >= 2
    assert len(observed_focuses) >= 2
    assert len(meta_stats) >= 2
    assert observation_stats
    assert any(bucket.get("seen", 0) >= 2 for bucket in observation_stats.values())
    assert any(bucket.get("judged", 0) >= 2 for bucket in focus_stats.values())
    assert final["deliberation"]["adaptive_policy"]["preferred_focus"] == "truthfulness"
    assert final["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "repair_truthfulness"
    assert final["cognition_state"]["meta_governance"]["mode"] in {"observe", "correct"}


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.stability_longcycle
async def test_accumulated_focus_context_and_observation_stats_do_not_block_learning_and_truthfulness_retake_and_can_resolve_stale_observation_targets(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Recovered self-learning A",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.72,
        },
        {
            "title": "Recovered self-learning B",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.77,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    def _build_idle_window(
        *,
        quality_score: float,
        error_count: int = 0,
        uncertainty_count: int = 0,
    ) -> dict:
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": error_count,
                    "uncertainty_high_count": uncertainty_count,
                },
            },
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": quality_score,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    async def stable_growth_idle_window(_request=None):
        return _build_idle_window(quality_score=0.88)

    async def degrading_idle_window(_request=None):
        return _build_idle_window(quality_score=0.2, error_count=1, uncertainty_count=1)

    async def strained_truthfulness_idle_window(_request=None):
        return _build_idle_window(quality_score=0.46, error_count=3, uncertainty_count=1)

    sequence = [
        (stable_growth_idle_window, "completed"),
        (degrading_idle_window, "failed"),
        (strained_truthfulness_idle_window, "completed"),
        (stable_growth_idle_window, "completed"),
        (degrading_idle_window, "failed"),
        (strained_truthfulness_idle_window, "completed"),
        (stable_growth_idle_window, "completed"),
        (degrading_idle_window, "failed"),
    ]

    wrote_back_cycles = 0
    observed_focuses: set[str] = set()
    observed_primary_needs: set[str] = set()

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        for idle_window_fn, outcome_status in sequence:
            supervisor.evaluate_idle_window = idle_window_fn  # type: ignore[method-assign]
            cycle = await _plan_and_write_back_endogenous_cycle(
                supervisor,
                outcome_status=outcome_status,
                reason=f"longcycle accumulation {outcome_status}",
                allow_empty_candidates=True,
            )
            observed_focuses.add(cycle["deliberation"]["adaptive_policy"]["preferred_focus"])
            observed_primary_needs.add(
                cycle["cognition_state"]["judgement_core"]["primary_need"]["need_type"]
            )
            if not cycle.get("writeback_skipped"):
                wrote_back_cycles += 1

    assert wrote_back_cycles >= 5
    assert "truthfulness" in observed_focuses
    assert "repair_truthfulness" in observed_primary_needs
    assert "expand_learning_frontier" in observed_primary_needs

    accumulated_history = supervisor._load_endogenous_drive_history()
    strategy_memory = accumulated_history["strategy_memory"]
    focus_stats = strategy_memory["focus_stats"]
    contextual_focus_stats = strategy_memory["contextual_focus_stats"]
    observation_stats = strategy_memory["observation_target_stats"]

    assert focus_stats.get("truthfulness", {}).get("judged", 0) >= 1
    assert len(focus_stats) >= 2
    assert len(contextual_focus_stats) >= 2
    assert observation_stats.get("truthfulness", {}).get("seen", 0) >= 1
    assert len(observation_stats) >= 2

    accumulated_regulation = supervisor._load_endogenous_self_regulation()

    def _make_replay_supervisor(name: str) -> Supervisor:
        replay_root = tmp_path / name
        replay_root.mkdir()
        replay = _make_supervisor(replay_root)
        replay._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
        replay._persist_endogenous_drive_history(json.loads(json.dumps(accumulated_history)))
        replay._get_endogenous_self_regulation_path().write_text(
            json.dumps(accumulated_regulation),
            encoding="utf-8",
        )
        return replay

    learning_supervisor = _make_replay_supervisor("learning_replay")
    truthfulness_supervisor = _make_replay_supervisor("truthfulness_replay")
    memory_supervisor = _make_replay_supervisor("memory_replay")

    async def recovered_learning_idle_window(_request=None):
        return _build_idle_window(quality_score=0.9)

    async def memory_queue_idle_window(_request=None):
        return _build_idle_window(quality_score=0.46)

    planned = await memory_supervisor.plan_self_evolution_task(
        {
            "title": "Queue debt probe",
            "summary": "probe",
        }
    )
    memory_task_id = planned["tasks"][0]["task_id"]
    memory_supervisor._self_evolution_queue.update_status(
        memory_task_id,
        status="deferred",
        reason="probe",
    )

    learning_supervisor.evaluate_idle_window = recovered_learning_idle_window  # type: ignore[method-assign]
    truthfulness_supervisor.evaluate_idle_window = strained_truthfulness_idle_window  # type: ignore[method-assign]
    memory_supervisor.evaluate_idle_window = memory_queue_idle_window  # type: ignore[method-assign]

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        learning_result = await learning_supervisor.evaluate_endogenous_drive({"record_activity": False})
        truthfulness_result = await truthfulness_supervisor.evaluate_endogenous_drive(
            {"record_activity": False}
        )
        memory_result = await memory_supervisor.evaluate_endogenous_drive({"record_activity": False})

    memory_history_after_replay = memory_supervisor._load_endogenous_drive_history()
    memory_observation_stats = memory_history_after_replay["strategy_memory"]["observation_target_stats"]

    assert learning_result["deliberation"]["reflection"]["dominant_constraint"] == "none"
    assert learning_result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == (
        "expand_learning_frontier"
    )
    assert learning_result["deliberation"]["adaptive_policy"]["preferred_focus"] in {
        "learning_expansion",
        "memory_continuity",
        "body_growth",
    }
    assert learning_result["deliberation"]["adaptive_policy"]["preferred_focus"] not in {
        "observation",
        "truthfulness",
    }

    assert truthfulness_result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == (
        "repair_truthfulness"
    )
    assert truthfulness_result["deliberation"]["adaptive_policy"]["preferred_focus"] == "truthfulness"
    assert (
        truthfulness_result["deliberation"]["adaptive_policy"]["truthfulness_bias"]
        > truthfulness_result["deliberation"]["adaptive_policy"]["memory_continuity_bias"]
    )

    assert memory_result["deliberation"]["reflection"]["dominant_constraint"] == "none"
    assert memory_result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == (
        "stabilize_memory_continuity"
    )
    assert memory_result["deliberation"]["adaptive_policy"]["preferred_focus"] == "memory_continuity"
    assert (
        memory_result["deliberation"]["adaptive_policy"]["memory_continuity_bias"]
        > memory_result["deliberation"]["adaptive_policy"]["truthfulness_bias"]
    )
    assert memory_observation_stats.get("truthfulness", {}).get("resolved", 0) >= 1
    assert memory_observation_stats.get("weak_learning_yield", {}).get("resolved", 0) >= 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_meta_governance_switches_toward_observe_when_uncertainty_pressure_rises(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 4,
                    "uncertainty_high_count": 2,
                },
            },
            "completed_learning_tasks": [
                {
                    "title": "Low-yield learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.2,
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    meta = result["cognition_state"]["meta_governance"]

    assert meta["mode"] in {"observe", "correct"}
    assert meta["confidence"] > 0.3
    assert meta["guardrails"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_meta_governance_switches_toward_expand_when_uncertainty_recovers(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 0,
                    "uncertainty_high_count": 0,
                },
            },
            "completed_learning_tasks": [
                {
                    "title": "Strong learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.9,
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    meta = result["cognition_state"]["meta_governance"]

    assert meta["mode"] in {"expand", "conserve"}
    assert meta["confidence"] > 0.2
    assert any(driver.startswith("agenda=") for driver in meta["drivers"])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_meta_governance_uses_recent_mode_history_to_reduce_flip_flop(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["strategy_memory"]["meta_governance_stats"] = {
        "observe": {
            "seen": 5,
            "active_cycles": 4,
            "resolved": 1,
            "stalled": 0,
            "last_priority": 0.78,
            "last_confidence": 0.74,
            "last_status": "active",
        }
    }
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 4,
                    "uncertainty_high_count": 2,
                },
            },
            "completed_learning_tasks": [
                {
                    "title": "Low-yield learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.2,
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    meta = result["cognition_state"]["meta_governance"]

    assert meta["mode"] == "observe"
    assert any("last_mode=observe" in item for item in meta["drivers"])
    assert meta["confidence"] >= 0.4


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.stability_core
async def test_meta_governance_persists_mode_history_across_cycles(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 0,
                    "uncertainty_high_count": 0,
                },
            },
            "completed_learning_tasks": [
                {
                    "title": "Strong learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.9,
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    first = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    second = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    history = supervisor._load_endogenous_drive_history()

    meta = second["cognition_state"]["meta_governance"]
    stats = history["strategy_memory"]["meta_governance_stats"]

    assert meta["mode"] in {"expand", "conserve"}
    assert stats[meta["mode"]]["seen"] >= 2
    assert stats[meta["mode"]]["active_cycles"] >= 2
    assert first["cognition_state"]["meta_governance"]["mode"] == meta["mode"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_attention_agenda_builds_cross_cycle_persistence_memory(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 2,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    first = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    second = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    third = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    history = supervisor._load_endogenous_drive_history()

    memory_stats = history["strategy_memory"]["agenda_topic_stats"]["stabilize_memory_continuity"]
    agenda_entries = {
        entry["topic"]: entry for entry in third["cognition_state"]["attention_agenda"]["entries"]
    }
    memory_entry = agenda_entries["stabilize_memory_continuity"]

    assert first["cognition_state"]["attention_agenda"]["entries"]
    assert second["cognition_state"]["attention_agenda"]["entries"]
    assert memory_stats["seen"] >= 3
    assert memory_stats["active_cycles"] >= 3
    assert memory_entry["seen_count"] >= 2
    assert memory_entry["persistence_state"] in {"persistent", "dragging"}
    assert third["cognition_state"]["strategy_memory"]["agenda_topic_stats"]["stabilize_memory_continuity"]["seen"] >= 3


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_endogenous_drive_cycle_observation_posture_defers_non_stability_candidates(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_evaluate_endogenous_drive(_request=None):
        return {
            "status": "evaluated",
            "idle_window": {},
            "drive_posture": {
                "signal_type": "drive_posture_signal",
                "payload": {
                    "preferred_focus": "observation",
                    "candidate_budget": 1,
                },
            },
            "candidates": [
                {
                    "title": "Review correction signals",
                    "summary": "Stability-first task",
                    "source": "endogenous_drive",
                    "priority": "high",
                    "governance_task_type": "self_learning",
                    "task_family": "self_learning",
                    "execution_kind": None,
                    "metadata": {
                        "endogenous_drive_key": "truthfulness:review_correction_signals",
                        "score_breakdown": {"candidate_kind": "truthfulness_review"},
                    },
                    "evidence": {"endogenous_drive": {}},
                    "constraints": {},
                },
                {
                    "title": "Explore new learning direction",
                    "summary": "Expansion task",
                    "source": "endogenous_drive",
                    "priority": "normal",
                    "governance_task_type": "self_learning",
                    "task_family": "self_learning",
                    "execution_kind": None,
                    "metadata": {
                        "endogenous_drive_key": "creativity:idle_learning:test",
                        "score_breakdown": {"candidate_kind": "exploratory_learning"},
                    },
                    "evidence": {"endogenous_drive": {}},
                    "constraints": {},
                },
            ],
        }

    supervisor.evaluate_endogenous_drive = fake_evaluate_endogenous_drive  # type: ignore[method-assign]

    cycle = await supervisor._run_endogenous_drive_cycle()

    assert cycle["planned"] == 1
    assert cycle["tasks"][0]["metadata"]["endogenous_drive_key"] == "truthfulness:review_correction_signals"
    assert cycle["deferred_candidates"]
    assert cycle["deferred_candidates"][0]["candidate_kind"] == "exploratory_learning"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_endogenous_drive_cycle_only_judges_candidates_kept_after_runtime_gate(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    evaluation_requests = []

    def drive_judgement(preferred_focus: str) -> dict:
        return {
            "perception": {
                "user_mode": "idle_window",
                "system_posture": "stable",
            },
            "reflection": {
                "dominant_constraint": "historical_underdelivery",
            },
            "adaptive_policy": {
                "preferred_focus": preferred_focus,
            },
            "needs": [
                {
                    "need_type": f"{preferred_focus}_need",
                    "severity": 0.7,
                    "confidence": 0.8,
                }
            ],
        }

    async def fake_evaluate_endogenous_drive(request=None):
        evaluation_requests.append(dict(request or {}))
        deliberation = {
            "perception": {
                "user_mode": "idle_window",
                "system_posture": "stable",
            },
            "reflection": {
                "dominant_constraint": "historical_underdelivery",
            },
            "adaptive_policy": {
                "preferred_focus": "observation",
            },
            "signals": [
                {
                    "signal_type": "drive_posture_signal",
                    "payload": {
                        "preferred_focus": "observation",
                        "candidate_budget": 1,
                    },
                }
            ],
        }
        return {
            "status": "evaluated",
            "idle_window": {
                "task_family_decisions": {
                    "self_learning": {
                        "eligible_for_planning": True,
                        "eligible_for_execution": True,
                    },
                },
                "governance_task_type_decisions": {
                    "self_learning": {
                        "eligible_for_planning": True,
                        "eligible_for_execution": True,
                    },
                },
                "decisions": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
            },
            "deliberation": deliberation,
            "drive_posture": deliberation["signals"][0],
            "governance_channels": {},
            "governance_event_stream": {"events": []},
            "self_regulation": {},
            "candidates": [
                {
                    "title": "Review correction signals",
                    "summary": "Stability-first task",
                    "source": "endogenous_drive",
                    "priority": "high",
                    "governance_task_type": "self_learning",
                    "task_family": "self_learning",
                    "execution_kind": None,
                    "metadata": {
                        "endogenous_drive_key": "truthfulness:review_correction_signals",
                        "drive_judgement": drive_judgement("truthfulness"),
                        "score_breakdown": {"candidate_kind": "truthfulness_review"},
                    },
                    "evidence": {"endogenous_drive": {}},
                    "constraints": {},
                },
                {
                    "title": "Explore new learning direction",
                    "summary": "Expansion task",
                    "source": "endogenous_drive",
                    "priority": "normal",
                    "governance_task_type": "self_learning",
                    "task_family": "self_learning",
                    "execution_kind": None,
                    "metadata": {
                        "endogenous_drive_key": "creativity:idle_learning:test",
                        "drive_judgement": drive_judgement("learning_expansion"),
                        "score_breakdown": {"candidate_kind": "exploratory_learning"},
                    },
                    "evidence": {"endogenous_drive": {}},
                    "constraints": {},
                },
            ],
        }

    supervisor.evaluate_endogenous_drive = fake_evaluate_endogenous_drive  # type: ignore[method-assign]

    cycle = await supervisor._run_endogenous_drive_cycle()
    history = supervisor._load_endogenous_drive_history()
    focus_stats = history["strategy_memory"]["focus_stats"]
    judgement_keys = {
        judgement.get("candidate_key")
        for judgement in history["judgements"]
    }

    assert evaluation_requests == [
        {"record_activity": False, "persist_evaluation": False}
    ]
    assert cycle["planned"] == 1
    assert focus_stats["truthfulness"]["judged"] == 1
    assert "learning_expansion" not in focus_stats
    assert "truthfulness:review_correction_signals" in judgement_keys
    assert "creativity:idle_learning:test" not in judgement_keys


@pytest.mark.asyncio
@pytest.mark.unit
async def test_governance_consumption_survives_drive_plan_failure_and_context_switch_replans_once(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    events_snapshot = supervisor._endogenous_governance_events_default()
    events_snapshot["events"] = [
        {
            "event_id": "evt-align-switch",
            "event_type": "autonomy_alignment_request",
            "channel": "autonomy_alignment_requests",
            "recorded_at": "2026-06-28T00:00:00+00:00",
            "context_key": "idle_window|stable|weak_learning_yield",
            "preferred_focus": "observation",
            "priority": 0.82,
            "message": "Alignment should tighten before autonomous work.",
            "rationale": "weak readiness",
            "payload": {"dominant_constraint": "weak_learning_yield"},
        },
        {
            "event_id": "evt-truth-switch",
            "event_type": "truthfulness_alert",
            "channel": "truthfulness_alerts",
            "recorded_at": "2026-06-28T00:01:00+00:00",
            "context_key": "idle_window|strained|none",
            "preferred_focus": "truthfulness",
            "priority": 0.88,
            "message": "Truthfulness pressure should suppress expansion.",
            "rationale": "correction pressure",
            "payload": {"observation_target": "truthfulness"},
        },
    ]
    supervisor._persist_endogenous_governance_events(events_snapshot)

    async def fake_review(_request=None):
        return {"count": 0, "tasks": [], "decision": "approved", "reviewed_statuses": [], "idle_window": {}}

    supervisor.review_self_evolution_tasks = fake_review  # type: ignore[method-assign]
    supervisor._fetch_gateway_active_cli_executor = AsyncMock(return_value={})  # type: ignore[method-assign]
    consumed = await supervisor._run_self_evolution_cycle()

    consumed_events = supervisor._load_endogenous_governance_events()["events"]
    consumed_actions = {item.get("event_id"): item.get("consumed_action") for item in consumed_events}
    assert consumed["alignment_consumption"]["count"] == 1
    assert consumed["truthfulness_consumption"]["count"] == 1
    assert consumed_actions["evt-align-switch"] == "increase_self_regulation"
    assert consumed_actions["evt-truth-switch"] == "increase_truthfulness_correction"
    assert supervisor._load_endogenous_self_regulation()["dynamic_truthfulness_bias_boost"] > 0.0

    current_self_regulation = supervisor._load_endogenous_self_regulation()
    failing_eval = _drive_cycle_failure_replay_evaluation(
        context="idle_window|stable|weak_learning_yield",
        key="continuity:queue_hygiene_review",
        focus="queue_hygiene",
        self_regulation=current_self_regulation,
    )
    successful_eval = _drive_cycle_failure_replay_evaluation(
        context="idle_window|strained|none",
        key="truthfulness:review_correction_signals",
        focus="truthfulness",
        self_regulation=current_self_regulation,
    )
    original_plan = supervisor.plan_self_evolution_task

    async def fake_evaluate_failed(_request=None):
        return failing_eval

    async def fail_plan(_request=None):
        raise RuntimeError("plan failed after drive persistence")

    supervisor.evaluate_endogenous_drive = fake_evaluate_failed  # type: ignore[method-assign]
    supervisor.plan_self_evolution_task = fail_plan  # type: ignore[method-assign]
    history_before_failure = supervisor._load_endogenous_drive_history()
    events_before_failure = supervisor._load_endogenous_governance_events()
    cognition_before_failure = supervisor._load_endogenous_cognition_state()

    with pytest.raises(RuntimeError, match="plan failed after drive persistence"):
        await supervisor._run_endogenous_drive_cycle()

    history_after_failure = supervisor._load_endogenous_drive_history()
    events_after_failure = supervisor._load_endogenous_governance_events()
    cognition_after_failure = supervisor._load_endogenous_cognition_state()
    assert history_after_failure["judgements"] == history_before_failure["judgements"]
    assert history_after_failure["outcomes"] == history_before_failure["outcomes"]
    assert history_after_failure["strategy_memory"]["focus_stats"] == (
        history_before_failure["strategy_memory"]["focus_stats"]
    )
    assert events_after_failure["events"] == events_before_failure["events"]
    assert cognition_after_failure["state"] == cognition_before_failure["state"]

    async def fake_evaluate_success(_request=None):
        return successful_eval

    supervisor.evaluate_endogenous_drive = fake_evaluate_success  # type: ignore[method-assign]
    supervisor.plan_self_evolution_task = original_plan  # type: ignore[method-assign]
    result = await supervisor._run_endogenous_drive_cycle()

    history = supervisor._load_endogenous_drive_history()
    events_after_success = supervisor._load_endogenous_governance_events()["events"]
    judgement_keys = [item.get("candidate_key") for item in history["judgements"]]

    assert result["planned"] == 1
    assert result["tasks"][0]["metadata"]["endogenous_drive_key"] == "truthfulness:review_correction_signals"
    assert judgement_keys.count("truthfulness:review_correction_signals") == 1
    assert "continuity:queue_hygiene_review" not in judgement_keys
    assert history["strategy_memory"]["focus_stats"]["truthfulness"]["judged"] == 1
    assert "queue_hygiene" not in history["strategy_memory"]["focus_stats"]
    assert any(item.get("event_id") == "evt-align-switch" for item in events_after_success)
    assert any(item.get("event_id") == "evt-truth-switch" for item in events_after_success)
    assert any(item.get("event_type") == "truthfulness_alert" for item in events_after_success)


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.stability_longcycle
async def test_repeated_governance_consumption_drive_failure_and_context_switch_remain_stable(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_review(_request=None):
        return {"count": 0, "tasks": [], "decision": "approved", "reviewed_statuses": [], "idle_window": {}}

    supervisor.review_self_evolution_tasks = fake_review  # type: ignore[method-assign]
    supervisor._fetch_gateway_active_cli_executor = AsyncMock(return_value={})  # type: ignore[method-assign]

    first_events = supervisor._endogenous_governance_events_default()
    first_events["events"] = [
        {
            "event_id": "evt-align-cycle-a",
            "event_type": "autonomy_alignment_request",
            "channel": "autonomy_alignment_requests",
            "recorded_at": "2026-06-28T00:00:00+00:00",
            "context_key": "idle_window|stable|weak_learning_yield",
            "preferred_focus": "observation",
            "priority": 0.82,
            "message": "Alignment should tighten before autonomous work.",
            "rationale": "weak readiness",
            "payload": {"dominant_constraint": "weak_learning_yield"},
        },
        {
            "event_id": "evt-truth-cycle-a",
            "event_type": "truthfulness_alert",
            "channel": "truthfulness_alerts",
            "recorded_at": "2026-06-28T00:01:00+00:00",
            "context_key": "idle_window|strained|none",
            "preferred_focus": "truthfulness",
            "priority": 0.88,
            "message": "Truthfulness pressure should suppress expansion.",
            "rationale": "correction pressure",
            "payload": {"observation_target": "truthfulness"},
        },
    ]
    supervisor._persist_endogenous_governance_events(first_events)

    first_consumed = await supervisor._run_self_evolution_cycle()
    events_after_first_consumption = supervisor._load_endogenous_governance_events()["events"]
    first_consumed_at = {
        item.get("event_id"): item.get("consumed_at")
        for item in events_after_first_consumption
    }
    assert first_consumed["alignment_consumption"]["count"] == 1
    assert first_consumed["truthfulness_consumption"]["count"] == 1
    assert first_consumed_at["evt-align-cycle-a"]
    assert first_consumed_at["evt-truth-cycle-a"]

    current_self_regulation = supervisor._load_endogenous_self_regulation()
    first_failure_eval = _drive_cycle_failure_replay_evaluation(
        context="idle_window|stable|weak_learning_yield",
        key="continuity:queue_hygiene_failed_cycle_a",
        focus="queue_hygiene",
        self_regulation=current_self_regulation,
    )
    first_success_eval = _drive_cycle_failure_replay_evaluation(
        context="idle_window|strained|none",
        key="truthfulness:review_cycle_a",
        focus="truthfulness",
        self_regulation=current_self_regulation,
    )
    original_plan = supervisor.plan_self_evolution_task

    async def fake_evaluate_first_failure(_request=None):
        return first_failure_eval

    async def fail_plan_first(_request=None):
        raise RuntimeError("first drive plan failed")

    supervisor.evaluate_endogenous_drive = fake_evaluate_first_failure  # type: ignore[method-assign]
    supervisor.plan_self_evolution_task = fail_plan_first  # type: ignore[method-assign]
    history_before_first_failure = supervisor._load_endogenous_drive_history()
    events_before_first_failure = supervisor._load_endogenous_governance_events()

    with pytest.raises(RuntimeError, match="first drive plan failed"):
        await supervisor._run_endogenous_drive_cycle()

    assert supervisor._load_endogenous_drive_history()["judgements"] == (
        history_before_first_failure["judgements"]
    )
    assert supervisor._load_endogenous_governance_events()["events"] == (
        events_before_first_failure["events"]
    )

    async def fake_evaluate_first_success(_request=None):
        return first_success_eval

    supervisor.evaluate_endogenous_drive = fake_evaluate_first_success  # type: ignore[method-assign]
    supervisor.plan_self_evolution_task = original_plan  # type: ignore[method-assign]
    first_drive_result = await supervisor._run_endogenous_drive_cycle()
    assert first_drive_result["planned"] == 1

    second_events = supervisor._load_endogenous_governance_events()
    second_events["events"].extend(
        [
            {
                "event_id": "evt-review-cycle-b",
                "event_type": "governance_review_request",
                "channel": "governance_review_requests",
                "recorded_at": "2026-06-28T00:02:00+00:00",
                "context_key": "idle_window|stable|queue_debt",
                "preferred_focus": "queue_hygiene",
                "priority": 0.79,
                "message": "Queue hygiene should be reviewed after context switch.",
                "rationale": "queue context switched",
                "payload": {"queue_health": "review"},
            },
            {
                "event_id": "evt-align-cycle-b",
                "event_type": "autonomy_alignment_request",
                "channel": "autonomy_alignment_requests",
                "recorded_at": "2026-06-28T00:03:00+00:00",
                "context_key": "idle_window|stable|queue_debt",
                "preferred_focus": "queue_hygiene",
                "priority": 0.81,
                "message": "Context switch should keep self-regulation tight.",
                "rationale": "queue context switched",
                "payload": {"dominant_constraint": "queue_debt"},
            },
        ]
    )
    supervisor._persist_endogenous_governance_events(second_events)

    second_consumed = await supervisor._run_self_evolution_cycle()
    events_after_second_consumption = supervisor._load_endogenous_governance_events()["events"]
    consumed_by_id = {item.get("event_id"): item for item in events_after_second_consumption}
    assert second_consumed["governance_consumption"]["count"] == 1
    assert second_consumed["alignment_consumption"]["count"] == 1
    assert consumed_by_id["evt-align-cycle-a"]["consumed_at"] == first_consumed_at["evt-align-cycle-a"]
    assert consumed_by_id["evt-truth-cycle-a"]["consumed_at"] == first_consumed_at["evt-truth-cycle-a"]
    assert consumed_by_id["evt-review-cycle-b"]["consumed_action"] == "trigger_review_pass"
    assert consumed_by_id["evt-align-cycle-b"]["consumed_action"] == "increase_self_regulation"

    second_self_regulation = supervisor._load_endogenous_self_regulation()
    second_failure_eval = _drive_cycle_failure_replay_evaluation(
        context="idle_window|stable|queue_debt",
        key="truthfulness:review_failed_cycle_b",
        focus="truthfulness",
        self_regulation=second_self_regulation,
    )
    second_success_eval = _drive_cycle_failure_replay_evaluation(
        context="idle_window|stable|queue_debt",
        key="continuity:queue_hygiene_cycle_b",
        focus="queue_hygiene",
        self_regulation=second_self_regulation,
    )

    async def fake_evaluate_second_failure(_request=None):
        return second_failure_eval

    async def fail_plan_second(_request=None):
        raise RuntimeError("second drive plan failed")

    supervisor.evaluate_endogenous_drive = fake_evaluate_second_failure  # type: ignore[method-assign]
    supervisor.plan_self_evolution_task = fail_plan_second  # type: ignore[method-assign]
    history_before_second_failure = supervisor._load_endogenous_drive_history()
    events_before_second_failure = supervisor._load_endogenous_governance_events()
    cognition_before_second_failure = supervisor._load_endogenous_cognition_state()

    with pytest.raises(RuntimeError, match="second drive plan failed"):
        await supervisor._run_endogenous_drive_cycle()

    assert supervisor._load_endogenous_drive_history()["judgements"] == (
        history_before_second_failure["judgements"]
    )
    assert supervisor._load_endogenous_governance_events()["events"] == (
        events_before_second_failure["events"]
    )
    assert supervisor._load_endogenous_cognition_state()["state"] == (
        cognition_before_second_failure["state"]
    )

    async def fake_evaluate_second_success(_request=None):
        return second_success_eval

    supervisor.evaluate_endogenous_drive = fake_evaluate_second_success  # type: ignore[method-assign]
    supervisor.plan_self_evolution_task = original_plan  # type: ignore[method-assign]
    second_drive_result = await supervisor._run_endogenous_drive_cycle()

    history = supervisor._load_endogenous_drive_history()
    judgement_keys = [item.get("candidate_key") for item in history["judgements"]]
    focus_stats = history["strategy_memory"]["focus_stats"]
    contextual_stats = history["strategy_memory"]["contextual_focus_stats"]
    final_events = {
        item.get("event_id"): item
        for item in supervisor._load_endogenous_governance_events()["events"]
    }

    assert second_drive_result["planned"] == 1
    assert judgement_keys.count("truthfulness:review_cycle_a") == 1
    assert judgement_keys.count("continuity:queue_hygiene_cycle_b") == 1
    assert "continuity:queue_hygiene_failed_cycle_a" not in judgement_keys
    assert "truthfulness:review_failed_cycle_b" not in judgement_keys
    assert focus_stats["truthfulness"]["judged"] == 1
    assert focus_stats["queue_hygiene"]["judged"] == 1
    assert contextual_stats["idle_window|strained|none"]["truthfulness"]["judged"] == 1
    assert contextual_stats["idle_window|stable|queue_debt"]["queue_hygiene"]["judged"] == 1
    assert final_events["evt-align-cycle-a"]["consumed_at"] == first_consumed_at["evt-align-cycle-a"]
    assert final_events["evt-truth-cycle-a"]["consumed_at"] == first_consumed_at["evt-truth-cycle-a"]
    assert final_events["evt-review-cycle-b"]["consumed_action"] == "trigger_review_pass"
    assert final_events["evt-align-cycle-b"]["consumed_action"] == "increase_self_regulation"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_lm_task_generation_is_disabled_by_default(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor._endogenous_drive_engine._latest_lm_task_generation_context = {
        "status": "completed",
        "proposal_count": 2,
        "proposal_drift_memory": {
            "available": True,
            "average_score": 0.1,
            "drift_state": "drifting",
            "posture_alignment_health": "missing",
            "priority_basis_health": "missing",
            "missing_posture_alignment_count": 3,
            "missing_priority_basis_count": 3,
        },
    }

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": False, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": False, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    assert all(
        not bool(dict(candidate.get("metadata") or {}).get("llm_task_generated"))
        for candidate in result["candidates"]
    )
    reason = str(result["cognitive_self_regulation"].get("last_reason") or "")
    assert "proposal_drift_is_active" not in reason
    assert "proposal_explanation_memory_is_missing" not in reason


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_can_materialize_llm_task_proposals_from_evidence_packet(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 2,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    research_file = tmp_path / "research.json"
    research_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "title": "Architecture evidence",
                        "summary": "External research supports structured self-understanding.",
                        "source": "test_research",
                        "published_at": "2026-06-25T00:00:00+00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    shell_worktree = (tmp_path / ".body-slots" / "slot-B" / "worktree").resolve()
    shell_worktree.mkdir(parents=True, exist_ok=True)
    (shell_worktree / "run_agent.py").write_text("print('ok')\n", encoding="utf-8")
    (shell_worktree / "config.yaml").write_text("name: shell\n", encoding="utf-8")
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_external_research_enabled": True,
                    "endogenous_drive_external_research_files": [str(research_file)],
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 1, "uncertainty_high_count": 1},
            },
            "shell_slot": {
                "slot_id": "slot-B",
                "worktree_path": str(shell_worktree),
                "candidate_commit": "bbb222",
            },
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]

    fake_client = _FakeLLMClient(
        {
            "proposals": [
                {
                    "title": "Research current shell body weak points",
                    "summary": "Review the shell body structure and collect evidence-backed weaknesses for later improvement.",
                    "candidate_kind": "shell_baseline_learning",
                    "task_type": "learning",
                    "rationale": "The system lacks recent self-understanding evidence.",
                    "evidence_summary": ["no recent learning history", "shell understanding gap"],
                    "confidence": 0.82,
                    "risk_level": "medium",
                    "evidence_level": "moderate",
                    "observation_required": False,
                    "execution_mode": "guarded_execution",
                    "blocking_factors": ["need fresher shell structure observations"],
                    "referenced_evidence_nodes": ["self_structure", "external_research"],
                    "referenced_agenda_nodes": [
                        "expand_learning_frontier",
                        "focus:learning_expansion",
                        "missing_agenda_node",
                    ],
                    "posture_alignment": [
                        "follows_observe_first_by prioritizing learning over improvement until structure evidence improves"
                    ],
                    "priority_basis": [
                        "self-understanding evidence is missing",
                        "shell structure remains under-observed",
                    ],
                }
            ]
        }
    )

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["candidates"]
    lm_candidates = [
        candidate for candidate in result["candidates"]
        if dict(candidate.get("metadata") or {}).get("llm_task_generated")
    ]
    assert lm_candidates
    assert lm_candidates[0]["title"] == "Research current shell body weak points"
    assert lm_candidates[0]["metadata"]["llm_task_confidence"] == pytest.approx(0.82, rel=1e-3)
    assert lm_candidates[0]["metadata"]["llm_task_type"] == "learning"
    assert lm_candidates[0]["metadata"]["llm_task_risk_level"] == "medium"
    assert lm_candidates[0]["metadata"]["llm_task_evidence_level"] == "moderate"
    assert lm_candidates[0]["metadata"]["llm_task_execution_mode"] == "guarded_execution"
    assert lm_candidates[0]["metadata"]["llm_task_blocking_factors"] == [
        "need fresher shell structure observations"
    ]
    assert lm_candidates[0]["metadata"]["llm_referenced_evidence_nodes"] == [
        "self_structure",
        "external_research",
    ]
    assert lm_candidates[0]["metadata"]["llm_referenced_agenda_nodes"] == [
        "expand_learning_frontier",
        "focus:learning_expansion",
        "missing_agenda_node",
    ]
    assert lm_candidates[0]["metadata"]["llm_posture_alignment"]
    assert lm_candidates[0]["metadata"]["llm_priority_basis"]
    assert lm_candidates[0]["metadata"]["reference_alignment"]["matched_evidence_nodes"] == [
        "self_structure",
        "external_research",
    ]
    assert set(lm_candidates[0]["metadata"]["reference_alignment"]["missing_agenda_nodes"]) == {
        "focus:learning_expansion",
        "missing_agenda_node",
    }
    assert lm_candidates[0]["metadata"]["reference_alignment"]["alignment_score"] < 1.0
    assert lm_candidates[0]["metadata"]["reference_alignment"]["alignment_quality"] in {"partial", "weak"}
    assert "primary_evidence_nodes" in lm_candidates[0]["metadata"]["reference_alignment"]
    assert "grounding_penalty" in lm_candidates[0]["metadata"]["reference_alignment"]
    assert lm_candidates[0]["evidence"]["llm_generated"] is True
    assert lm_candidates[0]["evidence"]["llm_task_type"] == "learning"
    assert lm_candidates[0]["evidence"]["llm_referenced_evidence_nodes"] == [
        "self_structure",
        "external_research",
    ]
    assert lm_candidates[0]["evidence"]["reference_alignment"]["matched_evidence_nodes"] == [
        "self_structure",
        "external_research",
    ]
    assert lm_candidates[0]["constraints"]["lm_execution_mode"] == "guarded_execution"
    assert lm_candidates[0]["constraints"]["lm_referenced_agenda_nodes"] == [
        "expand_learning_frontier",
        "focus:learning_expansion",
        "missing_agenda_node",
    ]
    assert lm_candidates[0]["constraints"]["lm_posture_alignment"]
    assert lm_candidates[0]["constraints"]["lm_priority_basis"]
    assert set(lm_candidates[0]["constraints"]["reference_alignment"]["missing_agenda_nodes"]) == {
        "focus:learning_expansion",
        "missing_agenda_node",
    }
    assert "alignment_quality" in lm_candidates[0]["constraints"]["reference_alignment"]
    assert "cognitive_alignment" in lm_candidates[0]["metadata"]
    assert "score" in lm_candidates[0]["metadata"]["cognitive_alignment"]
    assert "quality" in lm_candidates[0]["constraints"]["cognitive_alignment"]
    assert "summary" in lm_candidates[0]["evidence"]["cognitive_alignment"]
    assert "primary_evidence_or_agenda_binding_is_missing" in lm_candidates[0]["metadata"]["supervisor_advisory"]["advisory_reasons"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_reuses_lm_proposals_when_cognitive_self_regulation_recomputes_candidates(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Weak previous proposal",
            "status": "deferred",
            "cognitive_alignment": {
                "score": 0.31,
                "quality": "weak",
                "top_priority_task_type": "observation",
                "reasons": ["reference_alignment_is_weak"],
            },
            "reference_alignment": {
                "alignment_score": 0.36,
                "alignment_quality": "weak",
                "missing_evidence_nodes": ["external_research"],
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 1, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient(
        {
            "proposals": [
                {
                    "title": "Review weak reference grounding",
                    "summary": "Audit recent weak reference alignment before new learning.",
                    "candidate_kind": "truthfulness_review",
                    "task_type": "review",
                    "rationale": "Recent reference alignment is weak.",
                    "evidence_summary": ["weak reference alignment"],
                    "confidence": 0.74,
                    "risk_level": "medium",
                    "evidence_level": "moderate",
                    "observation_required": True,
                    "execution_mode": "review_then_queue",
                    "blocking_factors": [],
                    "referenced_evidence_nodes": ["reference_alignment"],
                    "referenced_agenda_nodes": ["repair_truthfulness"],
                    "posture_alignment": ["review before expansion"],
                    "priority_basis": ["reference grounding remains weak"],
                }
            ]
        }
    )

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    task_generation_calls = [
        call for call in fake_client.calls
        if isinstance(call.get("user_payload"), dict)
        and "task_generation" in dict(call.get("user_payload") or {})
    ]
    assert len(task_generation_calls) == 1
    assert len(fake_client.calls) <= 2
    assert any(
        float(result["cognitive_self_regulation"].get(key) or 0.0) > 0.0
        for key in (
            "dynamic_candidate_throttle_boost",
            "dynamic_observation_bias_boost",
            "dynamic_truthfulness_bias_boost",
            "dynamic_learning_expansion_suppression",
        )
    )
    assert any(
        dict(candidate.get("metadata") or {}).get("llm_task_generated")
        for candidate in result["candidates"]
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_prefers_lm_led_candidate_stream_with_small_heuristic_complement(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    engine = supervisor._endogenous_drive_engine

    lm_candidates = [
        EndogenousTaskCandidate(
            stable_key="lm:truthfulness:review:alpha",
            title="Review grounding drift",
            summary="LM-led review",
            priority="high",
            governance_task_type="self_learning",
            task_family="self_learning",
            execution_kind=None,
            value_tags=["truthfulness"],
            utility=0.92,
            metadata={"score_breakdown": {"candidate_kind": "truthfulness_review"}},
        )
    ]
    heuristic_candidates = [
        EndogenousTaskCandidate(
            stable_key="continuity:memory_maintenance_sweep",
            title="Maintain long-term memory continuity",
            summary="Heuristic maintenance",
            priority="normal",
            governance_task_type="memory_maintenance",
            task_family="memory_maintenance",
            execution_kind="memory_maintenance",
            value_tags=["continuity"],
            utility=0.78,
            metadata={"score_breakdown": {"candidate_kind": "memory_maintenance"}},
        ),
        EndogenousTaskCandidate(
            stable_key="truthfulness:review_correction_signals",
            title="Review grounding drift",
            summary="Heuristic duplicate",
            priority="high",
            governance_task_type="self_learning",
            task_family="self_learning",
            execution_kind=None,
            value_tags=["truthfulness"],
            utility=0.81,
            metadata={"score_breakdown": {"candidate_kind": "truthfulness_review"}},
        ),
        EndogenousTaskCandidate(
            stable_key="continuity:queue_hygiene_review",
            title="Review self-evolution queue hygiene",
            summary="Heuristic queue review",
            priority="normal",
            governance_task_type="self_evolution",
            task_family="general_self_evolution",
            execution_kind="general_self_evolution",
            value_tags=["continuity", "truthfulness"],
            utility=0.71,
            metadata={"score_breakdown": {"candidate_kind": "queue_hygiene_review"}},
        ),
    ]

    merged = engine._merge_lm_led_candidate_stream(
        lm_candidates=lm_candidates,
        heuristic_candidates=heuristic_candidates,
        adaptive_policy=DriveAdaptivePolicy(
            learning_expansion_bias=0.5,
            truthfulness_bias=0.5,
            memory_continuity_bias=0.5,
            queue_hygiene_bias=0.5,
            body_growth_bias=0.5,
            observation_bias=0.5,
            candidate_throttle=0.0,
            candidate_budget=4,
            exploratory_learning_quota=2,
            body_growth_quota=1,
            preferred_focus="truthfulness",
            rationale="test",
        ),
    )

    assert merged[0].stable_key == "lm:truthfulness:review:alpha"
    assert all(candidate.stable_key != "truthfulness:review_correction_signals" for candidate in merged)
    assert len(merged) <= 3
    assert any(candidate.stable_key == "continuity:memory_maintenance_sweep" for candidate in merged)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_prompt_packet_budget_preserves_queue_state_snapshot_under_large_context(tmp_path):
    from systems.supervisor.endogenous_drive_prompts import _prompt_facing_evidence_packet

    large_packet = {
        "identity": {"role": "endogenous_supervisory_core"},
        "decision_core": {
            "current_judgement": "review should dominate until grounding is repaired",
            "dominant_constraint": "weak self structure grounding",
            "grounding_pressure": "high",
            "governance_posture": "observation_or_review",
            "secondary_task_shape_hint": "review",
            "secondary_task_shape_score": 0.82,
            "top_self_iteration_domain": "grounding",
            "primary_evidence_nodes": ["self_structure"],
            "primary_agenda_nodes": ["focus:learning_expansion"],
            "queue_state_summary": "queued_tasks=2",
            "summary": "Decision core summary",
        },
        "supporting_detail": {
            "grounding_gaps": ["missing_evidence:self_structure"],
            "contradictory_topics": ["self_structure->external_research:contradicts"],
            "weak_or_missing_channels": ["recent_learning"],
            "self_understanding_gaps": ["missing_recent_learning_trace"],
            "why_not_improvement_now": ["improvement would outrun grounding"],
            "trend_state": "locked",
            "recent_effect_direction": "mixed",
            "summary": "Supporting detail summary",
        },
        "long_tail_context": {
            "recent_learning_titles": ["Learning A", "Learning B"],
            "external_research_titles": ["Research A", "Research B"],
            "evidence_channels": [{"channel": "recent_learning", "evidence_strength": "weak", "item_count": 6}],
            "memory_context_preview": "memory preview",
            "summary": "Long tail summary",
        },
        "queued_tasks": [
            {
                "title": "Existing queue task A",
                "status": "queued",
                "priority": "high",
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
                "execution_kind": None,
            },
            {
                "title": "Existing queue task B",
                "status": "deferred",
                "priority": "normal",
                "governance_task_type": "self_evolution",
                "task_family": "general_self_evolution",
                "execution_kind": "general_self_evolution",
            },
        ],
        "queued_learning_titles": ["Existing queue task A"],
        "queued_body_improvement_titles": ["Existing improvement task"],
        "recent_learning_evidence": [
            {"title": f"Learning {idx}", "summary": "x" * 400, "quality_score": 0.7}
            for idx in range(12)
        ],
        "external_research_evidence": [
            {"title": f"Research {idx}", "summary": "y" * 400, "source": "test"}
            for idx in range(12)
        ],
        "memory_context": "z" * 5000,
        "evidence_channels": {
            "channels": [
                {
                    "channel": f"channel_{idx}",
                    "kind": "generic",
                    "items": [{"title": f"Item {idx}", "summary": "w" * 300}],
                }
                for idx in range(8)
            ]
        },
    }

    compact = _prompt_facing_evidence_packet(large_packet)
    assert "decision_core" in compact
    assert compact["decision_core"]["current_judgement"] == (
        "review should dominate until grounding is repaired"
    )
    assert "supporting_detail" in compact
    assert compact["supporting_detail"]["grounding_gaps"] == [
        "missing_evidence:self_structure",
    ]
    assert "long_tail_context" in compact
    assert "Learning A" in compact["long_tail_context"]["recent_learning_titles"]
    assert "queue_state_snapshot" in compact
    queue_snapshot = compact["queue_state_snapshot"]
    assert queue_snapshot["queued_task_count"] == 2
    assert "Existing queue task A" in queue_snapshot["recent_titles"]
    assert "guidance" in queue_snapshot


@pytest.mark.unit
def test_prompt_packet_priority_order_follows_charter_attention_policy():
    from systems.supervisor.endogenous_drive_prompts import _prompt_facing_evidence_packet

    compact = _prompt_facing_evidence_packet(
        {
            "identity": {"role": "endogenous_supervisory_core"},
            "decision_core": {"current_judgement": "review first"},
            "queue_state_snapshot": {"summary": "queued_tasks=1"},
            "supporting_detail": {"grounding_gaps": ["missing_evidence:self_structure"]},
        },
        cognition_charter={
            "prompt_attention_policy": {
                "priority_order": [
                    "queue_state_snapshot",
                    "decision_core",
                    "identity",
                    "supporting_detail",
                ],
            }
        },
    )

    assert list(compact.keys())[:4] == [
        "queue_state_snapshot",
        "decision_core",
        "identity",
        "supporting_detail",
    ]


@pytest.mark.unit
def test_prompt_packet_prefers_context_layers_and_keeps_compact_cognitive_memory():
    from systems.supervisor.endogenous_drive_prompts import _prompt_facing_evidence_packet

    compact = _prompt_facing_evidence_packet(
        {
            "decision_core": {
                "current_judgement": "review first",
                "dominant_constraint": "weak grounding",
                "summary": "decision core summary",
            },
            "supporting_detail": {
                "grounding_gaps": ["missing_evidence:self_structure"],
                "why_not_improvement_now": ["improvement would outrun grounding"],
                "summary": "supporting detail summary",
            },
            "long_tail_context": {
                "recent_learning_titles": ["Learning A"],
                "summary": "long tail summary",
            },
            "meta_cognition_profile": {
                "dominant_failure_mode": "grounding_instability",
                "stay_or_switch_bias": "stay",
                "secondary_task_shape_hint": "review",
                "priority_signals": ["reference alignment remains weak"],
                "summary": "meta summary",
            },
            "cognitive_assessment_memory": {
                "dominant_constraint": "old duplicate constraint",
                "common_current_judgements": ["older judgement"],
                "summary": "assessment memory summary",
            },
            "self_iteration_trend_memory": {
                "dominant_target": "grounding",
                "dominant_hypothesis": "repair grounding",
                "dominant_stay_or_switch": "stay",
                "dominant_switch_reason": "grounding is still active",
                "trend_state": "locked",
                "target_count": 1,
                "hypothesis_count": 1,
                "stay_or_switch_count": 1,
                "switch_reason_count": 1,
                "summary": "trend summary",
            },
            "switch_self_regulation_memory": {
                "preferred_switch_bias": "stay",
                "summary": "switch summary",
            },
            "post_task_effect_memory": {
                "effect_direction": "mixed",
                "summary": "effect summary",
            },
            "self_iteration_hypotheses": {
                "top_target_domain": "grounding",
                "dominant_hypothesis": "repair grounding",
                "hypothesis_count": 1,
                "suggested_task_types": ["review"],
                "hypotheses": [
                    {
                        "target_domain": "grounding",
                        "hypothesis": "repair grounding",
                        "priority": 0.7,
                        "evidence": ["missing_evidence:self_structure"],
                        "suggested_task_types": ["learning", "observation"],
                    },
                    {
                        "target_domain": "expansion",
                        "hypothesis": "legacy expansion should not amplify thin count",
                        "priority": 0.6,
                        "suggested_task_types": ["learning"],
                    },
                ],
            },
            "recent_reference_alignment": {
                "available": True,
                "average_alignment_score": 0.58,
                "weak_or_partial_count": 1,
                "recent_entries": [
                    {
                        "quality": "partial",
                        "missing_evidence_nodes": ["self_structure"],
                        "missing_agenda_nodes": ["focus:learning_expansion"],
                    }
                ],
            },
            "proposal_drift_memory": {
                "drift_state": "correcting",
                "summary": "drift summary",
            },
            "cognitive_evolution_draft": {
                "available": True,
                "summary": "draft summary",
            },
        },
        cognition_charter={},
    )

    assert "decision_core" in compact
    assert "supporting_detail" in compact
    assert "long_tail_context" in compact
    assert "meta_cognition_profile" in compact
    assert compact["meta_cognition_profile"]["dominant_failure_mode"] == "grounding_instability"
    assert "secondary_task_shape_hint" not in compact["meta_cognition_profile"]
    assert compact["cognitive_assessment_memory"]["dominant_constraint"] == "old duplicate constraint"
    assert compact["cognitive_assessment_memory"]["current_judgement"] == "older judgement"
    assert "common_current_judgements" not in compact["cognitive_assessment_memory"]
    assert compact["self_iteration_trend_memory"]["trend_state"] == "locked"
    assert compact["self_iteration_trend_memory"]["dominant_hypothesis"] == "repair grounding"
    assert compact["self_iteration_trend_memory"]["stay_or_switch"] == "stay"
    assert compact["self_iteration_trend_memory"]["switch_reason"] == "grounding is still active"
    assert compact["self_iteration_trend_memory"]["target_signal_count"] == 1
    assert compact["self_iteration_trend_memory"]["hypothesis_signal_count"] == 1
    assert compact["self_iteration_trend_memory"]["stay_or_switch_signal_count"] == 1
    assert compact["self_iteration_trend_memory"]["switch_reason_signal_count"] == 1
    assert "common_targets" not in compact["self_iteration_trend_memory"]
    assert "common_hypotheses" not in compact["self_iteration_trend_memory"]
    assert compact["self_iteration_hypotheses"]["hypothesis_count"] == 1
    assert compact["self_iteration_hypotheses"]["suggested_task_types"] == ["review"]
    assert "hypotheses" not in compact["self_iteration_hypotheses"]
    assert compact["recent_reference_alignment"]["entry_count"] == 1
    assert compact["recent_reference_alignment"]["weak_or_partial_count"] == 1
    assert "recent_entries" not in compact["recent_reference_alignment"]
    assert compact["switch_self_regulation_memory"]["preferred_switch_bias"] == "stay"
    assert compact["post_task_effect_memory"]["effect_direction"] == "mixed"
    assert compact["proposal_drift_memory"]["drift_state"] == "correcting"
    assert "entries" not in compact["proposal_drift_memory"]
    assert "cognitive_evolution_draft" not in compact


@pytest.mark.unit
def test_cognitive_briefing_renders_task_shape_hint_as_secondary_hint():
    from systems.supervisor.endogenous_drive_prompts import _render_cognitive_briefing

    briefing = _render_cognitive_briefing(
        {
            "decision_core": {
                "current_judgement": "review first until grounding is repaired",
                "dominant_constraint": "weak grounding",
                "grounding_pressure": "high",
                "governance_posture": "observation_or_review",
                "secondary_task_shape_hint": "review",
                "secondary_task_shape_score": 0.82,
                "summary": "decision core summary",
            },
            "meta_cognition_profile": {
                "dominant_failure_mode": "grounding_instability",
            },
            "cognitive_posture": {
                "name": "observe_first",
                "selection_reason": "reference alignment remains weak",
            },
            "supporting_detail": {
                "grounding_gaps": ["missing_evidence:self_structure"],
                "why_not_improvement_now": ["improvement would outrun grounding"],
            },
        }
    )

    assert "任务形态辅助提示: review (0.82)。仅作辅助参考，不得覆盖当前判断与治理姿态。" in briefing
    assert "当前建议治理姿态: observation_or_review" in briefing


@pytest.mark.unit
def test_prompt_packet_trim_stage_order_follows_charter_attention_policy():
    from systems.supervisor.endogenous_drive_prompts import _ensure_prompt_packet_budget

    packet = {
        "decision_core": {
            "current_judgement": "x" * 400,
            "dominant_constraint": "y" * 400,
            "grounding_pressure": "high",
            "governance_posture": "review",
            "secondary_task_shape_hint": "review",
            "secondary_task_shape_score": 0.8,
            "top_self_iteration_domain": "grounding",
            "primary_evidence_nodes": ["self_structure"] * 10,
            "primary_agenda_nodes": ["focus:learning_expansion"] * 10,
            "queue_state_summary": "queued_tasks=2;" + ("q" * 500),
            "summary": "s" * 500,
        },
        "agenda_graph": {
            "relation_edges": [{"from": "a", "to": "b"} for _ in range(10)],
            "evidence_to_gap_edges": [{"from": "c", "to": "d"} for _ in range(10)],
            "direction_task_links": [{"direction": "learn", "candidate_kind": "exploratory_learning"} for _ in range(8)],
        },
        "evidence_graph": {
            "nodes": [{"topic": f"topic_{idx}"} for idx in range(10)],
            "support_edges": [{"from": "a", "to": "b"} for _ in range(10)],
            "contradiction_edges": [{"from": "a", "to": "b"} for _ in range(10)],
        },
    }

    trimmed = _ensure_prompt_packet_budget(
        packet,
        max_chars=600,
        prompt_attention_policy={
            "trim_stage_order": ["graph_compaction"],
        },
    )

    assert len(trimmed["agenda_graph"]["relation_edges"]) == 4
    assert len(trimmed["evidence_graph"]["nodes"]) == 3
    assert len(trimmed["decision_core"]["primary_evidence_nodes"]) == 10


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_cognition_charter_to_lm_system_prompt(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                    "endogenous_drive_cognition_charter": {
                        "core_mission": "你是内生驱动核心，要基于证据与自我理解推动自我迭代。",
                        "self_model_principles": [
                            "先理解自身结构，再决定是否提出升级。",
                        ],
                        "evidence_policy": [
                            "必须综合 evidence_channels 与历史纠偏结果。",
                        ],
                        "task_generation_policy": [
                            "优先提出能提升自我理解质量的结构化任务。",
                        ],
                        "task_generation_focus": [
                            "先综合证据主轴与认知记忆，再决定任务类型。",
                        ],
                        "prompt_output_requirements": [
                            "提案必须解释为什么当前不是 improvement。",
                        ],
                        "self_iteration_guardrails": [
                            "不得抢占 API-A 的对外服务链路。",
                        ],
                    },
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": False, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": False, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert fake_client.calls
    system_prompt = fake_client.calls[0]["system_prompt"]
    assert "【认知宪章：自我模型原则】" in system_prompt
    assert "【认知宪章：证据政策】" in system_prompt
    assert "【认知宪章：任务生成政策】" in system_prompt
    assert "【认知宪章：任务生成焦点】" in system_prompt
    assert "【认知宪章：输出要求】" in system_prompt
    assert "【认知宪章：自我迭代护栏】" in system_prompt
    assert "先理解自身结构，再决定是否提出升级。" in system_prompt
    assert "必须综合 evidence_channels 与历史纠偏结果。" in system_prompt
    assert "优先提出能提升自我理解质量的结构化任务。" in system_prompt
    assert "先综合证据主轴与认知记忆，再决定任务类型。" in system_prompt
    assert "提案必须解释为什么当前不是 improvement。" in system_prompt
    assert "不得抢占 API-A 的对外服务链路。" in system_prompt


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_configurable_task_generation_focus_to_payload(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                    "endogenous_drive_cognition_charter": {
                        "core_mission": "你是内生驱动核心。",
                        "task_generation_focus": [
                            "先判断当前最值得修复的是 grounding 还是 self_model。",
                            "如果存在趋势记忆，先判断延续还是切换。",
                        ],
                        "prompt_output_requirements": [
                            "提案必须解释为什么当前优先级成立。",
                            "如果证据不够，允许返回空 proposals。",
                        ],
                    },
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "【本轮任务生成焦点】" in payload
    assert "【decision_core】" in payload
    assert "【supporting_detail】" in payload
    assert "【long_tail_context】" in payload
    assert "先判断当前最值得修复的是 grounding 还是 self_model。" in payload
    assert "如果存在趋势记忆，先判断延续还是切换。" in payload
    assert "【本轮输出附加要求】" in payload
    assert "提案必须解释为什么当前优先级成立。" in payload
    assert "如果证据不够，允许返回空 proposals。" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_builds_context_layers_in_evidence_packet(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Previous cognition",
            "event_type": "planned",
            "reference_alignment": {
                "alignment_score": 0.33,
                "missing_evidence_nodes": ["self_structure"],
                "missing_agenda_nodes": ["focus:learning_expansion"],
            },
            "llm_cognitive_assessment": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak self structure grounding",
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    idle_window = await fake_idle_window()
    idle_window["drive_history"] = supervisor._history_for_endogenous_drive(
        supervisor._load_endogenous_drive_history()
    )
    drive_context = supervisor._endogenous_drive_engine._build_drive_context(idle_window)
    evidence_packet = supervisor._endogenous_drive_engine._build_lm_evidence_packet(
        idle_window=idle_window,
        deliberation=supervisor._endogenous_drive_engine.build_deliberation_report(
            idle_window=idle_window
        ),
        drive_context=drive_context,
        memory_plan={},
        self_learning_plan={},
        self_evolution_plan={},
    )

    assert "decision_core" in evidence_packet
    assert "supporting_detail" in evidence_packet
    assert "long_tail_context" in evidence_packet
    assert evidence_packet["decision_core"]["top_self_iteration_domain"] == "grounding"
    assert "grounding_gaps" in evidence_packet["supporting_detail"]
    assert "recent_learning_titles" in evidence_packet["long_tail_context"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_context_layers_follow_charter_layering_policy(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config.service_runtime.endogenous_drive_cognition_charter.context_layering_policy.decision_core_fields = [
        "current_judgement",
        "primary_evidence_nodes",
        "decision_summary",
    ]
    supervisor.config.service_runtime.endogenous_drive_cognition_charter.context_layering_policy.supporting_detail_fields = [
        "grounding_gaps",
        "why_not_improvement_now",
    ]
    supervisor.config.service_runtime.endogenous_drive_cognition_charter.context_layering_policy.long_tail_context_fields = [
        "external_research_titles",
        "long_tail_summary",
    ]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Previous cognition",
            "event_type": "planned",
            "reference_alignment": {
                "alignment_score": 0.33,
                "missing_evidence_nodes": ["self_structure"],
                "missing_agenda_nodes": ["focus:learning_expansion"],
            },
            "llm_cognitive_assessment": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak self structure grounding",
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
                "why_not_improvement_now": [
                    "improvement would outrun grounding",
                ],
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    idle_window = await fake_idle_window()
    idle_window["drive_history"] = supervisor._history_for_endogenous_drive(
        supervisor._load_endogenous_drive_history()
    )
    drive_context = supervisor._endogenous_drive_engine._build_drive_context(idle_window)
    evidence_packet = supervisor._endogenous_drive_engine._build_lm_evidence_packet(
        idle_window=idle_window,
        deliberation=supervisor._endogenous_drive_engine.build_deliberation_report(
            idle_window=idle_window
        ),
        drive_context=drive_context,
        memory_plan={},
        self_learning_plan={},
        self_evolution_plan={},
    )

    assert set(evidence_packet["decision_core"].keys()) == {
        "current_judgement",
        "primary_evidence_nodes",
        "summary",
    }
    assert set(evidence_packet["supporting_detail"].keys()) == {
        "grounding_gaps",
        "why_not_improvement_now",
    }
    assert set(evidence_packet["long_tail_context"].keys()) == {
        "external_research_titles",
        "summary",
    }


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_evidence_attention_policy_changes_context_promotion(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    policy = supervisor.config.service_runtime.endogenous_drive_cognition_charter.evidence_attention_policy
    policy.decision_core_topic_limit = 2
    policy.long_tail_item_limit = 1
    policy.agenda_relevance_weight = 0.55
    policy.conflict_weight = 0.22
    policy.self_relevance_weight = 0.02

    history = supervisor._endogenous_drive_history_default()
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [
                {
                    "title": "Study scheduler backpressure",
                    "summary": "Focus on memory continuity rather than learning frontier gaps.",
                    "quality_score": 0.72,
                    "completed_at": "2026-06-20T12:00:00+00:00",
                    "task_family": "self_learning",
                    "execution_kind": None,
                    "evidence": {"evidence_summary": ["memory continuity", "backpressure"]},
                },
                {
                    "title": "Inspect learning frontier evidence gap",
                    "summary": "Explicitly covers focus learning_expansion and missing agenda alignment.",
                    "quality_score": 0.61,
                    "completed_at": "2026-06-27T12:00:00+00:00",
                    "task_family": "self_learning",
                    "execution_kind": None,
                    "evidence": {"evidence_summary": ["focus:learning_expansion", "missing_agenda"]},
                },
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_external_research_enabled": True,
                    "endogenous_drive_external_research_entries": [
                        "Learning expansion repair::focus:learning_expansion missing_agenda evidence should be repaired first.",
                        "Memory continuity note::Maintain archive coherence and old queue continuity.",
                    ],
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    idle_window = await fake_idle_window()
    idle_window["drive_history"] = supervisor._history_for_endogenous_drive(
        supervisor._load_endogenous_drive_history()
    )
    drive_context = supervisor._endogenous_drive_engine._build_drive_context(idle_window)
    evidence_packet = supervisor._endogenous_drive_engine._build_lm_evidence_packet(
        idle_window=idle_window,
        deliberation=supervisor._endogenous_drive_engine.build_deliberation_report(
            idle_window=idle_window
        ),
        drive_context=drive_context,
        memory_plan={},
        self_learning_plan={},
        self_evolution_plan={},
    )

    assert "evidence_attention" in evidence_packet
    assert len(evidence_packet["decision_core"]["primary_evidence_nodes"]) <= 2
    assert "learning_expansion" in " ".join(evidence_packet["decision_core"]["primary_agenda_nodes"]).lower()
    assert len(evidence_packet["long_tail_context"]["external_research_titles"]) == 1
    assert evidence_packet["long_tail_context"]["external_research_titles"][0].startswith(
        "Learning expansion repair"
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_builds_cognitive_feedback_memory_and_applies_attention_feedback(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Weak reference outcome",
            "event_type": "planned",
            "quality_score": 0.31,
            "cognitive_alignment": {"score": 0.42},
            "reference_alignment": {"alignment_score": 0.28},
            "llm_cognitive_assessment": {
                "primary_grounding_gaps": ["missing_evidence:self_structure"],
                "self_iteration_target": "grounding",
            },
        },
        {
            "title": "Another weak reference outcome",
            "event_type": "planned",
            "quality_score": 0.34,
            "cognitive_alignment": {"score": 0.4},
            "reference_alignment": {"alignment_score": 0.35},
            "llm_cognitive_assessment": {
                "primary_grounding_gaps": ["missing_agenda:focus:learning_expansion"],
                "self_iteration_target": "grounding",
            },
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    idle_window = await fake_idle_window()
    idle_window["drive_history"] = supervisor._history_for_endogenous_drive(
        supervisor._load_endogenous_drive_history()
    )
    drive_context = supervisor._endogenous_drive_engine._build_drive_context(idle_window)
    feedback_memory = supervisor._endogenous_drive_engine._build_cognitive_feedback_memory(
        drive_context
    )

    assert feedback_memory["available"] is True
    assert feedback_memory["reference_feedback_direction"] == "weak"
    assert feedback_memory["long_tail_signal_bias"] == "compress"

    charter = supervisor._endogenous_drive_engine._resolve_endogenous_cognition_charter(
        supervisor.config.service_runtime
    )
    adjusted_policy = supervisor._endogenous_drive_engine._resolve_evidence_attention_policy(
        charter,
        cognitive_feedback_memory=feedback_memory,
    )

    assert adjusted_policy["conflict_weight"] > charter["evidence_attention_policy"]["conflict_weight"]
    assert adjusted_policy["agenda_relevance_weight"] > charter["evidence_attention_policy"]["agenda_relevance_weight"]
    assert adjusted_policy["long_tail_item_limit"] < charter["evidence_attention_policy"]["long_tail_item_limit"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_endogenous_drive_cycle_exposes_cognitive_feedback_memory(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "cognitive_feedback_memory": {
                "available": True,
                "average_quality_score": 0.42,
                "average_reference_alignment_score": 0.36,
                "average_cognitive_alignment_score": 0.44,
                "confidence_feedback_direction": "weak",
                "reference_feedback_direction": "weak",
                "freshness_feedback_direction": "strong",
                "self_relevance_feedback_direction": "strong",
                "long_tail_signal_bias": "compress",
                "summary": "Recent cognitive outcomes suggest reference repair pressure.",
            },
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 1200, "agent": 1200, "memory": 1200},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    proposal_cognition = result["cognition_state"]["proposal_cognition"]
    assert "cognitive_evolution_trace" not in proposal_cognition
    feedback_trace = proposal_cognition["auxiliary_memory"]["cognitive_evolution_trace"]
    assert feedback_trace["feedback_available"] is True
    assert feedback_trace["feedback_reference_direction"] == "weak"
    assert feedback_trace["feedback_long_tail_bias"] == "compress"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_builds_cognitive_strategy_delta(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Weak reference outcome",
            "event_type": "planned",
            "quality_score": 0.31,
            "cognitive_alignment": {"score": 0.42},
            "reference_alignment": {"alignment_score": 0.28},
            "llm_cognitive_assessment": {
                "primary_grounding_gaps": ["missing_evidence:self_structure"],
                "self_iteration_target": "grounding",
            },
        },
        {
            "title": "Another weak reference outcome",
            "event_type": "planned",
            "quality_score": 0.34,
            "cognitive_alignment": {"score": 0.4},
            "reference_alignment": {"alignment_score": 0.35},
            "llm_cognitive_assessment": {
                "primary_grounding_gaps": ["missing_agenda:focus:learning_expansion"],
                "self_iteration_target": "grounding",
            },
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    idle_window = {
        "drive_history": supervisor._history_for_endogenous_drive(
            supervisor._load_endogenous_drive_history()
        )
    }
    drive_context = supervisor._endogenous_drive_engine._build_drive_context(idle_window)
    feedback_memory = supervisor._endogenous_drive_engine._build_cognitive_feedback_memory(
        drive_context
    )
    charter = supervisor._endogenous_drive_engine._resolve_endogenous_cognition_charter(
        supervisor.config.service_runtime
    )
    delta = supervisor._endogenous_drive_engine._build_cognitive_strategy_delta(
        cognition_charter=charter,
        cognitive_feedback_memory=feedback_memory,
    )

    assert delta["available"] is True
    assert delta["recommended_changes"]
    assert any(
        change["target"] == "evidence_attention_policy.conflict_weight"
        for change in delta["recommended_changes"]
    )
    assert delta["summary"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_endogenous_drive_cycle_exposes_cognitive_strategy_delta(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "cognitive_strategy_delta": {
                "available": True,
                "recommended_changes": [
                    {
                        "target": "evidence_attention_policy.conflict_weight",
                        "direction": "increase",
                        "current_value": 0.14,
                        "suggested_value": 0.19,
                        "delta": 0.05,
                        "reason": "reference alignment remains weak, so conflict-sensitive evidence should be weighted more heavily.",
                    }
                ],
                "summary": "Recent cognitive feedback suggests adjusting 1 evidence-attention parameters.",
            },
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 1200, "agent": 1200, "memory": 1200},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    proposal_cognition = result["cognition_state"]["proposal_cognition"]
    assert "cognitive_evolution_trace" not in proposal_cognition
    trace = proposal_cognition["auxiliary_memory"]["cognitive_evolution_trace"]
    assert trace["strategy_delta_available"] is True
    assert trace["strategy_delta_count"] == 1
    assert trace["primary_strategy_delta_target"] == "evidence_attention_policy.conflict_weight"
    assert "strategy_delta_targets" not in trace


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_builds_cognitive_evolution_draft(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Weak reference outcome",
            "event_type": "planned",
            "quality_score": 0.31,
            "cognitive_alignment": {"score": 0.42},
            "reference_alignment": {"alignment_score": 0.28},
            "llm_cognitive_assessment": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak self structure grounding",
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding first",
            },
        },
        {
            "title": "Another weak reference outcome",
            "event_type": "planned",
            "quality_score": 0.34,
            "cognitive_alignment": {"score": 0.4},
            "reference_alignment": {"alignment_score": 0.35},
            "llm_cognitive_assessment": {
                "current_judgement": "stay cautious while grounding gaps remain",
                "dominant_constraint": "grounding instability",
                "primary_grounding_gaps": ["missing_agenda:focus:learning_expansion"],
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding first",
            },
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    idle_window = {
        "drive_history": supervisor._history_for_endogenous_drive(
            supervisor._load_endogenous_drive_history()
        )
    }
    drive_context = supervisor._endogenous_drive_engine._build_drive_context(idle_window)
    feedback_memory = supervisor._endogenous_drive_engine._build_cognitive_feedback_memory(
        drive_context
    )
    charter = supervisor._endogenous_drive_engine._resolve_endogenous_cognition_charter(
        supervisor.config.service_runtime
    )
    strategy_delta = supervisor._endogenous_drive_engine._build_cognitive_strategy_delta(
        cognition_charter=charter,
        cognitive_feedback_memory=feedback_memory,
    )
    reference_alignment = supervisor._endogenous_drive_engine._build_recent_reference_alignment(
        drive_context
    )
    cognitive_assessment_memory = supervisor._endogenous_drive_engine._build_cognitive_assessment_memory(
        drive_context
    )
    self_iteration_trend_memory = supervisor._endogenous_drive_engine._build_self_iteration_trend_memory(
        drive_context
    )
    switch_self_regulation_memory = supervisor._endogenous_drive_engine._build_switch_self_regulation_memory(
        drive_context
    )
    post_task_effect_memory = supervisor._endogenous_drive_engine._build_post_task_effect_memory(
        drive_context
    )
    meta_cognition_profile = supervisor._endogenous_drive_engine._build_meta_cognition_profile(
        grounding_focus={
            "grounding_gaps": ["missing_evidence:self_structure"],
            "weak_or_missing_channels": ["recent_learning"],
        },
        self_iteration_hypotheses={
            "top_self_iteration_domain": "grounding",
            "top_self_iteration_hypothesis": "repair evidence-to-agenda grounding first",
        },
        cognitive_assessment_memory=cognitive_assessment_memory,
        self_iteration_trend_memory=self_iteration_trend_memory,
        switch_self_regulation_memory=switch_self_regulation_memory,
        post_task_effect_memory=post_task_effect_memory,
        proposal_drift_memory={"available": False},
        task_type_priors={
            "top_priority_task_type": "review",
            "top_priority_score": 0.74,
            "priors": [],
        },
    )
    draft = supervisor._endogenous_drive_engine._build_cognitive_evolution_draft(
        cognition_charter=charter,
        cognitive_feedback_memory=feedback_memory,
        cognitive_strategy_delta=strategy_delta,
        meta_cognition_profile=meta_cognition_profile,
        recent_reference_alignment=reference_alignment,
        self_iteration_trend_memory=self_iteration_trend_memory,
        post_task_effect_memory=post_task_effect_memory,
    )

    assert draft["available"] is True
    assert draft["attention_policy_delta"]["available"] is True
    assert draft["charter_delta"]["available"] is True
    assert any(
        change["target"] == "prompt_output_requirements"
        for change in draft["charter_delta"]["recommended_changes"]
    )
    assert draft["evidence_basis"]["reference_alignment_score"] < 0.58
    assert draft["summary"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_endogenous_drive_cycle_exposes_cognitive_evolution_draft(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "cognitive_evolution_draft": {
                "available": True,
                "mission_pressure": {
                    "core_mission": "evidence-driven self-iteration under governance constraints",
                    "dominant_failure_mode": "grounding_instability",
                    "top_self_iteration_domain": "grounding",
                    "reference_feedback_direction": "weak",
                    "confidence_feedback_direction": "weak",
                    "trend_state": "locked",
                    "effect_direction": "mixed",
                    "summary": "Mission pressure remains centered on grounding repair.",
                },
                "attention_policy_delta": {
                    "available": True,
                    "recommended_changes": [
                        {
                            "target": "evidence_attention_policy.conflict_weight",
                            "direction": "increase",
                            "current_value": 0.14,
                            "suggested_value": 0.19,
                            "delta": 0.05,
                            "reason": "reference alignment remains weak, so conflict-sensitive evidence should be weighted more heavily.",
                        }
                    ],
                    "summary": "Recent cognitive feedback suggests adjusting 1 evidence-attention parameters.",
                },
                "charter_delta": {
                    "available": True,
                    "recommended_changes": [
                        {
                            "target": "prompt_output_requirements",
                            "direction": "strengthen",
                            "priority": "high",
                            "reason": "reference alignment remains weak, so proposals should bind evidence and agenda nodes more explicitly.",
                            "suggested_additions": [
                                "提案必须明确列出关键 evidence / agenda 绑定关系，避免引用漂移。"
                            ],
                        }
                    ],
                    "summary": "Recent cognition suggests clarifying charter focus, output requirements, or self-iteration guardrails.",
                },
                "evidence_basis": {
                    "reference_alignment_score": 0.34,
                    "cognitive_alignment_score": 0.41,
                    "quality_score": 0.33,
                    "trend_state": "locked",
                    "effect_direction": "mixed",
                    "dominant_failure_mode": "grounding_instability",
                },
                "summary": "Cognitive evolution draft proposes 1 attention-policy adjustments and 1 charter-level adjustments.",
            },
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 1200, "agent": 1200, "memory": 1200},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    proposal_cognition = result["cognition_state"]["proposal_cognition"]
    assert "cognitive_evolution_trace" not in proposal_cognition
    trace = proposal_cognition["auxiliary_memory"]["cognitive_evolution_trace"]
    assert trace["evolution_draft_available"] is True
    assert trace["evolution_failure_mode"] == "grounding_instability"
    assert trace["evolution_attention_delta_count"] == 1
    assert trace["evolution_charter_delta_count"] == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_cognitive_briefing_prefers_context_layers(tmp_path):
    from systems.supervisor.endogenous_drive_prompts import build_endogenous_task_generation_payload

    payload = build_endogenous_task_generation_payload(
        evidence_packet={
            "decision_core": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak self structure grounding",
                "grounding_pressure": "high",
                "governance_posture": "observation_or_review",
                "secondary_task_shape_hint": "review",
                "secondary_task_shape_score": 0.82,
                "top_self_iteration_domain": "grounding",
                "top_self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
                "primary_evidence_nodes": ["self_structure"],
                "primary_agenda_nodes": ["focus:learning_expansion"],
                "queue_state_summary": "queued_tasks=2; recent_titles=Existing queue task A.",
                "cognitive_posture": {
                    "name": "truthfulness_first",
                    "selection_reason": "grounding remains unstable",
                },
                "summary": "Decision core: review-first until grounding is repaired.",
            },
            "supporting_detail": {
                "grounding_gaps": ["missing_evidence:self_structure"],
                "contradictory_topics": ["self_structure->external_research:contradicts"],
                "weak_or_missing_channels": ["recent_learning"],
                "self_understanding_gaps": ["missing_recent_learning_trace"],
                "why_not_improvement_now": ["improvement would outrun grounding"],
                "trend_state": "locked",
                "recent_effect_direction": "mixed",
                "summary": "Supporting detail summary",
            },
            "long_tail_context": {
                "recent_learning_titles": ["Learning A"],
                "summary": "Long tail summary",
            },
            "queue_state_snapshot": {
                "queued_task_count": 2,
                "recent_titles": ["Existing queue task A"],
                "summary": "queued_tasks=2; recent_titles=Existing queue task A.",
            },
            "meta_cognition_profile": {
                "summary": "stale fallback summary",
                "dominant_failure_mode": "grounding_instability",
            },
            "grounding_focus": {
                "summary": "stale fallback grounding summary",
            },
            "self_iteration_hypotheses": {
                "summary": "stale fallback iteration summary",
            },
        },
        cognition_charter={},
        max_candidates=2,
    )

    assert "【认知简报】" in payload
    assert "- 当前判断: review should dominate until grounding is repaired" in payload
    assert "- 当前主约束: weak self structure grounding" in payload
    assert "- 当前首要自我迭代域: grounding" in payload
    assert "- 当前不宜直接 improvement 的原因: improvement would outrun grounding" in payload
    assert "- 当前排队上下文: queued_tasks=2; recent_titles=Existing queue task A." in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_uses_default_task_generation_focus_when_not_configured(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "【本轮任务生成焦点】" in payload
    assert "先综合主证据主题、主议程主题、grounding 缺口和近期认知记忆，再判断当前最该做什么。" in payload
    assert "【本轮输出附加要求】" in payload
    assert "提案必须显式绑定 evidence graph / agenda graph 节点，避免漂浮任务。" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_cognitive_posture_semantics_to_lm_system_prompt(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    supervisor.config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.posture_selection_mode = "manual"
    supervisor.config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.active_posture_profile = "truthfulness_first"

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 2, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert fake_client.calls
    system_prompt = fake_client.calls[0]["system_prompt"]
    assert "【当前认知姿态】" in system_prompt
    assert "posture=truthfulness_first" in system_prompt
    assert "selection_reason=manual_selection" in system_prompt
    assert "【当前姿态下的任务排序要求】" in system_prompt
    assert "优先排序 review" in system_prompt
    assert "不要优先输出 improvement" in system_prompt


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_constrains_high_risk_or_weak_evidence_lm_proposals(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 0, "uncertainty_high_count": 0},
            },
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]

    fake_client = _FakeLLMClient(
        {
            "proposals": [
                {
                    "title": "Review queue anomalies conservatively",
                    "summary": "Inspect queue anomalies first because the current evidence is weak.",
                    "candidate_kind": "queue_hygiene_review",
                    "task_type": "review",
                    "rationale": "Queue anomalies may exist but supporting evidence is still weak.",
                    "evidence_summary": ["only one weak signal"],
                    "confidence": 0.31,
                    "risk_level": "high",
                    "evidence_level": "weak",
                    "observation_required": False,
                    "execution_mode": "guarded_execution",
                    "blocking_factors": ["insufficient queue evidence", "missing cross-check validation"],
                }
            ]
        }
    )

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    lm_candidates = [
        candidate for candidate in result["candidates"]
        if dict(candidate.get("metadata") or {}).get("llm_task_generated")
    ]
    assert lm_candidates
    candidate = lm_candidates[0]
    assert candidate["metadata"]["llm_task_risk_level"] == "high"
    assert candidate["metadata"]["llm_task_evidence_level"] == "weak"
    assert candidate["metadata"]["llm_task_observation_required"] is False
    assert candidate["metadata"]["llm_task_execution_mode"] == "guarded_execution"
    assert candidate["constraints"]["lm_execution_mode"] == "guarded_execution"
    assert candidate["constraints"]["lm_observation_required"] is False
    assert candidate["constraints"]["lm_blocking_factors"] == [
        "insufficient queue evidence",
        "missing cross-check validation",
    ]
    assert candidate["metadata"]["supervisor_advisory"]["recommended_observation_required"] is True
    assert candidate["metadata"]["supervisor_advisory"]["recommended_execution_mode"] == "review_then_queue"
    assert "high_risk_requires_governance_review" in candidate["metadata"]["supervisor_advisory"]["advisory_reasons"]
    assert candidate["metadata"]["cognitive_alignment"]["quality"] in {"weak", "partial", "strong"}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_marks_improvement_proposal_weak_when_it_conflicts_with_program_priors(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 1, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [
                {
                    "title": "Weak learning trace",
                    "summary": "Learning is still too weak to justify direct improvement.",
                    "quality_score": 0.24,
                    "completed_at": "2026-06-27T12:00:00+00:00",
                    "evidence": {"evidence_summary": ["weak learning evidence"]},
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient(
        {
            "proposals": [
                {
                    "title": "Directly improve shell body now",
                    "summary": "Attempt an improvement even though current evidence is still weak.",
                    "candidate_kind": "body_improvement",
                    "task_type": "improvement",
                    "rationale": "Try to improve immediately.",
                    "evidence_summary": ["weak learning evidence"],
                    "confidence": 0.32,
                    "risk_level": "high",
                    "evidence_level": "weak",
                    "observation_required": False,
                    "execution_mode": "guarded_execution",
                    "blocking_factors": ["learning evidence is weak"],
                    "referenced_evidence_nodes": ["self_understanding"],
                    "referenced_agenda_nodes": ["prepare_body_growth"],
                    "posture_alignment": ["claims improvement is acceptable under observe_first"],
                    "priority_basis": ["attempt immediate action despite weak evidence"],
                }
            ]
        }
    )

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    lm_candidates = [
        candidate for candidate in result["candidates"]
        if dict(candidate.get("metadata") or {}).get("llm_task_generated")
    ]
    if lm_candidates:
        alignment = lm_candidates[0]["metadata"]["cognitive_alignment"]
        assert alignment["quality"] == "weak"
        assert alignment["score"] < 0.45
        assert "improvement_runs_against_weak_or_missing_channels" in alignment["reasons"] or (
            "weak_evidence_conflicts_with_improvement_shape" in alignment["reasons"]
        )
        assert "task_shape_conflicts_with_current_cognitive_posture" in alignment["reasons"] or (
            "proposal_explicitly_states_posture_alignment" in alignment["reasons"]
        )
        assert "proposal_does_not_bind_primary_evidence_nodes" in alignment["reasons"] or (
            "reference_grounding_penalty_is_active" in alignment["reasons"]
        )
    else:
        posture = result["cognition_state"]["proposal_cognition"]["active_cognitive_posture_profile"]
        assert posture["name"] in {"observe_first", "truthfulness_first", "evidence_repair_first"}
        assert result["cognitive_self_regulation"]["dynamic_observation_bias_boost"] > 0.0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_prefers_conservative_review_over_weak_improvement_in_same_batch(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 2,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 1, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [
                {
                    "title": "Weak learning trace",
                    "summary": "Learning is still too weak to justify direct improvement.",
                    "quality_score": 0.24,
                    "completed_at": "2026-06-27T12:00:00+00:00",
                    "evidence": {"evidence_summary": ["weak learning evidence"]},
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient(
        {
            "cognitive_assessment": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak grounding around self structure",
                "primary_grounding_gaps": ["missing_evidence:self_structure"],
                "why_this_task_type_now": ["review can repair grounding before risky action"],
                "why_not_improvement_now": ["improvement would outrun current self-understanding"],
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
            },
            "proposals": [
                {
                    "title": "Directly improve shell body now",
                    "summary": "Attempt an improvement even though current evidence is still weak.",
                    "candidate_kind": "body_improvement",
                    "task_type": "improvement",
                    "rationale": "Try to improve immediately.",
                    "evidence_summary": ["weak learning evidence"],
                    "confidence": 0.32,
                    "risk_level": "high",
                    "evidence_level": "weak",
                    "observation_required": False,
                    "execution_mode": "guarded_execution",
                    "blocking_factors": ["learning evidence is weak"],
                    "referenced_evidence_nodes": ["self_understanding"],
                    "referenced_agenda_nodes": ["prepare_body_growth"],
                    "posture_alignment": ["claims improvement is acceptable under observe_first"],
                    "priority_basis": ["attempt immediate action despite weak evidence"],
                },
                {
                    "title": "Review grounding gaps before any body change",
                    "summary": "Repair evidence binding and confirm self-structure grounding before considering improvement.",
                    "candidate_kind": "queue_hygiene_review",
                    "task_type": "review",
                    "rationale": "Weak grounding should be repaired before any risky body change.",
                    "evidence_summary": ["missing_evidence:self_structure", "weak learning evidence"],
                    "confidence": 0.58,
                    "risk_level": "medium",
                    "evidence_level": "weak",
                    "observation_required": True,
                    "execution_mode": "review_then_queue",
                    "blocking_factors": ["grounding gaps remain unresolved"],
                    "referenced_evidence_nodes": ["self_structure"],
                    "referenced_agenda_nodes": ["focus:learning_expansion"],
                    "posture_alignment": ["follows observe_first by repairing grounding before action"],
                    "priority_basis": ["weak evidence should be handled conservatively"],
                },
            ],
        }
    )

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    lm_candidates = [
        candidate for candidate in result["candidates"]
        if dict(candidate.get("metadata") or {}).get("llm_task_generated")
    ]

    review_candidate = next(
        candidate
        for candidate in lm_candidates
        if candidate["metadata"]["llm_task_type"] == "review"
    )
    assert "conservative_task_type_matches_weak_channel_context" in (
        review_candidate["metadata"]["cognitive_alignment"]["reasons"]
    )
    assert review_candidate["metadata"]["cognitive_alignment"]["quality"] in {
        "weak",
        "partial",
        "strong",
    }
    improvement_candidates = [
        candidate
        for candidate in lm_candidates
        if candidate["metadata"]["llm_task_type"] == "improvement"
    ]
    if improvement_candidates:
        improvement_candidate = improvement_candidates[0]
        assert review_candidate["utility"] > improvement_candidate["utility"]
        assert review_candidate["metadata"]["cognitive_alignment"]["score"] > (
            improvement_candidate["metadata"]["cognitive_alignment"]["score"]
        )
        assert improvement_candidate["metadata"]["cognitive_alignment"]["quality"] == "weak"
        assert (
            "weak_evidence_conflicts_with_improvement_shape"
            in improvement_candidate["metadata"]["cognitive_alignment"]["reasons"]
        )
    else:
        assert review_candidate["metadata"]["cognitive_alignment"]["quality"] == "weak"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_softly_weakens_lm_proposal_when_it_omits_graph_bindings(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    engine = supervisor._endogenous_drive_engine
    reference_alignment = engine._align_lm_references(
        referenced_evidence_nodes=[],
        referenced_agenda_nodes=[],
        evidence_graph={
            "nodes": [
                {"topic": "self_structure", "avg_confidence": 0.74, "priority": 0.8},
                {"topic": "external_research", "avg_confidence": 0.66, "priority": 0.7},
            ]
        },
        agenda_graph={
            "focus": "learning_expansion",
            "focus_confidence": 0.82,
            "unresolved_gaps": [
                {"gap": "expand_learning_frontier", "priority": 0.77},
            ],
            "recommended_directions": [],
            "active_signals": [],
        },
    )
    cognitive_alignment = engine._score_lm_proposal_cognitive_alignment(
        candidate_kind="exploratory_learning",
        task_type="learning",
        evidence_level="moderate",
        risk_level="medium",
        observation_required=False,
        execution_mode="guarded_execution",
        blocking_factors=[],
        reference_alignment=reference_alignment,
        evidence_packet={
            "task_type_priors": {
                "top_priority_task_type": "observation",
                "top_priority_score": 0.88,
                "priors": [
                    {"task_type": "observation", "score": 0.88, "reasons": ["self gaps remain active"]},
                    {"task_type": "learning", "score": 0.42, "reasons": ["learning may still help"]},
                ],
            },
            "evidence_credibility_summary": {
                "high_credibility_channels": ["deliberation_state"],
                "weak_or_missing_channels": ["recent_learning", "external_research"],
            },
            "self_model_snapshot": {
                "self_understanding_gaps": ["missing_recent_learning_trace"],
            },
            "cognitive_posture": {
                "name": "evidence_repair_first",
            },
        },
        posture_alignment=["suggests learning could still help"],
        priority_basis=["future upside may exist"],
    )
    advisory = engine._supervisor_advisory_for_lm_proposal(
        candidate_kind="exploratory_learning",
        evidence_level="moderate",
        risk_level="medium",
        observation_required=False,
        execution_mode="guarded_execution",
        blocking_factors=[],
        reference_alignment=reference_alignment,
    )

    assert reference_alignment["grounding_penalty"] > 0.0
    assert reference_alignment["missing_primary_evidence_nodes"]
    assert reference_alignment["missing_primary_agenda_nodes"]
    assert cognitive_alignment["quality"] in {"weak", "partial"}
    assert "proposal_does_not_reference_evidence_graph" in cognitive_alignment["reasons"]
    assert "proposal_does_not_reference_agenda_graph" in cognitive_alignment["reasons"]
    assert advisory["recommended_observation_required"] is True
    assert advisory["recommended_execution_mode"] == "review_then_queue"
    assert "reference_binding_is_not_grounded_enough" in advisory["advisory_reasons"] or (
        "primary_evidence_or_agenda_binding_is_missing" in advisory["advisory_reasons"]
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_learning_and_shell_body_evidence_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    shell_worktree = (tmp_path / ".body-slots" / "slot-B" / "worktree").resolve()
    shell_worktree.mkdir(parents=True, exist_ok=True)
    (shell_worktree / "run_agent.py").write_text("print('ok')\n", encoding="utf-8")
    (shell_worktree / "config.yaml").write_text("name: shell\n", encoding="utf-8")
    (shell_worktree / "agent").mkdir(exist_ok=True)
    (shell_worktree / ".body-origin.json").write_text(
        json.dumps(
            {
                "source": "slot:slot-A",
                "source_root": str(tmp_path.resolve()),
                "source_branch": "main",
                "source_commit": "aaa111",
                "candidate_branch": "slot-B",
                "candidate_commit": "bbb222",
                "materialized_at": "2026-06-28T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 0, "uncertainty_high_count": 0},
            },
            "shell_slot": {
                "slot_id": "slot-B",
                "worktree_path": str(shell_worktree),
                "body_version": "v-next",
                "generation": 3,
                "candidate_branch": "slot-B",
                "candidate_commit": "bbb222",
            },
            "completed_learning_tasks": [
                {
                    "title": "Trace planner bottlenecks",
                    "summary": "Mapped queue and planner friction around learning follow-up.",
                    "quality_score": 0.91,
                    "completed_at": "2026-06-27T12:00:00+00:00",
                    "task_family": "self_learning",
                    "execution_kind": None,
                    "evidence": {
                        "evidence_summary": [
                            "planner backlog pressure",
                            "follow-up path is underweighted",
                        ]
                    },
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]

    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert fake_client.calls
    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "recent_learning_evidence" in payload
    assert "shell_body_profile" in payload
    assert "Trace planner bottlenecks" in payload
    assert "planner backlog pressure" in payload
    assert "\"profile_status\": \"ready\"" in payload
    assert "\"candidate_commit\": \"bbb222\"" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_configured_external_research_evidence_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                    "endogenous_drive_external_research_enabled": True,
                    "endogenous_drive_external_research_entries": [
                        "Reasoning frontier::Recent work highlights structured self-reflection with evidence checks.",
                        "Agent governance::Modern agent research emphasizes auditable deliberation records.",
                    ],
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert fake_client.calls
    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "external_research_evidence" in payload
    assert "Reasoning frontier" in payload
    assert "structured self-reflection with evidence checks" in payload
    assert "Agent governance" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_external_research_file_evidence_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    research_file = tmp_path / "docs" / "research-evidence.json"
    research_file.parent.mkdir(parents=True, exist_ok=True)
    research_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "title": "Cognitive architecture review",
                        "summary": "Recent notes emphasize structured self-model updates before action selection.",
                        "source": "manual_research_digest",
                        "url": "https://example.com/research/cognitive-architecture",
                        "published_at": "2026-06-20",
                        "tags": ["self-model", "evidence"],
                    },
                    {
                        "topic": "Agent reflection frontier",
                        "note": "Layered reflection is most effective when tied to explicit evidence ledgers.",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                    "endogenous_drive_external_research_enabled": True,
                    "endogenous_drive_external_research_files": [str(research_file)],
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert fake_client.calls
    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "external_research_evidence" in payload
    assert "Cognitive architecture review" in payload
    assert "structured self-model updates before action selection" in payload
    assert "manual_research_digest" in payload
    assert "Agent reflection frontier" in payload
    assert "explicit evidence ledgers" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_unified_evidence_channels_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    research_file = tmp_path / "research.json"
    research_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "title": "Meta-cognition update",
                        "summary": "Recent structured cognition work emphasizes explicit evidence channels.",
                        "source": "research_digest_file",
                        "published_at": "2026-06-25T00:00:00+00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    shell_worktree = (tmp_path / ".body-slots" / "slot-B" / "worktree").resolve()
    shell_worktree.mkdir(parents=True, exist_ok=True)
    (shell_worktree / "run_agent.py").write_text("print('ok')\n", encoding="utf-8")
    (shell_worktree / "config.yaml").write_text("name: shell\n", encoding="utf-8")

    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                    "endogenous_drive_external_research_enabled": True,
                    "endogenous_drive_external_research_files": [str(research_file)],
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "shell_slot": {
                "slot_id": "slot-B",
                "worktree_path": str(shell_worktree),
                "candidate_commit": "bbb222",
            },
            "completed_learning_tasks": [
                {
                    "title": "Inspect endogenous cognition path",
                    "summary": "Mapped current evidence flow into the endogenous drive prompt.",
                    "quality_score": 0.88,
                    "completed_at": "2026-06-27T12:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert fake_client.calls
    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "evidence_channels" in payload
    assert "research_digest" in payload
    assert "evidence_graph" in payload
    assert "agenda_graph" in payload
    assert "\"channel\": \"recent_learning\"" in payload
    assert "\"channel\": \"external_research\"" in payload
    assert "\"freshness_hint\": \"fresh\"" in payload
    assert "\"confidence\":" in payload
    assert "\"evidence_strength\": \"strong\"" in payload
    assert "\"conflict_flags\":" in payload
    assert "\"confidence_score\":" in payload
    assert "\"novelty_score\":" in payload
    assert "\"source_reliability\":" in payload
    assert "\"supports\":" in payload
    assert "\"contradicts\":" in payload
    assert "\"nodes\":" in payload
    assert "\"support_edges\":" in payload
    assert "\"topic\": \"external_research\"" in payload
    assert "\"focus\":" in payload
    assert "\"current_topics\":" in payload
    assert "\"relation_edges\":" in payload
    assert "\"evidence_to_gap_edges\":" in payload
    assert "\"direction_task_links\":" in payload
    assert "Meta-cognition update" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_recent_reference_alignment_feedback_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Review evidence drift",
            "reference_alignment": {
                "alignment_quality": "partial",
                "alignment_score": 0.58,
                "missing_evidence_nodes": ["self_structure"],
                "missing_agenda_nodes": ["focus:learning_expansion"],
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert fake_client.calls
    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "recent_reference_alignment" in payload
    assert "\"entry_count\": 1" in payload
    assert "\"primary_missing_evidence_node\": \"self_structure\"" in payload
    assert "\"primary_missing_agenda_node\": \"focus:learning_expansion\"" in payload
    assert "\"recent_entries\":" not in payload
    assert "\"missing_evidence_nodes\":" not in payload
    assert "\"missing_agenda_nodes\":" not in payload


@pytest.mark.unit
def test_engine_recent_reference_alignment_summary_stays_thin(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Reference drift seed",
            "reference_alignment": {
                "alignment_quality": "partial",
                "alignment_score": 0.58,
                "missing_evidence_nodes": ["self_structure"],
                "missing_agenda_nodes": ["focus:learning_expansion"],
            },
        }
    ]
    drive_context = {"drive_history": history}

    summary = supervisor._endogenous_drive_engine._build_recent_reference_alignment(
        drive_context
    )

    assert summary["available"] is True
    assert summary["entry_count"] == 1
    assert summary["average_alignment_score"] == 0.58
    assert summary["weak_or_partial_count"] == 1
    assert summary["primary_missing_evidence_node"] == "self_structure"
    assert summary["primary_missing_agenda_node"] == "focus:learning_expansion"
    assert summary["missing_evidence_node_count"] == 1
    assert summary["missing_agenda_node_count"] == 1
    assert "recent_entries" not in summary


@pytest.mark.unit
def test_engine_proposal_drift_memory_source_stays_thin(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Weak posture conflict",
            "cognitive_alignment": {
                "score": 0.34,
                "quality": "weak",
                "top_priority_task_type": "observation",
                "reasons": ["posture_conflicts_with_observe_first"],
            },
            "llm_posture_alignment": ["pushes action before observation"],
            "llm_priority_basis": ["weak channels still unresolved"],
        },
        {
            "title": "Repair through observation",
            "cognitive_alignment": {
                "score": 0.72,
                "quality": "strong",
                "top_priority_task_type": "observation",
                "reasons": ["matches_program_top_task_type_prior"],
            },
            "llm_posture_alignment": ["follows observe_first"],
            "llm_priority_basis": ["evidence gaps dominate agenda"],
        },
    ]

    summary = supervisor._endogenous_drive_engine._build_proposal_drift_memory(
        {"drive_history": history}
    )

    assert summary["available"] is True
    assert summary["average_score"] == 0.53
    assert summary["drift_state"] == "correcting"
    assert summary["quality_counts"] == {"strong": 1, "partial": 0, "weak": 1}
    assert summary["posture_alignment_signal_count"] == 2
    assert summary["priority_basis_signal_count"] == 2
    assert summary["posture_alignment_health"] == "inconsistent"
    assert summary["priority_basis_health"] == "inconsistent"
    assert summary["dominant_posture_conflict_reason"] == "posture_conflicts_with_observe_first"
    assert "recent_entries" not in summary
    assert "entries" not in summary
    assert "common_posture_alignment" not in summary
    assert "common_priority_basis" not in summary


@pytest.mark.unit
def test_runtime_recent_cognitive_alignment_summary_source_stays_thin(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Weak posture conflict",
            "event_type": "planned",
            "cognitive_alignment": {
                "score": 0.36,
                "quality": "weak",
                "top_priority_task_type": "review",
                "reasons": ["task_shape_conflicts_with_current_cognitive_posture"],
            },
            "llm_posture_alignment": ["pushes action before observation"],
            "llm_priority_basis": ["weak channels still unresolved"],
        },
        {
            "title": "Partial repair",
            "event_type": "planned",
            "cognitive_alignment": {
                "score": 0.64,
                "quality": "partial",
                "top_priority_task_type": "review",
                "reasons": ["matches_program_top_task_type_prior"],
            },
            "llm_posture_alignment": ["keeps review bounded"],
            "llm_priority_basis": ["truthfulness pressure is elevated"],
        },
    ]

    summary = supervisor._build_recent_cognitive_alignment_summary(
        history_snapshot=history
    )

    assert summary["available"] is True
    assert summary["average_score"] == 0.5
    assert summary["quality_counts"] == {"strong": 0, "partial": 1, "weak": 1}
    assert summary["dominant_task_shape"] == "review"
    assert summary["reason_count"] == 2
    assert summary["posture_alignment_signal_count"] == 2
    assert summary["priority_basis_signal_count"] == 2
    assert summary["missing_posture_alignment_count"] == 0
    assert summary["missing_priority_basis_count"] == 0
    assert summary["entry_count"] == 2
    assert "entries" not in summary
    assert "common_reasons" not in summary
    assert "common_posture_alignment" not in summary
    assert "common_priority_basis" not in summary


@pytest.mark.unit
def test_engine_auxiliary_memory_sources_stay_thin(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Previous cognition",
            "event_type": "planned",
            "llm_cognitive_assessment": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak self structure grounding",
                "why_not_improvement_now": [
                    "improvement would run ahead of self-understanding",
                ],
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding first",
                "stay_or_switch": "stay",
                "switch_reason": "grounding gaps still dominate",
            },
        }
    ]
    drive_context = {"drive_history": history}

    assessment = supervisor._endogenous_drive_engine._build_cognitive_assessment_memory(
        drive_context
    )
    trend = supervisor._endogenous_drive_engine._build_self_iteration_trend_memory(
        drive_context
    )

    assert assessment["current_judgement"] == "review should dominate until grounding is repaired"
    assert assessment["current_judgement_count"] == 1
    assert assessment["why_not_improvement_now_count"] == 1
    assert assessment["self_iteration_target"] == "grounding"
    assert assessment["self_iteration_hypothesis_count"] == 1
    assert assessment["entry_count"] == 1
    assert "entries" not in assessment
    assert "common_current_judgements" not in assessment
    assert "common_self_iteration_hypotheses" not in assessment
    assert trend["dominant_target"] == "grounding"
    assert trend["dominant_hypothesis"] == "repair evidence-to-agenda grounding first"
    assert trend["target_count"] == 1
    assert trend["hypothesis_count"] == 1
    assert trend["dominant_stay_or_switch"] == "stay"
    assert trend["switch_reason_count"] == 1
    assert "entries" not in trend
    assert "common_targets" not in trend
    assert "common_hypotheses" not in trend


@pytest.mark.unit
def test_runtime_auxiliary_memory_sources_stay_thin(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Previous cognition",
            "event_type": "planned",
            "llm_cognitive_assessment": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak self structure grounding",
                "why_not_improvement_now": [
                    "improvement would run ahead of self-understanding",
                ],
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding first",
                "stay_or_switch": "switch",
                "switch_reason": "old target stopped improving",
            },
        }
    ]

    assessment = supervisor._build_recent_lm_cognitive_assessment_summary(
        history_snapshot=history
    )
    trend = supervisor._build_recent_self_iteration_trend_summary(
        history_snapshot=history
    )

    assert assessment["current_judgement"] == "review should dominate until grounding is repaired"
    assert assessment["self_iteration_target"] == "grounding"
    assert assessment["self_iteration_hypothesis"] == "repair evidence-to-agenda grounding first"
    assert assessment["why_not_improvement_now_count"] == 1
    assert assessment["entry_count"] == 1
    assert "entries" not in assessment
    assert "common_current_judgements" not in assessment
    assert "common_self_iteration_targets" not in assessment
    assert trend["dominant_target"] == "grounding"
    assert trend["dominant_hypothesis"] == "repair evidence-to-agenda grounding first"
    assert trend["dominant_stay_or_switch"] == "switch"
    assert trend["dominant_switch_reason"] == "old target stopped improving"
    assert trend["entry_count"] == 1
    assert "entries" not in trend
    assert "common_targets" not in trend
    assert "common_stay_or_switch" not in trend


@pytest.mark.unit
def test_engine_post_task_effect_memory_source_stays_thin_and_ignores_planned(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Planned only",
            "event_type": "planned",
            "quality_score": 0.2,
            "cognitive_alignment": {"score": 0.2},
            "reference_alignment": {"alignment_score": 0.2},
            "llm_cognitive_assessment": {"self_iteration_target": "grounding"},
        },
        {
            "title": "Completed grounding task",
            "event_type": "decision",
            "quality_score": 0.8,
            "cognitive_alignment": {"score": 0.7},
            "reference_alignment": {"alignment_score": 0.72},
            "llm_cognitive_assessment": {"self_iteration_target": "grounding"},
        },
    ]

    effect = supervisor._endogenous_drive_engine._build_post_task_effect_memory(
        {"drive_history": history}
    )

    assert effect["available"] is True
    assert effect["effect_direction"] == "improving"
    assert effect["average_quality_score"] == 0.8
    assert effect["average_cognitive_alignment_score"] == 0.7
    assert effect["average_reference_alignment_score"] == 0.72
    assert effect["dominant_target_effect"] == "grounding:helped"
    assert effect["entry_count"] == 1
    assert "entries" not in effect


@pytest.mark.unit
def test_engine_self_iteration_hypotheses_use_thin_why_not_improvement_field(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    hypotheses = supervisor._endogenous_drive_engine._build_self_iteration_hypotheses(
        self_model_snapshot={
            "readiness": {"self_iteration_readiness_score": 0.76},
            "self_understanding_gaps": [],
        },
        evidence_credibility_summary={"weak_or_missing_channels": []},
        task_type_priors={"top_priority_task_type": "review", "top_priority_score": 0.72},
        recent_reference_alignment={
            "available": True,
            "average_alignment_score": 0.82,
            "weak_or_partial_count": 0,
        },
        proposal_drift_memory={"available": True, "drift_state": "stable"},
        cognitive_assessment_memory={
            "available": True,
            "why_not_improvement_now": "improvement would outrun grounding",
            "why_not_improvement_now_count": 1,
        },
        self_iteration_trend_memory={},
        switch_self_regulation_memory={},
        post_task_effect_memory={"effect_direction": "mixed"},
        grounding_focus={"grounding_gaps": []},
    )

    readiness_hypothesis = next(
        row
        for row in hypotheses["hypotheses"]
        if row["target_domain"] == "improvement_readiness"
    )
    assert readiness_hypothesis["evidence"] == ["improvement would outrun grounding"]
    assert "common_why_not_improvement_now" not in hypotheses

    legacy_hypotheses = supervisor._endogenous_drive_engine._build_self_iteration_hypotheses(
        self_model_snapshot={
            "readiness": {"self_iteration_readiness_score": 0.76},
            "self_understanding_gaps": [],
        },
        evidence_credibility_summary={"weak_or_missing_channels": []},
        task_type_priors={"top_priority_task_type": "review", "top_priority_score": 0.72},
        recent_reference_alignment={
            "available": True,
            "average_alignment_score": 0.82,
            "weak_or_partial_count": 0,
        },
        proposal_drift_memory={"available": True, "drift_state": "stable"},
        cognitive_assessment_memory={
            "available": True,
            "common_why_not_improvement_now": "legacy improvement blocker",
        },
        self_iteration_trend_memory={},
        switch_self_regulation_memory={},
        post_task_effect_memory={"effect_direction": "mixed"},
        grounding_focus={"grounding_gaps": []},
    )
    legacy_readiness_hypothesis = next(
        row
        for row in legacy_hypotheses["hypotheses"]
        if row["target_domain"] == "improvement_readiness"
    )
    assert legacy_readiness_hypothesis["evidence"] == ["legacy improvement blocker"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_grounding_focus_summary_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                    "endogenous_drive_external_research_enabled": True,
                    "endogenous_drive_external_research_entries": [
                        "Grounding research::Evidence-bound proposals improve autonomous planning stability.",
                    ],
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Recent grounding miss",
            "reference_alignment": {
                "alignment_quality": "weak",
                "alignment_score": 0.41,
                "missing_evidence_nodes": ["self_structure"],
                "missing_agenda_nodes": ["focus:learning_expansion"],
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)

    shell_worktree = (tmp_path / ".body-slots" / "slot-Z" / "worktree").resolve()
    shell_worktree.mkdir(parents=True, exist_ok=True)
    (shell_worktree / "run_agent.py").write_text("print('ok')\n", encoding="utf-8")
    (shell_worktree / "config.yaml").write_text("name: shell\n", encoding="utf-8")
    (shell_worktree / "agent").mkdir(exist_ok=True)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 1, "uncertainty_high_count": 1},
            },
            "shell_slot": {
                "slot_id": "slot-Z",
                "worktree_path": str(shell_worktree),
                "candidate_commit": "zzz999",
            },
            "completed_learning_tasks": [
                {
                    "title": "Inspect structure grounding",
                    "summary": "Found weak evidence binding around self structure topics.",
                    "quality_score": 0.78,
                    "completed_at": "2026-06-27T12:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert fake_client.calls
    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "grounding_focus" in payload
    assert "\"primary_evidence_nodes\":" in payload
    assert "\"primary_agenda_nodes\":" in payload
    assert "\"grounding_gaps\":" in payload
    assert "\"weak_or_missing_channels\":" in payload
    assert "Bind proposals to primary evidence and agenda nodes first" in payload
    assert "missing_evidence:self_structure" in payload
    assert "missing_agenda:focus:learning_expansion" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_cognitive_assessment_memory_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Previous review-first judgement",
            "event_type": "planned",
            "llm_cognitive_assessment": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak self structure grounding",
                "primary_grounding_gaps": [
                    "missing_evidence:self_structure",
                    "missing_agenda:focus:learning_expansion",
                ],
                "why_not_improvement_now": [
                    "improvement would run ahead of self-understanding",
                ],
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert fake_client.calls
    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "cognitive_assessment_memory" in payload
    assert "review should dominate until grounding is repaired" in payload
    assert "weak self structure grounding" in payload
    assert "improvement would run ahead of self-understanding" in payload
    assert "\"common_current_judgements\":" not in payload
    assert "\"common_why_not_improvement_now\":" not in payload
    assert "\"common_grounding_gaps\":" not in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_self_iteration_hypotheses_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Previous cognition",
            "event_type": "planned",
            "llm_cognitive_assessment": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak self structure grounding",
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
                "why_not_improvement_now": [
                    "improvement would run ahead of self-understanding",
                ],
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert fake_client.calls
    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "self_iteration_hypotheses" in payload
    assert "\"top_target_domain\"" in payload
    assert "\"dominant_hypothesis\"" in payload
    assert "\"hypothesis_count\":" in payload
    assert "\"top_evidence\":" in payload
    assert "\"hypotheses\":" not in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_self_iteration_trend_memory_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Trend A",
            "event_type": "planned",
            "llm_cognitive_assessment": {
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
            },
        },
        {
            "title": "Trend B",
            "event_type": "planned",
            "llm_cognitive_assessment": {
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
            },
        },
        {
            "title": "Trend C",
            "event_type": "planned",
            "llm_cognitive_assessment": {
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
            },
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "self_iteration_trend_memory" in payload
    assert "\"dominant_target\": \"grounding\"" in payload
    assert "\"trend_state\": \"locked\"" in payload
    assert "\"target_signal_count\": 1" in payload
    assert "\"hypothesis_signal_count\": 1" in payload
    assert "\"common_targets\":" not in payload
    assert "\"common_hypotheses\":" not in payload
    assert "\"common_stay_or_switch\":" not in payload
    assert "\"common_switch_reasons\":" not in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_switch_self_regulation_memory_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Switch outcome",
            "event_type": "planned",
            "quality_score": 0.82,
            "llm_cognitive_assessment": {
                "stay_or_switch": "switch",
                "switch_reason": "new evidence overturned previous focus",
            },
        },
        {
            "title": "Stay outcome",
            "event_type": "planned",
            "quality_score": 0.31,
            "llm_cognitive_assessment": {
                "stay_or_switch": "stay",
                "switch_reason": "current path still looked acceptable",
            },
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "switch_self_regulation_memory" in payload
    assert "\"preferred_switch_bias\": \"switch\"" in payload
    assert "\"average_switch_quality\": 0.82" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_post_task_effect_memory_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Helpful grounding task",
            "event_type": "decision",
            "quality_score": 0.82,
            "cognitive_alignment": {"score": 0.73},
            "reference_alignment": {"alignment_score": 0.71},
            "llm_cognitive_assessment": {
                "self_iteration_target": "grounding",
            },
        },
        {
            "title": "Weak grounding task",
            "event_type": "decision",
            "quality_score": 0.28,
            "cognitive_alignment": {"score": 0.31},
            "reference_alignment": {"alignment_score": 0.22},
            "llm_cognitive_assessment": {
                "self_iteration_target": "grounding",
            },
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "\"recent_effect_direction\": \"mixed\"" in payload
    assert "mixed" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_meta_cognition_profile_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Previous review-first judgement",
            "event_type": "planned",
            "quality_score": 0.33,
            "cognitive_alignment": {"score": 0.38},
            "reference_alignment": {
                "alignment_score": 0.29,
                "missing_evidence_nodes": ["self_structure"],
                "missing_agenda_nodes": ["focus:learning_expansion"],
            },
            "llm_cognitive_assessment": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak self structure grounding",
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
                "stay_or_switch": "stay",
                "switch_reason": "grounding gaps still dominate the agenda",
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {"uncertainty_high_count": 1}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "meta_cognition_profile" in payload
    assert "review should dominate until grounding is repaired" in payload
    assert "\"grounding_pressure\": \"high\"" in payload or "\"grounding_pressure\": \"medium\"" in payload
    assert "repair evidence-to-agenda grounding before aggressive self-iteration" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_cognitive_briefing_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                    "endogenous_drive_external_research_enabled": True,
                    "endogenous_drive_external_research_entries": [
                        "Briefing research::Grounded cognition improves planning quality.",
                    ],
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 1, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [
                {
                    "title": "Inspect grounding posture",
                    "summary": "Mapped weak grounding and current observation-first pressure.",
                    "quality_score": 0.82,
                    "completed_at": "2026-06-27T12:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert fake_client.calls
    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "【认知简报】" in payload
    assert "元认知画像:" in payload
    assert "当前主失败模式:" in payload
    assert "当前建议治理姿态:" in payload
    assert "当前姿态:" in payload
    assert "任务形态辅助提示:" in payload
    assert "当前主证据主题:" in payload
    assert "当前主议程主题:" in payload
    assert "当前 grounding 缺口:" in payload
    assert "当前弱通道:" in payload
    assert "先基于主证据主题与主议程主题做判断" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_requests_cognitive_assessment_from_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert fake_client.calls
    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "\"cognitive_assessment\"" in payload
    assert "\"current_judgement\":\"...\"" in payload
    assert "\"dominant_constraint\":\"...\"" in payload
    assert "\"primary_grounding_gaps\":[\"...\"]" in payload
    assert "\"why_this_task_type_now\":[\"...\"]" in payload
    assert "\"stay_or_switch\":\"stay\"" in payload
    assert "\"switch_reason\":\"...\"" in payload
    assert "先输出一个 cognitive_assessment" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_self_model_snapshot_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                    "endogenous_drive_external_research_enabled": True,
                    "endogenous_drive_external_research_entries": [
                        "Self-model frontier::Structured self-understanding improves autonomous planning quality.",
                    ],
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Repair evidence references",
            "reference_alignment": {
                "alignment_quality": "partial",
                "alignment_score": 0.61,
                "missing_evidence_nodes": ["self_structure"],
                "missing_agenda_nodes": ["prepare_body_growth"],
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)

    shell_worktree = (tmp_path / ".body-slots" / "slot-B" / "worktree").resolve()
    shell_worktree.mkdir(parents=True, exist_ok=True)
    (shell_worktree / "run_agent.py").write_text("print('ok')\n", encoding="utf-8")
    (shell_worktree / "config.yaml").write_text("name: shell\n", encoding="utf-8")
    (shell_worktree / "agent").mkdir(exist_ok=True)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 1, "uncertainty_high_count": 1},
            },
            "shell_slot": {
                "slot_id": "slot-B",
                "worktree_path": str(shell_worktree),
                "candidate_commit": "bbb222",
            },
            "completed_learning_tasks": [
                {
                    "title": "Inspect self-model bottlenecks",
                    "summary": "Mapped missing body understanding and weak follow-up evidence.",
                    "quality_score": 0.86,
                    "completed_at": "2026-06-27T12:00:00+00:00",
                    "evidence": {
                        "evidence_summary": [
                            "body understanding is incomplete",
                            "follow-up evidence is weak",
                        ]
                    },
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert fake_client.calls
    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "self_model_snapshot" in payload
    assert "\"self_iteration_readiness_score\":" in payload
    assert "\"self_understanding_gaps\":" in payload
    assert "\"reference_alignment_feedback\":" in payload
    assert "\"body_profile_status\": \"ready\"" in payload
    assert "\"research_freshness\": \"unknown\"" in payload
    assert "Inspect self-model bottlenecks" in payload
    assert "\"grounding_focus\":" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_evidence_credibility_and_task_shape_hint_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                    "endogenous_drive_external_research_enabled": True,
                    "endogenous_drive_external_research_entries": [
                        "Evidence frontier::Structured research can guide autonomous self-observation.",
                    ],
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Weak reference alignment",
            "reference_alignment": {
                "alignment_quality": "partial",
                "alignment_score": 0.43,
                "missing_evidence_nodes": ["self_structure"],
                "missing_agenda_nodes": ["expand_learning_frontier"],
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 2, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [
                {
                    "title": "Weak self-observation trace",
                    "summary": "Recent learning did not yet close the self-understanding gap.",
                    "quality_score": 0.31,
                    "completed_at": "2026-06-27T12:00:00+00:00",
                    "evidence": {
                        "evidence_summary": [
                            "learning result remains weak",
                        ]
                    },
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert fake_client.calls
    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "evidence_credibility_summary" in payload
    assert "\"high_credibility_channels\":" in payload
    assert "\"weak_or_missing_channels\":" in payload
    assert "\"task_type_priors\":" not in payload
    assert "\"top_priority_task_type\":" not in payload
    assert "\"secondary_task_shape_hint\":" in payload
    assert "\"task_type\": \"observation\"" in payload or "\"task_type\": \"review\"" in payload
    assert "\"recent_reference_alignment\":" in payload
    assert "\"alignment_score\": 0.43" in payload or "\"average_alignment_score\": 0.43" in payload
    assert "self_structure" in payload
    assert "任务形态辅助提示:" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_proposal_drift_memory_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Misaligned improvement attempt",
            "cognitive_alignment": {
                "score": 0.32,
                "quality": "weak",
                "top_priority_task_type": "observation",
                "reasons": ["improvement_runs_against_weak_or_missing_channels"],
            },
            "llm_posture_alignment": [
                "claims improvement is acceptable under observe_first",
            ],
            "llm_priority_basis": [
                "attempt immediate action despite weak evidence",
            ],
        },
        {
            "title": "Repair through observation",
            "cognitive_alignment": {
                "score": 0.71,
                "quality": "strong",
                "top_priority_task_type": "observation",
                "reasons": ["matches_program_top_task_type_prior"],
            },
            "llm_posture_alignment": [
                "follows_observe_first by prioritizing observation",
            ],
            "llm_priority_basis": [
                "evidence gaps still dominate the agenda",
            ],
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert fake_client.calls
    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "proposal_drift_memory" in payload
    assert "Misaligned improvement attempt" not in payload
    assert "\"drift_state\": \"correcting\"" in payload or "\"drift_state\": \"drifting\"" in payload
    assert "\"quality_counts\":" in payload
    assert "\"posture_alignment_signal_count\":" in payload
    assert "\"priority_basis_signal_count\":" in payload
    proposal_drift_payload = payload.split('"proposal_drift_memory":', 1)[1].split(
        '"recent_learning_evidence":',
        1,
    )[0]
    assert "\"common_posture_alignment\":" not in payload
    assert "\"common_priority_basis\":" not in payload
    assert "\"recent_entries\":" not in proposal_drift_payload
    assert "\"posture_alignment_health\":" in payload
    assert "\"priority_basis_health\":" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runtime_cognition_exposes_posture_reasoning_memory(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Weak posture explanation",
            "event_type": "planned",
            "status": "deferred",
            "cognitive_alignment": {
                "score": 0.36,
                "quality": "weak",
                "top_priority_task_type": "review",
                "reasons": ["task_shape_conflicts_with_current_cognitive_posture"],
            },
            "llm_posture_alignment": [
                "claims improvement is acceptable under observe_first",
            ],
            "llm_priority_basis": [
                "push execution despite unresolved evidence gaps",
            ],
        }
    ]
    supervisor._persist_endogenous_drive_history(history)
    _seed_current_lm_reasoning_state(
        supervisor,
        {
            "status": "completed",
            "model_role": "governance_reasoner",
            "proposal_count": 1,
            "task_type_priors": {
                "top_priority_task_type": "review",
                "top_priority_score": 0.68,
                "priors": [
                    {"task_type": "review", "score": 0.68, "reasons": ["truthfulness pressure is elevated"]},
                ],
            },
            "proposal_drift_memory": {
                "available": True,
                "average_score": 0.36,
                "drift_state": "drifting",
                "quality_counts": {"strong": 0, "partial": 0, "weak": 1},
                "posture_alignment_signal_count": 1,
                "priority_basis_signal_count": 1,
                "missing_posture_alignment_count": 0,
                "missing_priority_basis_count": 0,
                "posture_alignment_health": "inconsistent",
                "priority_basis_health": "inconsistent",
                "summary": "Recent proposal alignment is drifting.",
            },
            "recent_reference_alignment": {
                "available": True,
                "average_alignment_score": 0.52,
                "weak_or_partial_count": 1,
                "summary": "Reference alignment is not yet stable.",
            },
            "summary": "LM cognition status=completed.",
        },
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 2,
                    "uncertainty_high_count": 1,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    proposal_cognition = result["cognition_state"]["proposal_cognition"]
    drift_memory = proposal_cognition["auxiliary_memory"]["proposal_drift_memory"]

    assert "proposal_drift_memory" not in proposal_cognition
    assert drift_memory["posture_alignment_signal_count"] == 1
    assert drift_memory["priority_basis_signal_count"] == 1
    assert "common_posture_alignment" not in drift_memory
    assert "common_priority_basis" not in drift_memory
    assert drift_memory["posture_alignment_health"] == "inconsistent"
    assert "recent_cognitive_alignment" not in proposal_cognition
    recent_alignment = proposal_cognition["auxiliary_memory"]["recent_cognitive_alignment"]
    assert recent_alignment["posture_alignment_signal_count"] == 1
    assert "common_posture_alignment" not in recent_alignment


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_cognitive_posture_to_lm(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    supervisor.config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.posture_selection_mode = "auto"

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 3, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert fake_client.calls
    payload = fake_client.calls[0]["user_payload"]["task_generation"]
    assert "cognitive_posture" in payload
    assert "\"name\": \"truthfulness_first\"" in payload
    assert "\"selection_reason\": \"truthfulness_signals_are_elevated\"" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_records_cognitive_posture_in_lm_generation_context(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config
    supervisor.config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.posture_selection_mode = "manual"
    supervisor.config.service_runtime.endogenous_drive_cognition_charter.cognitive_control_policy.active_posture_profile = "observe_first"
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Previous cognition",
            "event_type": "planned",
            "llm_cognitive_assessment": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak self structure grounding",
                "why_not_improvement_now": [
                    "improvement would run ahead of self-understanding",
                ],
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": (
                    "repair evidence-to-agenda grounding before aggressive self-iteration"
                ),
                "stay_or_switch": "stay",
                "switch_reason": "grounding gaps still dominate the agenda",
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    state = supervisor._endogenous_drive_engine.get_latest_lm_task_generation_context()
    assert state["cognitive_posture"]["name"] == "observe_first"
    assert state["cognitive_posture"]["selection_mode"] == "manual"
    assert "task_type_priors" not in state
    assert "task_shape_hint" not in state
    generation_trend_memory = state["self_iteration_trend_memory"]
    assert generation_trend_memory["target_count"] == 1
    assert generation_trend_memory["hypothesis_count"] == 1
    assert generation_trend_memory["stay_or_switch_count"] == 1
    assert generation_trend_memory["switch_reason_count"] == 1
    assert "common_targets" not in generation_trend_memory
    assert "common_hypotheses" not in generation_trend_memory
    assert "common_stay_or_switch" not in generation_trend_memory
    assert "common_switch_reasons" not in generation_trend_memory
    assessment_memory = state["cognitive_assessment_memory"]
    assert assessment_memory["current_judgement"] == (
        "review should dominate until grounding is repaired"
    )
    assert assessment_memory["current_judgement_count"] == 1
    assert assessment_memory["why_not_improvement_now_count"] == 1
    assert "common_current_judgements" not in assessment_memory
    assert "common_why_not_improvement_now" not in assessment_memory
    assert "hypotheses" not in state["self_iteration_hypotheses"]
    proposal_cognition = result["cognition_state"]["proposal_cognition"]
    runtime_trend_memory = proposal_cognition["auxiliary_memory"]["self_iteration_trend_memory"]
    assert runtime_trend_memory["target_count"] == 1
    assert runtime_trend_memory["hypothesis_count"] == 1
    assert runtime_trend_memory["stay_or_switch_count"] == 1
    assert runtime_trend_memory["switch_reason_count"] == 1
    assert proposal_cognition["assessment_trace"]["current_judgement"] == (
        "review should dominate until grounding is repaired"
    )
    assert proposal_cognition["assessment_trace"]["why_not_improvement_now_count"] == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_records_lm_cognitive_assessment_in_generation_context(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient(
        {
            "cognitive_assessment": {
                "current_judgement": "evidence is still incomplete, so review should dominate",
                "dominant_constraint": "weak grounding around self structure",
                "primary_grounding_gaps": [
                    "missing_evidence:self_structure",
                    "missing_agenda:focus:learning_expansion",
                ],
                "why_this_task_type_now": [
                    "review can repair evidence binding before risky action",
                ],
                "why_not_improvement_now": [
                    "improvement would outrun current self-understanding",
                ],
            },
            "proposals": [],
        }
    )

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    state = supervisor._endogenous_drive_engine.get_latest_lm_task_generation_context()
    assessment = state["cognitive_assessment"]
    assert assessment["current_judgement"] == "evidence is still incomplete, so review should dominate"
    assert assessment["dominant_constraint"] == "weak grounding around self structure"
    assert assessment["primary_grounding_gaps"] == [
        "missing_evidence:self_structure",
        "missing_agenda:focus:learning_expansion",
    ]
    assert assessment["why_this_task_type_now"] == [
        "review can repair evidence binding before risky action",
    ]
    assert assessment["why_not_improvement_now"] == [
        "improvement would outrun current self-understanding",
    ]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_records_self_iteration_fields_in_generation_context(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 1,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient(
        {
            "cognitive_assessment": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak grounding around self structure",
                "primary_grounding_gaps": ["missing_evidence:self_structure"],
                "why_this_task_type_now": ["review can repair grounding"],
                "why_not_improvement_now": ["improvement would outrun current self-understanding"],
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
                "stay_or_switch": "stay",
                "switch_reason": "grounding gaps still dominate the agenda",
            },
            "proposals": [],
        }
    )

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    state = supervisor._endogenous_drive_engine.get_latest_lm_task_generation_context()
    assessment = state["cognitive_assessment"]
    assert assessment["self_iteration_target"] == "grounding"
    assert assessment["self_iteration_hypothesis"] == (
        "repair evidence-to-agenda grounding before aggressive self-iteration"
    )
    assert assessment["stay_or_switch"] == "stay"
    assert assessment["switch_reason"] == "grounding gaps still dominate the agenda"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_keeps_lm_candidates_empty_when_weak_context_returns_empty_proposals(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "endogenous_drive_lm_task_generation_enabled": True,
                    "endogenous_drive_lm_task_max_candidates": 2,
                }
            )
        }
    )
    supervisor._endogenous_drive_engine.config = supervisor.config

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 1, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [
                {
                    "title": "Weak learning trace",
                    "summary": "Learning is still too weak to justify direct improvement.",
                    "quality_score": 0.24,
                    "completed_at": "2026-06-27T12:00:00+00:00",
                    "evidence": {"evidence_summary": ["weak learning evidence"]},
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient(
        {
            "cognitive_assessment": {
                "current_judgement": "evidence is still incomplete, so review should dominate",
                "dominant_constraint": "weak grounding around self structure",
                "primary_grounding_gaps": [
                    "missing_evidence:self_structure",
                    "missing_agenda:focus:learning_expansion",
                ],
                "why_this_task_type_now": [
                    "review can repair evidence binding before risky action",
                ],
                "why_not_improvement_now": [
                    "improvement would outrun current self-understanding",
                ],
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
            },
            "proposals": [],
        }
    )

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    lm_candidates = [
        candidate for candidate in result["candidates"]
        if dict(candidate.get("metadata") or {}).get("llm_task_generated")
    ]
    assert lm_candidates == []
    program_candidate_kinds = {
        str(
            dict(dict(candidate.get("metadata") or {}).get("score_breakdown") or {}).get(
                "candidate_kind"
            )
            or ""
        ).strip()
        for candidate in result["candidates"]
    }
    assert "body_improvement" not in program_candidate_kinds
    assert "generic_learning_fallback" not in program_candidate_kinds
    assert "exploratory_learning" not in program_candidate_kinds
    assert program_candidate_kinds <= {"truthfulness_review", "queue_hygiene_review", ""}
    state = supervisor._endogenous_drive_engine.get_latest_lm_task_generation_context()
    assert state["status"] == "completed"
    assert state["proposal_count"] == 0
    assert "raw_candidate_kinds" not in state
    assert state["cognitive_assessment"]["why_not_improvement_now"] == [
        "improvement would outrun current self-understanding",
    ]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_endogenous_drive_cycle_exposes_stay_switch_trend_memory(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Trend A",
            "event_type": "planned",
            "llm_cognitive_assessment": {
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
                "stay_or_switch": "stay",
                "switch_reason": "grounding gaps still dominate the agenda",
            },
        },
        {
            "title": "Trend B",
            "event_type": "planned",
            "llm_cognitive_assessment": {
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
                "stay_or_switch": "stay",
                "switch_reason": "grounding gaps still dominate the agenda",
            },
        },
        {
            "title": "Trend C",
            "event_type": "planned",
            "llm_cognitive_assessment": {
                "self_iteration_target": "self_model",
                "self_iteration_hypothesis": "expand self-understanding before escalation",
                "stay_or_switch": "switch",
                "switch_reason": "self-model uncertainty has overtaken grounding pressure",
            },
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "completed_learning_tasks": [],
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    trend_memory = result["cognition_state"]["proposal_cognition"]["auxiliary_memory"]["self_iteration_trend_memory"]
    assert trend_memory["available"] is True
    assert trend_memory["stay_or_switch_count"] == 2
    assert trend_memory["switch_reason_count"] == 2
    assert "common_stay_or_switch" not in trend_memory
    assert "common_switch_reasons" not in trend_memory


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_endogenous_drive_cycle_exposes_cognitive_assessment_memory(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Previous cognition",
            "event_type": "planned",
            "llm_cognitive_assessment": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak self structure grounding",
                "why_not_improvement_now": [
                    "improvement would run ahead of self-understanding",
                ],
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "completed_learning_tasks": [],
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    proposal_cognition = result["cognition_state"]["proposal_cognition"]
    assert "cognitive_assessment_memory" not in proposal_cognition
    assert proposal_cognition["assessment_trace"]["available"] is True
    assert proposal_cognition["assessment_trace"]["dominant_constraint"] == (
        "weak self structure grounding"
    )
    assert proposal_cognition["assessment_trace"]["current_judgement"] == (
        "review should dominate until grounding is repaired"
    )
    assert proposal_cognition["assessment_trace"]["why_not_improvement_now_count"] == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_proposal_cognition_fallback_self_iteration_hypotheses_stays_thin(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Fallback cognition",
            "event_type": "planned",
            "llm_cognitive_assessment": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak self structure grounding",
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding first",
                "why_not_improvement_now": [
                    "improvement would run ahead of self-understanding",
                ],
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)

    result = supervisor._build_endogenous_proposal_cognition(
        history_snapshot=history,
        candidate_items=[],
        lm_reasoning_state={},
    )

    hypotheses_memory = result["auxiliary_memory"]["self_iteration_hypotheses"]
    assert hypotheses_memory["available"] is True
    assert hypotheses_memory["dominant_hypothesis"] == (
        "repair evidence-to-agenda grounding first"
    )
    assert hypotheses_memory["top_target_domain"] == "grounding"
    assert hypotheses_memory["hypothesis_count"] == 1
    assert "hypotheses" not in hypotheses_memory
    assert "common_hypotheses" not in hypotheses_memory


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_endogenous_drive_cycle_falls_back_to_history_reference_alignment_without_lm_state(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Reference drift seed",
            "event_type": "decision",
            "reference_alignment": {
                "alignment_quality": "partial",
                "alignment_score": 0.58,
                "missing_evidence_nodes": ["self_structure"],
                "missing_agenda_nodes": ["focus:learning_expansion"],
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)
    _seed_current_lm_reasoning_state(
        supervisor,
        {},
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "completed_learning_tasks": [],
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    proposal_cognition = result["cognition_state"]["proposal_cognition"]
    assert "recent_reference_alignment" not in proposal_cognition
    reference_alignment = proposal_cognition["auxiliary_memory"]["recent_reference_alignment"]
    assert reference_alignment["available"] is True
    assert reference_alignment["average_alignment_score"] == 0.58
    assert reference_alignment["weak_or_partial_count"] == 1
    assert reference_alignment["entry_count"] == 1
    assert reference_alignment["primary_missing_evidence_node"] == "self_structure"
    assert reference_alignment["primary_missing_agenda_node"] == "focus:learning_expansion"
    assert "recent_entries" not in reference_alignment
    assert proposal_cognition["meta_cognition_profile"]["grounding_pressure"] == "medium"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_endogenous_drive_cycle_exposes_self_iteration_trend_memory(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Trend A",
            "event_type": "planned",
            "llm_cognitive_assessment": {
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
            },
        },
        {
            "title": "Trend B",
            "event_type": "planned",
            "llm_cognitive_assessment": {
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
            },
        },
        {
            "title": "Trend C",
            "event_type": "planned",
            "llm_cognitive_assessment": {
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
            },
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "completed_learning_tasks": [],
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    trend_memory = result["cognition_state"]["proposal_cognition"]["auxiliary_memory"]["self_iteration_trend_memory"]
    assert trend_memory["available"] is True
    assert trend_memory["dominant_target"] == "grounding"
    assert trend_memory["trend_state"] == "locked"
    assert trend_memory["target_stability"] == "stable"
    assert trend_memory["target_count"] == 1
    assert trend_memory["hypothesis_count"] == 1
    assert "common_targets" not in trend_memory
    assert "common_hypotheses" not in trend_memory


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_endogenous_drive_cycle_exposes_switch_self_regulation_memory(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Switch outcome",
            "event_type": "planned",
            "quality_score": 0.82,
            "llm_cognitive_assessment": {
                "stay_or_switch": "switch",
                "switch_reason": "new evidence overturned previous focus",
            },
        },
        {
            "title": "Stay outcome",
            "event_type": "planned",
            "quality_score": 0.31,
            "llm_cognitive_assessment": {
                "stay_or_switch": "stay",
                "switch_reason": "current path still looked acceptable",
            },
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "completed_learning_tasks": [],
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    switch_memory = result["cognition_state"]["proposal_cognition"]["auxiliary_memory"]["switch_self_regulation_memory"]
    assert switch_memory["available"] is True
    assert switch_memory["preferred_switch_bias"] == "switch"
    assert switch_memory["average_switch_quality"] == 0.82
    assert switch_memory["average_stay_quality"] == 0.31
    assert switch_memory["stay_or_switch_count"] == 0
    assert "common_stay_or_switch" not in switch_memory


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_endogenous_drive_cycle_exposes_post_task_effect_memory(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Helpful grounding task",
            "event_type": "decision",
            "quality_score": 0.82,
            "cognitive_alignment": {"score": 0.73},
            "reference_alignment": {"alignment_score": 0.71},
            "llm_cognitive_assessment": {
                "self_iteration_target": "grounding",
            },
        },
        {
            "title": "Weak grounding task",
            "event_type": "decision",
            "quality_score": 0.28,
            "cognitive_alignment": {"score": 0.31},
            "reference_alignment": {"alignment_score": 0.22},
            "llm_cognitive_assessment": {
                "self_iteration_target": "grounding",
            },
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "completed_learning_tasks": [],
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    effect_memory = result["cognition_state"]["proposal_cognition"]["auxiliary_memory"]["post_task_effect_memory"]
    assert effect_memory["available"] is True
    assert effect_memory["effect_direction"] == "mixed"
    assert effect_memory["average_quality_score"] == 0.55
    assert effect_memory["average_cognitive_alignment_score"] == 0.52
    assert effect_memory["average_reference_alignment_score"] == 0.465


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_endogenous_drive_cycle_exposes_meta_cognition_profile(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Meta cognition seed",
            "event_type": "planned",
            "quality_score": 0.25,
            "cognitive_alignment": {"score": 0.34},
            "reference_alignment": {
                "alignment_score": 0.28,
                "missing_evidence_nodes": ["self_structure"],
                "missing_agenda_nodes": ["focus:learning_expansion"],
            },
            "llm_cognitive_assessment": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak self structure grounding",
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
                "stay_or_switch": "stay",
                "switch_reason": "grounding gaps still dominate the agenda",
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "completed_learning_tasks": [],
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    profile = result["cognition_state"]["proposal_cognition"]["meta_cognition_profile"]
    assert profile["available"] is True
    assert profile["current_judgement"] == "review should dominate until grounding is repaired"
    assert profile["self_iteration_focus"]["domain"] == "grounding"
    assert profile["dominant_failure_mode"] in {
        "grounding_instability",
        "weak self structure grounding",
        "proposal_selection_drift",
    }
    assert profile["governance_posture"] in {"observation_or_review", "review"}


@pytest.mark.unit
def test_post_task_effect_memory_ignores_planned_only_outcomes(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    effect_memory = supervisor._build_recent_post_task_effect_summary(
        history_snapshot={
            "outcomes": [
                {
                    "title": "Planned only outcome",
                    "event_type": "planned",
                    "quality_score": 0.22,
                    "cognitive_alignment": {"score": 0.31},
                    "reference_alignment": {"alignment_score": 0.24},
                    "llm_cognitive_assessment": {
                        "self_iteration_target": "grounding",
                    },
                }
            ]
        }
    )

    assert effect_memory == {
        "available": False,
        "summary": "No recent post-task effect memory is available yet.",
    }


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_builds_meta_cognition_profile_in_evidence_packet(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Evidence packet seed",
            "event_type": "planned",
            "quality_score": 0.24,
            "cognitive_alignment": {"score": 0.35},
            "reference_alignment": {
                "alignment_score": 0.31,
                "missing_evidence_nodes": ["self_structure"],
                "missing_agenda_nodes": ["focus:learning_expansion"],
            },
            "llm_cognitive_assessment": {
                "current_judgement": "review should dominate until grounding is repaired",
                "dominant_constraint": "weak self structure grounding",
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
            },
        }
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    idle_window = await fake_idle_window()
    idle_window["drive_history"] = supervisor._history_for_endogenous_drive(
        supervisor._load_endogenous_drive_history()
    )
    drive_context = supervisor._endogenous_drive_engine._build_drive_context(idle_window)
    evidence_packet = supervisor._endogenous_drive_engine._build_lm_evidence_packet(
        idle_window=idle_window,
        deliberation=supervisor._endogenous_drive_engine.build_deliberation_report(
            idle_window=idle_window
        ),
        drive_context=drive_context,
        memory_plan={},
        self_learning_plan={},
        self_evolution_plan={},
    )

    profile = evidence_packet["meta_cognition_profile"]
    assert profile["available"] is True
    assert profile["current_judgement"] == "review should dominate until grounding is repaired"
    assert profile["top_self_iteration_domain"] == "grounding"
    assert "grounding_pressure" in profile
    assert profile["priority_signals"]


@pytest.mark.unit
def test_meta_cognition_profile_does_not_let_task_prior_override_review_judgement():
    from systems.supervisor.endogenous_drive import EndogenousDriveEngine

    engine = EndogenousDriveEngine()
    profile = engine._build_meta_cognition_profile(
        grounding_focus={
            "grounding_gaps": [],
            "contradictory_topics": [],
        },
        self_iteration_hypotheses={
            "top_target_domain": "grounding",
            "dominant_hypothesis": "repair evidence-to-agenda grounding before aggressive self-iteration",
            "hypotheses": [],
        },
        cognitive_assessment_memory={
            "common_current_judgements": [
                "review should dominate until grounding is repaired",
            ],
            "dominant_constraint": "weak self structure grounding",
            "common_self_iteration_targets": ["grounding"],
            "common_self_iteration_hypotheses": [
                "repair evidence-to-agenda grounding before aggressive self-iteration",
            ],
            "common_why_not_improvement_now": [
                "improvement would outrun current self-understanding",
            ],
        },
        self_iteration_trend_memory={
            "dominant_target": "grounding",
            "common_hypotheses": [
                "repair evidence-to-agenda grounding before aggressive self-iteration",
            ],
            "common_stay_or_switch": ["stay"],
            "trend_state": "steady",
        },
        switch_self_regulation_memory={
            "preferred_switch_bias": "stay",
            "stay_effectiveness": "strong",
        },
        post_task_effect_memory={"effect_direction": "mixed"},
        proposal_drift_memory={
            "available": True,
            "drift_state": "stable",
        },
        task_type_priors={
            "top_priority_task_type": "learning",
            "top_priority_score": 0.78,
            "priors": [],
        },
    )

    assert profile["current_judgement"] == "review should dominate until grounding is repaired"
    assert profile["top_self_iteration_domain"] == "grounding"
    assert profile["governance_posture"] == "review"
    assert "secondary_task_shape_hint" not in profile
    assert not any(
        str(item).startswith("secondary_task_shape_hint:")
        for item in list(profile["priority_signals"] or [])
    )


@pytest.mark.unit
def test_lm_generation_context_snapshot_reads_thin_memory_fields_without_common_lists():
    from systems.supervisor.endogenous_drive import EndogenousDriveEngine

    engine = EndogenousDriveEngine()
    snapshot = engine._build_lm_task_generation_context_snapshot(
        evidence_packet={
            "task_type_priors": {
                "top_priority_task_type": "review",
                "top_priority_score": 0.72,
                "priors": [],
            },
            "self_iteration_trend_memory": {
                "available": True,
                "dominant_target": "grounding",
                "trend_state": "steady",
                "target_stability": "stable",
                "dominant_hypothesis": "repair evidence grounding before expansion",
                "stay_or_switch": "stay",
                "switch_reason": "grounding is still the active constraint",
                "target_count": 3,
                "hypothesis_count": 2,
                "stay_or_switch_count": 2,
                "switch_reason_count": 1,
            },
            "cognitive_assessment_memory": {
                "available": True,
                "dominant_constraint": "weak grounding",
                "current_judgement": "review should remain primary",
                "why_not_improvement_now": "improvement would outrun grounding",
                "self_iteration_target": "grounding",
                "self_iteration_hypothesis": "repair evidence grounding first",
                "current_judgement_count": 4,
                "why_not_improvement_now_count": 2,
                "self_iteration_target_count": 3,
                "self_iteration_hypothesis_count": 2,
            },
            "self_iteration_hypotheses": {
                "available": True,
                "dominant_hypothesis": "keep correction narrow",
                "top_target_domain": "grounding",
                "hypothesis_count": 2,
                "top_priority": 0.84,
                "suggested_task_types": ["review", "observation"],
                "hypotheses": [
                    {
                        "target_domain": "legacy-expansion",
                        "hypothesis": "legacy expansion should not become dominant",
                        "priority": 0.33,
                        "suggested_task_types": ["learning"],
                    },
                    {
                        "target_domain": "legacy-memory",
                        "hypothesis": "legacy memory detail should not amplify count",
                        "priority": 0.31,
                        "suggested_task_types": ["memory_maintenance"],
                    },
                    {
                        "target_domain": "legacy-extra",
                        "hypothesis": "legacy extra detail should stay fallback only",
                        "priority": 0.29,
                        "suggested_task_types": ["observation"],
                    },
                ],
            },
        },
        cognition_charter={},
        role="governance_reasoner",
        max_candidates=1,
        status="completed",
        proposal_count=0,
    )

    trend_memory = snapshot["self_iteration_trend_memory"]
    assert trend_memory["dominant_hypothesis"] == "repair evidence grounding before expansion"
    assert trend_memory["dominant_stay_or_switch"] == "stay"
    assert trend_memory["dominant_switch_reason"] == "grounding is still the active constraint"
    assert trend_memory["target_count"] == 3
    assert trend_memory["hypothesis_count"] == 2
    assert trend_memory["stay_or_switch_count"] == 2
    assert trend_memory["switch_reason_count"] == 1
    assert "common_hypotheses" not in trend_memory

    assessment_memory = snapshot["cognitive_assessment_memory"]
    assert assessment_memory["current_judgement"] == "review should remain primary"
    assert assessment_memory["why_not_improvement_now"] == "improvement would outrun grounding"
    assert assessment_memory["self_iteration_target"] == "grounding"
    assert assessment_memory["self_iteration_hypothesis"] == "repair evidence grounding first"
    assert assessment_memory["current_judgement_count"] == 4
    assert assessment_memory["why_not_improvement_now_count"] == 2
    assert assessment_memory["self_iteration_target_count"] == 3
    assert assessment_memory["self_iteration_hypothesis_count"] == 2
    assert "common_current_judgements" not in assessment_memory

    hypothesis_memory = snapshot["self_iteration_hypotheses"]
    assert hypothesis_memory["dominant_hypothesis"] == "keep correction narrow"
    assert hypothesis_memory["top_target_domain"] == "grounding"
    assert hypothesis_memory["hypothesis_count"] == 2
    assert hypothesis_memory["top_priority"] == 0.84
    assert hypothesis_memory["suggested_task_types"] == ["review", "observation"]
    assert "hypotheses" not in hypothesis_memory


@pytest.mark.unit
def test_meta_cognition_profile_is_unavailable_without_real_signals():
    from systems.supervisor.endogenous_drive import EndogenousDriveEngine

    engine = EndogenousDriveEngine()
    profile = engine._build_meta_cognition_profile(
        grounding_focus={
            "grounding_gaps": [],
            "contradictory_topics": [],
        },
        self_iteration_hypotheses={
            "top_target_domain": "",
            "dominant_hypothesis": "",
            "hypotheses": [],
        },
        cognitive_assessment_memory={},
        self_iteration_trend_memory={},
        switch_self_regulation_memory={},
        post_task_effect_memory={},
        proposal_drift_memory={},
        task_type_priors={},
    )

    assert profile == {
        "available": False,
        "summary": "No unified meta-cognition profile is available yet.",
    }


@pytest.mark.unit
def test_meta_cognition_legacy_string_fallback_is_not_split_into_characters(tmp_path):
    from systems.supervisor.endogenous_drive import EndogenousDriveEngine

    engine = EndogenousDriveEngine()
    profile = engine._build_meta_cognition_profile(
        grounding_focus={"grounding_gaps": [], "contradictory_topics": []},
        self_iteration_hypotheses={},
        cognitive_assessment_memory={
            "common_current_judgements": "review should remain primary",
            "common_self_iteration_targets": "grounding",
            "common_self_iteration_hypotheses": "repair grounding first",
            "common_why_not_improvement_now": "improvement would outrun grounding",
        },
        self_iteration_trend_memory={"common_stay_or_switch": "stay"},
        switch_self_regulation_memory={},
        post_task_effect_memory={},
        proposal_drift_memory={},
        task_type_priors={},
    )

    assert profile["current_judgement"] == "review should remain primary"
    assert profile["top_self_iteration_domain"] == "grounding"
    assert profile["top_self_iteration_hypothesis"] == "repair grounding first"
    assert profile["stay_or_switch_bias"] == "stay"

    supervisor = _make_supervisor(tmp_path)
    runtime_profile = supervisor._build_recent_meta_cognition_profile_summary(
        cognitive_assessment_memory={
            "common_current_judgements": "review should remain primary",
            "common_self_iteration_targets": "grounding",
            "common_self_iteration_hypotheses": "repair grounding first",
            "common_why_not_improvement_now": "improvement would outrun grounding",
        },
        self_iteration_trend_memory={"common_stay_or_switch": "stay"},
        switch_self_regulation_memory={},
        post_task_effect_memory={},
        proposal_drift_memory={},
        task_type_priors={},
        recent_reference_alignment={},
    )

    assert runtime_profile["current_judgement"] == "review should remain primary"
    assert runtime_profile["top_self_iteration_domain"] == "grounding"
    assert runtime_profile["top_self_iteration_hypothesis"] == "repair grounding first"
    assert runtime_profile["stay_or_switch_bias"] == "stay"


@pytest.mark.unit
def test_recent_meta_cognition_summary_is_unavailable_without_real_signals(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    profile = supervisor._build_recent_meta_cognition_profile_summary(
        cognitive_assessment_memory={},
        self_iteration_trend_memory={},
        switch_self_regulation_memory={},
        post_task_effect_memory={},
        proposal_drift_memory={},
        task_type_priors={},
        recent_reference_alignment={},
    )

    assert profile == {
        "available": False,
        "summary": "No recent meta-cognition profile is available yet.",
    }


@pytest.mark.unit
def test_judgement_core_keeps_primary_intent_aligned_with_primary_need(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    judgement_core = supervisor._build_endogenous_judgement_core(
        deliberation={
            "reflection": {"dominant_constraint": "queue blockage"},
            "adaptive_policy": {"preferred_focus": "truthfulness"},
            "needs": [
                {
                    "need_type": "repair_truthfulness",
                    "severity": 0.86,
                    "urgency": 0.84,
                    "confidence": 0.82,
                },
                {
                    "need_type": "observe_before_acting",
                    "severity": 0.8,
                    "urgency": 0.78,
                    "confidence": 0.8,
                },
            ],
            "intents": [
                {
                    "intent_type": "observe_before_acting",
                    "priority": 0.92,
                    "source_needs": ["observe_before_acting"],
                },
                {
                    "intent_type": "review_truthfulness_signals",
                    "priority": 0.88,
                    "source_needs": ["repair_truthfulness"],
                },
            ],
        },
        governance_channels={},
        attention_agenda={},
        uncertainty_ledger={},
        observation_program={},
        meta_governance={},
    )

    assert judgement_core["primary_need"]["need_type"] == "repair_truthfulness"
    assert judgement_core["primary_intent"]["intent_type"] == "review_truthfulness_signals"
    assert judgement_core["primary_intent"]["source_needs"] == ["repair_truthfulness"]
    assert "primary_intent=review_truthfulness_signals" in judgement_core["summary"]


@pytest.mark.unit
def test_detect_needs_sorts_primary_need_by_strength_instead_of_append_order():
    from systems.supervisor.endogenous_drive import EndogenousDriveEngine

    engine = EndogenousDriveEngine()
    needs = engine._detect_needs(
        perception=DrivePerceptionSnapshot(
            user_mode="idle_window",
            governor_mode_active=False,
            system_posture="stable",
            active_sessions=0,
            recent_errors=4,
            uncertainty_count=2,
            correction_signals=6,
            learning_quality=25.0,
            has_learning_history=True,
            shell_slot_present=False,
            shell_slot_id="",
            active_queue_count=0,
            queued_learning_count=0,
            queued_body_improvement_count=0,
            stale_queue_count=0,
            pending_review_count=0,
            checks={"has_memory_idle": True, "in_execution_window": True},
            idle_seconds={"user": 1200, "agent": 1200, "memory": 1200},
        ),
        world_model=DriveWorldModel(
            user_mode="idle_window",
            system_posture="stable",
            truthfulness_pressure=0.95,
            learning_momentum=0.18,
            body_upgrade_readiness=0.05,
            queue_health="healthy",
            memory_pressure=0.12,
            self_confidence=0.32,
        ),
        reflection=DriveReflection(
            recent_learning_count=1,
            recent_learning_quality=0.24,
            learning_yield_state="weak",
            queue_blockage_pressure=0.08,
            queue_blockage_state="light",
            body_growth_blocked=False,
            repeated_drive_pressure=0.04,
            autonomy_readiness=0.34,
            dominant_constraint="truthfulness debt",
            rationale="Truthfulness debt dominates this window.",
            source_evidence=[],
        ),
        adaptive_policy=DriveAdaptivePolicy(
            learning_expansion_bias=0.08,
            truthfulness_bias=0.95,
            memory_continuity_bias=0.05,
            queue_hygiene_bias=0.08,
            body_growth_bias=0.0,
            observation_bias=0.22,
            candidate_throttle=0.18,
            candidate_budget=2,
            exploratory_learning_quota=1,
            body_growth_quota=0,
            preferred_focus="truthfulness",
            rationale="Truthfulness is the only justified focus right now.",
            source_evidence=[],
        ),
        memory_plan={"eligible_for_planning": True},
        self_learning_plan={"eligible_for_planning": True},
        self_evolution_plan={"eligible_for_planning": False},
    )

    assert needs[0].need_type == "repair_truthfulness"
    assert needs[0].severity >= needs[1].severity
    assert any(need.need_type == "stabilize_memory_continuity" for need in needs)


@pytest.mark.unit
def test_detect_needs_prefers_observe_before_learning_when_historical_underdelivery_dominates():
    from systems.supervisor.endogenous_drive import EndogenousDriveEngine

    engine = EndogenousDriveEngine()
    needs = engine._detect_needs(
        perception=DrivePerceptionSnapshot(
            user_mode="idle_window",
            governor_mode_active=False,
            system_posture="stable",
            active_sessions=0,
            recent_errors=1,
            uncertainty_count=0,
            correction_signals=1,
            learning_quality=46.0,
            has_learning_history=True,
            shell_slot_present=False,
            shell_slot_id="",
            active_queue_count=0,
            queued_learning_count=0,
            queued_body_improvement_count=0,
            stale_queue_count=0,
            pending_review_count=0,
            checks={"has_memory_idle": True, "in_execution_window": True},
            idle_seconds={"user": 900, "agent": 900, "memory": 900},
        ),
        world_model=DriveWorldModel(
            user_mode="idle_window",
            system_posture="stable",
            truthfulness_pressure=0.275,
            learning_momentum=0.468,
            body_upgrade_readiness=0.322,
            queue_health="clear",
            memory_pressure=0.4,
            self_confidence=0.65,
        ),
        reflection=DriveReflection(
            recent_learning_count=1,
            recent_learning_quality=0.46,
            learning_yield_state="mixed",
            queue_blockage_pressure=0.0,
            queue_blockage_state="clear",
            body_growth_blocked=False,
            repeated_drive_pressure=0.0,
            autonomy_readiness=0.3519,
            dominant_constraint="historical_underdelivery",
            rationale="Historical underdelivery now dominates and should suppress expansion.",
            source_evidence=[],
        ),
        adaptive_policy=DriveAdaptivePolicy(
            learning_expansion_bias=0.44,
            truthfulness_bias=0.73,
            memory_continuity_bias=0.58,
            queue_hygiene_bias=0.51,
            body_growth_bias=0.28,
            observation_bias=0.76,
            candidate_throttle=0.68,
            candidate_budget=1,
            exploratory_learning_quota=0,
            body_growth_quota=0,
            preferred_focus="observation",
            rationale="Observation should dominate under historical underdelivery pressure.",
            source_evidence=[],
        ),
        memory_plan={"eligible_for_planning": True},
        self_learning_plan={"eligible_for_planning": True},
        self_evolution_plan={"eligible_for_planning": True},
    )

    assert needs[0].need_type == "observe_before_acting"
    assert any(need.need_type == "expand_learning_frontier" for need in needs)


@pytest.mark.unit
def test_detect_needs_does_not_let_memory_continuity_override_observation_under_historical_underdelivery():
    from systems.supervisor.endogenous_drive import EndogenousDriveEngine

    engine = EndogenousDriveEngine()
    needs = engine._detect_needs(
        perception=DrivePerceptionSnapshot(
            user_mode="idle_window",
            governor_mode_active=False,
            system_posture="stable",
            active_sessions=0,
            recent_errors=1,
            uncertainty_count=0,
            correction_signals=1,
            learning_quality=46.0,
            has_learning_history=True,
            shell_slot_present=False,
            shell_slot_id="",
            active_queue_count=0,
            queued_learning_count=0,
            queued_body_improvement_count=0,
            stale_queue_count=0,
            pending_review_count=0,
            checks={"has_memory_idle": True, "in_execution_window": True},
            idle_seconds={"user": 900, "agent": 900, "memory": 900},
        ),
        world_model=DriveWorldModel(
            user_mode="idle_window",
            system_posture="stable",
            truthfulness_pressure=0.275,
            learning_momentum=0.468,
            body_upgrade_readiness=0.322,
            queue_health="clear",
            memory_pressure=0.4,
            self_confidence=0.65,
        ),
        reflection=DriveReflection(
            recent_learning_count=1,
            recent_learning_quality=0.46,
            learning_yield_state="mixed",
            queue_blockage_pressure=0.0,
            queue_blockage_state="clear",
            body_growth_blocked=False,
            repeated_drive_pressure=0.0,
            autonomy_readiness=0.3519,
            dominant_constraint="historical_underdelivery",
            rationale="Historical underdelivery now dominates and should slow autonomous output.",
            source_evidence=[],
        ),
        adaptive_policy=DriveAdaptivePolicy(
            learning_expansion_bias=0.44,
            truthfulness_bias=0.73,
            memory_continuity_bias=0.70,
            queue_hygiene_bias=0.51,
            body_growth_bias=0.28,
            observation_bias=0.76,
            candidate_throttle=0.68,
            candidate_budget=1,
            exploratory_learning_quota=0,
            body_growth_quota=0,
            preferred_focus="observation",
            rationale="Observation should dominate under historical underdelivery pressure.",
            source_evidence=[],
        ),
        memory_plan={"eligible_for_planning": True},
        self_learning_plan={"eligible_for_planning": True},
        self_evolution_plan={"eligible_for_planning": True},
    )

    assert needs[0].need_type == "observe_before_acting"
    assert any(need.need_type == "stabilize_memory_continuity" for need in needs)


@pytest.mark.unit
def test_detect_needs_keeps_memory_continuity_primary_before_observation_gate_triggers():
    from systems.supervisor.endogenous_drive import EndogenousDriveEngine

    engine = EndogenousDriveEngine()
    needs = engine._detect_needs(
        perception=DrivePerceptionSnapshot(
            user_mode="idle_window",
            governor_mode_active=False,
            system_posture="stable",
            active_sessions=0,
            recent_errors=1,
            uncertainty_count=0,
            correction_signals=1,
            learning_quality=46.0,
            has_learning_history=True,
            shell_slot_present=False,
            shell_slot_id="",
            active_queue_count=0,
            queued_learning_count=0,
            queued_body_improvement_count=0,
            stale_queue_count=0,
            pending_review_count=0,
            checks={"has_memory_idle": True, "in_execution_window": True},
            idle_seconds={"user": 900, "agent": 900, "memory": 900},
        ),
        world_model=DriveWorldModel(
            user_mode="idle_window",
            system_posture="stable",
            truthfulness_pressure=0.275,
            learning_momentum=0.468,
            body_upgrade_readiness=0.322,
            queue_health="clear",
            memory_pressure=0.4,
            self_confidence=0.65,
        ),
        reflection=DriveReflection(
            recent_learning_count=1,
            recent_learning_quality=0.46,
            learning_yield_state="mixed",
            queue_blockage_pressure=0.0,
            queue_blockage_state="clear",
            body_growth_blocked=False,
            repeated_drive_pressure=0.0,
            autonomy_readiness=0.4386,
            dominant_constraint="historical_underdelivery",
            rationale="Historical underdelivery is present but the observation gate has not yet fully triggered.",
            source_evidence=[],
        ),
        adaptive_policy=DriveAdaptivePolicy(
            learning_expansion_bias=0.44,
            truthfulness_bias=0.73,
            memory_continuity_bias=0.58,
            queue_hygiene_bias=0.51,
            body_growth_bias=0.28,
            observation_bias=0.52,
            candidate_throttle=0.54,
            candidate_budget=2,
            exploratory_learning_quota=1,
            body_growth_quota=0,
            preferred_focus="truthfulness",
            rationale="Moderate underdelivery alone should not fully flip the drive into observation-first mode.",
            source_evidence=[],
        ),
        memory_plan={"eligible_for_planning": True},
        self_learning_plan={"eligible_for_planning": True},
        self_evolution_plan={"eligible_for_planning": True},
    )

    assert needs[0].need_type == "stabilize_memory_continuity"
    assert all(need.need_type != "observe_before_acting" for need in needs)


@pytest.mark.unit
def test_detect_needs_enters_observation_when_historical_underdelivery_and_observation_bias_are_already_high():
    from systems.supervisor.endogenous_drive import EndogenousDriveEngine

    engine = EndogenousDriveEngine()
    needs = engine._detect_needs(
        perception=DrivePerceptionSnapshot(
            user_mode="idle_window",
            governor_mode_active=False,
            system_posture="stable",
            active_sessions=0,
            recent_errors=1,
            uncertainty_count=0,
            correction_signals=1,
            learning_quality=46.0,
            has_learning_history=True,
            shell_slot_present=False,
            shell_slot_id="",
            active_queue_count=0,
            queued_learning_count=0,
            queued_body_improvement_count=0,
            stale_queue_count=0,
            pending_review_count=0,
            checks={"has_memory_idle": True, "in_execution_window": True},
            idle_seconds={"user": 900, "agent": 900, "memory": 900},
        ),
        world_model=DriveWorldModel(
            user_mode="idle_window",
            system_posture="stable",
            truthfulness_pressure=0.275,
            learning_momentum=0.468,
            body_upgrade_readiness=0.322,
            queue_health="clear",
            memory_pressure=0.4,
            self_confidence=0.65,
        ),
        reflection=DriveReflection(
            recent_learning_count=1,
            recent_learning_quality=0.46,
            learning_yield_state="mixed",
            queue_blockage_pressure=0.0,
            queue_blockage_state="clear",
            body_growth_blocked=False,
            repeated_drive_pressure=0.0,
            autonomy_readiness=0.4386,
            dominant_constraint="historical_underdelivery",
            rationale="Historical underdelivery is already present and observation pressure is elevated.",
            source_evidence=[],
        ),
        adaptive_policy=DriveAdaptivePolicy(
            learning_expansion_bias=0.44,
            truthfulness_bias=0.73,
            memory_continuity_bias=0.58,
            queue_hygiene_bias=0.51,
            body_growth_bias=0.28,
            observation_bias=0.7184,
            candidate_throttle=0.54,
            candidate_budget=2,
            exploratory_learning_quota=1,
            body_growth_quota=0,
            preferred_focus="truthfulness",
            rationale="Observation pressure is already elevated even if the nominal focus has not flipped yet.",
            source_evidence=[],
        ),
        memory_plan={"eligible_for_planning": True},
        self_learning_plan={"eligible_for_planning": True},
        self_evolution_plan={"eligible_for_planning": True},
    )

    assert needs[0].need_type == "observe_before_acting"
    assert any(need.need_type == "stabilize_memory_continuity" for need in needs)


@pytest.mark.unit
def test_detect_needs_keeps_historical_underdelivery_boundary_deterministic_for_same_inputs():
    from systems.supervisor.endogenous_drive import EndogenousDriveEngine

    engine = EndogenousDriveEngine()

    def _run_once():
        return engine._detect_needs(
            perception=DrivePerceptionSnapshot(
                user_mode="idle_window",
                governor_mode_active=False,
                system_posture="stable",
                active_sessions=0,
                recent_errors=1,
                uncertainty_count=0,
                correction_signals=1,
                learning_quality=46.0,
                has_learning_history=True,
                shell_slot_present=False,
                shell_slot_id="",
                active_queue_count=0,
                queued_learning_count=0,
                queued_body_improvement_count=0,
                stale_queue_count=0,
                pending_review_count=0,
                checks={"has_memory_idle": True, "in_execution_window": True},
                idle_seconds={"user": 900, "agent": 900, "memory": 900},
            ),
            world_model=DriveWorldModel(
                user_mode="idle_window",
                system_posture="stable",
                truthfulness_pressure=0.275,
                learning_momentum=0.468,
                body_upgrade_readiness=0.322,
                queue_health="clear",
                memory_pressure=0.4,
                self_confidence=0.65,
            ),
            reflection=DriveReflection(
                recent_learning_count=1,
                recent_learning_quality=0.46,
                learning_yield_state="mixed",
                queue_blockage_pressure=0.0,
                queue_blockage_state="clear",
                body_growth_blocked=False,
                repeated_drive_pressure=0.0,
                autonomy_readiness=0.4386,
                dominant_constraint="historical_underdelivery",
                rationale="Boundary scan should stay deterministic under identical input.",
                source_evidence=[],
            ),
            adaptive_policy=DriveAdaptivePolicy(
                learning_expansion_bias=0.44,
                truthfulness_bias=0.73,
                memory_continuity_bias=0.58,
                queue_hygiene_bias=0.51,
                body_growth_bias=0.28,
                observation_bias=0.68,
                candidate_throttle=0.54,
                candidate_budget=2,
                exploratory_learning_quota=1,
                body_growth_quota=0,
                preferred_focus="truthfulness",
                rationale="Boundary scan should stay deterministic under identical input.",
                source_evidence=[],
            ),
            memory_plan={"eligible_for_planning": True},
            self_learning_plan={"eligible_for_planning": True},
            self_evolution_plan={"eligible_for_planning": True},
        )

    first = _run_once()
    second = _run_once()

    assert [need.need_type for need in first] == [need.need_type for need in second]
    assert first[0].need_type == "stabilize_memory_continuity"
    assert first[1].need_type == "observe_before_acting"


@pytest.mark.unit
def test_detect_needs_crosses_from_memory_to_observation_monotonically_near_historical_underdelivery_boundary():
    from systems.supervisor.endogenous_drive import EndogenousDriveEngine

    engine = EndogenousDriveEngine()

    def _top_needs(observation_bias: float):
        needs = engine._detect_needs(
            perception=DrivePerceptionSnapshot(
                user_mode="idle_window",
                governor_mode_active=False,
                system_posture="stable",
                active_sessions=0,
                recent_errors=1,
                uncertainty_count=0,
                correction_signals=1,
                learning_quality=46.0,
                has_learning_history=True,
                shell_slot_present=False,
                shell_slot_id="",
                active_queue_count=0,
                queued_learning_count=0,
                queued_body_improvement_count=0,
                stale_queue_count=0,
                pending_review_count=0,
                checks={"has_memory_idle": True, "in_execution_window": True},
                idle_seconds={"user": 900, "agent": 900, "memory": 900},
            ),
            world_model=DriveWorldModel(
                user_mode="idle_window",
                system_posture="stable",
                truthfulness_pressure=0.275,
                learning_momentum=0.468,
                body_upgrade_readiness=0.322,
                queue_health="clear",
                memory_pressure=0.4,
                self_confidence=0.65,
            ),
            reflection=DriveReflection(
                recent_learning_count=1,
                recent_learning_quality=0.46,
                learning_yield_state="mixed",
                queue_blockage_pressure=0.0,
                queue_blockage_state="clear",
                body_growth_blocked=False,
                repeated_drive_pressure=0.0,
                autonomy_readiness=0.4386,
                dominant_constraint="historical_underdelivery",
                rationale="Observation should only overtake memory continuity after the boundary is genuinely crossed.",
                source_evidence=[],
            ),
            adaptive_policy=DriveAdaptivePolicy(
                learning_expansion_bias=0.44,
                truthfulness_bias=0.73,
                memory_continuity_bias=0.58,
                queue_hygiene_bias=0.51,
                body_growth_bias=0.28,
                observation_bias=observation_bias,
                candidate_throttle=0.54,
                candidate_budget=2,
                exploratory_learning_quota=1,
                body_growth_quota=0,
                preferred_focus="truthfulness",
                rationale="Boundary scan.",
                source_evidence=[],
            ),
            memory_plan={"eligible_for_planning": True},
            self_learning_plan={"eligible_for_planning": True},
            self_evolution_plan={"eligible_for_planning": True},
        )
        return needs

    low_gate = _top_needs(0.52)
    mid_gate = _top_needs(0.68)
    high_gate = _top_needs(0.7184)

    assert low_gate[0].need_type == "stabilize_memory_continuity"
    assert all(need.need_type != "observe_before_acting" for need in low_gate)

    assert mid_gate[0].need_type == "stabilize_memory_continuity"
    assert any(need.need_type == "observe_before_acting" for need in mid_gate)

    assert high_gate[0].need_type == "observe_before_acting"
    assert any(need.need_type == "stabilize_memory_continuity" for need in high_gate)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_single_recovery_outcome_does_not_immediately_flip_primary_need_out_of_historical_underdelivery(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Retry self-learning H",
            "event_type": "decision",
            "status": "retry",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.20,
        },
        {
            "title": "Deferred self-learning G",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.24,
        },
        {
            "title": "Recovered self-learning F",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.83,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "historical_underdelivery"
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "observation"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_memory_maintenance_recovery_does_not_clear_self_learning_historical_underdelivery(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered memory maintenance D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "memory_maintenance",
            "governance_task_type": "memory_maintenance",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered memory maintenance E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "memory_maintenance",
            "governance_task_type": "memory_maintenance",
            "quality_score": 0.79,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "historical_underdelivery"
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"
    assert "historical_scope=self_learning" in result["deliberation"]["reflection"]["source_evidence"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_two_self_learning_recoveries_can_clear_historical_underdelivery(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "none"
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] != "observe_before_acting"
    assert "historical_scope=self_learning" in result["deliberation"]["reflection"]["source_evidence"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cleared_historical_underdelivery_does_not_reenter_observation_from_single_queue_retry(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
        {
            "title": "Retried queue item F",
            "event_type": "decision",
            "status": "retry",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "none"
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "expand_learning_frontier"
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] != "observation"
    assert result["deliberation"]["adaptive_policy"]["candidate_budget"] == 4


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cleared_historical_underdelivery_allows_truthfulness_to_take_primary_need_at_review_threshold(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 2, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "none"
    assert result["deliberation"]["perception"]["correction_signals"] == 3
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "repair_truthfulness"
    assert result["cognition_state"]["judgement_core"]["primary_intent"]["intent_type"] == "review_truthfulness_signals"
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "truthfulness"
    assert result["deliberation"]["adaptive_policy"]["candidate_budget"] == 4


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cleared_historical_underdelivery_keeps_learning_primary_under_weak_truthfulness_without_real_queue_debt(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 0, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    candidate_kinds = [
        str(
            dict(dict(candidate.get("metadata") or {}).get("score_breakdown") or {}).get(
                "candidate_kind"
            )
            or ""
        ).strip()
        for candidate in result["candidates"]
    ]

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "none"
    assert result["deliberation"]["perception"]["correction_signals"] == 1
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "expand_learning_frontier"
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "truthfulness"
    assert "queue_hygiene_review" not in candidate_kinds


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cleared_historical_underdelivery_shifts_to_memory_continuity_when_real_queue_debt_exists_below_truthfulness_threshold(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Queue debt probe",
            "summary": "probe",
        }
    )
    task_id = planned["tasks"][0]["task_id"]
    supervisor._self_evolution_queue.update_status(task_id, status="deferred", reason="probe")

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 0, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    candidate_kinds = [
        str(
            dict(dict(candidate.get("metadata") or {}).get("score_breakdown") or {}).get(
                "candidate_kind"
            )
            or ""
        ).strip()
        for candidate in result["candidates"]
    ]

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "none"
    assert result["deliberation"]["perception"]["correction_signals"] == 1
    assert result["deliberation"]["perception"]["pending_review_count"] == 1
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "stabilize_memory_continuity"
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "truthfulness"
    assert "queue_hygiene_review" in candidate_kinds


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cleared_historical_underdelivery_with_light_queue_debt_does_not_jump_to_observation_or_queue_primary_without_truthfulness_pressure(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Queue debt probe",
            "summary": "probe",
        }
    )
    task_id = planned["tasks"][0]["task_id"]
    supervisor._self_evolution_queue.update_status(task_id, status="deferred", reason="probe")

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {"error_count": 0, "uncertainty_high_count": 0}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "none"
    assert result["deliberation"]["perception"]["correction_signals"] == 0
    assert result["deliberation"]["perception"]["pending_review_count"] == 1
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "stabilize_memory_continuity"
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "memory_continuity"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_mixed_recovery_history_does_not_let_memory_need_override_observation_when_historical_underdelivery_still_dominates(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
        {
            "title": "Recovered memory maintenance F",
            "event_type": "decision",
            "status": "completed",
            "task_family": "memory_maintenance",
            "governance_task_type": "memory_maintenance",
            "quality_score": 0.81,
        },
        {
            "title": "Retried queue item G",
            "event_type": "decision",
            "status": "retry",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
        {
            "title": "Deferred self-learning H",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.21,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "historical_underdelivery"
    assert result["deliberation"]["adaptive_policy"]["candidate_budget"] == 1
    assert result["deliberation"]["adaptive_policy"]["observation_bias"] >= 0.72
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_self_learning_recovery_then_block_again_keeps_preferred_focus_aligned_with_observation(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
        {
            "title": "Recovered memory maintenance F",
            "event_type": "decision",
            "status": "completed",
            "task_family": "memory_maintenance",
            "governance_task_type": "memory_maintenance",
            "quality_score": 0.83,
        },
        {
            "title": "Deferred self-learning G",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.24,
        },
        {
            "title": "Retry self-learning H",
            "event_type": "decision",
            "status": "retry",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.20,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "historical_underdelivery"
    assert result["deliberation"]["adaptive_policy"]["observation_bias"] >= 0.72
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "observation"
    assert result["deliberation"]["adaptive_policy"]["candidate_budget"] == 1
    assert result["deliberation"]["adaptive_policy"]["exploratory_learning_quota"] == 0
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_recent_self_learning_relapse_reenters_historical_underdelivery_after_recovery_chain(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Retry self-learning H",
            "event_type": "decision",
            "status": "retry",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.20,
        },
        {
            "title": "Deferred self-learning G",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.24,
        },
        {
            "title": "Recovered self-learning F",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.83,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered memory maintenance I",
            "event_type": "decision",
            "status": "completed",
            "task_family": "memory_maintenance",
            "governance_task_type": "memory_maintenance",
            "quality_score": 0.84,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "historical_underdelivery"
    assert "recent_relapse_drag_count=2" in result["deliberation"]["reflection"]["source_evidence"]
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "observation"
    assert result["deliberation"]["adaptive_policy"]["observation_bias"] >= 0.68
    assert result["deliberation"]["adaptive_policy"]["exploratory_learning_quota"] == 0
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_recorded_at_normalization_keeps_historical_underdelivery_stable_across_outcome_orderings(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    newest_first = [
        {
            "title": "Retry self-learning H",
            "event_type": "decision",
            "status": "retry",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.20,
            "recorded_at": "2026-06-28T08:00:00+00:00",
        },
        {
            "title": "Deferred self-learning G",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.24,
            "recorded_at": "2026-06-28T07:00:00+00:00",
        },
        {
            "title": "Recovered self-learning F",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.83,
            "recorded_at": "2026-06-28T06:00:00+00:00",
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
            "recorded_at": "2026-06-28T05:00:00+00:00",
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
            "recorded_at": "2026-06-28T04:00:00+00:00",
        },
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
            "recorded_at": "2026-06-28T03:00:00+00:00",
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
            "recorded_at": "2026-06-28T02:00:00+00:00",
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
            "recorded_at": "2026-06-28T01:00:00+00:00",
        },
        {
            "title": "Recovered memory maintenance I",
            "event_type": "decision",
            "status": "completed",
            "task_family": "memory_maintenance",
            "governance_task_type": "memory_maintenance",
            "quality_score": 0.84,
            "recorded_at": "2026-06-28T00:30:00+00:00",
        },
    ]
    scrambled_same_facts = [
        newest_first[5],
        newest_first[6],
        newest_first[7],
        newest_first[4],
        newest_first[3],
        newest_first[2],
        newest_first[1],
        newest_first[0],
        newest_first[8],
    ]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    first_history = supervisor._endogenous_drive_history_default()
    first_history["outcomes"] = newest_first
    supervisor._persist_endogenous_drive_history(first_history)
    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        first = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    second_history = supervisor._endogenous_drive_history_default()
    second_history["outcomes"] = scrambled_same_facts
    supervisor._persist_endogenous_drive_history(second_history)
    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        second = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert first["deliberation"]["reflection"]["dominant_constraint"] == "historical_underdelivery"
    assert second["deliberation"]["reflection"]["dominant_constraint"] == "historical_underdelivery"
    assert first["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"
    assert second["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"
    assert first["deliberation"]["adaptive_policy"]["preferred_focus"] == "observation"
    assert second["deliberation"]["adaptive_policy"]["preferred_focus"] == "observation"
    assert first["deliberation"]["reflection"]["source_evidence"] == second["deliberation"]["reflection"]["source_evidence"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_legacy_history_without_timestamps_stays_conservative_across_orderings(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    newest_first = [
        {
            "title": "Retry self-learning H",
            "event_type": "decision",
            "status": "retry",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.20,
        },
        {
            "title": "Deferred self-learning G",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.24,
        },
        {
            "title": "Recovered self-learning F",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.83,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered memory maintenance I",
            "event_type": "decision",
            "status": "completed",
            "task_family": "memory_maintenance",
            "governance_task_type": "memory_maintenance",
            "quality_score": 0.84,
        },
    ]
    recovery_front_loaded = [
        newest_first[2],
        newest_first[3],
        newest_first[4],
        newest_first[0],
        newest_first[1],
        newest_first[5],
        newest_first[6],
        newest_first[7],
        newest_first[8],
    ]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    first_history = supervisor._endogenous_drive_history_default()
    first_history["outcomes"] = newest_first
    supervisor._persist_endogenous_drive_history(first_history)
    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        first = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    second_history = supervisor._endogenous_drive_history_default()
    second_history["outcomes"] = recovery_front_loaded
    supervisor._persist_endogenous_drive_history(second_history)
    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        second = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert first["deliberation"]["reflection"]["dominant_constraint"] == "historical_underdelivery"
    assert second["deliberation"]["reflection"]["dominant_constraint"] == "historical_underdelivery"
    assert first["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"
    assert second["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"
    assert first["deliberation"]["adaptive_policy"]["preferred_focus"] == "observation"
    assert second["deliberation"]["adaptive_policy"]["preferred_focus"] == "observation"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_recent_relapse_retightens_candidate_budget_in_longer_mixed_history(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Retry self-learning H",
            "event_type": "decision",
            "status": "retry",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.20,
        },
        {
            "title": "Deferred self-learning G",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.24,
        },
        {
            "title": "Recovered self-learning F",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.83,
        },
        {
            "title": "Recovered self-learning E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.79,
        },
        {
            "title": "Recovered self-learning D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered self-learning M",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.77,
        },
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Recovered memory maintenance I",
            "event_type": "decision",
            "status": "completed",
            "task_family": "memory_maintenance",
            "governance_task_type": "memory_maintenance",
            "quality_score": 0.84,
        },
        {
            "title": "Retried queue item K",
            "event_type": "decision",
            "status": "retry",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
        {
            "title": "Deferred queue item L",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.21,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "historical_underdelivery"
    assert "recent_relapse_drag_count=2" in result["deliberation"]["reflection"]["source_evidence"]
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "observation"
    assert result["deliberation"]["adaptive_policy"]["candidate_budget"] == 1
    assert result["deliberation"]["adaptive_policy"]["exploratory_learning_quota"] == 0
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_recent_completed_sequence_releases_observation_after_long_dirty_status_history(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Recovered self-learning 5",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.84,
            "recorded_at": "2026-06-28T12:00:00+00:00",
        },
        {
            "title": "Recovered self-learning 4",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.80,
            "recorded_at": "2026-06-28T11:00:00+00:00",
        },
        {
            "title": "Recovered self-learning 3",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.78,
            "recorded_at": "2026-06-28T10:00:00+00:00",
        },
        {
            "title": "Recovered self-learning 2",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.76,
            "recorded_at": "2026-06-28T09:00:00+00:00",
        },
        {
            "title": "Recovered self-learning 1",
            "event_type": "decision",
            "status": "completed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.74,
            "recorded_at": "2026-06-28T08:00:00+00:00",
        },
        {
            "title": "Paused self-learning old",
            "event_type": "decision",
            "status": "paused",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.24,
            "recorded_at": "2026-06-28T07:00:00+00:00",
        },
        {
            "title": "Retry self-learning old",
            "event_type": "decision",
            "status": "retry",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
            "recorded_at": "2026-06-28T06:00:00+00:00",
        },
        {
            "title": "Awaiting review self-learning old",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
            "recorded_at": "2026-06-28T05:00:00+00:00",
        },
        {
            "title": "Deferred self-learning old",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.26,
            "recorded_at": "2026-06-28T04:00:00+00:00",
        },
        {
            "title": "Failed self-learning old",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.20,
            "recorded_at": "2026-06-28T03:00:00+00:00",
        },
        {
            "title": "Completed queue hygiene",
            "event_type": "decision",
            "status": "completed",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.70,
            "recorded_at": "2026-06-28T02:00:00+00:00",
        },
        {
            "title": "Deferred queue hygiene",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.30,
            "recorded_at": "2026-06-28T01:00:00+00:00",
        },
    ]
    history["strategy_memory"] = {
        "focus_stats": {
            "observation": {"judged": 8, "completed": 2, "failed": 1, "dragging": 5},
            "learning_expansion": {"judged": 7, "completed": 5, "failed": 1, "dragging": 1},
        },
        "contextual_focus_stats": {
            "idle_window|stable|none": {
                "learning_expansion": {"judged": 5, "completed": 4, "failed": 0, "dragging": 1},
            }
        },
    }
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [
                {
                    "title": "Recent recovered learning",
                    "quality_score": 0.78,
                    "completed_at": "2026-06-28T12:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["reflection"]["dominant_constraint"] != "historical_underdelivery"
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] != "observe_before_acting"
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] != "observation"
    assert result["deliberation"]["adaptive_policy"]["candidate_budget"] >= 2
    assert result["deliberation"]["reflection"]["source_evidence"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_recent_relapse_reenters_observation_after_recovery_despite_stale_strategy_and_decayed_self_regulation(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        _self_learning_outcome(
            title,
            status,
            quality_score=quality,
            recorded_hour=hour,
        )
        for title, status, hour, quality in (
            ("Retry self-learning after recovery", "retry", 12, 0.22),
            ("Deferred self-learning after recovery", "deferred", 11, 0.24),
            ("Completed self-learning during relapse window", "completed", 10, 0.72),
            ("Recovered self-learning 3", "completed", 9, 0.82),
            ("Recovered self-learning 2", "completed", 8, 0.80),
            ("Recovered self-learning 1", "completed", 7, 0.78),
            ("Paused self-learning old", "paused", 6, 0.30),
            ("Awaiting review self-learning old", "awaiting_review", 5, 0.28),
            ("Failed self-learning old", "failed", 4, 0.20),
        )
    ] + [
        {
            "title": "Completed queue hygiene old",
            "event_type": "decision",
            "status": "completed",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.70,
            "recorded_at": "2026-06-28T03:00:00+00:00",
        },
    ]
    history["strategy_memory"] = {
        "focus_stats": {
            "learning_expansion": {"judged": 12, "completed": 10, "failed": 1, "dragging": 1},
            "observation": {"judged": 8, "completed": 7, "failed": 0, "dragging": 0},
        },
        "contextual_focus_stats": {
            "idle_window|stable|none": {
                "learning_expansion": {"judged": 8, "completed": 7, "failed": 0, "dragging": 1},
                "observation": {"judged": 6, "completed": 6, "failed": 0, "dragging": 0},
            }
        },
        "observation_target_stats": {
            "historical_underdelivery": {
                "seen": 10,
                "recommended": 8,
                "resolved": 8,
                "stalled": 0,
                "last_status": "resolved",
            }
        },
    }
    supervisor._persist_endogenous_drive_history(history)

    regulation_snapshot = supervisor._endogenous_self_regulation_default()
    regulation_snapshot["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
    regulation_snapshot["dynamic_candidate_throttle_boost"] = 0.28
    regulation_snapshot["dynamic_observation_bias_boost"] = 0.24
    regulation_snapshot["dynamic_learning_expansion_suppression"] = 0.18
    regulation_snapshot["last_reason"] = "stale_observation_carryover"
    supervisor._get_endogenous_self_regulation_path().write_text(
        json.dumps(regulation_snapshot),
        encoding="utf-8",
    )

    async def fake_idle_window(_request=None):
        return _endogenous_idle_window_payload(
            quality_score=0.72,
            completed_title="Recent mixed recovery learning",
            completed_at="2026-06-28T10:00:00+00:00",
        )

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    source_evidence = result["deliberation"]["reflection"]["source_evidence"]
    adaptive_policy = result["deliberation"]["adaptive_policy"]

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "historical_underdelivery"
    assert "recent_relapse_drag_count=2" in source_evidence
    assert "stale_observation_carryover" not in str(result["self_regulation"].get("last_reason") or "")
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"
    assert adaptive_policy["preferred_focus"] == "observation"
    assert adaptive_policy["candidate_budget"] == 1
    assert adaptive_policy["exploratory_learning_quota"] == 0


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.stability_longcycle
async def test_alternating_recovery_and_relapse_reacts_under_continuous_strategy_writeback(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    def build_history_with_strategy(outcomes: list[dict], strategy_memory: dict) -> dict:
        history = supervisor._endogenous_drive_history_default()
        history["outcomes"] = outcomes
        history["strategy_memory"] = json.loads(json.dumps(strategy_memory))
        return history

    def install_decayed_self_regulation() -> None:
        regulation_snapshot = supervisor._endogenous_self_regulation_default()
        regulation_snapshot["updated_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=7)
        ).isoformat()
        regulation_snapshot["dynamic_candidate_throttle_boost"] = 0.24
        regulation_snapshot["dynamic_observation_bias_boost"] = 0.22
        regulation_snapshot["dynamic_learning_expansion_suppression"] = 0.16
        regulation_snapshot["last_reason"] = "old alternating-cycle guard"
        supervisor._get_endogenous_self_regulation_path().write_text(
            json.dumps(regulation_snapshot),
            encoding="utf-8",
        )

    def idle_payload(*, quality: float, error_count: int = 0, uncertainty_count: int = 0) -> dict:
        return _endogenous_idle_window_payload(
            quality_score=quality,
            completed_title="Recent alternating learning",
            completed_at="2026-06-28T12:00:00+00:00",
            error_count=error_count,
            uncertainty_count=uncertainty_count,
        )

    async def stable_idle_window(_request=None):
        return idle_payload(quality=0.84)

    async def strained_idle_window(_request=None):
        return idle_payload(quality=0.46, error_count=2, uncertainty_count=1)

    writeback_sequence = [
        (stable_idle_window, "completed"),
        (strained_idle_window, "failed"),
        (stable_idle_window, "completed"),
        (strained_idle_window, "deferred"),
        (stable_idle_window, "completed"),
    ]
    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        for idle_window_fn, status in writeback_sequence:
            supervisor.evaluate_idle_window = idle_window_fn  # type: ignore[method-assign]
            await _plan_and_write_back_endogenous_cycle(
                supervisor,
                outcome_status=status,
                reason=f"alternating writeback {status}",
                allow_empty_candidates=True,
            )

    accumulated_strategy = supervisor._load_endogenous_drive_history()["strategy_memory"]
    focus_stats = accumulated_strategy["focus_stats"]
    contextual_stats = accumulated_strategy["contextual_focus_stats"]
    assert sum(bucket.get("judged", 0) for bucket in focus_stats.values()) >= 3
    assert len(contextual_stats) >= 2

    relapse_outcomes = [
        _self_learning_outcome(
            title,
            status,
            quality_score=quality,
            recorded_hour=hour,
        )
        for title, status, hour, quality in (
            ("Retry after recovery", "retry", 12, 0.22),
            ("Deferred after recovery", "deferred", 11, 0.24),
            ("Completed inside relapse window", "completed", 10, 0.72),
            ("Recovered C", "completed", 9, 0.84),
            ("Recovered B", "completed", 8, 0.80),
            ("Recovered A", "completed", 7, 0.78),
            ("Paused old", "paused", 6, 0.30),
            ("Failed old", "failed", 5, 0.20),
        )
    ]
    supervisor._persist_endogenous_drive_history(
        build_history_with_strategy(relapse_outcomes, accumulated_strategy)
    )
    install_decayed_self_regulation()
    supervisor.evaluate_idle_window = stable_idle_window  # type: ignore[method-assign]

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        relapse_result = await supervisor.evaluate_endogenous_drive(
            {"record_activity": False, "persist_evaluation": False}
        )

    assert (
        relapse_result["deliberation"]["reflection"]["dominant_constraint"]
        == "historical_underdelivery"
    )
    assert "recent_relapse_drag_count=2" in relapse_result["deliberation"]["reflection"]["source_evidence"]
    assert relapse_result["deliberation"]["adaptive_policy"]["preferred_focus"] == "observation"
    assert relapse_result["deliberation"]["adaptive_policy"]["candidate_budget"] == 1
    assert (
        relapse_result["cognition_state"]["judgement_core"]["primary_need"]["need_type"]
        == "observe_before_acting"
    )

    recovered_outcomes = [
        _self_learning_outcome(
            title,
            status,
            quality_score=quality,
            recorded_hour=hour,
        )
        for title, status, hour, quality in (
            ("Recovered final E", "completed", 12, 0.86),
            ("Recovered final D", "completed", 11, 0.84),
            ("Recovered final C", "completed", 10, 0.82),
            ("Recovered final B", "completed", 9, 0.80),
            ("Recovered final A", "completed", 8, 0.78),
            ("Retry old", "retry", 7, 0.24),
            ("Deferred old", "deferred", 6, 0.26),
            ("Failed old", "failed", 5, 0.20),
        )
    ]
    supervisor._persist_endogenous_drive_history(
        build_history_with_strategy(recovered_outcomes, accumulated_strategy)
    )
    install_decayed_self_regulation()
    supervisor.evaluate_idle_window = stable_idle_window  # type: ignore[method-assign]

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        recovered_result = await supervisor.evaluate_endogenous_drive(
            {"record_activity": False, "persist_evaluation": False}
        )

    assert recovered_result["deliberation"]["reflection"]["dominant_constraint"] != (
        "historical_underdelivery"
    )
    assert (
        recovered_result["cognition_state"]["judgement_core"]["primary_need"]["need_type"]
        != "observe_before_acting"
    )
    assert recovered_result["deliberation"]["adaptive_policy"]["preferred_focus"] != "observation"
    assert recovered_result["deliberation"]["adaptive_policy"]["candidate_budget"] >= 2


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.stability_longcycle
async def test_long_dirty_history_switches_between_relapse_tightening_and_recovery_release_under_same_strategy_memory(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    async def stable_idle_window(_request=None):
        return _endogenous_idle_window_payload(
            quality_score=0.84,
            completed_title="Stable long-span learning",
            completed_at="2026-06-29T12:00:00+00:00",
        )

    async def strained_idle_window(_request=None):
        return _endogenous_idle_window_payload(
            quality_score=0.38,
            completed_title="Strained long-span learning",
            completed_at="2026-06-29T12:00:00+00:00",
            error_count=2,
            uncertainty_count=1,
        )

    writeback_sequence = [
        (stable_idle_window, "completed"),
        (strained_idle_window, "failed"),
        (stable_idle_window, "completed"),
        (strained_idle_window, "deferred"),
        (stable_idle_window, "completed"),
        (stable_idle_window, "completed"),
    ]
    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        for idle_window_fn, status in writeback_sequence:
            supervisor.evaluate_idle_window = idle_window_fn  # type: ignore[method-assign]
            await _plan_and_write_back_endogenous_cycle(
                supervisor,
                outcome_status=status,
                reason=f"long dirty history writeback {status}",
                allow_empty_candidates=True,
            )

    accumulated_strategy = json.loads(
        json.dumps(supervisor._load_endogenous_drive_history()["strategy_memory"])
    )
    assert accumulated_strategy["focus_stats"]
    assert accumulated_strategy["contextual_focus_stats"]

    def install_decayed_self_regulation() -> None:
        regulation_snapshot = supervisor._endogenous_self_regulation_default()
        regulation_snapshot["updated_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=9)
        ).isoformat()
        regulation_snapshot["dynamic_candidate_throttle_boost"] = 0.26
        regulation_snapshot["dynamic_observation_bias_boost"] = 0.23
        regulation_snapshot["dynamic_learning_expansion_suppression"] = 0.17
        regulation_snapshot["last_reason"] = "old long-span carryover"
        supervisor._get_endogenous_self_regulation_path().write_text(
            json.dumps(regulation_snapshot),
            encoding="utf-8",
        )

    def install_history(outcomes: list[dict]) -> None:
        history = supervisor._endogenous_drive_history_default()
        history["outcomes"] = json.loads(json.dumps(outcomes))
        history["strategy_memory"] = json.loads(json.dumps(accumulated_strategy))
        supervisor._persist_endogenous_drive_history(history)
        install_decayed_self_regulation()

    dirty_relapse_outcomes = [
        _self_learning_outcome("Retry latest self-learning", "retry", quality_score=0.22, recorded_hour=23),
        _self_learning_outcome("Deferred latest self-learning", "deferred", quality_score=0.24, recorded_hour=22),
        _self_learning_outcome("Completed inside relapse window", "completed", quality_score=0.72, recorded_hour=21),
        {
            "title": "Planned-only observation should not count as recovery",
            "event_type": "planned",
            "status": "planned",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.88,
            "recorded_at": "2026-06-29T20:00:00+00:00",
        },
        _self_learning_outcome("Recovered old C", "completed", quality_score=0.82, recorded_hour=19),
        _self_learning_outcome("Recovered old B", "completed", quality_score=0.80, recorded_hour=18),
        _self_learning_outcome("Recovered old A", "completed", quality_score=0.78, recorded_hour=17),
        _self_learning_outcome("Paused old self-learning", "paused", quality_score=0.30, recorded_hour=16),
        _self_learning_outcome("Failed old self-learning", "failed", quality_score=0.20, recorded_hour=15),
        {
            "title": "Completed memory cleanup old",
            "event_type": "decision",
            "status": "completed",
            "task_family": "memory_maintenance",
            "governance_task_type": "memory_maintenance",
            "quality_score": 0.76,
            "recorded_at": "2026-06-29T14:00:00+00:00",
        },
    ]
    install_history(dirty_relapse_outcomes)
    supervisor.evaluate_idle_window = stable_idle_window  # type: ignore[method-assign]

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        relapse_result = await supervisor.evaluate_endogenous_drive(
            {"record_activity": False, "persist_evaluation": False}
        )

    assert relapse_result["deliberation"]["reflection"]["dominant_constraint"] == (
        "historical_underdelivery"
    )
    assert "recent_relapse_drag_count=2" in relapse_result["deliberation"]["reflection"]["source_evidence"]
    assert relapse_result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == (
        "observe_before_acting"
    )
    assert relapse_result["deliberation"]["adaptive_policy"]["preferred_focus"] == "observation"
    assert relapse_result["deliberation"]["adaptive_policy"]["candidate_budget"] == 1
    assert relapse_result["cognition_state"]["meta_governance"]["mode"] in {"observe", "correct"}

    recovered_outcomes = [
        _self_learning_outcome("Recovered latest E", "completed", quality_score=0.88, recorded_hour=23),
        _self_learning_outcome("Recovered latest D", "completed", quality_score=0.86, recorded_hour=22),
        _self_learning_outcome("Recovered latest C", "completed", quality_score=0.84, recorded_hour=21),
        _self_learning_outcome("Recovered latest B", "completed", quality_score=0.82, recorded_hour=20),
        _self_learning_outcome("Recovered latest A", "completed", quality_score=0.80, recorded_hour=19),
        {
            "title": "Planned-only low quality should not re-tighten",
            "event_type": "planned",
            "status": "planned",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.18,
            "recorded_at": "2026-06-29T18:00:00+00:00",
        },
        _self_learning_outcome("Retry old self-learning", "retry", quality_score=0.24, recorded_hour=17),
        _self_learning_outcome("Deferred old self-learning", "deferred", quality_score=0.26, recorded_hour=16),
        _self_learning_outcome("Failed old self-learning", "failed", quality_score=0.20, recorded_hour=15),
    ]
    install_history(list(reversed(recovered_outcomes)))
    supervisor.evaluate_idle_window = stable_idle_window  # type: ignore[method-assign]

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        recovered_result = await supervisor.evaluate_endogenous_drive(
            {"record_activity": False, "persist_evaluation": False}
        )

    assert recovered_result["deliberation"]["reflection"]["dominant_constraint"] != (
        "historical_underdelivery"
    )
    assert recovered_result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] != (
        "observe_before_acting"
    )
    assert recovered_result["deliberation"]["adaptive_policy"]["preferred_focus"] != "observation"
    assert recovered_result["deliberation"]["adaptive_policy"]["candidate_budget"] >= 2
    assert recovered_result["cognition_state"]["meta_governance"]["mode"] != "observe"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.stability_longcycle
async def test_writeback_history_replay_remains_time_ordered_when_outcomes_are_scrambled(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    async def stable_idle_window(_request=None):
        return _endogenous_idle_window_payload(
            quality_score=0.82,
            completed_title="Stable writeback learning",
            completed_at="2026-06-28T12:00:00+00:00",
        )

    async def strained_idle_window(_request=None):
        return _endogenous_idle_window_payload(
            quality_score=0.42,
            completed_title="Strained writeback learning",
            completed_at="2026-06-28T12:00:00+00:00",
            error_count=1,
            uncertainty_count=1,
        )

    writeback_sequence = [
        (stable_idle_window, "completed"),
        (stable_idle_window, "completed"),
        (stable_idle_window, "completed"),
        (strained_idle_window, "failed"),
        (stable_idle_window, "completed"),
        (strained_idle_window, "failed"),
    ]
    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        for idle_window_fn, status in writeback_sequence:
            supervisor.evaluate_idle_window = idle_window_fn  # type: ignore[method-assign]
            await _plan_and_write_back_endogenous_cycle(
                supervisor,
                outcome_status=status,
                reason=f"time ordered writeback {status}",
                allow_empty_candidates=True,
            )

    accumulated_history = supervisor._load_endogenous_drive_history()
    terminal_outcomes = [
        dict(item)
        for item in list(accumulated_history.get("outcomes") or [])
        if isinstance(item, dict)
        and str(item.get("status") or "").strip().lower() in {"completed", "failed"}
    ]
    assert len(terminal_outcomes) >= 4

    replay_outcomes = [
        {
            **dict(terminal_outcomes[3]),
            "recorded_at": "2026-06-28T09:00:00+00:00",
        },
        {
            **dict(terminal_outcomes[2]),
            "recorded_at": "2026-06-28T10:00:00+00:00",
        },
        {
            **dict(terminal_outcomes[1]),
            "recorded_at": "2026-06-28T11:00:00+00:00",
        },
        {
            **dict(terminal_outcomes[0]),
            "recorded_at": "2026-06-28T12:00:00+00:00",
        },
    ]
    scrambled_replay_outcomes = [
        replay_outcomes[2],
        replay_outcomes[0],
        replay_outcomes[3],
        replay_outcomes[1],
    ]
    replay_strategy = json.loads(json.dumps(accumulated_history["strategy_memory"]))

    async def replay_idle_window(_request=None):
        return _endogenous_idle_window_payload(
            quality_score=0.76,
            completed_title="Replay stable learning",
            completed_at="2026-06-28T12:00:00+00:00",
        )

    def _install_replay_history(outcomes: list[dict]) -> None:
        history = supervisor._endogenous_drive_history_default()
        history["outcomes"] = json.loads(json.dumps(outcomes))
        history["strategy_memory"] = json.loads(json.dumps(replay_strategy))
        supervisor._persist_endogenous_drive_history(history)

    supervisor.evaluate_idle_window = replay_idle_window  # type: ignore[method-assign]
    _install_replay_history(replay_outcomes)
    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        ordered_result = await supervisor.evaluate_endogenous_drive(
            {"record_activity": False, "persist_evaluation": False}
        )

    _install_replay_history(scrambled_replay_outcomes)
    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        scrambled_result = await supervisor.evaluate_endogenous_drive(
            {"record_activity": False, "persist_evaluation": False}
        )

    assert ordered_result["deliberation"]["reflection"]["source_evidence"] == (
        scrambled_result["deliberation"]["reflection"]["source_evidence"]
    )
    assert ordered_result["deliberation"]["reflection"]["dominant_constraint"] == (
        scrambled_result["deliberation"]["reflection"]["dominant_constraint"]
    )
    assert ordered_result["deliberation"]["adaptive_policy"]["preferred_focus"] == (
        scrambled_result["deliberation"]["adaptive_policy"]["preferred_focus"]
    )
    assert ordered_result["deliberation"]["adaptive_policy"]["candidate_budget"] == (
        scrambled_result["deliberation"]["adaptive_policy"]["candidate_budget"]
    )
    assert ordered_result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == (
        scrambled_result["cognition_state"]["judgement_core"]["primary_need"]["need_type"]
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_memory_success_does_not_reopen_candidate_budget_while_self_learning_underdelivery_persists(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    base_bad3 = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
    ]

    history_baseline = supervisor._endogenous_drive_history_default()
    history_baseline["outcomes"] = list(base_bad3)
    supervisor._persist_endogenous_drive_history(history_baseline)

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        baseline = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    history_with_memory_recovery = supervisor._endogenous_drive_history_default()
    history_with_memory_recovery["outcomes"] = list(base_bad3) + [
        {
            "title": "Recovered memory maintenance D",
            "event_type": "decision",
            "status": "completed",
            "task_family": "memory_maintenance",
            "governance_task_type": "memory_maintenance",
            "quality_score": 0.74,
        },
        {
            "title": "Recovered memory maintenance E",
            "event_type": "decision",
            "status": "completed",
            "task_family": "memory_maintenance",
            "governance_task_type": "memory_maintenance",
            "quality_score": 0.79,
        },
        {
            "title": "Recovered memory maintenance F",
            "event_type": "decision",
            "status": "completed",
            "task_family": "memory_maintenance",
            "governance_task_type": "memory_maintenance",
            "quality_score": 0.81,
        },
        {
            "title": "Recovered memory maintenance G",
            "event_type": "decision",
            "status": "completed",
            "task_family": "memory_maintenance",
            "governance_task_type": "memory_maintenance",
            "quality_score": 0.84,
        },
        {
            "title": "Recovered memory maintenance H",
            "event_type": "decision",
            "status": "completed",
            "task_family": "memory_maintenance",
            "governance_task_type": "memory_maintenance",
            "quality_score": 0.86,
        },
    ]
    supervisor._persist_endogenous_drive_history(history_with_memory_recovery)

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        with_memory_recovery = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    baseline_policy = baseline["deliberation"]["adaptive_policy"]
    recovered_policy = with_memory_recovery["deliberation"]["adaptive_policy"]

    assert baseline["deliberation"]["reflection"]["dominant_constraint"] == "historical_underdelivery"
    assert with_memory_recovery["deliberation"]["reflection"]["dominant_constraint"] == "historical_underdelivery"
    assert baseline_policy["candidate_budget"] == 1
    assert recovered_policy["candidate_budget"] == 1
    assert "historical_drag_scope=self_learning" in recovered_policy["source_evidence"]
    assert "scoped_historical_drag_ratio=1.00" in recovered_policy["source_evidence"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_observation_mode_does_not_revive_filtered_learning_fallback_when_no_allowed_candidates_remain(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": False, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": False, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "historical_underdelivery"
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "observation"
    assert result["deliberation"]["adaptive_policy"]["candidate_budget"] == 1
    assert result["deliberation"]["adaptive_policy"]["exploratory_learning_quota"] == 0
    assert result["candidates"] == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_observation_mode_does_not_fall_back_to_memory_maintenance_when_observe_before_acting_is_primary(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Failed self-learning A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "Deferred self-learning B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "Awaiting review self-learning C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": False, "eligible_for_execution": False},
                "general_self_evolution": {"eligible_for_planning": False, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_evolution": {"eligible_for_planning": False, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["reflection"]["dominant_constraint"] == "historical_underdelivery"
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"
    assert result["cognition_state"]["judgement_core"]["primary_intent"]["intent_type"] == "observe_before_acting"
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "observation"
    assert result["deliberation"]["adaptive_policy"]["candidate_budget"] == 1
    assert result["deliberation"]["adaptive_policy"]["exploratory_learning_quota"] == 0
    assert result["candidates"] == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_queue_hygiene_candidate_path_does_not_crash_when_self_learning_is_disabled(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Deferred queue item A",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
        {
            "title": "Awaiting review queue item B",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
        {
            "title": "Retry queue item C",
            "event_type": "decision",
            "status": "retry",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": False, "eligible_for_execution": False},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    candidate_kinds = {
        str(
            dict(dict(candidate.get("metadata") or {}).get("score_breakdown") or {}).get(
                "candidate_kind"
            )
            or ""
        ).strip()
        for candidate in result["candidates"]
    }
    assert "queue_hygiene_review" in candidate_kinds


@pytest.mark.asyncio
@pytest.mark.unit
async def test_truthfulness_candidate_survives_budget_trimming_when_truthfulness_is_primary_need(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "SL A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "SL B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "SL C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Q A",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
        {
            "title": "Q B",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
        {
            "title": "Q C",
            "event_type": "decision",
            "status": "retry",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 4, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "repair_truthfulness"
    assert result["cognition_state"]["judgement_core"]["primary_intent"]["intent_type"] == "review_truthfulness_signals"
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "truthfulness"
    assert result["deliberation"]["adaptive_policy"]["candidate_budget"] == 1
    candidate_kinds = [
        str(
            dict(dict(candidate.get("metadata") or {}).get("score_breakdown") or {}).get(
                "candidate_kind"
            )
            or ""
        ).strip()
        for candidate in result["candidates"]
    ]
    assert candidate_kinds == ["truthfulness_review"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_weak_truthfulness_signal_does_not_materialize_truthfulness_candidate_before_review_threshold(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "SL A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "SL B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "SL C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Q A",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
        {
            "title": "Q B",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 0, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["perception"]["correction_signals"] == 1
    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"
    assert result["cognition_state"]["judgement_core"]["primary_intent"]["intent_type"] == "observe_before_acting"
    candidate_kinds = [
        str(
            dict(dict(candidate.get("metadata") or {}).get("score_breakdown") or {}).get(
                "candidate_kind"
            )
            or ""
        ).strip()
        for candidate in result["candidates"]
    ]
    assert "truthfulness_review" not in candidate_kinds


@pytest.mark.asyncio
@pytest.mark.unit
async def test_truthfulness_candidate_materializes_once_review_threshold_is_reached_under_observation_pressure(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "SL A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "SL B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "SL C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Q A",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
        {
            "title": "Q B",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 2, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["deliberation"]["perception"]["correction_signals"] == 3
    candidate_kinds = [
        str(
            dict(dict(candidate.get("metadata") or {}).get("score_breakdown") or {}).get(
                "candidate_kind"
            )
            or ""
        ).strip()
        for candidate in result["candidates"]
    ]
    assert "truthfulness_review" in candidate_kinds


@pytest.mark.asyncio
@pytest.mark.unit
async def test_drive_posture_signal_keeps_observation_intent_link_when_truthfulness_is_primary_need(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "SL A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "SL B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "SL C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
        {
            "title": "Q A",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
        {
            "title": "Q B",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
        {
            "title": "Q C",
            "event_type": "decision",
            "status": "retry",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {
                "active_sessions": 0,
                "counts": {"error_count": 4, "uncertainty_high_count": 1},
            },
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "repair_truthfulness"
    assert result["cognition_state"]["judgement_core"]["primary_intent"]["intent_type"] == "review_truthfulness_signals"
    observe_signal = next(
        signal
        for signal in result["deliberation"]["signals"]
        if signal["signal_type"] == "drive_posture_signal"
    )
    assert observe_signal["source_needs"] == ["observe_before_acting"]
    assert observe_signal["related_intent"] == "observe_before_acting"
    assert observe_signal["payload"]["preferred_focus"] == "truthfulness"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_queue_hygiene_candidate_survives_budget_trimming_when_observation_mode_and_queue_review_signal_are_active(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Q A",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
        {
            "title": "Q B",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
        {
            "title": "Q C",
            "event_type": "decision",
            "status": "retry",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
        {
            "title": "Q D",
            "event_type": "decision",
            "status": "paused",
            "task_family": "general_self_evolution",
            "governance_task_type": "self_evolution",
            "quality_score": 0.20,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Queue hygiene debt A",
            "summary": "Review deferred self-evolution work.",
            "governance_task_type": "self_evolution",
            "task_family": "general_self_evolution",
            "execution_kind": "general_self_evolution",
        }
    )
    queued_task_id = planned["tasks"][0]["task_id"]
    supervisor._self_evolution_queue.update_status(
        queued_task_id,
        status="deferred",
        actor="test",
        reason="probe queue review signal",
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": False, "eligible_for_execution": False},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"
    assert result["cognition_state"]["judgement_core"]["primary_intent"]["intent_type"] == "observe_before_acting"
    assert result["deliberation"]["adaptive_policy"]["preferred_focus"] == "observation"
    assert result["deliberation"]["adaptive_policy"]["candidate_budget"] == 1
    assert any(
        signal["signal_type"] == "governance_review_suggestion"
        and signal.get("related_intent") == "review_queue_hygiene"
        for signal in result["deliberation"]["signals"]
    )
    candidate_kinds = [
        str(
            dict(dict(candidate.get("metadata") or {}).get("score_breakdown") or {}).get(
                "candidate_kind"
            )
            or ""
        ).strip()
        for candidate in result["candidates"]
    ]
    assert candidate_kinds == ["queue_hygiene_review"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_weak_queue_debt_does_not_materialize_queue_hygiene_candidate_before_review_signal_threshold(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "SL A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "SL B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "SL C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {"error_count": 0, "uncertainty_high_count": 0}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": False, "eligible_for_execution": False},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "pending_review_count": 0,
            "stale_queue_count": 0,
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert result["cognition_state"]["judgement_core"]["primary_need"]["need_type"] == "observe_before_acting"
    assert result["cognition_state"]["judgement_core"]["primary_intent"]["intent_type"] == "observe_before_acting"
    assert not any(
        signal["signal_type"] == "governance_review_suggestion"
        for signal in result["deliberation"]["signals"]
    )
    candidate_kinds = [
        str(
            dict(dict(candidate.get("metadata") or {}).get("score_breakdown") or {}).get(
                "candidate_kind"
            )
            or ""
        ).strip()
        for candidate in result["candidates"]
    ]
    assert "queue_hygiene_review" not in candidate_kinds


@pytest.mark.asyncio
@pytest.mark.unit
async def test_queue_hygiene_candidate_materializes_once_real_queue_debt_exists_under_observation_pressure(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "SL A",
            "event_type": "decision",
            "status": "failed",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.22,
        },
        {
            "title": "SL B",
            "event_type": "decision",
            "status": "deferred",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.28,
        },
        {
            "title": "SL C",
            "event_type": "decision",
            "status": "awaiting_review",
            "task_family": "self_learning",
            "governance_task_type": "self_learning",
            "quality_score": 0.31,
        },
    ]
    supervisor._persist_endogenous_drive_history(history)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Queue review debt A",
            "summary": "Review deferred self-evolution work.",
            "governance_task_type": "self_evolution",
            "task_family": "general_self_evolution",
            "execution_kind": "general_self_evolution",
        }
    )
    queued_task_id = planned["tasks"][0]["task_id"]
    supervisor._self_evolution_queue.update_status(
        queued_task_id,
        status="deferred",
        actor="test",
        reason="probe queue review debt",
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {"error_count": 0, "uncertainty_high_count": 0}},
            "completed_learning_tasks": [
                {
                    "title": "Recent learning",
                    "quality_score": 0.46,
                    "completed_at": "2026-06-28T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": False, "eligible_for_execution": False},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_learning": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    fake_client = _FakeLLMClient({"proposals": []})

    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        result = await supervisor.evaluate_endogenous_drive({"record_activity": False})

    assert any(
        signal["signal_type"] == "governance_review_suggestion"
        for signal in result["deliberation"]["signals"]
    )
    candidate_kinds = [
        str(
            dict(dict(candidate.get("metadata") or {}).get("score_breakdown") or {}).get(
                "candidate_kind"
            )
            or ""
        ).strip()
        for candidate in result["candidates"]
    ]
    assert "queue_hygiene_review" in candidate_kinds


@pytest.mark.asyncio
@pytest.mark.unit
async def test_observation_mode_prefers_candidate_with_stronger_drive_judgement_when_truthfulness_and_queue_review_both_exist(
    tmp_path,
):
    supervisor = _make_supervisor(tmp_path)
    engine = supervisor._endogenous_drive_engine

    candidate_truthfulness = EndogenousTaskCandidate(
        stable_key="truthfulness:review_correction_signals",
        title="Review grounding drift",
        summary="Truthfulness review",
        priority="high",
        governance_task_type="self_learning",
        task_family="self_learning",
        execution_kind=None,
        value_tags=["truthfulness"],
        utility=0.72,
        metadata={
            "score_breakdown": {"candidate_kind": "truthfulness_review"},
            "drive_judgement": {
                "intent": {"priority": 0.91},
                "needs": [{"need_type": "repair_truthfulness", "severity": 0.88, "urgency": 0.84}],
            },
        },
    )
    candidate_queue = EndogenousTaskCandidate(
        stable_key="continuity:queue_hygiene_review",
        title="Review self-evolution queue hygiene",
        summary="Queue hygiene review",
        priority="normal",
        governance_task_type="self_evolution",
        task_family="general_self_evolution",
        execution_kind="general_self_evolution",
        value_tags=["continuity", "truthfulness"],
        utility=0.95,
        metadata={
            "score_breakdown": {"candidate_kind": "queue_hygiene_review"},
            "drive_judgement": {
                "intent": {"priority": 0.54},
                "needs": [{"need_type": "clear_governance_backlog", "severity": 0.56, "urgency": 0.48}],
            },
        },
    )

    selected = engine._apply_adaptive_candidate_budget(
        [candidate_queue, candidate_truthfulness],
        adaptive_policy=DriveAdaptivePolicy(
            learning_expansion_bias=0.5,
            truthfulness_bias=0.5,
            memory_continuity_bias=0.5,
            queue_hygiene_bias=0.5,
            body_growth_bias=0.5,
            observation_bias=0.75,
            candidate_throttle=0.0,
            candidate_budget=1,
            exploratory_learning_quota=2,
            body_growth_quota=1,
            preferred_focus="observation",
            rationale="test",
        ),
    )

    assert [candidate.stable_key for candidate in selected] == ["truthfulness:review_correction_signals"]


@pytest.mark.unit
def test_observation_mode_tie_break_is_stable_across_input_order_when_truthfulness_and_queue_review_are_equally_scored():
    from systems.supervisor.endogenous_drive import EndogenousDriveEngine

    engine = EndogenousDriveEngine()

    candidate_truthfulness = EndogenousTaskCandidate(
        stable_key="truthfulness:review_correction_signals",
        title="Review grounding drift",
        summary="Truthfulness review",
        priority="normal",
        governance_task_type="self_learning",
        task_family="self_learning",
        execution_kind=None,
        value_tags=["truthfulness"],
        utility=0.8,
        metadata={
            "score_breakdown": {"candidate_kind": "truthfulness_review"},
            "drive_judgement": {
                "intent": {"priority": 0.7},
                "needs": [{"need_type": "repair_truthfulness", "severity": 0.7, "urgency": 0.7}],
            },
        },
    )
    candidate_queue = EndogenousTaskCandidate(
        stable_key="continuity:queue_hygiene_review",
        title="Review self-evolution queue hygiene",
        summary="Queue hygiene review",
        priority="normal",
        governance_task_type="self_evolution",
        task_family="general_self_evolution",
        execution_kind="general_self_evolution",
        value_tags=["continuity", "truthfulness"],
        utility=0.8,
        metadata={
            "score_breakdown": {"candidate_kind": "queue_hygiene_review"},
            "drive_judgement": {
                "intent": {"priority": 0.7},
                "needs": [{"need_type": "clear_governance_backlog", "severity": 0.7, "urgency": 0.7}],
            },
        },
    )
    adaptive_policy = DriveAdaptivePolicy(
        learning_expansion_bias=0.5,
        truthfulness_bias=0.5,
        memory_continuity_bias=0.5,
        queue_hygiene_bias=0.5,
        body_growth_bias=0.5,
        observation_bias=0.75,
        candidate_throttle=0.0,
        candidate_budget=1,
        exploratory_learning_quota=2,
        body_growth_quota=1,
        preferred_focus="observation",
        rationale="test",
    )

    forward = engine._apply_adaptive_candidate_budget(
        [candidate_truthfulness, candidate_queue],
        adaptive_policy=adaptive_policy,
    )
    reversed_order = engine._apply_adaptive_candidate_budget(
        [candidate_queue, candidate_truthfulness],
        adaptive_policy=adaptive_policy,
    )

    assert [candidate.stable_key for candidate in forward] == [
        "truthfulness:review_correction_signals"
    ]
    assert [candidate.stable_key for candidate in reversed_order] == [
        "truthfulness:review_correction_signals"
    ]


@pytest.mark.unit
def test_observation_mode_keeps_monotonic_switch_when_queue_review_becomes_slightly_stronger():
    from systems.supervisor.endogenous_drive import EndogenousDriveEngine

    engine = EndogenousDriveEngine()
    adaptive_policy = DriveAdaptivePolicy(
        learning_expansion_bias=0.5,
        truthfulness_bias=0.5,
        memory_continuity_bias=0.5,
        queue_hygiene_bias=0.5,
        body_growth_bias=0.5,
        observation_bias=0.75,
        candidate_throttle=0.0,
        candidate_budget=1,
        exploratory_learning_quota=2,
        body_growth_quota=1,
        preferred_focus="observation",
        rationale="test",
    )

    candidate_truthfulness = EndogenousTaskCandidate(
        stable_key="truthfulness:review_correction_signals",
        title="Review grounding drift",
        summary="Truthfulness review",
        priority="normal",
        governance_task_type="self_learning",
        task_family="self_learning",
        execution_kind=None,
        value_tags=["truthfulness"],
        utility=0.8,
        metadata={
            "score_breakdown": {"candidate_kind": "truthfulness_review"},
            "drive_judgement": {
                "intent": {"priority": 0.70},
                "needs": [{"need_type": "repair_truthfulness", "severity": 0.70, "urgency": 0.70}],
            },
        },
    )
    candidate_queue = EndogenousTaskCandidate(
        stable_key="continuity:queue_hygiene_review",
        title="Review self-evolution queue hygiene",
        summary="Queue hygiene review",
        priority="normal",
        governance_task_type="self_evolution",
        task_family="general_self_evolution",
        execution_kind="general_self_evolution",
        value_tags=["continuity", "truthfulness"],
        utility=0.8,
        metadata={
            "score_breakdown": {"candidate_kind": "queue_hygiene_review"},
            "drive_judgement": {
                "intent": {"priority": 0.71},
                "needs": [{"need_type": "clear_governance_backlog", "severity": 0.71, "urgency": 0.70}],
            },
        },
    )

    selected = engine._apply_adaptive_candidate_budget(
        [candidate_truthfulness, candidate_queue],
        adaptive_policy=adaptive_policy,
    )

    assert [candidate.stable_key for candidate in selected] == ["continuity:queue_hygiene_review"]


@pytest.mark.unit
def test_runtime_observation_gate_does_not_reopen_memory_maintenance_fallback(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    kept, deferred = supervisor._gate_endogenous_candidates_by_posture(
        candidate_items=[
            {
                "title": "Maintain memory",
                "stable_key": "memory:maintenance",
                "metadata": {"score_breakdown": {"candidate_kind": "memory_maintenance"}},
            },
            {
                "title": "Review truthfulness",
                "stable_key": "truthfulness:review_correction_signals",
                "metadata": {"score_breakdown": {"candidate_kind": "truthfulness_review"}},
            },
        ],
        drive_posture={
            "payload": {
                "preferred_focus": "observation",
                "candidate_budget": 2,
            }
        },
    )

    kept_kinds = [
        dict(dict(item.get("metadata") or {}).get("score_breakdown") or {}).get(
            "candidate_kind"
        )
        for item in kept
    ]
    deferred_kinds = [item.get("candidate_kind") for item in deferred]

    assert kept_kinds == ["truthfulness_review"]
    assert deferred_kinds == ["memory_maintenance"]


@pytest.mark.unit
def test_recent_completed_static_governance_candidates_are_not_recreated_immediately():
    engine = EndogenousDriveEngine()
    now = datetime.now(timezone.utc)
    idle = {
        "checks": {"in_execution_window": True, "has_user_idle": True},
        "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
        "activity": {
            "active_sessions": 0,
            "counts": {},
        },
        "queued_tasks": [
            {
                "title": "Recent memory sweep",
                "status": "completed",
                "governance_task_type": "memory_maintenance",
                "task_family": "memory_maintenance",
                "execution_kind": "memory_maintenance",
                "updated_at": (now - timedelta(hours=1)).isoformat(),
                "metadata": {
                    "endogenous_drive_key": "continuity:memory_maintenance_sweep",
                },
            },
            {
                "title": "Recent queue hygiene review",
                "status": "completed",
                "governance_task_type": "self_evolution",
                "task_family": "general_self_evolution",
                "execution_kind": "general_self_evolution",
                "updated_at": (now - timedelta(hours=1)).isoformat(),
                "metadata": {
                    "endogenous_drive_key": "continuity:queue_hygiene_review",
                },
            },
            {
                "title": "Pending review debt",
                "status": "deferred",
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
                "execution_kind": None,
                "updated_at": now.isoformat(),
                "metadata": {"endogenous_drive_key": "truthfulness:review_correction_signals"},
            },
        ],
        "task_family_decisions": {
            "memory_maintenance": {"eligible_for_planning": True},
            "self_learning": {"eligible_for_planning": False},
            "general_self_evolution": {"eligible_for_planning": True},
        },
        "governance_task_type_decisions": {
            "memory_maintenance": {"eligible_for_planning": True},
            "self_learning": {"eligible_for_planning": False},
            "self_evolution": {"eligible_for_planning": True},
        },
    }

    candidates = engine.generate_candidates(
        idle_window=idle,
        existing_drive_keys=set(),
        max_candidates=5,
    )
    candidate_keys = {candidate.stable_key for candidate in candidates}

    assert "continuity:memory_maintenance_sweep" not in candidate_keys
    assert "continuity:queue_hygiene_review" not in candidate_keys


@pytest.mark.unit
def test_static_governance_candidates_reopen_after_completion_cooldown():
    engine = EndogenousDriveEngine()
    now = datetime.now(timezone.utc)
    idle = {
        "checks": {"in_execution_window": True, "has_user_idle": True},
        "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
        "activity": {
            "active_sessions": 0,
            "counts": {},
        },
        "queued_tasks": [
            {
                "title": "Old memory sweep",
                "status": "completed",
                "governance_task_type": "memory_maintenance",
                "task_family": "memory_maintenance",
                "execution_kind": "memory_maintenance",
                "updated_at": (now - timedelta(hours=18)).isoformat(),
                "metadata": {
                    "endogenous_drive_key": "continuity:memory_maintenance_sweep",
                },
            },
            {
                "title": "Old queue hygiene review",
                "status": "completed",
                "governance_task_type": "self_evolution",
                "task_family": "general_self_evolution",
                "execution_kind": "general_self_evolution",
                "updated_at": (now - timedelta(hours=18)).isoformat(),
                "metadata": {
                    "endogenous_drive_key": "continuity:queue_hygiene_review",
                },
            },
            {
                "title": "Pending review debt",
                "status": "deferred",
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
                "execution_kind": None,
                "updated_at": now.isoformat(),
                "metadata": {"endogenous_drive_key": "truthfulness:review_correction_signals"},
            },
        ],
        "task_family_decisions": {
            "memory_maintenance": {"eligible_for_planning": True},
            "self_learning": {"eligible_for_planning": False},
            "general_self_evolution": {"eligible_for_planning": True},
        },
        "governance_task_type_decisions": {
            "memory_maintenance": {"eligible_for_planning": True},
            "self_learning": {"eligible_for_planning": False},
            "self_evolution": {"eligible_for_planning": True},
        },
    }

    candidates = engine.generate_candidates(
        idle_window=idle,
        existing_drive_keys=set(),
        max_candidates=5,
    )
    candidate_keys = {candidate.stable_key for candidate in candidates}

    assert "continuity:memory_maintenance_sweep" in candidate_keys
    assert "continuity:queue_hygiene_review" in candidate_keys


@pytest.mark.asyncio
@pytest.mark.unit
async def test_proposal_drift_memory_biases_program_task_type_priors_toward_observation_and_review(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    history = supervisor._endogenous_drive_history_default()
    history["outcomes"] = [
        {
            "title": "Misaligned improvement attempt A",
            "cognitive_alignment": {
                "score": 0.28,
                "quality": "weak",
                "top_priority_task_type": "observation",
                "reasons": ["improvement_runs_against_weak_or_missing_channels"],
            },
        },
        {
            "title": "Misaligned improvement attempt B",
            "cognitive_alignment": {
                "score": 0.34,
                "quality": "weak",
                "top_priority_task_type": "review",
                "reasons": ["weak_evidence_conflicts_with_improvement_shape"],
            },
        },
    ]
    supervisor._persist_endogenous_drive_history(history)

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {"user": 900, "agent": 900, "memory": 900},
            "activity": {"active_sessions": 0, "counts": {}},
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": False, "eligible_for_execution": False},
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]
    result = await supervisor.evaluate_endogenous_drive({"record_activity": False})
    deliberation = result["deliberation"]
    signals = deliberation["signals"]
    assert signals

    fake_client = _FakeLLMClient({"proposals": []})
    with patch("memai.model_config.resolve_mem_llm_client", return_value=(fake_client, "test-model")):
        await supervisor.evaluate_endogenous_drive({"record_activity": False})

    if fake_client.calls and "task_generation" in fake_client.calls[0].get("user_payload", {}):
        prompt_payload = fake_client.calls[0]["user_payload"]["task_generation"]
        assert "\"drift_state\": \"drifting\"" in prompt_payload
        assert "\"top_priority_task_type\":" not in prompt_payload
        assert "\"suggested_task_shape\":" not in prompt_payload
        assert "\"posture_alignment_signal_count\":" in prompt_payload
        assert "\"priority_basis_signal_count\":" in prompt_payload
        assert "\"posture_alignment_health\":" in prompt_payload
        assert "\"priority_basis_health\":" in prompt_payload
    else:
        idle_window = await fake_idle_window()
        idle_window["drive_history"] = supervisor._history_for_endogenous_drive(
            supervisor._load_endogenous_drive_history()
        )
        drive_context = supervisor._endogenous_drive_engine._build_drive_context(idle_window)
        evidence_packet = supervisor._endogenous_drive_engine._build_lm_evidence_packet(
            idle_window=idle_window,
            deliberation=supervisor._endogenous_drive_engine.build_deliberation_report(idle_window=idle_window),
            drive_context=drive_context,
            memory_plan={},
            self_learning_plan={},
            self_evolution_plan={},
        )
        priors = evidence_packet["task_type_priors"]
        assert priors["drift_state"] == "drifting"
        assert priors["top_priority_task_type"] in {"observation", "review"}
        observation_row = next(row for row in priors["priors"] if row["task_type"] == "observation")
        review_row = next(row for row in priors["priors"] if row["task_type"] == "review")
        assert (
            "proposal_drift_requires_more_observation" in observation_row["reasons"]
            or "review_can_help_correct_recent_proposal_drift" in review_row["reasons"]
        )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_fallback_learning_targets_shell_codebase_without_history(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {},
                "recent_metadata": {},
            },
            "shell_slot": {
                "slot_id": "slot-B",
                "worktree_path": str((tmp_path / ".body-slots" / "slot-B" / "worktree").resolve()),
            },
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": False,
                    "eligible_for_execution": False,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": False,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": False,
                    "eligible_for_execution": False,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": False,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]

    result = await supervisor._run_endogenous_drive_cycle()

    assert result["status"] == "planned"
    learning_task = next(
        task for task in result["tasks"]
        if task["governance_task_type"] == "self_learning"
    )
    assert learning_task["title"] == "Understand the current shell body codebase"
    assert learning_task["constraints"]["execution_policy"] == "learn_shell_baseline"
    assert learning_task["constraints"]["baseline_slot_id"] == "slot-B"
    assert learning_task["metadata"]["learning_branch"] == "codebase_baseline"
    assert learning_task["metadata"]["self_learning_mode"] == "shell_codebase_baseline"
    assert learning_task["evidence"]["learning_branch"] == "codebase_baseline"
    assert "slot-B" in learning_task["summary"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_schedule_allocator_skips_occupied_slots(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    await supervisor.plan_self_evolution_task(
        {
            "title": "Occupied slot task",
            "scheduled_for": "2026-06-28T00:00:00",
        }
    )

    prepared = supervisor._apply_scheduled_for_to_candidate_items(
        [
            {
                "title": "Generated candidate A",
                "metadata": {"endogenous_drive_key": "candidate-a"},
            },
            "not-a-candidate",
            {
                "title": "Generated candidate B",
                "metadata": {"endogenous_drive_key": "candidate-b"},
            },
        ],
        now=datetime.fromisoformat("2026-06-28T00:00:00"),
    )

    tokens = [item["scheduled_for"] for item in prepared]
    assert tokens == ["2026-06-28T00:05:00", "2026-06-28T00:10:00"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auto_decision_approves_task_when_idle_window_allows_execution(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task({"title": "Review body upgrade proposal"})
    task_id = planned["tasks"][0]["task_id"]

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.decide_self_evolution_task(
        task_id,
        {
            "decision": "auto",
            "idle_window": {"now": "2026-05-25T00:15:00"},
        },
    )

    assert result["status"] == "approved"
    assert result["task"]["status"] == "approved"
    assert result["task"]["task_family"] == "general_self_evolution"
    assert result["task"]["decision_history"][-1]["status"] == "approved"
    assert result["task"]["decision_history"][-1]["governance_task_type"] == "self_evolution"
    assert result["task"]["decision_history"][-1]["task_family"] == "general_self_evolution"
    assert result["task"]["decision_history"][-1]["execution_kind"] == "general_self_evolution"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_defers_tasks_when_idle_window_is_not_ready(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    await supervisor.plan_self_evolution_task(
        {
            "items": [
                {"title": "Evaluate tool scheduler backpressure"},
                {"title": "Assess body probe retry policy"},
            ]
        }
    )

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:12:00",
            "last_agent_work_at": "2026-05-25T00:12:00",
            "last_memory_task_at": None,
            "last_self_evolution_activity_at": None,
            "counts": {},
            "active_sessions": 1,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T00:15:00"},
        }
    )

    assert result["status"] == "reviewed"
    assert result["decision"] == "deferred"
    assert result["count"] == 2
    assert all(task["status"] == "deferred" for task in result["tasks"])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_can_reapprove_deferred_tasks_on_later_cycle(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    await supervisor.plan_self_evolution_task(
        {
            "items": [
                {"title": "Revisit idle learning thread"},
                {"title": "Revisit stale improvement evidence"},
            ]
        }
    )

    async def busy_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:12:00",
            "last_agent_work_at": "2026-05-25T00:12:00",
            "last_memory_task_at": None,
            "last_self_evolution_activity_at": None,
            "counts": {},
            "active_sessions": 1,
        }

    supervisor._fetch_gateway_activity_snapshot = busy_snapshot  # type: ignore[method-assign]

    first = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T00:15:00"},
        }
    )
    assert all(task["status"] == "deferred" for task in first["tasks"])

    async def idle_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = idle_snapshot  # type: ignore[method-assign]

    second = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T01:00:00"},
        }
    )

    assert second["status"] == "reviewed"
    assert second["count"] == 2
    assert all(task["status"] == "approved" for task in second["tasks"])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_still_plans_learning_candidates_with_active_sessions(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    async def fake_idle_window(request: dict | None = None):
        del request
        return {
            "checks": {"in_execution_window": True},
            "idle_seconds": {"user": 1000, "agent": 1000, "memory": 1000},
            "activity": {
                "counts": {},
                "active_sessions": 2,
                "recent_metadata": {
                    "user_request": {"topic": "AUTO foreground execution diagnostics"}
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]

    result = await supervisor._run_endogenous_drive_cycle()
    queued = await supervisor.list_self_evolution_tasks()
    keys = {
        task["metadata"]["endogenous_drive_key"]: task for task in queued["tasks"]
    }

    assert result["status"] == "planned"
    assert any(task["governance_task_type"] == "self_learning" for task in queued["tasks"])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_accepts_lm_queue_governance_override(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "items": [
                {"title": "Learn unresolved architecture issue"},
                {"title": "Weak duplicate follow-up", "priority": "low"},
            ]
        }
    )
    tasks_by_title = {task["title"]: task["task_id"] for task in planned["tasks"]}

    async def idle_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = idle_snapshot  # type: ignore[method-assign]

    async def fake_lm_review(tasks, *, idle_window):
        assert len(tasks) == 2
        assert idle_window["checks"]["has_user_idle"] is True
        return {
            tasks_by_title["Weak duplicate follow-up"]: {
                "action": "cancel",
                "reason": "Duplicate and low-evidence task should be cleared from the queue.",
            }
        }

    monkeypatch.setattr(supervisor, "_lm_review_task_queue", fake_lm_review)

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T01:00:00"},
        }
    )

    tasks = {task["title"]: task for task in result["tasks"]}
    assert tasks["Learn unresolved architecture issue"]["status"] == "approved"
    assert tasks["Weak duplicate follow-up"]["status"] == "cancelled"
    lm_context = tasks["Weak duplicate follow-up"]["decision_history"][-1]["context"]["lm_queue_review"]
    assert lm_context["action"] == "cancelled"
    assert "Duplicate and low-evidence task" in lm_context["reason"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_governor_mode_preserves_agent_pull_task_approval_when_lm_suggests_defer(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    supervisor._service_runtime.governor_mode_active = True
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Explore one unresolved learning thread",
            "task_family": "self_learning",
            "source": "self_learning",
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    async def idle_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = idle_snapshot  # type: ignore[method-assign]

    async def fake_lm_review(tasks, *, idle_window):
        del idle_window
        assert len(tasks) == 1
        return {
            task_id: {
                "action": "defer",
                "reason": "Conservative queue governance would wait for more evidence.",
            }
        }

    monkeypatch.setattr(supervisor, "_lm_review_task_queue", fake_lm_review)

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T01:00:00"},
        }
    )

    assert result["tasks"][0]["status"] == "approved"
    latest_context = result["tasks"][0]["decision_history"][-1]["context"]
    assert latest_context["lm_queue_review"]["action"] == "deferred"
    assert latest_context["lm_queue_shadow"]["preserved_status"] == "approved"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_preserves_agent_pull_task_approval_without_governor_mode(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Explore one unresolved learning thread",
            "task_family": "self_learning",
            "source": "self_learning",
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    async def busy_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:12:00",
            "last_agent_work_at": "2026-05-25T00:12:00",
            "last_memory_task_at": None,
            "last_self_evolution_activity_at": None,
            "counts": {},
            "active_sessions": 1,
        }

    supervisor._fetch_gateway_activity_snapshot = busy_snapshot  # type: ignore[method-assign]

    async def fake_lm_review(tasks, *, idle_window):
        del idle_window
        assert len(tasks) == 1
        return {
            task_id: {
                "action": "defer",
                "reason": "Conservative queue governance would wait for more evidence.",
            }
        }

    monkeypatch.setattr(supervisor, "_lm_review_task_queue", fake_lm_review)

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T00:15:00"},
        }
    )

    assert result["tasks"][0]["status"] == "approved"
    latest_context = result["tasks"][0]["decision_history"][-1]["context"]
    assert latest_context["lm_queue_review"]["action"] == "deferred"
    assert latest_context["lm_queue_shadow"]["preserved_status"] == "approved"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_auto_approves_body_improvement_agent_pull_task(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Improve shell body after learning",
            "task_family": "body_upgrade",
            "execution_kind": "body_improvement",
            "metadata": {
                "task_family": "body_upgrade",
                "execution_kind": "body_improvement",
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    async def busy_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:12:00",
            "last_agent_work_at": "2026-05-25T00:12:00",
            "last_memory_task_at": None,
            "last_self_evolution_activity_at": None,
            "counts": {},
            "active_sessions": 1,
        }

    supervisor._fetch_gateway_activity_snapshot = busy_snapshot  # type: ignore[method-assign]

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T00:15:00"},
        }
    )

    task = result["tasks"][0]
    assert task["task_id"] == task_id
    assert task["status"] == "approved"
    assert task["execution_kind"] == "body_improvement"
    assert task["execution_request"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_defers_body_improvement_until_self_learning_finishes(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    await supervisor.plan_self_evolution_task(
        {
            "title": "Understand current shell body baseline",
            "task_family": "self_learning",
            "source": "self_learning",
        }
    )
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Improve shell body after learning",
            "task_family": "body_upgrade",
            "execution_kind": "body_improvement",
            "metadata": {
                "task_family": "body_upgrade",
                "execution_kind": "body_improvement",
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    async def busy_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:12:00",
            "last_agent_work_at": "2026-05-25T00:12:00",
            "last_memory_task_at": None,
            "last_self_evolution_activity_at": None,
            "counts": {},
            "active_sessions": 1,
        }

    supervisor._fetch_gateway_activity_snapshot = busy_snapshot  # type: ignore[method-assign]

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T00:15:00"},
        }
    )

    task = next(item for item in result["tasks"] if item["task_id"] == task_id)
    assert task["status"] == "deferred"
    assert task["execution_kind"] == "body_improvement"
    assert "self-learning tasks awaiting completion" in task["decision_reason"]
    assert task["execution_request"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_gateway_activity_snapshot_retries_after_transient_failure(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)

    calls = {"count": 0}

    class _FakeResponse:
        status = 200

        async def json(self):
            return {"last_user_request_at": None, "counts": {}, "active_sessions": 0}

        async def __aenter__(self):
            calls["count"] += 1
            if calls["count"] == 1:
                raise TimeoutError("transient gateway timeout")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            del url
            return _FakeResponse()

    monkeypatch.setattr("aiohttp.ClientSession", _FakeSession)

    snapshot = await supervisor._fetch_gateway_activity_snapshot()

    assert snapshot["active_sessions"] == 0
    assert calls["count"] == 2


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_records_shadow_governance_actions_without_mutating_state(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "items": [
                {"title": "Duplicate learning branch", "priority": "normal"},
                {"title": "Canonical learning branch", "priority": "high"},
            ]
        }
    )
    tasks_by_title = {task["title"]: task["task_id"] for task in planned["tasks"]}

    async def idle_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = idle_snapshot  # type: ignore[method-assign]

    async def fake_lm_review(tasks, *, idle_window):
        assert len(tasks) == 2
        return {
            tasks_by_title["Duplicate learning branch"]: {
                "action": "merge",
                "reason": "This task duplicates the stronger canonical branch.",
                "shadow": {
                    "action": "merge",
                    "reason": "This task duplicates the stronger canonical branch.",
                    "merge_into": tasks_by_title["Canonical learning branch"],
                },
            },
            tasks_by_title["Canonical learning branch"]: {
                "action": "reprioritize",
                "reason": "Promote the canonical branch ahead of duplicates.",
                "shadow": {
                    "action": "reprioritize",
                    "reason": "Promote the canonical branch ahead of duplicates.",
                    "priority": "high",
                },
            },
        }

    monkeypatch.setattr(supervisor, "_lm_review_task_queue", fake_lm_review)

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T01:00:00"},
        }
    )

    tasks = {task["title"]: task for task in result["tasks"]}
    assert tasks["Duplicate learning branch"]["status"] == "approved"
    assert tasks["Canonical learning branch"]["status"] == "approved"
    duplicate_shadow = tasks["Duplicate learning branch"]["decision_history"][-1]["context"]["lm_queue_shadow"]
    canonical_shadow = tasks["Canonical learning branch"]["decision_history"][-1]["context"]["lm_queue_shadow"]
    assert duplicate_shadow["action"] == "merge"
    assert duplicate_shadow["merge_into"] == tasks_by_title["Canonical learning branch"]
    assert canonical_shadow["action"] == "reprioritize"
    assert canonical_shadow["priority"] == "high"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_can_apply_lm_reprioritize_to_real_task_priority(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Underweighted architecture follow-up",
            "priority": "low",
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    async def idle_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = idle_snapshot  # type: ignore[method-assign]

    async def fake_lm_review(tasks, *, idle_window):
        return {
            task_id: {
                "action": "reprioritize",
                "priority": "high",
                "reason": "This follow-up now blocks higher-value evolution work.",
            }
        }

    monkeypatch.setattr(supervisor, "_lm_review_task_queue", fake_lm_review)

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T01:00:00"},
        }
    )

    task = result["tasks"][0]
    assert task["priority"] == "high"
    priority_context = task["decision_history"][-1]["context"]["lm_queue_priority"]
    assert priority_context["priority"] == "high"
    assert "blocks higher-value evolution work" in priority_context["reason"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_plan_task_normalizes_scheduled_for_into_runtime_payload(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Scheduled queue task",
            "scheduled_for": "2026-06-28T01:00:00",
        }
    )

    task = planned["tasks"][0]
    assert task["scheduled_for"] == "2026-06-28T01:00:00"
    assert task["metadata"]["scheduled_for"] == "2026-06-28T01:00:00"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_defers_second_task_when_scheduled_for_conflicts(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    await supervisor.plan_self_evolution_task(
        {
            "title": "First scheduled task",
            "scheduled_for": "2026-06-28T01:00:00",
        }
    )
    await supervisor.plan_self_evolution_task(
        {
            "title": "Second scheduled task",
            "scheduled_for": "2026-06-28T01:00:00",
        }
    )

    async def idle_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = idle_snapshot  # type: ignore[method-assign]

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T01:00:00"},
        }
    )

    tasks = {task["title"]: task for task in result["tasks"]}
    assert tasks["First scheduled task"]["status"] == "approved"
    assert tasks["Second scheduled task"]["status"] == "deferred"
    conflict = tasks["Second scheduled task"]["decision_history"][-1]["context"]["schedule_conflict"]
    assert conflict["scheduled_for"] == "2026-06-28T01:00:00"
    assert conflict["occupied_by_title"] == "First scheduled task"
    assert "Only one live task may keep the same scheduled_for" in tasks["Second scheduled task"]["decision_reason"]


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.skip(reason="Boundary violation defer removed — body_upgrade/body_switch no longer driven by task queue")
async def test_batch_review_defers_body_task_with_boundary_violations(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    await supervisor.plan_self_evolution_task(
        {
            "title": "Reject boundary-violating body candidate during review",
            "metadata": {
                "execution_kind": "body_switch",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "git_lineage": {
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                    "changed_files": [
                        "agent/stream_handler.py",
                        "systems/body_registry.py",
                    ],
                },
            },
        }
    )

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T00:15:00"},
        }
    )

    task = result["tasks"][0]
    assert result["decision"] == "approved"
    assert task["status"] == "deferred"
    assert task["execution_request"] is None
    assert "systems/body_registry.py" in task["decision_reason"]
    assert task["decision_history"][-1]["context"]["evolution_boundary"]["ok"] is False
    assert task["evolution_boundary"]["violations"] == ["systems/body_registry.py"]
    history = supervisor._governor.list_history(limit=10)
    boundary_record = [record for record in history if record["kind"] == "boundary_defer"][-1]
    assert boundary_record["kind"] == "boundary_defer"
    assert boundary_record["request"]["body_id"] == "slot-B"
    assert boundary_record["response"]["decision"] == "defer"
    assert boundary_record["response"]["violations"] == ["systems/body_registry.py"]
    assert boundary_record["evolution_lineage"]["candidate_commit"] == "bbb222"
    assert any(record["kind"] == "supervisor_activity" for record in history)


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.skip(reason="Boundary defer removed — body_upgrade/body_switch no longer driven by task queue")
async def test_batch_review_boundary_defer_does_not_depend_on_mem_write_success(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    await supervisor.plan_self_evolution_task(
        {
            "title": "Boundary defer should survive governance log failure",
            "metadata": {
                "execution_kind": "body_switch",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "git_lineage": {
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                    "changed_files": ["systems/body_registry.py"],
                },
            },
        }
    )

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    def failing_record_boundary_defer(**_kwargs):
        raise RuntimeError("mem bridge unavailable")

    supervisor._governor.record_boundary_defer = failing_record_boundary_defer  # type: ignore[method-assign]

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T00:15:00"},
        }
    )

    task = result["tasks"][0]
    assert task["status"] == "deferred"
    assert task["execution_request"] is None
    assert task["evolution_boundary"]["violations"] == ["systems/body_registry.py"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cancelled_task_is_terminal_for_supervisor_decisioning(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task({"title": "Prepare guarded runtime experiment"})
    task_id = planned["tasks"][0]["task_id"]

    cancel_result = await supervisor.decide_self_evolution_task(
        task_id,
        {
            "decision": "cancel",
            "reason": "Operator cancelled this task family for the current cycle.",
        },
    )
    assert cancel_result["status"] == "cancelled"

    follow_up = await supervisor.decide_self_evolution_task(
        task_id,
        {
            "decision": "approve",
        },
    )
    assert follow_up["status"] == "unchanged"
    assert follow_up["task"]["status"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_self_evolution_approval_builds_formal_execution_request(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Promote cultivated body slot",
            "metadata": {
                "execution_kind": "body_switch",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "probe_report_ref": "probe-reports/slot-B/latest.json",
                "git_lineage": {
                    "source_commit": "aaa111",
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                    "diff_summary": "Improve body runtime isolation.",
                    "changed_files": ["agent/stream_handler.py"],
                },
            },
            "constraints": {
                "rollback_plan": {
                    "strategy": "restore_retired_slot",
                    "retired_slot": "slot-A",
                }
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    result = await supervisor.decide_self_evolution_task(
        task_id,
        {
            "decision": "approve",
            "actor": "mem_supervisor",
            "reason": "Probe and lineage evidence are sufficient for formal handoff.",
        },
    )

    execution_request = result["task"]["execution_request"]
    assert result["status"] == "approved"
    assert result["task"]["governance_task_type"] == "self_evolution"
    assert result["task"]["task_family"] == "body_switch"
    assert result["task"]["execution_kind"] == "body_switch"
    assert execution_request["status"] == "approved_for_execution"
    assert execution_request["task_type"] == "self_evolution"
    assert execution_request["trace_id"] == result["task"]["trace_id"]
    assert execution_request["decision_id"] == result["task"]["decision_history"][-1]["decision_id"]
    assert execution_request["kind"] == "general_self_evolution"
    assert execution_request["target_slot_id"] == "slot-B"
    assert execution_request["git_lineage"]["candidate_commit"] == "bbb222"
    assert execution_request["git_lineage"]["rollback_commit"] == "aaa111"
    assert execution_request["probe_report_ref"] == "probe-reports/slot-B/latest.json"
    assert execution_request["governor_decision"]["actor"] == "mem_supervisor"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.skip(reason="evolution_boundary removed — body switching validation no longer in task queue path")
async def test_body_self_evolution_task_api_exposes_boundary_summary_before_approval(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Review candidate boundary before approval",
            "metadata": {
                "execution_kind": "body_switch",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "git_lineage": {
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                    "changed_files": [
                        "agent/stream_handler.py",
                        "systems/body_registry.py",
                    ],
                },
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    listed = await supervisor.list_self_evolution_tasks()
    fetched = await supervisor.get_self_evolution_task(task_id)

    planned_boundary = planned["tasks"][0]["evolution_boundary"]
    listed_boundary = listed["tasks"][0]["evolution_boundary"]
    fetched_boundary = fetched["evolution_boundary"]

    assert planned["tasks"][0]["governance_task_type"] == "self_evolution"
    assert planned["tasks"][0]["task_family"] == "body_switch"
    assert planned["tasks"][0]["execution_kind"] == "body_switch"
    assert listed["tasks"][0]["task_family"] == "body_switch"
    assert fetched["task_family"] == "body_switch"
    assert planned_boundary["ok"] is False
    assert planned_boundary["allowed_files"] == ["agent/stream_handler.py"]
    assert planned_boundary["forbidden_files"] == ["systems/body_registry.py"]
    assert listed_boundary == planned_boundary
    assert fetched_boundary == planned_boundary


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.skip(reason="Body boundary validation removed — body_upgrade not driven by task queue")
async def test_body_self_evolution_approval_rejects_mother_system_changes(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Reject mixed mother-system body proposal",
            "metadata": {
                "execution_kind": "body_upgrade",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "probe_report_ref": "probe-reports/slot-B/latest.json",
                "git_lineage": {
                    "source_commit": "aaa111",
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                    "diff_summary": "Accidentally mixed body runtime and registry changes.",
                    "changed_files": [
                        "agent/stream_handler.py",
                        "systems/body_registry.py",
                    ],
                },
            },
            "constraints": {
                "rollback_plan": {
                    "strategy": "restore_retired_slot",
                    "retired_slot": "slot-A",
                }
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    with pytest.raises(Exception) as exc_info:
        await supervisor.decide_self_evolution_task(
            task_id,
            {
                "decision": "approve",
                "actor": "mem_supervisor",
            },
        )

    assert "outside the child-agent boundary" in str(exc_info.value)
    assert "systems/body_registry.py" in str(exc_info.value)
    queued = await supervisor.get_self_evolution_task(task_id)
    assert queued["status"] == "planned"
    assert queued["execution_request"] is None


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.skip(reason="Body validation removed — SelfEvolutionExecutionRequest no longer validates git_lineage")
async def test_body_self_evolution_approval_requires_git_lineage_and_rollback(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Unsafe body switch proposal",
            "metadata": {
                "execution_kind": "body_switch",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "git_lineage": {
                    "candidate_commit": "bbb222",
                },
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    with pytest.raises(Exception) as exc_info:
        await supervisor.decide_self_evolution_task(
            task_id,
            {
                "decision": "approve",
                "actor": "mem_supervisor",
            },
        )

    assert "git_lineage.rollback_commit" in str(exc_info.value)
    queued = await supervisor.get_self_evolution_task(task_id)
    assert queued["status"] == "planned"
    assert queued["execution_request"] is None


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.skip(reason="Body validation removed — SelfEvolutionExecutionRequest no longer validates changed_files")
async def test_body_self_evolution_approval_requires_changed_files(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Unsafe body switch without auditable diff",
            "metadata": {
                "execution_kind": "body_switch",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "git_lineage": {
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                },
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    with pytest.raises(Exception) as exc_info:
        await supervisor.decide_self_evolution_task(
            task_id,
            {
                "decision": "approve",
                "actor": "mem_supervisor",
            },
        )

    assert "git_lineage.changed_files" in str(exc_info.value)
    queued = await supervisor.get_self_evolution_task(task_id)
    assert queued["status"] == "planned"
    assert queued["execution_request"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_self_learning_followup_auto_approval_does_not_build_execution_request(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Review newly collected learning evidence",
            "task_family": "self_learning",
            "source": "self_learning",
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T11:40:00",
            "last_agent_work_at": "2026-05-25T11:40:00",
            "last_memory_task_at": "2026-05-25T11:40:00",
            "last_self_learning_activity_at": "2026-05-25T11:40:00",
            "last_self_evolution_activity_at": "2026-05-25T11:40:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.decide_self_evolution_task(
        task_id,
        {
            "decision": "auto",
            "idle_window": {
                "now": "2026-05-25T12:00:00",
            },
        },
    )

    assert result["status"] == "approved"
    assert result["task"]["status"] == "approved"
    assert result["task"]["governance_task_type"] == "self_learning"
    assert result["task"]["task_family"] == "self_learning"
    assert result["task"]["execution_kind"] is None
    assert result["task"]["execution_request"] is None
    decision = result["task"]["decision_history"][-1]
    assert decision["trace_id"] == result["task"]["trace_id"]
    assert decision["task_type"] == "self_learning_followup"
    assert decision["governance_task_type"] == "self_learning"
    assert decision["task_family"] == "self_learning"
    assert decision["execution_kind"] is None
    assert decision["decision_id"]
    idle_window = decision["context"]["idle_window"]
    assert idle_window["governance_task_type"] == "self_learning"
    assert idle_window["task_family"] == "self_learning"
    assert isinstance(idle_window["checks"]["in_execution_window"], bool)
    assert idle_window["decisions"]["eligible_for_execution"] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_memory_maintenance_auto_decision_defers_when_memory_activity_is_recent(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Run memory compression sweep",
            "execution_kind": "memory_maintenance",
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:14:30",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.decide_self_evolution_task(
        task_id,
        {
            "decision": "auto",
            "idle_window": {
                "now": "2026-05-25T00:15:00",
            },
        },
    )

    assert result["status"] == "deferred"
    assert result["task"]["status"] == "deferred"
    assert result["task"]["governance_task_type"] == "memory_maintenance"
    assert result["task"]["task_family"] == "memory_maintenance"
    assert result["task"]["execution_kind"] == "memory_maintenance"
    assert result["task"]["execution_request"] is None
    assert result["task"]["decision_history"][-1]["governance_task_type"] == "memory_maintenance"
    assert result["task"]["decision_history"][-1]["task_family"] == "memory_maintenance"
    assert result["task"]["decision_history"][-1]["execution_kind"] == "memory_maintenance"
    assert "memory maintenance requires the execution window plus idle user, runtime, memory" in result["task"]["decision_reason"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_upgrade_and_body_switch_keep_distinct_task_family_metadata(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    result = await supervisor.plan_self_evolution_task(
        {
            "items": [
                {
                    "title": "Prepare body upgrade handoff",
                    "execution_kind": "body_upgrade",
                },
                {
                    "title": "Prepare body switch handoff",
                    "execution_kind": "body_switch",
                },
            ]
        }
    )

    tasks_by_title = {task["title"]: task for task in result["tasks"]}
    upgrade_task = tasks_by_title["Prepare body upgrade handoff"]
    switch_task = tasks_by_title["Prepare body switch handoff"]

    assert upgrade_task["governance_task_type"] == "self_evolution"
    assert upgrade_task["task_family"] == "body_upgrade"
    assert upgrade_task["execution_kind"] == "body_upgrade"
    assert switch_task["governance_task_type"] == "self_evolution"
    assert switch_task["task_family"] == "body_switch"
    assert switch_task["execution_kind"] == "body_switch"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runtime_profile_prefers_canonical_task_fields_over_broad_task_type(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Canonical body switch should survive serialization",
            "task_type": "self_evolution",
            "task_family": "body_switch",
            "execution_kind": "body_switch",
            "metadata": {
                "task_family": "body_switch",
                "execution_kind": "body_switch",
            },
        }
    )

    task = planned["tasks"][0]
    listed = await supervisor.list_self_evolution_tasks()
    fetched = await supervisor.get_self_evolution_task(task["task_id"])

    assert task["task_type"] == "self_evolution"
    assert task["governance_task_type"] == "self_evolution"
    assert task["task_family"] == "body_switch"
    assert task["execution_kind"] == "body_switch"
    assert listed["tasks"][0]["task_family"] == "body_switch"
    assert listed["tasks"][0]["execution_kind"] == "body_switch"
    assert fetched["task_family"] == "body_switch"
    assert fetched["execution_kind"] == "body_switch"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_list_self_evolution_tasks_can_filter_body_improvement_agent_tasks(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    await supervisor.plan_self_evolution_task(
        {
            "title": "Improve shell body after learning",
            "task_family": "body_upgrade",
            "execution_kind": "body_improvement",
            "metadata": {
                "task_family": "body_upgrade",
                "execution_kind": "body_improvement",
            },
        }
    )

    listed = await supervisor.list_self_evolution_tasks(
        status="approved",
        execution_kind="body_improvement",
    )

    assert listed["count"] == 0

    task_id = supervisor._self_evolution_queue.list_tasks()[0].task_id
    await supervisor.decide_self_evolution_task(
        task_id,
        {"decision": "approve", "actor": "supervisor", "reason": "ready for agent pull"},
    )

    listed = await supervisor.list_self_evolution_tasks(
        status="approved",
        execution_kind="body_improvement",
    )

    assert listed["count"] == 1
    assert listed["tasks"][0]["execution_kind"] == "body_improvement"
    assert listed["tasks"][0]["execution_request"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_self_evolution_cycle_recovers_orphaned_agent_pull_running_task(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Recover abandoned learning task",
            "summary": "Ensure orphaned AUTO tasks return to approved",
            "task_type": "self_learning",
            "source": "endogenous_drive",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    supervisor._self_evolution_queue.update_status(
        task_id,
        status="approved",
        actor="test",
        reason="ready",
    )
    supervisor._self_evolution_queue.update_status(
        task_id,
        status="running",
        actor="cli_agent",
        reason="Agent pulled task for execution in AUTO mode.",
        context={"session_id": "stale-cli-session"},
    )
    supervisor._self_evolution_queue.update_metadata(
        task_id,
        metadata={
            "owner_session_id": "stale-cli-session",
            "execution_source": "cli_agent_pull",
            "execution_started_at": "2026-06-26T14:00:00+00:00",
        },
    )

    async def fake_active_executor():
        return {"session_id": "fresh-cli-session", "scene": "idle"}

    async def fake_review(request=None):
        return {"count": 0, "tasks": [], "decision": "approved", "reviewed_statuses": [], "idle_window": {}}

    supervisor._fetch_gateway_active_cli_executor = fake_active_executor  # type: ignore[method-assign]
    supervisor.review_self_evolution_tasks = fake_review  # type: ignore[method-assign]

    result = await supervisor._run_self_evolution_cycle()
    updated = await supervisor.get_self_evolution_task(task_id)

    assert result["recovered_orphaned"] == 1
    assert updated["status"] == "approved"
    assert updated["metadata"]["recovered_from_orphaned_running"] is True
    assert updated["decision_history"][-1]["context"]["previous_owner_session_id"] == "stale-cli-session"
    assert updated["decision_history"][-1]["context"]["active_cli_session_id"] == "fresh-cli-session"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_recovery_skipped_when_gateway_active_executor_fetch_fails(tmp_path):
    # P0-3 恢复保守化: when the active CLI executor cannot be determined (gateway
    # unreachable / 5xx), recovery must be a conservative no-op rather than
    # treating active as "" and recovering every running agent-pull task
    # (which would cause mass false recovery → double execution).
    supervisor = _make_supervisor(tmp_path)

    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Running learning task owned by a live CLI",
            "summary": "Must NOT be recovered while gateway is unreachable",
            "task_type": "self_learning",
            "source": "endogenous_drive",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]
    supervisor._self_evolution_queue.update_status(
        task_id, status="approved", actor="test", reason="ready",
    )
    supervisor._self_evolution_queue.update_status(
        task_id, status="running", actor="cli_agent",
        reason="Agent pulled task for execution in AUTO mode.",
        context={"session_id": "live-cli-session"},
    )
    supervisor._self_evolution_queue.update_metadata(
        task_id,
        metadata={
            "owner_session_id": "live-cli-session",
            "execution_source": "cli_agent_pull",
        },
    )

    async def failing_active_executor():
        raise HTTPException(status_code=503, detail="gateway down")

    supervisor._fetch_gateway_active_cli_executor = failing_active_executor  # type: ignore[method-assign]

    recovered = await supervisor._recover_orphaned_agent_pull_tasks()
    updated = await supervisor.get_self_evolution_task(task_id)

    assert recovered == 0
    assert updated["status"] == "running"
    assert updated["metadata"].get("recovered_from_orphaned_running") is not True
