from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from VoidCube_app.contracts.scheduler import TurnLane, TurnRequest
from VoidCube_app.turn_contract import begin_turn
from VoidCube_app.turn_scheduler import CancellationToken
from VoidCube_cli.cli_agent_turn_call_runtime import CliAgentTurnCallPorts
from VoidCube_cli.cli_agent_turn_executor_runtime import (
    CliAgentTurnExecutorPorts,
    CliAgentTurnExecutorRuntime,
    CliAgentTurnResult,
)
from VoidCube_cli.cli_turn_input_preparation_runtime import (
    CliTurnInputPreparationPorts,
)
from VoidCube_cli.turn_execution_runtime import TurnExecutionPorts
from VoidCube_cli.turn_postprocessing_runtime import TurnPostprocessingPorts
from VoidCube_cli.turn_result_application_runtime import TurnResultApplicationPorts


@dataclass
class _TurnOwner:
    session_id: str
    history: list[dict[str, Any]]
    calls: list[Any] = field(default_factory=list)
    role: str = "idle"
    agent_present: bool = True
    cancel_during_credentials: CancellationToken | None = None

    def ports(self) -> CliAgentTurnExecutorPorts:
        def ensure_credentials() -> bool:
            self.calls.append("credentials")
            if self.cancel_during_credentials is not None:
                self.cancel_during_credentials.cancel()
            return True

        def initialize_agent(route, toolsets) -> bool:
            self.calls.append(("initialize", route["signature"], toolsets))
            self.agent_present = True
            return True

        def clear_agent() -> None:
            self.calls.append("clear-agent")
            self.agent_present = False

        def prepare_input(message, images) -> CliTurnInputPreparationPorts:
            self.calls.append(("prepare", message, images))
            return CliTurnInputPreparationPorts(
                message=message,
                images=images,
                conversation_history=self.history,
                preprocess_images=lambda value, _images: value,
                model="model",
                base_url="",
                api_key="key",
                cwd=lambda: ".",
                should_emit=lambda: False,
                emit=lambda _value: None,
                sanitize=lambda value: value,
                begin_turn=lambda value: begin_turn(self.history, value),
            )

        def agent_call(message, prefix, prior_history) -> CliAgentTurnCallPorts:
            self.calls.append(("agent-call-ports", message, tuple(prior_history)))

            def run_conversation(**kwargs):
                self.calls.append(
                    (
                        "run",
                        kwargs["task_id"],
                        tuple(kwargs["conversation_history"]),
                    )
                )
                return {
                    "final_response": f"answer:{message}",
                    "messages": [
                        *kwargs["conversation_history"],
                        {"role": "user", "content": message},
                        {"role": "assistant", "content": f"answer:{message}"},
                    ],
                    "failed": False,
                }

            return CliAgentTurnCallPorts(
                message=message,
                voice_prefix=prefix,
                pending_model_switch_note=lambda: None,
                clear_pending_model_switch_note=lambda: None,
                prior_history=prior_history,
                session_id=self.session_id,
                stream_callback=None,
                persist_user_message=None,
                new_trace_id=lambda: f"trace-{self.session_id}",
                set_trace_id=lambda value: self.calls.append(("trace", value)),
                run_conversation=run_conversation,
                summarize_error=str,
                log_error=lambda error: self.calls.append(("error", error)),
            )

        def execution_ports(_run_id: str) -> TurnExecutionPorts:
            return TurnExecutionPorts(
                interrupt_agent=lambda: self.calls.append("interrupt"),
                check_autonomous_timeout=lambda: (False, False),
                cleanup_async_clients=lambda: self.calls.append("cleanup-clients"),
                flush_stream=lambda: self.calls.append("flush-stream"),
                flush_output=lambda: self.calls.append("flush-output"),
                sleep=lambda seconds: self.calls.append(("sleep", seconds)),
            )

        return CliAgentTurnExecutorPorts(
            ensure_credentials=ensure_credentials,
            current_autonomous_task=lambda: None,
            set_last_agent_turn_result=lambda value: self.calls.append(
                ("last-result", value)
            ),
            agent_exists=lambda: self.agent_present,
            clear_agent=clear_agent,
            active_route_signature=lambda: "route",
            resolve_route=lambda message: self.calls.append(("route", message))
            or {"signature": "route"},
            initialize_agent=initialize_agent,
            prepare_input_ports=prepare_input,
            record_user_message=lambda message: self.calls.append(
                ("record-user", message)
            ),
            notify_turn_started=lambda: self.calls.append("turn-started"),
            set_agent_running=lambda value: self.calls.append(("running", value)),
            active_role=lambda: self.role,
            set_active_role=lambda value: (
                self.calls.append(("role", value)),
                setattr(self, "role", value),
            )[-1],
            begin_stream=lambda: self.calls.append("begin-stream"),
            voice_prefix=lambda _message: "",
            agent_call_ports=agent_call,
            execution_ports=execution_ports,
            result_ports=lambda: TurnResultApplicationPorts(
                conversation_history=lambda: self.history,
                set_conversation_history=lambda value: (
                    self.calls.append("apply-history"),
                    setattr(self, "history", value),
                )[-1],
                record_autonomous_result=lambda *_args, **_kwargs: self.calls.append(
                    "record-result"
                ),
                record_autonomous_finished=lambda *_args, **_kwargs: self.calls.append(
                    "record-finished"
                ),
            ),
            postprocessing_ports=lambda: TurnPostprocessingPorts(
                session_db=lambda: None,
                session_id=lambda: self.session_id,
                voice_continuous=lambda: False,
                stop_voice_continuous=lambda: None,
                emit=lambda _value: None,
            ),
            finish_turn=lambda _applied: self.calls.append("finish"),
            handle_error=lambda error, *_args: self.calls.append(("handled-error", error)),
        )


def _request(
    lane: TurnLane,
    message: str,
    *,
    session_id: str = "session",
    tool_policy: dict[str, Any] | None = None,
) -> TurnRequest:
    return TurnRequest(
        request_id=f"request-{lane.value}",
        lane=lane,
        session_id=session_id,
        prompt=(message, None),
        tool_policy=tool_policy or {},
    )


def test_user_turn_runs_lifecycle_in_order(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.title_generator.maybe_auto_title",
        lambda *_args: None,
    )
    owner = _TurnOwner("user-session", [{"role": "system", "content": "user"}])

    result = CliAgentTurnExecutorRuntime(owner.ports()).execute(
        _request(TurnLane.USER_CHAT, "hello", session_id=owner.session_id),
        CancellationToken(),
    )

    assert isinstance(result, CliAgentTurnResult)
    assert result.response == "answer:hello"
    names = [value[0] if isinstance(value, tuple) else value for value in owner.calls]
    assert names.index("credentials") < names.index("initialize")
    assert names.index("initialize") < names.index("prepare")
    assert names.index("record-user") < names.index("turn-started")
    assert names.index("begin-stream") < names.index("run")
    assert names.index("run") < names.index("apply-history")
    assert names.index("apply-history") < names.index("finish")
    assert owner.calls[-2:] == [("role", "idle"), ("running", False)]
    assert ("role", TurnLane.USER_CHAT.value) in owner.calls


def test_autonomous_tool_policy_uses_temporary_agent_and_cleans_it(monkeypatch) -> None:
    monkeypatch.setattr("agent.title_generator.maybe_auto_title", lambda *_args: None)
    owner = _TurnOwner("auto-session", [])
    policy = {
        "autonomous_task_run_id": "run-1",
        "enabled_toolsets": ("research", "shell"),
    }

    result = CliAgentTurnExecutorRuntime(owner.ports()).execute(
        _request(
            TurnLane.SUPERVISOR_TASK,
            "inspect",
            session_id=owner.session_id,
            tool_policy=policy,
        ),
        CancellationToken(),
    )

    assert isinstance(result, CliAgentTurnResult)
    assert ("initialize", "route", ["research", "shell"]) in owner.calls
    assert ("role", TurnLane.SUPERVISOR_TASK.value) in owner.calls
    assert owner.calls.count("clear-agent") == 2
    assert owner.agent_present is False


def test_cancellation_before_initialization_stops_the_turn() -> None:
    token = CancellationToken()
    owner = _TurnOwner("cancelled-session", [], cancel_during_credentials=token)

    result = CliAgentTurnExecutorRuntime(owner.ports()).execute(
        _request(TurnLane.USER_CHAT, "cancel me"),
        token,
    )

    assert result is None
    assert "credentials" in owner.calls
    assert not any(
        isinstance(value, tuple) and value[0] == "initialize" for value in owner.calls
    )
    assert owner.calls[-2:] == [("role", "idle"), ("running", False)]


def test_user_and_autonomous_turns_keep_history_and_session_isolated(monkeypatch) -> None:
    monkeypatch.setattr("agent.title_generator.maybe_auto_title", lambda *_args: None)
    user = _TurnOwner("user-session", [{"role": "system", "content": "user"}])
    autonomous = _TurnOwner(
        "auto-session",
        [{"role": "system", "content": "autonomous"}],
    )

    CliAgentTurnExecutorRuntime(user.ports()).execute(
        _request(TurnLane.USER_CHAT, "user question", session_id=user.session_id),
        CancellationToken(),
    )
    CliAgentTurnExecutorRuntime(autonomous.ports()).execute(
        _request(
            TurnLane.SUPERVISOR_TASK,
            "auto task",
            session_id=autonomous.session_id,
        ),
        CancellationToken(),
    )

    user_run = next(value for value in user.calls if isinstance(value, tuple) and value[0] == "run")
    auto_run = next(
        value for value in autonomous.calls if isinstance(value, tuple) and value[0] == "run"
    )
    assert user_run[1:] == (
        "user-session",
        ({"role": "system", "content": "user"},),
    )
    assert auto_run[1:] == (
        "auto-session",
        ({"role": "system", "content": "autonomous"},),
    )
    assert all(message.get("content") != "auto task" for message in user.history)
    assert all(message.get("content") != "user question" for message in autonomous.history)


def test_executor_module_has_no_app_or_prompt_toolkit_imports() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "VoidCube_cli"
        / "cli_agent_turn_executor_runtime.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    assert "VoidCube_cli.app" not in imported
    assert not any(name == "prompt_toolkit" or name.startswith("prompt_toolkit.") for name in imported)
