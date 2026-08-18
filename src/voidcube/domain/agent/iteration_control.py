#!/usr/bin/env python3
"""
Agent Iteration Control

Contains iteration budget management and context pressure monitoring.
"""

import threading


class IterationBudget:
    """Thread-safe iteration counter for an agent.

    Each agent (parent or subagent) gets its own ``IterationBudget``.
    The parent's budget is capped at ``max_iterations`` (default 90).
    Each subagent gets an independent budget capped at
    ``delegation.max_iterations`` (default 50) — this means total
    iterations across parent + subagents can exceed the parent's cap.
    Users control the per-subagent limit via ``delegation.max_iterations``
    in config.yaml.

    ``execute_code`` (programmatic tool calling) iterations are refunded via
    :meth:`refund` so they don't eat into the budget.
    """

    def __init__(self, max_total: int):
        self.max_total = max_total
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        """Try to consume one iteration.  Returns True if allowed."""
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    def refund(self) -> None:
        """Give back one iteration (e.g. for execute_code turns)."""
        with self._lock:
            if self._used > 0:
                self._used -= 1

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)


class ContextPressureMonitor:
    """Monitor context pressure and provide warning tiers.

    Context pressure warnings notify the USER (not the LLM) as context
    fills up.  Purely informational — displayed in CLI output and sent via
    status_callback for gateway platforms.  Does NOT inject into messages.

    Tiered warnings fire at 85% and 95% of compaction threshold.
    """

    def __init__(self, warning_tiers: list[float] = None):
        self.warning_tiers = warning_tiers or [0.85, 0.95]
        self._warned_at = 0.0
        self._lock = threading.Lock()

    def check_pressure(self, current_ratio: float) -> float:
        """Check if context pressure warrants a warning.

        Returns the highest warning tier that should fire, or 0.0 if no warning.
        """
        with self._lock:
            for tier in sorted(self.warning_tiers, reverse=True):
                if current_ratio >= tier and self._warned_at < tier:
                    self._warned_at = tier
                    return tier
            return 0.0

    def reset(self) -> None:
        """Reset warning state for new session."""
        with self._lock:
            self._warned_at = 0.0

    @property
    def last_warned_tier(self) -> float:
        return self._warned_at
