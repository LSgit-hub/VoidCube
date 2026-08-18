"""Compatibility launcher for the canonical CLI root launcher."""

from __future__ import annotations

try:
    from voidcube.interfaces.cli.root_launcher import (
        _auto_start_daemons,
        _handle_daemon_lifecycle,
        _is_daemon_lifecycle_command,
        _is_fast_path,
        main as _canonical_main,
    )
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli.root_launcher import (
        _auto_start_daemons,
        _handle_daemon_lifecycle,
        _is_daemon_lifecycle_command,
        _is_fast_path,
        main as _canonical_main,
    )


def main(argv: list[str] | None = None) -> int:
    """Delegate legacy root invocation to the canonical launcher."""
    from VoidCube_cli.main import main as cli_main

    return _canonical_main(
        argv,
        cli_main=cli_main,
        auto_start_daemons=_auto_start_daemons,
    )


if __name__ == "__main__":
    import sys

    sys.exit(main())
