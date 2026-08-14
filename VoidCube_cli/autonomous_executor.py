from __future__ import annotations

import json
import time
import uuid
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from VoidCube_cli.ops.executor import default_gateway_url
from systems.evolution_evaluation.models import ExecutionEnvironmentManifest


AUTONOMOUS_LEARNING_TASK_PREFIX = "[Autonomous Learning Task]"
AUTONOMOUS_BODY_IMPROVEMENT_TASK_PREFIX = "[Autonomous Body Improvement Task]"


@dataclass(frozen=True, slots=True)
class AutonomousExecutorPorts:
    """State and side effects supplied by the hosting CLI runtime."""

    get_session_id: Callable[[], str]
    get_current_task: Callable[[], Dict[str, Any] | None]
    set_current_task: Callable[[Dict[str, Any] | None], None]
    get_current_task_started_at: Callable[[], float]
    set_current_task_started_at: Callable[[float], None]
    set_current_task_run_id: Callable[[str], None]
    get_last_agent_turn_result: Callable[[], Dict[str, Any] | None]
    set_last_agent_turn_result: Callable[[Dict[str, Any] | None], None]
    enqueue_pending_input: Callable[[str], None]
    agent_running: Callable[[], bool]
    autonomous_gate_active: Callable[[], bool]
    append_execution_event: Callable[..., None]
    prepare_body_worktree: Callable[[str, str, str], Dict[str, Any]]
    release_task_environment: Callable[[str], None]


def autonomous_task_execution_kind(task: Dict[str, Any]) -> str:
    return str(task.get("execution_kind") or task.get("task_type") or "").strip().lower()


def autonomous_task_label(execution_kind: str) -> str:
    return "改进链路项" if execution_kind == "body_improvement" else "学习链路项"


def autonomous_task_toolsets(task: Dict[str, Any] | None) -> list[str] | None:
    """Return the toolsets allowed for one autonomous task, if restricted."""
    if not isinstance(task, dict):
        return None
    if autonomous_task_execution_kind(task) == "self_learning":
        return ["learn"]
    return None


def autonomous_learning_branch(task: Dict[str, Any] | None) -> str:
    if not isinstance(task, dict):
        return ""
    metadata = dict(task.get("metadata") or {})
    evidence = dict(task.get("evidence") or {})
    return str(
        metadata.get("learning_branch") or evidence.get("learning_branch") or ""
    ).strip().lower()


def autonomous_learning_evidence_error(
    task: Dict[str, Any] | None,
    turn_result: Dict[str, Any] | None,
) -> str:
    """Return a deterministic completion failure for unsupported research evidence."""
    if autonomous_learning_branch(task) != "exploratory":
        return ""
    result = turn_result if isinstance(turn_result, dict) else {}
    tools_used = {
        str(name).strip()
        for name in list(result.get("tools_used") or [])
        if str(name).strip()
    }
    if "web_search" not in tools_used:
        return "exploratory learning requires a recorded web_search call"
    if not list(result.get("source_urls") or []):
        return "exploratory learning requires at least one URL from web research"
    return ""


def build_autonomous_task_prompt(
    task: Dict[str, Any],
    execution_kind: str,
    *,
    git_head_commit: Callable[[str], str] | None = None,
) -> str:
    title = task.get("title", "Agent task")
    summary = task.get("summary", "")
    if execution_kind == "body_improvement":
        constraints = dict(task.get("constraints") or {})
        worktree_path = str(
            constraints.get("worktree_path")
            or constraints.get("target_worktree")
            or ""
        ).strip()
        evaluated_baseline_commit = str(
            constraints.get("evaluated_baseline_commit") or ""
        ).strip().lower()
        evaluated_candidate_commit = str(
            constraints.get("evaluated_candidate_commit") or ""
        ).strip().lower()
        experiment_result_id = str(
            constraints.get("experiment_result_id") or ""
        ).strip()
        authoring_result_id = str(
            constraints.get("authoring_result_id") or ""
        ).strip()
        candidate_ref = str(constraints.get("candidate_ref") or "").strip()
        authorized_changed_files = tuple(
            str(item).strip()
            for item in constraints.get("changed_files") or []
            if str(item).strip()
        )
        authoring_environment_manifest_id = str(
            constraints.get("authoring_environment_manifest_id") or ""
        ).strip()
        authoring_environment_identity_id = str(
            constraints.get("authoring_environment_identity_id") or ""
        ).strip()
        evaluated_environment_id = str(
            constraints.get("execution_environment_id") or ""
        ).strip()
        evaluated_platforms = tuple(
            str(item).strip()
            for item in constraints.get("validated_platforms") or []
            if str(item).strip()
        )
        if not (
            constraints.get("must_not_create_new_commit") is True
            and constraints.get("must_match_evaluated_commit") is True
            and constraints.get("requires_governor_review") is True
            and constraints.get("requires_user_consent") is True
            and evaluated_baseline_commit
            and evaluated_candidate_commit
            and experiment_result_id
            and authoring_result_id
            and candidate_ref
            and authorized_changed_files
            and authoring_environment_manifest_id
            and authoring_environment_identity_id
            and evaluated_environment_id
            and evaluated_platforms
        ):
            raise ValueError(
                "body improvement task is missing immutable evaluation authorization"
            )
        task["_baseline_head"] = evaluated_baseline_commit
        task["_expected_candidate_head"] = evaluated_candidate_commit
        task["_initial_head"] = git_head_commit(worktree_path) if git_head_commit else ""
        task["_improvement_worktree"] = worktree_path
        task["_improvement_slot_id"] = str(
            constraints.get("target_slot_id")
            or constraints.get("target_slot")
            or ""
        ).strip()
        editable_dirs = constraints.get("editable_dirs") or []
        target_paths = constraints.get("target_paths") or []
        forbidden_patterns = constraints.get("forbidden_patterns") or []
        max_files = constraints.get("max_files_changed")
        evidence = dict(task.get("evidence") or {})
        learning_refs = [
            dict(ref)
            for ref in list(evidence.get("learning_refs") or [])
            if isinstance(ref, dict)
        ]
        prompt_parts = [f"{AUTONOMOUS_BODY_IMPROVEMENT_TASK_PREFIX} {title}"]
        if summary:
            prompt_parts.append(summary)
        prompt_parts.append(
            "Adopt and inspect the already evaluated candidate commit. Do not edit files or create a new commit."
        )
        if worktree_path:
            prompt_parts.append("Worktree path inside the sandbox: /workspace")
        if editable_dirs:
            prompt_parts.append(f"Editable dirs: {', '.join(str(x) for x in editable_dirs)}")
        if target_paths:
            prompt_parts.append(
                "Approved target paths: "
                + ", ".join(str(path) for path in target_paths)
            )
        if forbidden_patterns:
            prompt_parts.append(f"Forbidden patterns: {', '.join(str(x) for x in forbidden_patterns)}")
        if max_files:
            prompt_parts.append(f"Max files changed: {max_files}")
        prompt_parts.append(f"ExperimentResult: {experiment_result_id}")
        prompt_parts.append(f"AuthoringResult: {authoring_result_id}")
        prompt_parts.append(f"Candidate ref: {candidate_ref}")
        prompt_parts.append(
            "Authoring changed files: " + ", ".join(authorized_changed_files)
        )
        prompt_parts.append(
            f"Evaluated environment: {evaluated_environment_id} "
            f"(platforms: {', '.join(evaluated_platforms)})"
        )
        prompt_parts.append(f"Evaluated baseline commit: {evaluated_baseline_commit}")
        prompt_parts.append(f"Required candidate HEAD: {evaluated_candidate_commit}")
        if learning_refs:
            prompt_parts.append(
                "Learning evidence: "
                + "; ".join(
                    f"{ref.get('mem_id')}: {ref.get('title') or 'learning conclusion'}"
                    for ref in learning_refs[:5]
                )
            )
        prompt_parts.append(
            "Check out the required candidate HEAD, keep the worktree clean, and verify that its existing "
            "diff stays within the approved target paths. If the commit is unavailable or does not match "
            "the authorization, stop and report the mismatch."
        )
        prompt_parts.append(
            "Produce a concise verification summary with the concrete files changed and reasoning."
        )
        return "\n\n".join(prompt_parts)

    constraints = dict(task.get("constraints") or {})
    metadata = dict(task.get("metadata") or {})
    baseline_worktree = str(
        constraints.get("baseline_worktree_path")
        or constraints.get("worktree_path")
        or ""
    ).strip()
    baseline_slot_id = str(
        constraints.get("baseline_slot_id")
        or constraints.get("target_slot_id")
        or ""
    ).strip()
    learning_branch = str(
        metadata.get("learning_branch")
        or ((task.get("evidence") or {}).get("learning_branch"))
        or ""
    ).strip()
    prompt_parts = [f"{AUTONOMOUS_LEARNING_TASK_PREFIX} {title}"]
    if summary:
        prompt_parts.append(summary)
    if learning_branch == "codebase_baseline":
        prompt_parts.append("Learning branch: shell codebase baseline")
    elif learning_branch == "exploratory":
        prompt_parts.append("Learning branch: exploratory")
    if baseline_slot_id:
        prompt_parts.append(f"Shell slot baseline: {baseline_slot_id}")
    if baseline_worktree:
        prompt_parts.append(f"Shell worktree baseline: {baseline_worktree}")
    prompt_parts.append(
        "This is a read-only research run. Do not write files, modify skills or memory, "
        "delegate work, commit changes, or claim completion of implementation work."
    )
    if learning_branch == "exploratory":
        prompt_parts.append(
            "For exploratory research, call web_search first, then use web_extract on the "
            "most relevant primary sources. Base the conclusion on the returned evidence and "
            "include source URLs plus any material uncertainty in the final response."
        )
    else:
        prompt_parts.append(
            "Inspect the available read-only sources and produce structured findings, "
            "conclusions, and explicit uncertainty."
        )
    return "\n\n".join(part for part in prompt_parts if part)


def bind_autonomous_execution_start(
    task: Dict[str, Any],
    prompt: str,
) -> str:
    run_id = str(task.get("_autonomous_task_run_id") or "").strip() or str(uuid.uuid4())
    task["_autonomous_task_run_id"] = run_id
    task["_autonomous_execution_start_text"] = prompt
    task["_autonomous_execution_started"] = True
    return run_id


def autonomous_task_run_id_for_message(
    current_task: Dict[str, Any] | None,
    message: Any,
) -> str:
    if not isinstance(current_task, dict) or not isinstance(message, str):
        return ""
    run_id = str(current_task.get("_autonomous_task_run_id") or "").strip()
    if not run_id:
        return ""
    if message == str(current_task.get("_autonomous_execution_start_text") or ""):
        return run_id
    if message.startswith(AUTONOMOUS_LEARNING_TASK_PREFIX) or message.startswith(
        AUTONOMOUS_BODY_IMPROVEMENT_TASK_PREFIX
    ):
        return run_id
    return ""


class AutonomousExecutorRuntime:
    """Runtime bridge for API-A autonomous tasks, hosted by the interactive CLI."""

    _LEASE_RENEW_INTERVAL_SECONDS = 60.0
    _LEASE_SECONDS = 300.0

    def __init__(
        self,
        ports: AutonomousExecutorPorts,
        *,
        push_cli_agent_scene: Callable[..., Any],
        git_head_commit: Callable[[str], str],
        git_improvement_diff: Callable[[str, str], Optional[Dict[str, Any]]],
        cprint: Callable[[str], None],
    ) -> None:
        self.ports = ports
        self._push_cli_agent_scene = push_cli_agent_scene
        self._git_head_commit = git_head_commit
        self._git_improvement_diff = git_improvement_diff
        self._cprint = cprint
        self._last_lease_renewal_at = 0.0

    def find_owned_running_task(self) -> Dict[str, Any] | None:
        """Recover the running autonomous task owned by this CLI session, if any."""
        session_id = str(self.ports.get_session_id() or "").strip()
        if not session_id:
            return None

        try:
            resp = json.loads(
                urllib.request.urlopen(
                    f"{default_gateway_url()}/v1/tasks?status=running",
                    timeout=10,
                ).read()
            )
        except Exception:
            return None

        tasks = resp.get("tasks", []) if isinstance(resp, dict) else []
        for task in tasks:
            lease = dict(task.get("execution_lease") or {})
            owner_session_id = str(lease.get("owner_session_id") or "").strip()
            if owner_session_id != session_id:
                continue
            if lease.get("state") != "active":
                continue
            return task
        return None

    def inject_execution_prompt(
        self,
        task: Dict[str, Any],
        execution_kind: str,
        *,
        recovered: bool = False,
    ) -> bool:
        if task.get("_autonomous_execution_started"):
            return True
        try:
            prompt = build_autonomous_task_prompt(
                task,
                execution_kind,
                git_head_commit=self._git_head_commit,
            )
            if execution_kind == "body_improvement":
                raw_manifest = self.ports.prepare_body_worktree(
                    self.ports.get_session_id(),
                    str(task.get("_improvement_worktree") or ""),
                    str(task.get("_baseline_head") or ""),
                )
                manifest = ExecutionEnvironmentManifest.model_validate(raw_manifest)
                if manifest.validation_scope != "container":
                    raise ValueError(
                        "autonomous body worktree must be bound to a container manifest"
                    )
                task["_execution_environment"] = manifest.model_dump(mode="json")
                execution_tools = ", ".join(
                    (
                        f"{tool.name}={tool.executable} ({tool.version})"
                        if tool.available
                        else f"{tool.name}=unavailable"
                    )
                    for tool in manifest.tools
                    if tool.scope == "execution"
                )
                prompt += (
                    "\n\nExecution environment: "
                    f"{manifest.execution_environment_id} "
                    f"({manifest.execution_os}, scope={manifest.validation_scope}). "
                    "Paths under the mounted repository must use /workspace. "
                    f"Sandbox toolchain: {execution_tools}. "
                    "Use these sandbox executables and report a missing dependency as "
                    "blocked rather than substituting a host path. "
                    "This manifest proves only the container environment; do not claim "
                    "that Windows-host tests passed unless separate Windows evidence exists."
                )
            run_id = bind_autonomous_execution_start(task, prompt)
            self.ports.set_current_task_run_id(run_id)
            self.ports.enqueue_pending_input(prompt)
            self.ports.append_execution_event(
                "恢复链路项的自主执行已重新起跑，等待模型响应" if recovered else "自主执行已起跑，等待模型响应",
                tone="warn" if recovered else "info",
                stage="autonomous_execution_started",
            )
            return True
        except Exception as exc:
            if execution_kind == "body_improvement":
                try:
                    self.ports.release_task_environment(self.ports.get_session_id())
                except Exception:
                    pass
            for key in (
                "_autonomous_task_run_id",
                "_autonomous_execution_start_text",
                "_autonomous_execution_started",
                "_execution_environment",
            ):
                task.pop(key, None)
            self.ports.set_current_task_run_id("")
            self.ports.append_execution_event(
                f"自主执行环境准备失败: {str(exc)[:300]}",
                tone="error",
                stage="autonomous_environment_failed",
            )
            return False

    def clear_current_task_state(self) -> None:
        try:
            self.ports.release_task_environment(self.ports.get_session_id())
        except Exception:
            pass
        self.ports.set_current_task(None)
        self.ports.set_current_task_started_at(0)
        self.ports.set_current_task_run_id("")
        self.ports.set_last_agent_turn_result(None)

    def current_task(self) -> Dict[str, Any] | None:
        """Expose the current task snapshot without leaking host state."""
        return self.ports.get_current_task()

    def set_last_agent_turn_result(self, result: Dict[str, Any] | None) -> None:
        """Store the latest model-turn result for autonomous writeback."""
        self.ports.set_last_agent_turn_result(result)

    def record_turn_result(
        self,
        result: Dict[str, Any],
        *,
        autonomous_task_run_id: str,
        timeout_writeback_succeeded: bool,
    ) -> None:
        """Publish the model result to the autonomous task state owner."""
        if autonomous_task_run_id and not timeout_writeback_succeeded:
            result["autonomous_task_run_id"] = autonomous_task_run_id
            self.ports.set_last_agent_turn_result(result)
        elif self.ports.get_current_task() is None:
            self.ports.set_last_agent_turn_result(result)

    def record_model_turn_finished(
        self,
        result: Dict[str, Any],
        *,
        autonomous_task_run_id: str,
        timeout_writeback_succeeded: bool,
    ) -> None:
        """Publish the autonomous model-turn status event when applicable."""
        if (
            not self.ports.autonomous_gate_active()
            or self.ports.get_current_task() is None
            or not autonomous_task_run_id
            or timeout_writeback_succeeded
        ):
            return
        if result["failed"] or result["partial"]:
            message = f"模型回合结束，但结果异常: {result['error'] or 'unknown error'}"
            tone = "error"
        elif result["interrupted"]:
            message = "模型回合被中断，等待下一条指令"
            tone = "warn"
        else:
            message = "模型回合完成，等待任务回写"
            tone = "success"
        self.ports.append_execution_event(
            message,
            tone=tone,
            stage="model_turn_finished",
        )

    def report_current_task_timeout_if_needed(
        self,
        *,
        gateway_base: str | None = None,
        timeout: float = 15,
        now: float | None = None,
    ) -> bool:
        current = self.ports.get_current_task()
        if not isinstance(current, dict):
            return False
        started_at = float(self.ports.get_current_task_started_at() or 0.0)
        if not started_at:
            return False
        current_time = time.time() if now is None else float(now)
        elapsed = current_time - started_at
        if elapsed <= 1800:
            return False
        lease = dict(current.get("execution_lease") or {})
        heartbeat_raw = str(lease.get("heartbeat_at") or "").strip()
        if heartbeat_raw:
            try:
                heartbeat_at = datetime.fromisoformat(heartbeat_raw).timestamp()
                if current_time - heartbeat_at <= self._LEASE_SECONDS:
                    return False
            except ValueError:
                pass

        task_id = str(current.get("task_id") or "").strip()
        if not task_id:
            return False
        execution_kind = autonomous_task_execution_kind(current)
        task_label = autonomous_task_label(execution_kind)
        writeback_ok = self.post_task_decision(
            task_id,
            decision="failed",
            reason=f"Autonomous {task_label} timed out (30 min).",
            context={
                "error": "timeout",
                "elapsed_s": int(elapsed),
                "execution_kind": execution_kind,
                "autonomous_task_run_id": str(current.get("_autonomous_task_run_id") or ""),
            },
            timeout=timeout,
            gateway_base=gateway_base,
        )
        if writeback_ok:
            self._cprint(f"  ⏰  Autonomous {task_label} {task_id[:8]}... timed out ({int(elapsed)}s)")
            self.ports.append_execution_event(
                f"任务 {task_id[:8]} 超时，已回写 failed",
                tone="error",
                stage="writeback",
            )
            self._push_cli_agent_scene(
                "idle",
                session_id=self.ports.get_session_id() or None,
                agent_role="supervisor_task",
            )
            self.clear_current_task_state()
        return True

    def post_task_decision(
        self,
        task_id: str,
        *,
        decision: str,
        reason: str,
        context: Dict[str, Any] | None = None,
        final_response: str = "",
        timeout: float = 15,
        gateway_base: str | None = None,
    ) -> bool:
        gateway_base = (gateway_base or default_gateway_url()).rstrip("/")
        payload: Dict[str, Any] = {
            "decision": decision,
            "reason": reason,
            "session_id": str(self.ports.get_session_id() or ""),
            "context": {
                "source": "cli_agent_pull",
                **dict(context or {}),
            },
        }
        if final_response:
            payload["final_response"] = final_response[:4000]
        current = self.ports.get_current_task()
        if isinstance(current, dict) and current.get("task_id") == task_id:
            lease = current.get("execution_lease")
            if isinstance(lease, dict):
                payload["execution_lease"] = dict(lease)
        try:
            request = urllib.request.Request(
                f"{gateway_base}/v1/tasks/{task_id}/decision",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(request, timeout=timeout)
            return True
        except Exception as exc:
            self.ports.append_execution_event(
                f"任务 {task_id[:8]} 回写 {decision} 失败，保留本地状态待重试",
                tone="error",
                stage="writeback_failed",
            )
            try:
                self._cprint(f"  ⚠️  Autonomous task writeback failed for {task_id[:8]}: {exc}")
            except Exception:
                pass
            return False

    def renew_current_task_lease_if_due(
        self,
        *,
        gateway_base: str | None = None,
        now: float | None = None,
    ) -> bool:
        """Renew the active autonomous lease from the CLI poll heartbeat."""
        current = self.ports.get_current_task()
        if not isinstance(current, dict):
            return False
        lease = dict(current.get("execution_lease") or {})
        if lease.get("state") != "active" or not lease.get("attempt_id"):
            return False
        current_time = time.time() if now is None else float(now)
        if current_time - self._last_lease_renewal_at < self._LEASE_RENEW_INTERVAL_SECONDS:
            return True
        task_id = str(current.get("task_id") or "").strip()
        if not task_id:
            return False
        payload = {
            "decision": "running",
            "actor": "cli_agent",
            "reason": "API-A autonomous executor heartbeat.",
            "session_id": str(self.ports.get_session_id() or ""),
            "lease_seconds": self._LEASE_SECONDS,
            "execution_lease": lease,
            "context": {"source": "cli_agent_pull", "heartbeat": True},
        }
        try:
            request = urllib.request.Request(
                f"{(gateway_base or default_gateway_url()).rstrip('/')}/v1/tasks/{task_id}/decision",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            response = json.loads(urllib.request.urlopen(request, timeout=15).read())
            renewed = response.get("task") if isinstance(response, dict) else None
            if not isinstance(renewed, dict):
                return False
            self.ports.set_current_task(renewed)
            self._last_lease_renewal_at = current_time
            return True
        except Exception as exc:
            self.ports.append_execution_event(
                f"任务 {task_id[:8]} lease 续租失败",
                tone="error",
                stage="lease_renew_failed",
            )
            try:
                self._cprint(f"  ⚠️  Autonomous task lease renewal failed for {task_id[:8]}: {exc}")
            except Exception:
                pass
            return False

    def interrupt_current_task(
        self,
        *,
        reason: str,
        source: str,
        timeout: float = 5,
        gateway_base: str | None = None,
    ) -> bool:
        current = self.ports.get_current_task()
        if current is None:
            return True
        task_id = str(current.get("task_id") or "").strip()
        if not task_id:
            return True
        execution_kind = autonomous_task_execution_kind(current)
        ok = self.post_task_decision(
            task_id,
            decision="failed",
            reason=reason,
            context={
                "source": source,
                "execution_kind": execution_kind,
                "interrupted": True,
            },
            timeout=timeout,
            gateway_base=gateway_base,
        )
        if ok:
            self.ports.append_execution_event(
                f"任务 {task_id[:8]} 已按中断回写 failed",
                tone="warn",
                stage="writeback",
            )
            self.clear_current_task_state()
        return ok

    def poll_workflow(self) -> None:
        gateway_base = default_gateway_url()

        if self.ports.get_current_task() is None:
            recovered_task = self.find_owned_running_task()
            if recovered_task is not None:
                self.ports.set_current_task(recovered_task)
                self.ports.append_execution_event(
                    f"认回运行中任务 {str(recovered_task.get('task_id') or '')[:8]}",
                    tone="warn",
                    stage="claim",
                )
                metadata = dict(recovered_task.get("metadata") or {})
                started_at_raw = str(metadata.get("execution_started_at") or "").strip()
                if started_at_raw:
                    try:
                        started_dt = datetime.fromisoformat(started_at_raw)
                        self.ports.set_current_task_started_at(started_dt.timestamp())
                    except ValueError:
                        self.ports.set_current_task_started_at(0.0)
                recovered_execution_kind = autonomous_task_execution_kind(recovered_task)
                if not self.ports.agent_running() and self.ports.get_last_agent_turn_result() is None:
                    if not self.inject_execution_prompt(
                        recovered_task,
                        recovered_execution_kind,
                        recovered=True,
                    ):
                        writeback_ok = self.post_task_decision(
                            str(recovered_task.get("task_id") or ""),
                            decision="failed",
                            reason="API-A 自主执行面认回链路项后，未能重新起跑执行。",
                            context={
                                "error": "recovered_execution_start_failed",
                                "execution_kind": recovered_execution_kind,
                            },
                            timeout=15,
                            gateway_base=gateway_base,
                        )
                        if writeback_ok:
                            self._push_cli_agent_scene(
                                "idle",
                                session_id=self.ports.get_session_id() or None,
                                agent_role="supervisor_task",
                            )
                            self.clear_current_task_state()
                        return
                    return

        current = self.ports.get_current_task()
        if current is not None:
            self.renew_current_task_lease_if_due(gateway_base=gateway_base)
            current = self.ports.get_current_task() or current
            task_id = current.get("task_id", "")
            execution_kind = autonomous_task_execution_kind(current)
            task_label = autonomous_task_label(execution_kind)
            started_at = self.ports.get_current_task_started_at()
            elapsed = time.time() - started_at if started_at else -1
            turn_result = self.ports.get_last_agent_turn_result()
            expected_run_id = str(current.get("_autonomous_task_run_id") or "").strip()
            observed_run_id = str((turn_result or {}).get("autonomous_task_run_id") or "").strip()
            if (
                not self.ports.agent_running()
                and turn_result is not None
                and (not expected_run_id or observed_run_id == expected_run_id)
            ):
                decision = "failed" if (
                    turn_result.get("failed")
                    or turn_result.get("partial")
                    or turn_result.get("interrupted")
                ) else "completed"
                reason = (
                    f"API-A 自主执行面中的{task_label}执行失败：{turn_result.get('error', 'unknown error')}"
                    if decision == "failed"
                    else f"API-A 自主执行面已完成{task_label}。"
                )
                if decision == "completed" and execution_kind == "self_learning":
                    evidence_error = autonomous_learning_evidence_error(current, turn_result)
                    if evidence_error:
                        decision = "failed"
                        reason = f"API-A 自主学习证据不足：{evidence_error}。"
                        turn_result["failed"] = True
                        turn_result["error"] = evidence_error
                if decision == "completed" and execution_kind == "body_improvement":
                    if not self.submit_body_improvement_report(
                        current,
                        task_id,
                        gateway_base,
                        improvement_description=str(turn_result.get("response") or "")[:4000],
                    ):
                        decision = "failed"
                        reason = (
                            "API-A 自主执行面报告替身改进完成，但未能形成可治理的 "
                            "commit/diff 改进报告。"
                        )
                        turn_result["failed"] = True
                        turn_result["error"] = "missing_or_failed_body_improvement_report"

                writeback_ok = self.post_task_decision(
                    task_id,
                    decision=decision,
                    reason=reason,
                    final_response=str(turn_result.get("response") or "")[:4000],
                    context={
                        "elapsed_s": int(elapsed),
                        "execution_kind": execution_kind,
                        "failed": bool(turn_result.get("failed")),
                        "partial": bool(turn_result.get("partial")),
                        "interrupted": bool(turn_result.get("interrupted")),
                        "error": str(turn_result.get("error", "") or "")[:200],
                        "tools_used": list(turn_result.get("tools_used") or []),
                        "source_urls": list(turn_result.get("source_urls") or [])[:20],
                        "execution_environment": dict(
                            current.get("_execution_environment") or {}
                        ),
                    },
                    timeout=15,
                    gateway_base=gateway_base,
                )
                if writeback_ok:
                    self.ports.append_execution_event(
                        f"任务 {task_id[:8]} 已回写 {decision}",
                        tone="error" if decision == "failed" else "success",
                        stage="writeback",
                    )
                    self._push_cli_agent_scene(
                        "idle",
                        session_id=self.ports.get_session_id() or None,
                        agent_role="supervisor_task",
                    )
                    self.clear_current_task_state()
                return
            if self.report_current_task_timeout_if_needed(
                gateway_base=gateway_base,
                timeout=15,
            ):
                return
            return

        try:
            tasks = []
            urls = (
                f"{gateway_base}/v1/tasks?status=approved&task_type=self_learning",
                f"{gateway_base}/v1/tasks?status=approved&execution_kind=body_improvement",
            )
            seen = set()
            for url in urls:
                resp = json.loads(urllib.request.urlopen(url, timeout=10).read())
                fetched = resp.get("tasks", []) if isinstance(resp, dict) else []
                for task in fetched:
                    task_id = str(task.get("task_id", "")).strip()
                    if not task_id or task_id in seen:
                        continue
                    seen.add(task_id)
                    tasks.append(task)
        except Exception:
            return

        if not tasks:
            return

        task = tasks[0]
        task_id = task.get("task_id", "")
        execution_kind = autonomous_task_execution_kind(task)
        title = task.get("title", "Agent task")

        try:
            run_payload = json.dumps({
                "decision": "running",
                "actor": "cli_agent",
                "reason": "API-A 自主执行面已认领链路项并开始执行。",
                "context": {
                    "session_id": self.ports.get_session_id() or None,
                    "source": "cli_agent_pull",
                    "execution_kind": execution_kind,
                },
                "metadata": {
                    "execution_started_at": datetime.now().astimezone().isoformat(),
                    "execution_source": "cli_agent_pull",
                },
            }).encode()
            request = urllib.request.Request(
                f"{gateway_base}/v1/tasks/{task_id}/decision",
                data=run_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            claim_response = json.loads(
                urllib.request.urlopen(request, timeout=15).read()
            )
            claimed_task = claim_response.get("task")
            if not isinstance(claimed_task, dict):
                return
            lease = claimed_task.get("execution_lease")
            if not isinstance(lease, dict) or not lease.get("attempt_id"):
                return
            task = claimed_task
        except Exception:
            return

        self.ports.set_current_task(task)
        self.ports.set_current_task_started_at(time.time())
        self.ports.set_current_task_run_id("")
        self.ports.set_last_agent_turn_result(None)
        self.ports.append_execution_event(
            f"已接管任务 {task_id[:8]} · {title}",
            tone="success",
            stage="claim",
        )
        self._push_cli_agent_scene(
            "code_editing" if execution_kind == "body_improvement" else "learning",
            session_id=self.ports.get_session_id() or None,
            task_id=task_id,
            execution_kind=execution_kind,
            agent_role="supervisor_task",
        )

        try:
            touch_payload = json.dumps({
                "activity_kind": "agent_work",
                "source_service": "cli_agent",
                "metadata": {
                    "task_id": task_id,
                    "title": title,
                    "source": "autonomous_pull",
                    "execution_kind": execution_kind,
                },
            }).encode()
            request = urllib.request.Request(
                f"{gateway_base}/admin/activity/touch",
                data=touch_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(request, timeout=10)
        except Exception:
            pass

        if not self.inject_execution_prompt(task, execution_kind):
            task_label = autonomous_task_label(execution_kind)
            try:
                self._cprint(
                    f"  ⚠️  Autonomous {task_label} execution failed to start {task_id[:8]}..."
                )
            except Exception:
                pass
            self.ports.append_execution_event(
                f"任务 {task_id[:8]} 自主执行启动失败",
                tone="error",
                stage="autonomous_execution_start_failed",
            )
            writeback_ok = self.post_task_decision(
                task_id,
                decision="failed",
                reason="CLI Agent failed to start autonomous execution for this task.",
                context={"error": "execution_start_failed", "execution_kind": execution_kind},
                timeout=15,
                gateway_base=gateway_base,
            )
            if writeback_ok:
                self._push_cli_agent_scene(
                    "idle",
                    session_id=self.ports.get_session_id() or None,
                    agent_role="supervisor_task",
                )
                self.clear_current_task_state()

    def submit_body_improvement_report(
        self,
        task: Dict[str, Any],
        task_id: str,
        gateway_base: str,
        *,
        improvement_description: str,
    ) -> bool:
        worktree_path = str(task.get("_improvement_worktree") or "").strip()
        baseline_head = str(task.get("_baseline_head") or "").strip()
        slot_id = str(task.get("_improvement_slot_id") or "").strip()
        if not worktree_path or not baseline_head or not slot_id:
            return False
        diff = self._git_improvement_diff(worktree_path, baseline_head)
        if not diff or not diff.get("changed_files"):
            self.ports.append_execution_event(
                f"任务 {task_id[:8]} 未检测到替身提交，跳过改进报告",
                tone="warn",
                stage="improvement_report_skipped",
            )
            return False
        expected_candidate_head = str(
            task.get("_expected_candidate_head") or ""
        ).strip().lower()
        actual_candidate_head = str(diff.get("commit_hash") or "").strip().lower()
        if not expected_candidate_head or actual_candidate_head != expected_candidate_head:
            self.ports.append_execution_event(
                f"任务 {task_id[:8]} 当前 HEAD 与评测候选提交不一致，拒绝提交报告",
                tone="error",
                stage="improvement_report_commit_mismatch",
            )
            return False
        learning_refs = []
        evidence = task.get("evidence") or {}
        if isinstance(evidence, dict) and evidence.get("learning_refs"):
            learning_refs = evidence.get("learning_refs") or []
        report = {
            "slot_id": slot_id,
            "task_id": task_id,
            "baseline_commit": baseline_head,
            "commit_hash": diff["commit_hash"],
            "diff_summary": diff["diff_summary"],
            "changed_files": diff["changed_files"],
            "learning_refs": learning_refs,
            "improvement_description": improvement_description,
            "session_id": str(self.ports.get_session_id() or ""),
            "execution_lease": dict(task.get("execution_lease") or {}),
            "execution_environment": dict(
                task.get("_execution_environment") or {}
            ),
        }
        try:
            request = urllib.request.Request(
                f"{gateway_base}/v1/body/improvement-report",
                data=json.dumps(report).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(request, timeout=15)
            self.ports.append_execution_event(
                f"任务 {task_id[:8]} 改进报告已提交（{len(diff['changed_files'])} 文件）",
                tone="success",
                stage="improvement_report",
            )
            return True
        except Exception:
            self.ports.append_execution_event(
                f"任务 {task_id[:8]} 改进报告提交失败",
                tone="error",
                stage="improvement_report_failed",
            )
            return False
