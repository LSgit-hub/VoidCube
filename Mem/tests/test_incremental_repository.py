from __future__ import annotations

import json
from pathlib import Path

from memai import MemoryStateRepository


def test_incremental_update_preserves_far_history_ids(tmp_path: Path) -> None:
    repository = MemoryStateRepository()
    state_path = tmp_path / "state.json"
    initial_path = tmp_path / "initial.json"
    update_path = tmp_path / "update.json"

    initial_path.write_text(
        json.dumps(
            {
                "turns": [
                    {
                        "turn_id": "turn_old",
                        "speaker": "user",
                        "text": "2025-01-03 we decided to start the memory project.",
                        "timestamp": "2025-01-03T10:00:00Z",
                    },
                    {
                        "turn_id": "turn_recent",
                        "speaker": "assistant",
                        "text": "2026-03-20 we implemented the schema.",
                        "timestamp": "2026-03-20T10:00:00Z",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    update_path.write_text(
        json.dumps(
            {
                "turns": [
                    {
                        "turn_id": "turn_new",
                        "speaker": "user",
                        "text": "2026-03-23 we refined the revision rules.",
                        "timestamp": "2026-03-23T10:00:00Z",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    initial_state = repository.initialize_from_transcript(state_path, initial_path)
    oldest_event_id = min(
        initial_state.result.events, key=lambda item: item.timespan_start
    ).id

    updated_state = repository.update_from_transcript(state_path, update_path)

    assert updated_state.version == 2
    assert any(event.id == oldest_event_id for event in updated_state.result.events)
    assert any(turn.turn_id == "turn_new" for turn in updated_state.result.turns)
