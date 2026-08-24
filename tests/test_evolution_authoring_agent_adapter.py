from __future__ import annotations

import threading
from collections.abc import Mapping

import pytest

from voidcube.systems.evolution_authoring import (
    AUTHORING_TOOL_NAMES,
    AIAgentAuthoringAdapter,
    EvolutionAuthoringContext,
)
from voidcube.systems.evolution_evaluation import (
    ExecutionEnvironmentManifest,
    RuntimeToolIdentity,
    WorkspacePathMapping,
)
from voidcube.infrastructure.execution.task_execution import (
    TaskExecutionContract,
    begin_task_execution,
    clear_task_execution_state,
    configure_task_execution,
    mark_task_execution_ready,
    release_task_execution,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]
TASK_ID = "evolution-candidate-0123456789abcdef-0001"
WORKSPACE = "F:/voidcube-authoring/worktree"


@pytest.fixture(autouse=True)
def _clear_execution_scope():
    clear_task_execution_state(TASK_ID)
    yield
    clear_task_execution_state(TASK_ID)


def _manifest() -> ExecutionEnvironmentManifest:
    return ExecutionEnvironmentManifest.create(
        backend="local",
        validation_scope="host",
        host_os="Windows test",
        execution_os="Windows test",
        architecture="AMD64",
        host_workspace_path=WORKSPACE,
        execution_workspace_path=WORKSPACE,
        path_mappings=(
            WorkspacePathMapping(
                host_path=WORKSPACE,
                execution_path=WORKSPACE,
            ),
        ),
        tools=tuple(
            RuntimeToolIdentity(
                scope="host",
                name=name,
                available=True,
                executable=f"F:/voidcube/.venv/Scripts/{name}.exe",
                version=f"{name} test",
            )
            for name in ("git", "python", "pytest", "node", "npm")
        ),
        repository_head="a" * 40,
        dependency_fingerprint="b" * 64,
        validated_platforms=("windows",),
    )


def _context() -> EvolutionAuthoringContext:
    return EvolutionAuthoringContext(
        task_id=TASK_ID,
        objective="Improve stream output correctness.",
        improvement_hypothesis="A focused handler change prevents truncated output.",
        baseline_commit="a" * 40,
        execution_workspace_path=WORKSPACE,
        allowed_paths=("agent/stream_handler.py",),
        forbidden_patterns=("**/credential*",),
        max_files_changed=1,
        stop_conditions=(
            "Do not edit outside allowed_paths.",
            "Do not create commits.",
        ),
        environment_manifest=_manifest(),
    )


def _ready_scope() -> None:
    configure_task_execution(
        TaskExecutionContract(
            task_id=TASK_ID,
            backend="local",
            validation_scope="host",
            host_workspace_path=WORKSPACE,
            execution_workspace_path=WORKSPACE,
            allowed_execution_paths=(WORKSPACE,),
            command_timeout_seconds=300,
            max_output_chars=50_000,
            required_tools=("git", "python", "pytest"),
            required_platforms=("windows",),
            lifecycle_owner="executor",
        )
    )
    begin_task_execution(TASK_ID)
    mark_task_execution_ready(TASK_ID, active_backend="local")


def test_project_toolsets_resolve_only_authoring_tools():
    from voidcube.extensions.tools.model_tools import get_tool_definitions

    definitions = get_tool_definitions(
        enabled_toolsets=["file", "terminal"],
        quiet_mode=True,
    )
    tool_names = {
        str(item.get("function", {}).get("name") or "") for item in definitions
    }

    assert tool_names == AUTHORING_TOOL_NAMES


def test_provider_override_requires_an_explicit_matching_model():
    with pytest.raises(ValueError, match="model is required"):
        AIAgentAuthoringAdapter(requested_provider="another-provider")


class _Agent:
    valid_tool_names = {
        "read_file",
        "write_file",
        "patch",
        "search_files",
        "terminal",
        "process",
    }

    def __init__(
        self,
        result: Mapping[str, object] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.result = result or {
            "completed": True,
            "interrupted": False,
            "final_response": "Updated agent/stream_handler.py; verification remains external.",
        }
        self.error = error
        self.calls: list[tuple[str, dict[str, object], int]] = []

    def run_conversation(self, user_message: str, **kwargs: object):
        self.calls.append((user_message, dict(kwargs), threading.get_ident()))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_adapter_constructs_ephemeral_file_terminal_agent_and_runs_off_loop():
    _ready_scope()
    captured_kwargs: dict[str, object] = {}
    runtime_calls: list[str | None] = []
    agent = _Agent()
    event_loop_thread = threading.get_ident()

    def factory(**kwargs: object):
        captured_kwargs.update(kwargs)
        return agent

    adapter = AIAgentAuthoringAdapter(
        max_iterations=24,
        runtime_provider_resolver=lambda *, requested: (
            runtime_calls.append(requested)
            or {
                "provider": "test-provider",
                "base_url": "https://example.invalid/v1",
                "api_key": "test-key",
            }
        ),
        model_resolver=lambda: "test-model",
        ai_agent_factory=factory,
    )

    report = await adapter.author(_context())

    assert report.completed is True
    assert runtime_calls == [None]
    assert captured_kwargs["enabled_toolsets"] == ["file", "terminal"]
    assert captured_kwargs["persist_session"] is False
    assert captured_kwargs["skip_memory"] is True
    assert captured_kwargs["skip_context_files"] is True
    assert captured_kwargs["session_db"] is None
    assert captured_kwargs["clarification_sink"] is None
    assert captured_kwargs["checkpoints_enabled"] is False
    assert captured_kwargs["model"] == "test-model"
    assert captured_kwargs["provider"] == "test-provider"
    assert len(agent.calls) == 1
    user_message, call_kwargs, execution_thread = agent.calls[0]
    assert execution_thread != event_loop_thread
    assert call_kwargs["task_id"] == TASK_ID
    assert call_kwargs["conversation_history"] == []
    assert "untrusted task data" in str(call_kwargs["system_message"])
    assert '"allowed_paths": [' in user_message
    assert '"agent/stream_handler.py"' in user_message
    assert '"validation_scope": "host"' in user_message


@pytest.mark.asyncio
async def test_adapter_rejects_tools_outside_authoring_boundary():
    _ready_scope()
    agent = _Agent()
    agent.valid_tool_names = {*agent.valid_tool_names, "web_search"}
    adapter = AIAgentAuthoringAdapter(
        runtime_provider_resolver=lambda **_kwargs: {},
        model_resolver=lambda: "test-model",
        ai_agent_factory=lambda **_kwargs: agent,
    )

    report = await adapter.author(_context())

    assert report.completed is False
    assert "RuntimeError" in report.summary
    assert agent.calls == []


@pytest.mark.asyncio
async def test_adapter_reports_interrupted_conversation_as_incomplete():
    _ready_scope()
    agent = _Agent(
        {
            "completed": False,
            "interrupted": True,
            "final_response": "Stopped by lease cancellation.",
        }
    )
    adapter = AIAgentAuthoringAdapter(
        runtime_provider_resolver=lambda **_kwargs: {},
        model_resolver=lambda: "test-model",
        ai_agent_factory=lambda **_kwargs: agent,
    )

    report = await adapter.author(_context())

    assert report.completed is False
    assert report.summary == "Stopped by lease cancellation."


@pytest.mark.asyncio
async def test_adapter_does_not_leak_conversation_exception_details():
    _ready_scope()
    agent = _Agent(error=RuntimeError("credential=secret-value"))
    adapter = AIAgentAuthoringAdapter(
        runtime_provider_resolver=lambda **_kwargs: {},
        model_resolver=lambda: "test-model",
        ai_agent_factory=lambda **_kwargs: agent,
    )

    report = await adapter.author(_context())

    assert report.completed is False
    assert "RuntimeError" in report.summary
    assert "secret-value" not in report.summary


@pytest.mark.asyncio
async def test_adapter_detects_scope_released_during_conversation():
    _ready_scope()

    class ReleasingAgent(_Agent):
        def run_conversation(self, user_message: str, **kwargs: object):
            result = super().run_conversation(user_message, **kwargs)
            release_task_execution(TASK_ID)
            return result

    agent = ReleasingAgent()
    adapter = AIAgentAuthoringAdapter(
        runtime_provider_resolver=lambda **_kwargs: {},
        model_resolver=lambda: "test-model",
        ai_agent_factory=lambda **_kwargs: agent,
    )

    report = await adapter.author(_context())

    assert report.completed is False
    assert "RuntimeError" in report.summary


@pytest.mark.asyncio
async def test_adapter_requires_executor_owned_ready_scope():
    adapter = AIAgentAuthoringAdapter(
        runtime_provider_resolver=lambda **_kwargs: pytest.fail(
            "runtime resolution should not run without a ready scope"
        ),
        model_resolver=lambda: "test-model",
        ai_agent_factory=lambda **_kwargs: pytest.fail(
            "agent construction should not run without a ready scope"
        ),
    )

    report = await adapter.author(_context())

    assert report.completed is False
    assert "RuntimeError" in report.summary
