from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.stream_response import StreamingResponseAssembler
from run_agent import AIAgent


pytestmark = [pytest.mark.smoke, pytest.mark.unit]


def _tool_delta(
    *,
    index=0,
    call_id=None,
    name=None,
    arguments=None,
    extra_content=None,
    model_extra=None,
):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
        extra_content=extra_content,
        model_extra=model_extra,
    )


def _chunk(
    *,
    content=None,
    reasoning=None,
    tool_calls=None,
    finish_reason=None,
    model="safe-model",
    usage=None,
    choices=True,
):
    choice_values = []
    if choices:
        choice_values.append(
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=content,
                    reasoning_content=reasoning,
                    reasoning=None,
                    tool_calls=tool_calls,
                ),
                finish_reason=finish_reason,
            )
        )
    return SimpleNamespace(model=model, usage=usage, choices=choice_values)


def test_assembles_content_reasoning_model_usage_and_finish_reason():
    assembler = StreamingResponseAssembler()

    first = assembler.add(_chunk(reasoning="plan ", content="hello "))
    second = assembler.add(_chunk(content="world", finish_reason="stop"))
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2)
    final = assembler.add(_chunk(choices=False, usage=usage))
    response = assembler.build_response(response_id="stream-test")

    assert first.reasoning == "plan "
    assert first.content == "hello "
    assert first.stream_content is True
    assert first.starts_delivery is True
    assert second.stream_content is True
    assert final.starts_delivery is False
    assert response.id == "stream-test"
    assert response.model == "safe-model"
    assert response.usage is usage
    assert response.choices[0].message.content == "hello world"
    assert response.choices[0].message.reasoning_content == "plan "
    assert response.choices[0].finish_reason == "stop"


def test_tool_call_fragments_are_assembled_and_later_content_is_not_streamed():
    assembler = StreamingResponseAssembler()

    started = assembler.add(
        _chunk(
            tool_calls=[
                _tool_delta(
                    call_id="call-1",
                    name="read_file",
                    arguments='{"path":',
                    model_extra={"extra_content": {"trace": "kept"}},
                )
            ]
        )
    )
    continued = assembler.add(
        _chunk(
            content="tool commentary",
            tool_calls=[_tool_delta(arguments='"README.md"}')],
            finish_reason="tool_calls",
        )
    )
    response = assembler.build_response(response_id="stream-tool")

    assert started.started_tools == ("read_file",)
    assert started.starts_delivery is True
    assert continued.content == "tool commentary"
    assert continued.stream_content is False
    assert continued.started_tools == ()
    tool_call = response.choices[0].message.tool_calls[0]
    assert tool_call.id == "call-1"
    assert tool_call.function.name == "read_file"
    assert tool_call.function.arguments == '{"path":"README.md"}'
    assert tool_call.extra_content == {"trace": "kept"}
    assert response.choices[0].finish_reason == "tool_calls"


def test_reused_raw_tool_index_with_new_id_creates_a_new_call():
    assembler = StreamingResponseAssembler()

    assembler.add(
        _chunk(
            tool_calls=[
                _tool_delta(
                    index=0,
                    call_id="call-1",
                    name="first",
                    arguments="{}",
                )
            ]
        )
    )
    update = assembler.add(
        _chunk(
            tool_calls=[
                _tool_delta(
                    index=0,
                    call_id="call-2",
                    name="second",
                    arguments="{}",
                )
            ]
        )
    )
    response = assembler.build_response()

    assert update.started_tools == ("second",)
    assert [call.id for call in response.choices[0].message.tool_calls] == [
        "call-1",
        "call-2",
    ]


def test_invalid_nonempty_tool_arguments_force_length_finish_reason():
    assembler = StreamingResponseAssembler()
    assembler.add(
        _chunk(
            tool_calls=[
                _tool_delta(
                    call_id="call-1",
                    name="write_file",
                    arguments='{"path":',
                )
            ],
            finish_reason="tool_calls",
        )
    )

    response = assembler.build_response()

    assert response.choices[0].finish_reason == "length"


def test_partial_delivery_response_prevents_a_second_visible_answer():
    response = StreamingResponseAssembler.partial_delivery_response("safe-model")

    assert response.id == "partial-stream-stub"
    assert response.model == "safe-model"
    assert response.choices[0].message.content is None
    assert response.choices[0].message.tool_calls is None
    assert response.choices[0].finish_reason == "stop"


def test_agent_streaming_transport_uses_assembler_and_preserves_callbacks(monkeypatch):
    usage = SimpleNamespace(prompt_tokens=4, completion_tokens=2)
    stream = [
        _chunk(reasoning="check ", content="hello "),
        _chunk(content="world", finish_reason="stop"),
        _chunk(choices=False, usage=usage),
    ]
    request_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: stream)
        )
    )
    closed: list[tuple[object, str]] = []
    text_deltas: list[str] = []
    reasoning_deltas: list[str] = []
    first_deltas: list[bool] = []
    activity: list[str] = []

    agent = AIAgent.__new__(AIAgent)
    agent.base_url = "https://api.example/v1"
    agent.model = "safe-model"
    agent._interrupt_requested = False
    agent._stream_callback = None
    agent._stream_needs_break = False
    agent._current_streamed_assistant_text = ""
    agent.stream_delta_callback = text_deltas.append
    agent.reasoning_callback = reasoning_deltas.append
    agent.tool_gen_callback = None
    agent._client_lifecycle = SimpleNamespace(
        create_request_client=lambda *, reason: request_client,
        close_request_client=lambda client, *, reason: closed.append(
            (client, reason)
        ),
    )
    agent._touch_activity = activity.append
    agent._capture_rate_limits = lambda _response: None
    agent._emit_status = lambda _message: None
    agent._safe_print = lambda *_args, **_kwargs: None

    monkeypatch.setenv("VOIDCUBE_STREAM_STALE_TIMEOUT", "60")
    response = agent._interruptible_streaming_api_call(
        {"model": "safe-model", "messages": []},
        on_first_delta=lambda: first_deltas.append(True),
    )

    assert text_deltas == ["hello ", "world"]
    assert reasoning_deltas == ["check "]
    assert first_deltas == [True]
    assert agent._current_streamed_assistant_text == "hello world"
    assert response.usage is usage
    assert response.choices[0].message.content == "hello world"
    assert response.choices[0].message.reasoning_content == "check "
    assert activity == [
        "waiting for provider response (streaming)",
        "receiving stream response",
    ]
    assert closed == [(request_client, "stream_request_complete")]
