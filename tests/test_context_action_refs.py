from __future__ import annotations

import json

from voidcube.runtime.agent.context_compressor import ContextCompressor


def _ref(action_id: str, *, state: str = "succeeded") -> dict:
    return {
        "action_id": action_id,
        "state": state,
        "target_summary": f"target-{action_id}",
        "evidence_refs": [
            {
                "evidence_id": f"evidence-{action_id}",
                "kind": "execution_result",
                "content_hash": f"hash-{action_id}",
                "collected_at": 1.0,
            }
        ],
    }


def _compressor() -> ContextCompressor:
    compressor = ContextCompressor(
        model="test-model",
        config_context_length=16_000,
        protect_first_n=1,
        protect_last_n=1,
        quiet_mode=True,
    )
    compressor.tail_token_budget = 30
    return compressor


def _messages(ref: dict) -> list[dict]:
    return [
        {"role": "system", "content": "policy"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "write_file", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "x" * 1000,
            "action_refs": [ref],
        },
        {"role": "assistant", "content": "intermediate"},
        {"role": "assistant", "content": "older context"},
        {"role": "user", "content": "older question"},
        {"role": "user", "content": "latest"},
    ]


def _all_refs(messages: list[dict]) -> list[dict]:
    return [
        ref
        for message in messages
        for ref in message.get("action_refs") or []
    ]


def test_model_switch_refreshes_context_dependent_budgets():
    compressor = ContextCompressor(
        model="large-model",
        config_context_length=256_000,
        quiet_mode=True,
    )

    assert compressor.threshold_tokens == 128_000
    assert compressor.tail_token_budget == 25_600
    assert compressor.max_summary_tokens == 12_000

    compressor.update_model("smaller-model", 64_000)

    assert compressor.context_length == 64_000
    assert compressor.threshold_tokens == 64_000
    assert compressor.tail_token_budget == 12_800
    assert compressor.max_summary_tokens == 3_200


def test_pruning_large_tool_output_keeps_action_ref_and_readable_pointer():
    compressor = _compressor()
    ref = _ref("act-pruned")

    pruned, count = compressor._prune_old_tool_results(
        _messages(ref),
        protect_tail_count=1,
        protect_tail_tokens=None,
    )

    assert count == 1
    assert pruned[2]["action_refs"] == [ref]
    assert "act-pruned state=succeeded" in pruned[2]["content"]


def test_summary_failure_recovers_from_durable_structured_checkpoint(
    monkeypatch,
    tmp_path,
):
    compressor = _compressor()
    compressor.on_session_start("session-fallback", VoidCube_home=str(tmp_path))
    monkeypatch.setattr(compressor, "_generate_summary", lambda *_args, **_kwargs: None)

    compressed = compressor.compress(_messages(_ref("act-fallback")), current_tokens=9000)

    refs = _all_refs(compressed)
    assert [item["action_id"] for item in refs] == ["act-fallback"]
    summary = next(
        message["content"]
        for message in compressed
        if "Structured Recovery Checkpoint" in message["content"]
    )
    assert "act-fallback state=succeeded" in summary
    assert "intermediate" in summary

    checkpoint_files = list(
        (tmp_path / "runtime" / "context-checkpoints").glob("*.json")
    )
    assert len(checkpoint_files) == 1
    payload = json.loads(checkpoint_files[0].read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["checkpoint_id"].startswith("context-checkpoint-")
    assert payload["session_id"] == "session-fallback"
    assert payload["action_refs"][0]["action_id"] == "act-fallback"
    assert any(turn.get("content") == "intermediate" for turn in payload["turns"])

    restored = _compressor()
    restored.on_session_start("session-fallback", VoidCube_home=str(tmp_path))
    assert restored._last_checkpoint["checkpoint_id"] == payload["checkpoint_id"]


def test_repeated_compression_deduplicates_and_preserves_action_refs(monkeypatch):
    compressor = _compressor()
    monkeypatch.setattr(compressor, "_generate_summary", lambda *_args, **_kwargs: "summary")
    first = compressor.compress(_messages(_ref("act-repeat")), current_tokens=9000)
    expanded = [*first, {"role": "assistant", "content": "more"}, {"role": "user", "content": "latest again"}]

    second = compressor.compress(expanded, current_tokens=9000)

    action_ids = [item["action_id"] for item in _all_refs(second)]
    assert action_ids.count("act-repeat") == 1
    assert any("act-repeat state=succeeded" in str(message.get("content")) for message in second)
