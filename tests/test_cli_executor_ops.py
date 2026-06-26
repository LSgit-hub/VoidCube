from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import Mock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from VoidCube_cli.ops.executor import ExecutorOpsClient


@pytest.mark.unit
def test_executor_ops_client_routes_body_upgrade_through_gateway_executor():
    response = Mock()
    response.json.return_value = {"status": "upgrade_executed"}
    response.raise_for_status.return_value = None

    with patch("VoidCube_cli.ops.executor.requests.post", return_value=response) as post:
        client = ExecutorOpsClient(gateway_url="http://gateway.local/")
        result = client.execute_body_upgrade({"slot_id": "slot-B"})

    assert result["status"] == "upgrade_executed"
    post.assert_called_once_with(
        "http://gateway.local/api/executor/body/upgrade/execute",
        json={"slot_id": "slot-B"},
        timeout=30.0,
    )


@pytest.mark.unit
def test_executor_ops_client_routes_body_status_queries_through_gateway_executor():
    response = Mock()
    response.json.return_value = {"registry": {"active_slot": "slot-A"}}
    response.raise_for_status.return_value = None

    with patch("VoidCube_cli.ops.executor.requests.get", return_value=response) as get:
        client = ExecutorOpsClient(gateway_url="http://gateway.local/")
        result = client.get_body_registry()

    assert result["registry"]["active_slot"] == "slot-A"
    get.assert_called_once_with(
        "http://gateway.local/api/executor/body/registry",
        timeout=30.0,
    )


@pytest.mark.unit
def test_executor_ops_client_does_not_fallback_on_executor_validation_error():
    validation_error = requests.HTTPError("bad request")
    validation_error.response = Mock(status_code=400)

    with patch(
        "VoidCube_cli.ops.executor.requests.post",
        side_effect=validation_error,
    ) as post:
        client = ExecutorOpsClient(gateway_url="http://gateway.local")
        with pytest.raises(requests.HTTPError):
            client.execute_body_upgrade({})

    assert post.call_count == 1
    assert post.call_args_list[0].args[0] == "http://gateway.local/api/executor/body/upgrade/execute"


@pytest.mark.unit
def test_executor_ops_client_fails_closed_when_executor_route_is_unavailable():
    with patch(
        "VoidCube_cli.ops.executor.requests.post",
        side_effect=requests.ConnectionError("executor route unavailable"),
    ) as post:
        client = ExecutorOpsClient(gateway_url="http://gateway.local")
        with pytest.raises(requests.ConnectionError):
            client.execute_body_upgrade({})

    assert post.call_count == 1
    assert post.call_args_list[0].args[0] == "http://gateway.local/api/executor/body/upgrade/execute"
