from systems.supervisor.autonomous_chain_store import AutonomousChainTask
from systems.supervisor.autonomous_learning_quality import (
    assess_autonomous_learning_quality,
)


def _task(*, branch: str) -> AutonomousChainTask:
    return AutonomousChainTask(
        title="Research quality",
        task_family="self_learning",
        metadata={"learning_branch": branch},
    )


def test_exploratory_quality_requires_recorded_web_evidence() -> None:
    result = assess_autonomous_learning_quality(
        _task(branch="exploratory"),
        {
            "response": "Evidence-backed conclusion. " * 20,
            "tools_used": ["web_search", "web_extract"],
            "source_urls": ["https://example.com/primary"],
        },
    )

    assert result["score"] >= 0.6
    assert "web_search_recorded" in result["signals"]
    assert "web_sources:1" in result["signals"]


def test_local_quality_requires_recorded_read_only_inspection() -> None:
    task = _task(branch="codebase_baseline")
    weak = assess_autonomous_learning_quality(
        task,
        {"response": "A conclusion without observable inspection. " * 10},
    )
    strong = assess_autonomous_learning_quality(
        task,
        {
            "response": "Evidence and uncertainty were recorded. " * 20,
            "tools_used": ["read_file", "search_files"],
        },
    )

    assert weak["score"] < 0.6
    assert strong["score"] >= 0.6


def test_failed_turn_has_no_learning_quality() -> None:
    assert assess_autonomous_learning_quality(
        _task(branch="exploratory"),
        {"response": "Partial result", "failed": True},
    ) == {"score": 0.0, "signals": ["turn_not_completed"]}
