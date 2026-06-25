from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence


AGENT_EVOLUTION_ALLOWED_PATHS: tuple[str, ...] = (
    "agent/",
    "systems/agent/",
    "tools/",
    "skills/",
    "presets/",
)

AGENT_EVOLUTION_ALLOWED_FILES: tuple[str, ...] = (
    "run_agent.py",
)

MOTHER_SYSTEM_FORBIDDEN_PATHS: tuple[str, ...] = (
    "VoidCube_cli/",
    "VoidCube_core/",
    "Mem/",
    "plugins/memory/",
    "systems/supervisor/",
    "systems/execution/",
    "systems/gateway/",
    "systems/memory/",
    "systems/self_learning/",
    "docs/",
    "tests/",
)

MOTHER_SYSTEM_FORBIDDEN_FILES: tuple[str, ...] = (
    "cli.py",
    "config.yaml",
    "pyproject.toml",
    "pytest.ini",
    "requirements.txt",
    "systems/__init__.py",
    "systems/architecture.md",
    "systems/body_registry.py",
    "systems/config.py",
    "systems/governor.py",
    "systems/lifecycle.py",
    "systems/probe.py",
)

@dataclass(frozen=True)
class EvolutionBoundaryReport:
    changed_files: List[str]
    allowed_files: List[str]
    forbidden_files: List[str]
    unknown_files: List[str]
    score: float = 0.0

    @property
    def violations(self) -> List[str]:
        return [*self.forbidden_files, *self.unknown_files]

    @property
    def ok(self) -> bool:
        return not self.violations

    def model_dump(self) -> dict:
        return {
            "changed_files": self.changed_files,
            "allowed_files": self.allowed_files,
            "forbidden_files": self.forbidden_files,
            "unknown_files": self.unknown_files,
            "violations": self.violations,
            "ok": self.ok,
            "score": self.score,
        }


def normalize_repo_path(path: str) -> str:
    normalized = str(path).strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _matches(path: str, *, prefixes: Sequence[str], files: Sequence[str]) -> bool:
    return path in files or any(path.startswith(prefix) for prefix in prefixes)


def classify_agent_evolution_changes(changed_files: Iterable[str]) -> EvolutionBoundaryReport:
    normalized_files = []
    seen = set()
    for raw_path in changed_files:
        path = normalize_repo_path(str(raw_path))
        if not path or path in seen:
            continue
        seen.add(path)
        normalized_files.append(path)

    allowed_files = []
    forbidden_files = []
    unknown_files = []
    for path in normalized_files:
        if ".." in path.split("/"):
            forbidden_files.append(path)
        elif _matches(
            path,
            prefixes=MOTHER_SYSTEM_FORBIDDEN_PATHS,
            files=MOTHER_SYSTEM_FORBIDDEN_FILES,
        ):
            forbidden_files.append(path)
        elif _matches(
            path,
            prefixes=AGENT_EVOLUTION_ALLOWED_PATHS,
            files=AGENT_EVOLUTION_ALLOWED_FILES,
        ):
            allowed_files.append(path)
        else:
            unknown_files.append(path)

    total_files = len(normalized_files)
    if total_files == 0:
        score = 0.0
    else:
        allowed_ratio = len(allowed_files) / total_files
        forbidden_penalty = len(forbidden_files) / total_files * 0.5
        unknown_penalty = len(unknown_files) / total_files * 0.3
        raw_score = max(0.0, allowed_ratio - forbidden_penalty - unknown_penalty)
        score = raw_score * 20.0

    return EvolutionBoundaryReport(
        changed_files=normalized_files,
        allowed_files=allowed_files,
        forbidden_files=forbidden_files,
        unknown_files=unknown_files,
        score=score,
    )


def validate_agent_evolution_changes(changed_files: Iterable[str]) -> EvolutionBoundaryReport:
    report = classify_agent_evolution_changes(changed_files)
    if report.ok:
        return report

    violations = ", ".join(report.violations)
    allowed = ", ".join([*AGENT_EVOLUTION_ALLOWED_PATHS, *AGENT_EVOLUTION_ALLOWED_FILES])
    raise ValueError(
        "Body self-evolution changed files outside the child-agent boundary: "
        f"{violations}. Allowed child-agent paths are: {allowed}. "
        "VoidCube mother-system changes are maintained by developers, not by body self-evolution."
    )
