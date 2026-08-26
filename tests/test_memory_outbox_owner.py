"""MemoryWriteOutbox owner semantics (Stage 5).

The outbox deliberately has NO file-level exclusive lease: it is a transport
spool whose concurrency gate is the row-level lease (lease_owner/lease_until
claimed under BEGIN IMMEDIATE), and multi-process drain is a committed
contract.  These tests pin the explicit owner annotation written into
``outbox_state`` so the production topology (api_a -> agent, companion ->
supervisor, gateway -> gateway daemon) is observable from the DB itself.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from plugins.memory.mem.outbox import MemoryWriteOutbox


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _state_value(db_path, key: str) -> str | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM outbox_state WHERE key = ?", (key,)
        ).fetchone()
        return str(row[0]) if row else None


def test_create_annotates_owner_into_outbox_state(tmp_path):
    outbox = MemoryWriteOutbox(
        tmp_path / "api.db", owner_label="api_a"
    )
    payload = _state_value(tmp_path / "api.db", "outbox_owner")
    assert payload is not None
    meta = json.loads(payload)
    assert meta["queue_name"] == "api_a"
    assert meta["path"].endswith("api.db")
    assert "worker_id" in meta
    assert outbox.owner_label == "api_a"


def test_direct_construction_without_label_writes_no_owner(tmp_path):
    outbox = MemoryWriteOutbox(tmp_path / "bare.db")
    assert outbox.owner_label is None
    assert _state_value(tmp_path / "bare.db", "outbox_owner") is None


def test_runtime_settings_create_passes_queue_name(tmp_path):
    from plugins.memory.mem.outbox import MemoryOutboxRuntimeSettings

    settings = MemoryOutboxRuntimeSettings(queue_paths={})
    outbox = settings.create("gateway", home=tmp_path)
    assert outbox.owner_label == "gateway"
    meta = json.loads(_state_value(tmp_path / "runtime/memory/gateway-write-outbox.sqlite3", "outbox_owner"))
    assert meta["queue_name"] == "gateway"
