"""Welcome banner, ASCII art, and skills summary for the CLI.

Pure display functions with no VoidcubeCLI state dependency.
"""

import logging
import os
import shutil
from pathlib import Path
from VoidCube_core.constants import get_VoidCube_home
from typing import Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from prompt_toolkit import print_formatted_text as _pt_print
from prompt_toolkit.formatted_text import ANSI as _PT_ANSI

logger = logging.getLogger(__name__)


# =========================================================================
# ANSI building blocks for conversation display
# =========================================================================

_GOLD = "\033[1;38;2;255;215;0m"  # True-color #FFD700 bold
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RST = "\033[0m"


def cprint(text: str):
    """Print ANSI-colored text through prompt_toolkit's renderer."""
    _pt_print(_PT_ANSI(text))


# =========================================================================
# Skin-aware color helpers
# =========================================================================

def _skin_color(key: str, fallback: str) -> str:
    """Get a color from the active skin, or return fallback."""
    try:
        from VoidCube_cli.skin_engine import get_active_skin
        return get_active_skin().get_color(key, fallback)
    except Exception:
        return fallback


def _skin_branding(key: str, fallback: str) -> str:
    """Get a branding string from the active skin, or return fallback."""
    try:
        from VoidCube_cli.skin_engine import get_active_skin
        return get_active_skin().get_branding(key, fallback)
    except Exception:
        return fallback


# =========================================================================
# ASCII Art & Branding
# =========================================================================

from VoidCube_cli import __version__ as VERSION

VOIDCUBE_LOGO = r"""
 ██╗   ██╗  █████╗  ██╗ ██████╗   ██████╗ ██╗   ██╗ ██████╗  ███████╗
 ██║   ██║ ██╔══██╗ ██║ ██╔══██╗ ██╔════╝ ██║   ██║ ██╔══██╗ ██╔════╝
 ██║   ██║ ██║  ██║ ██║ ██║  ██║ ██║      ██║   ██║ ██████╔╝ █████╗  
 ╚██╗ ██╔╝ ██║  ██║ ██║ ██║  ██║ ██║      ██║   ██║ ██╔══██╗ ██╔══╝  
  ╚████╔╝  ╚█████╔╝ ██║ ██████╔╝ ╚██████╗ ╚██████╔╝ ██████╔╝ ███████╗
   ╚═══╝    ╚════╝  ╚═╝ ╚═════╝   ╚═════╝  ╚═════╝  ╚═════╝  ╚══════╝
"""


# =========================================================================
# Skills scanning
# =========================================================================

_RELEVANT_SKILL_CATEGORIES = {
    "devops",
    "github",
    "mlops",
}

def get_available_skills() -> Dict[str, List[str]]:
    """Return skills grouped by category, filtered by platform and disabled state."""
    try:
        from tools.skills_tool import _find_all_skills
        all_skills = _find_all_skills()
    except Exception:
        return {}

    skills_by_category: Dict[str, List[str]] = {}
    for skill in all_skills:
        category = skill.get("category") or "general"
        if category in _RELEVANT_SKILL_CATEGORIES:
            skills_by_category.setdefault(category, []).append(skill["name"])
    return skills_by_category


# =========================================================================
# Helper functions
# =========================================================================

def format_banner_version_label() -> str:
    """Return the version label for display in the banner."""
    return f"v{VERSION}"


def _format_context_length(tokens: int) -> str:
    """Format a token count for display."""
    if tokens >= 1_000_000:
        val = tokens / 1_000_000
        rounded = round(val)
        if abs(val - rounded) < 0.05:
            return f"{rounded}M"
        return f"{val:.1f}M"
    elif tokens >= 1_000:
        val = tokens / 1_000
        rounded = round(val)
        if abs(val - rounded) < 0.05:
            return f"{rounded}K"
        return f"{val:.1f}K"
    return str(tokens)


def _display_toolset_name(toolset_name: str) -> str:
    """Normalize internal/legacy toolset identifiers for banner display."""
    if not toolset_name:
        return "unknown"
    return toolset_name[:-6] if toolset_name.endswith("_tools") else toolset_name


# =========================================================================
# Welcome banner with ASCII logo
# =========================================================================

def build_welcome_banner(console: Console, model: str, cwd: str,
                         tools: List[dict] = None,
                         enabled_toolsets: List[str] = None,
                         session_id: str = None,
                         get_toolset_for_tool=None,
                         context_length: int = None,
                         conversation_history: List[dict] = None):
    """Build and print a welcome banner with ASCII art logo only."""
    
    accent = _skin_color("banner_accent", "#58A6FF")

    logo_lines = [line for line in VOIDCUBE_LOGO.split('\n') if line.strip()]
    if logo_lines:
        max_length = max(len(line) for line in logo_lines)
        terminal_width = shutil.get_terminal_size((80, 24)).columns
        padding = (terminal_width - max_length) // 2
        padding = max(padding, 0)
        
        for line in logo_lines:
            console.print(f"[{accent}]{' ' * padding}{line}[/]")
    
    console.print()
