"""Live-browser CDP command adapter with explicit runtime ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from VoidCube_cli.command_router import ParsedCliCommand


_DEFAULT_CDP_URL = "http://localhost:9222"
_LIVE_BROWSER_CONNECTED_NOTE = (
    "[System note: The user has connected your browser tools to their live Chrome browser "
    "via Chrome DevTools Protocol. Your browser_navigate, browser_snapshot, browser_click, "
    "and other browser tools now control their real browser — including any pages they have "
    "open, logged-in sessions, and cookies. They likely opened specific sites or logged into "
    "services before connecting. Please await their instruction before attempting to operate "
    "the browser. When you do act, be mindful that your actions affect their real browser — "
    "don't close tabs or navigate away from pages without asking.]"
)
_LIVE_BROWSER_DISCONNECTED_NOTE = (
    "[System note: The user has disconnected the browser tools from their live Chrome. "
    "Browser tools are back to default mode (headless local browser or cloud provider).]"
)


@dataclass(frozen=True, slots=True)
class BrowserCommandPorts:
    current_cdp_url: Callable[[], str]
    set_cdp_url: Callable[[str], None]
    clear_cdp_url: Callable[[], None]
    cleanup_browsers: Callable[[], None]
    probe_port: Callable[[int], bool]
    launch_chrome_debug: Callable[[int], bool]
    system_name: Callable[[], str]
    chrome_data_dir: Callable[[], str]
    cloud_provider: Callable[[], Any | None]
    enqueue_system_note: Callable[[str], None]
    sleep: Callable[[float], None]
    emit: Callable[[str], None]


def handle_browser_command(
    request: ParsedCliCommand,
    *,
    ports: BrowserCommandPorts,
) -> None:
    """Connect, disconnect, or display status for a live Chrome CDP endpoint."""
    subcommand = request.arguments.lower().strip() or "status"
    current = ports.current_cdp_url().strip()

    if subcommand.startswith("connect"):
        _connect_browser(request, ports=ports)
    elif subcommand == "disconnect":
        _disconnect_browser(current, ports=ports)
    elif subcommand == "status":
        _show_browser_status(current, ports=ports)
    else:
        _show_browser_usage(ports.emit)


def _connect_browser(request: ParsedCliCommand, *, ports: BrowserCommandPorts) -> None:
    connect_parts = request.original.strip().split(None, 2)
    cdp_url = (
        connect_parts[2].strip()
        if len(connect_parts) > 2
        else _DEFAULT_CDP_URL
    )
    ports.cleanup_browsers()
    ports.emit("")

    port = _cdp_port(cdp_url)
    already_open = ports.probe_port(port)
    if already_open:
        ports.emit(f"   ✓ Chrome is already listening on port {port}")
    elif cdp_url == _DEFAULT_CDP_URL:
        ports.emit("   Chrome isn't running with remote debugging — attempting to launch...")
        if ports.launch_chrome_debug(port):
            for _ in range(10):
                if ports.probe_port(port):
                    already_open = True
                    break
                ports.sleep(0.5)
            if already_open:
                ports.emit(f"   ✓ Chrome launched and listening on port {port}")
            else:
                ports.emit(f"   ⚠ Chrome launched but port {port} isn't responding yet")
                ports.emit(
                    "     Try again in a few seconds — the debug instance may still be starting"
                )
        else:
            ports.emit("   ⚠ Could not auto-launch Chrome")
            ports.emit("     Launch Chrome manually:")
            ports.emit(f"     {_manual_chrome_command(ports.system_name(), ports.chrome_data_dir())}")
    else:
        ports.emit(f"   ⚠ Port {port} is not reachable at {cdp_url}")

    ports.set_cdp_url(cdp_url)
    ports.emit("")
    ports.emit("🌐 Browser connected to live Chrome via CDP")
    ports.emit(f"   Endpoint: {cdp_url}")
    ports.emit("")
    ports.enqueue_system_note(_LIVE_BROWSER_CONNECTED_NOTE)


def _disconnect_browser(current: str, *, ports: BrowserCommandPorts) -> None:
    if not current:
        ports.emit("")
        ports.emit("Browser is not connected to live Chrome (already using default mode)")
        ports.emit("")
        return

    ports.clear_cdp_url()
    ports.cleanup_browsers()
    ports.emit("")
    ports.emit("🌐 Browser disconnected from live Chrome")
    ports.emit("   Browser tools reverted to default mode (local headless or cloud provider)")
    ports.emit("")
    ports.enqueue_system_note(_LIVE_BROWSER_DISCONNECTED_NOTE)


def _show_browser_status(current: str, *, ports: BrowserCommandPorts) -> None:
    ports.emit("")
    if current:
        ports.emit("🌐 Browser: connected to live Chrome via CDP")
        ports.emit(f"   Endpoint: {current}")
        if ports.probe_port(_cdp_port(current)):
            ports.emit("   Status: ✓ reachable")
        else:
            ports.emit("   Status: ⚠ not reachable (Chrome may not be running)")
    else:
        provider = ports.cloud_provider()
        if provider is not None:
            ports.emit(f"🌐 Browser: {provider.provider_name()} (cloud)")
        else:
            ports.emit("🌐 Browser: local headless Chromium (agent-browser)")
    ports.emit("")
    ports.emit("   /browser connect      — connect to your live Chrome")
    ports.emit("   /browser disconnect   — revert to default")
    ports.emit("")


def _show_browser_usage(emit: Callable[[str], None]) -> None:
    emit("")
    emit("Usage: /browser connect|disconnect|status")
    emit("")
    emit("   connect      Connect browser tools to your live Chrome session")
    emit("   disconnect   Revert to default browser backend")
    emit("   status       Show current browser mode")
    emit("")


def _cdp_port(url: str) -> int:
    try:
        return int(url.rsplit(":", 1)[-1].split("/")[0])
    except (ValueError, IndexError):
        return 9222


def _manual_chrome_command(system: str, data_dir: str) -> str:
    if system == "Darwin":
        return (
            'open -a "Google Chrome" --args'
            " --remote-debugging-port=9222"
            f' --user-data-dir="{data_dir}"'
            " --no-first-run --no-default-browser-check"
        )
    if system == "Windows":
        return (
            "chrome.exe --remote-debugging-port=9222"
            f' --user-data-dir="{data_dir}"'
            " --no-first-run --no-default-browser-check"
        )
    return (
        "google-chrome --remote-debugging-port=9222"
        f' --user-data-dir="{data_dir}"'
        " --no-first-run --no-default-browser-check"
    )
