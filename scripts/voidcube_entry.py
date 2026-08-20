"""Nuitka entry point for the bundled VoidCube executable.

Compiled with Nuitka so the single ``voidcube.exe`` serves both the terminal
CLI (default) and the desktop service-control protocol
(``--desktop-control <action>``, dispatched by ``root_launcher.main``).
"""

from voidcube.interfaces.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
