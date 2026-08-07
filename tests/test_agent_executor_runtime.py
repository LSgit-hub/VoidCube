from __future__ import annotations

import pytest

from VoidCube_app.contracts.scheduler import TurnLane, TurnRequest
from VoidCube_app.turn_scheduler import CancellationToken
from VoidCube_cli.agent_executor_runtime import CliAgentExecutor, CliAgentExecutorPorts


def _request(request_id: str, lane: TurnLane) -> TurnRequest:
    return TurnRequest(
        request_id=request_id,
        lane=lane,
        session_id="session",
        prompt="payload",
    )


def test_executor_routes_lane_and_unbinds_explicitly() -> None:
    calls = []
    executor = CliAgentExecutor(
        CliAgentExecutorPorts(
            execute_user=lambda host, value, token: calls.append(
                ("user", host, value, token)
            ),
            execute_autonomous=lambda host, value, token: calls.append(
                ("auto", host, value, token)
            ),
            cancel_user=lambda host, request_id: calls.append(("cancel-user", host, request_id)),
            cancel_autonomous=lambda host, request_id: calls.append(("cancel-auto", host, request_id)),
        )
    )
    host = object()
    request = _request("r", TurnLane.USER_CHAT)
    token = CancellationToken()
    executor.bind(request, host)

    assert executor.execute(request, token) is None
    assert calls[0][0:3] == ("user", host, request)
    executor.cancel("r")
    assert calls[1] == ("cancel-user", host, "r")
    executor.unbind("r")
    with pytest.raises(RuntimeError, match="no host bound"):
        executor.execute(request, token)
