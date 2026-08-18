#!/usr/bin/env python3
"""Thin composition root for the VoidCube CLI."""

from __future__ import annotations

from .entrypoints.dispatch import dispatch_cli
from .entrypoints.parser import build_parser


def main() -> None:
    dispatch_cli(build_parser())


if __name__ == "__main__":
    main()
