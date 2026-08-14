from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from systems.evolution_evaluation import (
    ExecutionEnvironmentIdentity,
    ExecutionEnvironmentManifest,
    SubjectCheckoutEvidence,
    build_container_environment_manifest,
    capture_host_environment_manifest,
    dependency_fingerprint,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_dependency_fingerprint_tracks_declared_inputs_only(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("pydantic==2.11.0\n", encoding="utf-8")
    initial = dependency_fingerprint(tmp_path)

    (tmp_path / "ordinary.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert dependency_fingerprint(tmp_path) == initial

    (tmp_path / "requirements.txt").write_text("pydantic==2.12.0\n", encoding="utf-8")
    assert dependency_fingerprint(tmp_path) != initial


def test_host_manifest_binds_virtualenv_toolchain_and_workspace():
    root = Path(__file__).parents[1]
    manifest = capture_host_environment_manifest(
        root,
        repository_head="a" * 40,
    )

    assert manifest.validation_scope == "host"
    assert manifest.validated_platforms == ("windows",)
    assert manifest.host_workspace_path == str(root.resolve())
    assert manifest.execution_workspace_path == str(root.resolve())
    python = next(
        tool
        for tool in manifest.tools
        if tool.scope == "host" and tool.name == "python"
    )
    pytest_tool = next(
        tool
        for tool in manifest.tools
        if tool.scope == "host" and tool.name == "pytest"
    )
    assert python.available is True
    assert ".venv" in python.executable
    assert pytest_tool.available is True
    assert "-m pytest" in pytest_tool.executable


def test_container_manifest_keeps_host_and_execution_identities_distinct(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    manifest = build_container_environment_manifest(
        tmp_path,
        backend="podman",
        execution_workspace_path="/workspace",
        probe={
            "os_name": "Linux",
            "os_release": "6.8.0",
            "architecture": "x86_64",
            "image_reference": "localhost/voidcube-project-podman:py314-v1",
            "image_digest": "sha256:" + "e" * 64,
            "repository_head": "b" * 40,
            "tools": {
                "git": {
                    "executable": "/usr/bin/git",
                    "version": "git version 2.45.0",
                },
                "python": {
                    "executable": "/usr/bin/python3",
                    "version": "Python 3.12.0",
                },
            },
        },
    )

    assert manifest.validation_scope == "container"
    assert manifest.validated_platforms == ("linux",)
    assert manifest.execution_workspace_path == "/workspace"
    assert manifest.image_reference.endswith("py314-v1")
    assert manifest.image_digest == "sha256:" + "e" * 64
    assert {tool.scope for tool in manifest.tools} == {"host", "execution"}
    sandbox_pytest = next(
        tool
        for tool in manifest.tools
        if tool.scope == "execution" and tool.name == "pytest"
    )
    assert sandbox_pytest.available is False


def test_manifest_content_address_rejects_tampering():
    manifest = capture_host_environment_manifest(
        Path(__file__).parents[1],
        repository_head="c" * 40,
    )
    payload = manifest.model_dump(mode="json")
    payload["backend"] = "forged"

    with pytest.raises(ValidationError, match="content_hash"):
        ExecutionEnvironmentManifest.model_validate(payload)


def test_manifest_reads_legacy_hash_without_image_fields():
    manifest = capture_host_environment_manifest(
        Path(__file__).parents[1], repository_head="e" * 40
    )
    payload = manifest.content_payload()
    payload.pop("image_reference")
    payload.pop("image_digest")
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    restored = ExecutionEnvironmentManifest.model_validate(
        {
            **payload,
            "execution_environment_id": f"execution-environment-{content_hash}",
            "content_hash": content_hash,
        }
    )

    assert restored.image_reference is None
    assert restored.image_digest is None


def test_manifest_cannot_claim_a_platform_different_from_execution_os():
    manifest = capture_host_environment_manifest(
        Path(__file__).parents[1],
        repository_head="d" * 40,
    )
    payload = manifest.content_payload()
    payload["execution_os"] = "Linux 6.8"

    with pytest.raises(ValidationError, match="execution operating system"):
        ExecutionEnvironmentManifest.create(**payload)


def test_identity_excludes_subject_head_and_checkout_evidence_is_addressed():
    root = Path(__file__).parents[1]
    baseline = capture_host_environment_manifest(root, repository_head="a" * 40)
    candidate = capture_host_environment_manifest(root, repository_head="b" * 40)

    baseline_identity = baseline.identity()
    candidate_identity = candidate.identity()

    assert isinstance(baseline_identity, ExecutionEnvironmentIdentity)
    assert (
        baseline_identity.execution_environment_identity_id
        == candidate_identity.execution_environment_identity_id
    )
    assert baseline_identity.content_hash == candidate_identity.content_hash

    evidence = SubjectCheckoutEvidence.create(
        subject="candidate",
        commit="b" * 40,
        worktree_path=str(root),
        execution_environment_identity_id=(
            candidate_identity.execution_environment_identity_id
        ),
        checked_out_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
    )
    assert evidence.subject_checkout_evidence_id.startswith("subject-checkout-")
