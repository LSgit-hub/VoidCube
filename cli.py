#!/usr/bin/env python3
"""Backward-compatible command-line entry point for VoidCube CLI.

The implementation lives in :mod:`VoidCube_cli.app`.  Keeping this module as
an alias preserves the public ``cli`` import while ensuring package code uses
the canonical CLI adapter module.
"""

from __future__ import annotations

import sys

from VoidCube_cli import app as _app


# Preserve module-level patching and legacy imports as one shared module object.
sys.modules[__name__] = _app


if __name__ == "__main__":
    import fire

    fire.Fire(_app.main)
