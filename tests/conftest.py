"""Keep pytest runtime state away from the user's active VoidCube home."""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path


_TEST_HOME = Path(tempfile.mkdtemp(prefix="voidcube-pytest-"))
os.environ["VOIDCUBE_HOME"] = str(_TEST_HOME)


@atexit.register
def _remove_test_home() -> None:
    shutil.rmtree(_TEST_HOME, ignore_errors=True)
