from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

pytestmark = [pytest.mark.smoke, pytest.mark.unit]

ACTIVE_DOCS = {
    "mem-integration-contract.md",
    "mem-temporary-memory-contract.md",
    "memory-resource-contract.md",
    "operations.md",
    "release.md",
    "testing.md",
    "troubleshooting.md",
}

MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
RETIRED_TERMS = (
    "agent-pull",
    "cli_agent_pull",
    "API-A 自主执行",
    "API-A 可以 claim",
)


def _mainline_markdown_files() -> list[Path]:
    return [ROOT / "README.md", ROOT / "ARCHITECTURE.md", *sorted(DOCS.glob("*.md"))]


def _local_link_target(source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip().strip("<>")
    if not target or target.startswith("#") or re.match(r"^[a-z][a-z0-9+.-]*:", target, flags=re.IGNORECASE):
        return None
    target = unquote(target.split("#", 1)[0]).strip()
    return (source.parent / target).resolve() if target else None


def test_docs_directory_contains_active_documents() -> None:
    assert {path.name for path in DOCS.glob("*.md")} == ACTIVE_DOCS


def test_mainline_markdown_links_resolve() -> None:
    broken = []
    for source in _mainline_markdown_files():
        for raw_target in MARKDOWN_LINK.findall(source.read_text(encoding="utf-8")):
            target = _local_link_target(source, raw_target)
            if target is not None and not target.exists():
                broken.append(f"{source.relative_to(ROOT).as_posix()} -> {raw_target}")
    assert broken == []


def test_docs_describe_employee_execution_baseline() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in _mainline_markdown_files())

    assert "API-B" in combined
    assert "员工" in combined
    assert "API-A 只" in combined
    assert all(term not in combined for term in RETIRED_TERMS)
