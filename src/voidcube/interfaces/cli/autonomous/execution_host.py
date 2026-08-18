"""Narrow state owner for the CLI autonomous execution lane."""

from __future__ import annotations

import queue
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..application import ApplicationRuntime
from ....domain.contracts.events import MessageDelta
from ....domain.contracts.scheduler import TurnLane, TurnRequest
from ....domain.contracts.tool_events import ToolEvent
from ....application.scheduling.turn_scheduler import CancellationToken
from .events import AutonomousPanelEventPorts
from ....systems.supervisor.autonomous_executor import (
    autonomous_task_run_id_for_message,
    autonomous_task_toolsets,
)
from .status_host import initialize_autonomous_status_caches
from ..subagent_observability_runtime import (
    CliSubagentObservabilityPorts,
    CliSubagentObservabilityRuntime,
)


@dataclass(frozen=True, slots=True)
class AutonomousExecutionSnapshot:
    """Read-only autonomous state projected to the parent CLI."""

    session_id: str
    current_task: Mapping[str, Any] | None
    current_task_started_at: float
    agent_running: bool
    last_agent_turn_result: Mapping[str, Any] | None
    pending_input_count: int
    spinner_text: str


class AutonomousExecutionHost:
    """Own autonomous session, Agent and observation state without TUI APIs."""

    def __init__(
        self,
        *,
        session_id: str,
        session_start: datetime,
        model: str,
        provider: str,
        session_db: Any,
        scheduler_runtime: Any,
        execute_turn: Callable[["AutonomousExecutionHost", TurnRequest, CancellationToken], Any],
        invalidate: Callable[[], None],
        tool_event_sink: Callable[["AutonomousExecutionHost", ToolEvent], None],
        panel_event_ports: Callable[[], AutonomousPanelEventPorts],
    ) -> None:
        self.model = str(model or "")
        self.provider = str(provider or "")
        self._session_db = session_db
        self._turn_scheduler_runtime = scheduler_runtime
        self._execute_turn = execute_turn
        self._invalidate_callback = invalidate
        self._tool_event_callback = tool_event_sink
        self._panel_event_ports = panel_event_ports
        self._application_runtime = ApplicationRuntime.create(
            session_id=session_id,
            session_start=session_start,
            event_sink=self._handle_application_event,
        )
        if session_db is not None:
            self._application_runtime.load_session_hydration(
                repository=session_db,
                session_id=session_id,
            )
        self.agent: Any | None = None
        self._active_agent_route_signature: Any = None
        self._active_chat_agent_role = ""
        self._current_trace_id = ""
        self._current_autonomous_task: dict[str, Any] | None = None
        self._current_autonomous_task_started_at = 0.0
        self._current_autonomous_task_run_id = ""
        self._last_agent_turn_result: dict[str, Any] | None = None
        self._autonomous_gate_active = True
        self._autonomous_executor_runtime_instance: Any = None
        self._agent_turn_executor_runtime_instance: Any = None
        self._spinner_text = ""
        self._tool_start_time = 0.0
        self._current_tool_name = ""
        self._last_scrollback_tool = ""
        self._command_running = False
        self._last_gateway_presence_refresh_at = 0.0
        self._gateway_presence_refresh_interval_seconds = 30.0
        initialize_autonomous_status_caches(self)

    @property
    def session_id(self) -> str:
        return self._application_runtime.state.session_id

    @property
    def conversation_history(self) -> list[dict[str, Any]]:
        return self._application_runtime.state.conversation_history

    @conversation_history.setter
    def conversation_history(self, history: Sequence[Mapping[str, Any]]) -> None:
        self._application_runtime.replace_history(history)

    @property
    def _pending_input(self) -> queue.Queue[Any]:
        return self._application_runtime.state.pending_input_queue

    @property
    def _agent_running(self) -> bool:
        return self._application_runtime.state.agent_running

    @_agent_running.setter
    def _agent_running(self, value: bool) -> None:
        self._application_runtime.set_agent_running(value)

    def _execute_pending_input(self, pending: Any, *, app: Any = None) -> bool:
        del app
        payload = pending if isinstance(pending, tuple) else (pending, None)
        runtime = self._turn_scheduler_runtime
        if not runtime.scheduler.snapshot().autonomous_gate:
            # Pending work admitted before /auto-q belongs to the stopped
            # generation and must not reopen the autonomous lane.
            return False
        return runtime.submit_autonomous(self, payload)

    def _turn_tool_policy(self, payload: Any, lane: TurnLane) -> dict[str, Any]:
        policy: dict[str, Any] = {"agent_role": lane.value}
        if lane is not TurnLane.SUPERVISOR_TASK:
            return policy
        message = payload[0] if isinstance(payload, tuple) and payload else payload
        run_id = autonomous_task_run_id_for_message(
            self._current_autonomous_task,
            message,
        )
        if run_id:
            policy["autonomous_task_run_id"] = run_id
            toolsets = autonomous_task_toolsets(self._current_autonomous_task)
            if toolsets is not None:
                policy["enabled_toolsets"] = tuple(toolsets)
        return policy

    def _execute_agent_turn_request(
        self,
        request: TurnRequest,
        cancellation: CancellationToken,
    ) -> Any:
        return self._execute_turn(self, request, cancellation)

    def _cancel_agent_for_scheduler(self) -> None:
        if self.agent is None:
            return
        try:
            self.agent.interrupt(None)
        except Exception:
            pass

    def _autonomous_panel_event_ports(self) -> AutonomousPanelEventPorts:
        return self._panel_event_ports()

    def snapshot(self) -> AutonomousExecutionSnapshot:
        return AutonomousExecutionSnapshot(
            session_id=self.session_id,
            current_task=(
                dict(self._current_autonomous_task)
                if isinstance(self._current_autonomous_task, Mapping)
                else None
            ),
            current_task_started_at=float(self._current_autonomous_task_started_at),
            agent_running=self._agent_running,
            last_agent_turn_result=(
                dict(self._last_agent_turn_result)
                if isinstance(self._last_agent_turn_result, Mapping)
                else None
            ),
            pending_input_count=self._pending_input.qsize(),
            spinner_text=str(self._spinner_text or ""),
        )

    def _get_subagent_observability_snapshot(self) -> dict[str, Any]:
        agent = self.agent
        managers: list[Any] = []
        if agent is not None:
            manager_map = getattr(agent, "_subagent_display_managers", None)
            if isinstance(manager_map, dict):
                managers.extend(value for value in manager_map.values() if value is not None)
            single = getattr(agent, "_subagent_display_manager", None)
            if single is not None and single not in managers:
                managers.append(single)
        return CliSubagentObservabilityRuntime(
            CliSubagentObservabilityPorts(display_managers=lambda: managers)
        ).snapshot()

    def _should_emit_scrollback_output(self) -> bool:
        return False

    def _invalidate(self, min_interval: float = 0.0) -> None:
        del min_interval
        self._invalidate_callback()

    def _handle_application_event(self, event: Any) -> None:
        if isinstance(event, ToolEvent):
            self._tool_event_callback(self, event)
        elif isinstance(event, MessageDelta):
            return


__all__ = ["AutonomousExecutionHost", "AutonomousExecutionSnapshot"]
