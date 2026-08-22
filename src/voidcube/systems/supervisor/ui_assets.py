"""Package-resource access for the Supervisor web UI."""

from __future__ import annotations

from importlib.resources import files


_SUPERVISOR_UI_RESOURCE = "web/supervisor.html"


def load_supervisor_ui_html() -> str:
    """Load the packaged Supervisor UI without a development-path fallback."""
    return files(__package__).joinpath(_SUPERVISOR_UI_RESOURCE).read_text(
        encoding="utf-8"
    )
