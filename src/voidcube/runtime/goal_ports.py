"""Runtime composition for optional Goal Manager integrations."""

from __future__ import annotations

from typing import Any


def create_goal_manager_port() -> Any:
    """Construct the optional Goal Manager adapter at the composition edge."""
    from plugins.goal_manager.tools.client import GoalClient

    return GoalClient()


__all__ = ["create_goal_manager_port"]
