import os

_current_lang = os.getenv("VOIDCUBE_LANG", "zh")

LANGS = {"zh", "en"}


def get_lang() -> str:
    return _current_lang


def set_lang(lang: str) -> None:
    global _current_lang
    lang = lang.lower().strip()
    if lang in LANGS:
        _current_lang = lang
    elif lang in ("中文", "chinese", "cn"):
        _current_lang = "zh"
    elif lang in ("英文", "english", "us"):
        _current_lang = "en"
    else:
        _current_lang = "zh"


def t(key: str, **kwargs) -> str:
    text = _MESSAGES.get(_current_lang, {}).get(key, _MESSAGES["en"].get(key, key))
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text


_MESSAGES = {
    "zh": {
        # Banner
        "banner.title": "VoidCube Agent v0.8",
        "banner.subtitle": "轻量级服务器运维与部署工具",
        # Chat loop
        "chat.model": "  模型: {model}",
        "chat.prompt": "VoidCube> ",
        "chat.hint": "  输入 /help 查看命令, exit 或 Ctrl+C 退出\n",
        "chat.bye": "  再见!",
        "chat.cleared": "  对话已清空。",
        "chat.unknown_cmd": "  未知命令: /{cmd}，输入 /help 查看可用命令。",
        "chat.no_api_key": "  未设置 API Key，请在 ~/.VoidCube/.env 中配置 VOIDCUBE_API_KEY",
        # Slash help
        "help.title": "  斜杠命令 (交互模式):",
        "help.help": "    /help                     显示此帮助",
        "help.exit": "    /exit, /quit              退出",
        "help.clear": "    /clear                    清空对话历史",
        "help.history": "    /history                  查看对话历史",
        "help.model": "    /model [名称]             查看或切换模型",
        "help.lang": "    /lang [zh|en]             切换语言 (中文/English)",
        "help.tools": "    /tools                    列出所有已注册工具",
        "help.shell_title": "  Shell:",
        "help.run": "    /run <命令>               直接执行 Shell 命令",
        "help.system_title": "  系统监控:",
        "help.status": "    /status                   系统信息 (CPU/内存/磁盘)",
        "help.services": "    /services [状态]          列出服务",
        "help.service": "    /service <操作> <名称>    管理服务 (start/stop/restart/status/logs/enable/disable)",
        "help.network_title": "  网络:",
        "help.ping": "    /ping <主机>              Ping 主机",
        "help.ports": "    /ports [主机]             扫描常用端口",
        "help.pkg_title": "  包管理 & Docker:",
        "help.install": "    /install <包>             安装包 (apt/yum/pip/npm)",
        "help.docker": "    /docker [ps|images|stop|start|restart|logs] [名称]  Docker 管理",
        "help.logsec_title": "  日志 & 安全:",
        "help.logs": "    /logs <路径> [模式]       读取/过滤日志文件",
        "help.firewall": "    /firewall [allow|deny] <端口>  防火墙管理",
        # Slash commands
        "cmd.model_switched": "  模型已切换为: {model}",
        "cmd.model_current": "  当前模型: {model}",
        "cmd.lang_switched": "  语言已切换为: {lang}",
        "cmd.lang_current": "  当前语言: {lang}",
        "cmd.usage_run": "  用法: /run <命令>",
        "cmd.usage_service": "  用法: /service <start|stop|restart|status|logs|enable|disable> <名称>",
        "cmd.usage_service_action": "  用法: /service <操作> <名称>",
        "cmd.usage_ping": "  用法: /ping <主机>",
        "cmd.usage_install": "  用法: /install <包>",
        "cmd.usage_logs": "  用法: /logs <路径> [模式]",
        "cmd.usage_firewall": "  用法: /firewall [allow|deny] <端口>",
        "cmd.usage_docker": "  用法: /docker [ps|images|stop|start|restart|logs] [名称]",
        "cmd.unknown_action": "  未知操作: {action}",
        "cmd.open_ports": "  开放端口: {ports}",
        # Section headers
        "section.system": "  === 系统 ===",
        "section.memory": "  === 内存 ===",
        "section.disk": "  === 磁盘 ===",
        # System prompt
        "prompt.identity": "你是 VoidCube，一个服务器运维与部署智能体。",
        "prompt.purpose": "你帮助用户管理服务器、部署应用、排查故障。",
        "prompt.guideline1": "- 做变更前先检查系统状态",
        "prompt.guideline2": "- 优先使用运维专用工具而非原始 Shell 命令",
        "prompt.guideline3": "- 重启/部署后验证服务运行正常",
        "prompt.guideline4": "- 执行命令前确认安全性",
        "prompt.guideline5": "- 同时支持 Linux 和 Windows 环境",
        "prompt.guideline6": "- 回复简洁直接",
        # Config display
        "config.home": "  主目录:    {value}",
        "config.model": "  模型:      {value}",
        "config.base_url": "  接口地址:  {value}",
        "config.api_key": "  API密钥:   {value}",
        "config.auto_approve": "  自动审批:  {value}",
        # CLI help
        "cli.title": "VoidCube - 服务器运维与部署智能体",
        "cli.usage": "用法:",
        "cli.chat": "  VoidCube                          交互对话模式",
        "cli.chat_msg": "  VoidCube chat [消息]              与智能体对话",
        "cli.run": "  VoidCube run <命令>               执行 Shell 命令",
        "cli.deploy": "  VoidCube deploy <目标>            部署应用",
        "cli.status": "  VoidCube status                   系统状态 (CPU/内存/磁盘)",
        "cli.services": "  VoidCube services [状态]          列出服务",
        "cli.service": "  VoidCube service <操作> <名称>    管理服务",
        "cli.install": "  VoidCube install <包>             安装包 (apt/yum/pip/npm)",
        "cli.docker": "  VoidCube docker [ps|images|logs|stop|start|restart]  Docker 管理",
        "cli.ping": "  VoidCube ping <主机>              Ping 主机",
        "cli.ports": "  VoidCube ports [主机]             扫描常用端口",
        "cli.logs": "  VoidCube logs <路径> [模式]       读取/过滤日志",
        "cli.firewall": "  VoidCube firewall [allow|deny] <端口>  防火墙管理",
        "cli.config": "  VoidCube config                   显示配置",
        "cli.version": "  VoidCube version                  显示版本",
        "cli.help": "  VoidCube help                     显示帮助",
        # Approval
        "approval.dangerous": "\n  [!] 检测到危险命令: {reason}\n  [!] 命令: {command}\n  [?] 仍要执行? (y/N): ",

        # Setup wizard
        "setup.welcome": "欢迎使用 VoidCube! 首次运行需要完成基础配置。",
        "setup.step1": "\n  [1/4] 设置 API Key",
        "setup.api_key_prompt": "  请输入 API Key (如 sk-...): ",
        "setup.api_key_hint": "  (直接回车跳过，稍后可通过 /config 设置)",
        "setup.step2": "\n  [2/4] 选择模型",
        "setup.model_prompt": "  选择模型编号 [1-6]: ",
        "setup.step3": "\n  [3/4] 设置 API Base URL",
        "setup.base_url_prompt": "  请输入 Base URL (直接回车使用默认): ",
        "setup.step4": "\n  [4/4] 选择界面语言",
        "setup.lang_prompt": "  选择语言 [1/2]: ",
        "setup.done": "\n  配置完成! 已保存到 {path}",
        "setup.skip": "  (已跳过)",

        # Chat status bar
        "chat.statusbar": "  ── 模型: {model} │ 语言: {lang} │ 消息: {msgs} ──",
        "chat.no_key_warning": "  ⚠ 未配置 API Key! 使用 /config 或 /setup 进行配置",
        "chat.first_run_hint": "\n  💡 提示: 这是首次运行，输入 /setup 完成配置，或 /help 查看所有命令\n",
        "chat.welcome_back": "\n  输入 /help 查看命令，直接输入文字与 Agent 对话\n",

        # /config command
        "config.title": "  ══ 当前配置 ══",
        "config.lang": "  语言:      {value}",
        "config.env_path": "  配置文件:  {value}",
        "config.not_set": "(未设置)",
        "config.hint": "\n  使用 /setup 重新配置，或 /model, /lang, /baseurl 单独修改",

        # /setup command (interactive)
        "setup.interactive.title": "\n  ══ 配置向导 ══",
        "setup.interactive.api_key": "\n  API Key [{current}]: ",
        "setup.interactive.model": "  模型 [{current}]: ",
        "setup.interactive.base_url": "  Base URL [{current}]: ",
        "setup.interactive.lang": "  语言(zh/en) [{current}]: ",
        "setup.interactive.saved": "  ✓ 配置已保存",

        # /baseurl command
        "cmd.baseurl_current": "  当前 Base URL: {url}",
        "cmd.baseurl_switched": "  Base URL 已切换为: {url}",

        # Model list for selection
        "model.list_title": "  可用模型:",
        "model.1": "    1. gpt-4o-mini     (快速，经济)",
        "model.2": "    2. gpt-4o           (均衡)",
        "model.3": "    3. gpt-4o-mini      (OpenAI 轻量)",
        "model.4": "    4. deepseek-chat    (DeepSeek)",
        "model.5": "    5. qwen-plus        (通义千问)",
        "model.6": "    6. glm-4-flash      (智谱清言)",
    },
    "en": {
        "banner.title": "VoidCube Agent v0.8",
        "banner.subtitle": "Lightweight Server Ops & Deployment Tool",
        "chat.model": "  Model: {model}",
        "chat.prompt": "VoidCube> ",
        "chat.hint": "  Type /help for commands, exit or Ctrl+C to quit\n",
        "chat.bye": "  Bye!",
        "chat.cleared": "  Conversation cleared.",
        "chat.unknown_cmd": "  Unknown command: /{cmd}. Type /help for available commands.",
        "chat.no_api_key": "  No API Key set, configure VOIDCUBE_API_KEY in ~/.VoidCube/.env",
        "help.title": "  Slash Commands (interactive mode):",
        "help.help": "    /help                     Show this help",
        "help.exit": "    /exit, /quit              Exit",
        "help.clear": "    /clear                    Clear conversation history",
        "help.history": "    /history                  Show conversation history",
        "help.model": "    /model [name]             Show or switch model",
        "help.lang": "    /lang [zh|en]             Switch language (Chinese/English)",
        "help.tools": "    /tools                    List all registered tools",
        "help.shell_title": "  Shell:",
        "help.run": "    /run <command>            Execute shell command directly",
        "help.system_title": "  System Monitoring:",
        "help.status": "    /status                   System info (CPU/memory/disk)",
        "help.services": "    /services [state]         List services",
        "help.service": "    /service <action> <name>  Manage service (start/stop/restart/status/logs/enable/disable)",
        "help.network_title": "  Network:",
        "help.ping": "    /ping <host>              Ping a host",
        "help.ports": "    /ports [host]             Scan common ports",
        "help.pkg_title": "  Package & Docker:",
        "help.install": "    /install <packages>       Install packages (apt/yum/pip/npm)",
        "help.docker": "    /docker [ps|images|stop|start|restart|logs] [name]  Docker management",
        "help.logsec_title": "  Logs & Security:",
        "help.logs": "    /logs <path> [pattern]    Read/filter log files",
        "help.firewall": "    /firewall [allow|deny] <port>  Firewall management",
        "cmd.model_switched": "  Model switched to: {model}",
        "cmd.model_current": "  Current model: {model}",
        "cmd.lang_switched": "  Language switched to: {lang}",
        "cmd.lang_current": "  Current language: {lang}",
        "cmd.usage_run": "  Usage: /run <command>",
        "cmd.usage_service": "  Usage: /service <start|stop|restart|status|logs|enable|disable> <name>",
        "cmd.usage_service_action": "  Usage: /service <action> <name>",
        "cmd.usage_ping": "  Usage: /ping <host>",
        "cmd.usage_install": "  Usage: /install <packages>",
        "cmd.usage_logs": "  Usage: /logs <path> [pattern]",
        "cmd.usage_firewall": "  Usage: /firewall [allow|deny] <port>",
        "cmd.usage_docker": "  Usage: /docker [ps|images|stop|start|restart|logs] [name]",
        "cmd.unknown_action": "  Unknown action: {action}",
        "cmd.open_ports": "  Open ports: {ports}",
        "section.system": "  === System ===",
        "section.memory": "  === Memory ===",
        "section.disk": "  === Disk ===",
        "prompt.identity": "You are VoidCube, a server operations and deployment agent.",
        "prompt.purpose": "You help users manage servers, deploy applications, and troubleshoot issues.",
        "prompt.guideline1": "- Check system status before making changes",
        "prompt.guideline2": "- Use specific ops tools over raw shell commands when available",
        "prompt.guideline3": "- Verify services are running after restart/deploy",
        "prompt.guideline4": "- Always check if commands are safe before execution",
        "prompt.guideline5": "- Support both Linux and Windows environments",
        "prompt.guideline6": "- Be concise and direct in responses",
        "config.home": "  Home:      {value}",
        "config.model": "  Model:     {value}",
        "config.base_url": "  Base URL:  {value}",
        "config.api_key": "  API Key:   {value}",
        "config.auto_approve": "  Auto Approve: {value}",
        "cli.title": "VoidCube - Server operations & deployment agent",
        "cli.usage": "Usage:",
        "cli.chat": "  VoidCube                          Interactive chat mode",
        "cli.chat_msg": "  VoidCube chat [message]           Chat with the agent",
        "cli.run": "  VoidCube run <command>            Execute a shell command",
        "cli.deploy": "  VoidCube deploy <target>          Deploy an application",
        "cli.status": "  VoidCube status                   Show system status (CPU/mem/disk)",
        "cli.services": "  VoidCube services [state]         List services",
        "cli.service": "  VoidCube service <action> <name>  Manage service",
        "cli.install": "  VoidCube install <package>        Install package (apt/yum/pip/npm)",
        "cli.docker": "  VoidCube docker [ps|images|logs|stop|start|restart]  Docker management",
        "cli.ping": "  VoidCube ping <host>              Ping a host",
        "cli.ports": "  VoidCube ports [host]             Scan common ports",
        "cli.logs": "  VoidCube logs <path> [pattern]    Read/filter log files",
        "cli.firewall": "  VoidCube firewall [allow|deny] [port]  Firewall management",
        "cli.config": "  VoidCube config                   Show configuration",
        "cli.version": "  VoidCube version                  Show version",
        "cli.help": "  VoidCube help                     Show this help",
        "approval.dangerous": "\n  [!] Dangerous command detected: {reason}\n  [!] Command: {command}\n  [?] Execute anyway? (y/N): ",

        "setup.welcome": "Welcome to VoidCube! First-time setup required.",
        "setup.step1": "\n  [1/4] Set API Key",
        "setup.api_key_prompt": "  Enter API Key (e.g. sk-...): ",
        "setup.api_key_hint": "  (Press Enter to skip, configure later via /config)",
        "setup.step2": "\n  [2/4] Choose Model",
        "setup.model_prompt": "  Select model number [1-6]: ",
        "setup.step3": "\n  [3/4] Set API Base URL",
        "setup.base_url_prompt": "  Enter Base URL (Enter for default): ",
        "setup.step4": "\n  [4/4] Choose Language",
        "setup.lang_prompt": "  Select language [1/2]: ",
        "setup.done": "\n  Setup complete! Saved to {path}",
        "setup.skip": "  (skipped)",

        "chat.statusbar": "  ── Model: {model} │ Lang: {lang} │ Messages: {msgs} ──",
        "chat.no_key_warning": "  ⚠ No API Key configured! Use /config or /setup to configure",
        "chat.first_run_hint": "\n  💡 Tip: First run detected. Type /setup to configure, or /help for commands\n",
        "chat.welcome_back": "\n  Type /help for commands, or just chat with the agent\n",

        "config.title": "  ══ Current Config ══",
        "config.lang": "  Language:  {value}",
        "config.env_path": "  Config:    {value}",
        "config.not_set": "(not set)",
        "config.hint": "\n  Use /setup to reconfigure, or /model, /lang, /baseurl to change individually",

        "setup.interactive.title": "\n  ══ Setup Wizard ══",
        "setup.interactive.api_key": "\n  API Key [{current}]: ",
        "setup.interactive.model": "  Model [{current}]: ",
        "setup.interactive.base_url": "  Base URL [{current}]: ",
        "setup.interactive.lang": "  Language(zh/en) [{current}]: ",
        "setup.interactive.saved": "  ✓ Configuration saved",

        "cmd.baseurl_current": "  Current Base URL: {url}",
        "cmd.baseurl_switched": "  Base URL switched to: {url}",

        "model.list_title": "  Available models:",
        "model.1": "    1. gpt-4o-mini     (fast, cheap)",
        "model.2": "    2. gpt-4o           (balanced)",
        "model.3": "    3. gpt-4o-mini      (OpenAI lite)",
        "model.4": "    4. deepseek-chat    (DeepSeek)",
        "model.5": "    5. qwen-plus        (Tongyi Qianwen)",
        "model.6": "    6. glm-4-flash      (Zhipu Qingyan)",
    },
}
