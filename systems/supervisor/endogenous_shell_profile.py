"""Shell body profile projection with an explicit worktree input."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from systems.supervisor.endogenous_evidence import item_evidence_quality


def build_shell_body_profile(shell_slot_meta: Dict[str, Any]) -> Dict[str, Any]:
    profile: Dict[str, Any] = {
        "slot_id": str(shell_slot_meta.get("slot_id") or "").strip(),
        "worktree_path": str(shell_slot_meta.get("worktree_path") or "").strip(),
        "body_version": shell_slot_meta.get("body_version"),
        "generation": shell_slot_meta.get("generation"),
        "materialized_from": shell_slot_meta.get("materialized_from"),
        "candidate_branch": shell_slot_meta.get("candidate_branch"),
        "candidate_commit": shell_slot_meta.get("candidate_commit"),
    }
    worktree_path = profile["worktree_path"]
    if not worktree_path:
        profile["profile_status"] = "missing_worktree"
        return profile

    worktree = Path(worktree_path)
    if not worktree.exists():
        profile["profile_status"] = "worktree_missing_on_disk"
        return profile

    manifest_path = worktree.parent / "worktree-origin.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            profile["origin_manifest"] = {
                "source": manifest.get("source"),
                "source_root": manifest.get("source_root"),
                "source_branch": manifest.get("source_branch"),
                "source_commit": manifest.get("source_commit"),
                "candidate_branch": manifest.get("candidate_branch"),
                "candidate_commit": manifest.get("candidate_commit"),
                "materialized_at": manifest.get("materialized_at"),
            }
        except Exception:
            profile["origin_manifest_error"] = True

    editable_indicators = [
        "agent",
        "skills",
        "tools",
        "prompts",
        "systems",
        "Mem",
        "tests",
    ]
    present_roots = [name for name in editable_indicators if (worktree / name).exists()]
    try:
        top_level_entries = sorted(child.name for child in worktree.iterdir())[:20]
    except Exception:
        top_level_entries = []

    profile.update(
        {
            "profile_status": "ready",
            "present_roots": present_roots,
            "top_level_entries": top_level_entries,
            "has_run_agent": (worktree / "run_agent.py").exists(),
            "has_config": (worktree / "config.yaml").exists(),
        }
    )
    profile.update(
        item_evidence_quality(
            item=profile,
            source_reliability=0.9,
            supports=["self_structure", "body_state"],
            contradicts=[],
        )
    )
    return profile
