"""Welcome banner, ASCII art, and skills summary for the CLI.

Pure display functions with no VoidcubeCLI state dependency.
"""

import logging
import shutil
from typing import Dict, List

from rich.console import Console

logger = logging.getLogger(__name__)


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


def build_compact_banner() -> str:
    """Build the welcome banner used by narrow terminal adapters."""
    from VoidCube_cli.style import BANNER_BORDER, BANNER_DIM, BANNER_TITLE

    line1 = "> VoidCube - AI Agent"
    tiny_line = "> VoidCube"
    version_line = format_banner_version_label()
    width = min(shutil.get_terminal_size().columns - 2, 88)
    if width < 30:
        return f"\n[{BANNER_TITLE}]{tiny_line}[/]\n"

    content_width = width - 4
    bar = "═" * width
    line1 = line1[:content_width].ljust(content_width)
    line2 = version_line[:content_width].ljust(content_width)
    return (
        f"\n[bold {BANNER_BORDER}]╔{bar}╗[/]\n"
        f"[bold {BANNER_BORDER}]║[/] [{BANNER_TITLE}]{line1}[/] "
        f"[bold {BANNER_BORDER}]║[/]\n"
        f"[bold {BANNER_BORDER}]║[/] [dim {BANNER_DIM}]{line2}[/] "
        f"[bold {BANNER_BORDER}]║[/]\n"
        f"[bold {BANNER_BORDER}]╚{bar}╝[/]\n"
    )


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
    """Build and print the wide welcome banner and its session metadata."""
    
    from VoidCube_cli.style import BANNER_ACCENT, BANNER_BORDER, BANNER_DIM, BANNER_TITLE

    accent = BANNER_ACCENT

    logo_lines = [line for line in VOIDCUBE_LOGO.split('\n') if line.strip()]
    if logo_lines:
        max_length = max(len(line) for line in logo_lines)
        terminal_width = shutil.get_terminal_size((80, 24)).columns
        padding = (terminal_width - max_length) // 2
        padding = max(padding, 0)
        
        for line in logo_lines:
            console.print(f"[{accent}]{' ' * padding}{line}[/]")
    
    # Keep the wide banner informative without turning it into a dashboard.
    width = shutil.get_terminal_size((80, 24)).columns
    rule_width = min(max_length if logo_lines else 48, max(32, width - 8))
    console.print(f"[{BANNER_BORDER}]" + "─" * rule_width + "[/]")
    model_label = str(model or "default").rsplit("/", 1)[-1]
    if len(model_label) > 28:
        model_label = model_label[:25] + "..."
    session_label = str(session_id or "new")[-12:]
    console.print(
        f"[bold {BANNER_TITLE}]AGENT CONSOLE[/] "
        f"[dim {BANNER_DIM}]{format_banner_version_label()}  ·  "
        f"{model_label}  ·  session {session_label}[/]"
    )
    console.print()
