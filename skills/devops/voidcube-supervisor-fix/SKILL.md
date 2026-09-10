---
name: voidcube-supervisor-fix
description: 诊断和修复 VoidCube supervisor 问题——启动失败（残留进程/PID、git 工作树损坏）以及已启动但 degraded（slot_worktree_dirty / slot_head_metadata_mismatch，用官方 prepare 入口重物化 slot 修复）
category: devops
created: 2026-08-21
updated: 2026-09-10
---

# VoidCube Supervisor 启动故障排查与修复

## 问题描述

Supervisor 启动失败，显示 "supervisor did not respond within 15.0s" 或 "supervisor did not respond within 30.0s" 错误。

## 症状

- 端口 6002 (supervisor) 未监听
- 端口 6000 (gateway) 未监听
- 端口 6001 (memory) 可能未监听
- `~/.VoidCube/run/` 目录下有残留的 PID 文件
- supervisor.log 显示 PermissionError 或 git 引用错误

## 根因分析

### 1. 内存后端服务未运行
- `mem` 后端依赖外部内存服务
- 如果 memory 进程已停止，supervisor 无法启动

### 2. Git 工作树损坏
- slot-A 的 worktree `.git` 目录状态异常
- 可能是符号链接损坏或权限问题
- Windows 文件系统锁定导致的访问被拒绝

### 3. 配置路径错误
- `meta.json` 中的 `materialized_from` 指向不存在的路径
- 例如: `F:\My_code\Traecode\VoidCube` 但实际路径是 `F:\My_code\VScode_py\VoidCube`

## 解决方案

### 步骤 1: 清理残留进程和 PID 文件

```bash
# 查找并终止残留的进程
ps aux | grep -E "voidcube|supervisor|gateway|memory" | grep -v grep
taskkill //F //PID <PID> 2>/dev/null

# 清理 PID 文件
rm -f ~/.VoidCube/run/*.pid
```

### 步骤 2: 检查并修复 Git 工作树

```bash
# 检查 worktree 状态
ls -la ~/.VoidCube/runtime/body/slots/slot-A/worktree/.git

# 如果是文件而不是目录，尝试修复
cd ~/.VoidCube/runtime/body/slots/slot-A/worktree
rm -f .git
git init

# 或者从正确的源码仓库同步
git remote add origin /f/My_code/VScode_py/VoidCube
git fetch origin
git checkout main
```

### 步骤 3: 使用正确的 voidcube 命令启动

```bash
# 定位 voidcube 可执行文件
cd /f/My_code/VScode_py/VoidCube

# 启动服务
./venv/Scripts/voidcube.exe serve start

# 检查状态
./venv/Scripts/voidcube.exe serve status
./venv/Scripts/voidcube.exe status
```

### 步骤 4: 验证服务健康

```bash
# 检查端口监听
netstat -ano | grep -E "6000|6001|6002"

# 检查健康状态
curl http://localhost:6002/health
curl http://localhost:6000/health
```

## 常见错误及处理

### 错误 1: PermissionError accessing .git/objects
```
PermissionError: [WinError 5] 拒绝访问
```
**处理**: 这是 Windows 文件锁定问题。重启系统或手动解锁文件后重试。

### 错误 2: is not a .git file, error code 7
```
git reference error: "is not a .git file, error code 7"
```
**处理**: `.git` 应该是目录而不是文件。删除并重新初始化。

### 错误 3: supervisor did not respond within timeout
```
⚠ supervisor did not respond within 15.0s
```
**处理**: 检查 memory 服务是否运行，清理 PID 文件后重启。

## 场景二：Supervisor 活着但 degraded（slot 工作树 / 元数据不一致）

服务 6000-6003 全部可访问，但 `GET http://localhost:6002/health` 返回：

```json
{"status": "degraded",
 "body_runtime": {"active_slot": "slot-A", "healthy": false,
   "violations": [{"code": "slot_worktree_dirty", "slot_id": "slot-A"}]}}
```

两个常见 violation：

- `slot_worktree_dirty`：`meta.active_commit`/`candidate_commit` 之间的 worktree 有未提交改动。
- `slot_head_metadata_mismatch`：worktree 实际 HEAD != 记录的 commit 元数据。

**不要**直接 `git reset --hard master` 去“清干净”。先用只读对比判断哪一侧才是权威版本：

```bash
python -c "import subprocess,pathlib; root=pathlib.Path('.'); slot=pathlib.Path.home()/'.VoidCube/runtime/body/slots/slot-A/worktree';
for f in ['src/voidcube/...']:
 r=subprocess.run(['git','diff','--no-index','--stat',str(root/f),str(slot/f)],capture_output=True,text=True,encoding='utf-8',errors='replace'); print(f,'SAME' if r.returncode==0 else r.stdout.strip())"
```

实测经验：slot 侧的“未提交改动”往往是**过期回退**（比主仓库少逻辑），不是待合并的新修复。确认后才可以对齐。

### 修复步骤

1. **先备份 body 元数据**（写运行时状态前必须留回滚点）：

```bash
python -c "import pathlib,shutil,datetime; body=pathlib.Path.home()/'.VoidCube/runtime/body';
dst=body/'backups'/('body-meta-'+datetime.datetime.now().strftime('%Y%m%dT%H%M%S')); dst.mkdir(parents=True,exist_ok=True);
items=[('active.json','active.json'),('registry.json','registry.json'),('slots/slot-A/meta.json','slot-A.meta.json'),('slots/slot-A/worktree-origin.json','slot-A.worktree-origin.json'),('slots/slot-B/meta.json','slot-B.meta.json'),('slots/slot-B/worktree-origin.json','slot-B.worktree-origin.json')]
for src,name in items:
 s=body/src
 if s.exists(): shutil.copy2(s,dst/name); print('saved',name,s.stat().st_size)
print(dst)"
```

> 坑：两个槽的 `meta.json` 同名。若按原文件名 copy 到同一目录会互相覆盖，必须按槽位加前缀。

2. **用官方 prepare 入口重物化该 slot**（不要手改运行时 JSON）：

```bash
python -c "import urllib.request,json; body=json.dumps({'clear_existing':False,'source_path':r'F:\My_code\VScode_py\VoidCube'}).encode();
req=urllib.request.Request('http://localhost:6002/executor/body/slots/slot-A/prepare',data=body,headers={'Content-Type':'application/json'},method='POST');
r=urllib.request.urlopen(req,timeout=180); d=json.loads(r.read().decode('utf-8','replace')); s=d.get('slot',{}); print(r.status); print({k:s.get(k) for k in ['candidate_commit','source_commit','active_commit']})"
```

> **关键坑（本轮实际踩到）**：对 **active slot** 不传 `source_path` 会 HTTP 500，服务端日志报
> `RuntimeError: Slot workspace preparation failed and rollback was incomplete: failed to restore the worktree: A non-active source_slot_id or source_path is required for materialization.`
> 这是 `body_registry._resolve_materialization_source` 的护栏：active slot 必须显式指定物化源。**必须传 `source_path`** 指向 canonical 仓库根。首次失败会触发回滚，工作树与 meta 通常保持原状，但别依赖它，直接一次就传全参数。

> 另一个坑：`clear_existing` 默认 `True`，会**清空 slot 的 runtime 目录**（`cache/`、`state/`、`slot-runtime.json`）。想保留运行时状态就传 `clear_existing: false`。

3. **验证**：

```bash
python -c "import urllib.request,json; d=json.load(urllib.request.urlopen('http://localhost:6002/health',timeout=15)); print(d.get('status'), json.dumps(d.get('body_runtime'),ensure_ascii=False))"
```

期望 `status=healthy`、`body_runtime.healthy=true`、`violations=[]`。

4. **重启服务：先确认命令真的执行了，再判断 pid 含义**（两个独立的坑，别混淆）：

> **坑 A — 命令被去重，根本没执行。** 若本轮已跑过完全相同的 `serve stop && serve start`，
> terminal 会返回 `duplicate_or_in_flight_action`（可能带 `state=succeeded`），但**命令并未执行**：
> PID 完全不变、`/health` 依然返回 healthy，极易误判成“已重启、新代码已加载”。
> 规避：把 `.venv/Scripts/vc.exe serve stop` 与 `.venv/Scripts/vc.exe serve start` 拆成**两条独立命令**执行，
> 并以 `~/.VoidCube/run/*.pid` 的数值是否变化作为重启成功的判据。健康端点不校验代码版本，
> `healthy` **不能**证明新代码已加载。

> **坑 B — `already running (pid N)` 里的 N 可能是本次刚拉起的新进程（PID 复用）**，
> 不是残留旧进程，不要据此判定“新代码没加载”。判据是下面查到的 `CreationDate`。

核实方式是看进程启动时间（Windows 下 `wmic` 可能不存在，用 PowerShell）：

```bash
# 坑：终端 harness 会把双引号命令里的 $_ 替换成工作目录，直接内联 PowerShell 会报语法错。
# 正确做法：用 write_file 写入脚本文件再执行。
#   Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' } |
#     Select-Object ProcessId, CreationDate, CommandLine | Format-List
powershell -NoProfile -ExecutionPolicy Bypass -File .test-tmp/procs.ps1
```

若四个服务进程的 `CreationDate` 都是刚才那次 `serve start` 的时间，说明新代码已加载，无需再杀进程。

### 语义说明（别误判为缺陷）

- prepare 的 active-slot 分支只更新 `candidate_commit`/`source_commit`；`active_commit`/`current_healthy_commit` **保持不变**，这是设计行为，不要手工改。
- mismatch 判定取 `meta.candidate_commit or meta.active_commit or meta.current_healthy_commit`，所以修好 candidate_commit 即可消解 violation。
- 检测相关代码：`src/voidcube/systems/body_registry.py`（`inspect_layout` 的 violation 生成、`_prepare_slot_workspace` 的元数据更新分支）、`src/voidcube/systems/execution/adapters.py::prepare_body_slot`（HTTP → registry 的入参映射）。

## 预防建议

1. **定期检查服务状态**: 使用 `voidcube status` 命令
2. **保持 PATH 正确**: 确保 Python 3.14+ 在 PATH 中优先
3. **避免强制关机**: 可能导致 git 文件锁定
4. **备份配置**: 定期备份 `~/.VoidCube/config.yaml`

## 相关文件

- 配置文件: `~/.VoidCube/config.yaml`
- PID 文件: `~/.VoidCube/run/*.pid`
- 日志文件: `~/.VoidCube/run/supervisor.log`
- 内存数据: `~/.VoidCube/run/memory-state.json`
- Slot 配置: `~/.VoidCube/runtime/body/slots/slot-A/meta.json`

## 快速修复脚本

```bash
#!/bin/bash
# 保存为 fix-voidcube.sh

echo "清理残留进程..."
ps aux | grep -E "voidcube|supervisor|gateway" | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null

echo "清理 PID 文件..."
rm -f ~/.VoidCube/run/*.pid

echo "启动服务..."
cd /f/My_code/VScode_py/VoidCube
./venv/Scripts/voidcube.exe serve start

echo "检查状态..."
./venv/Scripts/voidcube.exe serve status
```

## 参考

- VoidCube 架构: `devops/voidcube-architecture`
- Supervisor 调试: `devops/supervisor-media-debug`