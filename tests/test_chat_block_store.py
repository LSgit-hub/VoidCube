from __future__ import annotations

from voidcube.domain.contracts.events import MessageDelta, TurnEvent, TurnEventKind
from voidcube.domain.contracts.artifacts import Artifact
from voidcube.domain.contracts.execution import ExecutionState
from voidcube.domain.contracts.tool_events import ToolEvent
from voidcube.interfaces.cli.chat.block_store import ChatBlockStore


def test_store_correlates_turn_stream_and_tool_lifecycle() -> None:
    store = ChatBlockStore(id_factory=lambda: "id")
    store.bind_session("session-1")
    store.consume(TurnEvent(TurnEventKind.STARTED, "session-1", "turn-1"))
    store.record_user_message("inspect files", turn_id="turn-1")
    store.consume(ToolEvent.started(call_id="call-1", name="read_file", arguments={"path": "a.py"}, preview="a.py"))
    store.consume(MessageDelta("session-1", "turn-1", "answer"))
    store.consume(ToolEvent.terminal(call_id="call-1", name="read_file", arguments={"path": "a.py"}, result="ok", duration=0.3, state=ExecutionState.SUCCEEDED))
    store.consume(TurnEvent(TurnEventKind.COMPLETED, "session-1", "turn-1"))

    blocks = store.blocks()
    assert [block.kind for block in blocks] == ["user", "tool_result", "assistant"]
    assert blocks[1].status == "succeeded"
    assert blocks[1].call_id == "call-1"
    assert blocks[2].text == "answer"
    assert blocks[2].status == "completed"


def test_store_keeps_orphaned_tool_result_and_resets_session() -> None:
    store = ChatBlockStore()
    store.bind_session("session-1")
    store.consume(ToolEvent.terminal(call_id="missing", name="shell", arguments={}, result="failed", duration=1, state=ExecutionState.FAILED))
    assert store.blocks()[0].status == "orphaned"
    store.bind_session("session-2")
    assert store.blocks() == ()
    assert store.session_id == "session-2"


def test_store_repeated_deltas_update_one_assistant_block() -> None:
    store = ChatBlockStore()
    store.bind_session("session-1")
    store.consume(TurnEvent(TurnEventKind.STARTED, "session-1", "turn-1"))
    store.consume(MessageDelta("session-1", "turn-1", "a"))
    store.consume(MessageDelta("session-1", "turn-1", "b"))
    blocks = store.blocks(turn_id="turn-1")
    assert len(blocks) == 1
    assert blocks[0].text == "ab"


def test_store_does_not_duplicate_repeated_tool_events() -> None:
    store = ChatBlockStore()
    store.bind_session("session-1")
    store.consume(TurnEvent(TurnEventKind.STARTED, "session-1", "turn-1"))
    started = ToolEvent.started(call_id="call-1", name="shell", arguments={})
    completed = ToolEvent.terminal(
        call_id="call-1",
        name="shell",
        arguments={},
        result="ok",
        duration=0.1,
        state=ExecutionState.SUCCEEDED,
    )
    store.consume(started)
    store.consume(started)
    store.consume(completed)
    store.consume(completed)
    assert len(store.blocks()) == 1
    assert store.blocks()[0].status == "succeeded"


def test_store_ignores_late_events_from_another_session() -> None:
    store = ChatBlockStore()
    store.bind_session("session-1")
    store.consume(TurnEvent(TurnEventKind.STARTED, "session-2", "turn-2"))
    store.consume(MessageDelta("session-2", "turn-2", "stale"))
    assert store.blocks() == ()


def test_store_keeps_full_tool_result_and_artifact_metadata() -> None:
    store = ChatBlockStore()
    store.bind_session("session-1")
    store.consume(TurnEvent(TurnEventKind.STARTED, "session-1", "turn-1"))
    store.consume(
        ToolEvent.terminal(
            call_id="call-1",
            name="write_file",
            arguments={"path": "report.txt"},
            result="x" * 20_000,
            duration=0.5,
            state=ExecutionState.SUCCEEDED,
            artifacts=(
                Artifact(
                    kind="file",
                    uri="report.txt",
                    mime_type="text/plain",
                    title="Report",
                ),
            ),
        )
    )

    block = store.blocks()[0]
    assert len(block.result) == 20_000
    assert block.metadata["artifacts"][0]["uri"] == "report.txt"


def test_store_hydrates_resumed_history_in_turn_order() -> None:
    store = ChatBlockStore()
    store.bind_session("session-1")
    store.hydrate_history((
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "checking"},
        {"role": "tool", "name": "read_file", "content": "result"},
        {"role": "assistant", "content": "answer"},
    ))
    assert [block.kind for block in store.blocks()] == [
        "user", "assistant", "tool_result", "assistant",
    ]
    assert {block.turn_id for block in store.blocks()} == {"history-1"}
