"""CLI presentation -- spinner, kawaii faces, tool preview formatting.

Pure display functions and classes with no AIAgent dependency.
Used by AIAgent._execute_tool_calls for CLI feedback.
"""

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from difflib import unified_diff
from pathlib import Path

from VoidCube_core.utils import safe_json_loads

# ANSI escape codes for coloring tool failure indicators
_RED = "\033[31m"
_RESET = "\033[0m"

logger = logging.getLogger(__name__)

_ANSI_RESET = "\033[0m"

def _diff_ansi() -> dict[str, str]:
    """Return the fixed ANSI palette used for diff display."""
    return {
        "dim": "\033[38;2;150;150;150m",
        "file": "\033[38;2;180;160;255m",
        "hunk": "\033[38;2;120;120;140m",
        "minus": "\033[38;2;255;255;255;48;2;120;20;20m",
        "plus": "\033[38;2;255;255;255;48;2;20;90;20m",
    }


# Module-level helpers for the fixed diff palette.
def _diff_dim():   return _diff_ansi()["dim"]
def _diff_file():  return _diff_ansi()["file"]
def _diff_hunk():  return _diff_ansi()["hunk"]
def _diff_minus(): return _diff_ansi()["minus"]
def _diff_plus():  return _diff_ansi()["plus"]
_MAX_INLINE_DIFF_FILES = 6
_MAX_INLINE_DIFF_LINES = 80


@dataclass
class LocalEditSnapshot:
    """Pre-tool filesystem snapshot used to render diffs locally after writes."""
    paths: list[Path] = field(default_factory=list)
    before: dict[str, str | None] = field(default_factory=dict)

# =========================================================================
# Configurable tool preview length (0 = no limit)
# Set once at startup by CLI or gateway from display.tool_preview_length config.
# =========================================================================
_tool_preview_max_len: int = 0  # 0 = unlimited


def set_tool_preview_max_len(n: int) -> None:
    """Set the global max length for tool call previews. 0 = no limit."""
    global _tool_preview_max_len
    _tool_preview_max_len = max(int(n), 0) if n else 0


def get_tool_preview_max_len() -> int:
    """Return the configured max preview length (0 = unlimited)."""
    return _tool_preview_max_len


def get_tool_emoji(tool_name: str, default: str = "🔧") -> str:
    """Get the display emoji for a tool.

    Resolution order: tool registry metadata, then *default*.
    """
    try:
        from tools.registry import registry
        emoji = registry.get_emoji(tool_name, default="")
        if emoji:
            return emoji
    except Exception:
        pass
    return default


# =========================================================================
# Tool preview (one-line summary of a tool call's primary argument)
# =========================================================================

def _oneline(text: str) -> str:
    """Collapse whitespace (including newlines) to single spaces."""
    return " ".join(text.split())


def build_tool_preview(tool_name: str, args: dict, max_len: int | None = None) -> str | None:
    """Build a short preview of a tool call's primary argument for display.

    *max_len* controls truncation.  ``None`` (default) defers to the global
    ``_tool_preview_max_len`` set via config; ``0`` means unlimited.
    """
    if max_len is None:
        max_len = _tool_preview_max_len
    if not args:
        return None
    primary_args = {
        "terminal": "command", "web_search": "query", "web_extract": "urls",
        "read_file": "path", "write_file": "path", "patch": "path",
        "search_files": "pattern", "browser_navigate": "url",
        "browser_click": "ref", "browser_type": "text",
        "image_generate": "prompt", "image_edit": "image_path",
        "video_generate": "prompt", "text_to_speech": "text",
        "vision_analyze": "question", "mixture_of_agents": "user_prompt",
        "skill_view": "name", "skills_list": "category",
        "scheduled_task": "action",
        "execute_code": "code", "delegate_task": "goal",
        "clarify": "question", "skill_manage": "name",
    }

    if tool_name == "process":
        action = args.get("action", "")
        sid = args.get("session_id", "")
        data = args.get("data", "")
        timeout_val = args.get("timeout")
        parts = [action]
        if sid:
            parts.append(sid[:16])
        if data:
            parts.append(f'"{_oneline(data[:20])}"')
        if timeout_val and action == "wait":
            parts.append(f"{timeout_val}s")
        return " ".join(parts) if parts else None

    if tool_name == "todo":
        todos_arg = args.get("todos")
        merge = args.get("merge", False)
        if todos_arg is None:
            return "reading task list"
        elif merge:
            return f"updating {len(todos_arg)} task(s)"
        else:
            return f"planning {len(todos_arg)} task(s)"

    if tool_name == "session_search":
        query = _oneline(args.get("query", ""))
        return f"recall: \"{query[:25]}{'...' if len(query) > 25 else ''}\""

    if tool_name == "memory":
        action = args.get("action", "")
        target = args.get("target", "")
        if action == "add":
            content = _oneline(args.get("content", ""))
            return f"+{target}: \"{content[:25]}{'...' if len(content) > 25 else ''}\""
        elif action == "replace":
            return f"~{target}: \"{_oneline(args.get('old_text', '')[:20])}\""
        elif action == "remove":
            return f"-{target}: \"{_oneline(args.get('old_text', '')[:20])}\""
        return action

    if tool_name.startswith("rl_"):
        rl_previews = {
            "rl_list_environments": "listing envs",
            "rl_select_environment": args.get("name", ""),
            "rl_get_current_config": "reading config",
            "rl_edit_config": f"{args.get('field', '')}={args.get('value', '')}",
            "rl_start_training": "starting",
            "rl_check_status": args.get("run_id", "")[:16],
            "rl_stop_training": f"stopping {args.get('run_id', '')[:16]}",
            "rl_get_results": args.get("run_id", "")[:16],
            "rl_list_runs": "listing runs",
            "rl_test_inference": f"{args.get('num_steps', 3)} steps",
        }
        return rl_previews.get(tool_name)

    key = primary_args.get(tool_name)
    if not key:
        for fallback_key in ("query", "text", "command", "path", "name", "prompt", "code", "goal"):
            if fallback_key in args:
                key = fallback_key
                break

    if not key or key not in args:
        return None

    value = args[key]
    if isinstance(value, list):
        value = value[0] if value else ""

    preview = _oneline(str(value))
    if not preview:
        return None
    if max_len > 0 and len(preview) > max_len:
        preview = preview[:max_len - 3] + "..."
    return preview


# =========================================================================
# Inline diff previews for write actions
# =========================================================================

def _resolved_path(path: str) -> Path:
    """Resolve a possibly-relative filesystem path against the current cwd."""
    candidate = Path(os.path.expanduser(path))
    if candidate.is_absolute():
        return candidate
    return Path.cwd() / candidate


def _snapshot_text(path: Path) -> str | None:
    """Return UTF-8 file content, or None for missing/unreadable files."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError, OSError):
        return None


def _display_diff_path(path: Path) -> str:
    """Prefer cwd-relative paths in diffs when available."""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except Exception:
        return str(path)


def _resolve_skill_manage_paths(args: dict) -> list[Path]:
    """Resolve skill_manage write targets to filesystem paths."""
    action = args.get("action")
    name = args.get("name")
    if not action or not name:
        return []

    from tools.skill_manager_tool import _find_skill, _resolve_skill_dir

    if action == "create":
        skill_dir = _resolve_skill_dir(name, args.get("category"))
        return [skill_dir / "SKILL.md"]

    existing = _find_skill(name)
    if not existing:
        return []

    skill_dir = Path(existing["path"])
    if action in {"edit", "patch"}:
        file_path = args.get("file_path")
        return [skill_dir / file_path] if file_path else [skill_dir / "SKILL.md"]
    if action in {"write_file", "remove_file"}:
        file_path = args.get("file_path")
        return [skill_dir / file_path] if file_path else []
    if action == "delete":
        files = [path for path in sorted(skill_dir.rglob("*")) if path.is_file()]
        return files
    return []


def _resolve_local_edit_paths(tool_name: str, function_args: dict | None) -> list[Path]:
    """Resolve local filesystem targets for write-capable tools."""
    if not isinstance(function_args, dict):
        return []

    if tool_name == "write_file":
        path = function_args.get("path")
        return [_resolved_path(path)] if path else []

    if tool_name == "patch":
        path = function_args.get("path")
        return [_resolved_path(path)] if path else []

    if tool_name == "skill_manage":
        return _resolve_skill_manage_paths(function_args)

    return []


def capture_local_edit_snapshot(tool_name: str, function_args: dict | None) -> LocalEditSnapshot | None:
    """Capture before-state for local write previews."""
    paths = _resolve_local_edit_paths(tool_name, function_args)
    if not paths:
        return None

    snapshot = LocalEditSnapshot(paths=paths)
    for path in paths:
        snapshot.before[str(path)] = _snapshot_text(path)
    return snapshot


def _result_succeeded(result: str | None) -> bool:
    """Conservatively detect whether a tool result represents success."""
    if not result:
        return False
    data = safe_json_loads(result)
    if data is None:
        return False
    if not isinstance(data, dict):
        return False
    if data.get("error"):
        return False
    if "success" in data:
        return bool(data.get("success"))
    return True


def _diff_from_snapshot(snapshot: LocalEditSnapshot | None) -> str | None:
    """Generate unified diff text from a stored before-state and current files."""
    if not snapshot:
        return None

    chunks: list[str] = []
    for path in snapshot.paths:
        before = snapshot.before.get(str(path))
        after = _snapshot_text(path)
        if before == after:
            continue

        display_path = _display_diff_path(path)
        diff = "".join(
            unified_diff(
                [] if before is None else before.splitlines(keepends=True),
                [] if after is None else after.splitlines(keepends=True),
                fromfile=f"a/{display_path}",
                tofile=f"b/{display_path}",
            )
        )
        if diff:
            chunks.append(diff)

    if not chunks:
        return None
    return "".join(chunk if chunk.endswith("\n") else chunk + "\n" for chunk in chunks)


def extract_edit_diff(
    tool_name: str,
    result: str | None,
    *,
    function_args: dict | None = None,
    snapshot: LocalEditSnapshot | None = None,
) -> str | None:
    """Extract a unified diff from a file-edit tool result."""
    if tool_name == "patch" and result:
        data = safe_json_loads(result)
        if isinstance(data, dict):
            diff = data.get("diff")
            if isinstance(diff, str) and diff.strip():
                return diff

    if tool_name not in {"write_file", "patch", "skill_manage"}:
        return None
    if not _result_succeeded(result):
        return None
    return _diff_from_snapshot(snapshot)


def _emit_inline_diff(diff_text: str, print_fn) -> bool:
    """Emit rendered diff text through the CLI's prompt_toolkit-safe printer."""
    if print_fn is None or not diff_text:
        return False
    try:
        print_fn("  ┊ review diff")
        for line in diff_text.rstrip("\n").splitlines():
            print_fn(line)
        return True
    except Exception:
        return False


def _render_inline_unified_diff(diff: str) -> list[str]:
    """Render unified diff lines in Voidcube' inline transcript style."""
    rendered: list[str] = []
    from_file = None
    to_file = None

    for raw_line in diff.splitlines():
        if raw_line.startswith("--- "):
            from_file = raw_line[4:].strip()
            continue
        if raw_line.startswith("+++ "):
            to_file = raw_line[4:].strip()
            if from_file or to_file:
                rendered.append(f"{_diff_file()}{from_file or 'a/?'} → {to_file or 'b/?'}{_ANSI_RESET}")
            continue
        if raw_line.startswith("@@"):
            rendered.append(f"{_diff_hunk()}{raw_line}{_ANSI_RESET}")
            continue
        if raw_line.startswith("-"):
            rendered.append(f"{_diff_minus()}{raw_line}{_ANSI_RESET}")
            continue
        if raw_line.startswith("+"):
            rendered.append(f"{_diff_plus()}{raw_line}{_ANSI_RESET}")
            continue
        if raw_line.startswith(" "):
            rendered.append(f"{_diff_dim()}{raw_line}{_ANSI_RESET}")
            continue
        if raw_line:
            rendered.append(raw_line)

    return rendered


def _split_unified_diff_sections(diff: str) -> list[str]:
    """Split a unified diff into per-file sections."""
    sections: list[list[str]] = []
    current: list[str] = []

    for line in diff.splitlines():
        if line.startswith("--- ") and current:
            sections.append(current)
            current = [line]
            continue
        current.append(line)

    if current:
        sections.append(current)

    return ["\n".join(section) for section in sections if section]


def _summarize_rendered_diff_sections(
    diff: str,
    *,
    max_files: int = _MAX_INLINE_DIFF_FILES,
    max_lines: int = _MAX_INLINE_DIFF_LINES,
) -> list[str]:
    """Render diff sections while capping file count and total line count."""
    sections = _split_unified_diff_sections(diff)
    rendered: list[str] = []
    omitted_files = 0
    omitted_lines = 0

    for idx, section in enumerate(sections):
        if idx >= max_files:
            omitted_files += 1
            omitted_lines += len(_render_inline_unified_diff(section))
            continue

        section_lines = _render_inline_unified_diff(section)
        remaining_budget = max_lines - len(rendered)
        if remaining_budget <= 0:
            omitted_lines += len(section_lines)
            omitted_files += 1
            continue

        if len(section_lines) <= remaining_budget:
            rendered.extend(section_lines)
            continue

        rendered.extend(section_lines[:remaining_budget])
        omitted_lines += len(section_lines) - remaining_budget
        omitted_files += 1 + max(0, len(sections) - idx - 1)
        for leftover in sections[idx + 1:]:
            omitted_lines += len(_render_inline_unified_diff(leftover))
        break

    if omitted_files or omitted_lines:
        summary = f"… omitted {omitted_lines} diff line(s)"
        if omitted_files:
            summary += f" across {omitted_files} additional file(s)/section(s)"
        rendered.append(f"{_diff_hunk()}{summary}{_ANSI_RESET}")

    return rendered


def render_edit_diff_with_delta(
    tool_name: str,
    result: str | None,
    *,
    function_args: dict | None = None,
    snapshot: LocalEditSnapshot | None = None,
    print_fn=None,
) -> bool:
    """Render an edit diff inline without taking over the terminal UI."""
    diff = extract_edit_diff(
        tool_name,
        result,
        function_args=function_args,
        snapshot=snapshot,
    )
    if not diff:
        return False
    try:
        rendered_lines = _summarize_rendered_diff_sections(diff)
    except Exception as exc:
        logger.debug("Could not render inline diff: %s", exc)
        return False
    return _emit_inline_diff("\n".join(rendered_lines), print_fn)


# =========================================================================
# KawaiiSpinner
# =========================================================================

class KawaiiSpinner:
    """Animated spinner with kawaii faces for CLI feedback during tool execution."""

    SPINNERS = {
        'dots': ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'],
        'bounce': ['⠁', '⠂', '⠄', '⡀', '⢀', '⠠', '⠐', '⠈'],
        'grow': ['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█', '▇', '▆', '▅', '▄', '▃', '▂'],
        'arrows': ['←', '↖', '↑', '↗', '→', '↘', '↓', '↙'],
        'star': ['✶', '✷', '✸', '✹', '✺', '✹', '✸', '✷'],
        'moon': ['🌑', '🌒', '🌓', '🌔', '🌕', '🌖', '🌗', '🌘'],
        'pulse': ['◜', '◠', '◝', '◞', '◡', '◟'],
        'brain': ['🧠', '💭', '💡', '✨', '💫', '🌟', '💡', '💭'],
        'sparkle': ['⁺', '˚', '*', '✧', '✦', '✧', '*', '˚'],
    }

    KAWAII_WAITING = [
        "⏳", "🔄", "⚙️", "🔧", "📡",
        "💭", "🤔", "✨", "🌟", "💫",
    ]

    KAWAII_THINKING = [
        "🤔", "💭", "🧠", "💡", "🔍",
        "📝", "🎯", "⚡", "🔥", "💪",
        "🚀", "🎨", "🔮", "🎪", "🌈",
    ]

    THINKING_VERBS = [
        "思考中", "分析中", "计算中", "推演中", "规划中",
        "检索中", "整合中", "处理中", "生成中", "学习中",
        "理解中", "评估中", "优化中", "编译中", "调度中",
    ]

    def __init__(self, message: str = "", spinner_type: str = 'dots', print_fn=None):
        self.message = message
        self.spinner_frames = self.SPINNERS.get(spinner_type, self.SPINNERS['dots'])
        self.running = False
        self.thread = None
        self.frame_idx = 0
        self.start_time = None
        self.last_line_len = 0
        # Optional callable to route all output through (e.g. a no-op for silent
        # background agents).  When set, bypasses self._out entirely so that
        # agents with _print_fn overridden remain fully silent.
        self._print_fn = print_fn
        # Capture stdout NOW, before any redirect_stdout(devnull) from
        # child agents can replace sys.stdout with a black hole.
        self._out = sys.stdout

    def _write(self, text: str, end: str = '\n', flush: bool = False):
        """Write to the stdout captured at spinner creation time.

        If a print_fn was supplied at construction, all output is routed through
        it instead — allowing callers to silence the spinner with a no-op lambda.
        """
        if self._print_fn is not None:
            try:
                self._print_fn(text)
            except Exception:
                pass
            return
        try:
            self._out.write(text + end)
            if flush:
                self._out.flush()
        except (ValueError, OSError):
            pass

    @property
    def _is_tty(self) -> bool:
        """Check if output is a real terminal, safe against closed streams."""
        try:
            return hasattr(self._out, 'isatty') and self._out.isatty()
        except (ValueError, OSError):
            return False

    def _is_patch_stdout_proxy(self) -> bool:
        """Return True when stdout is prompt_toolkit's StdoutProxy.

        patch_stdout wraps sys.stdout in a StdoutProxy that queues writes and
        injects newlines around each flush().  The \\r overwrite never lands on
        the correct line — each spinner frame ends up on its own line.

        The CLI already drives a TUI widget (_spinner_text) for spinner display,
        so KawaiiSpinner's \\r-based animation is redundant under StdoutProxy.
        """
        try:
            from prompt_toolkit.patch_stdout import StdoutProxy
            return isinstance(self._out, StdoutProxy)
        except ImportError:
            return False

    def _animate(self):
        # When stdout is not a real terminal (e.g. Docker, systemd, pipe),
        # skip the animation entirely — it creates massive log bloat.
        # Just log the start once and let stop() log the completion.
        if not self._is_tty:
            self._write(f"  [tool] {self.message}", flush=True)
            while self.running:
                time.sleep(0.5)
            return

        # When running inside prompt_toolkit's patch_stdout context the CLI
        # renders spinner state via a dedicated TUI widget (_spinner_text).
        # Driving a \r-based animation here too causes visual overdraw: the
        # StdoutProxy injects newlines around each flush, so every frame lands
        # on a new line and overwrites the status bar.
        if self._is_patch_stdout_proxy():
            while self.running:
                time.sleep(0.1)
            return

        while self.running:
            if os.getenv("VOIDCUBE_SPINNER_PAUSE"):
                time.sleep(0.1)
                continue
            frame = self.spinner_frames[self.frame_idx % len(self.spinner_frames)]
            elapsed = time.time() - self.start_time
            line = f"  {frame} {self.message} ({elapsed:.1f}s)"
            pad = max(self.last_line_len - len(line), 0)
            self._write(f"\r{line}{' ' * pad}", end='', flush=True)
            self.last_line_len = len(line)
            self.frame_idx += 1
            time.sleep(0.12)

    def start(self):
        if self.running:
            return
        self.running = True
        self.start_time = time.time()
        self.thread = threading.Thread(target=self._animate, daemon=True)
        self.thread.start()

    def update_text(self, new_message: str):
        self.message = new_message

    def print_above(self, text: str):
        """Print a line above the spinner without disrupting animation.

        Clears the current spinner line, prints the text, and lets the
        next animation tick redraw the spinner on the line below.
        Thread-safe: uses the captured stdout reference (self._out).
        Works inside redirect_stdout(devnull) because _write bypasses
        sys.stdout and writes to the stdout captured at spinner creation.
        """
        if not self.running:
            self._write(f"  {text}", flush=True)
            return
        # Clear spinner line with spaces (not \033[K) to avoid garbled escape
        # codes when prompt_toolkit's patch_stdout is active — same approach
        # as stop(). Then print text; spinner redraws on next tick.
        blanks = ' ' * max(self.last_line_len + 5, 40)
        self._write(f"\r{blanks}\r  {text}", flush=True)

    def stop(self, final_message: str = None):
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.5)

        is_tty = self._is_tty
        if is_tty:
            # Clear the spinner line with spaces instead of \033[K to avoid
            # garbled escape codes when prompt_toolkit's patch_stdout is active.
            blanks = ' ' * max(self.last_line_len + 5, 40)
            self._write(f"\r{blanks}\r", end='', flush=True)
        if final_message:
            elapsed = f" ({time.time() - self.start_time:.1f}s)" if self.start_time else ""
            if is_tty:
                self._write(f"  {final_message}", flush=True)
            else:
                self._write(f"  [done] {final_message}{elapsed}", flush=True)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False


# =========================================================================
# Cute tool message (completion line that replaces the spinner)
# =========================================================================

def _detect_tool_failure(tool_name: str, result: str | None) -> tuple[bool, str]:
    """Inspect a tool result string for signs of failure.

    Returns ``(is_failure, suffix)`` where *suffix* is an informational tag
    like ``" [exit 1]"`` for terminal failures, or ``" [error]"`` for generic
    failures.  On success, returns ``(False, "")``.
    """
    if result is None:
        return False, ""

    if tool_name == "terminal":
        data = safe_json_loads(result)
        if isinstance(data, dict):
            exit_code = data.get("exit_code")
            if exit_code is not None and exit_code != 0:
                return True, f" [exit {exit_code}]"
        return False, ""

    # Memory-specific: distinguish "full" from real errors
    if tool_name == "memory":
        data = safe_json_loads(result)
        if isinstance(data, dict):
            if data.get("success") is False and "exceed the limit" in data.get("error", ""):
                return True, " [full]"

    # Generic heuristic for non-terminal tools
    lower = result[:500].lower()
    if '"error"' in lower or '"failed"' in lower or result.startswith("Error"):
        return True, " [error]"

    return False, ""


def get_cute_tool_message(
    tool_name: str, args: dict, duration: float, result: str | None = None,
) -> str:
    """Generate a formatted tool completion line for CLI quiet mode.

    Format: ``| {emoji} {verb:9} {detail}  {duration}``

    When *result* is provided the line is checked for failure indicators.
    Failed tool calls get a red prefix and an informational suffix.
    """
    dur = f"{duration:.1f}s"
    is_failure, failure_suffix = _detect_tool_failure(tool_name, result)

    def _display_width(s: str) -> int:
        """Return the terminal display width of a string.

        Delegates to prompt_toolkit's battle-tested get_cwidth which handles
        CJK wide chars, zero-width combining marks, and Unicode version updates.
        """
        try:
            from prompt_toolkit.utils import get_cwidth
        except ImportError:
            # Fallback when prompt_toolkit is not available (rare)
            return len(s)
        return sum(get_cwidth(ch) for ch in s)

    def _pad(text: str, target_width: int) -> str:
        """Pad *text* with spaces so its display width equals *target_width*."""
        current = _display_width(text)
        if current >= target_width:
            return text
        return text + " " * (target_width - current)

    def _trunc(s, n=40):
        s = str(s)
        if _tool_preview_max_len == 0:
            return s  # no limit
        return (s[:n-3] + "...") if len(s) > n else s

    def _path(p, n=35):
        p = str(p)
        if _tool_preview_max_len == 0:
            return p  # no limit
        return ("..." + p[-(n-3):]) if len(p) > n else p

    def _wrap(line: str) -> str:
        """Apply the failure suffix to a fixed-format tool line."""
        if not is_failure:
            return line
        return f"{line}{failure_suffix}"

    if tool_name == "web_search":
        return _wrap(f"┊ 🔍 {_pad('搜索', 8)} {_trunc(args.get('query', ''), 42)}  {dur}")
    if tool_name == "web_extract":
        urls = args.get("urls", [])
        if urls:
            url = urls[0] if isinstance(urls, list) else str(urls)
            domain = url.replace("https://", "").replace("http://", "").split("/")[0]
            extra = f" +{len(urls)-1}" if len(urls) > 1 else ""
            return _wrap(f"┊ 📄 {_pad('抓取', 8)} {_trunc(domain, 35)}{extra}  {dur}")
        return _wrap(f"┊ 📄 {_pad('抓取', 8)} pages  {dur}")
    if tool_name == "web_crawl":
        url = args.get("url", "")
        domain = url.replace("https://", "").replace("http://", "").split("/")[0]
        return _wrap(f"┊ 🕸️  {_pad('爬取', 8)} {_trunc(domain, 35)}  {dur}")
    if tool_name == "terminal":
        return _wrap(f"┊ 💻 {_pad('终端', 8)} {_trunc(args.get('command', ''), 42)}  {dur}")
    if tool_name == "process":
        action = args.get("action", "?")
        sid = args.get("session_id", "")[:12]
        labels = {"list": "进程列表", "poll": f"轮询 {sid}", "log": f"日志 {sid}",
                  "wait": f"等待 {sid}", "kill": f"终止 {sid}", "write": f"写入 {sid}", "submit": f"提交 {sid}"}
        return _wrap(f"┊ >️  {_pad('进程', 8)} {labels.get(action, f'{action} {sid}')}  {dur}")
    if tool_name == "read_file":
        return _wrap(f"┊ 📖 {_pad('读取', 8)} {_path(args.get('path', ''))}  {dur}")
    if tool_name == "write_file":
        return _wrap(f"┊ ✍️  {_pad('写入', 8)} {_path(args.get('path', ''))}  {dur}")
    if tool_name == "patch":
        return _wrap(f"┊ 🔧 {_pad('补丁', 8)} {_path(args.get('path', ''))}  {dur}")
    if tool_name == "search_files":
        pattern = _trunc(args.get("pattern", ""), 35)
        target = args.get("target", "content")
        verb = "查找文件" if target == "files" else "搜索内容"
        return _wrap(f"┊ 🔎 {_pad(verb, 8)} {pattern}  {dur}")
    if tool_name == "browser_navigate":
        url = args.get("url", "")
        domain = url.replace("https://", "").replace("http://", "").split("/")[0]
        return _wrap(f"┊ 🌐 {_pad('导航', 8)} {_trunc(domain, 35)}  {dur}")
    if tool_name == "browser_snapshot":
        mode = "full" if args.get("full") else "compact"
        return _wrap(f"┊ 📸 {_pad('快照', 8)}{mode}  {dur}")
    if tool_name == "browser_click":
        return _wrap(f"┊ 👆 {_pad('点击', 8)}{args.get('ref', '?')}  {dur}")
    if tool_name == "browser_type":
        return _wrap(f"┊ ⌨️  {_pad('输入', 8)}\"{_trunc(args.get('text', ''), 30)}\"  {dur}")
    if tool_name == "browser_scroll":
        d = args.get("direction", "down")
        arrow = {"down": "↓", "up": "↑", "right": "→", "left": "←"}.get(d, "↓")
        return _wrap(f"┊ {arrow} {_pad('滚动', 8)}{d}  {dur}")
    if tool_name == "browser_back":
        return _wrap(f"┊ ◀️  {_pad('返回', 8)}{dur}")
    if tool_name == "browser_press":
        return _wrap(f"┊ ⌨️  {_pad('按键', 8)}{args.get('key', '?')}  {dur}")
    if tool_name == "browser_get_images":
        return _wrap(f"┊ 🖼️  {_pad('图像', 8)}提取中  {dur}")
    if tool_name == "browser_vision":
        return _wrap(f"┊ 👁️  {_pad('视觉', 8)}分析中  {dur}")
    if tool_name == "todo":
        todos_arg = args.get("todos")
        merge = args.get("merge", False)
        if todos_arg is None:
            return _wrap(f"┊ 📋 {_pad('计划', 8)}读取任务  {dur}")
        elif merge:
            return _wrap(f"┊ 📋 {_pad('计划', 8)}更新 {len(todos_arg)} 项  {dur}")
        else:
            return _wrap(f"┊ 📋 {_pad('计划', 8)}{len(todos_arg)} 项  {dur}")
    if tool_name == "session_search":
        return _wrap(f"┊ 🔍 {_pad('记忆搜索', 8)}\"{_trunc(args.get('query', ''), 35)}\"  {dur}")
    if tool_name == "memory":
        action = args.get("action", "?")
        target = args.get("target", "")
        if action == "add":
            return _wrap(f"┊ 🧠 {_pad('记忆', 8)}+{target}: \"{_trunc(args.get('content', ''), 30)}\"  {dur}")
        elif action == "replace":
            return _wrap(f"┊ 🧠 {_pad('记忆', 8)}~{target}: \"{_trunc(args.get('old_text', ''), 20)}\"  {dur}")
        elif action == "remove":
            return _wrap(f"┊ 🧠 {_pad('记忆', 8)}-{target}: \"{_trunc(args.get('old_text', ''), 20)}\"  {dur}")
        return _wrap(f"┊ 🧠 {_pad('记忆', 8)}{action}  {dur}")
    if tool_name == "skills_list":
        return _wrap(f"┊ 📚 {_pad('技能列表', 8)}list {args.get('category', 'all')}  {dur}")
    if tool_name == "skill_view":
        return _wrap(f"┊ 📚 {_pad('技能查看', 8)}{_trunc(args.get('name', ''), 30)}  {dur}")
    if tool_name == "image_generate":
        return _wrap(f"┊ 🎨 {_pad('创作', 8)}{_trunc(args.get('prompt', ''), 35)}  {dur}")
    if tool_name == "text_to_speech":
        return _wrap(f"┊ 🔊 {_pad('朗读', 8)}{_trunc(args.get('text', ''), 30)}  {dur}")
    if tool_name == "vision_analyze":
        return _wrap(f"┊ 👁️  {_pad('视觉分析', 8)}{_trunc(args.get('question', ''), 30)}  {dur}")
    if tool_name == "mixture_of_agents":
        return _wrap(f"┊ 🧠 {_pad('深度推理', 8)}{_trunc(args.get('user_prompt', ''), 30)}  {dur}")
    if tool_name == "scheduled_task":
        action_labels = {
            "list": "查看", "create": "创建", "update": "修改",
            "pause": "暂停", "resume": "恢复", "delete": "删除",
        }
        action = str(args.get("action") or "管理")
        label = args.get("title") or args.get("schedule_id") or "任务列表"
        return _wrap(
            f"┊ ⏱ {_pad('定时任务', 8)}{action_labels.get(action, action)} {_trunc(str(label), 24)}  {dur}"
        )
    if tool_name.startswith("rl_"):
        rl = {
            "rl_list_environments": "环境列表", "rl_select_environment": f"选择 {args.get('name', '')}",
            "rl_get_current_config": "当前配置", "rl_edit_config": f"设置 {args.get('field', '?')}",
            "rl_start_training": "开始训练", "rl_check_status": f"状态 {args.get('run_id', '?')[:12]}",
            "rl_stop_training": f"停止 {args.get('run_id', '?')[:12]}", "rl_get_results": f"结果 {args.get('run_id', '?')[:12]}",
            "rl_list_runs": "运行列表", "rl_test_inference": "测试推理",
        }
        return _wrap(f"┊ 🧪 强化学习   {rl.get(tool_name, tool_name.replace('rl_', ''))}  {dur}")
    if tool_name == "execute_code":
        code = args.get("code", "")
        first_line = code.strip().split("\n")[0] if code.strip() else ""
        return _wrap(f"┊ 🐍 {_pad('代码执行', 8)}{_trunc(first_line, 35)}  {dur}")
    if tool_name == "delegate_task":
        tasks = args.get("tasks")
        if tasks and isinstance(tasks, list):
            return _wrap(f"┊ 🔀 {_pad('委派', 8)}{len(tasks)} 个并行任务  {dur}")
        return _wrap(f"┊ 🔀 {_pad('委派', 8)}{_trunc(args.get('goal', ''), 35)}  {dur}")

    _OPS_MESSAGES = {
        "system_info": lambda a: "┊ 📊 系统信息   概览",
        "cpu_stats": lambda a: "┊ 📊 CPU       状态",
        "memory_stats": lambda a: "┊ 💾 内存       状态",
        "disk_usage": lambda a: f"┊ 💿 磁盘       {_path(a.get('path', '/'))}",
        "disk_partitions": lambda a: "┊ 💿 磁盘       分区",
        "top_processes": lambda a: f"┊ 📋 进程       Top {a.get('limit', 10)} by {a.get('sort_by', 'cpu')}",
        "service_status": lambda a: f"┊ 🔧 服务       {_trunc(a.get('name', ''), 25)} 状态",
        "service_start": lambda a: f"┊ ▶️  服务       {_trunc(a.get('name', ''), 25)} 启动",
        "service_stop": lambda a: f"┊ ⏹️  服务       {_trunc(a.get('name', ''), 25)} 停止",
        "service_restart": lambda a: f"┊ 🔄 服务       {_trunc(a.get('name', ''), 25)} 重启",
        "service_enable": lambda a: f"┊ ✅ 服务       {_trunc(a.get('name', ''), 25)} 启用",
        "service_disable": lambda a: f"┊ ❌ 服务       {_trunc(a.get('name', ''), 25)} 禁用",
        "service_logs": lambda a: f"┊ 📜 服务       {_trunc(a.get('name', ''), 25)} 日志",
        "list_services": lambda a: f"┊ 📋 服务       列表 {a.get('state', 'all')}",
        "ping": lambda a: f"┊ 📡 网络检测    {_trunc(a.get('host', ''), 30)}",
        "check_port": lambda a: f"┊ 🔌 端口检查    {a.get('host', '?')}:{a.get('port', '?')}",
        "scan_ports": lambda a: f"┊ 🔍 端口扫描    {_trunc(a.get('host', ''), 25)} {a.get('ports', '')}",
        "net_connections": lambda a: "┊ 🔗 网络连接    列表",
        "net_interfaces": lambda a: "┊ 🌐 网络接口    列表",
        "dns_lookup": lambda a: f"┊ 🔍 DNS        {_trunc(a.get('domain', ''), 30)}",
        "curl_check": lambda a: f"┊ 🌐 网页请求    {_trunc(a.get('url', ''), 30)}",
        "traceroute": lambda a: f"┊ 🗺️  路由追踪   {_trunc(a.get('host', ''), 30)}",
        "pkg_install": lambda a: f"┊ 📦 安装       {_trunc(a.get('packages', ''), 25)}",
        "pkg_update": lambda a: "┊ 📦 更新       缓存",
        "pkg_upgrade": lambda a: "┊ 📦 升级       全部",
        "pkg_remove": lambda a: f"┊ 🗑️  卸载       {_trunc(a.get('packages', ''), 25)}",
        "pkg_search": lambda a: f"┊ 🔍 搜索       {_trunc(a.get('query', ''), 25)}",
        "pkg_list_installed": lambda a: "┊ 📋 软件包     列表",
        "docker_ps": lambda a: "┊ 🐳 Docker    容器列表",
        "docker_images": lambda a: "┊ 🐳 Docker    镜像列表",
        "docker_run": lambda a: f"┊ 🐳 Docker    运行 {_trunc(a.get('image', ''), 25)}",
        "docker_stop": lambda a: f"┊ 🐳 Docker    停止 {_trunc(a.get('container', ''), 20)}",
        "docker_start": lambda a: f"┊ 🐳 Docker    启动 {_trunc(a.get('container', ''), 20)}",
        "docker_restart": lambda a: f"┊ 🐳 Docker    重启 {_trunc(a.get('container', ''), 18)}",
        "docker_rm": lambda a: f"┊ 🐳 Docker    删除 {_trunc(a.get('container', ''), 20)}",
        "docker_logs": lambda a: f"┊ 📜 Docker    日志 {_trunc(a.get('container', ''), 20)}",
        "docker_exec": lambda a: f"┊ 🐳 Docker    执行 {_trunc(a.get('container', ''), 15)}",
        "docker_compose_up": lambda a: "┊ 🐳 Compose   启动",
        "docker_compose_down": lambda a: "┊ 🐳 Compose   停止",
        "read_log": lambda a: f"┊ 📜 日志       读取 {_path(a.get('path', ''))}",
        "journalctl": lambda a: f"┊ 📜 系统日志    {_trunc(a.get('unit', 'all'), 25)}",
        "analyze_log": lambda a: f"┊ 📊 日志分析    {_path(a.get('path', ''))}",
        "firewall_status": lambda a: "┊ 🛡️  防火墙     状态",
        "firewall_allow": lambda a: f"┊ 🛡️  防火墙     允许 {a.get('port', '?')}/{a.get('protocol', 'tcp')}",
        "firewall_deny": lambda a: f"┊ 🛡️  防火墙     拒绝 {a.get('port', '?')}/{a.get('protocol', 'tcp')}",
        "list_users": lambda a: "┊ 👤 用户       列表",
        "user_add": lambda a: f"┊ 👤 用户       添加 {_trunc(a.get('username', ''), 25)}",
        "user_del": lambda a: f"┊ 👤 用户       删除 {_trunc(a.get('username', ''), 25)}",
        "file_permissions": lambda a: f"┊ 🔐 文件权限    {_path(a.get('path', ''))}",
        "set_permissions": lambda a: f"┊ 🔐 perm      {_path(a.get('path', ''))} {a.get('mode', '')}",
        "ssh_keygen": lambda a: f"┊ 🔑 ssh       keygen {a.get('key_type', 'ed25519')}",
    }
    _ops_fn = _OPS_MESSAGES.get(tool_name)
    if _ops_fn:
        return _wrap(f"{_ops_fn(args)}  {dur}")

    preview = build_tool_preview(tool_name, args) or ""
    return _wrap(f"┊ 🔧 {tool_name[:9]:9} {_trunc(preview, 35)}  {dur}")


# =========================================================================
# Memory session line
# =========================================================================

_DIM = "\033[2m"
_SKY_BLUE = "\033[38;5;117m"
_ANSI_RESET = "\033[0m"


# =========================================================================
# Context pressure display (CLI user-facing warnings)
# =========================================================================

# ANSI color codes for context pressure tiers
_CYAN = "\033[36m"
_YELLOW = "\033[33m"
_BOLD = "\033[1m"
_DIM_ANSI = "\033[2m"

# Bar characters
_BAR_FILLED = "▰"
_BAR_EMPTY = "▱"
_BAR_WIDTH = 20


def format_context_pressure(
    compaction_progress: float,
    threshold_tokens: int,
    threshold_percent: float,
    compression_enabled: bool = True,
) -> str:
    """Build a formatted context pressure line for CLI display.

    The bar and percentage show progress toward the compaction threshold,
    NOT the raw context window.  100% = compaction fires.

    Args:
        compaction_progress: How close to compaction (0.0–1.0, 1.0 = fires).
        threshold_tokens: Compaction threshold in tokens.
        threshold_percent: Compaction threshold as a fraction of context window.
        compression_enabled: Whether auto-compression is active.
    """
    pct_int = min(int(compaction_progress * 100), 100)
    filled = min(int(compaction_progress * _BAR_WIDTH), _BAR_WIDTH)
    bar = _BAR_FILLED * filled + _BAR_EMPTY * (_BAR_WIDTH - filled)

    threshold_k = f"{threshold_tokens // 1000}k" if threshold_tokens >= 1000 else str(threshold_tokens)
    threshold_pct_int = int(threshold_percent * 100)

    color = f"{_BOLD}{_YELLOW}"
    icon = "⚠"
    if compression_enabled:
        hint = "compaction approaching"
    else:
        hint = "no auto-compaction"

    return (
        f"  {color}{icon} context {bar} {pct_int}% to compaction{_ANSI_RESET}"
        f"  {_DIM_ANSI}{threshold_k} threshold ({threshold_pct_int}%) · {hint}{_ANSI_RESET}"
    )


def format_context_pressure_gateway(
    compaction_progress: float,
    threshold_percent: float,
    compression_enabled: bool = True,
) -> str:
    """Build a plain-text context pressure notification for messaging platforms.

    No ANSI — just Unicode and plain text suitable for messaging platforms.
    The percentage shows progress toward the compaction threshold.
    """
    pct_int = min(int(compaction_progress * 100), 100)
    filled = min(int(compaction_progress * _BAR_WIDTH), _BAR_WIDTH)
    bar = _BAR_FILLED * filled + _BAR_EMPTY * (_BAR_WIDTH - filled)

    threshold_pct_int = int(threshold_percent * 100)

    icon = "⚠️"
    if compression_enabled:
        hint = f"Context compaction approaching (threshold: {threshold_pct_int}% of window)."
    else:
        hint = "Auto-compaction is disabled — context may be truncated."

    return f"{icon} Context: {bar} {pct_int}% to compaction\n{hint}"
