from types import SimpleNamespace

from voidcube.domain.contracts.execution import ExecutionState
from voidcube.runtime.agent.tool_execution import ToolExecutionCoordinator
from voidcube.runtime.agent.tool_loop_service import ToolLoopService


def _call(name: str, call_id: str):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments='{"value": 1}'),
    )


def test_tool_loop_service_normalizes_calls_and_forwards_parallel_context():
    seen = []
    completed = []
    service = ToolLoopService(
        coordinator_factory=ToolExecutionCoordinator,
        invoke=lambda call, parallel: seen.append((call.name, parallel)) or "ok",
        is_interrupted=lambda: False,
        classify_failure=lambda _name, _content: (False, ""),
        max_workers=2,
    )

    outcomes = service.execute(
        [_call("read_file", "c1")],
        before_call=lambda call, parallel: completed.append(("before", call.call_id, parallel)),
        after_call=lambda outcome, parallel: completed.append(("after", outcome.state, parallel)),
    )

    assert seen == [("read_file", False)]
    assert outcomes[0].state is ExecutionState.SUCCEEDED
    assert completed[0] == ("before", "c1", False)
    assert completed[1] == ("after", ExecutionState.SUCCEEDED, False)


def test_tool_loop_service_does_not_invoke_empty_batches():
    service = ToolLoopService(
        coordinator_factory=ToolExecutionCoordinator,
        invoke=lambda *_args: (_ for _ in ()).throw(AssertionError("called")),
        is_interrupted=lambda: False,
        classify_failure=lambda *_args: (False, ""),
        max_workers=1,
    )
    assert service.execute([]) == ()
