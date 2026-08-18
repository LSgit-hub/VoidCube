#!/usr/bin/env python3
"""
Subagent Display System - structured subagent visualization

Provides CLI tracking for subagent execution including:
- Compact lifecycle output
- Thinking/reasoning state
- Background task management (/tasks command)
- Color-coded output with depth indicators

Provides compact progress and result rendering for child agents.
"""

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from ...domain.contracts.execution import ExecutionState

# ANSI color codes for terminal styling
class Colors:
    # Status colors
    ACTIVE = "\033[92m"      # Green - active/running
    WAITING = "\033[93m"     # Yellow - waiting
    SUCCESS = "\033[32m"     # Bright green - completed
    ERROR = "\033[91m"       # Red - failed/error
    INFO = "\033[94m"        # Blue - info
    PURPLE = "\033[95m"      # Magenta - subagent
    CYAN = "\033[96m"        # Cyan - thinking
    
    # Depth/structure colors
    DIM = "\033[2m"          # Dim
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    
    # Tree characters
    VERTICAL = "\033[90m│\033[0m"      # Gray vertical line
    BRANCH = "\033[90m├─\033[0m"       # Branch
    LAST_BRANCH = "\033[90m└─\033[0m"  # Last branch
    SPACE = "  "                         # Indent space
    
    RESET = "\033[0m"


class SubagentStatus(Enum):
    """Subagent lifecycle states."""
    PENDING = "pending"      # Task queued, not started
    STARTING = "starting"   # Initializing
    RUNNING = "running"      # Actively executing
    THINKING = "thinking"    # Model reasoning
    TOOL_CALL = "tool_call" # Executing tool
    COMPLETED = "completed"  # Finished successfully
    FAILED = "failed"       # Finished with error
    INTERRUPTED = "interrupted"  # User interrupted
    CANCELLED = "cancelled"  # Cancelled


def _execution_state_label(state: ExecutionState) -> str:
    """Return the Chinese label used for a tool's terminal state."""
    return {
        ExecutionState.FAILED: "失败",
        ExecutionState.CANCELLED: "已取消",
        ExecutionState.TIMED_OUT: "超时",
        ExecutionState.UNKNOWN: "未知状态",
    }.get(state, state.value)


@dataclass
class ToolCallEntry:
    """A single tool call with metadata."""
    tool_name: str
    timestamp: float
    args_preview: str = ""
    status: str = "running"  # running, completed, error
    result_preview: str = ""
    depth: int = 1
    iteration: int = 0
    duration_ms: float = 0


@dataclass(frozen=True)
class SubagentEvent:
    """One ordered lifecycle event retained for explicit task inspection."""

    timestamp: float
    kind: str
    label: str
    detail: str = ""
    state: str = ""


@dataclass
class SubagentTask:
    """Represents a subagent task with full tracking."""
    task_id: str
    task_index: int
    goal: str
    goal_preview: str = ""
    status: SubagentStatus = SubagentStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: float = 0
    completed_at: float = 0
    duration_seconds: float = 0
    
    # Tool call tracking
    tool_calls: List[ToolCallEntry] = field(default_factory=list)
    events: List[SubagentEvent] = field(default_factory=list)
    
    # Reasoning/thinking
    thinking_steps: List[str] = field(default_factory=list)
    current_thinking: str = ""
    
    # Progress
    current_tool: str = ""
    current_tool_preview: str = ""
    iteration: int = 0
    max_iterations: int = 50
    api_calls: int = 0
    
    # Results
    summary: str = ""
    error: str = ""
    exit_reason: str = ""
    
    # Model info
    model: str = ""
    tokens_used: Dict[str, int] = field(default_factory=dict)
    
    # Background status
    is_background: bool = False
    background_id: str = ""
    
    def __post_init__(self):
        if not self.goal_preview and self.goal:
            self.goal_preview = self.goal[:60] + ("..." if len(self.goal) > 60 else "")


class SubagentDisplayManager:
    """
    Tracks subagent execution and emits bounded lifecycle events.
    
    Features:
    - Compact lifecycle output
    - Tool call and reasoning state
    - Background task tracking
    - Color-coded output with depth indicators
    
    Usage:
        manager = SubagentDisplayManager()
        task = manager.create_task(task_id, goal, max_iterations=50)
        
        # Update from callbacks
        manager.on_thinking(task_id, "Analyzing the codebase...")
        manager.on_tool_start(task_id, "read_file", "src/main.py", depth=1)
        manager.on_tool_complete(task_id, "read_file")
        manager.on_complete(task_id, summary="Found 3 issues...")
        
    """
    
    # Status indicators
    STATUS_ICONS = {
        SubagentStatus.PENDING: "○",
        SubagentStatus.STARTING: "◐",
        SubagentStatus.RUNNING: "●",
        SubagentStatus.THINKING: "◔",
        SubagentStatus.TOOL_CALL: "◆",
        SubagentStatus.COMPLETED: "✓",
        SubagentStatus.FAILED: "✗",
        SubagentStatus.INTERRUPTED: "⊘",
        SubagentStatus.CANCELLED: "⊗",
    }

    STATUS_LABELS = {
        SubagentStatus.PENDING: "排队",
        SubagentStatus.STARTING: "启动中",
        SubagentStatus.RUNNING: "工作中",
        SubagentStatus.THINKING: "思考中",
        SubagentStatus.TOOL_CALL: "执行中",
        SubagentStatus.COMPLETED: "完成",
        SubagentStatus.FAILED: "失败",
        SubagentStatus.INTERRUPTED: "已中断",
        SubagentStatus.CANCELLED: "已取消",
    }
    
    # Tool emojis for quick recognition
    TOOL_EMOJIS = {
        "terminal": "⌨",
        "read_file": "📄",
        "write_file": "📝",
        "patch": "📌",
        "search_files": "🔍",
        "grep": "🔎",
        "web_search": "🌐",
        "web_extract": "🌐",
        "browser_navigate": "🌐",
        "execute_code": "⚡",
        "delegate_task": "🔀",
        "memory": "🧠",
        "edit": "✏",
        "bash": "⌨",
        "file": "📁",
        "glob": "🔎",
    }
    
    def __init__(
        self,
        max_tool_args_len: int = 50,
        output_lock: Optional[threading.Lock] = None,
    ):
        self.max_tool_args_len = max_tool_args_len
        
        # Task storage
        self._tasks: Dict[str, SubagentTask] = {}
        self._background_tasks: Dict[str, SubagentTask] = {}  # Background tasks
        
        # Output control
        self._lock = output_lock or threading.RLock()
        
        # Print function (can be overridden)
        self._print_fn: Optional[Callable] = None
        
        # Track active tools for each task (for nesting)
        self._active_tools: Dict[str, List[str]] = {}
        
    @property
    def print_fn(self) -> Callable:
        """Get the print function."""
        if self._print_fn:
            return self._print_fn
        return self._safe_print
    
    @print_fn.setter
    def print_fn(self, fn: Callable) -> None:
        """Set a custom print function; output remains serialized by self._lock."""
        if fn is None:
            self._print_fn = None
            return

        def _locked(*args, **kwargs) -> None:
            with self._lock:
                fn(*args, **kwargs)

        self._print_fn = _locked
    
    def _safe_print(self, *args, **kwargs) -> None:
        """Thread-safe print with optional output lock."""
        kwargs.setdefault("flush", True)
        with self._lock:
            print(*args, **kwargs)
    
    # =====================================================================
    # Task Management
    # =====================================================================
    
    def create_task(
        self,
        task_id: str,
        goal: str,
        task_index: int = 0,
        max_iterations: int = 50,
        is_background: bool = False,
    ) -> SubagentTask:
        """Create a new task tracking entry."""
        task = SubagentTask(
            task_id=task_id,
            task_index=task_index,
            goal=goal,
            goal_preview=goal[:60] + ("..." if len(goal) > 60 else ""),
            max_iterations=max_iterations,
            status=SubagentStatus.PENDING,
            is_background=is_background,
        )
        
        with self._lock:
            self._record_event(task, "created", "已创建", task.goal_preview)
            self._tasks[task_id] = task
            if is_background:
                self._background_tasks[task_id] = task
            self._active_tools[task_id] = []
        
        return task
    
    def get_task(self, task_id: str) -> Optional[SubagentTask]:
        """Get a task by ID."""
        return self._tasks.get(task_id)
    
    def list_tasks(self, include_background: bool = True) -> List[SubagentTask]:
        """List all tracked tasks."""
        tasks = list(self._tasks.values())
        if not include_background:
            tasks = [t for t in tasks if not t.is_background]
        return sorted(tasks, key=lambda t: t.task_index)
    
    def list_background_tasks(self) -> List[SubagentTask]:
        """List all background tasks."""
        return list(self._background_tasks.values())

    def resolve_task_ref(self, task_ref: str) -> Optional[SubagentTask]:
        """Resolve a task by task_id or 1-based task index."""
        ref = str(task_ref or "").strip()
        if not ref:
            return None

        task = self._tasks.get(ref)
        if task is not None:
            return task

        try:
            idx = int(ref)
        except ValueError:
            return None

        for task in self._tasks.values():
            if task.task_index + 1 == idx:
                return task
        return None
    
    def get_active_count(self) -> int:
        """Get count of currently running tasks."""
        return sum(
            1 for t in self._tasks.values()
            if not t.is_background and t.status in (SubagentStatus.RUNNING, SubagentStatus.THINKING,
                          SubagentStatus.TOOL_CALL, SubagentStatus.STARTING)
        )
    
    # =====================================================================
    # Event Handlers (called from callbacks)
    # =====================================================================

    @staticmethod
    def _record_event(
        task: SubagentTask,
        kind: str,
        label: str,
        detail: str = "",
        state: str = "",
    ) -> None:
        task.events.append(
            SubagentEvent(
                timestamp=time.time(),
                kind=kind,
                label=label,
                detail=detail,
                state=state,
            )
        )
    
    def on_start(self, task_id: str) -> None:
        """Mark task as started without adding scrollback noise."""
        task = self._tasks.get(task_id)
        if task:
            with self._lock:
                task.status = SubagentStatus.STARTING
                task.started_at = time.time()
                self._record_event(task, "started", "已启动")
    
    def on_thinking(self, task_id: str, thinking: str, iteration: int = 0) -> None:
        """Record thinking/reasoning step."""
        task = self._tasks.get(task_id)
        if not task:
            return
            
        with self._lock:
            task.status = SubagentStatus.THINKING
            task.current_thinking = thinking
            task.iteration = iteration
            
            # Keep last N thinking steps
            if len(task.thinking_steps) > 5:
                task.thinking_steps = task.thinking_steps[-5:]
            task.thinking_steps.append(thinking)
            self._record_event(
                task,
                "progress",
                "进展",
                self._compact_text(thinking, 300),
            )
    
    def on_tool_start(
        self,
        task_id: str,
        tool_name: str,
        args_preview: str = "",
        depth: int = 1,
        iteration: int = 0,
    ) -> None:
        """Record tool call start."""
        task = self._tasks.get(task_id)
        if not task:
            return
        
        # Truncate args preview
        if args_preview and len(args_preview) > self.max_tool_args_len:
            args_preview = args_preview[:self.max_tool_args_len] + "..."
        
        tool_call = ToolCallEntry(
            tool_name=tool_name,
            timestamp=time.time(),
            args_preview=args_preview,
            status="running",
            depth=depth,
            iteration=iteration,
        )
        
        with self._lock:
            task.status = SubagentStatus.TOOL_CALL
            task.current_tool = tool_name
            task.current_tool_preview = args_preview
            task.iteration = iteration
            task.tool_calls.append(tool_call)
            self._record_event(task, "tool_started", tool_name, args_preview)
            
            # Track nesting
            if depth > 1:
                self._active_tools.setdefault(task_id, []).append(tool_name)
    
    def on_tool_complete(
        self,
        task_id: str,
        tool_name: str,
        result_preview: str = "",
        state: ExecutionState = ExecutionState.SUCCEEDED,
        error: str = "",
    ) -> None:
        """Record tool call completion."""
        task = self._tasks.get(task_id)
        if not task:
            return

        matched = False
        # Find and update the tool call
        with self._lock:
            # Find most recent matching tool call
            for tc in reversed(task.tool_calls):
                if tc.tool_name == tool_name and tc.status == "running":
                    tc.status = state.value
                    tc.result_preview = result_preview[:200] if result_preview else ""
                    if error:
                        tc.result_preview = error[:200]
                    tc.duration_ms = (time.time() - tc.timestamp) * 1000
                    matched = True
                    
                    # Remove from nesting tracker
                    if self._active_tools.get(task_id) and tc.depth > 1:
                        try:
                            self._active_tools[task_id].remove(tool_name)
                        except ValueError:
                            pass
                    if task.current_tool == tool_name:
                        task.current_tool = ""
                        task.current_tool_preview = ""
                        if task.status is SubagentStatus.TOOL_CALL:
                            task.status = SubagentStatus.RUNNING
                    self._record_event(
                        task,
                        "tool_completed",
                        tool_name,
                        " · ".join(
                            part
                            for part in (
                                f"{tc.duration_ms / 1000:.1f}s",
                                tc.result_preview,
                            )
                            if part
                        ),
                        state.value,
                    )
                    break

        # Successful tool calls remain in the task record and live status
        # projection. Only failures belong in scrollback; printing every
        # successful tool completion turns observation into a second tool log.
        if matched and state is not ExecutionState.SUCCEEDED:
            self._print_tool_completion(task, tool_name, state)
    
    def on_api_call(self, task_id: str, iteration: int = 0) -> None:
        """Record an API call (iteration)."""
        task = self._tasks.get(task_id)
        if task:
            with self._lock:
                task.api_calls = iteration
                task.iteration = iteration
    
    def on_complete(
        self,
        task_id: str,
        summary: str = "",
        error: str = "",
        exit_reason: str = "completed",
        state: ExecutionState = ExecutionState.SUCCEEDED,
        tokens: Optional[Dict[str, int]] = None,
        model: str = "",
    ) -> None:
        """Mark task as completed."""
        task = self._tasks.get(task_id)
        if not task:
            return
        
        with self._lock:
            if state is ExecutionState.CANCELLED:
                task.status = SubagentStatus.CANCELLED
            elif state is ExecutionState.TIMED_OUT:
                task.status = SubagentStatus.FAILED
                task.error = error or "Subagent timed out."
            elif state is ExecutionState.FAILED or error:
                task.status = SubagentStatus.FAILED
                task.error = error
            else:
                task.status = SubagentStatus.COMPLETED
            task.completed_at = time.time()
            task.duration_seconds = task.completed_at - (
                task.started_at or task.created_at
            )
            task.summary = summary
            task.exit_reason = exit_reason
            if tokens:
                task.tokens_used = tokens
            if model:
                task.model = model
            self._record_event(
                task,
                "finished",
                self.STATUS_LABELS.get(task.status, task.status.value),
                self._compact_text(task.error or task.summary, 500),
                task.status.value,
            )
            

        if task.status is SubagentStatus.CANCELLED:
            self._print_cancel(task)
        else:
            self._print_final_summary(task)
    
    def on_interrupt(self, task_id: str) -> None:
        """Mark task as interrupted."""
        task = self._tasks.get(task_id)
        if task is None:
            return
        with self._lock:
            task.status = SubagentStatus.INTERRUPTED
            task.completed_at = time.time()
            task.duration_seconds = task.completed_at - (
                task.started_at or task.created_at
            )
            task.exit_reason = "interrupted"
            self._record_event(
                task,
                "finished",
                "已中断",
                state=SubagentStatus.INTERRUPTED.value,
            )

        self._print_interrupt(task)
    
    def on_cancel(self, task_id: str) -> None:
        """Mark task as cancelled."""
        task = self._tasks.get(task_id)
        if task is None:
            return
        with self._lock:
            task.status = SubagentStatus.CANCELLED
            task.completed_at = time.time()
            task.duration_seconds = task.completed_at - (
                task.started_at or task.created_at
            )
            task.exit_reason = "cancelled"
            self._record_event(
                task,
                "finished",
                "已取消",
                state=SubagentStatus.CANCELLED.value,
            )

        self._print_cancel(task)
    
    def _get_status_color(self, status: SubagentStatus) -> str:
        """Get ANSI color for status."""
        colors = {
            SubagentStatus.PENDING: Colors.DIM,
            SubagentStatus.STARTING: Colors.INFO,
            SubagentStatus.RUNNING: Colors.ACTIVE,
            SubagentStatus.THINKING: Colors.CYAN,
            SubagentStatus.TOOL_CALL: Colors.PURPLE,
            SubagentStatus.COMPLETED: Colors.SUCCESS,
            SubagentStatus.FAILED: Colors.ERROR,
            SubagentStatus.INTERRUPTED: Colors.WAITING,
            SubagentStatus.CANCELLED: Colors.DIM,
        }
        return colors.get(status, Colors.DIM)
    
    # =====================================================================
    # Print Helpers
    # =====================================================================
    
    def _print_tool_completion(
        self,
        task: SubagentTask,
        tool_name: str,
        state: ExecutionState,
    ) -> None:
        """Print tool completion inline."""
        emoji = self.TOOL_EMOJIS.get(tool_name, "🔧")
        marker = {
            ExecutionState.FAILED: "✗",
            ExecutionState.CANCELLED: "⊘",
            ExecutionState.TIMED_OUT: "⌛",
            ExecutionState.UNKNOWN: "?",
        }[state]
        self.print_fn(
            f"  {Colors.ERROR}{marker}{Colors.RESET} {emoji} {tool_name} "
            f"{Colors.ERROR}{_execution_state_label(state)}{Colors.RESET}"
        )
    def _print_final_summary(self, task: SubagentTask) -> None:
        """Print final task summary (single atomic output to avoid interleaving)."""
        prefix = f"[{task.task_index + 1}]" if task.task_index >= 0 else ""

        lines: list[str] = []
        if task.status == SubagentStatus.COMPLETED:
            lines.append(
                f"{Colors.PURPLE}{Colors.BOLD}{prefix} ✓ 子代理完成{Colors.RESET}"
                f" {Colors.DIM}· {task.duration_seconds:.1f}s{Colors.RESET}"
            )
            if task.summary:
                summary = task.summary[:500]
                if len(task.summary) > 500:
                    summary += "..."
                lines.append(f"  {summary}")
        else:
            lines.append(
                f"{Colors.ERROR}{Colors.BOLD}{prefix} ✗ 子代理失败{Colors.RESET}"
            )
            if task.error:
                lines[-1] += f" {Colors.ERROR}· {task.error[:200]}{Colors.RESET}"

        self.print_fn("\n".join(lines))
    
    def _print_interrupt(self, task: SubagentTask) -> None:
        """Print task interrupt notification."""
        prefix = f"[{task.task_index + 1}]" if task.task_index >= 0 else ""
        self.print_fn(f"\n{Colors.WAITING}{Colors.BOLD}{prefix} ⊘ 子代理已中断{Colors.RESET}")
    
    def _print_cancel(self, task: SubagentTask) -> None:
        """Print task cancel notification."""
        prefix = f"[{task.task_index + 1}]" if task.task_index >= 0 else ""
        self.print_fn(f"\n{Colors.DIM}{prefix} ⊗ 子代理已取消{Colors.RESET}")
    
    # =====================================================================
    # Tasks Command (/tasks)
    # =====================================================================
    
    def render_tasks_command(self) -> str:
        """
        Render the compact status list for /tasks.

        Tool arguments and call history stay in the task record for explicit
        diagnostics, but the default command shows only user-facing progress.
        """
        lines = [f"{Colors.BOLD}子代理{Colors.RESET}"]

        foreground_tasks = self.list_tasks(include_background=False)
        background_tasks = self.list_background_tasks()

        if not foreground_tasks and not background_tasks:
            lines.append(f"{Colors.DIM}  空闲{Colors.RESET}")
        else:
            tasks = foreground_tasks + sorted(
                background_tasks,
                key=lambda task: task.task_index,
            )
            lines.extend(self._render_task_summary_line(task) for task in tasks)
        
        return "\n".join(lines)
    
    def _render_task_summary_line(self, task: SubagentTask) -> str:
        """Render a single line task summary for /tasks output."""
        status_color = self._get_status_color(task.status)
        status_icon = self.STATUS_ICONS.get(task.status, "○")
        
        # Duration
        if task.completed_at > 0:
            duration_str = f" · {task.duration_seconds:.1f}s"
        elif task.started_at > 0:
            elapsed = time.time() - task.started_at
            duration_str = f" · {elapsed:.1f}s"
        else:
            duration_str = ""
        
        # Status line: goal first, then one actionable phase. Tool arguments
        # are intentionally omitted from the default view.
        prefix = f"[{task.task_index + 1}]" if task.task_index >= 0 else ""
        phase = self.STATUS_LABELS.get(task.status, task.status.value)
        lane = " · 后台" if task.is_background else ""
        line = f"  {status_color}{status_icon}{Colors.RESET} {prefix} {task.goal_preview}"
        line += f" {Colors.DIM}· {phase}{duration_str}{lane}{Colors.RESET}"
        
        return line

    def render_task_detail(self, task_ref: str) -> str | None:
        """Render one task's diagnostics without expanding the default list."""
        task = self.resolve_task_ref(task_ref)
        if task is None:
            return None

        status_color = self._get_status_color(task.status)
        status_icon = self.STATUS_ICONS.get(task.status, "○")
        phase = self.STATUS_LABELS.get(task.status, task.status.value)
        prefix = f"[{task.task_index + 1}]" if task.task_index >= 0 else ""
        lane = "后台" if task.is_background else "前台"
        elapsed = self._task_elapsed(task)
        lines = [
            f"{Colors.BOLD}{prefix} {self._compact_text(task.goal or task.goal_preview, 300)}{Colors.RESET}",
            (
                f"{status_color}{status_icon} {phase}{Colors.RESET}"
                f" {Colors.DIM}· {elapsed:.1f}s · {lane} · {task.task_id}{Colors.RESET}"
            ),
        ]

        if task.current_tool:
            current = f"当前工具: {task.current_tool}"
            if task.current_tool_preview:
                current += f" · {self._compact_text(task.current_tool_preview, 100)}"
            lines.append(current)
        elif task.current_thinking:
            lines.append(
                f"当前进展: {self._compact_text(task.current_thinking, 140)}"
            )

        recent_tools = task.tool_calls[-5:]
        if recent_tools:
            lines.append("")
            lines.append(f"{Colors.BOLD}最近工具{Colors.RESET}")
            for tool in recent_tools:
                marker = {
                    "running": "◌",
                    ExecutionState.SUCCEEDED.value: "✓",
                    ExecutionState.FAILED.value: "✗",
                    ExecutionState.CANCELLED.value: "⊘",
                    ExecutionState.TIMED_OUT.value: "⌛",
                    ExecutionState.UNKNOWN.value: "?",
                }.get(tool.status, "?")
                duration = (
                    f" · {tool.duration_ms / 1000:.1f}s"
                    if tool.duration_ms > 0
                    else ""
                )
                preview = (
                    f" · {self._compact_text(tool.args_preview, 80)}"
                    if tool.args_preview
                    else ""
                )
                lines.append(f"  {marker} {tool.tool_name}{duration}{preview}")
                if tool.result_preview and tool.status != ExecutionState.SUCCEEDED.value:
                    lines.append(
                        f"    {Colors.ERROR}{self._compact_text(tool.result_preview, 140)}{Colors.RESET}"
                    )

        outcome = task.error or task.summary
        if outcome:
            heading = "错误" if task.error else "结果"
            lines.extend(
                [
                    "",
                    f"{Colors.BOLD}{heading}{Colors.RESET}",
                    f"  {self._compact_text(outcome, 500)}",
                ]
            )
        return "\n".join(lines)

    def render_task_log(self, task_ref: str) -> str | None:
        """Render the full ordered lifecycle log for one subagent task."""
        task = self.resolve_task_ref(task_ref)
        if task is None:
            return None
        with self._lock:
            events = tuple(task.events)

        prefix = f"[{task.task_index + 1}]" if task.task_index >= 0 else ""
        lines = [
            f"{Colors.BOLD}{prefix} {self._compact_text(task.goal or task.goal_preview, 300)}{Colors.RESET}",
            f"{Colors.DIM}事件记录 · {task.task_id}{Colors.RESET}",
        ]
        for event in events:
            offset = max(0.0, event.timestamp - task.created_at)
            marker = self._event_marker(event)
            line = (
                f"{Colors.DIM}+{offset:06.1f}s{Colors.RESET} "
                f"{marker} {event.label}"
            )
            if event.detail:
                line += f" · {self._compact_text(event.detail, 220)}"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _event_marker(event: SubagentEvent) -> str:
        if event.kind == "tool_completed":
            return {
                ExecutionState.SUCCEEDED.value: "✓",
                ExecutionState.FAILED.value: "✗",
                ExecutionState.CANCELLED.value: "⊘",
                ExecutionState.TIMED_OUT.value: "⌛",
                ExecutionState.UNKNOWN.value: "?",
            }.get(event.state, "?")
        if event.kind == "finished":
            return {
                SubagentStatus.COMPLETED.value: "✓",
                SubagentStatus.FAILED.value: "✗",
                SubagentStatus.INTERRUPTED.value: "⊘",
                SubagentStatus.CANCELLED.value: "⊗",
            }.get(event.state, "○")
        return {
            "created": "○",
            "started": "●",
            "progress": "◔",
            "tool_started": "◆",
            "lane": "↔",
        }.get(event.kind, "·")

    @staticmethod
    def _task_elapsed(task: SubagentTask) -> float:
        if task.completed_at > 0:
            return max(0.0, task.duration_seconds)
        if task.started_at > 0:
            return max(0.0, time.time() - task.started_at)
        return 0.0

    @staticmethod
    def _compact_text(value: str, limit: int) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)] + "..."
    
    # =====================================================================
    # Background Task Management
    # =====================================================================
    
    def send_to_background(self, task_id: str) -> bool:
        """Move a running task to background."""
        task = self._tasks.get(task_id)
        if (
            not task
            or task.is_background
            or task.status in (
                SubagentStatus.COMPLETED,
                SubagentStatus.FAILED,
                SubagentStatus.INTERRUPTED,
                SubagentStatus.CANCELLED,
            )
        ):
            return False
        
        with self._lock:
            task.is_background = True
            self._background_tasks[task_id] = task
            self._record_event(task, "lane", "转入后台")
        
        self.print_fn(
            f"{Colors.INFO}→ 子代理转入后台{Colors.RESET}  {task.goal_preview}"
        )
        
        return True
    
    def bring_to_foreground(self, task_id: str) -> bool:
        """Bring a background task back to foreground."""
        task = self._tasks.get(task_id)
        if not task or not task.is_background:
            return False
        
        with self._lock:
            task.is_background = False
            if task_id in self._background_tasks:
                del self._background_tasks[task_id]
            self._record_event(task, "lane", "恢复到前台")
        
        self.print_fn(
            f"{Colors.INFO}← 子代理恢复到前台{Colors.RESET}  {task.goal_preview}"
        )
        
        return True
    
    def reset(self) -> None:
        """Reset all tracked tasks."""
        with self._lock:
            self._tasks.clear()
            self._background_tasks.clear()
            self._active_tools.clear()
