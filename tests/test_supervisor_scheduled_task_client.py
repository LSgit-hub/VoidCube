from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from voidcube.infrastructure.gateway.scheduled_tasks import SupervisorScheduledTaskClient
from voidcube.application.scheduling.scheduled_executor import ScheduledRequestRejected


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_scheduled_task_client_rejects_non_object_response():
    response = _Response([])
    with patch("voidcube.infrastructure.gateway.scheduled_tasks.urllib.request.urlopen", return_value=response):
        with pytest.raises(ScheduledRequestRejected, match="non-object"):
            SupervisorScheduledTaskClient(base_url="http://gateway.test").get("/scheduled-tasks")


def test_scheduled_task_client_rejects_malformed_json_response():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"not-json"

    with patch(
        "voidcube.infrastructure.gateway.scheduled_tasks.urllib.request.urlopen",
        return_value=Response(),
    ):
        with pytest.raises(ScheduledRequestRejected, match="invalid JSON"):
            SupervisorScheduledTaskClient(base_url="http://gateway.test").get("/scheduled-tasks")


def test_scheduled_task_client_normalizes_transport_failure():
    with patch(
        "voidcube.infrastructure.gateway.scheduled_tasks.urllib.request.urlopen",
        side_effect=OSError("connection refused"),
    ):
        with pytest.raises(ScheduledRequestRejected, match="unavailable") as error:
            SupervisorScheduledTaskClient(base_url="http://gateway.test").get("/scheduled-tasks")
    assert error.value.status_code == 503


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), "invalid"])
def test_scheduled_task_client_rejects_invalid_timeout(timeout):
    with pytest.raises(ValueError, match="finite positive number"):
        SupervisorScheduledTaskClient(base_url="http://gateway.test", timeout_seconds=timeout)
