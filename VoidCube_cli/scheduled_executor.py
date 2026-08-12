from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from agent.error_classifier import FailoverReason, classify_api_error
from VoidCube_cli.ops.executor import default_gateway_url
from VoidCube_core.runtime_paths import get_runtime_layout


logger = logging.getLogger(__name__)


def _rate_limit_writeback(error: str) -> Dict[str, Any]:
    message = str(error or "").strip()
    if not message:
        return {
            "rate_limited": False,
            "retry_after_seconds": None,
            "error_code": None,
        }
    classified = classify_api_error(RuntimeError(message))
    explicit_429 = bool(re.search(r"\b429\b", message))
    rate_limited = classified.reason is FailoverReason.rate_limit or explicit_429
    retry_after: float | None = None
    reset_at = classified.error_context.get("reset_at")
    if rate_limited and reset_at not in (None, ""):
        try:
            retry_after = max(0.0, float(reset_at) - time.time())
        except (TypeError, ValueError):
            retry_after = None
    return {
        "rate_limited": rate_limited,
        "retry_after_seconds": retry_after,
        "error_code": 429 if rate_limited else None,
    }


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
    cancel_background_task: Callable[[str, str], bool] | None = None


def _scheduled_timeout_seconds(
    env_name: str,
    *,
    default: float,
    explicit: float | None,
) -> float:
    value: Any = explicit
    if value is None:
        raw = os.getenv(env_name)
        if raw not in (None, ""):
            try:
                value = float(raw)
            except ValueError:
                logger.warning("Ignoring invalid %s=%r; using %.0f", env_name, raw, default)
    if value is None:
        value = default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.1, min(parsed, 86400.0))


class ScheduledWritebackOutbox:
    """Durable completion queue for scheduled API-A executions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS pending_writebacks ("
                    "run_id TEXT PRIMARY KEY, payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, "
                    "next_attempt_at REAL NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '', "
                    "dead_letter INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_scheduled_writebacks_due "
                    "ON pending_writebacks(dead_letter, next_attempt_at, created_at)"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10.0)
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def enqueue(self, run_id: str, payload: Dict[str, Any]) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "INSERT OR REPLACE INTO pending_writebacks "
                    "(run_id, payload, attempts, next_attempt_at, last_error, dead_letter, created_at) "
                    "VALUES (?, ?, 0, 0, '', 0, ?)",
                    (run_id, json.dumps(payload, ensure_ascii=False), time.time()),
                )

    def next_due(self) -> Dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT run_id, payload, attempts FROM pending_writebacks "
                "WHERE dead_letter = 0 AND next_attempt_at <= ? "
                "ORDER BY created_at LIMIT 1",
                (time.time(),),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[1])
        payload["_outbox_run_id"] = str(row[0])
        payload["_outbox_attempts"] = int(row[2])
        return payload

    def mark_delivered(self, run_id: str) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("DELETE FROM pending_writebacks WHERE run_id = ?", (run_id,))

    def mark_failed(self, run_id: str, *, attempts: int, error: str) -> None:
        delay = min(60.0, float(2 ** min(max(attempts, 1), 6)))
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "UPDATE pending_writebacks SET attempts = ?, next_attempt_at = ?, last_error = ? "
                    "WHERE run_id = ?",
                    (attempts, time.time() + delay, error[:1000], run_id),
                )

    def mark_dead(self, run_id: str, *, attempts: int, error: str) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "UPDATE pending_writebacks SET attempts = ?, last_error = ?, dead_letter = 1 "
                    "WHERE run_id = ?",
                    (attempts, error[:1000], run_id),
                )

    def pending_count(self) -> int:
        with closing(self._connect()) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM pending_writebacks WHERE dead_letter = 0"
                ).fetchone()[0]
            )


class ScheduledTaskExecutorRuntime:
    """Main-CLI-only poller that hands due plans to an isolated API-A session."""

    def __init__(
        self,
        ports: ScheduledTaskExecutorPorts,
        *,
        poll_interval_seconds: float = 2.0,
        lease_seconds: int = 300,
        lease_renew_interval_seconds: float = 15.0,
        request_timeout_seconds: float | None = None,
        execution_timeout_seconds: float | None = None,
        outbox_path: str | Path | None = None,
    ):
        self.ports = ports
        self.poll_interval_seconds = max(0.5, float(poll_interval_seconds))
        self.lease_seconds = max(60, min(int(lease_seconds), 3600))
        self.lease_renew_interval_seconds = max(
            10.0,
            min(float(lease_renew_interval_seconds), self.lease_seconds / 2),
        )
        self.request_timeout_seconds = _scheduled_timeout_seconds(
            "VOIDCUBE_SCHEDULED_REQUEST_TIMEOUT_SECONDS",
            default=120.0,
            explicit=request_timeout_seconds,
        )
        self.execution_timeout_seconds = _scheduled_timeout_seconds(
            "VOIDCUBE_SCHEDULED_EXECUTION_TIMEOUT_SECONDS",
            default=600.0,
            explicit=execution_timeout_seconds,
        )
        self._last_poll_at = 0.0
        self._poll_lock = threading.Lock()
        self._delivery_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._active_run_ids: set[str] = set()
        self._companion_run_ids: set[str] = set()
        self._run_task_ids: dict[str, str] = {}
        self._execution_gate_acquired = False
        self._outbox = ScheduledWritebackOutbox(
            outbox_path
            or (get_runtime_layout().runtime_root / "cli" / "scheduled_writebacks.db")
        )

    @staticmethod
    def _post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{default_gateway_url().rstrip('/')}/api/supervisor{path}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            decoded = json.loads(response.read().decode("utf-8"))
            return dict(decoded) if isinstance(decoded, dict) else {}

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
                    self._post(f"/scheduled-task-runs/{run_id}/finish", pending)
                except urllib.error.HTTPError as exc:
                    detail = f"HTTP {exc.code}: {exc.reason}"
                    if exc.code in {400, 404, 409}:
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
    ) -> None:
        def renew_loop() -> None:
            while not stop_event.wait(self.lease_renew_interval_seconds):
                try:
                    self._post(
                        f"/scheduled-task-runs/{run_id}/renew",
                        {
                            "owner_session_id": owner_session_id,
                            "lease_seconds": self.lease_seconds,
                        },
                    )
                except urllib.error.HTTPError as exc:
                    if exc.code in {400, 404, 409}:
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
            try:
                response = self._post(
                    "/scheduled-tasks/claim",
                    {
                        "owner_session_id": owner_session_id,
                        "lease_seconds": self.lease_seconds,
                        "exclude_companion_work": self._autonomous_mode_is_active(),
                    },
                )
            except (OSError, ValueError, urllib.error.HTTPError):
                self._release_execution_slot()
                return
            claim = response.get("claim")
            if not isinstance(claim, dict):
                self._release_execution_slot()
                return
            task = dict(claim.get("task") or {})
            run = dict(claim.get("run") or {})
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
            if api_b_origin and not provider_pool_test:
                self._mark_companion_started(run_id)
            if companion_delegate:
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
                limit_metadata = _rate_limit_writeback(error) if not success else {
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
                )
            except Exception as exc:
                on_complete(False, "", f"API-A worker route unavailable: {exc}")
                started = False
            execution_started = bool(started)
            if not started and run_id in self._active_run_ids:
                on_complete(False, "", "API-A scheduled execution could not start")
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
