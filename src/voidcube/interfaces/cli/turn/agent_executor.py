"""Execute one admitted CLI Agent turn through explicit, UI-free ports."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ....domain.contracts.scheduler import TurnLane, TurnRequest
from ....application.single_turn_executor import SingleTurnExecutor, SingleTurnExecutorPorts
from ....domain.contracts.turn import TurnOutcome
from ....application.scheduling.turn_scheduler import CancellationToken
from .agent_call import (
    CliAgentTurnCallPorts,
    CliAgentTurnCallRuntime,
)
from .input_preparation import (
    CliTurnInputPreparationPorts,
    CliTurnInputPreparationRuntime,
)
from .execution import (
    TurnExecutionPorts,
    TurnExecutionRuntime,
)
from .postprocessing import (
    TurnPostprocessingPorts,
    TurnPostprocessingResult,
    TurnPostprocessingRuntime,
)
from .result_application import (
    AppliedTurnResult,
    TurnResultApplicationPorts,
    TurnResultApplicationRuntime,
)


@dataclass(frozen=True, slots=True)
class CliAgentTurnResult:
    """Core result returned before any terminal presentation."""

    outcome: TurnOutcome
    response: str
    turn_result: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CliAgentTurnExecutorPorts:
    """Host capabilities required to execute one turn without owning UI state."""

    ensure_credentials: Callable[[], bool]
    agent_exists: Callable[[], bool]
    clear_agent: Callable[[], None]
    active_route_signature: Callable[[], str | None]
    resolve_route: Callable[[Any], Mapping[str, Any]]
    initialize_agent: Callable[[Mapping[str, Any], Sequence[str] | None], bool]
    prepare_input_ports: Callable[
        [Any, Sequence[Any] | None],
        CliTurnInputPreparationPorts,
    ]
    record_user_message: Callable[[Any], None]
    notify_turn_started: Callable[[], None]
    set_agent_running: Callable[[bool], None]
    active_role: Callable[[], str]
    set_active_role: Callable[[str], None]
    begin_stream: Callable[[], None]
    voice_prefix: Callable[[Any], str]
    agent_call_ports: Callable[
        [Any, Sequence[Mapping[str, Any]], str, Sequence[Mapping[str, Any]]],
        CliAgentTurnCallPorts,
    ]
    execution_ports: Callable[[], TurnExecutionPorts]
    result_ports: Callable[[], TurnResultApplicationPorts]
    postprocessing_ports: Callable[[], TurnPostprocessingPorts]
    synchronize_session_identity: Callable[[], None]
    finish_turn: Callable[[AppliedTurnResult], None]
    handle_error: Callable[[Exception], None]
    finish_failed_turn: Callable[[Exception, bool], None] | None = None


class CliAgentTurnExecutorRuntime:
    """Own the complete non-presentation lifecycle of one admitted turn."""

    def __init__(self, ports: CliAgentTurnExecutorPorts) -> None:
        self.ports = ports

    def execute(
        self,
        request: TurnRequest,
        cancellation: CancellationToken,
    ) -> CliAgentTurnResult | str | None:
        if cancellation.cancelled:
            return None

        message, images = self._payload(request.prompt)
        toolsets = self._toolsets(request.tool_policy)
        ports = self.ports
        ports.set_agent_running(True)
        clear_temporary_agent = toolsets is not None
        previous_role = ports.active_role()
        turn_started = False
        finalized = False
        failure_handled = False

        try:
            if not ports.ensure_credentials() or cancellation.cancelled:
                return None

            if clear_temporary_agent and ports.agent_exists():
                ports.clear_agent()

            route = ports.resolve_route(message)
            if route.get("signature") != ports.active_route_signature():
                ports.clear_agent()
            if cancellation.cancelled:
                return None
            if not ports.initialize_agent(route, toolsets):
                return None

            prepared = CliTurnInputPreparationRuntime(
                ports.prepare_input_ports(message, images)
            ).prepare()
            if prepared.blocked_response is not None:
                return prepared.blocked_response
            if prepared.turn_input is None or cancellation.cancelled:
                return None

            turn_started = True
            message = prepared.message
            ports.record_user_message(message)
            ports.notify_turn_started()
            ports.set_active_role(request.lane.value)
            ports.begin_stream()

            def run_agent() -> Mapping[str, Any] | None:
                return CliAgentTurnCallRuntime(
                    ports.agent_call_ports(
                        message,
                        prepared.attachments,
                        ports.voice_prefix(message),
                        prepared.turn_input.prior_history,
                    )
                ).run()

            execution = TurnExecutionRuntime(
                ports.execution_ports()
            ).execute(run_agent)
            ports.synchronize_session_identity()
            if cancellation.cancelled:
                return None

            def finish_turn(value: AppliedTurnResult, _postprocessed: Any) -> None:
                nonlocal finalized
                ports.finish_turn(value)
                finalized = True

            applied, postprocessed = SingleTurnExecutor(
                SingleTurnExecutorPorts(
                    execute=lambda: execution,
                    apply_result=lambda value: TurnResultApplicationRuntime(
                        ports.result_ports()
                    ).apply(
                        value.result,
                    ),
                    postprocess=lambda value: TurnPostprocessingRuntime(
                        ports.postprocessing_ports()
                    ).process(
                        outcome=value.outcome,
                        message=message,
                        conversation_history=value.outcome.conversation_history,
                        turn_result=value.turn_result,
                    ),
                    finish=finish_turn,
                    finalize=lambda value, processed: (value, processed),
                )
            ).execute()
            return self._result(applied, postprocessed)
        except Exception as error:
            try:
                ports.synchronize_session_identity()
                ports.handle_error(error)
            finally:
                if ports.finish_failed_turn is not None:
                    try:
                        ports.finish_failed_turn(error, cancellation.cancelled)
                        failure_handled = True
                    except Exception:
                        pass
            raise
        finally:
            if (
                turn_started
                and not finalized
                and not failure_handled
                and ports.finish_failed_turn is not None
            ):
                try:
                    ports.finish_failed_turn(
                        RuntimeError("turn cancelled before completion"),
                        cancellation.cancelled,
                    )
                except Exception:
                    pass
            if clear_temporary_agent:
                ports.clear_agent()
            ports.set_active_role(previous_role)
            ports.set_agent_running(False)

    @staticmethod
    def _payload(payload: Any) -> tuple[Any, Sequence[Any] | None]:
        if isinstance(payload, tuple) and len(payload) == 2:
            return payload[0], payload[1]
        return payload, None

    @staticmethod
    def _toolsets(tool_policy: Mapping[str, Any]) -> list[str] | None:
        if "enabled_toolsets" not in tool_policy:
            return None
        return [
            str(value).strip()
            for value in list(tool_policy.get("enabled_toolsets") or ())
            if str(value).strip()
        ]

    @staticmethod
    def _result(
        applied: AppliedTurnResult,
        postprocessed: TurnPostprocessingResult,
    ) -> CliAgentTurnResult:
        return CliAgentTurnResult(
            outcome=applied.outcome,
            response=postprocessed.response,
            turn_result=postprocessed.turn_result,
        )


__all__ = [
    "CliAgentTurnExecutorPorts",
    "CliAgentTurnExecutorRuntime",
    "CliAgentTurnResult",
]
