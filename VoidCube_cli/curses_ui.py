"""
Curses UI - 交互式终端选择界面
支持上下键导航和数字标号选择
"""

import sys
from collections.abc import Callable, Iterable, Sequence

try:
    import curses
except ImportError:  # pragma: no cover - exercised on Windows without windows-curses
    curses = None  # type: ignore[assignment]

from VoidCube_cli.terminal_text_layout import trim_to_width


def _init_colors():
    """初始化颜色对"""
    try:
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)   # 选中
        curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLACK)   # 普通
        curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)  # 标题
        curses.init_pair(4, curses.COLOR_GREEN, curses.COLOR_BLACK)   # 提示
    except Exception:
        pass


def _run_curses_loop(
    stdscr,
    title: str,
    options: Sequence[str],
    multi: bool = False,
    defaults: set[int] | None = None,
    default_idx: int = 0,
    status_fn: Callable[[set[int]], str] | None = None,
) -> list[int] | int | None:
    """通用 curses 选择循环"""
    if curses is None:
        raise RuntimeError("curses backend is unavailable")
    curses.curs_set(0)  # 隐藏光标
    _init_colors()
    
    current_idx = default_idx
    selected = set(defaults or [])
    if multi and defaults:
        selected = set(defaults)
    
    scroll_offset = 0
    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        max_visible = max(1, h - 8)  # 留空间给标题和提示
        
        # 标题
        title_text = trim_to_width(title, max(0, w - 4))
        try:
            stdscr.addstr(1, 2, title_text, curses.color_pair(3) | curses.A_BOLD)
        except curses.error:
            pass
        
        # 提示行
        if multi:
            hint = "SPACE=选择  ENTER=确认  ↑↓=移动  q=退出  [数字]=跳转"
        else:
            hint = "ENTER=确认  ↑↓=移动  q=退出  [数字]=跳转  ESC=取消"
        try:
            stdscr.addstr(2, 2, trim_to_width(hint, max(0, w - 4)), curses.color_pair(4))
        except curses.error:
            pass
        
        # 分隔线
        try:
            stdscr.addstr(3, 2, "─" * max(0, min(w - 4, 60)))
        except curses.error:
            pass
        
        # 调整滚动
        if current_idx < scroll_offset:
            scroll_offset = current_idx
        elif current_idx >= scroll_offset + max_visible:
            scroll_offset = current_idx - max_visible + 1
        
        # 显示选项
        for i in range(scroll_offset, min(len(options), scroll_offset + max_visible)):
            display_row = 4 + (i - scroll_offset)
            text = options[i]
            display = trim_to_width(text, max(0, w - 8))
            
            is_current = (i == current_idx)
            is_selected = (i in selected) if multi else False
            
            prefix = "●" if (multi and is_selected) else "○" if multi else " "
            number = f"{i + 1:>3}. "
            line = f" {prefix} {number}{display}"
            
            if is_current and not multi:
                attr = curses.color_pair(1) | curses.A_BOLD
            elif is_current and multi:
                attr = curses.color_pair(1)
            elif is_selected and multi:
                attr = curses.color_pair(4)
            else:
                attr = curses.color_pair(2)
            
            try:
                stdscr.addstr(display_row, 2, trim_to_width(line, max(0, w - 4)), attr)
            except curses.error:
                pass
        
        # 底部信息
        try:
            footer = f" 共 {len(options)} 项  当前: {current_idx + 1}"
            if multi:
                footer += f"  已选: {len(selected)}/{len(options)}"
                if status_fn:
                    status = status_fn(selected)
                    if status:
                        footer += f"  {status}"
            stdscr.addstr(h - 2, 2, trim_to_width(footer, max(0, w - 4)), curses.color_pair(3))
        except curses.error:
            pass
        
        stdscr.refresh()
        
        # 按键处理
        try:
            key = stdscr.getch()
        except Exception:
            break
        
        if key == curses.KEY_UP or key == ord('k'):
            current_idx = (current_idx - 1) % len(options) if options else 0
        elif key == curses.KEY_DOWN or key == ord('j'):
            current_idx = (current_idx + 1) % len(options) if options else 0
        elif key == curses.KEY_HOME:
            current_idx = 0
        elif key == curses.KEY_END:
            current_idx = len(options) - 1 if options else 0
        elif key == curses.KEY_NPAGE:  # Page Down
            current_idx = min(len(options) - 1, current_idx + max_visible) if options else 0
        elif key == curses.KEY_PPAGE:  # Page Up
            current_idx = max(0, current_idx - max_visible) if options else 0
        elif key == ord(' ') and multi:
            # 切换选择
            if current_idx in selected:
                selected.remove(current_idx)
            else:
                selected.add(current_idx)
        elif key == curses.KEY_ENTER or key == 10 or key == 13:
            if multi:
                return sorted(selected)
            else:
                return current_idx
        elif key == 27:  # ESC
            if multi:
                return sorted(selected) if selected else None
            else:
                return None
        elif key == ord('q') or key == ord('Q'):
            if multi:
                return sorted(selected) if selected else None
            else:
                return None
        elif ord('0') <= key <= ord('9'):
            # 数字快速跳转
            num_buffer = chr(key)
            stdscr.nodelay(True)
            try:
                while True:
                    next_key = stdscr.getch()
                    if ord('0') <= next_key <= ord('9'):
                        num_buffer += chr(next_key)
                    else:
                        if next_key != -1:
                            curses.ungetch(next_key)  # 放回非数字键
                        break
            except Exception:
                pass
            finally:
                stdscr.nodelay(False)
            
            try:
                target = int(num_buffer) - 1
                if 0 <= target < len(options):
                    current_idx = target
            except ValueError:
                pass
        
    return None


def flush_stdin() -> None:
    """刷新标准输入缓冲区"""
    try:
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:
        pass


def curses_single_select(title: str, options: Sequence[str], default: int = 0) -> int | None:
    """
    单选列表 — 支持上下键、数字标号跳转
    
    Args:
        title: 标题
        options: 选项列表
        default: 默认选中索引
    
    Returns:
        选中的索引，取消返回 None
    """
    if not options:
        return None
    
    if curses is None:
        return _fallback_single_select(title, options, default)

    flush_stdin()
    
    try:
        return curses.wrapper(
            _run_curses_loop,
            title,
            options,
            multi=False,
            default_idx=min(default, len(options) - 1) if default >= 0 else 0
        )
    except Exception:
        return _fallback_single_select(title, options, default)


def _fallback_single_select(title: str, options: Sequence[str], default: int) -> int | None:
    """Use line input when curses is unavailable or the terminal is not a TTY."""
    print(f"\n  {title}")
    print(f"  {'─' * min(len(title), 50)}")
    for i, option in enumerate(options, 1):
        print(f"    [{i}] {option}")
    print()
    try:
        choice = input(f"  请输入数字 (1-{len(options)}, Enter=默认): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not choice:
        return default if 0 <= default < len(options) else 0
    try:
        index = int(choice) - 1
    except ValueError:
        return default if 0 <= default < len(options) else 0
    return index if 0 <= index < len(options) else (default if 0 <= default < len(options) else 0)


def curses_checklist(
    title: str,
    options: Sequence[str],
    defaults: Iterable[int] | None = None,
    *,
    cancel_returns: Iterable[int] | None = None,
    status_fn: Callable[[set[int]], str] | None = None,
) -> set[int]:
    """
    多选列表 — 支持 SPACE 选择、上下键、数字标号跳转
    
    Args:
        title: 标题
        options: 显示文本列表
        defaults: 默认选中的索引
    
    Returns:
        选中的索引集合
    """
    if not options:
        return set()

    labels = [str(option) for option in options]
    default_indices = {index for index in (defaults or ()) if 0 <= index < len(labels)}

    def selected_or_default(value: Iterable[int] | None) -> set[int]:
        return set(default_indices if value is None else value)

    if curses is None:
        return _fallback_checklist(title, labels, default_indices, cancel_returns, status_fn)
    
    try:
        result = curses.wrapper(
            _run_curses_loop,
            title,
            labels,
            multi=True,
            defaults=default_indices,
            default_idx=0,
            status_fn=status_fn,
        )
    except Exception:
        return _fallback_checklist(title, labels, default_indices, cancel_returns, status_fn)
    
    if result is None:
        return selected_or_default(cancel_returns)
    return {i for i in result if 0 <= i < len(labels)}


def _fallback_checklist(
    title: str,
    options: Sequence[str],
    defaults: set[int],
    cancel_returns: Iterable[int] | None,
    status_fn: Callable[[set[int]], str] | None,
) -> set[int]:
    print(f"\n  {title}")
    for i, label in enumerate(options, 1):
        marker = "*" if i - 1 in defaults else " "
        print(f"    [{marker}] {i}: {label}")
    try:
        choice = input("  输入数字选择 (逗号分隔, Enter=默认): ").strip()
    except (EOFError, KeyboardInterrupt):
        return set(cancel_returns or defaults)
    if not choice:
        return set(defaults)
    selected: set[int] = set()
    try:
        for part in choice.split(","):
            index = int(part.strip()) - 1
            if 0 <= index < len(options):
                selected.add(index)
    except ValueError:
        return set(cancel_returns or defaults)
    if status_fn:
        status = status_fn(selected)
        if status:
            print(f"  {status}")
    return selected
