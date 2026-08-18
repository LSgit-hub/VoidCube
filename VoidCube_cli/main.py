"""Compatibility wrapper for the canonical CLI composition root."""

from __future__ import annotations

from VoidCube_cli.entrypoints.dispatch import dispatch_cli
from VoidCube_cli.entrypoints.parser import build_parser


def main() -> None:
    dispatch_cli(build_parser())


if __name__ == "__main__":
    main()
