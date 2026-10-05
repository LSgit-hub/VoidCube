import json
from types import SimpleNamespace

import pytest

from voidcube.infrastructure.gateway.supervisor_voice import (
    SupervisorVoiceClient,
    SupervisorVoiceClientError,
)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_supervisor_voice_client_routes_session_through_gateway(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(SimpleNamespace(request=request, timeout=timeout))
        return _Response({"status": "complete"})

    monkeypatch.setattr(
        "voidcube.infrastructure.gateway.supervisor_voice.urllib.request.urlopen",
        fake_urlopen,
    )

    result = SupervisorVoiceClient(
        base_url="http://gateway.example:6000",
        timeout_seconds=12,
    ).start_session(session_id="session-1")

    assert result == {"status": "complete"}
    assert requests[0].request.full_url == (
        "http://gateway.example:6000/api/supervisor/voice/session/start"
    )
    assert requests[0].request.get_method() == "POST"
    assert requests[0].timeout == 12
    assert json.loads(requests[0].request.data.decode("utf-8")) == {
        "session_id": "session-1"
    }


def test_supervisor_voice_client_gets_status_through_gateway(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(SimpleNamespace(request=request, timeout=timeout))
        return _Response({"enabled": True})

    monkeypatch.setattr(
        "voidcube.infrastructure.gateway.supervisor_voice.urllib.request.urlopen",
        fake_urlopen,
    )

    result = SupervisorVoiceClient(base_url="http://gateway.example:6000").status()

    assert result == {"enabled": True}
    assert requests[0].request.full_url == (
        "http://gateway.example:6000/api/supervisor/voice/status"
    )
    assert requests[0].request.get_method() == "GET"


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), "invalid"])
def test_supervisor_voice_client_rejects_invalid_timeout(timeout):
    with pytest.raises(ValueError, match="finite positive number"):
        SupervisorVoiceClient(base_url="http://gateway.example:6000", timeout_seconds=timeout)


@pytest.mark.parametrize("url", ["file:///tmp/gateway", "ftp://gateway.example", "http://gateway.example?bad=1"])
def test_supervisor_voice_client_rejects_invalid_gateway_url(url):
    with pytest.raises(ValueError, match="must use http or https"):
        SupervisorVoiceClient(base_url=url)


def test_supervisor_voice_client_rejects_non_object_response(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"[]"

    monkeypatch.setattr(
        "voidcube.infrastructure.gateway.supervisor_voice.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    with pytest.raises(SupervisorVoiceClientError, match="non-object"):
        SupervisorVoiceClient(base_url="http://gateway.example:6000").status()
