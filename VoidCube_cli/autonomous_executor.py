from __future__ import annotations

import json
import time
import uuid
import urllib.request
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from VoidCube_cli.autonomous_events import append_autonomous_execution_event


AUTONOMOUS_LEARNING_TASK_PREFIX = "[Autonomous Learning Task]"
AUTONOMOUS_BODY_IMPROVEMENT_TASK_PREFIX = "[Autonomous Body Improvement Task]"


def autonomous_task_execution_kind(task: Dict[str, Any]) -> str:
    return str(task.get("execution_kind") or task.get("task_type") or "").strip().lower()


def autonomous_task_label(execution_kind: str) -> str:
    return "改进链路项" if execution_kind == "body_improvement" else "学习链路项"


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
        task["_baseline_head"] = git_head_commit(worktree_path) if git_head_commit else ""
        task["_improvement_worktree"] = worktree_path
        task["_improvement_slot_id"] = str(
            constraints.get("target_slot_id")
            or constraints.get("target_slot")
            or ""
        ).strip()
        editable_dirs = constraints.get("editable_dirs") or []
        forbidden_patterns = constraints.get("forbidden_patterns") or []
        max_files = constraints.get("max_files_changed")
        prompt_parts = [f"{AUTONOMOUS_BODY_IMPROVEMENT_TASK_PREFIX} {title}"]
        if summary:
            prompt_parts.append(summary)
        prompt_parts.append("Edit the shell body code directly and implement the approved improvement.")
        if worktree_path:
            prompt_parts.append(f"Worktree path: {worktree_path}")
        if editable_dirs:
            prompt_parts.append(f"Editable dirs: {', '.join(str(x) for x in editable_dirs)}")
        if forbidden_patterns:
            prompt_parts.append(f"Forbidden patterns: {', '.join(str(x) for x in forbidden_patterns)}")
        if max_files:
            prompt_parts.append(f"Max files changed: {max_files}")
        prompt_parts.append("Commit the shell worktree changes before reporting completion.")
        prompt_parts.append("Produce a concise implementation summary with the concrete files changed and reasoning.")
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
        "Execute this research task thoroughly. Produce structured findings and conclusions."
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

    def __init__(
        self,
        host: Any,
        *,
        push_cli_agent_scene: Callable[..., Any],
        git_head_commit: Callable[[str], str],
        git_improvement_diff: Callable[[str, str], Optional[Dict[str, Any]]],
        cprint: Callable[[str], None],
    ) -> None:
        self.host = host
        self._push_cli_agent_scene = push_cli_agent_scene
        self._git_head_commit = git_head_commit
        self._git_improvement_diff = git_improvement_diff
        self._cprint = cprint

    def find_owned_running_task(self) -> Dict[str, Any] | None:
        """Recover the running autonomous task owned by this CLI session, if any."""
        session_id = str(getattr(self.host, "session_id", "") or "").strip()
        if not session_id:
            return None

        try:
            resp = json.loads(
                urllib.request.urlopen(
                    "http://127.0.0.1:6000/v1/tasks?status=running",
                    timeout=10,
                ).read()
            )
        except Exception:
            return None

        tasks = resp.get("tasks", []) if isinstance(resp, dict) else []
        for task in tasks:
            metadata = dict(task.get("metadata") or {})
            owner_session_id = str(metadata.get("owner_session_id") or "").strip()
            execution_source = str(metadata.get("execution_source") or "").strip().lower()
            if owner_session_id != session_id:
                continue
            if execution_source and execution_source != "cli_agent_pull":
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
            run_id = bind_autonomous_execution_start(task, prompt)
            self.host._current_autonomous_task_run_id = run_id
            self.host._pending_input.put(prompt)
            append_autonomous_execution_event(
                self.host,
                "恢复链路项的自主执行已重新起跑，等待模型响应" if recovered else "自主执行已起跑，等待模型响应",
                tone="warn" if recovered else "info",
                stage="autonomous_execution_started",
            )
            return True
        except Exception:
            return False

    def clear_current_task_state(self) -> None:
        self.host._current_autonomous_task = None
        self.host._current_autonomous_task_started_at = 0
        self.host._current_autonomous_task_run_id = ""
        self.host._last_agent_turn_result = None

    def report_current_task_timeout_if_needed(
        self,
        *,
        gateway_base: str = "http://127.0.0.1:6000",
        timeout: float = 15,
        now: float | None = None,
    ) -> bool:
        current = getattr(self.host, "_current_autonomous_task", None)
        if not isinstance(current, dict):
            return False
        started_at = float(getattr(self.host, "_current_autonomous_task_started_at", 0.0) or 0.0)
        if not started_at:
            return False
        current_time = time.time() if now is None else float(now)
        elapsed = current_time - started_at
        if elapsed <= 1800:
            return False

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
            append_autonomous_execution_event(
                self.host,
                f"任务 {task_id[:8]} 超时，已回写 failed",
                tone="error",
                stage="writeback",
            )
            self._push_cli_agent_scene(
                "idle",
                session_id=getattr(self.host, "session_id", None),
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
        gateway_base: str = "http://127.0.0.1:6000",
    ) -> bool:
        payload: Dict[str, Any] = {
            "decision": decision,
            "reason": reason,
            "session_id": str(getattr(self.host, "session_id", "") or ""),
            "context": {
                "source": "cli_agent_pull",
                **dict(context or {}),
            },
        }
        if final_response:
            payload["final_response"] = final_response[:4000]
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
            append_autonomous_execution_event(
                self.host,
                f"任务 {task_id[:8]} 回写 {decision} 失败，保留本地状态待重试",
                tone="error",
                stage="writeback_failed",
            )
            try:
                self._cprint(f"  ⚠️  Autonomous task writeback failed for {task_id[:8]}: {exc}")
            except Exception:
                pass
            return False

    def interrupt_current_task(
        self,
        *,
        reason: str,
        source: str,
        timeout: float = 5,
        gateway_base: str = "http://127.0.0.1:6000",
    ) -> bool:
        current = getattr(self.host, "_current_autonomous_task", None)
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
            append_autonomous_execution_event(
                self.host,
                f"任务 {task_id[:8]} 已按中断回写 failed",
                tone="warn",
                stage="writeback",
            )
            self.clear_current_task_state()
        return ok

    def poll_workflow(self) -> None:
        gateway_base = "http://127.0.0.1:6000"

        if getattr(self.host, "_current_autonomous_task", None) is None:
            recovered_task = self.find_owned_running_task()
            if recovered_task is not None:
                self.host._current_autonomous_task = recovered_task
                append_autonomous_execution_event(
                    self.host,
                    f"认回运行中任务 {str(recovered_task.get('task_id') or '')[:8]}",
                    tone="warn",
                    stage="claim",
                )
                metadata = dict(recovered_task.get("metadata") or {})
                started_at_raw = str(metadata.get("execution_started_at") or "").strip()
                if started_at_raw:
                    try:
                        started_dt = datetime.fromisoformat(started_at_raw)
                        self.host._current_autonomous_task_started_at = started_dt.timestamp()
                    except ValueError:
                        self.host._current_autonomous_task_started_at = 0.0
                recovered_execution_kind = autonomous_task_execution_kind(recovered_task)
                if not getattr(self.host, "_agent_running", False) and getattr(self.host, "_last_agent_turn_result", None) is None:
                    if not self.inject_execution_prompt(
                        self.host._current_autonomous_task,
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
                                session_id=getattr(self.host, "session_id", None),
                                agent_role="supervisor_task",
                            )
                            self.clear_current_task_state()
                        return
                    return

        current = getattr(self.host, "_current_autonomous_task", None)
        if current is not None:
            task_id = current.get("task_id", "")
            execution_kind = autonomous_task_execution_kind(current)
            task_label = autonomous_task_label(execution_kind)
            started_at = getattr(self.host, "_current_autonomous_task_started_at", 0)
            elapsed = time.time() - started_at if started_at else -1
            turn_result = getattr(self.host, "_last_agent_turn_result", None)
            expected_run_id = str(current.get("_autonomous_task_run_id") or "").strip()
            observed_run_id = str((turn_result or {}).get("autonomous_task_run_id") or "").strip()
            if (
                not self.host._agent_running
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
                    },
                    timeout=15,
                    gateway_base=gateway_base,
                )
                if writeback_ok:
                    append_autonomous_execution_event(
                        self.host,
                        f"任务 {task_id[:8]} 已回写 {decision}",
                        tone="error" if decision == "failed" else "success",
                        stage="writeback",
                    )
                    self._push_cli_agent_scene(
                        "idle",
                        session_id=getattr(self.host, "session_id", None),
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
                    "session_id": getattr(self.host, "session_id", None),
                    "source": "cli_agent_pull",
                    "execution_kind": execution_kind,
                },
                "metadata": {
                    "owner_session_id": getattr(self.host, "session_id", None),
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
            urllib.request.urlopen(request, timeout=15)
        except Exception:
            return

        self.host._current_autonomous_task = task
        self.host._current_autonomous_task_started_at = time.time()
        self.host._current_autonomous_task_run_id = ""
        self.host._last_agent_turn_result = None
        append_autonomous_execution_event(
            self.host,
            f"已接管任务 {task_id[:8]} · {title}",
            tone="success",
            stage="claim",
        )
        self._push_cli_agent_scene(
            "code_editing" if execution_kind == "body_improvement" else "learning",
            session_id=getattr(self.host, "session_id", None),
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

        if not self.inject_execution_prompt(self.host._current_autonomous_task, execution_kind):
            task_label = autonomous_task_label(execution_kind)
            self._cprint(f"  ⚠️  Autonomous {task_label} execution failed to start {task_id[:8]}...")
            append_autonomous_execution_event(
                self.host,
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
                    session_id=getattr(self.host, "session_id", None),
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
            append_autonomous_execution_event(
                self.host,
                f"任务 {task_id[:8]} 未检测到替身提交，跳过改进报告",
                tone="warn",
                stage="improvement_report_skipped",
            )
            return False
        learning_refs = []
        evidence = task.get("evidence") or {}
        if isinstance(evidence, dict) and evidence.get("learning_refs"):
            learning_refs = evidence.get("learning_refs") or []
        report = {
            "slot_id": slot_id,
            "task_id": task_id,
            "commit_hash": diff["commit_hash"],
            "diff_summary": diff["diff_summary"],
            "changed_files": diff["changed_files"],
            "learning_refs": learning_refs,
            "improvement_description": improvement_description,
        }
        try:
            request = urllib.request.Request(
                f"{gateway_base}/v1/body/improvement-report",
                data=json.dumps(report).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(request, timeout=15)
            append_autonomous_execution_event(
                self.host,
                f"任务 {task_id[:8]} 改进报告已提交（{len(diff['changed_files'])} 文件）",
                tone="success",
                stage="improvement_report",
            )
            return True
        except Exception:
            append_autonomous_execution_event(
                self.host,
                f"任务 {task_id[:8]} 改进报告提交失败",
                tone="error",
                stage="improvement_report_failed",
            )
            return False
