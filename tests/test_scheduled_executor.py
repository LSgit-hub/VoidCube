from __future__ import annotations

import json
import shutil
from types import SimpleNamespace
from unittest.mock import Mock
import subprocess

import pytest

from voidcube.application.scheduling.scheduled_executor import (
    ScheduledTaskExecutorPorts,
    ScheduledTaskExecutorRuntime,
)


def test_poll_recovers_gateway_before_abandoning_employee_claim():
    post_supervisor = Mock(
        side_effect=[OSError("gateway unavailable"), {"status": "idle", "claim": None}]
    )
    recover_executor = Mock(return_value=True)
    outbox = SimpleNamespace(
        pending_count=Mock(return_value=0),
        next_due=Mock(return_value=None),
    )
    runtime = ScheduledTaskExecutorRuntime(
        ScheduledTaskExecutorPorts(
            autonomous_mode_active=lambda: True,
            autonomous_mode_lock=None,
            execution_gate=None,
            get_session_id=lambda: "cli-session",
            set_execution_active=Mock(),
            set_companion_active=Mock(),
            start_background_task=Mock(return_value=False),
            post_supervisor=post_supervisor,
            rate_limit_metadata=lambda _error: {},
            writeback_outbox=outbox,
            recover_executor=recover_executor,
        ),
        poll_interval_seconds=0.5,
    )

    runtime.poll_workflow()

    assert post_supervisor.call_count == 2
    recover_executor.assert_called_once_with()


def test_body_improvement_binding_uses_registry_worktree_and_expected_head(
    monkeypatch, tmp_path
):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "agent.py").write_text("VERSION = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.name", "VoidCube Tests"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "tests@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "add", "agent.py"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repository, check=True)
    worktree = tmp_path / "slots" / "slot-B" / "worktree"
    worktree.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "worktree", "add", "--detach", "-q", str(worktree), "HEAD"],
        cwd=repository,
        check=True,
    )
    (worktree.parent / "worktree-origin.json").write_text(
        __import__("json").dumps(
            {
                "slot_id": "slot-B",
                "worktree_path": str(worktree.resolve()),
                "materialization_mode": "git_worktree",
            }
        ),
        encoding="utf-8",
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    from voidcube.infrastructure.execution import terminal_tool

    captured_environment = {
        "backend": "docker",
        "validation_scope": "container",
        "repository_head": head,
        "execution_environment_id": "execution-environment-test",
    }
    monkeypatch.setattr(
        terminal_tool,
        "prepare_task_git_worktree",
        lambda task_id, worktree_path, *, expected_head: captured_environment,
        raising=False,
    )
    outbox = SimpleNamespace(
        pending_count=Mock(return_value=0),
        next_due=Mock(return_value=None),
    )
    runtime = ScheduledTaskExecutorRuntime(
        ScheduledTaskExecutorPorts(
            autonomous_mode_active=lambda: True,
            autonomous_mode_lock=None,
            execution_gate=None,
            get_session_id=lambda: "session",
            set_execution_active=Mock(),
            set_companion_active=Mock(),
            start_background_task=Mock(return_value=False),
            post_supervisor=Mock(),
            rate_limit_metadata=lambda _error: {},
            writeback_outbox=outbox,
            inspect_body_execution_readiness=(
                __import__(
                    "voidcube.systems.supervisor.body_execution_readiness",
                    fromlist=["inspect_body_execution_readiness"],
                ).inspect_body_execution_readiness
            ),
            prepare_task_git_worktree=terminal_tool.prepare_task_git_worktree,
            release_task_environment=terminal_tool.release_task_environment,
            get_supervisor=lambda path: (
                {
                    "slot_id": "slot-B",
                    "body_state": "shell",
                    "worktree_path": str(worktree),
                    "candidate_commit": head,
                }
                if path.endswith("/slot-B")
                else {"registry": {"active_slot": "slot-A", "shell_slot": "slot-B"}}
            ),
        )
    )

    bound_path, verify, environment = runtime._bind_body_improvement_worktree(
        autonomous_task={
            "execution_kind": "body_improvement",
            "constraints": {
                "target_slot_id": "slot-B",
                "worktree_path": str(worktree),
                "evaluated_candidate_commit": head,
            },
        },
        task_id="scheduled-body",
    )

    assert bound_path == str(worktree.resolve())
    assert environment == captured_environment
    assert verify() == (True, "")
    (worktree / "agent.py").write_text("VERSION = 2\n", encoding="utf-8")
    assert verify() == (False, "body worktree is dirty after employee execution")

    from voidcube.infrastructure.execution.terminal_tool import clear_task_env_overrides

    clear_task_env_overrides("scheduled-body")


def test_body_improvement_report_gets_infrastructure_environment_manifest():
    response = '{"body_improvement_report": {"task_id": "task-1"}}'
    manifest = {
        "backend": "docker",
        "validation_scope": "container",
        "repository_head": "a" * 40,
        "execution_environment_id": "execution-environment-test",
    }

    enriched = ScheduledTaskExecutorRuntime._attach_body_execution_environment(
        response, manifest
    )

    import json

    payload = json.loads(enriched)
    assert payload["body_improvement_report"]["execution_environment"] == manifest


@pytest.mark.integration
def test_body_employee_scheduler_uses_real_podman_manifest(tmp_path, monkeypatch):
    """Exercise the scheduler binding and writeback with a real Podman probe."""
    podman = shutil.which("podman")
    image = "localhost/voidcube-project-podman:py314-v1"
    if not podman:
        pytest.skip("podman executable not found")
    image_check = subprocess.run(
        [podman, "image", "exists", image],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if image_check.returncode != 0:
        pytest.skip(f"required Podman image missing: {image}")

    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "agent.py").write_text("VERSION = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.name", "VoidCube Integration"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "integration@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "add", "agent.py"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "baseline"], cwd=repository, check=True
    )
    worktree = tmp_path / "slots" / "slot-B" / "worktree"
    worktree.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "worktree", "add", "--detach", "-q", str(worktree), "HEAD"],
        cwd=repository,
        check=True,
    )
    (worktree.parent / "worktree-origin.json").write_text(
        json.dumps(
            {
                "slot_id": "slot-B",
                "worktree_path": str(worktree.resolve()),
                "materialization_mode": "git_worktree",
            }
        ),
        encoding="utf-8",
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    class Outbox:
        def __init__(self):
            self.items = []
            self.delivered = []

        def enqueue(self, run_id, payload):
            self.items.append({
                "_outbox_run_id": run_id,
                "_outbox_attempts": 0,
                **payload,
            })

        def next_due(self):
            return self.items.pop(0) if self.items else None

        def mark_delivered(self, run_id):
            self.delivered.append(run_id)

        def mark_failed(self, **_kwargs):
            raise AssertionError("Podman integration writeback unexpectedly failed")

        def mark_dead(self, **_kwargs):
            raise AssertionError("Podman integration writeback was rejected")

        def pending_count(self):
            return len(self.items)

    outbox = Outbox()
    finish_payload = {}

    def post_supervisor(path, payload):
        if path == "/scheduled-tasks/claim":
            return {
                "claim": {
                    "task": {
                        "title": "Verify evaluated body",
                        "instruction": "Run the verification and return JSON evidence.",
                        "created_by": "api_b",
                        "requested_via": "autonomous_worker",
                        "worker_role": "coding",
                    },
                    "run": {"run_id": "podman-run-1"},
                    "autonomous_task": {
                        "task_id": "body-task-1",
                        "execution_kind": "body_improvement",
                        "constraints": {
                            "target_slot_id": "slot-B",
                            "worktree_path": str(worktree),
                            "evaluated_candidate_commit": head,
                        },
                    },
                }
            }
        if path.endswith("/finish"):
            finish_payload.update(payload)
            return {"status": "ok"}
        raise AssertionError(f"unexpected Supervisor call: {path}")

    task_manifest = {
        "task_id": "body-task-1",
        "lease_generation": 1,
        "attempt_id": "attempt-1",
        "slot_id": "slot-B",
        "baseline_commit": head,
        "commit_hash": head,
        "changed_files": ["agent.py"],
        "verification": {"passed": True},
    }

    def start_background_task(_prompt, *, on_complete, **_kwargs):
        on_complete(True, json.dumps({"body_improvement_report": task_manifest}), "")
        return True

    monkeypatch.setenv("TERMINAL_ENV", "podman")
    monkeypatch.setenv("TERMINAL_PODMAN_IMAGE", image)
    monkeypatch.setenv("TERMINAL_FALLBACK_TO_LOCAL", "false")
    from voidcube.infrastructure.execution import terminal_tool
    from voidcube.systems.supervisor.body_execution_readiness import (
        inspect_body_execution_readiness,
    )

    runtime = ScheduledTaskExecutorRuntime(
        ScheduledTaskExecutorPorts(
            autonomous_mode_active=lambda: True,
            autonomous_mode_lock=None,
            execution_gate=None,
            get_session_id=lambda: "integration-session",
            set_execution_active=Mock(),
            set_companion_active=Mock(),
            start_background_task=start_background_task,
            post_supervisor=post_supervisor,
            rate_limit_metadata=lambda _error: {},
            writeback_outbox=outbox,
            inspect_body_execution_readiness=inspect_body_execution_readiness,
            prepare_task_git_worktree=terminal_tool.prepare_task_git_worktree,
            release_task_environment=terminal_tool.release_task_environment,
            get_supervisor=lambda path: (
                {
                    "slot_id": "slot-B",
                    "body_state": "shell",
                    "worktree_path": str(worktree),
                    "candidate_commit": head,
                }
                if path.endswith("/slot-B")
                else {"registry": {"active_slot": "slot-A", "shell_slot": "slot-B"}}
            ),
        ),
        poll_interval_seconds=0.5,
    )

    try:
        runtime.poll_workflow()
    finally:
        from voidcube.infrastructure.execution.terminal_tool import (
            release_task_environment,
        )

        release_task_environment("scheduled_podman-run-1")

    assert finish_payload["success"] is True
    report = json.loads(finish_payload["result_summary"])["body_improvement_report"]
    environment = report["execution_environment"]
    assert environment["validation_scope"] == "container"
    assert environment["backend"] == "podman"
    assert environment["repository_head"] == head
    assert environment["image_digest"].startswith("sha256:")
    assert outbox.delivered == ["podman-run-1"]
