"""CLI projection for shared agent tool events."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from VoidCube_app.tool_events import ToolEvent, ToolEventKind


logger = logging.getLogger(__name__)


def project_tool_event(
    host: Any,
    event: ToolEvent,
    *,
    append_autonomous_event: Callable[..., None],
    emit_line: Callable[[str], None],
) -> None:
    """Project one tool event into CLI-owned view and audio state."""
    if event.kind in {ToolEventKind.REASONING, ToolEventKind.SUBAGENT_PROGRESS}:
        return
    if event.kind is ToolEventKind.COMPLETED:
        _project_completed(
            host,
            event,
            append_autonomous_event=append_autonomous_event,
            emit_line=emit_line,
        )
    elif event.kind is ToolEventKind.STARTED:
        _project_started(
            host,
            event,
            append_autonomous_event=append_autonomous_event,
        )


def _project_completed(
    host: Any,
    event: ToolEvent,
    *,
    append_autonomous_event: Callable[..., None],
    emit_line: Callable[[str], None],
) -> None:
    host._tool_start_time = 0.0
    host._current_tool_name = ""
    if (
        getattr(host, "_autonomous_gate_active", False)
        and getattr(host, "_current_autonomous_task", None)
        and event.name
    ):
        suffix = f" ({event.duration:.1f}s)" if event.duration else ""
        append_autonomous_event(
            host,
            f"工具完成: {event.name}{suffix}",
            tone="error" if event.is_error else "success",
            stage="tool_completed",
        )

    progress_mode = getattr(host, "tool_progress_mode", "off")
    if event.name and progress_mode in {"all", "new"}:
        repeated = (
            progress_mode == "new"
            and event.name == getattr(host, "_last_scrollback_tool", "")
        )
        if not repeated:
            host._last_scrollback_tool = event.name
        if not repeated and host._should_emit_scrollback_output():
            try:
                from agent.display import get_cute_tool_message

                line = get_cute_tool_message(
                    event.name,
                    dict(event.arguments),
                    event.duration,
                )
                emit_line(f"  {line} [error]" if event.is_error else f"  {line}")
            except Exception:
                logger.debug("Tool scrollback rendering failed", exc_info=True)

    if getattr(host, "_inline_diffs_enabled", False):
        snapshots = getattr(host, "_pending_edit_snapshots", {})
        if not host._should_emit_scrollback_output():
            snapshots.pop(event.call_id, None)
        else:
            snapshot = snapshots.pop(event.call_id, None)
            try:
                from agent.display import render_edit_diff_with_delta

                render_edit_diff_with_delta(
                    event.name,
                    event.result,
                    function_args=dict(event.arguments),
                    snapshot=snapshot,
                    print_fn=emit_line,
                )
            except Exception:
                logger.debug(
                    "Edit diff preview failed for %s",
                    event.name,
                    exc_info=True,
                )
    host._invalidate()


def _project_started(
    host: Any,
    event: ToolEvent,
    *,
    append_autonomous_event: Callable[..., None],
) -> None:
    arguments = dict(event.arguments)
    if getattr(host, "_inline_diffs_enabled", False):
        try:
            from agent.display import capture_local_edit_snapshot

            snapshot = capture_local_edit_snapshot(event.name, arguments)
            if snapshot is not None:
                host._pending_edit_snapshots[event.call_id] = snapshot
        except Exception:
            logger.debug(
                "Edit snapshot capture failed for %s",
                event.name,
                exc_info=True,
            )

    if event.name and not event.name.startswith("_"):
        from agent.display import get_tool_emoji, get_tool_preview_max_len

        label = event.preview or event.name
        max_length = get_tool_preview_max_len()
        if max_length > 0 and len(label) > max_length:
            label = label[: max_length - 3] + "..."
        host._spinner_text = f"{get_tool_emoji(event.name)} {label}"
        host._tool_start_time = time.monotonic()
        host._current_tool_name = event.name
        if (
            getattr(host, "_autonomous_gate_active", False)
            and getattr(host, "_current_autonomous_task", None)
        ):
            append_autonomous_event(
                host,
                f"工具启动: {event.name}",
                tone="info",
                stage="tool_started",
            )
        host._invalidate()

    if not getattr(host, "_voice_mode", False) or not event.name:
        return
    try:
        from tools.voice_mode import play_beep

        threading.Thread(
            target=play_beep,
            kwargs={"frequency": 1200, "duration": 0.06, "count": 1},
            daemon=True,
        ).start()
    except Exception:
        logger.debug("Tool audio cue failed", exc_info=True)
