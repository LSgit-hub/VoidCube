"""Keep pytest runtime state away from the user's active VoidCube home."""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from memai.host_integration import configure_mem_host_integration, get_mem_host_integration


_TEST_HOME = Path(tempfile.mkdtemp(prefix="voidcube-pytest-"))
os.environ["VOIDCUBE_HOME"] = str(_TEST_HOME)
_BASE_MEM_HOST_INTEGRATION = get_mem_host_integration()


@pytest.fixture(autouse=True)
def _restore_mem_host_integration():
    """Keep process-global Mem host callbacks isolated between tests."""
    try:
        yield
    finally:
        configure_mem_host_integration(_BASE_MEM_HOST_INTEGRATION)


@atexit.register
def _remove_test_home() -> None:
    shutil.rmtree(_TEST_HOME, ignore_errors=True)
