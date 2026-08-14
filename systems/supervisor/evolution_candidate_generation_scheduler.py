"""Non-blocking scheduling and observation for persisted candidate cycles."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from systems.evolution_candidate_generation import (
    EvolutionCandidateGenerationRepository,
    EvolutionCandidateGenerationRequest,
    EvolutionCandidateGenerationState,
)


JsonDict = dict[str, Any]
TriggerMode = Literal["automatic", "manual", "shadow"]
TERMINAL_BODY_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})


class CandidateGenerationExecutor(Protocol):
    async def __call__(
        self,
        request_id: str,
        *,
        lease_owner: str,
    ) -> Any: ...


class EvolutionCandidateGenerationScheduler:
    """Apply runtime gates, then start at most one background candidate cycle."""

    def __init__(
        self,
        *,
        repository: EvolutionCandidateGenerationRepository,
        execute: CandidateGenerationExecutor,
        automatic_enabled: Callable[[], bool],
        load_runtime_observation: Callable[[], Awaitable[JsonDict]],
        has_active_body_task: Callable[[], bool],
        clock: Callable[[], datetime] | None = None,
        lease_owner: str | None = None,
    ) -> None:
        self._repository = repository
        self._execute = execute
        self._automatic_enabled = automatic_enabled
        self._load_runtime_observation = load_runtime_observation
        self._has_active_body_task = has_active_body_task
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lease_owner = lease_owner or (
            f"supervisor-{os.getpid()}-{uuid.uuid4().hex[:12]}"
        )
        self._schedule_lock = asyncio.Lock()
        self._background_task: asyncio.Task[Any] | None = None
        self._latest_run: JsonDict = {}

    def register(
        self,
        request: EvolutionCandidateGenerationRequest,
        *,
        requested_at: datetime | None = None,
    ) -> JsonDict:
        state = self._repository.register(
            request,
            requested_at=requested_at or self._now(),
        )
        return {
            "status": "registered",
            "request_id": request.request_id,
            "state": self._state_summary(state),
        }

    def status(self) -> JsonDict:
        states = self._states()
        counts = {
            status: sum(state.status == status for state in states)
            for status in (
                "pending",
                "authoring",
                "evaluating",
                "authorized",
                "blocked",
                "failed",
            )
        }
        active_state = next(
            (
                state
                for state in states
                if state.status in {"authoring", "evaluating"}
                and state.lease_expires_at is not None
                and state.lease_expires_at > self._now()
            ),
            None,
        )
        if active_state is None:
            next_state, selection_reason = self._select_candidate(
                states,
                request_id=None,
            )
        else:
            next_state, selection_reason = None, "active_candidate_cycle"
        task = self._background_task
        return {
            "status": "ok",
            "automatic_enabled": bool(self._automatic_enabled()),
            "background_task_running": task is not None and not task.done(),
            "lease_owner": self._lease_owner,
            "counts": counts,
            "request_count": len(states),
            "active_cycle": (
                self._state_summary(active_state) if active_state is not None else None
            ),
            "next_candidate": (
                self._state_summary(next_state) if next_state is not None else None
            ),
            "selection_reason": selection_reason,
            "latest_run": dict(self._latest_run),
        }

    async def trigger(
        self,
        *,
        mode: TriggerMode,
        request_id: str | None = None,
    ) -> JsonDict:
        if mode not in {"automatic", "manual", "shadow"}:
            raise ValueError("mode must be automatic, manual, or shadow")
        normalized_request_id = str(request_id or "").strip() or None
        async with self._schedule_lock:
            return await self._trigger_locked(
                mode=mode,
                request_id=normalized_request_id,
            )

    async def cancel_active(self) -> None:
        task = self._background_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _trigger_locked(
        self,
        *,
        mode: TriggerMode,
        request_id: str | None,
    ) -> JsonDict:
        if mode == "automatic" and not self._automatic_enabled():
            return self._skipped(mode, "automatic_disabled")

        task = self._background_task
        if task is not None and not task.done():
            return self._skipped(mode, "active_candidate_cycle")

        states = self._states()
        active = next(
            (
                state
                for state in states
                if state.status in {"authoring", "evaluating"}
                and state.lease_expires_at is not None
                and state.lease_expires_at > self._now()
            ),
            None,
        )
        if active is not None:
            return self._skipped(
                mode,
                "active_candidate_cycle",
                request_id=active.request_id,
            )

        selected, reason = self._select_candidate(states, request_id=request_id)
        if selected is None:
            return self._skipped(mode, reason)

        try:
            if self._has_active_body_task():
                return self._skipped(
                    mode,
                    "active_body_task",
                    request_id=selected.request_id,
                )
        except Exception as exc:
            return self._skipped(
                mode,
                "body_task_observation_unavailable",
                request_id=selected.request_id,
                error_code=type(exc).__name__,
            )

        activity_gate = await self._activity_gate()
        if activity_gate is not None:
            return self._skipped(
                mode,
                activity_gate[0],
                request_id=selected.request_id,
                error_code=activity_gate[1],
            )

        if mode == "shadow":
            return {
                "status": "shadow_ready",
                "mode": mode,
                "would_start": True,
                "request_id": selected.request_id,
                "automatic_enabled": bool(self._automatic_enabled()),
            }

        started_at = self._now()
        self._latest_run = {
            "status": "running",
            "mode": mode,
            "request_id": selected.request_id,
            "started_at": started_at.isoformat(),
            "finished_at": None,
            "error_code": None,
        }
        self._background_task = asyncio.create_task(
            self._run_candidate(selected.request_id, mode=mode),
            name=f"evolution-candidate-generation:{selected.request_id}",
        )
        return {
            "status": "started",
            "mode": mode,
            "request_id": selected.request_id,
            "started_at": started_at.isoformat(),
        }

    async def _run_candidate(self, request_id: str, *, mode: TriggerMode) -> None:
        try:
            outcome = await self._execute(
                request_id,
                lease_owner=self._lease_owner,
            )
            state = outcome.state
            self._latest_run = {
                **self._latest_run,
                "status": "completed",
                "mode": mode,
                "request_id": request_id,
                "finished_at": self._now().isoformat(),
                "result_state": state.status,
                "attempt_id": state.attempt_id,
                "authoring_task_id": state.authoring_task_id,
                "error_code": state.error_code,
            }
        except asyncio.CancelledError:
            self._latest_run = {
                **self._latest_run,
                "status": "cancelled",
                "finished_at": self._now().isoformat(),
                "error_code": "background_task_cancelled",
            }
            raise
        except Exception as exc:
            self._latest_run = {
                **self._latest_run,
                "status": "error",
                "finished_at": self._now().isoformat(),
                "error_code": type(exc).__name__,
            }

    async def _activity_gate(self) -> tuple[str, str | None] | None:
        try:
            payload = await self._load_runtime_observation()
        except Exception as exc:
            return "runtime_observation_unavailable", type(exc).__name__
        observation = dict(payload.get("observation_input") or payload)
        activity = dict(observation.get("activity") or {})
        signal = dict(observation.get("user_chain_signal") or {})
        try:
            active_sessions = max(0, int(activity.get("active_sessions") or 0))
        except (TypeError, ValueError):
            return "runtime_observation_invalid", "invalid_active_sessions"
        is_quiet = signal.get("is_quiet")
        if active_sessions > 0 or is_quiet is not True:
            return "user_service_active", None
        return None

    def _states(self) -> list[EvolutionCandidateGenerationState]:
        states = [
            state
            for request_id in self._repository.list_request_ids()
            if (state := self._repository.get_current_state(request_id)) is not None
        ]
        return sorted(states, key=lambda state: (state.requested_at, state.request_id))

    def _select_candidate(
        self,
        states: list[EvolutionCandidateGenerationState],
        *,
        request_id: str | None,
    ) -> tuple[EvolutionCandidateGenerationState | None, str]:
        if request_id is not None:
            selected = next(
                (state for state in states if state.request_id == request_id),
                None,
            )
            if selected is None:
                return None, "unknown_request"
            return self._eligible_state(selected)

        cooling = False
        for state in states:
            eligible, reason = self._eligible_state(state)
            if eligible is not None:
                return eligible, "candidate_ready"
            cooling = cooling or reason == "candidate_cooldown"
        if cooling:
            return None, "candidate_cooldown"
        return None, "no_pending_candidate"

    def _eligible_state(
        self,
        state: EvolutionCandidateGenerationState,
    ) -> tuple[EvolutionCandidateGenerationState | None, str]:
        now = self._now()
        if state.status == "pending":
            return state, "candidate_ready"
        if state.status in {"authoring", "evaluating"}:
            if state.lease_expires_at is not None and state.lease_expires_at <= now:
                return state, "candidate_ready"
            return None, "active_candidate_cycle"
        if state.status in {"blocked", "failed"}:
            if state.cooldown_until is None or state.cooldown_until <= now:
                return state, "candidate_ready"
            return None, "candidate_cooldown"
        return None, "candidate_already_authorized"

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("candidate scheduler clock must be timezone-aware")
        return value

    @staticmethod
    def _state_summary(state: EvolutionCandidateGenerationState) -> JsonDict:
        return {
            "request_id": state.request_id,
            "state_id": state.state_id,
            "status": state.status,
            "attempt_number": state.attempt_number,
            "attempt_id": state.attempt_id,
            "authoring_task_id": state.authoring_task_id,
            "experiment_result_id": state.experiment_result_id,
            "lease_expires_at": (
                state.lease_expires_at.isoformat() if state.lease_expires_at else None
            ),
            "cooldown_until": (
                state.cooldown_until.isoformat() if state.cooldown_until else None
            ),
            "error_code": state.error_code,
            "requested_at": state.requested_at.isoformat(),
            "updated_at": state.updated_at.isoformat(),
        }

    @staticmethod
    def _skipped(
        mode: TriggerMode,
        reason: str,
        *,
        request_id: str | None = None,
        error_code: str | None = None,
    ) -> JsonDict:
        return {
            "status": "shadow_blocked" if mode == "shadow" else "skipped",
            "mode": mode,
            "would_start": False if mode == "shadow" else None,
            "reason": reason,
            "request_id": request_id,
            "error_code": error_code,
        }


__all__ = [
    "EvolutionCandidateGenerationScheduler",
    "TERMINAL_BODY_TASK_STATUSES",
    "TriggerMode",
]
