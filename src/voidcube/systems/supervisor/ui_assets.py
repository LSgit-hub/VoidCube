"""Package-resource access for the Supervisor web UI."""

from __future__ import annotations

from importlib.resources import files


_SUPERVISOR_UI_RESOURCE = "web/supervisor.html"
_cached_ui_bytes: bytes | None = None
_cached_ui_html: str | None = None


def load_supervisor_ui_html() -> str:
    """Load the packaged Supervisor UI without a development-path fallback."""
    global _cached_ui_bytes, _cached_ui_html
    payload = files(__package__).joinpath(_SUPERVISOR_UI_RESOURCE).read_bytes()
    if _cached_ui_html is None or payload != _cached_ui_bytes:
        _cached_ui_bytes = payload
        _cached_ui_html = payload.decode("utf-8").replace("\r\n", "\n")
    return _cached_ui_html
