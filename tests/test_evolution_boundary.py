from __future__ import annotations

import pytest

from systems.evolution_boundary import classify_agent_evolution_changes


pytestmark = [pytest.mark.smoke, pytest.mark.unit]


def test_body_evolution_accepts_only_current_agent_implementation_paths() -> None:
    report = classify_agent_evolution_changes(
        [
            "agent/runtime.py",
            "tools/terminal_tool.py",
            "systems/agent/runtime.py",
        ]
    )

    assert report.allowed_files == [
        "agent/runtime.py",
        "tools/terminal_tool.py",
    ]
    assert report.unknown_files == ["systems/agent/runtime.py"]
    assert report.ok is False
