from types import SimpleNamespace

from voidcube.interfaces.cli.btw_runtime import CliBtwPorts, CliBtwRuntime


class _ImmediateThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


class _Console:
    def __init__(self):
        self.items = []

    def print(self, value):
        self.items.append(value)


def _runtime(agent, output, console, events, credentials=True):
    created = []
    return (
        CliBtwRuntime(
            CliBtwPorts(
                ensure_credentials=lambda: credentials,
                resolve_agent_route=lambda _question: {"model": "m", "runtime": {}},
                conversation_history=lambda: [{"role": "user", "content": "old"}],
                create_agent=lambda route, task_id: created.append((route, task_id)) or agent,
                task_id_factory=lambda: "btw-test-1",
                emit=output.append,
                invalidate=lambda: events.append("invalidate"),
                sleep=lambda seconds: events.append(("sleep", seconds)),
                emit_blank_line=lambda: events.append("blank"),
                create_console=lambda: console,
                rich_text_from_ansi=lambda text: f"rich:{text}",
                bell=lambda: events.append("bell"),
                thread_factory=_ImmediateThread,
            )
        ),
        created,
    )


def test_btw_runtime_runs_ephemeral_agent_and_renders_response():
    output = []
    console = _Console()
    events = []
    agent = SimpleNamespace(
        run_conversation=lambda **kwargs: {
            "final_response": "answer",
            "seen": kwargs,
        }
    )
    runtime, created = _runtime(agent, output, console, events)

    assert runtime.start("question") is True
    assert created == [({"model": "m", "runtime": {}}, "btw-test-1")]
    assert agent.run_conversation
    assert "question" in output[0]
    assert "> /btw" in console.items[-1].title
    assert "bell" in events
    assert events[-1] == "invalidate"


def test_btw_runtime_handles_empty_response_and_failure():
    empty_output = []
    empty_console = _Console()
    empty_events = []
    empty_runtime, _ = _runtime(
        SimpleNamespace(run_conversation=lambda **_kwargs: {}),
        empty_output,
        empty_console,
        empty_events,
    )
    empty_runtime.start("empty")
    assert any("no response" in line for line in empty_output)

    failed_output = []
    failed_console = _Console()
    failed_runtime, _ = _runtime(
        SimpleNamespace(
            run_conversation=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("broken"))
        ),
        failed_output,
        failed_console,
        [],
    )
    failed_runtime.start("failed")
    assert any("btw failed: broken" in line for line in failed_output)


def test_btw_runtime_rejects_missing_credentials_before_route_or_thread():
    output = []
    runtime, created = _runtime(
        SimpleNamespace(),
        output,
        _Console(),
        [],
        credentials=False,
    )

    assert runtime.start("question") is False
    assert created == []
    assert "no valid credentials" in output[0]
