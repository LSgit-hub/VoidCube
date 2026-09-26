"""Conservative routing of session goals to the formal Goal Manager."""

from __future__ import annotations

import re
from dataclasses import dataclass


_MULTI_STEP = re.compile(
    r"(?:先.{0,80}(?:再|然后)|然后|接着|分解|多步|步骤|first|then|after that|step\s+\d|and then)",
    re.IGNORECASE,
)
_VERIFICATION = re.compile(
    r"(?:测试|验证|验收|检查|复核|证明|回归|test|verify|validate|review|acceptance|ci|coverage)",
    re.IGNORECASE,
)
_RISK_OR_CHANGE = re.compile(
    r"(?:修复|重构|迁移|升级|部署|发布|鉴权|权限|安全|数据库|接口|协议|fix|refactor|migrat|deploy|release|auth|security|database|api|protocol)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class GoalRoutingDecision:
    """The backend selected for one newly created session goal."""

    backend: str
    score: int
    reasons: tuple[str, ...]
    explicit: bool = False


def classify_goal_backend(objective: str, *, explicit: bool = False) -> GoalRoutingDecision:
    """Choose a lightweight session goal or the formal Goal Manager.

    The automatic route requires at least two independent complexity signals and
    a score of three. This keeps short, one-shot requests out of the Goal
    Manager while routing work that needs planning and verification into it.
    An explicit request always wins, including an empty or unusual objective.
    """
    text = " ".join(str(objective or "").split())
    if explicit:
        return GoalRoutingDecision("goal_manager", 0, ("explicit_request",), True)

    score = 0
    reasons: list[str] = []
    if _MULTI_STEP.search(text):
        score += 2
        reasons.append("multi_step")
    if _VERIFICATION.search(text):
        score += 2
        reasons.append("verification")
    if _RISK_OR_CHANGE.search(text):
        score += 1
        reasons.append("change_or_risk")
    if len(text) >= 160:
        score += 1
        reasons.append("long_objective")
    if text.count(";") >= 1 or text.count("；") >= 1:
        score += 1
        reasons.append("multiple_deliverables")

    # A delimiter explicitly separating deliverables is itself enough to
    # justify a formal goal: the work has multiple outcomes even when the
    # wording contains no risk or verification keyword.
    backend = (
        "goal_manager"
        if (score >= 3 and len(reasons) >= 2) or "multiple_deliverables" in reasons
        else "session"
    )
    return GoalRoutingDecision(backend, score, tuple(reasons), False)


__all__ = ["GoalRoutingDecision", "classify_goal_backend"]
