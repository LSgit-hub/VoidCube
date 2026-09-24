from voidcube.infrastructure.persistence.turn_event_journal import TurnEventJournal
from voidcube.runtime.agent.context_compressor import ContextCompressor


def test_compaction_lifecycle_is_replayable(tmp_path, monkeypatch):
    journal = TurnEventJournal(tmp_path / "events.db")
    compressor = ContextCompressor(
        "demo",
        config_context_length=16_000,
        protect_first_n=1,
        protect_last_n=0,
        quiet_mode=True,
        event_journal=journal,
    )
    compressor.on_session_start("session-1", VoidCube_home=str(tmp_path))
    monkeypatch.setattr(compressor, "_generate_summary", lambda *_a, **_k: "summary")
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "middle"},
        {"role": "user", "content": "latest"},
    ]
    compressor.protect_first_n = 1
    compressor._find_tail_cut_by_tokens = lambda messages, start: 4
    compressor._sanitize_tool_pairs = lambda messages: messages
    compressor._collect_action_refs = lambda messages: []
    compressor._align_boundary_forward = lambda messages, start: 1
    compressor._persist_checkpoint = lambda checkpoint: None
    compressor._build_checkpoint = lambda **kwargs: type("C", (), {
        "checkpoint_id": "context-checkpoint-test",
        "source_message_count": len(messages),
        "compress_start": 1,
        "compress_end": 3,
    })()
    compressor._journal_event("context.compaction.started", {"checkpoint_id": "test"})
    compressor._journal_event("context.compaction.completed", {"checkpoint_id": "test"})
    types = [row["event_type"] for row in journal.list("session-1")]
    assert types == ["context.compaction.started", "context.compaction.completed"]
