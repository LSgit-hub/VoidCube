from __future__ import annotations

import json
import logging
import os
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

from VoidCube_cli.ops.executor import default_gateway_url
from VoidCube_core.runtime_paths import get_runtime_layout


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScheduledTaskExecutorPorts:
    """Explicit CLI operations required by scheduled execution."""

    auto_task_running: Callable[[], bool]
    execution_gate: Any | None
    get_session_id: Callable[[], str]
    set_execution_active: Callable[[bool], None]
    start_background_task: Callable[..., bool]


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
        lease_renew_interval_seconds: float = 60.0,
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
        self._active_run_id = ""
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

    def _auto_task_is_running(self) -> bool:
        return bool(self.ports.auto_task_running())

    def _acquire_execution_gate(self) -> bool:
        gate = self.ports.execution_gate
        if gate is None:
            return True
        acquired = bool(gate.acquire(blocking=False))
        self._execution_gate_acquired = acquired
        return acquired

    def _release_execution_slot(self) -> None:
        with self._state_lock:
            self.ports.set_execution_active(False)
            if self._execution_gate_acquired:
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
        if self._active_run_id:
            return
        now = time.monotonic()
        if now - self._last_poll_at < self.poll_interval_seconds:
            return
        self._last_poll_at = now
        # The scheduled Host owns its own Agent and execution gate.  Foreground
        # chat, commands, and manual background work live on another Host and
        # must never delay this poller or vice versa.
        if self._auto_task_is_running() or not self._poll_lock.acquire(blocking=False):
            return

        execution_started = False
        heartbeat_stop: threading.Event | None = None
        try:
            if not self._acquire_execution_gate():
                return
            self.ports.set_execution_active(True)
            if self._auto_task_is_running():
                self._release_execution_slot()
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

            with self._state_lock:
                self._active_run_id = run_id
            heartbeat_stop = threading.Event()
            self._start_lease_heartbeat(
                run_id=run_id,
                owner_session_id=owner_session_id,
                stop_event=heartbeat_stop,
            )
            title = str(task.get("title") or "定时任务").strip()
            instruction = str(task.get("instruction") or "").strip()
            companion_media = task.get("requested_via") == "companion_media"
            companion_delegate = task.get("requested_via") == "companion_delegate"
            api_b_origin = (
                str(task.get("created_by") or "").strip().lower() == "api_b"
                or companion_media
                or companion_delegate
            )
            worker_role = str(task.get("worker_role") or "").strip().lower()
            if companion_delegate:
                prompt = (
                    "这是日常模式下 API-B 制定计划后转交的执行请求。你是隔离的 API-A 子代理，"
                    "必须使用正常工具和技能完成请求并给出真实结果。API-B 只负责规划，尚未执行任何步骤。"
                    "不要创建新的定时任务，也不要把请求交给 Auto 自主链。\n\n"
                    f"员工角色：{worker_role}\n请求：{title}\nAPI-B 的执行说明：{instruction}"
                )
                task_label = f"API-B 指令 · {title}"
                response_title = "> Voidcube（API-A 子代理）"
            elif companion_media:
                prompt = (
                    "这是日常模式下星子转交的即时媒体播放请求。请使用 API-A 的正常工具能力"
                    "查找可靠、可播放的媒体 URL；歌单优先一次调用 media_playlist，单项才调用 media_play。"
                    "media_playlist 返回 status=ok 即表示整张歌单已入队，不要再调用浏览器、端口检查或其他验证工具。"
                    "不要创建定时任务，也不要把请求交给 Auto 自主链。\n\n"
                    f"员工角色：{worker_role}\n请求：{title}\n播放要求：{instruction}"
                )
                task_label = f"媒体请求 · {title}"
                response_title = "> Voidcube（媒体播放）"
            elif api_b_origin:
                prompt = (
                    "这是日常模式下由 API-B 秘书安排并已到期的工作。你是隔离的 API-A 子代理，"
                    "必须使用正常工具和技能完成任务并给出真实结果。API-B 只负责传达和安排，"
                    "尚未执行任务。不要创建新的定时任务，也不要把任务交给 Auto 自主链。\n\n"
                    f"员工角色：{worker_role}\n任务：{title}\nAPI-B 的工作指令：{instruction}"
                )
                task_label = f"API-B 指令 · {title}"
                response_title = "> Voidcube（API-A 子代理）"
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
                    if self._active_run_id != run_id:
                        return
                    self._active_run_id = ""
                heartbeat_stop.set()
                self._outbox.enqueue(
                    run_id,
                    {
                        "owner_session_id": owner_session_id,
                        "success": bool(success),
                        "result_summary": response_text,
                        "error": error,
                    },
                )
                self._flush_writebacks()
                self._release_execution_slot()

            try:
                started = self.ports.start_background_task(
                    prompt,
                    task_id=f"scheduled_{run_id}",
                    task_label=task_label,
                    response_title=response_title,
                    request_timeout_seconds=self.request_timeout_seconds,
                    timeout_seconds=self.execution_timeout_seconds,
                    persist_session=False,
                    on_complete=on_complete,
                    worker_role=worker_role,
                )
            except Exception as exc:
                on_complete(False, "", f"API-A worker route unavailable: {exc}")
                started = False
            execution_started = bool(started)
            if not started and self._active_run_id:
                on_complete(False, "", "API-A scheduled execution could not start")
        finally:
            if not execution_started and self._active_run_id:
                if heartbeat_stop is not None:
                    heartbeat_stop.set()
                with self._state_lock:
                    self._active_run_id = ""
                self._release_execution_slot()
            self._poll_lock.release()
