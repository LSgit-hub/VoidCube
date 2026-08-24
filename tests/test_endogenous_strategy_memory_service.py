from voidcube.systems.supervisor.endogenous_strategy_memory_service import (
    EndogenousStrategyMemoryService,
)


def test_strategy_memory_service_updates_all_runtime_buckets() -> None:
    service = EndogenousStrategyMemoryService()
    history = {}

    service.focus_bucket(history, "Observation")["judged"] += 1
    service.focus_bucket(history, "Observation", context_key="quiet|stable|none")[
        "judged"
    ] += 1
    service.record_agenda(
        history,
        topic="observe_before_acting",
        priority=0.8,
        confidence=0.7,
        context_key="quiet|stable|none",
        recorded_at="2026-08-04T00:00:00+00:00",
        status="active",
    )
    service.record_observation(
        history,
        target="truthfulness",
        priority=0.9,
        risk=0.6,
        context_key="quiet|stable|none",
        recorded_at="2026-08-04T00:00:00+00:00",
        status="recommended",
    )
    service.record_meta_governance(
        history,
        mode="observe",
        priority=0.75,
        confidence=0.8,
        context_key="quiet|stable|none",
        recorded_at="2026-08-04T00:00:00+00:00",
        status="active",
    )

    memory = history["strategy_memory"]
    assert memory["focus_stats"]["observation"]["judged"] == 1
    assert memory["agenda_topic_stats"]["observe_before_acting"]["seen"] == 1
    assert memory["observation_target_stats"]["truthfulness"]["recommended"] == 1
    assert memory["meta_governance_stats"]["observe"]["active_cycles"] == 1


def test_strategy_memory_service_resolves_cleared_observation_targets() -> None:
    service = EndogenousStrategyMemoryService()
    history = {
        "strategy_memory": {
            "observation_target_stats": {
                "truthfulness": {
                    "recommended": 2,
                    "resolved": 0,
                    "last_priority": 0.8,
                    "last_risk": 0.5,
                    "last_status": "recommended",
                }
            }
        }
    }

    changed = service.resolve_cleared_observation_targets(
        history,
        active_targets=set(),
        context_key="quiet|stable|none",
        recorded_at="2026-08-04T00:00:00+00:00",
    )

    assert changed is True
    stats = history["strategy_memory"]["observation_target_stats"]["truthfulness"]
    assert stats["resolved"] == 1
    assert stats["last_status"] == "resolved"
