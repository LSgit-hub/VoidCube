"""
用量追踪 — Token 计数、会话成本统计、模型用量报告。

特性:
  - 实时 token 计数 (prompt + completion)
  - 多模型成本计算
  - 会话级别用量汇总
  - /usage 命令支持
  - 用量警告阈值
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Model pricing (USD per 1M tokens) ──────────────────────────────────
# Prices are approximate — check provider pages for current rates.

MODEL_PRICING: Dict[str, Tuple[float, float]] = {
    # (prompt_price_per_1M, completion_price_per_1M) in USD
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-4": (30.00, 60.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o1": (15.00, 60.00),
    "o1-mini": (3.00, 12.00),
    "o3-mini": (1.10, 4.40),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-v3": (0.27, 1.10),
    # Google
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.0-flash": (0.10, 0.40),
    # OpenRouter averages
    "openrouter/auto": (0.50, 2.00),
}

# Default: assume a mid-range model
DEFAULT_PRICING = (1.00, 4.00)


@dataclass
class TurnUsage:
    """Token usage for a single conversation turn."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    model: str = ""
    provider: str = ""
    latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cost_usd(self) -> float:
        """Estimated cost in USD."""
        pp1m, cp1m = self._get_pricing()
        prompt_cost = (self.prompt_tokens / 1_000_000) * pp1m
        completion_cost = (self.completion_tokens / 1_000_000) * cp1m
        return prompt_cost + completion_cost

    def _get_pricing(self) -> Tuple[float, float]:
        """Find pricing for this turn's model."""
        model_lower = self.model.lower()
        for key, prices in MODEL_PRICING.items():
            if key in model_lower or model_lower in key:
                return prices
        return DEFAULT_PRICING


@dataclass
class SessionUsage:
    """Cumulative usage for a conversation session."""
    session_id: str = ""
    turns: List[TurnUsage] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    @property
    def total_prompt_tokens(self) -> int:
        return sum(t.prompt_tokens for t in self.turns)

    @property
    def total_completion_tokens(self) -> int:
        return sum(t.completion_tokens for t in self.turns)

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    @property
    def total_cost_usd(self) -> float:
        return sum(t.cost_usd for t in self.turns)

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    def add_turn(self, turn: TurnUsage) -> None:
        self.turns.append(turn)


class UsageTracker:
    """Tracks token usage and cost across sessions."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: Dict[str, SessionUsage] = {}
        self._current_session_id: Optional[str] = None
        # Warning thresholds
        self.warn_cost_usd: float = 5.0      # warn when session exceeds $5
        self.warn_tokens: int = 100_000      # warn when session exceeds 100K tokens
        self._warnings_issued: set = set()    # avoid duplicate warnings

    # ── Session management ─────────────────────────────────────────────

    def start_session(self, session_id: str) -> None:
        with self._lock:
            self._current_session_id = session_id
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionUsage(session_id=session_id)

    def _get_current(self) -> Optional[SessionUsage]:
        if self._current_session_id is None:
            return None
        return self._sessions.get(self._current_session_id)

    # ── Record usage ───────────────────────────────────────────────────

    def record(self, prompt_tokens: int, completion_tokens: int,
               model: str = "", provider: str = "",
               reasoning_tokens: int = 0, latency_ms: float = 0.0) -> TurnUsage:
        """Record token usage for the current turn and return the TurnUsage."""
        turn = TurnUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            model=model,
            provider=provider,
            latency_ms=latency_ms,
        )
        with self._lock:
            session = self._get_current()
            if session is not None:
                session.add_turn(turn)
                self._check_warnings(session)
        return turn

    def _check_warnings(self, session: SessionUsage) -> None:
        """Issue warnings if thresholds are exceeded."""
        sid = session.session_id
        if sid in self._warnings_issued:
            return
        if session.total_cost_usd >= self.warn_cost_usd:
            logger.warning(
                f"Session {sid[:8]}... cost ${session.total_cost_usd:.2f} "
                f"exceeds ${self.warn_cost_usd:.2f} threshold"
            )
            self._warnings_issued.add(sid)
        elif session.total_tokens >= self.warn_tokens:
            logger.warning(
                f"Session {sid[:8]}... used {session.total_tokens:,} tokens "
                f"exceeds {self.warn_tokens:,} threshold"
            )
            self._warnings_issued.add(sid)

    # ── Query ──────────────────────────────────────────────────────────

    def get_current_summary(self) -> Dict:
        """Get a summary of the current session's usage."""
        session = self._get_current()
        if session is None:
            return {"active": False}
        return {
            "active": True,
            "session_id": session.session_id,
            "turns": session.turn_count,
            "prompt_tokens": session.total_prompt_tokens,
            "completion_tokens": session.total_completion_tokens,
            "total_tokens": session.total_tokens,
            "cost_usd": round(session.total_cost_usd, 4),
            "duration_minutes": round((time.time() - session.started_at) / 60, 1),
        }

    def get_all_sessions_summary(self) -> List[Dict]:
        """Get summaries for all tracked sessions."""
        with self._lock:
            return [
                {
                    "session_id": s.session_id,
                    "turns": s.turn_count,
                    "total_tokens": s.total_tokens,
                    "cost_usd": round(s.total_cost_usd, 4),
                }
                for s in self._sessions.values()
            ]

    def get_total_cost_all_sessions(self) -> float:
        with self._lock:
            return sum(s.total_cost_usd for s in self._sessions.values())

    def format_status_line(self) -> str:
        """Format a one-line status string for the CLI."""
        summary = self.get_current_summary()
        if not summary["active"]:
            return ""
        cost = summary["cost_usd"]
        tokens = summary["total_tokens"]
        turns = summary["turns"]
        parts = [f"T:{turns}"]
        if tokens >= 1000:
            parts.append(f"{tokens/1000:.0f}K tok")
        else:
            parts.append(f"{tokens} tok")
        if cost >= 0.01:
            parts.append(f"${cost:.2f}")
        elif cost > 0:
            parts.append(f"${cost:.4f}")
        return " | ".join(parts)

    def reset(self) -> None:
        """Reset all tracking data."""
        with self._lock:
            self._sessions.clear()
            self._warnings_issued.clear()
            self._current_session_id = None


# ── Singleton ──────────────────────────────────────────────────────────

_usage_tracker = UsageTracker()


def get_usage_tracker() -> UsageTracker:
    """Get the global usage tracker."""
    return _usage_tracker


def record_usage(prompt_tokens: int, completion_tokens: int,
                 model: str = "", provider: str = "",
                 reasoning_tokens: int = 0, latency_ms: float = 0.0) -> TurnUsage:
    """Convenience: record usage for the current turn."""
    return _usage_tracker.record(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model=model,
        provider=provider,
        reasoning_tokens=reasoning_tokens,
        latency_ms=latency_ms,
    )


def get_usage_summary() -> Dict:
    """Convenience: get current session usage summary."""
    return _usage_tracker.get_current_summary()
