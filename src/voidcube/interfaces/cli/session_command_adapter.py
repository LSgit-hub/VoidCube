"""Pure CLI projections for session command handlers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping, Sequence

from ...application.sessions import BranchSessionResult, ResumeSessionResult


class ResumeTargetStatus(str, Enum):
    RESOLVED = "resolved"
    INDEX_OUT_OF_RANGE = "index_out_of_range"


@dataclass(frozen=True, slots=True)
class ResumeTarget:
    status: ResumeTargetStatus
    requested: str
    session_id: str = ""
    available_count: int = 0


@dataclass(frozen=True, slots=True)
class ResumeSummaryLabels:
    resumed_session: str
    user_messages: str
    total: str
    no_messages_starting_fresh: str


def is_resume_index(requested: str) -> bool:
    """Return whether a resume target expresses a signed integer index."""
    return requested.isdigit() or (
        requested.startswith("-") and requested[1:].isdigit()
    )


def resolve_resume_target(
    requested: str,
    *,
    recent_sessions: Sequence[Mapping[str, object]],
    resolve_named: Callable[[str], str | None],
) -> ResumeTarget:
    """Resolve an adapter-selected number, title, or session identifier."""
    if is_resume_index(requested):
        index = int(requested) - 1
        if 0 <= index < len(recent_sessions):
            return ResumeTarget(
                ResumeTargetStatus.RESOLVED,
                requested,
                session_id=str(recent_sessions[index].get("id") or ""),
                available_count=len(recent_sessions),
            )
        return ResumeTarget(
            ResumeTargetStatus.INDEX_OUT_OF_RANGE,
            requested,
            available_count=len(recent_sessions),
        )
    return ResumeTarget(
        ResumeTargetStatus.RESOLVED,
        requested,
        session_id=resolve_named(requested) or requested,
    )


def project_resume_summary(
    result: ResumeSessionResult,
    *,
    labels: ResumeSummaryLabels,
) -> str:
    title = result.metadata.get("title")
    title_part = f' "{title}"' if title else ""
    history = result.state.conversation_history
    if history:
        user_count = sum(message.get("role") == "user" for message in history)
        return (
            f"  ↻ {labels.resumed_session} {result.state.session_id}{title_part}"
            f" ({user_count} {labels.user_messages}, {len(history)} {labels.total})"
        )
    return (
        f"  ↻ {labels.resumed_session} {result.state.session_id}{title_part} — "
        f"{labels.no_messages_starting_fresh}."
    )


def project_branch_summary(result: BranchSessionResult) -> tuple[str, str, str]:
    user_count = sum(
        message.get("role") == "user"
        for message in result.state.conversation_history
    )
    noun = "message" if user_count == 1 else "messages"
    return (
        f'  ⑂ Branched session "{result.title}" ({user_count} user {noun})',
        f"  Original session: {result.parent_session_id}",
        f"  Branch session:   {result.state.session_id}",
    )
