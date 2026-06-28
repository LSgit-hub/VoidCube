#!/usr/bin/env python3
"""
Subagent Display System - Claude Code-style subagent visualization

Provides rich CLI display for subagent execution including:
- Real-time status panel with animated indicators
- Tree-view tool call visualization
- Thinking/reasoning process display
- Background task management (/tasks command)
- Color-coded output with depth indicators

Inspired by Claude Code's subagent UX patterns.
"""

import asyncio
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

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
    Manages real-time display of subagent execution.
    
    Features:
    - Real-time status panel with animated indicators
    - Tree-view tool call visualization
    - Thinking/reasoning process display  
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
        
        # Render display
        manager.render()
    """
    
    # Spinner frames for animated indicators
    SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    
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
        show_thinking: bool = True,
        show_tool_args: bool = False,
        max_tool_args_len: int = 50,
        tree_indent: str = "│  ",
        auto_refresh: bool = True,
        refresh_interval: float = 0.1,
        output_lock: Optional[threading.Lock] = None,
    ):
        self.show_thinking = show_thinking
        self.show_tool_args = show_tool_args
        self.max_tool_args_len = max_tool_args_len
        self.tree_indent = tree_indent
        self.auto_refresh = auto_refresh
        self.refresh_interval = refresh_interval
        
        # Task storage
        self._tasks: Dict[str, SubagentTask] = {}
        self._background_tasks: Dict[str, SubagentTask] = {}  # Background tasks
        
        # Output control
        self._lock = output_lock or threading.Lock()
        self._last_render_lines: int = 0
        self._render_lock = threading.Lock()
        
        # Refresh thread
        self._refresh_thread: Optional[threading.Thread] = None
        self._stop_refresh = threading.Event()
        
        # Print function (can be overridden)
        self._print_fn: Optional[Callable] = None
        
        # Terminal dimensions
        self._terminal_width: int = 120
        
        # Track active tools for each task (for nesting)
        self._active_tools: Dict[str, List[str]] = {}
        
        # Track completion time for memory cleanup
        self._completed_tasks: Dict[str, float] = {}
        
    @property
    def print_fn(self) -> Callable:
        """Get the print function."""
        if self._print_fn:
            return self._print_fn
        return self._safe_print
    
    @print_fn.setter
    def print_fn(self, fn: Callable) -> None:
        """Set a custom print function."""
        self._print_fn = fn
    
    def _safe_print(self, *args, **kwargs) -> None:
        """Thread-safe print with optional output lock."""
        kwargs.setdefault("flush", True)
        with self._lock:
            print(*args, **kwargs)
    
    def _move_cursor_up(self, lines: int) -> None:
        """Move cursor up n lines."""
        self.print_fn(f"\033[{lines}A")
    
    def _clear_line(self) -> None:
        """Clear current line."""
        self.print_fn("\033[2K\r", end="")
    
    def _get_terminal_width(self) -> int:
        """Get terminal width."""
        try:
            size = os.get_terminal_size()
            return size.columns
        except (OSError, AttributeError):
            return 120
    
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
    
    def on_start(self, task_id: str) -> None:
        """Mark task as started."""
        task = self._tasks.get(task_id)
        if task:
            with self._lock:
                task.status = SubagentStatus.STARTING
                task.started_at = time.time()
            
            self._print_status_header(task)
    
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
            
            # Track nesting
            if depth > 1:
                self._active_tools.setdefault(task_id, []).append(tool_name)
    
    def on_tool_complete(
        self,
        task_id: str,
        tool_name: str,
        result_preview: str = "",
        status: str = "ok",
        error: str = "",
    ) -> None:
        """Record tool call completion."""
        task = self._tasks.get(task_id)
        if not task:
            return
        
        # Find and update the tool call
        with self._lock:
            # Find most recent matching tool call
            for tc in reversed(task.tool_calls):
                if tc.tool_name == tool_name and tc.status == "running":
                    tc.status = "error" if status == "error" else "completed"
                    tc.result_preview = result_preview[:200] if result_preview else ""
                    if error:
                        tc.result_preview = error[:200]
                    tc.duration_ms = (time.time() - tc.timestamp) * 1000
                    
                    # Remove from nesting tracker
                    if self._active_tools.get(task_id) and tc.depth > 1:
                        try:
                            self._active_tools[task_id].remove(tool_name)
                        except ValueError:
                            pass
                    break
        
        # Print completion line
        self._print_tool_completion(task, tool_name, result_preview, status)
    
    def on_api_call(self, task_id: str, iteration: int = 0) -> None:
        """Record an API call (iteration)."""
        task = self._tasks.get(task_id)
        if task:
            with self._lock:
                task.api_calls = iteration
    
    def on_complete(
        self,
        task_id: str,
        summary: str = "",
        error: str = "",
        exit_reason: str = "completed",
        tokens: Optional[Dict[str, int]] = None,
        model: str = "",
    ) -> None:
        """Mark task as completed."""
        task = self._tasks.get(task_id)
        if not task:
            return
        
        with self._lock:
            if error:
                task.status = SubagentStatus.FAILED
                task.error = error
            else:
                task.status = SubagentStatus.COMPLETED
            task.completed_at = time.time()
            task.duration_seconds = task.completed_at - task.started_at
            task.summary = summary
            task.exit_reason = exit_reason
            if tokens:
                task.tokens_used = tokens
            if model:
                task.model = model
            
            # Track completion time for cleanup
            self._completed_tasks[task_id] = time.time()
        
        # Print final summary
        self._print_final_summary(task)
    
    def on_interrupt(self, task_id: str) -> None:
        """Mark task as interrupted."""
        task = self._tasks.get(task_id)
        if task:
            with self._lock:
                task.status = SubagentStatus.INTERRUPTED
                task.completed_at = time.time()
                task.duration_seconds = task.completed_at - task.started_at
                task.exit_reason = "interrupted"
                self._completed_tasks[task_id] = time.time()
        
        self._print_interrupt(task)
    
    def on_cancel(self, task_id: str) -> None:
        """Mark task as cancelled."""
        task = self._tasks.get(task_id)
        if task:
            with self._lock:
                task.status = SubagentStatus.CANCELLED
                task.completed_at = time.time()
                task.duration_seconds = task.completed_at - task.started_at
                task.exit_reason = "cancelled"
                self._completed_tasks[task_id] = time.time()
        
        self._print_cancel(task)
    
    # =====================================================================
    # Display Rendering
    # =====================================================================
    
    def render(self, clear: bool = True) -> None:
        """Render the current state of all tasks."""
        with self._render_lock:
            with self._lock:
                tasks = self.list_tasks(include_background=False)
                active_tasks = [t for t in tasks if t.status not in 
                              (SubagentStatus.COMPLETED, SubagentStatus.FAILED, 
                               SubagentStatus.INTERRUPTED, SubagentStatus.CANCELLED)]
            
            if not tasks:
                if clear and self._last_render_lines > 0:
                    self.clear()
                return
            
            # Clear previous output
            if clear and self._last_render_lines > 0:
                self._move_cursor_up(self._last_render_lines)
            
            lines = []
            
            # Header
            lines.append(self._render_header(active_tasks))
            
            # Task panels (rendering doesn't need lock since we already copied tasks)
            for task in tasks:
                lines.extend(self._render_task(task))
            
            # Print all lines
            with self._lock:
                for line in lines:
                    self.print_fn(line)
                self._last_render_lines = len(lines)
    
    def _render_header(self, active_tasks: List[SubagentTask]) -> str:
        """Render the display header."""
        width = self._get_terminal_width()
        
        if not active_tasks:
            return f"{Colors.DIM}{'─' * width}{Colors.RESET}"
        
        # Spinner for active animation
        frame_idx = int(time.time() * 10) % len(self.SPINNER_FRAMES)
        spinner = self.SPINNER_FRAMES[frame_idx]
        
        active_count = len(active_tasks)
        if active_count == 1:
            task = active_tasks[0]
            status_text = f"{Colors.ACTIVE}{spinner}{Colors.RESET} 1 subagent running"
            if task.current_tool:
                status_text += f" - {Colors.PURPLE}{task.current_tool}{Colors.RESET}"
        else:
            status_text = f"{Colors.ACTIVE}{spinner}{Colors.RESET} {active_count} subagents running"
        
        # Background tasks count
        bg_count = len(self.list_background_tasks())
        if bg_count > 0:
            status_text += f"  {Colors.DIM}|{Colors.RESET} {Colors.INFO}{bg_count} in background{Colors.RESET}"
        
        return f"{Colors.DIM}{'─' * width}{Colors.RESET}\n{status_text}"
    
    def _render_task(self, task: SubagentTask) -> List[str]:
        """Render a single task panel."""
        lines = []
        width = self._get_terminal_width()
        
        # Status color and icon
        status_color = self._get_status_color(task.status)
        status_icon = self.STATUS_ICONS.get(task.status, "○")
        
        # Task header line
        prefix = f"[{task.task_index + 1}]" if task.task_index >= 0 else ""
        header = f"{Colors.PURPLE}{prefix} {status_icon}{status_color} {task.goal_preview}{Colors.RESET}"
        
        # Add status-specific info
        if task.status == SubagentStatus.THINKING and task.current_thinking:
            thinking_preview = task.current_thinking[:50]
            header += f"\n  {Colors.CYAN}💭 {thinking_preview}{Colors.RESET}"
        
        elif task.status == SubagentStatus.TOOL_CALL and task.current_tool:
            emoji = self.TOOL_EMOJIS.get(task.current_tool, "🔧")
            header += f"\n  {emoji} {task.current_tool}"
            if task.current_tool_preview:
                header += f" {Colors.DIM}{task.current_tool_preview}{Colors.RESET}"
        
        elif task.status == SubagentStatus.COMPLETED:
            header += f" {Colors.SUCCESS}✓{Colors.RESET}"
            header += f" {Colors.DIM}({task.duration_seconds:.1f}s){Colors.RESET}"
        
        elif task.status == SubagentStatus.FAILED:
            header += f" {Colors.ERROR}✗{Colors.RESET}"
            if task.error:
                header += f" {Colors.ERROR}{task.error[:50]}{Colors.RESET}"
        
        lines.append(header)
        
        # Tool call tree (for non-completed tasks)
        if task.tool_calls and task.status not in (SubagentStatus.COMPLETED, SubagentStatus.FAILED):
            lines.extend(self._render_tool_tree(task))
        
        # Progress bar for running tasks
        if task.status in (SubagentStatus.RUNNING, SubagentStatus.THINKING, SubagentStatus.TOOL_CALL):
            progress = min(task.iteration / task.max_iterations, 1.0)
            bar_width = 30
            filled = int(progress * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            lines.append(f"  {Colors.DIM}[{bar}] {task.iteration}/{task.max_iterations}{Colors.RESET}")
        
        # Separator
        lines.append(f"{Colors.DIM}{'─' * width}{Colors.RESET}")
        
        return lines
    
    def _render_tool_tree(self, task: SubagentTask) -> List[str]:
        """Render tool call tree visualization."""
        lines = []
        indent = "  "
        
        # Group tools by depth
        tools_by_depth: Dict[int, List[ToolCallEntry]] = {}
        for tc in task.tool_calls:
            tools_by_depth.setdefault(tc.depth, []).append(tc)
        
        # Render last N tools (up to 8)
        recent_tools = task.tool_calls[-8:] if len(task.tool_calls) > 8 else task.tool_calls
        
        for i, tc in enumerate(recent_tools):
            is_last = (i == len(recent_tools) - 1)
            branch_char = Colors.VERTICAL if not is_last else Colors.SPACE
            
            emoji = self.TOOL_EMOJIS.get(tc.tool_name, "🔧")
            status_color = Colors.SUCCESS if tc.status == "completed" else (
                          Colors.ERROR if tc.status == "error" else Colors.ACTIVE)
            
            if tc.status == "running":
                frame_idx = int(time.time() * 10) % len(self.SPINNER_FRAMES)
                icon = self.SPINNER_FRAMES[frame_idx]
            else:
                icon = "✓" if tc.status == "completed" else "✗"
            
            tool_line = f"{indent}{branch_char}{status_color}{icon}{Colors.RESET} {emoji} {tc.tool_name}"
            
            # Add args preview
            if self.show_tool_args and tc.args_preview:
                tool_line += f" {Colors.DIM}{tc.args_preview}{Colors.RESET}"
            
            # Add duration for completed tools
            if tc.duration_ms > 0:
                tool_line += f" {Colors.DIM}({tc.duration_ms:.0f}ms){Colors.RESET}"
            
            lines.append(tool_line)
        
        return lines
    
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
    
    def _print_status_header(self, task: SubagentTask) -> None:
        """Print task start header."""
        prefix = f"[{task.task_index + 1}]" if task.task_index >= 0 else ""
        self.print_fn(f"\n{Colors.PURPLE}{Colors.BOLD}{prefix} 🔀 Subagent started{Colors.RESET}")
        self.print_fn(f"{Colors.DIM}  Task: {task.goal_preview}{Colors.RESET}")
        self.print_fn(f"{Colors.DIM}  Model: {task.model or 'inherited'}{Colors.RESET}")
    
    def _print_tool_completion(
        self,
        task: SubagentTask,
        tool_name: str,
        result_preview: str,
        status: str,
    ) -> None:
        """Print tool completion inline."""
        emoji = self.TOOL_EMOJIS.get(tool_name, "🔧")
        
        if status == "error":
            self.print_fn(f"  {Colors.ERROR}✗{Colors.RESET} {emoji} {tool_name} {Colors.ERROR}failed{Colors.RESET}")
        else:
            self.print_fn(f"  {Colors.SUCCESS}✓{Colors.RESET} {emoji} {tool_name}")
    
    def _print_final_summary(self, task: SubagentTask) -> None:
        """Print final task summary."""
        prefix = f"[{task.task_index + 1}]" if task.task_index >= 0 else ""
        
        if task.status == SubagentStatus.COMPLETED:
            self.print_fn(f"\n{Colors.PURPLE}{Colors.BOLD}{prefix} ✓ Subagent completed{Colors.RESET}")
            self.print_fn(f"  {Colors.DIM}Duration: {task.duration_seconds:.1f}s{Colors.RESET}")
            self.print_fn(f"  {Colors.DIM}API calls: {task.api_calls}{Colors.RESET}")
            
            if task.summary:
                # Truncate summary for display
                summary = task.summary[:500]
                if len(task.summary) > 500:
                    summary += "..."
                self.print_fn(f"\n{Colors.BOLD}Summary:{Colors.RESET}")
                self.print_fn(f"  {summary}")
        else:
            self.print_fn(f"\n{Colors.ERROR}{Colors.BOLD}{prefix} ✗ Subagent failed{Colors.RESET}")
            if task.error:
                self.print_fn(f"  {Colors.ERROR}{task.error[:200]}{Colors.RESET}")
    
    def _print_interrupt(self, task: SubagentTask) -> None:
        """Print task interrupt notification."""
        prefix = f"[{task.task_index + 1}]" if task.task_index >= 0 else ""
        self.print_fn(f"\n{Colors.WAITING}{Colors.BOLD}{prefix} ⊘ Subagent interrupted{Colors.RESET}")
    
    def _print_cancel(self, task: SubagentTask) -> None:
        """Print task cancel notification."""
        prefix = f"[{task.task_index + 1}]" if task.task_index >= 0 else ""
        self.print_fn(f"\n{Colors.DIM}{prefix} ⊗ Subagent cancelled{Colors.RESET}")
    
    # =====================================================================
    # Tasks Command (/tasks)
    # =====================================================================
    
    def render_tasks_command(self) -> str:
        """
        Render output for /tasks command.
        Returns a formatted string showing all tasks.
        """
        lines = []
        lines.append(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}")
        lines.append(f"{Colors.BOLD}Subagent Tasks{Colors.RESET}\n")

        foreground_tasks = self.list_tasks(include_background=False)
        background_tasks = self.list_background_tasks()

        if not foreground_tasks and not background_tasks:
            lines.append(f"{Colors.DIM}  No active subagent tasks{Colors.RESET}")
        else:
            if foreground_tasks:
                lines.append(f"{Colors.INFO}Foreground{Colors.RESET}")
                for task in foreground_tasks:
                    lines.append(self._render_task_summary_line(task))
            if background_tasks:
                if foreground_tasks:
                    lines.append("")
                lines.append(f"{Colors.INFO}Background{Colors.RESET}")
                for task in sorted(background_tasks, key=lambda t: t.task_index):
                    lines.append(self._render_task_summary_line(task))
        
        lines.append(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}\n")
        
        # Tips
        lines.append(f"{Colors.DIM}Notes:{Colors.RESET}")
        lines.append(f"  API-A will manage subagents automatically during multi-step work.")
        lines.append(f"  {Colors.INFO}/tasks{Colors.RESET}        - Observe current subagent state")
        lines.append(f"  {Colors.DIM}/tasks bg <task>{Colors.RESET} - Advanced debug: move a foreground task to background")
        lines.append(f"  {Colors.DIM}/tasks fg <task>{Colors.RESET} - Advanced debug: bring a background task back")
        
        return "\n".join(lines)
    
    def _render_task_summary_line(self, task: SubagentTask) -> str:
        """Render a single line task summary for /tasks output."""
        status_color = self._get_status_color(task.status)
        status_icon = self.STATUS_ICONS.get(task.status, "○")
        
        # Duration
        if task.completed_at > 0:
            duration_str = f" ({task.duration_seconds:.1f}s)"
        elif task.started_at > 0:
            elapsed = time.time() - task.started_at
            duration_str = f" ({elapsed:.1f}s elapsed)"
        else:
            duration_str = ""
        
        # Status line
        prefix = f"[{task.task_index + 1}]" if task.task_index >= 0 else ""
        lane = "BG" if task.is_background else "FG"
        line = f"  {status_color}{status_icon}{Colors.RESET} {prefix} {task.goal_preview}"
        line += f"{Colors.DIM}{duration_str}{Colors.RESET}"
        line += f" {Colors.DIM}({lane} id={task.task_id}){Colors.RESET}"
        
        # Additional info for running tasks
        if task.status == SubagentStatus.TOOL_CALL and task.current_tool:
            emoji = self.TOOL_EMOJIS.get(task.current_tool, "🔧")
            line += f" {Colors.PURPLE}{emoji} {task.current_tool}{Colors.RESET}"
        
        return line
    
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
        self.clear()
        
        self.print_fn(f"\n{Colors.INFO}→ Advanced debug action applied: {task.goal_preview}{Colors.RESET}")
        self.print_fn(f"{Colors.DIM}  Task is now running in the background; use /tasks to observe it{Colors.RESET}")
        
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
        self.render(clear=False)
        
        self.print_fn(f"\n{Colors.INFO}← Advanced debug action applied: {task.goal_preview}{Colors.RESET}")
        
        return True
    
    # =====================================================================
    # Lifecycle
    # =====================================================================
    
    def start(self) -> None:
        """Start the display manager."""
        self._stop_refresh.clear()
        if self.auto_refresh and not self._refresh_thread:
            self._refresh_thread = threading.Thread(
                target=self._refresh_loop,
                daemon=True,
                name="subagent-display"
            )
            self._refresh_thread.start()
    
    def stop(self) -> None:
        """Stop the display manager."""
        self._stop_refresh.set()
        if self._refresh_thread:
            self._refresh_thread.join(timeout=1.0)
            self._refresh_thread = None
    
    def _refresh_loop(self) -> None:
        """Background refresh loop for real-time updates."""
        CLEANUP_AFTER_SECONDS = 30  # Clean up completed tasks after 30 seconds
        
        while not self._stop_refresh.is_set():
            try:
                # Clean up completed tasks that are older than cleanup threshold
                current_time = time.time()
                with self._lock:
                    to_remove = [
                        tid for tid, completed_at in self._completed_tasks.items()
                        if current_time - completed_at > CLEANUP_AFTER_SECONDS
                    ]
                    for tid in to_remove:
                        self._tasks.pop(tid, None)
                        self._background_tasks.pop(tid, None)
                        self._active_tools.pop(tid, None)
                        self._completed_tasks.pop(tid, None)
                
                # Only render if there are active tasks
                if self.get_active_count() > 0:
                    self.render(clear=True)
                time.sleep(self.refresh_interval)
            except Exception as e:
                logger.debug("Refresh loop error: %s", e)
    
    def clear(self) -> None:
        """Clear the display."""
        with self._lock:
            # Clear rendered lines
            if self._last_render_lines > 0:
                self._move_cursor_up(self._last_render_lines)
                for _ in range(self._last_render_lines):
                    self.print_fn("\033[2K\r", end="")
                self._last_render_lines = 0
    
    def reset(self) -> None:
        """Reset all tracked tasks."""
        with self._lock:
            self._tasks.clear()
            self._background_tasks.clear()
            self._active_tools.clear()
            self._last_render_lines = 0
