"""Tests for endogenous drive with real gateway activity data (T-05),
CLI executor canonical path (T-07), configuration validation (T-08),
learning conclusion store compatibility layer (T-09), service runtime lifecycle (T-06),
and endogenous drive error bridge (S-05)."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from systems.supervisor.endogenous_drive import EndogenousDriveEngine
from systems.supervisor.supervisor import SupervisorConfig


# ── T-05: Endogenous drive with real gateway activity data ──────────

class TestEndogenousDriveWithGatewayData:
    """Verify the drive engine produces correct candidates from realistic
    gateway activity snapshots (T-05)."""

    def test_drive_generates_memory_maintenance_when_eligible(self):
        engine = EndogenousDriveEngine()
        drive_input = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {"counts": {}, "active_sessions": 0},
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True},
                "self_learning": {"eligible_for_planning": False},
                "general_self_evolution": {"eligible_for_planning": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True},
                "self_learning": {"eligible_for_planning": False},
                "self_evolution": {"eligible_for_planning": False},
            },
        }
        candidates = engine.generate_candidates(
            drive_input=drive_input, existing_drive_keys=set(), max_candidates=5
        )
        keys = {c.stable_key for c in candidates}
        assert "continuity:memory_maintenance_sweep" in keys
        mem = next(c for c in candidates if c.stable_key == "continuity:memory_maintenance_sweep")
        assert mem.governance_task_type == "memory_maintenance"
        assert mem.priority == "high"
        assert "observation_checks" in mem.evidence
        assert "activity_guard_checks" not in mem.evidence

    def test_memory_maintenance_urgency_ignores_generic_agent_idle(self):
        engine = EndogenousDriveEngine()

        formal = {
            "idle_seconds": {
                "user": 900,
                "api_a_execution": 0,
                "agent": 900,
                "memory": 900,
            }
        }
        missing_api_a_execution = {
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            }
        }
        no_agent = {
            "idle_seconds": {
                "user": 900,
                "memory": 900,
            }
        }

        formal_idle = {
            "idle_seconds": {
                "user": 900,
                "api_a_execution": 900,
                "agent": 0,
                "memory": 900,
            }
        }

        assert engine._memory_maintenance_urgency(missing_api_a_execution) == engine._memory_maintenance_urgency(no_agent)
        assert engine._memory_maintenance_urgency(formal) == engine._memory_maintenance_urgency(no_agent)
        assert engine._memory_maintenance_urgency(formal_idle) > engine._memory_maintenance_urgency(missing_api_a_execution)

    def test_drive_generates_truthfulness_when_errors_exist(self):
        engine = EndogenousDriveEngine()
        drive_input = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000},
            "activity": {"counts": {"error_count": 5, "uncertainty_high_count": 3}, "active_sessions": 0},
            "correction_signals": 8,
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": False},
                "general_self_evolution": {"eligible_for_planning": False},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": False},
                "self_evolution": {"eligible_for_planning": False},
            },
        }
        candidates = engine.generate_candidates(
            drive_input=drive_input, existing_drive_keys=set(), max_candidates=5
        )
        keys = {c.stable_key for c in candidates}
        assert "truthfulness:review_correction_signals" in keys
        truth = next(c for c in candidates if c.stable_key == "truthfulness:review_correction_signals")
        assert truth.priority == "high"
        assert truth.governance_task_type == "self_learning"
        assert truth.evidence["signal_source"] == "runtime_observation_snapshot"

    def test_drive_skips_existing_keys(self):
        engine = EndogenousDriveEngine()
        drive_input = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {"counts": {}, "active_sessions": 0},
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True},
                "self_learning": {"eligible_for_planning": False},
                "general_self_evolution": {"eligible_for_planning": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True},
                "self_learning": {"eligible_for_planning": False},
                "self_evolution": {"eligible_for_planning": False},
            },
        }
        existing = {"continuity:memory_maintenance_sweep"}
        candidates = engine.generate_candidates(
            drive_input=drive_input, existing_drive_keys=existing, max_candidates=5
        )
        keys = {c.stable_key for c in candidates}
        assert "continuity:memory_maintenance_sweep" not in keys

    def test_drive_input_alias_drives_deliberation_and_candidates(self):
        engine = EndogenousDriveEngine()
        drive_input = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {"counts": {"error_count": 2}, "active_sessions": 0},
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True},
                "self_learning": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True},
                "self_learning": {"eligible_for_planning": True},
                "self_evolution": {"eligible_for_planning": False},
            },
        }

        report = engine.build_deliberation_report(drive_input=drive_input).to_dict()
        candidates = engine.generate_candidates(
            drive_input=drive_input,
            existing_drive_keys=set(),
            max_candidates=5,
        )

        assert report["perception"]["recent_errors"] == 2
        assert report["perception"]["api_b_judgement_count"] == 0
        assert len(candidates) >= 1


# ── T-06: Service runtime lifecycle ─────────────────────────────────

class TestServiceRuntimeLifecycle:
    """Verify supervisor periodic task start/stop lifecycle (T-06)."""

    def test_periodic_tasks_start_and_stop(self, tmp_path):
        from systems.supervisor.supervisor import Supervisor, SupervisorConfig
        cfg = SupervisorConfig()
        cfg.soul_store_path = str(tmp_path)
        cfg.execution.git_repo_path = str(tmp_path)
        cfg.body_runtime.state_root = str(tmp_path / "body-state")
        (tmp_path / ".git").mkdir(exist_ok=True)
        sv = Supervisor(config=cfg)
        sv._fetch_gateway_activity_snapshot = AsyncMock(return_value={
            "last_user_request_at": None, "last_agent_work_at": None,
            "last_memory_task_at": None, "counts": {}, "active_sessions": 0,
        })
        sv._agents = {}
        sv._governor = Mock()
        sv._governor.list_history = Mock(return_value=[])
        sv._governor.get_latest = Mock(return_value=None)
        sv._governor.record_boundary_defer = Mock()
        sv._body_registry = Mock()
        sv._body_registry.initialize_layout = Mock()
        sv._body_registry.load_registry = Mock()
        sv._body_registry.load_slot_meta = Mock()
        sv._execution_facade = Mock()
        sv._execution_facade.execute_autonomous_chain_request = AsyncMock()
        sv._execution_facade.get_body_registry = Mock(return_value={})
        sv._execution_facade.list_body_slots = Mock(return_value={"slots": {}})
        sv._endogenous_drive_task = None
        sv._run_endogenous_drive_cycle = AsyncMock(return_value={"status": "idle", "planned": 0})
        sv._run_autonomous_chain_review_cycle = AsyncMock(return_value={"reviewed": 0, "handed_off": []})
        sv._touch_gateway_activity = AsyncMock()
        sv._memory_maintenance_executor = Mock()
        sv._ensure_watch_window_task = Mock()
        sv._watch_window_runtime = Mock()

        async def _run():
            await sv._start_periodic_tasks()
            assert sv._service_runtime_started is True
            assert sv._health_check_task is not None
            assert sv._service_runtime.autonomous_chain_gate_active is False
            assert sv._autonomous_chain_review_task is None
            assert sv._endogenous_drive_task is None

            await sv._start_autonomous_chain_gate()
            assert sv._service_runtime.autonomous_chain_gate_active is True
            assert sv._autonomous_chain_review_task is not None
            assert sv._endogenous_drive_task is not None
            await sv._stop_periodic_tasks()
            assert sv._service_runtime_started is False
            assert sv._service_runtime.autonomous_chain_gate_active is False
        asyncio.run(_run())


# ── T-07: CLI executor canonical path ───────────────────────────────

class TestCLIExecutorCanonicalPath:
    """Verify CLI executor commands go through canonical executor path (T-07)."""

    def test_execution_facade_routes_self_evolution_to_executor(self):
        from systems.execution.facade import VoidCubeExecutionFacade

        body_upgrade = AsyncMock()
        body_upgrade.execute_body_upgrade = AsyncMock(return_value={"status": "ok"})
        memory_maintenance = AsyncMock()
        memory_maintenance.trigger_memory_compression = AsyncMock(return_value={"status": "ok"})
        facade = VoidCubeExecutionFacade(
            watch_window=Mock(),
            body_lifecycle=Mock(),
            body_upgrade=body_upgrade,
            memory_maintenance=memory_maintenance,
        )

        async def _run():
            from systems.supervisor.autonomous_chain_store import AutonomousChainExecutionRequest
            req = AutonomousChainExecutionRequest(
                task_id="t1", kind="general_self_evolution",
                git_lineage={"source_commit": "def", "candidate_commit": "abc", "rollback_commit": "def", "changed_files": ["agent/x.py"]},
                target_slot_id="slot-B",
            )
            result = await facade.execute_autonomous_chain_request(req.model_dump(mode="json"))
            assert result["status"] == "autonomous_chain_execution_executed"
            assert result["execution_metadata"]["execution_kind"] == "general_self_evolution"

            req2 = AutonomousChainExecutionRequest(task_id="t2", kind="memory_maintenance")
            result2 = await facade.execute_autonomous_chain_request(req2.model_dump(mode="json"))
            assert result2["execution_metadata"]["execution_kind"] == "memory_maintenance"

            req3 = AutonomousChainExecutionRequest(
                task_id="t3", kind="general_self_evolution",
                git_lineage={"source_commit": "def", "candidate_commit": "abc", "rollback_commit": "def", "changed_files": ["agent/x.py"]},
                target_slot_id="slot-B",
            )
            result3 = await facade.execute_autonomous_chain_request(req3.model_dump(mode="json"))
            assert result3["execution_metadata"]["execution_kind"] == "general_self_evolution"

        asyncio.run(_run())


# ── T-08: Configuration validation ──────────────────────────────────

class TestConfigurationValidation:
    """Verify configuration parsing, defaults, and env overrides (T-08)."""

    def test_supervisor_config_defaults(self):
        cfg = SupervisorConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 6002
        assert cfg.service_runtime.endogenous_drive_enabled is True
        assert "autonomous_chain_start_on_boot" not in type(cfg.service_runtime).model_fields
        assert cfg.service_runtime.autonomous_chain_review_interval == 300

    def test_supervisor_config_segmented(self):
        from systems.supervisor.config_models import (
            SupervisorExecutionConfig, SupervisorServiceRuntimeConfig, SupervisorBodyRuntimeConfig,
        )
        cfg = SupervisorConfig(
            execution=SupervisorExecutionConfig(gateway_address="http://gw:6000"),
            service_runtime=SupervisorServiceRuntimeConfig(
                endogenous_drive_interval=600,
            ),
            body_runtime=SupervisorBodyRuntimeConfig(state_root="custom-body-state"),
        )
        assert cfg.execution.gateway_address == "http://gw:6000"
        assert cfg.service_runtime.endogenous_drive_interval == 600
        assert cfg.body_runtime.state_root == "custom-body-state"

    def test_supervisor_config_ui_disabled(self):
        cfg = SupervisorConfig(ui_enabled=False, ui_auto_open=False)
        assert cfg.ui_enabled is False
        assert cfg.ui_auto_open is False


# ── S-05: Endogenous drive error signal bridge ──────────────────────

class TestEndogenousDriveErrorBridge:
    """Verify endogenous drive correctly bridges gateway error signals (S-05)."""

    def test_error_count_triggers_high_priority_truthfulness(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000},
            "activity": {"counts": {"error_count": 4, "uncertainty_high_count": 2}, "active_sessions": 0},
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": False},
                "general_self_evolution": {"eligible_for_planning": False},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": False},
                "self_evolution": {"eligible_for_planning": False},
            },
        }
        candidates = engine.generate_candidates(drive_input=idle, existing_drive_keys=set(), max_candidates=5)
        truth = [c for c in candidates if c.stable_key == "truthfulness:review_correction_signals"]
        assert len(truth) == 1
        assert truth[0].priority == "high"
        assert truth[0].utility >= 0.90

    def test_truthfulness_outranks_exploratory_learning_when_correction_signals_are_high(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {
                "counts": {"error_count": 4, "uncertainty_high_count": 2},
                "active_sessions": 0,
                "recent_metadata": {
                    "user_request": {
                        "text": "Investigate autonomous backlog scheduling fairness across body improvement and learning tasks"
                    }
                },
            },
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": False},
                "general_self_evolution": {"eligible_for_planning": False},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": False},
                "self_evolution": {"eligible_for_planning": False},
            },
        }
        candidates = engine.generate_candidates(drive_input=idle, existing_drive_keys=set(), max_candidates=5)
        truth = next(c for c in candidates if c.stable_key == "truthfulness:review_correction_signals")
        exploratory = next(
            c for c in candidates
            if c.governance_task_type == "self_learning"
            and c.stable_key != "truthfulness:review_correction_signals"
        )
        assert truth.utility > exploratory.utility
        assert truth.metadata["score_breakdown"]["candidate_kind"] == "truthfulness_review"
        assert exploratory.metadata["score_breakdown"]["candidate_kind"] == "exploratory_learning"

    def test_candidates_include_score_breakdown_for_auditable_ranking(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {"counts": {}, "active_sessions": 0},
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True},
                "self_learning": {"eligible_for_planning": False},
                "general_self_evolution": {"eligible_for_planning": False},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True},
                "self_learning": {"eligible_for_planning": False},
                "self_evolution": {"eligible_for_planning": False},
            },
        }
        candidates = engine.generate_candidates(drive_input=idle, existing_drive_keys=set(), max_candidates=3)
        memory_candidate = next(c for c in candidates if c.stable_key == "continuity:memory_maintenance_sweep")
        breakdown = memory_candidate.metadata.get("score_breakdown") or {}
        judgement_item = memory_candidate.to_api_b_judgement_item()
        endogenous_evidence = judgement_item["evidence"]["endogenous_drive"]

        assert breakdown["score_model"] == "endogenous_drive_v2"
        assert breakdown["candidate_kind"] == "memory_maintenance"
        assert "dimensions" in breakdown
        assert "penalties" in breakdown
        assert "stable_key" not in judgement_item
        assert "value_tags" not in judgement_item
        assert "utility" not in judgement_item
        assert judgement_item["rationale"]
        assert judgement_item["metadata"]["endogenous_drive_key"] == memory_candidate.stable_key
        assert judgement_item["metadata"]["core_values"] == memory_candidate.value_tags
        assert judgement_item["metadata"]["utility"] == memory_candidate.utility
        assert endogenous_evidence["score_breakdown"]["utility"] == memory_candidate.utility

    def test_candidates_include_drive_judgement_layers(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {"counts": {"error_count": 3, "uncertainty_high_count": 1}, "active_sessions": 0},
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "self_evolution": {"eligible_for_planning": True},
            },
        }
        candidates = engine.generate_candidates(drive_input=idle, existing_drive_keys=set(), max_candidates=5)
        truth = next(c for c in candidates if c.stable_key == "truthfulness:review_correction_signals")
        judgement = truth.metadata.get("drive_judgement") or {}

        assert judgement["perception"]["correction_signals"] == 4
        assert judgement["world_model"]["truthfulness_pressure"] > 0
        assert "reflection" in judgement
        assert judgement["reflection"]["autonomy_readiness"] >= 0
        assert judgement["intent"]["intent_type"] == "review_truthfulness_signals"
        assert any(need["need_type"] == "repair_truthfulness" for need in judgement["needs"])

    def test_shell_baseline_candidate_includes_drive_judgement(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {"counts": {}, "active_sessions": 0, "recent_metadata": {}},
            "shell_slot": {
                "slot_id": "slot-B",
                "worktree_path": "F:/tmp/slot-B/worktree",
            },
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": False},
                "general_self_evolution": {"eligible_for_planning": False},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": False},
                "self_evolution": {"eligible_for_planning": False},
            },
        }
        candidates = engine.generate_candidates(drive_input=idle, existing_drive_keys=set(), max_candidates=5)
        baseline = next(
            c for c in candidates
            if c.metadata.get("self_learning_mode") == "shell_codebase_baseline"
        )
        judgement = baseline.metadata.get("drive_judgement") or {}

        assert judgement["intent"]["candidate_kind"] == "shell_baseline_learning"
        assert judgement["perception"]["learning_quality"] == 0.0
        assert any(need["need_type"] == "expand_learning_frontier" for need in judgement["needs"])

    def test_deliberation_report_exposes_perception_needs_and_intents(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {"counts": {"error_count": 2, "uncertainty_high_count": 1}, "active_sessions": 0},
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "self_evolution": {"eligible_for_planning": True},
            },
        }
        report = engine.build_deliberation_report(drive_input=idle).to_dict()

        assert report["perception"]["correction_signals"] == 3
        assert report["world_model"]["truthfulness_pressure"] > 0
        assert report["reflection"]["learning_yield_state"] in {"cold", "mixed", "strong"}
        assert "adaptive_policy" in report
        assert report["adaptive_policy"]["preferred_focus"]
        assert any(need["need_type"] == "repair_truthfulness" for need in report["needs"])
        assert any(intent["candidate_kind"] == "truthfulness_review" for intent in report["intents"])
        assert any(signal["signal_type"] == "observation_signal" for signal in report["signals"])

    def test_deliberation_report_reflection_detects_blockage_and_alignment_need(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {"counts": {}, "active_sessions": 0},
            "api_b_judgement_tasks": [
                {
                    "title": "Deferred endogenous review A",
                    "status": "deferred",
                    "governance_task_type": "self_evolution",
                    "task_family": "general_self_evolution",
                    "execution_kind": "general_self_evolution",
                    "updated_at": "2026-06-20T00:00:00+00:00",
                    "metadata": {"endogenous_drive_key": "continuity:governance_hygiene_review"},
                },
                {
                    "title": "Paused endogenous review B",
                    "status": "awaiting_review",
                    "governance_task_type": "self_learning",
                    "task_family": "self_learning",
                    "execution_kind": None,
                    "updated_at": "2026-06-20T00:00:00+00:00",
                    "metadata": {"endogenous_drive_key": "truthfulness:review_correction_signals"},
                },
            ],
            "completed_learning_tasks": [
                {
                    "title": "Weak learning result",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.2,
                }
            ],
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "self_evolution": {"eligible_for_planning": True},
            },
        }
        report = engine.build_deliberation_report(drive_input=idle).to_dict()

        assert report["reflection"]["api_b_judgement_blockage_state"] in {"dragging", "blocked"}
        assert report["reflection"]["dominant_constraint"] in {
            "api_b_judgement_blockage",
            "weak_learning_yield",
        }
        assert any(need["need_type"] == "observe_before_acting" for need in report["needs"])
        signal_types = {signal["signal_type"] for signal in report["signals"]}
        assert "observation_signal" in signal_types
        assert "autonomy_alignment_signal" in signal_types

    def test_deliberation_report_reflection_uses_historical_outcomes(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {"counts": {}, "active_sessions": 0},
            "drive_history": {
                "outcomes": [
                    {"status": "failed", "task_family": "self_learning"},
                    {"status": "deferred", "task_family": "self_learning"},
                    {"status": "paused", "task_family": "general_self_evolution"},
                ]
            },
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "self_evolution": {"eligible_for_planning": True},
            },
        }
        report = engine.build_deliberation_report(drive_input=idle).to_dict()

        assert report["reflection"]["dominant_constraint"] == "historical_underdelivery"
        assert any(
            evidence.startswith("historical_outcomes=")
            for evidence in report["reflection"]["source_evidence"]
        )
        assert report["adaptive_policy"]["observation_bias"] > 0.5
        assert any(need["need_type"] == "observe_before_acting" for need in report["needs"])

    def test_adaptive_policy_suppresses_exploratory_learning_after_historical_underdelivery(self):
        engine = EndogenousDriveEngine()
        base_idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {
                "counts": {},
                "active_sessions": 0,
                "recent_metadata": {
                    "user_request": {
                        "text": "Investigate supervisor backlog scheduling fairness and duplicate learning output"
                    }
                },
            },
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": False},
                "general_self_evolution": {"eligible_for_planning": False},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": False},
                "self_evolution": {"eligible_for_planning": False},
            },
        }
        healthy_candidates = engine.generate_candidates(
            drive_input=base_idle,
            existing_drive_keys=set(),
            max_candidates=5,
        )
        healthy_learning = next(
            c for c in healthy_candidates if c.metadata.get("learning_branch") == "exploratory"
        )

        throttled_idle = {
            **base_idle,
            "drive_history": {
                "outcomes": [
                    {"status": "failed", "task_family": "self_learning"},
                    {"status": "deferred", "task_family": "self_learning"},
                    {"status": "paused", "task_family": "self_learning"},
                ]
            },
        }
        throttled_candidates = engine.generate_candidates(
            drive_input=throttled_idle,
            existing_drive_keys=set(),
            max_candidates=5,
        )
        throttled_report = engine.build_deliberation_report(
            drive_input=throttled_idle,
        ).to_dict()
        throttled_kinds = {
            c.metadata["score_breakdown"]["candidate_kind"]
            for c in throttled_candidates
        }

        assert healthy_learning.utility > 0
        assert "exploratory_learning" not in throttled_kinds
        assert throttled_report["adaptive_policy"]["candidate_budget"] == 1
        assert throttled_report["adaptive_policy"]["exploratory_learning_quota"] == 0

    def test_adaptive_policy_budget_reduces_total_candidates_under_high_throttle(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {
                "counts": {"error_count": 2, "uncertainty_high_count": 1},
                "active_sessions": 0,
                "recent_metadata": {
                    "user_request": {
                        "text": "Investigate backlog scheduling fairness and unresolved learning duplication"
                    }
                },
            },
            "drive_history": {
                "outcomes": [
                    {"status": "failed", "task_family": "self_learning"},
                    {"status": "deferred", "task_family": "self_learning"},
                    {"status": "paused", "task_family": "self_learning"},
                    {"status": "deferred", "task_family": "general_self_evolution"},
                ]
            },
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "self_evolution": {"eligible_for_planning": True},
            },
        }
        report = engine.build_deliberation_report(drive_input=idle).to_dict()
        candidates = engine.generate_candidates(
            drive_input=idle,
            existing_drive_keys=set(),
            max_candidates=5,
        )

        assert report["adaptive_policy"]["candidate_budget"] == 1
        assert len(candidates) == 1

    def test_strategy_memory_can_shift_preferred_focus_toward_truthfulness(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {
                "counts": {"error_count": 3, "uncertainty_high_count": 1},
                "active_sessions": 0,
            },
            "drive_history": {
                "strategy_memory": {
                    "focus_stats": {
                        "truthfulness": {
                            "judged": 6,
                            "completed": 5,
                            "failed": 0,
                            "dragging": 1,
                        },
                        "learning_expansion": {
                            "judged": 6,
                            "completed": 0,
                            "failed": 2,
                            "dragging": 4,
                        },
                    }
                }
            },
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": False},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "self_evolution": {"eligible_for_planning": False},
            },
        }

        report = engine.build_deliberation_report(drive_input=idle).to_dict()

        assert report["adaptive_policy"]["preferred_focus"] == "truthfulness"
        assert any(
            evidence.startswith("focus_effectiveness[truthfulness]=")
            for evidence in report["adaptive_policy"]["source_evidence"]
        )

    def test_contextual_strategy_memory_can_shift_preferred_focus_toward_observation(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {"counts": {}, "active_sessions": 0},
            "completed_learning_tasks": [
                {
                    "title": "Weak learning result",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.2,
                }
            ],
            "drive_history": {
                "strategy_memory": {
                    "focus_stats": {
                        "learning_expansion": {
                            "judged": 8,
                            "completed": 5,
                            "failed": 1,
                            "dragging": 2,
                        },
                        "observation": {
                            "judged": 8,
                            "completed": 1,
                            "failed": 1,
                            "dragging": 6,
                        },
                    },
                    "contextual_focus_stats": {
                        "user_chain_quiet|stable|weak_learning_yield": {
                            "observation": {
                                "judged": 6,
                                "completed": 5,
                                "failed": 0,
                                "dragging": 1,
                            },
                            "learning_expansion": {
                                "judged": 6,
                                "completed": 0,
                                "failed": 2,
                                "dragging": 4,
                            },
                        }
                    },
                }
            },
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "self_evolution": {"eligible_for_planning": True},
            },
        }

        report = engine.build_deliberation_report(drive_input=idle).to_dict()

        assert report["adaptive_policy"]["preferred_focus"] == "observation"
        assert any(
            evidence == "context_key=user_chain_quiet|stable|weak_learning_yield"
            for evidence in report["adaptive_policy"]["source_evidence"]
        )

    def test_deliberation_report_emits_drive_posture_signal(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {"counts": {}, "active_sessions": 0},
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "self_evolution": {"eligible_for_planning": True},
            },
        }
        report = engine.build_deliberation_report(drive_input=idle).to_dict()

        posture = next(signal for signal in report["signals"] if signal["signal_type"] == "drive_posture_signal")
        assert posture["payload"]["preferred_focus"] == report["adaptive_policy"]["preferred_focus"]
        assert posture["payload"]["candidate_budget"] == report["adaptive_policy"]["candidate_budget"]
        assert posture["payload"]["source_evidence"] == report["adaptive_policy"]["source_evidence"]

    def test_adaptive_policy_raises_observation_bias_when_observation_targets_stall(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {
                "counts": {"error_count": 1, "uncertainty_high_count": 1},
                "active_sessions": 0,
            },
            "completed_learning_tasks": [
                {
                    "title": "Weak learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.25,
                }
            ],
            "drive_history": {
                "judgements": [],
                "outcomes": [],
                "strategy_memory": {
                    "focus_stats": {},
                    "contextual_focus_stats": {},
                    "agenda_topic_stats": {},
                    "observation_target_stats": {
                        "learning_yield": {
                            "seen": 4,
                            "recommended": 4,
                            "resolved": 0,
                            "stalled": 2,
                            "last_priority": 0.82,
                            "last_risk": 0.76,
                            "last_status": "recommended",
                        }
                    },
                },
            },
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "self_evolution": {"eligible_for_planning": True},
            },
        }

        report = engine.build_deliberation_report(drive_input=idle).to_dict()
        policy = report["adaptive_policy"]

        assert policy["observation_bias"] >= 0.6
        assert policy["candidate_throttle"] >= 0.35
        assert any("unresolved_observation_pressure" in item for item in policy["source_evidence"])

    def test_adaptive_policy_recovers_learning_bias_when_observation_targets_resolve(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {
                "counts": {"error_count": 0, "uncertainty_high_count": 0},
                "active_sessions": 0,
            },
            "completed_learning_tasks": [
                {
                    "title": "Recovered learning",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.82,
                }
            ],
            "drive_history": {
                "judgements": [],
                "outcomes": [],
                "strategy_memory": {
                    "focus_stats": {},
                    "contextual_focus_stats": {},
                    "agenda_topic_stats": {
                        "expand_learning_frontier": {
                            "seen": 4,
                            "active_cycles": 2,
                            "resolved": 3,
                            "dragging": 0,
                            "last_priority": 0.7,
                            "last_confidence": 0.8,
                            "last_status": "resolved",
                        }
                    },
                    "observation_target_stats": {
                        "learning_yield": {
                            "seen": 4,
                            "recommended": 4,
                            "resolved": 3,
                            "stalled": 0,
                            "last_priority": 0.42,
                            "last_risk": 0.18,
                            "last_status": "resolved",
                        }
                    },
                },
            },
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "self_evolution": {"eligible_for_planning": True},
            },
        }

        report = engine.build_deliberation_report(drive_input=idle).to_dict()
        policy = report["adaptive_policy"]

        assert policy["learning_expansion_bias"] >= 0.5
        assert policy["candidate_throttle"] <= 0.45
        assert any("observation_recovery_signal" in item for item in policy["source_evidence"])

    def test_observation_posture_filters_out_exploratory_candidates(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {
                "counts": {"error_count": 2, "uncertainty_high_count": 1},
                "active_sessions": 0,
                "recent_metadata": {
                    "user_request": {
                        "text": "Investigate backlog scheduling fairness and duplicate learning output"
                    }
                },
            },
            "api_b_judgement_tasks": [
                {
                    "title": "Deferred endogenous review A",
                    "status": "deferred",
                    "governance_task_type": "self_evolution",
                    "task_family": "general_self_evolution",
                    "execution_kind": "general_self_evolution",
                    "updated_at": "2026-06-20T00:00:00+00:00",
                    "metadata": {"endogenous_drive_key": "continuity:governance_hygiene_review"},
                },
                {
                    "title": "Paused endogenous review B",
                    "status": "awaiting_review",
                    "governance_task_type": "self_learning",
                    "task_family": "self_learning",
                    "execution_kind": None,
                    "updated_at": "2026-06-20T00:00:00+00:00",
                    "metadata": {"endogenous_drive_key": "truthfulness:review_correction_signals"},
                },
            ],
            "completed_learning_tasks": [
                {
                    "title": "Weak learning result",
                    "completed_at": "2026-06-27T00:00:00+00:00",
                    "quality_score": 0.2,
                }
            ],
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "self_evolution": {"eligible_for_planning": True},
            },
        }

        report = engine.build_deliberation_report(drive_input=idle).to_dict()
        candidates = engine.generate_candidates(
            drive_input=idle,
            existing_drive_keys=set(),
            max_candidates=5,
        )

        assert report["adaptive_policy"]["preferred_focus"] in {
            "observation",
            "governance_hygiene",
            "truthfulness",
        }
        assert report["adaptive_policy"]["observation_bias"] >= 0.58
        assert candidates
        candidate_kinds = {
            c.metadata["score_breakdown"]["candidate_kind"]
            for c in candidates
        }
        assert "exploratory_learning" not in candidate_kinds

    def test_deliberation_report_emits_non_task_signals(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {"counts": {"error_count": 4, "uncertainty_high_count": 1}, "active_sessions": 0},
            "api_b_judgement_tasks": [
                {
                    "title": "Revisit weak backlog evidence",
                    "status": "deferred",
                    "governance_task_type": "self_evolution",
                    "task_family": "general_self_evolution",
                    "execution_kind": "general_self_evolution",
                    "updated_at": "2099-01-01T00:00:00+00:00",
                }
            ],
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "self_evolution": {"eligible_for_planning": True},
            },
        }
        report = engine.build_deliberation_report(drive_input=idle).to_dict()

        signal_types = {signal["signal_type"] for signal in report["signals"]}
        assert "observation_signal" in signal_types
        assert "governance_review_suggestion" in signal_types

    def test_truthfulness_pressure_can_support_alert_channel_inputs(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {"counts": {"error_count": 4, "uncertainty_high_count": 1}, "active_sessions": 0},
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "self_evolution": {"eligible_for_planning": True},
            },
        }
        report = engine.build_deliberation_report(drive_input=idle).to_dict()

        assert report["adaptive_policy"]["preferred_focus"] == "truthfulness"
        assert any(
            need["need_type"] == "repair_truthfulness"
            for need in report["needs"]
        )

    def test_no_candidates_when_fully_blocked(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 100, "api_a_execution": 100, "memory": 100},
            "activity": {"counts": {"error_count": 0, "uncertainty_high_count": 0}, "active_sessions": 5},
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": False},
                "memory_maintenance": {"eligible_for_planning": False},
                "general_self_evolution": {"eligible_for_planning": False},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": False},
                "memory_maintenance": {"eligible_for_planning": False},
                "self_evolution": {"eligible_for_planning": False},
            },
        }
        candidates = engine.generate_candidates(drive_input=idle, existing_drive_keys=set(), max_candidates=5)
        assert len(candidates) == 0

    def test_learning_candidates_use_active_sessions_as_soft_signal_when_planning_allowed(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {
                "counts": {"error_count": 4, "uncertainty_high_count": 1},
                "active_sessions": 3,
                "recent_metadata": {
                    "user_request": {"topic": "API-A autonomous execution visibility"}
                },
            },
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": False},
                "general_self_evolution": {"eligible_for_planning": False},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": False},
                "self_evolution": {"eligible_for_planning": False},
            },
        }
        candidates = engine.generate_candidates(drive_input=idle, existing_drive_keys=set(), max_candidates=5)
        assert any(candidate.governance_task_type == "self_learning" for candidate in candidates)

    def test_learning_topics_respect_recent_completion_cooldown(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {
                "counts": {},
                "active_sessions": 0,
                "recent_metadata": {
                    "user_request": {
                        "text": "研究自主链路门控下的治理在途去重策略"
                    }
                },
            },
            "completed_learning_tasks": [
                {
                    "title": "研究自主链路门控下的治理在途去重策略",
                    "completed_at": "2099-01-01T00:00:00+00:00",
                    "quality_score": 0.9,
                }
            ],
            "api_b_judgement_tasks": [],
            "endogenous_drive_policy": {
                "learning_topic_cooldown_hours": 72,
                "topic_overlap_threshold": 0.5,
            },
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": False},
                "general_self_evolution": {"eligible_for_planning": False},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": False},
                "self_evolution": {"eligible_for_planning": False},
            },
        }
        candidates = engine.generate_candidates(
            drive_input=idle,
            existing_drive_keys=set(),
            max_candidates=5,
        )
        exploratory = [c for c in candidates if c.governance_task_type == "self_learning"]
        assert exploratory == [], "recently completed topic should be cooled down instead of re-generated"

    def test_body_improvement_candidate_respects_recent_backlog_cooldown(self):
        engine = EndogenousDriveEngine()
        idle = {
            "checks": {},
            "idle_seconds": {"user": 1000, "api_a_execution": 1000, "memory": 1000},
            "activity": {"counts": {}, "active_sessions": 0, "recent_metadata": {}},
            "shell_slot": {
                "slot_id": "slot-B",
                "worktree_path": "F:/tmp/slot-B/worktree",
            },
            "completed_learning_tasks": [
                {
                    "title": "研究 Agent 展示链路",
                    "completed_at": "2099-01-01T00:00:00+00:00",
                    "quality_score": 1.0,
                }
            ],
            "api_b_judgement_tasks": [
                {
                    "title": "改进 shell 替身：收紧治理在途复核",
                    "status": "approved",
                    "execution_kind": "body_improvement",
                    "constraints": {"target_slot_id": "slot-B"},
                    "updated_at": "2099-01-01T01:00:00+00:00",
                }
            ],
            "endogenous_drive_policy": {
                "body_improvement_min_quality": 60.0,
                "body_improvement_cooldown_hours": 24,
                "body_improvement_editable_dirs": ["agent/"],
                "body_improvement_forbidden_patterns": ["systems/**"],
                "body_improvement_max_files": 5,
            },
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": False},
                "memory_maintenance": {"eligible_for_planning": False},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": False},
                "memory_maintenance": {"eligible_for_planning": False},
                "self_evolution": {"eligible_for_planning": True},
            },
        }
        candidates = engine.generate_candidates(
            drive_input=idle,
            existing_drive_keys=set(),
            max_candidates=5,
        )
        assert not any(c.execution_kind == "body_improvement" for c in candidates)

    @staticmethod
    def _body_improvement_drive_input(*, conclusion: str) -> dict:
        return {
            "checks": {},
            "idle_seconds": {
                "user": 1000,
                "api_a_execution": 1000,
                "memory": 1000,
            },
            "activity": {"counts": {}, "active_sessions": 0, "recent_metadata": {}},
            "shell_slot": {
                "slot_id": "slot-B",
                "worktree_path": "F:/tmp/slot-B/worktree",
            },
            "completed_learning_tasks": [
                {
                    "task_id": "learning-stream-1",
                    "title": "研究 Agent 流式展示链路",
                    "summary": "定位流式输出中的稳定性问题。",
                    "conclusion": conclusion,
                    "completed_at": "2099-01-01T00:00:00+00:00",
                    "quality_score": 1.0,
                    "evidence_summary": ["stream rendering has a concrete fix"],
                }
            ],
            "api_b_judgement_tasks": [],
            "endogenous_drive_policy": {
                "body_improvement_min_quality": 60.0,
                "body_improvement_cooldown_hours": 24,
                "body_improvement_editable_dirs": ["agent/", "tools/", "systems/"],
                "body_improvement_forbidden_patterns": [
                    "systems/**",
                    "**/credential*",
                ],
                "body_improvement_max_files": 3,
            },
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": False},
                "memory_maintenance": {"eligible_for_planning": False},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": False},
                "memory_maintenance": {"eligible_for_planning": False},
                "self_evolution": {"eligible_for_planning": True},
            },
        }

    def test_learning_evidence_maps_only_to_canonical_body_nodes(self):
        engine = EndogenousDriveEngine()
        drive_input = self._body_improvement_drive_input(
            conclusion=(
                "Update agent/stream_handler.py after validating the stream display fix. "
                "Do not modify systems/supervisor/planning_runtime.py or "
                "agent/credential_pool.py."
            )
        )
        projection = engine._build_body_improvement_projection(
            drive_context=engine._build_drive_context(drive_input),
            shell_slot_meta=drive_input["shell_slot"],
        )

        assert projection["available"] is True
        assert "agent/stream_handler.py" in projection["target_paths"]
        assert all(not path.startswith("systems/") for path in projection["target_paths"])
        assert "agent/credential_pool.py" not in projection["target_paths"]
        assert projection["editable_dirs"] == ["agent/", "tools/"]
        assert projection["learning_refs"][0]["mem_id"] == "learning-stream-1"

    def test_completed_learning_generates_governed_body_improvement_without_lm(self):
        engine = EndogenousDriveEngine()
        drive_input = self._body_improvement_drive_input(
            conclusion="Improve agent/stream_handler.py based on the verified stream result."
        )

        candidates = engine.generate_candidates(
            drive_input=drive_input,
            existing_drive_keys=set(),
            max_candidates=8,
        )
        candidate = next(
            item for item in candidates if item.execution_kind == "body_improvement"
        )

        assert candidate.metadata["improvement_direction_source"] == (
            "learning_evidence_structure_projection_v1"
        )
        assert candidate.constraints["target_slot_id"] == "slot-B"
        assert candidate.constraints["worktree_path"] == "F:/tmp/slot-B/worktree"
        assert "agent/stream_handler.py" in candidate.constraints["target_paths"]
        assert candidate.evidence["learning_refs"][0]["mem_id"] == "learning-stream-1"
        assert candidate.evidence["learning_quality_score"] >= 60.0

    def test_unmapped_learning_evidence_does_not_generate_body_improvement(self):
        engine = EndogenousDriveEngine()
        drive_input = self._body_improvement_drive_input(
            conclusion=(
                "The runtime profile observation is theoretical and has no concrete subsystem target."
            )
        )
        drive_input["completed_learning_tasks"][0]["title"] = "抽象研究结论"
        drive_input["completed_learning_tasks"][0]["summary"] = "仅记录理论观察。"
        drive_input["completed_learning_tasks"][0]["evidence_summary"] = []

        candidates = engine.generate_candidates(
            drive_input=drive_input,
            existing_drive_keys=set(),
            max_candidates=8,
        )

        assert not any(
            item.execution_kind == "body_improvement" for item in candidates
        )

    def test_lm_body_improvement_is_bound_to_program_structure_mapping(self):
        engine = EndogenousDriveEngine()
        drive_input = self._body_improvement_drive_input(
            conclusion="Improve agent/stream_handler.py using the verified stream finding."
        )
        drive_context = engine._build_drive_context(drive_input)
        deliberation = engine.build_deliberation_report(drive_input=drive_input)

        candidates = engine._materialize_lm_task_proposals(
            proposals=[
                {
                    "title": "Improve stream reliability",
                    "summary": "Apply the grounded learning result to the shell stream path.",
                    "candidate_kind": "body_improvement",
                    "task_type": "improvement",
                    "confidence": 0.9,
                    "risk_level": "high",
                    "evidence_level": "strong",
                    "execution_mode": "guarded_execution",
                }
            ],
            existing_keys=set(),
            deliberation=deliberation,
            drive_context=drive_context,
            evidence_packet={
                "plans": {"self_evolution": {"eligible_for_planning": True}},
                "shell_slot": drive_input["shell_slot"],
                "evidence_graph": {},
                "agenda_graph": {},
            },
        )

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.metadata["llm_task_generated"] is True
        assert candidate.constraints["worktree_path"] == "F:/tmp/slot-B/worktree"
        assert "agent/stream_handler.py" in candidate.constraints["target_paths"]
        assert candidate.evidence["learning_refs"][0]["mem_id"] == "learning-stream-1"
        assert candidate.evidence["structure_mapping"]["source"] == (
            "learning_evidence_structure_projection_v1"
        )


# ── T-09: Self-learning conclusion compatibility layer ─────────────

class TestSelfLearningConclusionStoreLifecycle:
    """Verify the learning conclusion store compatibility layer (T-09)."""

    @pytest.mark.xfail(reason="LearningSession Pydantic validation — pre-existing schema issue")
    def test_create_topic_and_build_submission(self, tmp_path):
        from systems.self_learning.conclusion_store import SelfLearningConclusionStore

        svc = SelfLearningConclusionStore(storage_root=str(tmp_path))
        topic = svc.create_topic(title="测试主题", reason="验证生命周期")
        assert topic.topic_id
        assert topic.title == "测试主题"

        session = svc.plan_session(topic=topic, trigger="test")
        assert session.session_id

        experiment = svc.record_experiment(
            topic=topic, session=session,
            hypothesis="X causes Y", method="Test X",
            observations=["obs1"], outcome="confirmed",
        )
        assert experiment.experiment_id
        assert experiment.outcome == "confirmed"

        conclusion = svc.submit_conclusion(
            topic=topic, session=session,
            experiments=[experiment], comparisons=["baseline"],
            summary="学习已完成收束", verified=True,
        )
        assert conclusion.conclusion_id

        submission = svc.build_supervisor_submission(conclusion)
        assert submission.conclusion_id == conclusion.conclusion_id

