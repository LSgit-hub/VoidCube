from __future__ import annotations

import pytest

from voidcube.infrastructure.gateway.agent_adapter import GatewayAgentAdapter


@pytest.mark.asyncio
async def test_gateway_agent_adapter_encodes_session_id_path_segment():
    adapter = GatewayAgentAdapter("http://gateway.test", session_id="session/a?b#c")
    captured: list[str] = []

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def json(self):
            return {"ok": True}

        async def text(self):
            return ""

    class Session:
        closed = False

        def get(self, url):
            captured.append(url)
            return Response()

        def delete(self, url):
            captured.append(url)
            return Response()

        async def close(self):
            return None

    adapter._client_session = Session()

    await adapter.get_session_info()
    await adapter.delete_session()

    assert captured == [
        "http://gateway.test/v1/sessions/session%2Fa%3Fb%23c",
        "http://gateway.test/v1/sessions/session%2Fa%3Fb%23c",
    ]


@pytest.mark.asyncio
async def test_gateway_agent_adapter_rejects_non_object_response():
    adapter = GatewayAgentAdapter("http://gateway.test")

    class Response:
        status = 200

        async def json(self):
            return []

        async def text(self):
            return ""

    class Session:
        closed = False

        def get(self, _url):
            class Context:
                async def __aenter__(self):
                    return Response()

                async def __aexit__(self, *_args):
                    return False

            return Context()

    adapter._client_session = Session()
    with pytest.raises(RuntimeError, match="non-object"):
        await adapter.get_session_info()


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), "invalid"])
def test_gateway_agent_adapter_rejects_invalid_timeout(timeout):
    with pytest.raises(ValueError, match="finite positive number"):
        GatewayAgentAdapter("http://gateway.test", timeout_seconds=timeout)


@pytest.mark.parametrize("url", ["file:///tmp/gateway", "ftp://gateway.test", "gateway.test", "http://gateway.test?bad=1", "http://gateway.test/#bad"])
def test_gateway_agent_adapter_rejects_non_http_url(url):
    with pytest.raises(ValueError, match="must use http or https"):
        GatewayAgentAdapter(url)
