from __future__ import annotations

import pytest

from scripts.smoke_memory_outbox import (
    run_http_health_smoke,
    run_recovery_soak,
)


pytestmark = [pytest.mark.integration, pytest.mark.operational]


@pytest.mark.asyncio
@pytest.mark.smoke
async def test_two_provider_outbox_health_reaches_memory_through_gateway():
    result = await run_http_health_smoke()

    assert result["memory_status"] == "healthy"
    assert result["agent_outbox"] == {
        "reporter_count": 2,
        "pending_count": 2,
        "dead_letter_count": 0,
        "status": "healthy",
    }


@pytest.mark.smoke
def test_outbox_retry_dead_letter_requeue_and_multiprocess_drain():
    result = run_recovery_soak(
        duration_seconds=0.0,
        interval_seconds=0.0,
        batch_size=8,
    )

    assert result["cycles"] == 1
    assert result["writes_delivered"] == 9
    assert result["duplicate_claims"] == 0
    assert result["final_outbox"]["pending_count"] == 0
    assert result["final_outbox"]["inflight_count"] == 0
    assert result["final_outbox"]["dead_letter_count"] == 0
