# 编辑器设置指南

本项目包含了推荐的编辑器配置，帮助团队保持代码风格一致。

## 已包含的配置

### 1. `.editorconfig` (跨编辑器通用)
已自动应用于支持 EditorConfig 的编辑器（VS Code, IntelliJ, Sublime Text 等）。

主要配置：
- UTF-8 编码
- 4 空格缩进（Python, Shell）
- 2 空格缩进（YAML, JSON, JS/TS）
- 保留 markdown 尾随空格
- 自动在文件末尾添加换行
- 移除尾部空格

## 各编辑器配置

### VS Code (推荐)

#### 1. 安装扩展
建议安装以下扩展：
- EditorConfig for VS Code
- Python
- Pylance
- GitLens
- Prettier (可选)

#### 2. 项目级别设置（推荐）

在 `.vscode/settings.json` 中添加以下内容（此文件已被 .gitignore 忽略，不会被提交）：

```json
{
    "files.exclude": {
        "**/.git": true,
        "**/.env": true,
        "**/.env.*": true,
        "**/__pycache__": true,
        "**/*.pyc": true,
        "**/.pytest_cache": true,
        "**/.pytest_tmp": true,
        "**/.mypy_cache": true,
        "**/.trae": true,
        "**/.arts": true,
        "**/.VoidCube": true,
        "**/.venv": true,
        "**/venv": true,
        "**/.body-slots": true,
        "**/.body-registry.json": true,
        "**/.body-active.json": true,
        "**/soul-runtime": true,
        "**/.mem": true,
        "**/dist": true,
        "**/build": true,
        "**/*.egg-info": true
    },
    "search.exclude": {
        "**/.git": true,
        "**/.env": true,
        "**/.env.*": true,
        "**/__pycache__": true,
        "**/*.pyc": true,
        "**/.pytest_cache": true,
        "**/.pytest_tmp": true,
        "**/.mypy_cache": true,
        "**/.trae": true,
        "**/.arts": true,
        "**/.VoidCube": true,
        "**/.venv": true,
        "**/venv": true,
        "**/.body-slots": true,
        "**/dist": true,
        "**/build": true,
        "**/*.egg-info": true
    },
    "files.watcherExclude": {
        "**/.git/objects/**": true,
        "**/.git/subtree-cache/**": true,
        "**/node_modules/**": true,
        "**/.pytest_tmp/**": true,
        "**/.pytest_cache/**": true,
        "**/.mypy_cache/**": true
    },
    "python.analysis.autoImportCompletions": true,
    "python.analysis.typeCheckingMode": "basic"
}
```

#### 3. 全局用户设置（可选）

如果想在所有项目中使用类似的设置，可以在 VS Code 的用户设置中添加：

1. 按 `Ctrl + Shift + P` (Windows/Linux) 或 `Cmd + Shift + P` (Mac)
2. 输入 "Preferences: Open User Settings (JSON)"
3. 粘贴上述配置（调整为您喜欢的设置）

### IntelliJ / PyCharm

1. 确保已安装 EditorConfig 插件（通常默认包含）
2. File → Settings → Editor → Code Style
3. 选择 "Enable EditorConfig support"

### Sublime Text

1. 安装 EditorConfig 插件（通过 Package Control）
2. 插件会自动识别项目根目录的 `.editorconfig`

### Vim / Neovim

使用 [editorconfig-vim](https://github.com/editorconfig/editorconfig-vim) 插件：

```vim
" 使用 vim-plug
Plug 'editorconfig/editorconfig-vim'
```

## Git 忽略文件说明

`.gitignore` 中配置的所有文件和文件夹都不会被 Git 跟踪：

- 环境变量和密钥：`.env`, `.env.*`
- Python 缓存：`__pycache__/`, `*.pyc`, `.mypy_cache/`
- 测试缓存：`.pytest_cache/`, `.pytest_tmp/`
- 本地状态：`.trae/`, `.arts/`, `.VoidCube/`
- 运行时：`.body-slots/`, `soul-runtime/`, `.mem/`
- 虚拟环境：`.venv/`, `venv/`
- 构建产物：`dist/`, `build/`, `*.egg-info/`

## 工作流程建议

### Python 运行环境

当前本地推荐环境是项目内 Python 3.14.6：

- 解释器目录：`.python-3.14.6/`
- 虚拟环境：`.venv/`
- 旧失效环境备份：`.venv-python313-broken/`

验证命令：

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip check
```

在当前沙箱里运行 pytest 时，建议显式指定 workspace 内临时目录，避免访问用户级 Temp 目录失败：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gateway_activity.py tests/test_supervisor_runtime_wiring.py -q --basetemp=.tmp-pytest-python314
```

1. 提交前检查
   - 确保没有密钥或凭证文件
   - 清理临时文件
   - 运行测试

2. 代码审查
   - 确保遵循项目风格
   - 检查 `.gitignore` 规则
