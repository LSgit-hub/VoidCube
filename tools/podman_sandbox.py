"""Build and inspect the packaged VoidCube Podman sandbox image."""

from __future__ import annotations

import argparse
from importlib.resources import as_file, files
import shutil
import subprocess
from typing import Sequence


DEFAULT_IMAGE = "localhost/voidcube-podman-local:latest"
CONTAINERFILE_RESOURCE = "containerfiles/podman-agent.Containerfile"


def find_podman() -> str:
    executable = shutil.which("podman")
    if not executable:
        raise RuntimeError("podman is not installed or is not available on PATH")
    return executable


def image_exists(image: str = DEFAULT_IMAGE, *, executable: str | None = None) -> bool:
    podman = executable or find_podman()
    result = subprocess.run(
        [podman, "image", "exists", image],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def build_image(image: str = DEFAULT_IMAGE, *, executable: str | None = None) -> None:
    podman = executable or find_podman()
    resource = files("tools").joinpath(CONTAINERFILE_RESOURCE)
    with as_file(resource) as containerfile:
        subprocess.run(
            [
                podman,
                "build",
                "--tag",
                image,
                "--file",
                str(containerfile),
                str(containerfile.parent),
            ],
            check=True,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "status"))
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        podman = find_podman()
        if args.action == "build":
            build_image(args.image, executable=podman)
            print(f"Podman sandbox image ready: {args.image}")
            return 0

        if image_exists(args.image, executable=podman):
            print(f"Podman sandbox image ready: {args.image}")
            return 0
        print(f"Podman sandbox image missing: {args.image}")
        print(f"Build it with: python -m tools.podman_sandbox build --image {args.image}")
        return 1
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Podman sandbox error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
