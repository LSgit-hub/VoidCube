from pathlib import Path

from voidcube.domain.agent.effect_outcomes import EffectOutcome
from voidcube.infrastructure.persistence.checkpoint_manager import CheckpointManager


def test_disabled_checkpoint_is_structured_skip(tmp_path: Path):
    outcome = CheckpointManager(enabled=False).ensure_checkpoint_effect(str(tmp_path))
    assert isinstance(outcome, EffectOutcome)
    assert outcome.status == "skipped"
    assert outcome.details["reason"] == "disabled"


def test_failed_checkpoint_can_be_retried(monkeypatch, tmp_path: Path):
    manager = CheckpointManager(enabled=True)
    manager._git_available = True
    calls = []
    monkeypatch.setattr(manager, "_take", lambda directory, reason: calls.append(directory) or False)

    first = manager.ensure_checkpoint_effect(str(tmp_path), "write")
    second = manager.ensure_checkpoint_effect(str(tmp_path), "write")
    assert first.status == second.status == "failed"
    assert len(calls) == 2
