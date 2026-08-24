from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from typing import Any, Dict


logger = logging.getLogger(__name__)


class ScheduledRequestRejected(RuntimeError):
    """A scheduled-task gateway request was permanently or temporarily rejected."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = int(status_code)


class ScheduledWritebackOutbox(Protocol):
    def enqueue(self, run_id: str, payload: Dict[str, Any]) -> None: ...
    def next_due(self) -> Dict[str, Any] | None: ...
    def mark_delivered(self, run_id: str) -> None: ...
    def mark_failed(self, run_id: str, *, attempts: int, error: str) -> None: ...
    def mark_dead(self, run_id: str, *, attempts: int, error: str) -> None: ...
    def pending_count(self) -> int: ...


@dataclass(frozen=True, slots=True)
class ScheduledTaskExecutorPorts:
    """Explicit CLI operations required by scheduled execution."""

    autonomous_mode_active: Callable[[], bool]
    autonomous_mode_lock: Any | None
    execution_gate: Any | None
    get_session_id: Callable[[], str]
    set_execution_active: Callable[[bool], None]
    set_companion_active: Callable[[bool], None]
    start_background_task: Callable[..., bool]
    post_supervisor: Callable[[str, Dict[str, Any]], Dict[str, Any]]
    rate_limit_metadata: Callable[[str], Mapping[str, Any]]
    writeback_outbox: ScheduledWritebackOutbox
    inspect_body_execution_readiness: Callable[..., Dict[str, Any]] | None = None
    prepare_task_git_worktree: Callable[..., Dict[str, Any]] | None = None
    release_task_environment: Callable[[str], None] | None = None
    get_supervisor: Callable[[str], Dict[str, Any]] | None = None
    cancel_background_task: Callable[[str, str], bool] | None = None
    validate_execution_lease: Callable[..., Any] | None = None
    recover_executor: Callable[[], bool] | None = None


class ScheduledTaskExecutorRuntime:
    """Employee poller for due API-B assignments and user schedules."""

    def __init__(
        self,
        ports: ScheduledTaskExecutorPorts,
        *,
        poll_interval_seconds: float = 2.0,
        lease_seconds: int = 300,
        lease_renew_interval_seconds: float = 15.0,
        request_timeout_seconds: float = 120.0,
        execution_timeout_seconds: float = 600.0,
    ):
        self.ports = ports
        self.poll_interval_seconds = max(0.5, float(poll_interval_seconds))
        self.lease_seconds = max(60, min(int(lease_seconds), 3600))
        self.lease_renew_interval_seconds = max(
            10.0,
            min(float(lease_renew_interval_seconds), self.lease_seconds / 2),
        )
        self.request_timeout_seconds = max(
            0.1, min(float(request_timeout_seconds), 86400.0)
        )
        self.execution_timeout_seconds = max(
            0.1, min(float(execution_timeout_seconds), 86400.0)
        )
        self._last_poll_at = 0.0
        self._poll_lock = threading.Lock()
        self._delivery_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._active_run_ids: set[str] = set()
        self._companion_run_ids: set[str] = set()
        self._run_task_ids: dict[str, str] = {}
        self._execution_gate_acquired = False
        self._outbox = ports.writeback_outbox

    def _bind_body_improvement_worktree(
        self,
        *,
        autonomous_task: Dict[str, Any],
        task_id: str,
    ) -> tuple[str, Callable[[], tuple[bool, str]], Dict[str, Any]]:
        """Bind one employee task to the registry-owned shell worktree.

        The path is read from Supervisor metadata, never trusted from the task
        prompt.  The terminal override is keyed by task id, so concurrent
        employees cannot overwrite each other's cwd.
        """
        constraints = dict(autonomous_task.get("constraints") or {})
        slot_id = str(
            constraints.get("target_slot_id")
            or autonomous_task.get("target_slot_id")
            or ""
        ).strip()
        declared_path = str(constraints.get("worktree_path") or "").strip()
        if not slot_id or self.ports.get_supervisor is None:
            raise ValueError("body improvement requires Supervisor slot metadata")
        payload = self.ports.get_supervisor(f"/body/slots/{slot_id}")
        meta = dict(payload.get("slot") or payload)
        if str(meta.get("body_state") or "").strip().lower() != "shell":
            raise ValueError("body improvement target is no longer a shell slot")
        registry_payload = self.ports.get_supervisor("/body/registry")
        registry = dict(registry_payload.get("registry") or {})
        if registry and str(registry.get("shell_slot") or "").strip() != slot_id:
            raise ValueError("body improvement target is no longer the registered shell slot")
        if registry and str(registry.get("active_slot") or "").strip() == slot_id:
            raise ValueError("body improvement target cannot be the active slot")
        canonical = str(meta.get("worktree_path") or "").strip()
        if not canonical:
            raise ValueError("Supervisor returned no canonical body worktree")
        from pathlib import Path

        canonical_path = Path(canonical).resolve()
        if declared_path and Path(declared_path).resolve() != canonical_path:
            raise ValueError("body improvement worktree path does not match registry")
        lineage = dict(autonomous_task.get("git_lineage") or {})
        execution_request = dict(autonomous_task.get("execution_request") or {})
        request_lineage = dict(execution_request.get("git_lineage") or {})
        evidence = dict(autonomous_task.get("evidence") or {})
        governed_candidate = str(
            constraints.get("evaluated_candidate_commit")
            or lineage.get("candidate_commit")
            or request_lineage.get("candidate_commit")
            or evidence.get("evaluated_candidate_commit")
            or ""
        ).strip()
        metadata_head = str(
            meta.get("candidate_commit")
            or meta.get("active_commit")
            or meta.get("current_healthy_commit")
            or ""
        ).strip()
        expected_head = governed_candidate or metadata_head
        if governed_candidate and metadata_head.lower() != governed_candidate.lower():
            raise ValueError("body worktree metadata does not match the evaluated candidate")
        if not canonical_path.is_dir() or not expected_head:
            raise ValueError("body improvement worktree is not execution-ready")

        inspect_readiness = self.ports.inspect_body_execution_readiness
        prepare_environment = self.ports.prepare_task_git_worktree
        if inspect_readiness is None or prepare_environment is None:
            raise ValueError("body improvement execution ports are not configured")

        readiness = inspect_readiness(
            slot_id=slot_id,
            worktree_path=str(canonical_path),
            expected_body_state="shell",
        )
        if not readiness.get("ready"):
            raise ValueError(
                "body improvement worktree is not execution-ready: "
                + str(readiness.get("reason") or "unknown")
            )

        environment_manifest = prepare_environment(
            task_id,
            str(canonical_path),
            expected_head=expected_head,
        )

        def verify() -> tuple[bool, str]:
            import subprocess

            try:
                root = subprocess.run(
                    ["git", "rev-parse", "--show-toplevel"],
                    cwd=canonical_path, capture_output=True, text=True, timeout=10,
                )
                head = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=canonical_path, capture_output=True, text=True, timeout=10,
                )
                status = subprocess.run(
                    ["git", "status", "--porcelain", "--untracked-files=all"],
                    cwd=canonical_path, capture_output=True, text=True, timeout=10,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return False, f"body worktree verification failed: {exc}"
            if root.returncode != 0 or Path(root.stdout.strip()).resolve() != canonical_path:
                return False, "body employee escaped the canonical worktree"
            if head.returncode != 0 or head.stdout.strip().lower() != expected_head.lower():
                return False, "body worktree HEAD changed during employee execution"
            if status.returncode != 0 or status.stdout.strip():
                return False, "body worktree is dirty after employee execution"
            return True, ""

        return str(canonical_path), verify, dict(environment_manifest)

    @staticmethod
    def _attach_body_execution_environment(
        response_text: str,
        environment_manifest: Dict[str, Any] | None,
    ) -> str:
        """Attach infrastructure-captured environment evidence to the report."""
        if not environment_manifest:
            return response_text
        import json

        text = str(response_text or "").strip()
        fenced = text.startswith("```")
        if fenced:
            lines = text.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return response_text
        if not isinstance(payload, dict):
            return response_text
        report = payload.get("body_improvement_report")
        if isinstance(report, dict):
            report["execution_environment"] = dict(environment_manifest)
        elif payload.get("execution_kind") == "body_improvement":
            payload["execution_environment"] = dict(environment_manifest)
        else:
            return response_text
        return json.dumps(payload, ensure_ascii=True, sort_keys=True)

    def _autonomous_mode_is_active(self) -> bool:
        return bool(self.ports.autonomous_mode_active())

    def _acquire_execution_gate(self) -> bool:
        with self._state_lock:
            if self._execution_gate_acquired:
                return True
            gate = self.ports.execution_gate
            if gate is None:
                self._execution_gate_acquired = True
                return True
            acquired = bool(gate.acquire(blocking=False))
            self._execution_gate_acquired = acquired
            return acquired

    def _mark_execution_started(self, run_id: str) -> None:
        with self._state_lock:
            self._active_run_ids.add(run_id)
            self.ports.set_execution_active(True)

    def _mark_companion_started(self, run_id: str) -> None:
        with self._state_lock:
            self._companion_run_ids.add(run_id)
            self.ports.set_companion_active(True)

    def _release_execution_slot(self, run_id: str = "") -> None:
        with self._state_lock:
            if run_id:
                self._active_run_ids.discard(run_id)
                self._companion_run_ids.discard(run_id)
                self._run_task_ids.pop(run_id, None)
            active = bool(self._active_run_ids)
            self.ports.set_execution_active(active)
            self.ports.set_companion_active(bool(self._companion_run_ids))
            if not active and self._execution_gate_acquired:
                gate = self.ports.execution_gate
                self._execution_gate_acquired = False
                if gate is not None:
                    try:
                        gate.release()
                    except RuntimeError:
                        pass

    def _flush_writebacks(self, *, limit: int = 4) -> None:
        if not self._delivery_lock.acquire(blocking=False):
            return
        try:
            for _ in range(max(1, int(limit))):
                pending = self._outbox.next_due()
                if pending is None:
                    return
                run_id = str(pending.pop("_outbox_run_id"))
                attempts = int(pending.pop("_outbox_attempts", 0)) + 1
                try:
                    self.ports.post_supervisor(
                        f"/scheduled-task-runs/{run_id}/finish", pending
                    )
                except ScheduledRequestRejected as exc:
                    detail = str(exc)
                    if exc.status_code in {400, 404, 409}:
                        self._outbox.mark_dead(run_id, attempts=attempts, error=detail)
                        logger.error("Scheduled task writeback permanently rejected for %s: %s", run_id, detail)
                    else:
                        self._outbox.mark_failed(run_id, attempts=attempts, error=detail)
                    return
                except Exception as exc:
                    self._outbox.mark_failed(run_id, attempts=attempts, error=str(exc))
                    logger.warning("Scheduled task writeback retry deferred for %s: %s", run_id, exc)
                    return
                self._outbox.mark_delivered(run_id)
        finally:
            self._delivery_lock.release()

    def _start_lease_heartbeat(
        self,
        *,
        run_id: str,
        owner_session_id: str,
        task_id: str,
        stop_event: threading.Event,
        autonomous_task: Dict[str, Any] | None = None,
    ) -> None:
        def renew_loop() -> None:
            while not stop_event.wait(self.lease_renew_interval_seconds):
                try:
                    self.ports.post_supervisor(
                        f"/scheduled-task-runs/{run_id}/renew",
                        {
                            "owner_session_id": owner_session_id,
                            "lease_seconds": self.lease_seconds,
                        },
                    )
                    if autonomous_task:
                        lease = dict(autonomous_task.get("execution_lease") or {})
                        self.ports.post_supervisor(
                            f"/autonomous-chain/tasks/{autonomous_task.get('task_id')}/lease/renew",
                            {
                                "generation": lease.get("generation"),
                                "attempt_id": lease.get("attempt_id"),
                                "owner_session_id": owner_session_id,
                                "lease_seconds": self.lease_seconds,
                            },
                        )
                except ScheduledRequestRejected as exc:
                    if exc.status_code in {400, 404, 409}:
                        cancel = self.ports.cancel_background_task
                        if cancel is not None:
                            cancel(task_id, "任务已被星子取消")
                        stop_event.set()
                        return
                    logger.warning("Scheduled task lease renewal failed for %s: %s", run_id, exc)
                except Exception as exc:
                    logger.warning("Scheduled task lease renewal failed for %s: %s", run_id, exc)

        threading.Thread(
            target=renew_loop,
            daemon=True,
            name=f"scheduled-lease-{run_id[:8]}",
        ).start()

    def poll_workflow(self) -> None:
        self._flush_writebacks()
        if self._outbox.pending_count():
            return
        now = time.monotonic()
        if now - self._last_poll_at < self.poll_interval_seconds:
            return
        self._last_poll_at = now
        # The scheduled Host owns its own Agent and execution gate.  Foreground
        # chat, commands, and manual background work live on another Host and
        # must never delay this poller or vice versa.
        if not self._poll_lock.acquire(blocking=False):
            return

        execution_started = False
        heartbeat_stop: threading.Event | None = None
        claimed_run_id = ""
        mode_lock_acquired = False
        try:
            mode_lock = self.ports.autonomous_mode_lock
            if mode_lock is not None:
                mode_lock_acquired = bool(mode_lock.acquire(blocking=False))
                if not mode_lock_acquired:
                    return
            if not self._acquire_execution_gate():
                return
            owner_session_id = str(self.ports.get_session_id() or "").strip()
            if not owner_session_id:
                self._release_execution_slot()
                return
            autonomous_mode = self._autonomous_mode_is_active()
            claim_payload = {
                "owner_session_id": owner_session_id,
                "lease_seconds": self.lease_seconds,
                "exclude_companion_work": autonomous_mode,
                "exclude_autonomous_work": not autonomous_mode,
            }
            try:
                response = self.ports.post_supervisor(
                    "/scheduled-tasks/claim", claim_payload
                )
            except ScheduledRequestRejected:
                self._release_execution_slot()
                return
            except (OSError, ValueError):
                recover = self.ports.recover_executor
                if recover is None:
                    self._release_execution_slot()
                    return
                try:
                    if not recover():
                        self._release_execution_slot()
                        return
                    response = self.ports.post_supervisor(
                        "/scheduled-tasks/claim", claim_payload
                    )
                except (OSError, ValueError, ScheduledRequestRejected):
                    self._release_execution_slot()
                    return
            claim = response.get("claim")
            if not isinstance(claim, dict):
                self._release_execution_slot()
                return
            task = dict(claim.get("task") or {})
            run = dict(claim.get("run") or {})
            autonomous_task = dict(claim.get("autonomous_task") or {})
            run_id = str(run.get("run_id") or "").strip()
            if not run_id:
                self._release_execution_slot()
                return

            claimed_run_id = run_id
            self._mark_execution_started(run_id)
            task_id = f"scheduled_{run_id}"
            self._run_task_ids[run_id] = task_id
            heartbeat_stop = threading.Event()
            self._start_lease_heartbeat(
                run_id=run_id,
                owner_session_id=owner_session_id,
                task_id=task_id,
                stop_event=heartbeat_stop,
                autonomous_task=autonomous_task or None,
            )
            title = str(task.get("title") or "定时任务").strip()
            instruction = str(task.get("instruction") or "").strip()
            companion_media = task.get("requested_via") == "companion_media"
            companion_delegate = task.get("requested_via") == "companion_delegate"
            provider_pool_test = task.get("requested_via") == "provider_pool_test"
            api_b_origin = (
                str(task.get("created_by") or "").strip().lower() == "api_b"
                or companion_media
                or companion_delegate
            )
            worker_role = str(task.get("worker_role") or "").strip().lower()
            body_worktree_verifier: Callable[[], tuple[bool, str]] | None = None
            body_worktree: str | None = None
            body_environment_manifest: Dict[str, Any] | None = None
            body_binding_error = ""
            execution_kind = str(
                autonomous_task.get("execution_kind")
                or autonomous_task.get("task_family")
                or ""
            ).strip().lower()
            if execution_kind == "body_improvement":
                try:
                    body_worktree, body_worktree_verifier, body_environment_manifest = (
                        self._bind_body_improvement_worktree(
                            autonomous_task=autonomous_task,
                            task_id=task_id,
                        )
                    )
                    ready, readiness_error = body_worktree_verifier()
                    if not ready:
                        raise ValueError(readiness_error)
                except Exception as exc:
                    body_binding_error = f"body improvement worktree binding failed: {exc}"
            if api_b_origin and not provider_pool_test:
                self._mark_companion_started(run_id)
            if execution_kind == "body_improvement":
                prompt = (
                    "这是已通过评测的替身候选提交的受治理验证任务。Supervisor 已将精确的 "
                    "evaluated candidate commit 物化到 canonical shell worktree，并会在回执中注入真实的执行环境证明。"
                    "你只允许检查该 worktree、运行验证并提交 JSON 证据；不得修改任何文件、创建新 commit、reset、"
                    "checkout 或切换 worktree。若检查失败，返回失败原因，不要修复代码。\n\n"
                    f"员工角色：{worker_role}\n任务：{title}\n验证说明：{instruction}"
                )
                task_label = f"替身验证 · {title}"
                response_title = "> Voidcube（替身验证）"
            elif companion_delegate:
                prompt = (
                    "这是日常模式下 API-B 制定计划后转交的执行请求。你是自主链路中的隔离员工 Agent，"
                    "必须使用正常工具和技能完成请求并给出真实结果。API-B 只负责规划，尚未执行任何步骤。"
                    "不要创建新的定时任务，也不要把请求交给 Auto 自主链。\n\n"
                    f"员工角色：{worker_role}\n请求：{title}\nAPI-B 的执行说明：{instruction}"
                )
                task_label = f"自主指令 · {title}"
                response_title = "> Voidcube（员工 Agent）"
            elif companion_media:
                prompt = (
                    "这是日常模式下星子转交的即时媒体播放请求。你是自主链路中的隔离媒体员工 Agent，"
                    "请使用当前角色可用的正常工具能力"
                    "查找可靠、可播放的媒体 URL；歌单优先一次调用 media_playlist，单项才调用 media_play。"
                    "media_playlist 返回 status=ok 即表示整张歌单已入队，不要再调用浏览器、端口检查或其他验证工具。"
                    "不要创建定时任务，也不要把请求交给 Auto 自主链。\n\n"
                    f"员工角色：{worker_role}\n请求：{title}\n播放要求：{instruction}"
                )
                task_label = f"自主媒体 · {title}"
                response_title = "> Voidcube（媒体播放）"
            elif provider_pool_test:
                prompt = (
                    "这是 Provider 池中的员工连通性测试。你是隔离的员工 Agent，"
                    "只需完成下面的测试指令并返回真实结果；不要创建定时任务，"
                    "不要进入用户聊天链路，也不要把任务交给 Auto 自主链。\n\n"
                    f"员工角色：{worker_role}\n测试指令：{instruction}"
                )
                task_label = f"员工测试 · {title}"
                response_title = "> Voidcube（员工测试）"
            elif api_b_origin:
                prompt = (
                    "这是日常模式下由 API-B 秘书安排并已到期的工作。你是自主链路中的隔离执行 Agent，"
                    "必须使用正常工具和技能完成任务并给出真实结果。API-B 只负责传达和安排，"
                    "尚未执行任务。不要创建新的定时任务，也不要把任务交给 Auto 自主链。\n\n"
                    f"员工角色：{worker_role}\n任务：{title}\nAPI-B 的工作指令：{instruction}"
                )
                task_label = f"自主指令 · {title}"
                response_title = "> Voidcube（员工 Agent）"
            else:
                prompt = (
                    "这是用户预先安排并已到期的定时任务。请使用 API-A 的正常工具能力完成任务，"
                    "不要创建新的定时任务，也不要把它交给 Auto 自主链。\n\n"
                    f"任务：{title}\n指令：{instruction}"
                )
                task_label = f"定时任务 · {title}"
                response_title = "> Voidcube（定时任务）"

            def on_complete(success: bool, response_text: str, error: str) -> None:
                with self._state_lock:
                    if run_id not in self._active_run_ids:
                        return
                heartbeat_stop.set()
                if success and body_worktree_verifier is not None:
                    success, verification_error = body_worktree_verifier()
                    if not success:
                        response_text = ""
                        error = verification_error
                    elif body_environment_manifest is not None:
                        response_text = self._attach_body_execution_environment(
                            response_text,
                            body_environment_manifest,
                        )
                try:
                    if (
                        body_worktree_verifier is not None
                        and self.ports.release_task_environment is not None
                    ):
                        self.ports.release_task_environment(task_id)
                except Exception:
                    pass
                limit_metadata = dict(self.ports.rate_limit_metadata(error)) if not success else {
                    "rate_limited": False,
                    "retry_after_seconds": None,
                    "error_code": None,
                }
                self._outbox.enqueue(
                    run_id,
                    {
                        "owner_session_id": owner_session_id,
                        "success": bool(success),
                        "result_summary": response_text,
                        "error": error,
                        "execution_provider": str(execution_details.get("provider") or ""),
                        "execution_model": str(execution_details.get("model") or ""),
                        "elapsed_ms": max(
                            0,
                            round((time.monotonic() - execution_started_at) * 1000),
                        ),
                        **limit_metadata,
                    },
                )
                self._flush_writebacks()
                self._release_execution_slot(run_id)

            try:
                execution_details: Dict[str, Any] = {}
                execution_started_at = time.monotonic()
                if body_binding_error:
                    on_complete(False, "", body_binding_error)
                    started = False
                else:
                    started = self.ports.start_background_task(
                        prompt,
                        task_id=task_id,
                        task_label=task_label,
                        response_title=response_title,
                        request_timeout_seconds=self.request_timeout_seconds,
                        timeout_seconds=self.execution_timeout_seconds,
                        persist_session=False,
                        on_complete=on_complete,
                        worker_role=worker_role,
                        execution_details=execution_details,
                        autonomous_task=autonomous_task or None,
                        validate_execution_lease=self.ports.validate_execution_lease,
                        working_dir=body_worktree,
                    )
            except Exception as exc:
                on_complete(False, "", f"employee worker route unavailable: {exc}")
                started = False
            execution_started = bool(started)
            if not started and run_id in self._active_run_ids:
                on_complete(
                    False,
                    "",
                    body_binding_error
                    or "employee scheduled execution could not start",
                )
        finally:
            if (
                not execution_started
                and claimed_run_id
                and claimed_run_id in self._active_run_ids
            ):
                if heartbeat_stop is not None:
                    heartbeat_stop.set()
                self._release_execution_slot(claimed_run_id)
            elif not claimed_run_id:
                self._release_execution_slot()
            if mode_lock_acquired:
                self.ports.autonomous_mode_lock.release()
            self._poll_lock.release()
