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
from systems.config import load_config_from_env


def _make_supervisor_config(tmp_path: Path) -> SupervisorConfig:
    return SupervisorConfig(
        execution=SupervisorExecutionConfig(git_repo_path=str(tmp_path))
    )


def _make_supervisor(tmp_path: Path) -> Supervisor:
    return Supervisor(_make_supervisor_config(tmp_path))


class _FakeLLMClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


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
    assert first["planned"] == 4
    assert second["status"] == "idle"
    assert queued["count"] == 4
    tasks_by_key = {
        task["metadata"]["endogenous_drive_key"]: task for task in queued["tasks"]
    }
    assert "continuity:memory_maintenance_sweep" in tasks_by_key
    assert "truthfulness:review_correction_signals" in tasks_by_key
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
    assert "endogenous_drive_evaluated" in event_types
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
async def test_evaluate_endogenous_drive_exposes_cognition_state(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor._endogenous_drive_engine._latest_lm_task_generation_context = {
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
        "summary": "LM task generation status=completed.",
    }

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
    assert cognition["proposal_cognition"]["task_type_priors"]["top_priority_task_type"] == "observation"
    assert cognition["proposal_cognition"]["proposal_drift_memory"]["drift_state"] == "correcting"
    assert cognition["proposal_cognition"]["lm_reasoning_state"]["charter"]["core_mission"]
    assert cognition["proposal_cognition"]["current_candidates"]["count"] >= 1
    assert cognition["attention_agenda"]["active_count"] >= 1
    assert cognition["attention_agenda"]["entries"][0]["topic"]
    assert cognition["uncertainty_ledger"]["active_count"] >= 1
    assert cognition["uncertainty_ledger"]["entries"][0]["hypothesis"]
    assert cognition["observation_program"]["active_count"] >= 1
    assert cognition["observation_program"]["entries"][0]["target"]
    assert cognition["meta_governance"]["mode"] in {"observe", "correct", "expand", "conserve"}
    assert cognition["meta_governance"]["confidence"] >= 0
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
    assert history["strategy_memory"]["focus_stats"][preferred_focus]["dragging"] >= 1
    assert history["strategy_memory"]["contextual_focus_stats"][context_key][preferred_focus]["judged"] >= 1
    assert history["strategy_memory"]["contextual_focus_stats"][context_key][preferred_focus]["dragging"] >= 1


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
    supervisor._endogenous_drive_engine._latest_lm_task_generation_context = {
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
        "summary": "LM task generation status=completed.",
    }

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
    assert snapshot["state"]["proposal_cognition"]["task_type_priors"]["top_priority_task_type"] == "review"
    assert snapshot["state"]["proposal_cognition"]["lm_reasoning_state"]["charter"]["core_mission"] == "Evolve through evidence-backed structured proposals."
    assert snapshot["state"]["proposal_cognition"]["current_candidates"]["count"] >= 1


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
    supervisor._endogenous_drive_engine._latest_lm_task_generation_context = {
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
        "summary": "LM task generation status=completed.",
    }

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
    assert proposal_cognition["recent_cognitive_alignment"]["average_score"] < 0.5


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
    supervisor._endogenous_drive_engine._latest_lm_task_generation_context = {
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
        "summary": "LM task generation status=completed.",
    }

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
    supervisor._endogenous_drive_engine._latest_lm_task_generation_context = {
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
    }

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
    supervisor._endogenous_drive_engine._latest_lm_task_generation_context = {
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
    }

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
    supervisor._endogenous_drive_engine._latest_lm_task_generation_context = {
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
    }

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
    supervisor._endogenous_drive_engine._latest_lm_task_generation_context = {
        "status": "completed",
        "proposal_drift_memory": {"available": True, "average_score": 0.78, "drift_state": "stable"},
        "recent_reference_alignment": {"available": True, "average_alignment_score": 0.82, "weak_or_partial_count": 0},
        "evidence_basis": {"self_iteration_readiness_score": 0.76, "self_understanding_gaps": [], "weak_or_missing_channels": []},
    }

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
    supervisor._endogenous_drive_engine._latest_lm_task_generation_context = {
        "status": "completed",
        "proposal_drift_memory": {"available": True, "average_score": 0.62, "drift_state": "stable"},
        "recent_reference_alignment": {"available": True, "average_alignment_score": 0.55, "weak_or_partial_count": 3},
        "evidence_basis": {
            "self_iteration_readiness_score": 0.64,
            "self_understanding_gaps": ["reference_alignment_is_unstable"],
            "weak_or_missing_channels": ["external_research", "shell_body_profile", "recent_learning"],
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
    supervisor._endogenous_drive_engine._latest_lm_task_generation_context = {
        "status": "completed",
        "proposal_drift_memory": {"available": True, "average_score": 0.7, "drift_state": "stable"},
        "recent_reference_alignment": {"available": True, "average_alignment_score": 0.78, "weak_or_partial_count": 0},
        "evidence_basis": {"self_iteration_readiness_score": 0.7, "self_understanding_gaps": [], "weak_or_missing_channels": []},
    }

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
    supervisor._endogenous_drive_engine._latest_lm_task_generation_context = {
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
    supervisor._endogenous_drive_engine._latest_lm_task_generation_context = {
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
    supervisor._endogenous_drive_engine._latest_lm_task_generation_context = {
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
    }

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
async def test_endogenous_drive_lm_task_generation_is_disabled_by_default(tmp_path):
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
    assert "【认知宪章：自我迭代护栏】" in system_prompt
    assert "先理解自身结构，再决定是否提出升级。" in system_prompt
    assert "必须综合 evidence_channels 与历史纠偏结果。" in system_prompt
    assert "优先提出能提升自我理解质量的结构化任务。" in system_prompt
    assert "不得抢占 API-A 的对外服务链路。" in system_prompt


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
    assert candidate["metadata"]["cognitive_alignment"]["quality"] in {"strong", "partial"}


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
    else:
        posture = result["cognition_state"]["proposal_cognition"]["active_cognitive_posture_profile"]
        assert posture["name"] in {"observe_first", "truthfulness_first", "evidence_repair_first"}
        assert result["cognitive_self_regulation"]["dynamic_observation_bias_boost"] > 0.0


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
    assert "Review evidence drift" in payload
    assert "\"alignment_quality\": \"partial\"" in payload
    assert "\"missing_evidence_nodes\": [\"self_structure\"]" in payload
    assert "\"missing_agenda_nodes\": [\"focus:learning_expansion\"]" in payload


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
    assert "Repair evidence references" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_passes_evidence_credibility_and_task_type_priors_to_lm(tmp_path):
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
    assert "task_type_priors" in payload
    assert "\"high_credibility_channels\":" in payload
    assert "\"weak_or_missing_channels\":" in payload
    assert "\"top_priority_task_type\":" in payload
    assert "\"task_type\": \"observation\"" in payload or "\"task_type\": \"review\"" in payload
    assert "reference_alignment_is_not_yet_stable" in payload


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
    assert "Misaligned improvement attempt" in payload
    assert "\"drift_state\": \"correcting\"" in payload or "\"drift_state\": \"drifting\"" in payload
    assert "\"quality_counts\":" in payload
    assert "\"common_posture_alignment\":" in payload
    assert "\"common_priority_basis\":" in payload
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
    supervisor._endogenous_drive_engine._latest_lm_task_generation_context = {
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
            "common_posture_alignment": [
                "claims improvement is acceptable under observe_first",
            ],
            "common_priority_basis": [
                "push execution despite unresolved evidence gaps",
            ],
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
        "summary": "LM task generation status=completed.",
    }

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

    assert proposal_cognition["proposal_drift_memory"]["common_posture_alignment"] == [
        "claims improvement is acceptable under observe_first",
    ]
    assert proposal_cognition["proposal_drift_memory"]["common_priority_basis"] == [
        "push execution despite unresolved evidence gaps",
    ]
    assert proposal_cognition["proposal_drift_memory"]["posture_alignment_health"] == "inconsistent"
    assert proposal_cognition["recent_cognitive_alignment"]["common_posture_alignment"] == [
        "claims improvement is acceptable under observe_first",
    ]


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

    state = supervisor._endogenous_drive_engine.get_latest_lm_task_generation_context()
    assert state["cognitive_posture"]["name"] == "observe_first"
    assert state["cognitive_posture"]["selection_mode"] == "manual"


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
        assert "\"top_priority_task_type\": \"observation\"" in prompt_payload or "\"top_priority_task_type\": \"review\"" in prompt_payload
        assert "proposal_drift_requires_more_observation" in prompt_payload or (
            "review_can_help_correct_recent_proposal_drift" in prompt_payload
        )
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
