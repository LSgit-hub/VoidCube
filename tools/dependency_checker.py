"""
Runtime Dependency Checker — Bootstrap Tool
============================================
Checks that external runtime dependencies (git, node, bash, etc.) are
installed and meet minimum version requirements.  Provides platform-aware
install instructions for each missing dependency.

Registered as the ``check_dependencies`` tool so the agent can inspect and
remediate its own environment.  The companion ``skills/system/bootstrap``
skill teaches the agent *when* and *how* to use this tool.

Concepts
--------
- **critical** deps — the agent should refuse meaningful work until fixed
- **optional** deps — nice to have; tools degrade gracefully when absent
- The manifest at ``tools/dependency_manifest.yaml`` is the single source
  of truth; the agent can read it directly to discover new dependencies.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------

_MANIFEST_PATH = Path(__file__).resolve().parent / "dependency_manifest.yaml"
_manifest_cache: Optional[Dict[str, Any]] = None


def _load_manifest() -> Dict[str, Any]:
    """Load the dependency manifest (cached in-process)."""
    global _manifest_cache
    if _manifest_cache is not None:
        return _manifest_cache
    if not _MANIFEST_PATH.exists():
        logger.warning("Dependency manifest not found at %s", _MANIFEST_PATH)
        _manifest_cache = {"dependencies": {}}
        return _manifest_cache
    try:
        with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
            _manifest_cache = yaml.safe_load(f) or {"dependencies": {}}
    except Exception as exc:
        logger.warning("Failed to load dependency manifest: %s", exc)
        _manifest_cache = {"dependencies": {}}
    return _manifest_cache


def _get_platform() -> str:
    """Return the current platform key used in the manifest."""
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Darwin":
        return "macos"
    # Distinguish deb/rpm-ish Linux families
    if system == "Linux":
        if shutil.which("apt-get"):
            return "linux_deb"
        if shutil.which("dnf") or shutil.which("yum"):
            return "linux_rpm"
        return "linux_deb"  # safe default
    return system.lower()


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

# Loose semver: captures "2.43.0", "v2.43", "18.20.4", etc.
_SEMVER_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def _parse_version(text: str) -> Optional[Tuple[int, ...]]:
    """Extract the first version-looking triple from *text*."""
    m = _SEMVER_RE.search(text)
    if not m:
        return None
    major, minor, patch = m.groups()
    return (int(major), int(minor), int(patch or "0"))


def _version_ok(actual: Optional[Tuple[int, ...]], spec: str) -> bool:
    """Check *actual* against a PEP 440-ish min-version spec (e.g. ``>=2.30``)."""
    if actual is None:
        return False  # couldn't parse
    if spec.startswith(">="):
        want_text = spec[2:].strip()
        want = _parse_version(want_text)
        if want is None:
            return True  # can't parse spec → be permissive
        return actual >= want
    # Without a spec just having it is enough
    return True


# ---------------------------------------------------------------------------
# Single-dependency check
# ---------------------------------------------------------------------------

def _check_command(cmd: str) -> Tuple[bool, Optional[Tuple[int, ...]], Optional[str]]:
    """
    Check whether *cmd* is on PATH and return its version.

    Returns ``(found, version_tuple, raw_version_string)``.
    """
    # 1. PATH lookup
    found_path = shutil.which(cmd)
    if found_path is None:
        return False, None, None

    # 2. Try to get version
    manifest = _load_manifest()
    version_raw: Optional[str] = None
    for dep in manifest.get("dependencies", {}).values():
        if cmd in dep.get("commands", []):
            check_cmd = dep.get("version_check")
            if check_cmd:
                try:
                    result = subprocess.run(
                        check_cmd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    combined = (result.stdout + "\n" + result.stderr).strip()
                    if combined:
                        version_raw = combined.split("\n")[0]
                except Exception:
                    pass
            break

    version_tuple = _parse_version(version_raw) if version_raw else None
    return True, version_tuple, version_raw


def check_dependency_status(name: str) -> Dict[str, Any]:
    """
    Inspect a single dependency and return its status dict.

    Keys: name, found, path, version_raw, version_ok, min_version,
          installed, critical, description, install_commands, error.
    """
    manifest = _load_manifest()
    deps = manifest.get("dependencies", {})
    dep = deps.get(name)

    if dep is None:
        return {"name": name, "found": False, "error": f"Unknown dependency '{name}'"}

    commands: List[str] = dep.get("commands", [])
    found = False
    found_path: Optional[str] = None
    version_raw: Optional[str] = None
    version_tuple: Optional[Tuple[int, ...]] = None

    for cmd in commands:
        ok, vt, vr = _check_command(cmd)
        if ok:
            found = True
            found_path = shutil.which(cmd)
            version_tuple = vt
            version_raw = vr
            break

    min_version = dep.get("min_version")
    v_ok = True
    if found and min_version:
        v_ok = _version_ok(version_tuple, min_version)

    platform_key = _get_platform()
    install_map = dep.get("install", {})
    install_cmd = install_map.get(platform_key) or install_map.get("all")

    return {
        "name": name,
        "description": dep.get("description", ""),
        "found": found,
        "path": found_path,
        "version_raw": version_raw,
        "version_tuple": list(version_tuple) if version_tuple else None,
        "min_version": min_version,
        "version_ok": v_ok if found else None,
        "critical": dep.get("critical", False),
        "related_toolsets": dep.get("related_toolsets", []),
        "install_command": install_cmd,
        "notes": dep.get("notes"),
    }


# ---------------------------------------------------------------------------
# Batch check
# ---------------------------------------------------------------------------

def check_all_dependencies() -> List[Dict[str, Any]]:
    """Run ``check_dependency_status`` for every entry in the manifest."""
    manifest = _load_manifest()
    deps = manifest.get("dependencies", {})
    results: List[Dict[str, Any]] = []
    for name in deps:
        results.append(check_dependency_status(name))
    return results


def get_missing_dependencies() -> List[Dict[str, Any]]:
    """Return only dependencies that are missing or have wrong versions."""
    return [d for d in check_all_dependencies() if not d.get("found") or not d.get("version_ok")]


def get_critical_missing() -> List[Dict[str, Any]]:
    """Return only *critical* dependencies that are missing / out of date."""
    return [d for d in get_missing_dependencies() if d.get("critical")]


# ---------------------------------------------------------------------------
# Formatted reports
# ---------------------------------------------------------------------------

def format_dependency_report(deps: List[Dict[str, Any]]) -> str:
    """Format a dependency list as a human-readable markdown report."""
    if not deps:
        return "✓ All dependencies are installed."

    critical = [d for d in deps if d.get("critical") and (not d.get("found") or not d.get("version_ok"))]
    missing = [d for d in deps if not d.get("found")]
    outdated = [d for d in deps if d.get("found") and not d.get("version_ok")]

    lines: List[str] = []
    platform_key = _get_platform()

    if critical:
        lines.append("## 🚨 Critical Missing Dependencies\n")
        for d in critical:
            desc = d.get("description", "")
            lines.append(f"- **{d['name']}** — {desc}")
            install = d.get("install_command")
            if install:
                lines.append(f"  Install: `{install}`")
            notes = d.get("notes")
            if notes:
                lines.append(f"  Note: {notes.strip()}")
        lines.append("")

    if missing and missing != critical:
        lines.append("## ⚠️  Missing Dependencies\n")
        for d in missing:
            if d in critical:
                continue
            desc = d.get("description", "")
            lines.append(f"- **{d['name']}** — {desc}")
            install = d.get("install_command")
            if install:
                lines.append(f"  Install: `{install}`")
        lines.append("")

    if outdated:
        lines.append("## 🔄 Outdated Dependencies\n")
        for d in outdated:
            lines.append(
                f"- **{d['name']}**: have {d.get('version_raw', '?')}, "
                f"need {d.get('min_version', '?')}"
            )
        lines.append("")

    if not lines:
        # Everything found, just outdated
        lines.append("All dependencies found.")

    return "\n".join(lines)


def format_bootstrap_summary() -> str:
    """
    One-shot full-environment summary for the doctor command / agent startup.

    Includes platform, Python version, and a dependency table.
    """
    all_deps = check_all_dependencies()
    missing = [d for d in all_deps if not d.get("found")]
    critical_missing = [d for d in missing if d.get("critical")]
    outdated = [d for d in all_deps if d.get("found") and not d.get("version_ok")]

    status = "✓ Ready"
    if critical_missing:
        status = "✗ Missing critical dependencies"
    elif missing:
        status = "⚠ Some optional tools missing"

    lines = [
        f"# Environment Bootstrap Report",
        f"",
        f"**Platform**: {platform.system()} {platform.release()}",
        f"**Python**:   {sys.version.split()[0]}",
        f"**Status**:   {status}",
        f"",
        f"| Dependency | Status | Version |",
        f"|------------|--------|---------|",
    ]

    for d in all_deps:
        name = d["name"]
        if d["found"] and d["version_ok"]:
            state = "✓"
            ver = d.get("version_raw", "—")
        elif d["found"] and not d["version_ok"]:
            state = "⚠ outdated"
            ver = f"{d.get('version_raw', '?')} (need {d.get('min_version', '?')})"
        elif d.get("critical"):
            state = "✗ CRITICAL"
            ver = "not found"
        else:
            state = "—"
            ver = "not found"
        lines.append(f"| {name:20s} | {state:12s} | {ver:30s} |")

    lines.append("")

    if missing:
        lines.append("## Install Commands\n")
        platform_key = _get_platform()
        for d in missing:
            install = d.get("install_command")
            if install:
                lines.append(f"**{d['name']}**: `{install}`")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool entry point (registered as check_dependencies)
# ---------------------------------------------------------------------------

CHECK_DEPENDENCIES_SCHEMA = {
    "name": "check_dependencies",
    "description": (
        "Check which external runtime dependencies (git, node, bash, docker, "
        "etc.) are installed on this machine and meet version requirements. "
        "Returns a report with platform-specific install commands for any "
        "missing or outdated dependency.\n\n"
        "Use this during initial setup / bootstrap, when a tool reports a "
        "missing command, or when the user asks about environment readiness.\n\n"
        "Actions:\n"
        "- 'check_all':  full environment scan, all dependencies\n"
        "- 'check_one':  inspect a single dependency by name\n"
        "- 'missing':    only return missing/outdated dependencies\n"
        "- 'summary':    formatted markdown bootstrap report"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["check_all", "check_one", "missing", "summary"],
                "description": "Which check to perform (default: 'summary')."
            },
            "name": {
                "type": "string",
                "description": (
                    "Dependency name (required with action='check_one'). "
                    "Use check_all first to discover names."
                ),
            },
        },
        "required": [],
    },
}


def check_dependencies(action: str = "summary", name: str = "") -> str:
    """
    Main entry point for the ``check_dependencies`` tool.

    Returns a JSON string with the requested check results.
    """
    if action == "check_one":
        if not name:
            return json.dumps(
                {"success": False, "error": "action='check_one' requires 'name'"},
                ensure_ascii=False,
            )
        result = check_dependency_status(name)
        return json.dumps({"success": True, "dependency": result}, ensure_ascii=False, default=str)

    if action == "missing":
        deps = get_missing_dependencies()
        return json.dumps({
            "success": True,
            "missing_count": len(deps),
            "critical_count": len([d for d in deps if d.get("critical")]),
            "dependencies": deps,
            "report": format_dependency_report(deps),
        }, ensure_ascii=False, default=str)

    if action == "check_all":
        deps = check_all_dependencies()
        return json.dumps({
            "success": True,
            "total": len(deps),
            "dependencies": deps,
        }, ensure_ascii=False, default=str)

    # default: summary
    report = format_bootstrap_summary()
    missing = get_missing_dependencies()
    return json.dumps({
        "success": True,
        "report": report,
        "missing_dependencies": missing,
        "all_ok": len(missing) == 0,
    }, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Re-usable check_fn for tool registry (always available)
# ---------------------------------------------------------------------------

def check_bootstrap_requirements() -> bool:
    """The bootstrap checker itself has no external requirements."""
    return True


# ---------------------------------------------------------------------------
# Register with the tool registry
# ---------------------------------------------------------------------------

from tools.registry import registry

registry.register(
    name="check_dependencies",
    toolset="system",
    schema=CHECK_DEPENDENCIES_SCHEMA,
    handler=lambda args, **kw: check_dependencies(
        action=args.get("action", "summary"),
        name=args.get("name", ""),
    ),
    check_fn=check_bootstrap_requirements,
    emoji="🔍",
)
