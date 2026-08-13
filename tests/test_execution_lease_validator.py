from __future__ import annotations

from io import BytesIO
import json
import urllib.error

import pytest

from VoidCube_cli.execution_lease_validator import (
    StaleExecutionLeaseError,
    validate_execution_lease,
)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def test_cli_execution_lease_validator_posts_fencing_identity(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured.update(
            url=request.full_url,
            payload=json.loads(request.data),
            timeout=timeout,
        )
        return _Response({"valid": True, "task": {"task_id": "task-1"}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = validate_execution_lease(
        task_id="task-1",
        generation=4,
        attempt_id="attempt-4",
        owner_session_id="owner-4",
        gateway_base="http://gateway.test",
    )

    assert result["valid"] is True
    assert captured["url"] == "http://gateway.test/v1/tasks/task-1/lease/validate"
    assert captured["payload"] == {
        "generation": 4,
        "attempt_id": "attempt-4",
        "owner_session_id": "owner-4",
    }


def test_cli_execution_lease_validator_preserves_confirmed_stale_code(monkeypatch):
    body = BytesIO(
        json.dumps(
            {
                "detail": {
                    "code": "stale_execution_lease",
                    "message": "owner changed",
                }
            }
        ).encode("utf-8")
    )

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 409, "Conflict", {}, body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(StaleExecutionLeaseError, match="owner changed") as exc:
        validate_execution_lease(
            task_id="task-1",
            generation=1,
            attempt_id="old-attempt",
            owner_session_id="old-owner",
        )

    assert exc.value.code == "stale_execution_lease"


def test_cli_execution_lease_validator_network_failure_is_not_stale(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("gateway unavailable")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(urllib.error.URLError) as exc:
        validate_execution_lease(
            task_id="task-1",
            generation=1,
            attempt_id="attempt-1",
            owner_session_id="owner-1",
        )

    assert getattr(exc.value, "code", None) != "stale_execution_lease"
