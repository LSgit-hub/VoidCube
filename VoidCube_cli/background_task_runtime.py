"""Explicit runtime for isolated manual and scheduled API-A tasks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import threading
import time
from datetime import datetime
from threading import RLock, Thread
from typing import Any
import uuid


CompletionOutcome = Callable[[dict[str, Any] | None], tuple[bool, str, str]]
AgentFactory = Callable[[dict[str, Any], str, dict[str, Any], bool], Any]


@dataclass(frozen=True, slots=True)
class BackgroundTaskSnapshot:
    task_id: str
    thread_name: str
    task_num: int | None
    prompt_preview: str
    started_at: float


@dataclass
class BackgroundTaskState:
    """Single mutable owner for manual and scheduled background task tracking."""

    _tasks: dict[str, Thread] = field(default_factory=dict)
    _info: dict[str, dict[str, Any]] = field(default_factory=dict)
    _counter: int = 0
    _lock: RLock = field(default_factory=RLock)

    def next_task_number(self) -> int:
        with self._lock:
            self._counter += 1
            return self._counter

    def record_info(self, task_id: str, info: dict[str, Any]) -> None:
        with self._lock:
            self._info[task_id] = dict(info)

    def register_thread(self, task_id: str, thread: Thread) -> None:
        with self._lock:
            self._tasks[task_id] = thread

    def finish(self, task_id: str) -> None:
        with self._lock:
            self._tasks.pop(task_id, None)
            self._info.pop(task_id, None)

    def has_running_tasks(self) -> bool:
        with self._lock:
            return any(
                callable(getattr(thread, "is_alive", None)) and thread.is_alive()
                for thread in self._tasks.values()
            )

    def active_snapshots(self) -> tuple[BackgroundTaskSnapshot, ...]:
        with self._lock:
            snapshots: list[BackgroundTaskSnapshot] = []
            for task_id, thread in self._tasks.items():
                if not thread.is_alive():
                    continue
                info = self._info.get(task_id, {})
                snapshots.append(
                    BackgroundTaskSnapshot(
                        task_id=task_id,
                        thread_name=str(getattr(thread, "name", "")),
                        task_num=info.get("task_num"),
                        prompt_preview=str(info.get("prompt_preview") or task_id),
                        started_at=float(info.get("started_at") or 0.0),
                    )
                )
            return tuple(snapshots)


@dataclass(frozen=True, slots=True)
class BackgroundTaskPorts:
    """CLI operations required by the background execution runtime."""

    state: BackgroundTaskState
    ensure_credentials: Callable[[], bool]
    resolve_agent_route: Callable[[str], dict[str, Any]]
    create_agent: AgentFactory
    announce_start: Callable[[int, str, str, str], None]
    render_completion: Callable[[bool, str, str, int, str, str | None, str], None]
    set_thinking: Callable[[str], None]
    invalidate: Callable[[], None]
    bell_on_complete: Callable[[], None]
    completion_outcome: CompletionOutcome
    thread_factory: Callable[..., Thread] = Thread


class BackgroundTaskRuntime:
    """Run isolated agents without accepting a complete CLI host object."""

    def __init__(self, ports: BackgroundTaskPorts) -> None:
        self.ports = ports

    def start(
        self,
        prompt: str,
        *,
        task_id: str | None = None,
        task_label: str = "Background task",
        response_title: str | None = None,
        request_timeout_seconds: float | None = None,
        timeout_seconds: float | None = None,
        persist_session: bool = True,
        on_complete: Callable[[bool, str, str], None] | None = None,
        route_override: dict[str, Any] | None = None,
    ) -> bool:
        state = self.ports.state
        task_num = state.next_task_number()
        task_id = task_id or (
            f"bg_{datetime.now().strftime('%H%M%S')}_{uuid.uuid4().hex[:6]}"
        )

        if not self.ports.ensure_credentials():
            return False

        self.ports.announce_start(task_num, task_id, prompt, task_label)
        task_info = {
            "task_num": task_num,
            "prompt_preview": (
                task_label
                if task_label != "Background task"
                else prompt[:60] + ("..." if len(prompt) > 60 else "")
            ),
            "started_at": time.time(),
        }
        state.record_info(task_id, task_info)

        turn_route = dict(route_override) if route_override is not None else self.ports.resolve_agent_route(prompt)
        request_overrides = dict(turn_route.get("request_overrides") or {})
        if request_timeout_seconds is not None:
            request_overrides["timeout"] = max(0.1, float(request_timeout_seconds))

        def run_background() -> None:
            timeout_timer: threading.Timer | None = None
            timed_out = threading.Event()
            finished = threading.Event()
            timeout_error = ""
            active_agent: dict[str, Any] = {}
            if timeout_seconds is not None:
                effective_timeout = max(0.1, float(timeout_seconds))
                timeout_error = (
                    "API-A background execution timed out after "
                    f"{effective_timeout:g} seconds"
                )

                def interrupt_on_timeout() -> None:
                    if finished.is_set():
                        return
                    timed_out.set()
                    agent = active_agent.get("value")
                    if agent is not None:
                        agent.interrupt(timeout_error)

                timeout_timer = threading.Timer(effective_timeout, interrupt_on_timeout)
                timeout_timer.daemon = True
                timeout_timer.start()
            try:
                bg_agent = self.ports.create_agent(
                    turn_route,
                    task_id,
                    request_overrides,
                    persist_session,
                )
                active_agent["value"] = bg_agent
                if timed_out.is_set():
                    raise TimeoutError(timeout_error)
                bg_agent._print_fn = lambda *_args, **_kwargs: None

                bg_agent.thinking_callback = self.ports.set_thinking
                result = bg_agent.run_conversation(
                    user_message=prompt,
                    task_id=task_id,
                )
                finished.set()

                success, response, error = self.ports.completion_outcome(result)
                if timed_out.is_set():
                    success = False
                    response = ""
                    error = timeout_error
                self.ports.render_completion(
                    success,
                    response,
                    error,
                    task_num,
                    task_label,
                    response_title,
                    prompt,
                )
                self.ports.bell_on_complete()
                self._notify_completion(on_complete, success, response, error)
            except Exception as error:
                finished.set()
                completion_error = timeout_error if timed_out.is_set() else str(error)
                self.ports.render_completion(
                    False,
                    "",
                    completion_error,
                    task_num,
                    task_label,
                    response_title,
                    prompt,
                )
                self._notify_completion(on_complete, False, "", completion_error)
            finally:
                finished.set()
                active_agent.clear()
                if timeout_timer is not None:
                    timeout_timer.cancel()
                state.finish(task_id)
                self.ports.invalidate()

        thread = self.ports.thread_factory(
            target=run_background,
            daemon=True,
            name=f"bg-task-{task_id}",
        )
        state.register_thread(task_id, thread)
        thread.start()
        self.ports.invalidate()
        return True

    @staticmethod
    def _notify_completion(
        callback: Callable[[bool, str, str], None] | None,
        success: bool,
        response: str,
        error: str,
    ) -> None:
        if callback is None:
            return
        try:
            callback(success, response, error)
        except Exception:
            # Completion callbacks are writeback/UI hooks and cannot fail the worker.
            return
