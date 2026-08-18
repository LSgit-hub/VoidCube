"""Deterministic platform selection for evolution benchmark plans."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Iterable, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..evolution_boundary import normalize_repo_path


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_PLATFORM_ORDER = ("linux", "windows")
_DEPENDENCY_NAMES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "poetry.lock",
    "uv.lock",
}
_CONTAINER_MARKERS = (
    "containerfile",
    "dockerfile",
    "podman",
    "src/voidcube/infrastructure/execution/environments/",
)
_WINDOWS_MARKERS = (
    ".bat",
    ".cmd",
    ".ps1",
    ".sln",
    ".vcxproj",
    ".pyd",
    ".dll",
    "windows_host_executor.py",
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class _BenchmarkPlatformSelectionContent(_FrozenModel):
    schema_version: Literal[1] = 1
    changed_files: tuple[str, ...] = Field(min_length=1)
    dependency_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    required_platforms: tuple[str, ...] = Field(min_length=1)
    reason_codes: tuple[str, ...] = Field(min_length=1)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value

    @field_validator("changed_files")
    @classmethod
    def _normalize_changed_files(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({normalize_repo_path(item) for item in values}))
        if not normalized or any(not item for item in normalized):
            raise ValueError("changed_files must contain normalized repository paths")
        return normalized

    @model_validator(mode="after")
    def _validate_selection(self) -> Self:
        if any(item not in _PLATFORM_ORDER for item in self.required_platforms):
            raise ValueError("required_platforms contains an unsupported platform")
        expected_platforms = tuple(
            item for item in _PLATFORM_ORDER if item in set(self.required_platforms)
        )
        if expected_platforms != self.required_platforms:
            raise ValueError("required_platforms must be unique and use canonical order")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("reason_codes must be unique")
        return self


class BenchmarkPlatformSelection(_BenchmarkPlatformSelectionContent):
    selection_id: str = Field(min_length=1)
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @classmethod
    def create(cls, **values: object) -> Self:
        content = _BenchmarkPlatformSelectionContent.model_validate(values)
        payload = content.model_dump(mode="json")
        digest = _content_hash(payload)
        return cls.model_validate(
            {
                **payload,
                "selection_id": f"benchmark-platform-selection-{digest}",
                "content_hash": digest,
            }
        )

    def content_payload(self) -> dict[str, object]:
        return _BenchmarkPlatformSelectionContent.model_validate(
            self.model_dump(exclude={"selection_id", "content_hash"})
        ).model_dump(mode="json")

    @model_validator(mode="after")
    def _validate_content_address(self) -> Self:
        digest = _content_hash(self.content_payload())
        if self.content_hash != digest:
            raise ValueError("content_hash does not match platform selection")
        if self.selection_id != f"benchmark-platform-selection-{digest}":
            raise ValueError("selection_id does not match content_hash")
        return self


def select_benchmark_platforms(
    changed_files: Iterable[str],
    dependency_fingerprint: str,
    *,
    created_at: datetime,
) -> BenchmarkPlatformSelection:
    normalized = tuple(sorted({normalize_repo_path(item) for item in changed_files}))
    if not normalized or any(not item for item in normalized):
        raise ValueError("changed_files must contain at least one repository path")
    reasons = ["project_default_windows"]
    required = {"windows"}
    names = {path.rsplit("/", 1)[-1].lower() for path in normalized}
    if names & _DEPENDENCY_NAMES:
        required.add("linux")
        reasons.append("dependency_declaration_changed")
    if any(
        marker in path.lower()
        for path in normalized
        for marker in _CONTAINER_MARKERS
    ):
        required.add("linux")
        reasons.append("container_runtime_changed")
    if any(
        path.lower().endswith(marker) or marker in path.lower()
        for path in normalized
        for marker in _WINDOWS_MARKERS
    ):
        reasons.append("windows_runtime_changed")
    ordered_platforms = tuple(item for item in _PLATFORM_ORDER if item in required)
    return BenchmarkPlatformSelection.create(
        changed_files=normalized,
        dependency_fingerprint=dependency_fingerprint,
        required_platforms=ordered_platforms,
        reason_codes=tuple(reasons),
        created_at=created_at,
    )


def _content_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["BenchmarkPlatformSelection", "select_benchmark_platforms"]
