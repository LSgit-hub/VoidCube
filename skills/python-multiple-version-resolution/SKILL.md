---
name: python-multiple-version-resolution
description: >
  诊断和解决 Windows 上多版本 Python 冲突问题。当 check_dependencies 误报 Python 版本过旧、
  或 python 命令指向错误的 Python 版本时使用。触发词："Python 找不到"、"版本不对"、
  "PATH 优先级"、"多个 Python"、"python --version 结果不对"。
version: 1.0.0
platforms: [windows]
metadata:
  VoidCube:
    tags: [python, version-conflict, path, windows, environment]
    related_skills: [bootstrap]
---

# Python 多版本冲突解决技能

## 何时使用此技能

触发条件（满足任一即使用）：

1. **check_dependencies 误报** — 工具报告 Python 版本过旧（如 3.11），但用户已安装更新的版本
2. **用户反馈** — "我的 Python 3.14 为什么找不到"、"python --version 显示不对"
3. **PATH 冲突** — 系统中存在多个 Python 版本，需要调整优先级
4. **虚拟环境外异常** — 激活虚拟环境前 `python` 命令指向了错误的版本

## 核心原则

**先诊断，后修复。** 不要盲目重装 Python。Windows 上常见的问题是：
- 多个 Python 安装路径都在 PATH 中
- Windows Store 的 Python stub 占用了 `python` 命令名
- Chocolatey 安装的 Python 与实际安装的 Python 路径不同
- Git Bash 和 PowerShell 的 PATH 解析结果可能不同

## 诊断步骤

### Step 1: 识别所有 Python 安装位置

```bash
# 查找所有 python 可执行文件
where python python3 py 2>&1

# 分别检查每个版本
python --version
python3 --version
py --version

# 检查具体安装路径的版本
ls /c/Users/lishuo/AppData/Local/Python/bin/      # Chocolatey 安装
ls /c/Users/lishuo/AppData/Local/Programs/Python/  # 官方安装
ls /c/Users/lishuo/AppData/Local/Microsoft/WindowsApps/  # Windows Store stub
```

**关键观察**：
- `WindowsApps/python.exe` 通常是 Windows Store 的占位符（指向 3.11 或更旧）
- `AppData/Local/Python/bin/` 是 Chocolatey 安装的标准位置
- `AppData/Local/Programs/Python/PythonXXX/` 是官方安装程序的位置

### Step 2: 确认目标版本

询问用户期望的 Python 版本，或检查 VoidCube 要求：
- VoidCube 要求：**Python >= 3.14**
- 检查用户实际安装的版本：`/c/Users/lishuo/AppData/Local/Python/bin/python.exe --version`

### Step 3: 检查当前 PATH 顺序

```bash
# Git Bash (voidcube agent 默认 shell)
echo $PATH | tr ':' '\n' | grep -i python

# PowerShell (用户手动执行时)
[Environment]::GetEnvironmentVariable('PATH', 'User').Split(';') | Where-Object { $_ -like '*Python*' }
[Environment]::GetEnvironmentVariable('PATH', 'Machine').Split(';') | Where-Object { $_ -like '*Python*' }
```

**典型问题模式**：
```
❌ 错误顺序：
C:\...\Python311\Scripts     ← Python 3.11 (旧)
C:\...\Python311             ← Python 3.11 (旧)
C:\...\Local\Python\bin      ← Python 3.14 (新) ← 在后面！

✅ 正确顺序：
C:\...\Local\Python\bin      ← Python 3.14 (新) ← 在最前
C:\...\Python311\Scripts     ← Python 3.11 (旧)
C:\...\Python311             ← Python 3.11 (旧)
```

## 修复步骤

### 方法 A: 调整用户 PATH（推荐）

使用 PowerShell 修改用户级环境变量：

```powershell
$oldPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
$paths = $oldPath -split ';'
$pythonPaths = @()
$otherPaths = @()

foreach ($p in $paths) {
    if ($p -like '*Python*') {
        $pythonPaths += $p
    } else {
        $otherPaths += $p
    }
}

# 重新排序：目标版本在前，旧版本在后
$sortedPython = @()
foreach ($p in $pythonPaths) {
    # 排除旧版和 debugpy
    if ($p -notlike '*Python311*' -and $p -notlike '*debugpy*') {
        $sortedPython += $p
    }
}
foreach ($p in $pythonPaths) {
    if ($p -like '*Python311*' -or $p -like '*debugpy*') {
        $sortedPython += $p
    }
}

$newPath = ($otherPaths + $sortedPython) -join ';'
[Environment]::SetEnvironmentVariable('PATH', $newPath, 'User')
Write-Host 'PATH updated successfully'
```

### 方法 B: 创建 Python 版本别名（可选）

如果用户需要频繁切换版本，可以创建别名：

```bash
# 在 ~/.bashrc 中添加
alias python314='/c/Users/lishuo/AppData/Local/Python/bin/python.exe'
alias python311='/c/Users/lishuo/AppData/Local/Programs/Python/Python311/python.exe'
```

### 方法 C: 使用 py launcher（如果已安装）

Windows Python 安装程序通常附带 `py` launcher：

```bash
py -3.14 --version   # 指定版本
py -3.11 --version
py -3 --version      # 默认版本
```

## 验证修复

```bash
# 刷新 shell 缓存
hash -r

# 验证 python 命令
python --version          # 应显示 3.14.x
python3 --version         # 应显示 3.14.x
which python              # 应指向正确路径

# 运行完整依赖检查
check_dependencies(action="summary")
```

**注意**：Git Bash 可能需要重启才能看到新的 PATH。如果 `which python` 仍然显示旧路径，提示用户重启会话。

## 特殊情况处理

### 情况 1: Windows Store stub 占用了 python 命令

**现象**：`python` 指向 `WindowsApps/python.exe`，点击会打开 Microsoft Store

**解决**：
1. 将真正的 Python bin 路径移到 PATH 最前面（见方法 A）
2. 或在 PowerShell 中卸载 Windows Store Python：
   ```powershell
   Get-AppxPackage *Python* | Remove-AppxPackage
   ```

### 情况 2: Chocolatey 和官方安装器都安装了 Python

**现象**：两个版本都存在于 PATH 中

**解决**：
1. 确定哪个版本是用户想要的
2. 调整 PATH 优先级（方法 A）
3. 或者卸载不需要的版本：
   ```bash
   choco uninstall python  # 卸载 Chocolatey 版本
   # 或在"设置 > 应用"中卸载官方版本
   ```

### 情况 3: 项目虚拟环境问题

**现象**：`.venv` 中的 Python 版本与系统默认不一致

**解决**：
```bash
# 删除旧虚拟环境
rm -rf .venv

# 用新 Python 创建新虚拟环境
python -m venv .venv

# 激活并验证
source .venv/Scripts/activate
python --version
```

## 记忆保存

修复完成后，必须调用 `mem_remember` 记录用户的环境配置：

```python
mem_remember(
    title="Python PATH 优先级已修正",
    summary="用户已调整 PATH 环境变量，将 Python X.X 的路径（具体路径）移到 Python X.X 前面。现在 python 命令默认解析到 Python X.X。",
    evidence_refs=["terminal: python --version → Python X.X", "powershell: PATH 更新成功"],
    topics=["python", "environment", "path", "version-conflict"],
    importance=0.9
)
```

## 什么不需要做

- **不要**建议用户卸载旧版 Python（除非用户明确要求）
- **不要**修改系统级 PATH（只修改用户级即可）
- **不要**修改注册表（使用 PowerShell 的 [Environment] 类更安全）
- **不要**忽略用户的偏好（如果用户想要保持 3.11 为主版本，尊重这个选择）

## 相关技能

- `bootstrap` — 环境依赖检查和安装
- `memory-system-diagnostics` — 记忆系统问题诊断

## 示例会话

### 示例：用户说"我的 Python 3.14 为什么找不到"

```
用户：我的计算机中已经安装了 Python 3.14 为什么找不到？

Agent：让我检查一下你的 Python 环境...
  → where python python3 py
  → 发现：Python 3.11.9 在 WindowsApps，Python 3.14.6 在 AppData/Local/Python/bin
  
  Agent：找到了！你的 Python 3.14.6 安装在：
         C:\Users\lishuo\AppData\Local\Python\bin\python.exe
  
  但 PATH 中 Python 3.11 的路径排在前面，导致 `python` 命令指向了旧版本。
  
  让我帮你调整 PATH 优先级...
  → PowerShell: 调整 PATH，将 3.14 移到前面
  
  Agent：✅ 完成！现在 python --version 应该显示 Python 3.14.6
  
  → python --version → Python 3.14.6 ✅
  
  我已经记住这个配置了，下次不会误报。需要我重新运行环境检测吗？
```