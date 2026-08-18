"""Shared session identity selection for front-end application adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol, Sequence
import uuid


class SessionIndex(Protocol):
    """Read-only session-index port required for automatic resume."""

    def list_sessions_rich(
        self,
        *,
        limit: int,
        exclude_id_prefixes: Sequence[str],
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    """The session selected for one front-end runtime instance."""

    session_id: str
    resumed: bool
    resume_lookup_error: str = ""


def generate_session_id(
    session_start: datetime,
    *,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> str:
    """Create the stable timestamp-and-random identifier used by session stores."""
    return f"{session_start.strftime('%Y%m%d_%H%M%S')}_{uuid_factory().hex[:6]}"


def resolve_session_identity(
    *,
    requested_session_id: str | None,
    auto_resume_enabled: bool,
    session_index: SessionIndex | None,
    session_start: datetime,
    interactive_source: str,
    autonomous_source: str,
    excluded_id_prefixes: Sequence[str] = ("scheduled_",),
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> SessionIdentity:
    """Resolve explicit, resumable, or fresh session identity without UI state.

    An open autonomous owner session takes precedence because it can legitimately
    contain no user messages after a process restart. Otherwise the newest
    interactive session with user-visible history is selected.
    """
    requested = str(requested_session_id or "").strip()
    if requested:
        return SessionIdentity(session_id=requested, resumed=True)

    if auto_resume_enabled and session_index is not None:
        try:
            sessions = session_index.list_sessions_rich(
                limit=20,
                exclude_id_prefixes=list(excluded_id_prefixes),
            )
            for session in sessions:
                if (
                    session.get("source") == autonomous_source
                    and session.get("ended_at") is None
                    and str(session.get("id") or "").strip()
                ):
                    return SessionIdentity(
                        session_id=str(session["id"]).strip(),
                        resumed=True,
                    )
            for session in sessions:
                if (
                    session.get("source") == interactive_source
                    and int(session.get("message_count") or 0) > 0
                    and str(session.get("id") or "").strip()
                ):
                    return SessionIdentity(
                        session_id=str(session["id"]).strip(),
                        resumed=True,
                    )
        except Exception as exc:
            return SessionIdentity(
                session_id=generate_session_id(session_start, uuid_factory=uuid_factory),
                resumed=False,
                resume_lookup_error=str(exc),
            )

    return SessionIdentity(
        session_id=generate_session_id(session_start, uuid_factory=uuid_factory),
        resumed=False,
    )
