from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import tarfile
import tempfile

import httpx


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    actual = file_sha256(path)
    if actual != expected:
        raise RuntimeError(
            f"Voice model checksum mismatch for {path.name}: "
            f"expected {expected}, got {actual}"
        )


def download_verified(url: str, destination: Path, sha256: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        verify_sha256(destination, sha256)
        return destination
    temporary = destination.with_suffix(f"{destination.suffix}.part-{os.getpid()}")
    try:
        import truststore

        truststore.inject_into_ssl()
        with httpx.stream(
            "GET",
            url,
            follow_redirects=True,
            timeout=180.0,
        ) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
        verify_sha256(temporary, sha256)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def extract_verified_tar(archive: Path, destination: Path) -> Path:
    if destination.is_dir():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent)
    )
    try:
        with tarfile.open(archive, "r:bz2") as bundle:
            bundle.extractall(temporary_root, filter="data")
        extracted = temporary_root / destination.name
        if not extracted.is_dir():
            raise RuntimeError(
                f"Voice model archive does not contain {destination.name}"
            )
        extracted.replace(destination)
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
    return destination
