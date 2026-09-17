from types import SimpleNamespace

from voidcube.infrastructure.llm.request import ChatRequestConfig
from voidcube.runtime.agent.model_call_service import ModelCallService


class _Transport:
    def __init__(self):
        self.calls = []

    def stream(self, request, *, on_update, on_first_delta=None):
        self.calls.append(request)
        return SimpleNamespace(choices=[])


def test_model_call_service_builds_request_and_reports_duration():
    transport = _Transport()
    seen = []
    result = ModelCallService(clock=iter([10.0, 10.25]).__next__).call(
        config=ChatRequestConfig(model="demo"),
        messages=[{"role": "user", "content": "hello"}],
        transport=transport,
        on_update=lambda update: None,
        before_send=lambda request: seen.append(request),
    )
    assert result.response.choices == []
    assert result.duration_seconds == 0.25
    assert transport.calls == [result.request_kwargs]
    assert seen == [result.request_kwargs]
