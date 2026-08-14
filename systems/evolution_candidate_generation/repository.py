"""Atomic persistence and leases for evolution candidate generation cycles."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import ValidationError

from VoidCube_core.utils import atomic_json_write, interprocess_file_lock
from systems.evolution_candidate_generation.models import (
    EvolutionCandidateGenerationRequest,
    EvolutionCandidateGenerationState,
    attempt_identity,
)


INDEX_SCHEMA_VERSION = 1
_REQUEST_ID_RE = re.compile(r"^evolution-candidate-request-[0-9a-f]{64}$")
_STATE_ID_RE = re.compile(r"^evolution-candidate-state-[0-9a-f]{64}$")


class EvolutionCandidateGenerationRepositoryError(RuntimeError):
    pass


class EvolutionCandidateGenerationRecordCorrupted(
    EvolutionCandidateGenerationRepositoryError
):
    pass


class EvolutionCandidateGenerationImmutableConflict(
    EvolutionCandidateGenerationRepositoryError
):
    pass


class EvolutionCandidateGenerationTransitionRejected(
    EvolutionCandidateGenerationRepositoryError
):
    pass


class EvolutionCandidateGenerationRepository(Protocol):
    def register(
        self,
        request: EvolutionCandidateGenerationRequest,
        *,
        requested_at: datetime,
    ) -> EvolutionCandidateGenerationState: ...

    def get_request(
        self, request_id: str
    ) -> EvolutionCandidateGenerationRequest | None: ...

    def get_current_state(
        self, request_id: str
    ) -> EvolutionCandidateGenerationState | None: ...

    def list_request_ids(self) -> tuple[str, ...]: ...

    def recover_evaluation(
        self,
        request_id: str,
        *,
        attempt_id: str,
        authoring_result_id: str,
        lease_owner: str,
        resumed_at: datetime,
        lease_expires_at: datetime,
    ) -> EvolutionCandidateGenerationState: ...


class JsonEvolutionCandidateGenerationRepository:
    """Store immutable requests and append-only state snapshots with a current index."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.requests_root = self.root / "requests"
        self.states_root = self.root / "states"
        self.index_path = self.root / "index.json"
        self.lock_path = self.root / ".repository.lock"

    def register(
        self,
        request: EvolutionCandidateGenerationRequest,
        *,
        requested_at: datetime,
    ) -> EvolutionCandidateGenerationState:
        validated = EvolutionCandidateGenerationRequest.model_validate(
            request.model_dump(mode="json")
        )
        initial = EvolutionCandidateGenerationState.create(
            request_id=validated.request_id,
            revision=0,
            status="pending",
            requested_at=requested_at,
            updated_at=requested_at,
        )
        with interprocess_file_lock(self.lock_path):
            request_path = self._request_path(validated.request_id)
            if request_path.exists():
                existing = self._read_request(request_path)
                if existing != validated:
                    raise EvolutionCandidateGenerationImmutableConflict(
                        f"candidate request {validated.request_id} has different content"
                    )
                current = self._get_current_state_unlocked(validated.request_id)
                if current is None:
                    current = self._find_initial_state_unlocked(validated.request_id)
                    if current is None:
                        self._write_state(initial)
                        current = initial
                    index = self._read_index()
                    index["cycles"][validated.request_id] = current.state_id
                    self._write_index(index)
                return current
            self._write_state(initial)
            self._write_immutable(request_path, validated.model_dump(mode="json"))
            index = self._read_index()
            index["cycles"][validated.request_id] = initial.state_id
            self._write_index(index)
        return initial

    def get_request(
        self, request_id: str
    ) -> EvolutionCandidateGenerationRequest | None:
        path = self._request_path(request_id)
        return self._read_request(path) if path.exists() else None

    def get_state(self, state_id: str) -> EvolutionCandidateGenerationState | None:
        path = self._state_path(state_id)
        return self._read_state(path) if path.exists() else None

    def get_current_state(
        self, request_id: str
    ) -> EvolutionCandidateGenerationState | None:
        return self._get_current_state_unlocked(request_id)

    def list_request_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._read_index()["cycles"]))

    def state_history(
        self, request_id: str
    ) -> tuple[EvolutionCandidateGenerationState, ...]:
        current = self.get_current_state(request_id)
        if current is None:
            return ()
        history = [current]
        seen = {current.state_id}
        while current.previous_state_id:
            previous = self.get_state(current.previous_state_id)
            if previous is None or previous.state_id in seen:
                raise EvolutionCandidateGenerationRecordCorrupted(
                    f"broken candidate state history for {request_id}"
                )
            if (
                previous.request_id != request_id
                or previous.revision + 1 != current.revision
            ):
                raise EvolutionCandidateGenerationRecordCorrupted(
                    f"invalid candidate state history for {request_id}"
                )
            history.append(previous)
            seen.add(previous.state_id)
            current = previous
        return tuple(reversed(history))

    def claim_authoring(
        self,
        request_id: str,
        *,
        lease_owner: str,
        claimed_at: datetime,
        lease_expires_at: datetime,
    ) -> EvolutionCandidateGenerationState | None:
        if not lease_owner.strip():
            raise ValueError("lease_owner must not be empty")
        if lease_expires_at <= claimed_at:
            raise ValueError("lease_expires_at must follow claimed_at")
        with interprocess_file_lock(self.lock_path):
            current = self._require_current_unlocked(request_id)
            claimable = current.status == "pending"
            claimable = claimable or (
                current.status == "authoring"
                and current.lease_expires_at is not None
                and current.lease_expires_at <= claimed_at
            )
            claimable = claimable or (
                current.status in {"blocked", "failed"}
                and (
                    current.cooldown_until is None
                    or current.cooldown_until <= claimed_at
                )
            )
            if not claimable:
                return None
            attempt_number = current.attempt_number + 1
            attempt_id, task_id = attempt_identity(request_id, attempt_number)
            return self._transition_unlocked(
                current,
                status="authoring",
                attempt_number=attempt_number,
                attempt_id=attempt_id,
                authoring_task_id=task_id,
                authoring_result_id=None,
                experiment_result_id=None,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
                cooldown_until=None,
                error_code=None,
                error_reason=None,
                updated_at=claimed_at,
            )

    def begin_evaluation(
        self,
        request_id: str,
        *,
        attempt_id: str,
        authoring_result_id: str,
        lease_owner: str,
        started_at: datetime,
        lease_expires_at: datetime,
    ) -> EvolutionCandidateGenerationState:
        if lease_expires_at <= started_at:
            raise ValueError("lease_expires_at must follow started_at")
        with interprocess_file_lock(self.lock_path):
            current = self._require_attempt_unlocked(
                request_id,
                attempt_id=attempt_id,
                expected_status="authoring",
            )
            if current.lease_owner != lease_owner:
                raise EvolutionCandidateGenerationTransitionRejected(
                    "only the authoring lease owner may begin evaluation"
                )
            return self._transition_unlocked(
                current,
                status="evaluating",
                authoring_result_id=authoring_result_id,
                lease_expires_at=lease_expires_at,
                updated_at=started_at,
            )

    def recover_evaluation(
        self,
        request_id: str,
        *,
        attempt_id: str,
        authoring_result_id: str,
        lease_owner: str,
        resumed_at: datetime,
        lease_expires_at: datetime,
    ) -> EvolutionCandidateGenerationState:
        """Resume an expired authoring attempt whose immutable result was persisted."""

        if not lease_owner.strip():
            raise ValueError("lease_owner must not be empty")
        if lease_expires_at <= resumed_at:
            raise ValueError("lease_expires_at must follow resumed_at")
        with interprocess_file_lock(self.lock_path):
            current = self._require_attempt_unlocked(
                request_id,
                attempt_id=attempt_id,
                expected_status="authoring",
            )
            if current.lease_expires_at is None or current.lease_expires_at > resumed_at:
                raise EvolutionCandidateGenerationTransitionRejected(
                    "authoring lease has not expired"
                )
            return self._transition_unlocked(
                current,
                status="evaluating",
                authoring_result_id=authoring_result_id,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
                updated_at=resumed_at,
            )

    def claim_evaluation(
        self,
        request_id: str,
        *,
        lease_owner: str,
        claimed_at: datetime,
        lease_expires_at: datetime,
    ) -> EvolutionCandidateGenerationState | None:
        if not lease_owner.strip():
            raise ValueError("lease_owner must not be empty")
        if lease_expires_at <= claimed_at:
            raise ValueError("lease_expires_at must follow claimed_at")
        with interprocess_file_lock(self.lock_path):
            current = self._require_current_unlocked(request_id)
            if (
                current.status != "evaluating"
                or current.lease_expires_at is None
                or current.lease_expires_at > claimed_at
            ):
                return None
            return self._transition_unlocked(
                current,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
                updated_at=claimed_at,
            )

    def renew_lease(
        self,
        request_id: str,
        *,
        attempt_id: str,
        lease_owner: str,
        renewed_at: datetime,
        lease_expires_at: datetime,
    ) -> EvolutionCandidateGenerationState:
        if lease_expires_at <= renewed_at:
            raise ValueError("lease_expires_at must follow renewed_at")
        with interprocess_file_lock(self.lock_path):
            current = self._require_current_unlocked(request_id)
            if (
                current.status not in {"authoring", "evaluating"}
                or current.attempt_id != attempt_id
                or current.lease_owner != lease_owner
            ):
                raise EvolutionCandidateGenerationTransitionRejected(
                    "candidate attempt does not own the active lease"
                )
            return self._transition_unlocked(
                current,
                lease_expires_at=lease_expires_at,
                updated_at=renewed_at,
            )

    def mark_authorized(
        self,
        request_id: str,
        *,
        attempt_id: str,
        experiment_result_id: str,
        completed_at: datetime,
    ) -> EvolutionCandidateGenerationState:
        with interprocess_file_lock(self.lock_path):
            current = self._require_attempt_unlocked(
                request_id,
                attempt_id=attempt_id,
                expected_status="evaluating",
            )
            return self._transition_unlocked(
                current,
                status="authorized",
                experiment_result_id=experiment_result_id,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=completed_at,
            )

    def mark_failure(
        self,
        request_id: str,
        *,
        attempt_id: str,
        status: Literal["blocked", "failed"],
        error_code: str,
        error_reason: str,
        completed_at: datetime,
        cooldown_until: datetime | None = None,
        authoring_result_id: str | None = None,
        experiment_result_id: str | None = None,
    ) -> EvolutionCandidateGenerationState:
        if not error_code.strip() or not error_reason.strip():
            raise ValueError("failure requires error_code and error_reason")
        with interprocess_file_lock(self.lock_path):
            current = self._require_current_unlocked(request_id)
            if current.status not in {"authoring", "evaluating"}:
                raise EvolutionCandidateGenerationTransitionRejected(
                    f"cannot fail candidate cycle from {current.status}"
                )
            if current.attempt_id != attempt_id:
                raise EvolutionCandidateGenerationTransitionRejected(
                    "candidate attempt does not own the current state"
                )
            result_id = authoring_result_id or current.authoring_result_id
            return self._transition_unlocked(
                current,
                status=status,
                authoring_result_id=result_id,
                experiment_result_id=experiment_result_id,
                lease_owner=None,
                lease_expires_at=None,
                cooldown_until=cooldown_until,
                error_code=error_code,
                error_reason=error_reason,
                updated_at=completed_at,
            )

    def _transition_unlocked(
        self,
        current: EvolutionCandidateGenerationState,
        **changes: object,
    ) -> EvolutionCandidateGenerationState:
        updated_at = changes.get("updated_at")
        if not isinstance(updated_at, datetime) or updated_at < current.updated_at:
            raise EvolutionCandidateGenerationTransitionRejected(
                "candidate transition time cannot move backwards"
            )
        payload = current.content_payload()
        payload.update(changes)
        payload.update(
            previous_state_id=current.state_id,
            revision=current.revision + 1,
        )
        next_state = EvolutionCandidateGenerationState.create(**payload)
        self._write_state(next_state)
        index = self._read_index()
        if index["cycles"].get(current.request_id) != current.state_id:
            raise EvolutionCandidateGenerationTransitionRejected(
                "candidate cycle current state changed during transition"
            )
        index["cycles"][current.request_id] = next_state.state_id
        self._write_index(index)
        return next_state

    def _require_current_unlocked(
        self, request_id: str
    ) -> EvolutionCandidateGenerationState:
        current = self._get_current_state_unlocked(request_id)
        if current is None:
            raise KeyError(f"unknown candidate request: {request_id}")
        return current

    def _require_attempt_unlocked(
        self,
        request_id: str,
        *,
        attempt_id: str,
        expected_status: str,
    ) -> EvolutionCandidateGenerationState:
        current = self._require_current_unlocked(request_id)
        if current.status != expected_status or current.attempt_id != attempt_id:
            raise EvolutionCandidateGenerationTransitionRejected(
                "candidate attempt does not own the expected current state"
            )
        return current

    def _get_current_state_unlocked(
        self, request_id: str
    ) -> EvolutionCandidateGenerationState | None:
        request_path = self._request_path(request_id)
        state_id = self._read_index()["cycles"].get(request_id)
        if state_id is None:
            return None
        if not request_path.exists():
            raise EvolutionCandidateGenerationRecordCorrupted(
                f"missing candidate request {request_id}"
            )
        request = self._read_request(request_path)
        if request.request_id != request_id:
            raise EvolutionCandidateGenerationRecordCorrupted(
                f"candidate request record does not match {request_id}"
            )
        state_path = self._state_path(state_id)
        if not state_path.exists():
            raise EvolutionCandidateGenerationRecordCorrupted(
                f"missing current state {state_id} for {request_id}"
            )
        state = self._read_state(state_path)
        if state.request_id != request_id:
            raise EvolutionCandidateGenerationRecordCorrupted(
                f"current state {state_id} belongs to another request"
            )
        return state

    def _write_state(self, state: EvolutionCandidateGenerationState) -> None:
        path = self._state_path(state.state_id)
        if path.exists():
            existing = self._read_state(path)
            if existing != state:
                raise EvolutionCandidateGenerationImmutableConflict(
                    f"candidate state {state.state_id} has different content"
                )
            return
        self._write_immutable(path, state.model_dump(mode="json"))

    def _find_initial_state_unlocked(
        self, request_id: str
    ) -> EvolutionCandidateGenerationState | None:
        if not self.states_root.exists():
            return None
        matches = []
        for path in self.states_root.glob("evolution-candidate-state-*.json"):
            state = self._read_state(path)
            if state.request_id == request_id and state.revision == 0:
                matches.append(state)
        if len(matches) > 1:
            raise EvolutionCandidateGenerationRecordCorrupted(
                f"candidate request {request_id} has multiple initial states"
            )
        return matches[0] if matches else None

    @staticmethod
    def _write_immutable(path: Path, payload: dict[str, object]) -> None:
        atomic_json_write(path, payload, sort_keys=True)

    def _request_path(self, request_id: str) -> Path:
        if _REQUEST_ID_RE.fullmatch(str(request_id or "")) is None:
            raise ValueError("invalid candidate request ID")
        return self.requests_root / f"{request_id}.json"

    def _state_path(self, state_id: str) -> Path:
        if _STATE_ID_RE.fullmatch(str(state_id or "")) is None:
            raise ValueError("invalid candidate state ID")
        return self.states_root / f"{state_id}.json"

    def _read_request(self, path: Path) -> EvolutionCandidateGenerationRequest:
        return self._read_model(path, EvolutionCandidateGenerationRequest)

    def _read_state(self, path: Path) -> EvolutionCandidateGenerationState:
        return self._read_model(path, EvolutionCandidateGenerationState)

    @staticmethod
    def _read_model(path: Path, model_type):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return model_type.model_validate(payload)
        except (
            OSError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValidationError,
        ) as exc:
            raise EvolutionCandidateGenerationRecordCorrupted(
                f"invalid candidate generation record: {path}"
            ) from exc

    def _read_index(self) -> dict[str, object]:
        if not self.index_path.exists():
            return {"schema_version": INDEX_SCHEMA_VERSION, "cycles": {}}
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("index must be an object")
            if payload.get("schema_version") != INDEX_SCHEMA_VERSION:
                raise ValueError("unsupported index schema_version")
            cycles = payload.get("cycles")
            if not isinstance(cycles, dict):
                raise ValueError("index cycles must be an object")
            normalized: dict[str, str] = {}
            for request_id, state_id in cycles.items():
                self._request_path(str(request_id))
                self._state_path(str(state_id))
                normalized[str(request_id)] = str(state_id)
            return {"schema_version": INDEX_SCHEMA_VERSION, "cycles": normalized}
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise EvolutionCandidateGenerationRecordCorrupted(
                f"invalid candidate generation index: {self.index_path}"
            ) from exc

    def _write_index(self, index: dict[str, object]) -> None:
        cycles = dict(index["cycles"])
        atomic_json_write(
            self.index_path,
            {
                "schema_version": INDEX_SCHEMA_VERSION,
                "cycles": {key: cycles[key] for key in sorted(cycles)},
            },
            sort_keys=True,
        )


__all__ = [
    "EvolutionCandidateGenerationImmutableConflict",
    "EvolutionCandidateGenerationRecordCorrupted",
    "EvolutionCandidateGenerationRepository",
    "EvolutionCandidateGenerationRepositoryError",
    "EvolutionCandidateGenerationTransitionRejected",
    "JsonEvolutionCandidateGenerationRepository",
]
