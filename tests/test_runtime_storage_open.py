from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from voidcube.infrastructure.memory.governor_bridge import MemGovernorBridge
from voidcube.systems.governor import GovernorDecisionEngine
from voidcube.systems.supervisor.autonomous_chain_store import AutonomousChainStore


@pytest.mark.parametrize("operation", ["create", "clear", "read"])
def test_deferred_chain_store_first_access_completes(tmp_path, operation):
    # A child process bounds a lock regression without leaving a blocked thread
    # behind in the test runner.
    script = """
import sys
from pathlib import Path
from voidcube.systems.supervisor.autonomous_chain_store import AutonomousChainStore

path = Path(sys.argv[1]) / 'nested' / 'tasks.json'
store = AutonomousChainStore(path, defer_open=True)
assert not path.parent.exists()
operation = sys.argv[2]
if operation == 'create':
    store.create_task(title='first write')
elif operation == 'clear':
    store.clear_tasks()
else:
    assert store.list_employee_dispatch_tasks() == []
assert path.is_file()
reopened = AutonomousChainStore(path)
assert len(reopened.list_tasks()) == (1 if operation == 'create' else 0)
"""
    subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), operation],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        timeout=30,
        capture_output=True,
        text=True,
    )


def test_chain_store_failed_open_can_be_retried(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text("invalid json", encoding="utf-8")
    store = AutonomousChainStore(path, defer_open=True)
    with pytest.raises(json.JSONDecodeError):
        store.open()
    path.write_text('{"version": 2, "tasks": []}', encoding="utf-8")
    assert store.list_tasks() == []
    store.create_task(title="recovered")
    assert len(store.list_tasks()) == 1


def test_governor_defers_normalization_until_first_read(tmp_path):
    path = tmp_path / "governor_latest.json"
    original = '{"record_id": "legacy", "kind": "review"}'
    path.write_text(original, encoding="utf-8")
    bridge = MemGovernorBridge(
        storage_root=tmp_path, engine=GovernorDecisionEngine(), defer_open=True,
    )
    assert path.read_text(encoding="utf-8") == original
    assert bridge.get_latest()["memory_domain"] == "evolution"
    assert json.loads(path.read_text(encoding="utf-8"))["memory_domain"] == "evolution"
