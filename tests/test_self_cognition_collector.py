from __future__ import annotations

import ast
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from voidcube.systems.self_cognition import (
    JsonSelfCognitionRepository,
    SelfCognitionCollector,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]

NOW = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)
COMMIT = "a" * 40


def _git_runner(
    command: list[str],
    **_kwargs: object,
) -> subprocess.CompletedProcess[str]:
    output = COMMIT if command[1:] == ["rev-parse", "HEAD"] else "git version 2.50.0"
    return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")


def _build_project(root: Path) -> None:
    package = root / "src" / "voidcube"
    agent = package / "runtime" / "agent"
    systems = package / "systems"
    tools = package / "extensions" / "tools"
    cli = package / "interfaces" / "cli"
    for path in (package, agent, systems, tools, cli):
        path.mkdir(parents=True, exist_ok=True)
        (path / "__init__.py").write_text("", encoding="utf-8")
    (agent / "main.py").write_text(
        "import json\nfrom voidcube.systems.worker import run\n\n"
        "def lazy_import():\n    import pathlib\n",
        encoding="utf-8",
    )
    (systems / "worker.py").write_text(
        "def run():\n    return None\n",
        encoding="utf-8",
    )
    for directory in ("Mem", "memai", "tests", "skills"):
        (root / directory).mkdir()
    (root / "skills" / "source-a").mkdir()
    (cli / "root_launcher.py").write_text("", encoding="utf-8")
    (root / "config.yaml").write_text("runtime: local\n", encoding="utf-8")


def _collector(root: Path) -> SelfCognitionCollector:
    return SelfCognitionCollector(root, command_runner=_git_runner)


def test_collection_is_stable_at_a_fixed_time(tmp_path: Path):
    _build_project(tmp_path)
    collector = _collector(tmp_path)

    first = collector.collect(collected_at=NOW)
    second = collector.collect(collected_at=NOW)

    assert first == second
    assert first.snapshot_id == second.snapshot_id
    assert first.git_commit == COMMIT
    assert first.known_gaps == ()
    agent_main = next(
        item for item in first.modules if item.module == "voidcube.runtime.agent.main"
    )
    assert agent_main.dependencies == ("json", "voidcube")
    assert "pathlib" not in agent_main.dependencies


def test_safe_config_changes_snapshot_but_dotenv_does_not(tmp_path: Path):
    _build_project(tmp_path)
    collector = _collector(tmp_path)
    dotenv = tmp_path / ".env"
    dotenv.write_text("SECRET=first\n", encoding="utf-8")

    initial = collector.collect(collected_at=NOW)
    dotenv.write_text("SECRET=second\n", encoding="utf-8")
    after_dotenv = collector.collect(collected_at=NOW)
    (tmp_path / "config.yaml").write_text("runtime: remote\n", encoding="utf-8")
    after_config = collector.collect(collected_at=NOW)

    assert after_dotenv.config_digest == initial.config_digest
    assert after_dotenv.snapshot_id == initial.snapshot_id
    assert after_config.config_digest != initial.config_digest
    assert after_config.snapshot_id != initial.snapshot_id


def test_missing_roots_and_invalid_python_are_reported_as_gaps(tmp_path: Path):
    package = tmp_path / "src" / "voidcube"
    package.mkdir(parents=True)
    (package / "broken.py").write_text("def broken(:\n", encoding="utf-8")

    snapshot = _collector(tmp_path).collect(collected_at=NOW)

    assert "module_parse_failed:voidcube.broken" in snapshot.known_gaps
    assert "health_failed:configuration" in snapshot.known_gaps
    assert "Mem" in snapshot.uncovered_areas


def test_git_unavailable_is_reflected_in_revision_and_capability(tmp_path: Path):
    _build_project(tmp_path)

    def unavailable(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("git")

    snapshot = SelfCognitionCollector(
        tmp_path,
        command_runner=unavailable,
    ).collect(collected_at=NOW)

    git_capability = next(item for item in snapshot.capabilities if item.name == "git")
    assert snapshot.git_commit == "unknown"
    assert git_capability.available is False
    assert git_capability.version is None
    assert "git_commit_unavailable" in snapshot.known_gaps
    assert "command_unavailable:git" in snapshot.known_gaps


def test_collect_and_store_writes_snapshot_and_index(tmp_path: Path):
    project = tmp_path / "project"
    _build_project(project)
    repository_root = tmp_path / "state" / "self-cognition"
    repository = JsonSelfCognitionRepository(repository_root)

    snapshot = _collector(project).collect_and_store(
        repository,
        collected_at=NOW,
    )

    assert repository.get(snapshot.snapshot_id) == snapshot
    assert repository.list_ids() == (snapshot.snapshot_id,)
    assert (repository_root / "snapshots" / f"{snapshot.snapshot_id}.json").is_file()
    assert (repository_root / "index.json").is_file()


def test_collector_has_no_supervisor_or_legacy_store_imports():
    collector_path = (
        Path(__file__).parents[1]
        / "src"
        / "voidcube"
        / "systems"
        / "self_cognition"
        / "collector.py"
    )
    tree = ast.parse(collector_path.read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    assert not any(name.startswith("voidcube.systems.supervisor") for name in imports)
    assert "SelfLearningConclusionStore" not in collector_path.read_text(encoding="utf-8")
