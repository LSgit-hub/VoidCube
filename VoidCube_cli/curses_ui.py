"""
Curses UI - 交互式终端选择界面
支持上下键导航、数字标号选择、搜索过滤
"""

import curses
import sys
from typing import List, Dict, Any, Optional


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
    options: List[str],
    multi: bool = False,
    defaults: List[int] = None,
    default_idx: int = 0,
) -> Optional:
    """通用 curses 选择循环"""
    curses.curs_set(0)  # 隐藏光标
    _init_colors()
    
    current_idx = default_idx
    selected = set(defaults or [])
    if multi and defaults:
        selected = set(defaults)
    
    scroll_offset = 0
    max_visible = curses.LINES - 8  # 留空间给标题和提示
    
    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        
        # 标题
        title_text = title[:w - 4] if len(title) > w - 4 else title
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
            stdscr.addstr(2, 2, hint[:w - 4], curses.color_pair(4))
        except curses.error:
            pass
        
        # 分隔线
        try:
            stdscr.addstr(3, 2, "─" * min(w - 4, 60))
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
            display = text[:w - 8] if len(text) > w - 8 else text
            
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
                stdscr.addstr(display_row, 2, line[:w - 4], attr)
            except curses.error:
                pass
        
        # 底部信息
        try:
            footer = f" 共 {len(options)} 项  当前: {current_idx + 1}"
            if multi:
                footer += f"  已选: {len(selected)}/{len(options)}"
            stdscr.addstr(h - 2, 2, footer[:w - 4], curses.color_pair(3))
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
        import termios, tty
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:
        pass


def curses_single_select(
    title: str,
    options: List[str],
    default: int = 0
) -> Optional[int]:
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
        # curses 不可用时回退到数字输入
        print(f"\n  {title}")
        print(f"  {'─' * min(len(title), 50)}")
        for i, opt in enumerate(options, 1):
            print(f"    [{i}] {opt}")
        print()
        try:
            choice = input(f"  请输入数字 (1-{len(options)}, Enter=默认): ").strip()
            if not choice:
                return default if default >= 0 and default < len(options) else 0
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return idx
        except (ValueError, EOFError):
            pass
        return default if default >= 0 and default < len(options) else 0


def curses_radiolist(
    title: str,
    options: List[Dict[str, Any]],
    default: str = ""
) -> Optional[str]:
    """
    单选列表（字典格式） — 支持上下键、数字标号跳转
    
    Args:
        title: 标题
        options: [{"label": "显示名", "value": "返回值"}, ...]
        default: 默认值
    
    Returns:
        选中的 value，取消返回 None
    """
    if not options:
        return None
    
    labels = [opt.get("label", str(opt.get("value", ""))) for opt in options]
    values = [opt.get("value", str(i)) for i, opt in enumerate(options)]
    
    default_idx = 0
    if default:
        for i, v in enumerate(values):
            if v == default:
                default_idx = i
                break
    
    result_idx = curses_single_select(title, labels, default=default_idx)
    
    if result_idx is not None and 0 <= result_idx < len(values):
        return values[result_idx]
    return None


def curses_checklist(
    title: str,
    options: List[Dict[str, Any]],
    defaults: List[str] = None
) -> List[str]:
    """
    多选列表 — 支持 SPACE 选择、上下键、数字标号跳转
    
    Args:
        title: 标题
        options: [{"label": "显示名", "value": "返回值"}, ...]
        defaults: 默认选中的 values
    
    Returns:
        选中的 value 列表
    """
    if not options:
        return []
    
    labels = [opt.get("label", str(opt.get("value", ""))) for opt in options]
    values = [opt.get("value", str(i)) for i, opt in enumerate(options)]
    
    default_indices = set()
    if defaults:
        for d in defaults:
            for i, v in enumerate(values):
                if v == d:
                    default_indices.add(i)
    
    try:
        result = curses.wrapper(
            _run_curses_loop,
            title,
            labels,
            multi=True,
            defaults=default_indices,
            default_idx=0
        )
    except Exception:
        # curses 不可用时回退
        print(f"\n  {title}")
        print(f"  {'─' * min(len(title), 50)}")
        for i, opt in enumerate(options, 1):
            label = opt.get("label", str(opt.get("value", "")))
            print(f"    [{i}] {label}")
        print()
        try:
            choice = input(f"  输入数字选择 (逗号分隔, Enter=默认): ").strip()
            if not choice:
                return defaults or []
            selected = []
            for part in choice.split(","):
                idx = int(part.strip()) - 1
                if 0 <= idx < len(values):
                    selected.append(values[idx])
            return selected if selected else (defaults or [])
        except (ValueError, EOFError):
            pass
        return defaults or []
    
    if result is None:
        return defaults or []
    
    return [values[i] for i in result if 0 <= i < len(values)]
