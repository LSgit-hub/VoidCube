from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.unit
@pytest.mark.parametrize(
    "module_name",
    [
        "voidcube.infrastructure.gateway.service_launcher",
        "voidcube.systems.supervisor.supervisor",
        "memai.application.memory_service",
    ],
)
def test_service_imports_do_not_install_console_logging_handler(module_name: str) -> None:
    """Service imports must not write directly into the interactive TUI."""
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root / "src"), str(repo_root / "Mem" / "src")]
    )
    script = (
        "import logging, importlib; "
        "importlib.import_module(__import__('sys').argv[1]); "
        "print(any(isinstance(handler, logging.StreamHandler) "
        "and not isinstance(handler, logging.FileHandler) "
        "for handler in logging.getLogger().handlers))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, module_name],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"
