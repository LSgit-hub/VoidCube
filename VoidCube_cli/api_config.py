"""
API 配置向导 - 交互式配置 API 设置
"""

import os
import subprocess
import sys
import re
from typing import Optional


def load_current_config() -> dict:
    """加载当前配置"""
    try:
        from VoidCube_cli.config import load_config
        return load_config()
    except Exception:
        return {}


def save_config_value(key_path: str, value: any) -> bool:
    """保存配置值到 config.yaml"""
    try:
        from cli import save_config_value as _save
        return _save(key_path, value)
    except Exception:
        return False


def save_env_value(key: str, value: str) -> bool:
    """保存环境变量到 .env 文件"""
    try:
        from VoidCube_cli.config import save_env_value as _save_env
        _save_env(key, value)
        return True
    except Exception:
        return False


def _provider_key_from_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-") or "provider"


def save_provider_config(
    provider_key: str,
    *,
    label: str,
    selected_model: str,
    provider_type: str,
    base_url: str = "",
    api_key_env: str = "",
    api_key: str = "",
    auth_mode: str = "",
) -> bool:
    """Persist a provider entry and set it active."""
    try:
        from VoidCube_cli.config import (
            load_config,
            save_config,
            set_active_provider,
            upsert_provider,
        )

        cfg = load_config()
        cfg = upsert_provider(
            cfg,
            provider_key,
            {
                "label": label,
                "type": provider_type,
                "base_url": base_url,
                "selected_model": selected_model,
                "api_key_env": api_key_env,
                "api_key": api_key,
                "auth_mode": auth_mode,
            },
            make_active=True,
        )
        cfg = set_active_provider(cfg, provider_key)
        save_config(cfg)
        return True
    except Exception:
        return False


def test_api_connection(provider: str, api_key: str, base_url: str = "") -> bool:
    """测试 API 连接"""
    try:
        import httpx
        
        headers = {"Authorization": f"Bearer {api_key}"}
        url = base_url or "https://openrouter.ai/api/v1/models"
        
        response = httpx.get(url, headers=headers, timeout=10)
        return response.status_code == 200
    except Exception:
        return False


def get_provider_models_from_api(provider: str) -> list[tuple[str, str]]:
    """从API获取provider的模型列表"""
    try:
        from VoidCube_cli.models import curated_models_for_provider
        return curated_models_for_provider(provider)
    except Exception:
        return []


# =========================================================================
# 显示组件插口 - 可在此添加自定义显示功能
# =========================================================================

class DisplayComponents:
    """显示组件集合，提供各种可插拔的显示功能"""
    
    # ANSI 颜色代码
    COLORS = {
        'black': '\033[30m',
        'red': '\033[31m',
        'green': '\033[32m',
        'yellow': '\033[33m',
        'blue': '\033[34m',
        'magenta': '\033[35m',
        'cyan': '\033[36m',
        'white': '\033[37m',
        'bright_black': '\033[90m',
        'bright_red': '\033[91m',
        'bright_green': '\033[92m',
        'bright_yellow': '\033[93m',
        'bright_blue': '\033[94m',
        'bright_magenta': '\033[95m',
        'bright_cyan': '\033[96m',
        'bright_white': '\033[97m',
        'reset': '\033[0m',
        'bold': '\033[1m',
        'dim': '\033[2m',
        'italic': '\033[3m',
        'underline': '\033[4m',
    }
    
    # 进度条样式
    PROGRESS_STYLES = {
        'classic': {'filled': '█', 'empty': '░'},
        'modern': {'filled': '▰', 'empty': '▱'},
        'dots': {'filled': '●', 'empty': '○'},
        'arrows': {'filled': '▶', 'empty': '▷'},
        'stars': {'filled': '★', 'empty': '☆'},
    }
    
    # 边框样式
    BORDER_STYLES = {
        'simple': {'tl': '+', 'tr': '+', 'bl': '+', 'br': '+', 'h': '-', 'v': '|'},
        'double': {'tl': '╔', 'tr': '╗', 'bl': '╚', 'br': '╝', 'h': '═', 'v': '║'},
        'rounded': {'tl': '╭', 'tr': '╮', 'bl': '╰', 'br': '╯', 'h': '─', 'v': '│'},
        'bold': {'tl': '┏', 'tr': '┓', 'bl': '┗', 'br': '┛', 'h': '━', 'v': '┃'},
    }
    
    @staticmethod
    def colored(text: str, color: str = 'white', bold: bool = False) -> str:
        """返回彩色文本
        
        Args:
            text: 要显示的文本
            color: 颜色名称 (red, green, yellow, blue, magenta, cyan, white, etc.)
            bold: 是否加粗
            
        Returns:
            带ANSI颜色代码的文本
        """
        result = DisplayComponents.COLORS.get(color, DisplayComponents.COLORS['white'])
        if bold:
            result += DisplayComponents.COLORS['bold']
        result += text
        result += DisplayComponents.COLORS['reset']
        return result
    
    @staticmethod
    def separator(width: int = 60, char: str = '=', color: str = 'yellow') -> str:
        """生成分隔线
        
        Args:
            width: 分隔线宽度
            char: 分隔线字符
            color: 颜色
            
        Returns:
            分隔线字符串
        """
        return DisplayComponents.colored(char * width, color)
    
    @staticmethod
    def header(text: str, width: int = 60, border_style: str = 'rounded', color: str = 'cyan') -> str:
        """生成带边框的标题
        
        Args:
            text: 标题文本
            width: 标题框宽度
            border_style: 边框样式 (simple, double, rounded, bold)
            color: 颜色
            
        Returns:
            格式化的标题字符串
        """
        border = DisplayComponents.BORDER_STYLES.get(border_style, DisplayComponents.BORDER_STYLES['rounded'])
        content_width = width - 4
        text_lines = text.split('\n')
        
        result = []
        result.append(DisplayComponents.colored(f"{border['tl']}{border['h'] * (width - 2)}{border['tr']}", color))
        
        for line in text_lines:
            padded_line = line.center(content_width)
            result.append(DisplayComponents.colored(f"{border['v']} {padded_line} {border['v']}", color))
        
        result.append(DisplayComponents.colored(f"{border['bl']}{border['h'] * (width - 2)}{border['br']}", color))
        return '\n'.join(result)
    
    @staticmethod
    def progress_bar(current: int, total: int, width: int = 50, style: str = 'classic', 
                    color: str = 'green', show_percent: bool = True, 
                    show_count: bool = True, prefix: str = '') -> str:
        """生成进度条
        
        Args:
            current: 当前进度
            total: 总进度
            width: 进度条宽度
            style: 进度条样式 (classic, modern, dots, arrows, stars)
            color: 颜色
            show_percent: 是否显示百分比
            show_count: 是否显示计数
            prefix: 前缀文本
            
        Returns:
            格式化的进度条字符串
        """
        style_chars = DisplayComponents.PROGRESS_STYLES.get(style, DisplayComponents.PROGRESS_STYLES['classic'])
        
        if total <= 0:
            percent = 0
        else:
            percent = min(current / total, 1.0)
        
        filled = int(width * percent)
        empty = width - filled
        
        bar = style_chars['filled'] * filled + style_chars['empty'] * empty
        
        result = []
        if prefix:
            result.append(f"{prefix} ")
        
        result.append(DisplayComponents.colored(f"[{bar}]", color))
        
        if show_percent:
            result.append(f" {percent * 100:.1f}%")
        
        if show_count:
            result.append(f" ({current}/{total})")
        
        return ''.join(result)
    
    @staticmethod
    def table(data: list[list], headers: list = None, border_style: str = 'simple', 
             cell_padding: int = 2, header_color: str = 'cyan', 
             row_colors: list = None, align: str = 'left') -> str:
        """生成表格
        
        Args:
            data: 表格数据（二维列表）
            headers: 表头列表
            border_style: 边框样式
            cell_padding: 单元格内边距
            header_color: 表头颜色
            row_colors: 行颜色列表（循环使用）
            align: 对齐方式 (left, center, right)
            
        Returns:
            格式化的表格字符串
        """
        if not data:
            return ""
        
        border = DisplayComponents.BORDER_STYLES.get(border_style, DisplayComponents.BORDER_STYLES['simple'])
        
        all_rows = [headers] + data if headers else data
        
        col_widths = []
        for col in range(len(all_rows[0])):
            max_width = max(len(str(row[col])) for row in all_rows if col < len(row))
            col_widths.append(max_width + cell_padding * 2)
        
        result = []
        
        def format_row(row, is_header=False, color='white'):
            cells = []
            for i, cell in enumerate(row):
                if i >= len(col_widths):
                    continue
                cell_str = str(cell)
                width = col_widths[i] - cell_padding * 2
                
                if align == 'left':
                    formatted = cell_str.ljust(width)
                elif align == 'right':
                    formatted = cell_str.rjust(width)
                else:
                    formatted = cell_str.center(width)
                
                cells.append(' ' * cell_padding + formatted + ' ' * cell_padding)
            
            line = border['v'].join(cells)
            return DisplayComponents.colored(f"{border['v']}{line}{border['v']}", color)
        
        def separator_line():
            parts = [border['h'] * w for w in col_widths]
            return DisplayComponents.colored(f"{border['tl']}{border['h'].join(parts)}{border['tr']}", 'dim')
        
        def bottom_separator_line():
            parts = [border['h'] * w for w in col_widths]
            return DisplayComponents.colored(f"{border['bl']}{border['h'].join(parts)}{border['br']}", 'dim')
        
        def middle_separator_line():
            parts = [border['h'] * w for w in col_widths]
            return DisplayComponents.colored(f"{border['v'].replace(border['v'], '├')}{border['h'].join(parts)}{border['v'].replace(border['v'], '┤')}", 'dim')
        
        result.append(separator_line())
        
        if headers:
            result.append(format_row(headers, True, header_color))
            result.append(middle_separator_line())
            data_rows = data
        else:
            data_rows = data
        
        row_colors = row_colors or ['white']
        for i, row in enumerate(data_rows):
            color = row_colors[i % len(row_colors)]
            result.append(format_row(row, False, color))
        
        result.append(bottom_separator_line())
        return '\n'.join(result)
    
    @staticmethod
    def list_items(items: list, bullet: str = '•', color: str = 'white', 
                  indent: int = 2, numbered: bool = False) -> str:
        """生成列表
        
        Args:
            items: 项目列表
            bullet: 项目符号
            color: 颜色
            indent: 缩进
            numbered: 是否使用数字编号
            
        Returns:
            格式化的列表字符串
        """
        result = []
        for i, item in enumerate(items):
            prefix = f"{i + 1}." if numbered else bullet
            line = ' ' * indent + f"{prefix} {item}"
            result.append(DisplayComponents.colored(line, color))
        return '\n'.join(result)
    
    @staticmethod
    def key_value(data: dict, key_color: str = 'yellow', value_color: str = 'white',
                 colon: str = ': ', align_keys: bool = True) -> str:
        """生成键值对显示
        
        Args:
            data: 字典数据
            key_color: 键的颜色
            value_color: 值的颜色
            colon: 分隔符
            align_keys: 是否对齐键
            
        Returns:
            格式化的键值对字符串
        """
        if not data:
            return ""
        
        result = []
        
        if align_keys:
            max_key_len = max(len(str(k)) for k in data.keys())
        else:
            max_key_len = 0
        
        for key, value in data.items():
            key_str = str(key)
            if align_keys:
                key_str = key_str.ljust(max_key_len)
            
            line = (DisplayComponents.colored(key_str, key_color) + 
                   colon + 
                   DisplayComponents.colored(str(value), value_color))
            result.append(line)
        
        return '\n'.join(result)
    
    @staticmethod
    def spinner(message: str = "Loading...", style: str = 'dots') -> 'Spinner':
        """创建一个加载动画
        
        Args:
            message: 加载消息
            style: 动画样式
            
        Returns:
            Spinner实例
        """
        return Spinner(message, style)
    
    @staticmethod
    def success(message: str, icon: str = '✓') -> str:
        """成功消息"""
        return DisplayComponents.colored(f"{icon} {message}", 'green', bold=True)
    
    @staticmethod
    def error(message: str, icon: str = '✗') -> str:
        """错误消息"""
        return DisplayComponents.colored(f"{icon} {message}", 'red', bold=True)
    
    @staticmethod
    def warning(message: str, icon: str = '⚠') -> str:
        """警告消息"""
        return DisplayComponents.colored(f"{icon} {message}", 'yellow', bold=True)
    
    @staticmethod
    def info(message: str, icon: str = 'ℹ') -> str:
        """信息消息"""
        return DisplayComponents.colored(f"{icon} {message}", 'cyan', bold=True)
    
    @staticmethod
    def highlight(text: str, substring: str, highlight_color: str = 'yellow',
                 bold: bool = True) -> str:
        """高亮文本中的子字符串
        
        Args:
            text: 原文本
            substring: 要高亮的子字符串
            highlight_color: 高亮颜色
            bold: 是否加粗
            
        Returns:
            带高亮的文本
        """
        import re
        pattern = re.compile(re.escape(substring), re.IGNORECASE)
        
        def replace(match):
            return DisplayComponents.colored(match.group(0), highlight_color, bold=bold)
        
        return pattern.sub(replace, text)
    
    @staticmethod
    def tree(data: dict, prefix: str = '', is_last: bool = True) -> str:
        """生成树形结构显示
        
        Args:
            data: 树数据（嵌套字典）
            prefix: 前缀（用于递归）
            is_last: 是否是最后一个节点（用于递归）
            
        Returns:
            树形结构字符串
        """
        result = []
        items = list(data.items())
        
        for i, (key, value) in enumerate(items):
            is_last_item = i == len(items) - 1
            
            connector = '└── ' if is_last_item else '├── '
            result.append(f"{prefix}{connector}{key}")
            
            if isinstance(value, dict):
                extension = '    ' if is_last_item else '│   '
                result.append(DisplayComponents.tree(value, prefix + extension, True))
            elif isinstance(value, list):
                extension = '    ' if is_last_item else '│   '
                list_dict = {str(j): item for j, item in enumerate(value)}
                result.append(DisplayComponents.tree(list_dict, prefix + extension, True))
            elif value is not None:
                extension = '    ' if is_last_item else '│   '
                result.append(f"{prefix}{extension}└── {value}")
        
        return '\n'.join(result)
    
    @staticmethod
    def git_info(path: str = '.', show_details: bool = True) -> str:
        """生成 Git 仓库信息显示
        
        Args:
            path: Git 仓库路径
            show_details: 是否显示详细信息
            
        Returns:
            格式化的 Git 信息字符串
        """
        import subprocess
        import os
        
        def run_git_cmd(cmd):
            try:
                # 使用 utf-8 编码，避免 Windows 下 GBK 编码问题
                result = subprocess.run(
                    ['git'] + cmd,
                    cwd=path,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=5
                )
                if result.returncode == 0:
                    return result.stdout.strip()
                return None
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
                return None
        
        # 检查是否是 Git 仓库
        git_dir = os.path.join(path, '.git')
        if not os.path.exists(git_dir):
            return DisplayComponents.error("当前目录不是 Git 仓库")
        
        result = []
        
        # 获取基本信息
        branch = run_git_cmd(['rev-parse', '--abbrev-ref', 'HEAD']) or '未知分支'
        commit_hash = run_git_cmd(['rev-parse', '--short', 'HEAD']) or '未知提交'
        commit_msg = run_git_cmd(['log', '-1', '--pretty=%B']) or '无提交信息'
        commit_msg = commit_msg.split('\n')[0][:50]
        
        # 获取状态信息
        status = run_git_cmd(['status', '--porcelain'])
        modified = len([line for line in status.split('\n') if line.strip()]) if status else 0
        staged = len([line for line in status.split('\n') if line.strip() and line[1] != ' ']) if status else 0
        
        # 获取远程信息
        remote = run_git_cmd(['remote', 'get-url', 'origin']) or '无远程仓库'
        if len(remote) > 50:
            remote = remote[:47] + '...'
        
        # 获取作者信息
        author = run_git_cmd(['config', 'user.name']) or '未知用户'
        email = run_git_cmd(['config', 'user.email']) or ''
        
        # 构建显示
        result.append(DisplayComponents.header("Git 仓库信息", width=50, color='green'))
        
        git_data = {
            "分支": branch,
            "提交": commit_hash,
            "提交信息": commit_msg,
            "未提交修改": f"{modified} 个文件" if modified > 0 else "无",
            "暂存文件": f"{staged} 个文件" if staged > 0 else "无",
            "远程仓库": remote,
        }
        
        if email:
            git_data["作者"] = f"{author} <{email}>"
        else:
            git_data["作者"] = author
        
        result.append(DisplayComponents.key_value(git_data, key_color='yellow', value_color='cyan'))
        
        # 如果有修改，显示状态
        if modified > 0 and show_details:
            result.append("")
            result.append(DisplayComponents.colored("  文件变更:", 'yellow'))
            
            if status:
                files = status.split('\n')[:10]  # 最多显示10个文件
                for file in files:
                    if file.strip():
                        status_char = file[0]
                        filename = file[2:].strip()
                        
                        if status_char == 'M':
                            icon = '✏️'
                            color = 'yellow'
                        elif status_char == 'A':
                            icon = '➕'
                            color = 'green'
                        elif status_char == 'D':
                            icon = '🗑️'
                            color = 'red'
                        elif status_char == 'R':
                            icon = '🔄'
                            color = 'cyan'
                        elif status_char == '??':
                            icon = '❓'
                            color = 'magenta'
                        else:
                            icon = '📄'
                            color = 'white'
                        
                        result.append(f"    {icon} {DisplayComponents.colored(filename, color)}")
                
                if len(status.split('\n')) > 10:
                    result.append(f"    ... 还有 {len(status.split('\n')) - 10} 个文件")
        
        return '\n'.join(result)

    @staticmethod
    def system_info():
        """生成系统信息显示
        
        Returns:
            格式化的系统信息字符串
        """
        import platform
        import sys
        import os
        
        result = []
        result.append(DisplayComponents.header('系统信息', width=50, border_style='rounded', color='cyan'))
        
        info = {
            '操作系统': f"{platform.system()} {platform.release()}",
            'Python 版本': platform.python_version(),
            '架构': platform.machine(),
            '处理器': platform.processor() or '未知',
            '当前目录': os.getcwd(),
        }
        
        # 尝试获取更多系统信息
        try:
            import psutil
            mem = psutil.virtual_memory()
            info['内存总量'] = f"{mem.total / (1024**3):.1f} GB"
            info['内存使用'] = f"{mem.percent}%"
        except Exception:
            pass
        
        result.append(DisplayComponents.key_value(info, key_color='yellow', value_color='white'))
        
        return '\n'.join(result)


class Spinner:
    """简单的加载动画类"""
    
    SPINNERS = {
        'dots': ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'],
        'line': ['|', '/', '-', '\\'],
        'bounce': ['◐', '◓', '◑', '◒'],
        'arrow': ['←', '↖', '↑', '↗', '→', '↘', '↓', '↙'],
        'grow': ['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█', '▇', '▆', '▅', '▄', '▃', '▂'],
        'moon': ['🌑', '🌒', '🌓', '🌔', '🌕', '🌖', '🌗', '🌘'],
        'earth': ['🌍', '🌎', '🌏'],
    }
    
    def __init__(self, message: str = "Loading...", style: str = 'dots', output_fn=None):
        self.message = message
        self.style = style
        self.frames = self.SPINNERS.get(style, self.SPINNERS['dots'])
        self.current_frame = 0
        self.output_fn = output_fn or print
        self.running = False
        
    def start(self):
        """开始加载动画（这里只是占位，实际使用可能需要线程）"""
        self.running = True
        
    def stop(self, final_message: str = None):
        """停止加载动画"""
        self.running = False
        if final_message:
            self.output_fn(f"\r{final_message}")
        else:
            self.output_fn("\r" + " " * (len(self.message) + 10))
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False
    
    def update(self, message: str = None):
        """更新消息并返回当前帧"""
        if message:
            self.message = message
        frame = self.frames[self.current_frame % len(self.frames)]
        self.current_frame += 1
        return f"\r{frame} {self.message}"


# 便捷访问显示组件
dc = DisplayComponents


def run_api_config_wizard(console=None):
    """运行 API 配置向导"""
    
    original_stdout = sys.stdout
    try:
        sys.stdout = sys.__stdout__
    except (AttributeError, OSError):
        pass
    
    def p(text):
        """直接写入原始 stdout"""
        sys.stdout.write(str(text) + "\n")
        sys.stdout.flush()
    
    def ph(title):
        p("\n" + "=" * 60)
        p(f"  {title}")
        p("=" * 60)
    
    def ps(msg):
        p(f"  ✅ {msg}")
    
    def pe(msg):
        p(f"  ❌ {msg}")
    
    def pi(msg):
        p(f"  ℹ️  {msg}")
    
    def inp(prompt, default=""):
        pr = f"{prompt}: "
        sys.stdout.write(pr)
        sys.stdout.flush()
        try:
            user_input = sys.stdin.readline().strip()
            return user_input if user_input else default
        except (KeyboardInterrupt, EOFError):
            return default
    
    if os.name == 'nt':
        subprocess.call('cls', shell=True)
    else:
        subprocess.call(['clear'])
    
    ph("API 配置向导")
    
    p("\n欢迎使用 VoidCube API 配置向导！")
    p("本向导将帮助您配置 LLM API 设置。\n")
    
    current_config = load_current_config()
    runtime_config = current_config.get("runtime", {}) if isinstance(current_config.get("runtime"), dict) else {}
    providers_config = current_config.get("providers", {}) if isinstance(current_config.get("providers"), dict) else {}
    
    p("📋 当前配置：")
    current_provider = runtime_config.get("active_provider") or "未设置"
    current_provider_cfg = providers_config.get(current_provider, {}) if current_provider in providers_config else {}
    current_model = current_provider_cfg.get("selected_model") or "未设置"
    p(f"   Provider: {current_provider}")
    p(f"   Model: {current_model}")
    
    # 显示记忆系统配置
    memory_config = current_config.get("memory", {})
    memory_llm_config = memory_config.get("llm", {})
    memory_provider = memory_llm_config.get("provider", "未设置")
    memory_model = memory_llm_config.get("model", "未设置")
    p(f"   Memory Provider: {memory_provider}")
    p(f"   Memory Model: {memory_model}")
    p("")
    
    # 主菜单循环
    while True:
        p("\n请选择配置模式：")
        p("   [1] 快速配置 (推荐) - 使用 OpenRouter")
        p("   [2] 自定义配置 - 添加其他 Provider")
        p("   [3] 记忆系统模型配置")
        p("   [4] 查看当前配置")
        p("   [0] 退出")
        
        choice = inp("\n请选择")
        
        if choice == "0":
            p("\n已取消配置。")
            break
        
        elif choice == "1":
            # OpenRouter 配置
            while True:
                ph("OpenRouter 配置")
                
                p("\n📝 OpenRouter 是一个聚合多个 AI 模型的平台")
                p("   优点：支持多种模型，一个 API Key 通用")
                p("   获取地址：https://openrouter.ai/keys\n")
                
                p("   [0] 返回")
                
                api_key = inp("请输入 OpenRouter API Key")
                
                if api_key == "0":
                    break
                
                if not api_key:
                    pe("API Key 不能为空")
                    continue
                
                pi("正在验证 API Key...")
                
                if test_api_connection("openrouter", api_key):
                    ps("API Key 验证成功！")
                else:
                    pe("API Key 验证失败，请检查是否正确")
                    if inp("是否继续？ (y/n)", "n").lower() != "y":
                        continue
                
                # 从API获取模型列表
                p("\n📦 正在获取可用模型...")
                models_with_labels = get_provider_models_from_api("openrouter")
                
                if not models_with_labels:
                    # 回退到静态列表
                    try:
                        from VoidCube_cli.models import OPENROUTER_MODELS
                        models_with_labels = list(OPENROUTER_MODELS)
                    except Exception:
                        models_with_labels = [("gpt-4o", "推荐"), ("gpt-4o-mini", "免费")]
                
                p(f"\n可用模型 (共 {len(models_with_labels)} 个)：")
                for i, (model_id, desc) in enumerate(models_with_labels[:20], 1):
                    if desc:
                        p(f"   [{i}] {model_id} ({desc})")
                    else:
                        p(f"   [{i}] {model_id}")
                if len(models_with_labels) > 20:
                    p(f"   ... 还有 {len(models_with_labels) - 20} 个模型")
                
                p(f"\n请选择默认模型：")
                p("   [0] 返回")
                model_choice = inp("请输入数字")
                
                if model_choice == "0":
                    break
                
                try:
                    idx = int(model_choice) - 1
                    if 0 <= idx < len(models_with_labels):
                        selected_model = models_with_labels[idx][0]
                    else:
                        selected_model = models_with_labels[0][0]
                except (ValueError, IndexError):
                    selected_model = models_with_labels[0][0]
                
                p(f"\n选择的模型: {selected_model}")
                
                p("\n💾 保存配置...")

                if save_provider_config(
                    "openrouter",
                    label="OpenRouter",
                    selected_model=selected_model,
                    provider_type="openrouter",
                    base_url="https://openrouter.ai/api/v1",
                    api_key_env="OPENROUTER_API_KEY",
                    auth_mode="env",
                ):
                    ps("Provider 配置保存成功")
                    ps("默认模型保存成功")
                
                if console and hasattr(console, 'model'):
                    console.model = selected_model
                    ps("CLI 当前模型已更新")
                
                if console and hasattr(console, 'provider'):
                    console.provider = "openrouter"
                    ps("CLI 当前 Provider 已更新")
                
                if console and hasattr(console, 'requested_provider'):
                    console.requested_provider = "openrouter"
                    ps("CLI 请求 Provider 已更新")
                
                if save_env_value("OPENROUTER_API_KEY", api_key):
                    ps("API Key 保存成功")
                
                try:
                    from VoidCube_cli.config import load_config
                    from cli import CLI_CONFIG
                    new_config = load_config()
                    CLI_CONFIG.update(new_config)
                    ps("配置已重新加载")
                except Exception as e:
                    pi(f"重新加载配置时出错: {e}")
                
                ph("配置完成")
                ps("OpenRouter 配置完成！")
                p("\n运行 /doctor 检查配置状态")
                break
        
        elif choice == "2":
            # 自定义 Provider 配置
            while True:
                ph("自定义 Provider 配置")
                
                p("\n支持的 Provider：")
                providers = [
                    ("anthropic", "Anthropic (Claude)"),
                    ("openai", "OpenAI (GPT)"),
                    ("deepseek", "DeepSeek"),
                    ("gemini", "Google Gemini"),
                    ("ollama", "Ollama (本地)"),
                    ("custom", "自定义 Provider"),
                ]
                
                for i, (pid, desc) in enumerate(providers, 1):
                    p(f"   [{i}] {desc}")
                p("   [0] 返回")
                
                provider_choice = inp("\n请选择 Provider")
                
                if provider_choice == "0":
                    break
                
                try:
                    idx = int(provider_choice) - 1
                    if 0 <= idx < len(providers):
                        selected_provider = providers[idx][0]
                    else:
                        selected_provider = providers[0][0]
                except (ValueError, IndexError):
                    selected_provider = providers[0][0]
                
                p(f"\n选择的 Provider: {selected_provider}")
                
                if selected_provider == "ollama":
                    base_url = inp("Ollama Base URL", "http://localhost:11434")
                    api_key = ""
                    pi("Ollama 本地部署，无需 API Key")
                    model_name = inp("模型名称 (如 llama3, qwen2)")
                    if not model_name:
                        pe("模型名称不能为空")
                        continue
                    selected_model = model_name
                elif selected_provider == "custom":
                    provider_name = inp("Provider 名称")
                    base_url = inp("Base URL")
                    api_key = inp("API Key")
                    model_name = inp("模型名称")
                    if not model_name:
                        pe("模型名称不能为空")
                        continue
                    selected_model = model_name
                else:
                    base_url = ""
                    api_key = inp("API Key")
                    
                    if not api_key:
                        pe("API Key 不能为空")
                        continue
                    
                    # 从API获取模型列表
                    p("\n📦 正在获取可用模型...")
                    models_with_labels = get_provider_models_from_api(selected_provider)
                    
                    if models_with_labels:
                        p(f"\n{selected_provider.title()} 可用模型 (共 {len(models_with_labels)} 个)：")
                        for i, (mid, mdesc) in enumerate(models_with_labels[:20], 1):
                            if mdesc:
                                p(f"   [{i}] {mid} ({mdesc})")
                            else:
                                p(f"   [{i}] {mid}")
                        if len(models_with_labels) > 20:
                            p(f"   ... 还有 {len(models_with_labels) - 20} 个模型")
                        p("   [0] 手动输入模型名称")
                        
                        model_choice = inp("\n请选择模型")
                        
                        if model_choice == "0":
                            model_name = inp("请输入模型名称")
                            if not model_name:
                                pe("模型名称不能为空")
                                continue
                            selected_model = model_name
                        else:
                            try:
                                midx = int(model_choice) - 1
                                if 0 <= midx < len(models_with_labels):
                                    selected_model = models_with_labels[midx][0]
                                else:
                                    model_name = inp("请输入模型名称")
                                    if not model_name:
                                        pe("模型名称不能为空")
                                        continue
                                    selected_model = model_name
                            except (ValueError, IndexError):
                                model_name = inp("请输入模型名称")
                                if not model_name:
                                    pe("模型名称不能为空")
                                    continue
                                selected_model = model_name
                    else:
                        p("\n无法从API获取模型列表")
                        model_name = inp("请输入模型名称")
                        if not model_name:
                            pe("模型名称不能为空")
                            continue
                        selected_model = model_name
                
                p(f"\n将使用模型: {selected_model}")
                
                env_var_map = {
                    "anthropic": "ANTHROPIC_API_KEY",
                    "openai": "OPENAI_API_KEY",
                    "deepseek": "DEEPSEEK_API_KEY",
                    "gemini": "GEMINI_API_KEY",
                }
                
                env_var = env_var_map.get(selected_provider, "")
                
                p("\n💾 保存配置...")
                
                provider_key = selected_provider
                provider_label = selected_provider.title()
                provider_type = selected_provider
                auth_mode = "env"
                api_key_env = env_var

                if selected_provider == "ollama":
                    provider_key = "ollama"
                    provider_label = "Ollama"
                    provider_type = "ollama"
                    auth_mode = "none"
                    api_key_env = ""
                elif selected_provider == "custom":
                    provider_key = _provider_key_from_name(provider_name)
                    provider_label = provider_name
                    provider_type = "openai_compatible"
                    auth_mode = "stored" if api_key else "none"
                    api_key_env = ""

                if save_provider_config(
                    provider_key,
                    label=provider_label,
                    selected_model=selected_model,
                    provider_type=provider_type,
                    base_url=base_url,
                    api_key_env=api_key_env,
                    api_key=api_key if selected_provider == "custom" else "",
                    auth_mode=auth_mode,
                ):
                    ps("Provider 配置保存成功")
                    ps(f"默认模型保存成功: {selected_model}")
                
                if console and hasattr(console, 'model'):
                    console.model = selected_model
                    ps("CLI 当前模型已更新")
                
                if console and hasattr(console, 'provider'):
                    console.provider = provider_key
                    ps("CLI 当前 Provider 已更新")
                
                if console and hasattr(console, 'requested_provider'):
                    console.requested_provider = provider_key
                    ps("CLI 请求 Provider 已更新")
                
                if env_var and api_key:
                    if save_env_value(env_var, api_key):
                        ps(f"{env_var} 保存成功")
                    else:
                        pe(f"保存 {env_var} 失败")
                
                if selected_provider == "custom" and api_key and provider_name:
                        custom_env_var = f"{provider_name.upper()}_API_KEY"
                        if save_env_value(custom_env_var, api_key):
                            ps(f"{custom_env_var} 保存成功")
                
                ph("配置完成")
                ps("自定义 Provider 配置完成！")
                p("\n运行 /doctor 检查配置状态")
                break
        
        elif choice == "3":
            # 记忆系统模型配置
            while True:
                ph("记忆系统模型配置")
                
                memory_config = current_config.get("memory", {})
                memory_llm_config = memory_config.get("llm", {})
                current_memory_provider = memory_llm_config.get("provider", "未设置")
                current_memory_model = memory_llm_config.get("model", "未设置")
                
                p(f"\n当前记忆系统配置：")
                p(f"   Provider: {current_memory_provider}")
                p(f"   Model: {current_memory_model}")
                
                p("\n记忆系统用于存储对话历史和上下文信息。")
                p("建议使用与主模型相同的 Provider，但可以选择更轻量的模型。\n")
                
                memory_providers = [
                    ("openrouter", "OpenRouter"),
                    ("deepseek", "DeepSeek"),
                    ("openai", "OpenAI"),
                    ("anthropic", "Anthropic"),
                ]
                
                p("请选择记忆系统 Provider：")
                for i, (pid, desc) in enumerate(memory_providers, 1):
                    p(f"   [{i}] {desc}")
                p("   [0] 返回")
                
                mem_provider_choice = inp("\n请选择")
                
                if mem_provider_choice == "0":
                    break
                
                try:
                    idx = int(mem_provider_choice) - 1
                    if 0 <= idx < len(memory_providers):
                        mem_provider = memory_providers[idx][0]
                    else:
                        mem_provider = "openrouter"
                except (ValueError, IndexError):
                    mem_provider = "openrouter"
                
                p(f"\n选择的 Provider: {mem_provider}")
                
                # 从API获取模型列表
                p("\n📦 正在获取可用模型...")
                models_with_labels = get_provider_models_from_api(mem_provider)
                
                if models_with_labels:
                    p(f"\n{mem_provider.title()} 可用模型 (共 {len(models_with_labels)} 个)：")
                    for i, (mid, mdesc) in enumerate(models_with_labels[:20], 1):
                        if mdesc:
                            p(f"   [{i}] {mid} ({mdesc})")
                        else:
                            p(f"   [{i}] {mid}")
                    if len(models_with_labels) > 20:
                        p(f"   ... 还有 {len(models_with_labels) - 20} 个模型")
                    p("   [0] 手动输入模型名称")
                    
                    model_choice = inp("\n请选择模型")
                    
                    if model_choice == "0":
                        memory_model = inp("请输入模型名称")
                        if not memory_model:
                            pe("模型名称不能为空")
                            continue
                    else:
                        try:
                            midx = int(model_choice) - 1
                            if 0 <= midx < len(models_with_labels):
                                memory_model = models_with_labels[midx][0]
                            else:
                                memory_model = inp("请输入模型名称")
                                if not memory_model:
                                    pe("模型名称不能为空")
                                    continue
                        except (ValueError, IndexError):
                            memory_model = inp("请输入模型名称")
                            if not memory_model:
                                pe("模型名称不能为空")
                                continue
                else:
                    p("\n无法从API获取模型列表")
                    memory_model = inp("请输入模型名称")
                    if not memory_model:
                        pe("模型名称不能为空")
                        continue
                
                p(f"\n将使用记忆模型: {memory_model}")
                
                p("\n💾 保存配置...")
                
                if save_config_value("memory.llm.provider", mem_provider):
                    ps("记忆系统 Provider 保存成功")
                
                if save_config_value("memory.llm.model", memory_model):
                    ps(f"记忆系统模型保存成功: {memory_model}")
                
                ph("配置完成")
                ps("记忆系统模型配置完成！")
                break
        
        elif choice == "4":
            ph("当前配置")
            
            if not current_config:
                pi("未找到配置文件")
                continue
            
            import json
            p(json.dumps(current_config, indent=2, ensure_ascii=False))
            continue
        
        else:
            pe("无效选择，请重新选择。")
            continue
        
        # 跳出主循环
        break
    
    sys.stdout = original_stdout
