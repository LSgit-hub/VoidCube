"""Pure learning-topic policy and endogenous learning candidate factories."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Any, Dict, List, Optional

from .endogenous_candidate_pipeline import (
    AdaptivePolicyLike,
    EndogenousTaskCandidate,
    adaptive_factor_for_candidate,
    build_scored_candidate,
    clamp01,
)


_TOPIC_WORD_RE = re.compile(r"[a-zA-Z0-9_]{3,}")
_TOPIC_STOPWORDS = {
    "voidcube", "agent", "system", "task", "tasks", "work", "review", "recent",
    "learning", "learn", "research", "improve", "improvement", "current", "shell",
    "body", "code", "codebase", "baseline", "follow", "followup", "thread",
    "general", "quality", "issue", "issues", "notes", "evidence", "future",
}


def stable_learning_topic_key(topic: str) -> str:
    """Generate a stable key so distinct learning topics can coexist."""
    normalized = str(topic or "").strip().lower()
    if not normalized:
        return "creativity:idle_learning:fallback"
    digest = hashlib.md5(normalized.encode()).hexdigest()[:8]
    return f"creativity:idle_learning:{digest}"


def extract_learning_topic(activity: Dict[str, Any]) -> str:
    """Extract one concise topic from recent gateway activity metadata."""
    recent = dict(activity.get("recent_metadata") or {})
    user_request = dict(recent.get("user_request") or {})
    agent_work = dict(recent.get("agent_work") or {})

    user_text = str(
        user_request.get("text")
        or user_request.get("query")
        or user_request.get("topic")
        or user_request.get("title")
        or user_request.get("summary")
        or ""
    )
    if user_text and len(user_text) > 10:
        topic = user_text.split(".")[0].split("\n")[0].strip()
        if len(topic) > 80:
            topic = topic[:77] + "..."
        if len(topic) >= 10:
            return topic

    agent_text = str(agent_work.get("summary") or agent_work.get("title") or "")
    if agent_text and len(agent_text) > 10:
        topic = agent_text.split(".")[0].strip()
        if len(topic) > 80:
            topic = topic[:77] + "..."
        if len(topic) >= 10:
            return topic
    return ""


def topic_signature(text: str) -> set[str]:
    return {
        word.lower()
        for word in _TOPIC_WORD_RE.findall(str(text or "").lower())
        if word.lower() not in _TOPIC_STOPWORDS
    }


def topic_overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def filter_learning_topics(
    topics: List[Dict[str, str]],
    *,
    drive_context: Dict[str, Any],
    existing_keys: set[str],
    cooldown_hours: int,
    overlap_threshold: float,
    max_topics: int,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    filtered: list[Dict[str, Any]] = []
    seen_signatures: list[set[str]] = []
    completed_learning_tasks = list(drive_context.get("completed_learning_tasks") or [])
    api_b_judgement_tasks = list(drive_context.get("autonomous_chain_live_tasks") or [])
    current_time = now or datetime.now(timezone.utc)

    for topic in topics:
        title = str(topic.get("title") or "").strip()
        if not title:
            continue
        topic_key = stable_learning_topic_key(title)
        if topic_key in existing_keys:
            continue
        signature = topic_signature(title)
        if any(topic_overlap(signature, previous) >= overlap_threshold for previous in seen_signatures):
            continue
        if _topic_seen_recently(
            title,
            signature,
            completed_learning_tasks=completed_learning_tasks,
            api_b_judgement_tasks=api_b_judgement_tasks,
            cooldown_hours=cooldown_hours,
            overlap_threshold=overlap_threshold,
            now=current_time,
        ):
            continue
        novelty_score = topic_novelty_score(signature, drive_context=drive_context)
        specificity_score = topic_specificity_score(title, signature)
        filtered.append(
            {
                "title": title,
                "summary": str(topic.get("summary") or title).strip(),
                "novelty_score": novelty_score,
                "specificity_score": specificity_score,
            }
        )
        seen_signatures.append(signature)

    filtered.sort(
        key=lambda item: (
            float(item.get("novelty_score") or 0.0),
            float(item.get("specificity_score") or 0.0),
        ),
        reverse=True,
    )
    return filtered[: max(0, max_topics)]


def topic_novelty_score(signature: set[str], *, drive_context: Dict[str, Any]) -> float:
    if not signature:
        return 0.0
    recent_signatures = list(drive_context.get("recent_learning_signatures") or [])
    if not recent_signatures:
        return 1.0
    highest_overlap = max(
        (topic_overlap(signature, prior) for prior in recent_signatures),
        default=0.0,
    )
    return max(0.0, 1.0 - highest_overlap)


def topic_specificity_score(title: str, signature: set[str]) -> float:
    word_count = len(str(title or "").split())
    signature_bonus = min(len(signature), 6) / 6.0
    word_bonus = min(max(word_count, 1), 12) / 12.0
    return round(signature_bonus * 0.7 + word_bonus * 0.3, 4)


def idle_learning_urgency(
    *,
    active_sessions: int,
    topic_source: str,
    autonomous_chain_gate: bool,
) -> float:
    base = {
        "activity_metadata": 0.42,
        "shell_baseline_bootstrap": 0.55,
        "shell_baseline_fallback": 0.4,
    }.get(topic_source, 0.4)
    session_penalty = min(max(active_sessions, 0), 3) * 0.05
    gate_bonus = 0.05 if autonomous_chain_gate else 0.0
    return round(clamp01(base - session_penalty + gate_bonus), 4)


def build_shell_baseline_learning_candidate(
    *,
    stable_key: str,
    active_sessions: int,
    shell_slot_id: str,
    shell_worktree: str,
    trigger: str,
    bootstrap: bool,
    urgency: float,
    backlog_pressure_penalty: float,
    drive_judgement: Optional[Dict[str, Any]],
    adaptive_policy: AdaptivePolicyLike,
) -> EndogenousTaskCandidate:
    summary = (
        "Use idle capacity to inspect the current shell-body codebase, "
        "map its structure, identify current weaknesses, and record evidence-backed "
        "learning notes that can guide future self-improvement."
    )
    if shell_worktree:
        summary += f" Start from shell slot {shell_slot_id} at {shell_worktree}."
    return build_scored_candidate(
        stable_key=stable_key,
        title="Understand the current shell body codebase",
        summary=summary,
        priority="normal",
        governance_task_type="self_learning",
        task_family="self_learning",
        execution_kind=None,
        value_tags=["creativity"],
        candidate_kind="shell_baseline_learning",
        score_inputs={
            "core_value_strength": 0.79 if bootstrap else 0.66,
            "urgency": urgency,
            "novelty": 0.88 if bootstrap else 0.45,
            "specificity": 0.68 if bootstrap else 0.58,
            "execution_readiness": 0.92 if shell_worktree else 0.78,
            "backlog_pressure_penalty": backlog_pressure_penalty,
            "adaptive_factor": adaptive_factor_for_candidate(
                candidate_kind="shell_baseline_learning",
                adaptive_policy=adaptive_policy,
            ),
        },
        metadata={
            "learning_branch": "codebase_baseline",
            "self_learning_mode": "shell_codebase_baseline",
            **({"drive_judgement": drive_judgement} if drive_judgement else {}),
        },
        evidence={
            "active_sessions": active_sessions,
            "trigger": trigger,
            "learning_topic": "",
            "topic_source": "shell_codebase_baseline",
            "learning_branch": "codebase_baseline",
            "llm_generated": False,
            "baseline_worktree_path": shell_worktree,
            "baseline_slot_id": shell_slot_id,
        },
        constraints={
            "execution_policy": "learn_shell_baseline",
            "must_not_modify_active_body": True,
            "baseline_worktree_path": shell_worktree,
            "baseline_slot_id": shell_slot_id,
        },
    )


def build_exploratory_learning_candidate(
    *,
    topic: Dict[str, Any],
    active_sessions: int,
    urgency: float,
    backlog_pressure_penalty: float,
    adaptive_policy: AdaptivePolicyLike,
    drive_judgement: Dict[str, Any],
) -> EndogenousTaskCandidate:
    title = str(topic.get("title") or "").strip()
    return build_scored_candidate(
        stable_key=stable_learning_topic_key(title),
        title=f"Research: {title}",
        summary=str(topic.get("summary") or title),
        priority="normal",
        governance_task_type="self_learning",
        task_family="self_learning",
        execution_kind=None,
        value_tags=["creativity"],
        candidate_kind="exploratory_learning",
        score_inputs={
            "core_value_strength": 0.64,
            "urgency": urgency,
            "novelty": float(topic.get("novelty_score") or 0.6),
            "specificity": float(topic.get("specificity_score") or 0.55),
            "execution_readiness": 0.66,
            "backlog_pressure_penalty": backlog_pressure_penalty,
            "repetition_penalty": round(
                max(0.0, 0.55 - float(topic.get("novelty_score") or 0.6)),
                4,
            ),
            "adaptive_factor": adaptive_factor_for_candidate(
                candidate_kind="exploratory_learning",
                adaptive_policy=adaptive_policy,
            ),
        },
        metadata={
            "learning_branch": "exploratory",
            "self_learning_mode": "no_dependency_exploration",
            "drive_judgement": dict(drive_judgement),
        },
        evidence={
            "active_sessions": active_sessions,
            "trigger": "idle_capacity",
            "learning_topic": title,
            "topic_source": "activity_metadata",
            "learning_branch": "exploratory",
            "llm_generated": False,
            "novelty_score": topic.get("novelty_score"),
            "specificity_score": topic.get("specificity_score"),
        },
        constraints={
            "execution_policy": "learn_only",
            "must_not_modify_active_body": True,
        },
    )


def build_cognitive_assessment_review_candidate(
    *,
    target: str,
    judgement: str,
    cognitive_assessment_memory: Dict[str, Any],
    active_sessions: int,
    preferred_focus: str,
    backlog_pressure_penalty: float,
    adaptive_policy: AdaptivePolicyLike,
    drive_judgement: Dict[str, Any],
) -> EndogenousTaskCandidate:
    review_summary = (
        "Review the latest endogenous cognitive-assessment memory, "
        "extract what changed, and record evidence-backed learning notes "
        "for the next autonomous planning cycle."
    )
    if judgement:
        review_summary += f" Current judgement: {judgement}."
    return build_scored_candidate(
        stable_key=(
            "creativity:self_learning:cognitive_review:"
            f"{stable_learning_topic_key(target or judgement)}"
        ),
        title=f"Review endogenous cognition: {target or 'current judgement'}",
        summary=review_summary,
        priority="normal",
        governance_task_type="self_learning",
        task_family="self_learning",
        execution_kind=None,
        value_tags=["creativity", "truthfulness"],
        candidate_kind="exploratory_learning",
        score_inputs={
            "core_value_strength": 0.72,
            "urgency": clamp01(
                0.42
                + float(cognitive_assessment_memory.get("why_not_improvement_now_count") or 0) * 0.08
                + (0.08 if preferred_focus in {"truthfulness", "observation"} else 0.0)
            ),
            "novelty": 0.52,
            "specificity": 0.66,
            "execution_readiness": 0.72,
            "backlog_pressure_penalty": backlog_pressure_penalty,
            "adaptive_factor": adaptive_factor_for_candidate(
                candidate_kind="exploratory_learning",
                adaptive_policy=adaptive_policy,
            ),
        },
        metadata={
            "learning_branch": "cognitive_assessment_review",
            "self_learning_mode": "endogenous_cognition_review",
            "cognitive_assessment_target": target,
            "llm_cognitive_assessment": dict(cognitive_assessment_memory),
            "drive_judgement": dict(drive_judgement),
        },
        evidence={
            "active_sessions": active_sessions,
            "trigger": "canonical_cognitive_assessment_memory",
            "learning_topic": target,
            "topic_source": "cognitive_assessment_memory",
            "learning_branch": "cognitive_assessment_review",
            "llm_generated": False,
            "cognitive_assessment_memory": dict(cognitive_assessment_memory),
        },
        constraints={
            "execution_policy": "learn_only",
            "must_not_modify_active_body": True,
        },
    )


def _topic_seen_recently(
    title: str,
    signature: set[str],
    *,
    completed_learning_tasks: List[Dict[str, Any]],
    api_b_judgement_tasks: List[Dict[str, Any]],
    cooldown_hours: int,
    overlap_threshold: float,
    now: datetime,
) -> bool:
    normalized = _normalize_topic_text(title)
    for task in completed_learning_tasks:
        prior_title = str(task.get("title") or "").strip()
        if not prior_title:
            continue
        if normalized == _normalize_topic_text(prior_title) and _within_cooldown(
            task.get("completed_at"), now=now, cooldown_hours=cooldown_hours
        ):
            return True
        if topic_overlap(signature, topic_signature(prior_title)) >= overlap_threshold and _within_cooldown(
            task.get("completed_at"), now=now, cooldown_hours=cooldown_hours
        ):
            return True

    for task in api_b_judgement_tasks:
        prior_title = str(task.get("title") or "").strip()
        if not prior_title:
            continue
        if str(task.get("status") or "").strip().lower() in {
            "completed", "failed", "cancelled"
        }:
            continue
        prior_signature = topic_signature(prior_title)
        if normalized == _normalize_topic_text(prior_title) or topic_overlap(
            signature, prior_signature
        ) >= overlap_threshold:
            return True
    return False


def _normalize_topic_text(text: str) -> str:
    return " ".join(_TOPIC_WORD_RE.findall(str(text or "").lower())).strip()


def _within_cooldown(
    raw_timestamp: Any,
    *,
    now: datetime,
    cooldown_hours: int,
) -> bool:
    if cooldown_hours <= 0 or not raw_timestamp:
        return False
    try:
        parsed = datetime.fromisoformat(str(raw_timestamp))
    except (TypeError, ValueError, OverflowError):
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return now - parsed <= timedelta(hours=cooldown_hours)
