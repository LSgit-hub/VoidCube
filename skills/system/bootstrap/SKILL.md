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

If dependencies are missing, sort them into two groups:

| Priority | Meaning | Action |
|----------|---------|--------|
| **Critical** | Agent cannot function without these (git, bash, python) | Install immediately |
| **Optional** | Tools degrade gracefully without these (docker, ripgrep) | Install if the user needs the related toolsets |

The `check_dependencies` output marks each dependency as `critical: true/false`.

### Step 3: Install Missing Dependencies

For each critical missing dependency, **one at a time**:

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

- **Git Bash** is required for the terminal tool.  Installing Git via
  `winget install Git.Git` provides both `git` and `bash.exe`.
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

## Example Session

```
User: just installed VoidCube, does everything work?

Agent: Let me check your environment first.
  → check_dependencies(action="summary")
  → Result: git ✗ CRITICAL, node ✗ CRITICAL, bash ✓, python ✓

Agent: Two critical dependencies are missing. Let me install them.

  [Step 1: Install Git]
  → terminal: winget install --id Git.Git -e --source winget
  → check_dependencies(action="check_one", name="git")
  → ✓ git 2.47.0 installed

  [Step 2: Install Node.js]
  → terminal: winget install OpenJS.NodeJS.LTS
  → check_dependencies(action="check_one", name="node")
  → ✓ node 22.11.0 installed

  → check_dependencies(action="summary")
  → all_ok: true ✓

Agent: Your environment is ready! All critical dependencies are installed.
       Skipped optional: docker, podman, apptainer (not needed unless you
       use container backends).
```
