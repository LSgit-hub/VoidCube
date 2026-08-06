---
name: bootstrap
description: >
  Agent self-bootstrap — check and install external runtime dependencies
  (git, node, bash, docker, etc.) so the agent environment is ready for
  all tools.  Triggered on first install, on `voidcube doctor` failures,
  when a tool reports a missing command, or when the user asks to
  "setup", "bootstrap", or "检查环境".
version: 1.0.0
platforms: [windows, macos, linux]
metadata:
  VoidCube:
    tags: [setup, bootstrap, dependency, environment, doctor]
    related_skills: []
---

# Agent Self-Bootstrap Skill

## When to Use This Skill

Trigger this skill when **any** of these hold:

1. **First install** — VoidCube was just installed and no environment check
   has been run yet.
2. **`voidcube doctor` reports issues** — the doctor command found missing
   or misconfigured dependencies.
3. **Tool failures** — a tool call fails with "command not found", missing
   executable, or a related error suggesting a dependency is absent.
4. **User asks** — "setup", "bootstrap", "check my environment", "安装依赖",
   "检查环境", "configure dependencies", etc.
5. **After OS upgrade or migration** — the user moved to a new machine or
   upgraded their OS.

## Core Principle

**Always check before installing.**  Never assume a dependency is missing
without running `check_dependencies` first.  Many dependencies (git, bash)
ship with the OS or were already installed by another tool.

## ⚠️ Windows Pre-Flight: The Git Bootstrap Deadlock

On **Windows only**, there is a bootstrap chicken-and-egg problem that this
skill MUST handle explicitly:

```
The terminal tool requires Git Bash (bash.exe) to execute ANY command.
But on a fresh Windows machine, Git (which provides bash.exe) is not installed.
→ The terminal tool cannot run winget to install Git.
→ Deadlock.
```

**When `check_dependencies` reports git or bash as missing on Windows:**

1. **Do NOT attempt to run `terminal` to install git.**  The terminal tool
   will fail with "Git Bash not found" because `bash.exe` does not exist yet.
2. **Present the user with manual install instructions instead:**

   > 🚨 **Git Bash is required before VoidCube can auto-install anything.**
   >
   > Since Git is not installed yet, the terminal tool cannot start. Please
   > install Git manually first:
   >
   > 1. Download Git for Windows: **https://git-scm.com/download/win**
   > 2. Run the installer (default options are fine)
   > 3. **Restart your terminal / VoidCube session** (PATH changes
   >    won't take effect until a new shell is opened)
   > 4. After restart, tell me "继续" or "continue" and I'll install the
   >    remaining dependencies automatically.

3. **Stop here.**  Do not proceed to Step 2 until git is verified working.
   The user must restart their session for PATH to pick up `bash.exe`.
   After restart, re-run `check_dependencies(action="summary")` to confirm
   git is now found, then continue with the remaining steps.

This deadlock does NOT exist on macOS or Linux — both ship with `bash`
as a native OS component, so the terminal tool works even without git.

## Step-by-Step Procedure

### Step 1: Run the Environment Scan

Call the `check_dependencies` tool with `action="summary"`:

```
check_dependencies(action="summary")
```

This returns:
- A table of every tracked dependency with status (✓ / ✗ / ⚠)
- Platform-specific install commands for missing items
- A clear `all_ok` flag

**If `all_ok` is `true`** — report to the user that the environment is
ready.  No further action needed.

### Step 2: Prioritize

If dependencies are missing, sort them into three groups:

| Priority | Meaning | Examples | Action |
|----------|---------|----------|--------|
| **Blocking** | Cannot be auto-installed because the terminal tool itself depends on them | Windows: git, bash | Tell user to install manually, then restart session (see Windows Pre-Flight above) |
| **Critical** | Agent cannot function well without these, but the terminal tool CAN install them | python (version check), node (if critical to user's workflow) | Install automatically via terminal |
| **Optional** | Tools degrade gracefully without these | docker, podman, ripgrep, cosign | Install if the user needs the related toolsets |

**Detection rule for Blocking:** On Windows, if `git` or `bash` is in the
missing critical list, they are Blocking.  Everything else is Critical or
Optional based on `critical: true/false` in the dependency status.

When Blocking dependencies exist, **handle them first and stop** — the user
must install manually and restart before the terminal tool can be used for
the remaining items.

### Step 3: Install Missing Dependencies

**Pre-condition:** All Blocking dependencies (Windows git/bash deadlock)
must already be resolved before this step.  If `git` or `bash` is still
missing on Windows, go back to the Windows Pre-Flight section — do NOT
attempt auto-install.

For each remaining critical (non-blocking) dependency, **one at a time**:

1. Read the `install_command` from the dependency status.
2. Use the `terminal` tool to run the install command.
3. **Wait for the command to complete** — installers can take several minutes.
4. Re-run `check_dependencies(action="check_one", name="<dep>")` to verify
   the install succeeded.
5. If verification fails:
   - Check the terminal output for errors.
   - Try the `windows_alt` / `linux_rpm` alternative install command if
     the primary one failed.
   - On Windows, remind the user that **a terminal restart may be needed**
     for PATH changes to take effect (winget and choco update the user's
     PATH but existing shells don't see it until restart).

### Step 4: PATH Refresh (Windows-specific)

On Windows, after installing via `winget` or `choco`, the current shell
may not see the new executables.  Have the user either:

- Restart the VoidCube session, **or**
- Refresh PATH manually:
  ```powershell
  $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
              [System.Environment]::GetEnvironmentVariable("Path","User")
  ```

### Step 5: Final Verification

After all critical dependencies are installed:

```
check_dependencies(action="summary")
```

Present a clean summary to the user:
- ✓ which deps are now ready
- — which optional deps were skipped (explain what they enable)
- Any remaining issues and how to fix them

## Platform-Specific Notes

### Windows

- **🚨 Git Bash is the single manual pre-requisite.**  VoidCube's terminal
  tool depends on `bash.exe` from Git for Windows.  If Git is not installed,
  the agent CANNOT auto-install it — the user must do this manually (see
  Windows Pre-Flight section above).  Once Git is installed and the session
  is restarted, everything else (Node.js, Podman, ripgrep, etc.) can be
  installed automatically via `winget`.
- **`winget` is the preferred package manager.**  It ships with Windows 10
  (21H2+) and Windows 11.  If `winget` itself is unavailable (very old
  Windows builds), fall back to `choco` or direct download links.
- If Git is installed to a non-standard location, remind the user about
  the `VOIDCUBE_GIT_BASH_PATH` environment variable.
- **Node.js**: the `winget` package is `OpenJS.NodeJS.LTS`.
- Administrator permissions may be needed for some installers.

### macOS

- **Homebrew** is assumed for most installs.  If Homebrew is not installed,
  the install commands will fail.  Offer to install Homebrew first:
  ```bash
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  ```
- Xcode Command Line Tools may be needed for some packages.

### Linux (Debian/Ubuntu)

- `apt-get update` before installing to ensure package lists are current.
- Some packages (Node.js, Docker) need additional APT repositories.

### Linux (RHEL/Fedora)

- `dnf` is the primary package manager.
- EPEL may be needed for some packages: `sudo dnf install -y epel-release`.

## What NOT to Do

- **Do NOT use `terminal` to install Git on Windows when Git is missing.**
  The terminal tool depends on Git Bash.  This is the one dependency the
  agent cannot self-install on Windows — present the manual download link
  and wait for the user to restart their session.
- **Do NOT install optional dependencies unprompted.**  If the user only
  uses local terminal and file tools, don't install Docker or Podman.
- **Do NOT re-install something that already works.**  `check_dependencies`
  verifies both presence and version — if it says "OK", skip it.
- **Do NOT run install commands in parallel.**  Package managers (winget,
  apt, brew) typically lock themselves; parallel installs will fail.
- **Do NOT use `code_execution` for system-level installs.**  Use the
  `terminal` tool, which runs with the user's real shell and PATH.

## Quick Reference: Common Install Commands

| Dependency | Windows | macOS | Linux |
|-----------|---------|-------|-------|
| Git | `winget install Git.Git` | `brew install git` | `apt install git` |
| Node.js | `winget install OpenJS.NodeJS.LTS` | `brew install node` | see manifest |
| Docker | `winget install Docker.DockerDesktop` | `brew install --cask docker` | `curl ... \| sh` |
| ripgrep | `winget install BurntSushi.ripgrep.MSVC` | `brew install ripgrep` | `apt install ripgrep` |

*The `check_dependencies` tool always returns the exact command for the
current platform — prefer that over this table.*

## Example Sessions

### Example 1: macOS/Linux (fully automatic)

```
User: just installed VoidCube, does everything work?

Agent: Let me check your environment first.
  → check_dependencies(action="summary")
  → Result: git ✓, bash ✓, node ✗, python ✓, docker ✗

Agent: Node.js is missing. Let me install it.
  → terminal: brew install node        (macOS)
  → check_dependencies(action="check_one", name="node")
  → ✓ node 22.11.0 installed

Agent: All critical dependencies are ready. Docker was skipped
       (optional — only needed for container backends).
```

### Example 2: Windows fresh install (manual Git first, then automatic)

```
User: 我刚装的电脑，帮我配好 VoidCube 环境

Agent: 让我先检查一下环境。
  → check_dependencies(action="summary")
  → Result: git ✗ CRITICAL, bash ✗ CRITICAL, node ✗, python ✓

Agent: 🚨 检测到 Windows 上的初始环境问题：

       Git Bash 是 VoidCube 终端工具运行的前提。由于当前系统没有
       安装 Git，我无法通过命令行自动安装任何依赖。

       请先手动安装 Git for Windows：
       1. 下载：https://git-scm.com/download/win
       2. 运行安装程序（默认选项即可）
       3. ⚠️ 安装完成后，请重启 VoidCube 会话（PATH 变更需新 Shell 生效）
       4. 重启后告诉我"继续"，我会自动完成剩余依赖的安装。

       （安装 Git 后，Node.js、Podman、ripgrep 等我都可以自动装）

--- 用户安装 Git、重启会话后 ---

User: 继续

Agent: 让我重新检查环境。
  → check_dependencies(action="summary")
  → Result: git ✓ 2.47.0, bash ✓ 5.2, node ✗, python ✓

Agent: Git Bash 就绪！现在开始自动安装剩余依赖。

  [安装 Node.js]
  → terminal: winget install OpenJS.NodeJS.LTS
  → ✓ node 22.11.0 installed

  [安装 Podman — 用户之前说过需要]
  → terminal: winget install RedHat.Podman
  → ✓ podman 5.2 installed

  → check_dependencies(action="summary")
  → all_ok: true ✓

Agent: 环境配置完成！
       ✓ Git 2.47.0
       ✓ Node.js 22.11.0
       ✓ Podman 5.2
       ✓ Python 3.14
       — ripgrep 已跳过（可选，文件搜索性能优化用）
```
