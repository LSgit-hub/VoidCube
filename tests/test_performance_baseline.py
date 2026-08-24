from __future__ import annotations

import json

import pytest

from scripts.performance_baseline import (
    SCHEMA,
    _summary,
    collect_baseline,
    scenarios,
)


def test_performance_scenarios_have_stable_names_and_measurement_contract() -> None:
    items = scenarios()

    assert [item.name for item in items] == [
        "import_graph",
        "cli_help",
        "turn_contract",
        "supervisor_init",
        "ui_projection",
    ]
    assert all(item.description for item in items)
    assert all(item.code is not None for item in items if item.name != "cli_help")
    assert all(item.code is None for item in items if item.name == "cli_help")


def test_summary_is_deterministic_and_reports_milliseconds() -> None:
    assert _summary([3.0, 1.0, 2.0]) == {
        "min_ms": 1.0,
        "median_ms": 2.0,
        "max_ms": 3.0,
    }


def test_collect_baseline_rejects_empty_sample_count() -> None:
    with pytest.raises(ValueError, match="repeat must be at least 1"):
        collect_baseline(repeat=0)


def test_baseline_payload_has_versioned_metric_shape(monkeypatch) -> None:
    samples = iter(((10.0, None), (20.0, None), (30.0, 2.0), (40.0, None), (50.0, 3.0)))
    monkeypatch.setattr(
        "scripts.performance_baseline._run_scenario",
        lambda _scenario: next(samples),
    )

    payload = collect_baseline(repeat=1)

    assert payload["schema"] == SCHEMA
    assert payload["repeat"] == 1
    assert set(payload["metrics"]) == {
        "import_graph",
        "cli_help",
        "turn_contract",
        "supervisor_init",
        "ui_projection",
    }
    assert payload["metrics"]["turn_contract"]["operation"]["median_ms"] == 2.0
    json.dumps(payload)
