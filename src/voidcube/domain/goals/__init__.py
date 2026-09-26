"""Domain contracts for routing and tracking a single active goal."""

from .routing import GoalRoutingDecision, classify_goal_backend

__all__ = ["GoalRoutingDecision", "classify_goal_backend"]
