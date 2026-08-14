from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from systems.evolution_evaluation import select_benchmark_platforms


pytestmark = [pytest.mark.unit, pytest.mark.smoke]
NOW = datetime(2026, 8, 14, 16, 0, tzinfo=timezone.utc)


def test_default_candidate_requires_windows_with_replayable_reason():
    selection = select_benchmark_platforms(
        ("agent/demo.py",),
        "a" * 64,
        created_at=NOW,
    )

    assert selection.changed_files == ("agent/demo.py",)
    assert selection.required_platforms == ("windows",)
    assert selection.reason_codes == ("project_default_windows",)
    assert selection.selection_id.endswith(selection.content_hash)


@pytest.mark.parametrize(
    ("changed_file", "reason"),
    [
        ("pyproject.toml", "dependency_declaration_changed"),
        ("tools/containerfiles/podman-agent.Containerfile", "container_runtime_changed"),
    ],
)
def test_dependency_and_container_changes_require_linux_and_windows(
    changed_file: str,
    reason: str,
):
    selection = select_benchmark_platforms(
        (changed_file,),
        "b" * 64,
        created_at=NOW,
    )

    assert selection.required_platforms == ("linux", "windows")
    assert reason in selection.reason_codes


def test_windows_runtime_change_records_specific_reason():
    selection = select_benchmark_platforms(
        ("tools/windows_host_executor.py",),
        "c" * 64,
        created_at=NOW,
    )

    assert selection.required_platforms == ("windows",)
    assert selection.reason_codes == (
        "project_default_windows",
        "windows_runtime_changed",
    )


def test_platform_selection_rejects_tampered_content_address():
    selection = select_benchmark_platforms(
        ("agent/demo.py",),
        "d" * 64,
        created_at=NOW,
    )
    payload = selection.model_dump(mode="json")
    payload["required_platforms"] = ["linux", "windows"]

    with pytest.raises(ValidationError, match="content_hash"):
        type(selection).model_validate(payload)
