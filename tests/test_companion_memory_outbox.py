from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from plugins.memory.mem.outbox import MemoryWriteOutbox
from voidcube.systems.supervisor.service_runtime import ServiceRuntimeMixin


class _CompanionRuntime(ServiceRuntimeMixin):
    def __init__(self, path):
        self.config = SimpleNamespace(
            execution=SimpleNamespace(gateway_address="http://127.0.0.1:1")
        )
        self._initialize_service_runtime()
        self._companion_memory_outbox = MemoryWriteOutbox(path)


@pytest.mark.asyncio
async def test_companion_turn_is_durable_before_transport(tmp_path):
    runtime = _CompanionRuntime(tmp_path / "companion.sqlite3")

    assert await runtime._persist_companion_turn_pair(
        session_id="companion-session",
        user_text="你好",
        assistant_text="我在。",
    ) is True
    assert runtime._companion_memory_outbox.pending_count() == 1
    await runtime._stop_companion_memory_outbox()


@pytest.mark.asyncio
async def test_companion_outbox_removes_write_after_successful_delivery(tmp_path):
    runtime = _CompanionRuntime(tmp_path / "companion.sqlite3")
    runtime._deliver_companion_memory_write = AsyncMock()  # type: ignore[method-assign]
    await runtime._persist_companion_turn_pair(
        session_id="companion-session",
        user_text="你好",
        assistant_text="我在。",
    )

    await runtime._start_companion_memory_outbox()
    for _ in range(20):
        if runtime._companion_memory_outbox.pending_count() == 0:
            break
        await asyncio.sleep(0.01)
    await runtime._stop_companion_memory_outbox()

    assert runtime._companion_memory_outbox.pending_count() == 0
    runtime._deliver_companion_memory_write.assert_awaited_once()
