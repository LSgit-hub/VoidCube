"""
Windows 控制台编码修复模块
解决 UnicodeEncodeError: 'gbk' codec can't encode character 问题
"""

import sys
import os
from pathlib import Path


def fix_windows_console_encoding():
    """
    修复 Windows 控制台编码问题

    在 Windows 上，默认的控制台编码是 GBK，无法显示某些 Unicode 字符。
    这个函数会：
    1. 设置环境变量 PYTHONIOENCODING=utf-8
    2. 重新配置 sys.stdout 和 sys.stderr 的编码
    3. 设置控制台输出编码为 UTF-8
    """
    if sys.platform != 'win32':
        return  # 只在 Windows 上执行

    # 方法 1: 设置环境变量
    os.environ['PYTHONIOENCODING'] = 'utf-8'

    # 方法 2: 重新配置标准输出
    if sys.stdout.encoding != 'utf-8':
        try:
            import io
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer,
                encoding='utf-8',
                errors='replace'  # 遇到无法编码的字符时用 ? 替换
            )
            sys.stderr = io.TextIOWrapper(
                sys.stderr.buffer,
                encoding='utf-8',
                errors='replace'
            )
        except Exception:
            pass  # 如果失败，继续使用默认编码

    # 方法 3: 设置控制台代码页为 UTF-8 (65001)
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # 设置控制台输出代码页为 UTF-8
        kernel32.SetConsoleOutputCP(65001)
        # 设置控制台输入代码页为 UTF-8
        kernel32.SetConsoleCP(65001)
    except Exception:
        pass  # 如果失败，继续使用默认代码页


def configure_rich_for_windows():
    """
    配置 Rich 库以支持 Windows 控制台

    Rich 库在 Windows 上需要特殊配置才能正确显示 Unicode 字符
    """
    if sys.platform != 'win32':
        return

    try:
        from rich.console import Console

        # 创建一个配置了 UTF-8 编码的 Console 实例
        # 这个函数可以被导入并用于替换默认的 Console
        def create_windows_safe_console():
            return Console(
                force_terminal=True,  # 强制使用终端模式
                legacy_windows=False,  # 禁用旧版 Windows 模式
                safe_box=True,  # 使用安全的 ASCII 框线字符
            )

        return create_windows_safe_console
    except ImportError:
        return None


def patch_rich_console():
    """
    修补 Rich Console 以避免编码错误

    这个函数会修改 Rich 的 Console.print 方法，
    在遇到编码错误时使用安全的 ASCII 字符替代
    """
    if sys.platform != 'win32':
        return

    try:
        from rich.console import Console
        from rich import _windows_renderer

        # 保存原始的 write_styled 方法
        original_write_styled = _windows_renderer.legacy_windows_render

        def safe_write_styled(text, style):
            """安全的写入方法，处理编码错误"""
            try:
                original_write_styled(text, style)
            except UnicodeEncodeError:
                # 如果遇到编码错误，尝试用 ASCII 替代
                safe_text = text.encode('ascii', errors='replace').decode('ascii')
                original_write_styled(safe_text, style)

        # 替换为安全版本
        _windows_renderer.legacy_windows_render = safe_write_styled

    except Exception:
        pass  # 如果修补失败，继续使用原始方法


# 自动应用修复
def apply_all_fixes():
    """应用所有编码修复"""
    fix_windows_console_encoding()
    patch_rich_console()


# 在模块导入时自动应用
if sys.platform == 'win32':
    apply_all_fixes()
