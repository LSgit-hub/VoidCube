from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.body_registry import BodyRegistryManager
from systems.lifecycle import BodyLifecycleController
from systems.probe import ProbeCheckResult, ProbeExecutor, ProbeRunner


@pytest.mark.unit
def test_probe_runner_marks_complete_successful_report_as_passed():
    runner = ProbeRunner()
    report = runner.build_report(
        "slot-B",
        [
            ProbeCheckResult(name="startup_ok", passed=True),
            ProbeCheckResult(name="config_load_ok", passed=True),
            ProbeCheckResult(name="memory_path_ok", passed=True),
            ProbeCheckResult(name="tool_smoke_ok", passed=True),
            ProbeCheckResult(name="task_replay_ok", passed=True),
        ],
    )

    assert report.overall_passed is True
    assert report.overall_status == "passed"
    assert report.failed_count == 0
    assert report.missing_required_checks == []


@pytest.mark.unit
def test_probe_runner_fails_when_required_checks_are_missing():
    runner = ProbeRunner()
    report = runner.build_report(
        "slot-B",
        [
            ProbeCheckResult(name="startup_ok", passed=True),
            ProbeCheckResult(name="config_load_ok", passed=True),
        ],
    )

    assert report.overall_passed is False
    assert report.overall_status == "failed"
    assert "memory_path_ok" in report.missing_required_checks
    assert "tool_smoke_ok" in report.missing_required_checks
    assert "task_replay_ok" in report.missing_required_checks


@pytest.mark.unit
def test_probe_runner_fails_when_required_check_fails():
    runner = ProbeRunner()
    report = runner.build_report(
        "slot-B",
        [
            ProbeCheckResult(name="startup_ok", passed=True),
            ProbeCheckResult(name="config_load_ok", passed=True),
            ProbeCheckResult(name="memory_path_ok", passed=False),
            ProbeCheckResult(name="tool_smoke_ok", passed=True),
            ProbeCheckResult(name="task_replay_ok", passed=True),
        ],
    )

    assert report.overall_passed is False
    assert report.failed_count >= 1
    assert "memory_path_ok" in report.summary


@pytest.mark.unit
def test_lifecycle_can_persist_probe_report_to_slot_meta(tmp_path):
    manager = BodyRegistryManager(tmp_path)
    manager.initialize_layout()
    manager.mark_candidate("slot-B")
    manager.start_probe("slot-B")

    runner = ProbeRunner()
    report = runner.build_report(
        "slot-B",
        [
            ProbeCheckResult(name="startup_ok", passed=True),
            ProbeCheckResult(name="config_load_ok", passed=True),
            ProbeCheckResult(name="memory_path_ok", passed=True),
            ProbeCheckResult(name="tool_smoke_ok", passed=True),
            ProbeCheckResult(name="task_replay_ok", passed=True),
        ],
    )

    controller = BodyLifecycleController(manager)
    result = controller.record_probe_report("slot-B", report)
    stored = manager.load_slot_meta("slot-B")

    assert result.status == "applied"
    assert stored.last_probe_result is not None
    assert stored.last_probe_result["overall_passed"] is True


@pytest.mark.unit
def test_probe_report_persistence_inherits_slot_git_lineage(tmp_path):
    manager = BodyRegistryManager(tmp_path)
    manager.initialize_layout()
    manager.mark_candidate(
        "slot-B",
        source_commit="aaa111",
        candidate_commit="bbb222",
        rollback_commit="aaa111",
        diff_summary="Improve probe lineage coverage.",
        changed_files=["systems/probe.py"],
    )
    manager.start_probe("slot-B")

    runner = ProbeRunner()
    report = runner.build_report(
        "slot-B",
        [
            ProbeCheckResult(name="startup_ok", passed=True),
            ProbeCheckResult(name="config_load_ok", passed=True),
            ProbeCheckResult(name="memory_path_ok", passed=True),
            ProbeCheckResult(name="tool_smoke_ok", passed=True),
            ProbeCheckResult(name="task_replay_ok", passed=True),
        ],
    )

    controller = BodyLifecycleController(manager)
    result = controller.record_probe_report("slot-B", report)
    stored = manager.load_slot_meta("slot-B")

    assert result.status == "applied"
    assert stored.last_probe_result["source_commit"] == "aaa111"
    assert stored.last_probe_result["candidate_commit"] == "bbb222"
    assert stored.last_probe_result["rollback_commit"] == "aaa111"
    assert stored.last_probe_result["diff_summary"] == "Improve probe lineage coverage."
    assert stored.last_probe_result["changed_files"] == ["systems/probe.py"]


@pytest.mark.unit
def test_lifecycle_rejects_probe_report_for_wrong_slot(tmp_path):
    manager = BodyRegistryManager(tmp_path)
    manager.initialize_layout()
    manager.mark_candidate("slot-B")
    manager.start_probe("slot-B")

    runner = ProbeRunner()
    report = runner.build_report(
        "slot-A",
        [
            ProbeCheckResult(name="startup_ok", passed=True),
            ProbeCheckResult(name="config_load_ok", passed=True),
            ProbeCheckResult(name="memory_path_ok", passed=True),
            ProbeCheckResult(name="tool_smoke_ok", passed=True),
            ProbeCheckResult(name="task_replay_ok", passed=True),
        ],
    )

    controller = BodyLifecycleController(manager)
    result = controller.record_probe_report("slot-B", report)

    assert result.status == "failed"
    assert "does not match" in result.details["reason"]


@pytest.mark.unit
def test_probe_executor_runs_real_minimal_checks(tmp_path):
    (tmp_path / "run_agent.py").write_text("print('agent entrypoint')\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("model: test\n", encoding="utf-8")
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "model_tools.py").write_text("# probe smoke\n", encoding="utf-8")
    soul_dir = tmp_path / ".soul-runtime"
    soul_dir.mkdir()

    manager = BodyRegistryManager(tmp_path)
    manager.initialize_layout()
    manager.prepare_slot_workspace("slot-B")
    manager.mark_candidate("slot-B")
    slot = manager.start_probe("slot-B")

    executor = ProbeExecutor()
    context = executor.build_context(
        slot_id="slot-B",
        repo_root=tmp_path,
        worktree_path=slot.worktree_path,
        runtime_path=slot.runtime_path,
        logs_path=slot.logs_path,
        soul_store_path=soul_dir,
    )
    report = executor.run(context)

    assert report.overall_passed is True
    assert report.overall_status == "passed"
    assert report.failed_count == 0
