"""State and transitions for one model API attempt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from .response import ChatResponseInspection


@dataclass(slots=True)
class ApiAttemptState:
    """Mutable retry state scoped to one conversation-loop iteration."""

    started_at: float
    max_retries: int = 3
    max_compression_attempts: int = 3
    retry_count: int = 0
    primary_recovery_attempted: bool = False
    subscription_auth_retry_attempted: bool = False
    rate_limit_retry_attempted: bool = False
    restart_with_compressed_messages: bool = False
    restart_with_length_continuation: bool = False
    finish_reason: str = "stop"
    response: Any = None
    response_inspection: ChatResponseInspection | None = None
    request_kwargs: dict[str, Any] | None = None

    @property
    def can_retry(self) -> bool:
        return self.retry_count < self.max_retries

    def record_failure(self) -> int:
        self.retry_count += 1
        return self.retry_count

    def reset_retry_cycle(self) -> None:
        self.retry_count = 0
        self.primary_recovery_attempted = False

    def request_compressed_restart(self) -> None:
        self.restart_with_compressed_messages = True

    def request_length_continuation(self) -> None:
        self.restart_with_length_continuation = True
