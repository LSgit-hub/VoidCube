---
name: voidcube-workspace-hygiene
description: VoidCube 仓库与运行时工作区卫生清理——构建产物/缓存/幽灵 pyc、残留 git worktree 注册、测试现场目录、陈旧运行时绑定记录、日志轮转、运行时技能入库。当用户说"清理残留/工程卫生/回收磁盘/worktree 太多/日志太大/整理工作区"时使用。
category: devops
created: 2026-09-10
updated: 2026-09-10
---

# VoidCube 工作区卫生清理

## 何时使用

- 用户要求「清理工程残留 / 回收磁盘 / 整理工作区 / 日志太大 / worktree 太多」
- 自检报告里出现「构建产物残留」「幽灵 pyc」「worktree 注册残留」「陈旧绑定记录」「技能库数量不一致」

## 铁律：先取证，再动手

1. **删除前必须确认是否被 git 跟踪**——本项目最容易犯的错：

```bash
git ls-files <path>
```

有输出 = 已跟踪源码，**绝对不能**当残留删除。
实例：`_task_work/` 看起来像一次性调试残留（内含 probe_*.py / verify_*.py），实际是 **13 个已跟踪源码文件**。本轮差点误删。

2. **删除前先归档**，即使是「看起来没用」的目录：

```bash
python -c "import pathlib,shutil,datetime; stamp=datetime.datetime.now().strftime('%Y%m%dT%H%M%S'); dst=pathlib.Path.home()/'.VoidCube/runtime/backups/hygiene'/('scratch-'+stamp); dst.mkdir(parents=True,exist_ok=True); shutil.copytree('<dir>', dst/'<name>'); print(dst)"
```

3. **每步之后 `git status --short`**，只允许出现预期改动。若出现预期外的 `D <path>`，立即 `git checkout -- <path>` 还原。
4. **写运行时 JSON/DB 前先备份**（slot 元数据、registry.json、memory.db 用 sqlite3 `backup()` API）。

## 盘点（只读，先出清单再删）

```bash
# 构建产物与缓存目录体积
python -c "
import pathlib,os
def sz(p):
    p=pathlib.Path(p)
    if not p.exists(): return 0
    t=0
    for r,d,fs in os.walk(p):
        for f in fs:
            try: t+=os.path.getsize(os.path.join(r,f))
            except OSError: pass
    return t
for r in ['build','dist','voidcube_agent.egg-info','Mem/build','Mem/dist-mem','Mem/src/memai.egg-info','.test-tmp','.tmp-auto-check','.tmp-auto-check2','.local_state','.pytest_cache','.ruff_cache']:
    print(f'{r:32s}', round(sz(r)/1048576,2),'MB')"

# 幽灵 pyc（.py 已不存在的 .pyc）与 __pycache__ 统计
python -c "
import pathlib,subprocess
root=pathlib.Path('.')
tracked=set(subprocess.check_output(['git','ls-files','*.pyc'],text=True).splitlines())
g=[];tot=0
for f in root.rglob('*.pyc'):
    if {'.venv','node_modules','.git'} & set(f.parts): continue
    try:
        if not f.with_suffix('.py').exists(): g.append(f); tot+=f.stat().st_size
    except OSError: pass
print('tracked pyc',len(tracked),'ghost',len(g),'MB',round(tot/1048576,3))"

# worktree 注册（找出残留：路径存在于临时目录下的都是）
git worktree list

# 技能库差异
python -c "
import pathlib
def n(p): return {f.parent.name for f in pathlib.Path(p).rglob('SKILL.md')}
u,r=n(pathlib.Path.home()/'.VoidCube/skills'),n('skills')
print('runtime',len(u),'repo',len(r)); print('runtime-only',sorted(u-r)); print('repo-only',sorted(r-u))"

# 活跃日志体积
python -c "import pathlib; [print(f.name, round(f.stat().st_size/1048576,2),'MB') for f in sorted((pathlib.Path.home()/'.VoidCube/run').glob('*.log'))]"
```

## 执行顺序（A → D → E → B+F → C）

先做可重建项，最后做需要停服/抢时间窗的项。

### A 构建产物与缓存（零风险，可直接删）

```bash
python -c "
import shutil,pathlib,os
root=pathlib.Path('.'); freed=0
def sz(p):
    p=pathlib.Path(p); t=0
    for r,d,fs in os.walk(p):
        for f in fs:
            try: t+=os.path.getsize(os.path.join(r,f))
            except OSError: pass
    return t if p.exists() else 0
for t in ['build','dist','voidcube_agent.egg-info','Mem/build','Mem/dist-mem','Mem/src/memai.egg-info','.pytest_cache','.ruff_cache']:
    p=root/t
    if p.exists(): s=sz(p); freed+=s; shutil.rmtree(p,ignore_errors=True); print('removed',t,round(s/1048576,2),'MB')
n=0
for d in list(root.rglob('__pycache__')):
    if {'.venv','node_modules','.git'} & set(d.parts): continue
    freed+=sz(d); shutil.rmtree(d,ignore_errors=True); n+=1
print('__pycache__ removed',n,'total MB',round(freed/1048576,2))"
```

实测：本轮回收 73.2 MB，幽灵 pyc 从 3195 个 / 60.64 MB 归零。

### D 陈旧运行时绑定记录

先判定「有没有读取方」，再决定删：

```bash
# 全仓库搜文件名与键名；src/ 零命中 = 无读取方，属退役特性残留
# 例：mem-editable-binding.json / mem_editable_binding（实际只被一个测试断言"不应出现"）
```

```bash
python -c "
import pathlib,json,shutil,datetime
b=pathlib.Path.home()/'.VoidCube/runtime/body'
stamp=datetime.datetime.now().strftime('%Y%m%dT%H%M%S')
bak=b/'backups'/('body-meta-'+stamp); bak.mkdir(parents=True,exist_ok=True)
for src,name in [('registry.json','registry.json'),('mem-editable-binding.json','mem-editable-binding.json'),('active.json','active.json')]:
    p=b/src
    if p.exists(): shutil.copy2(p,bak/name); print('backed up',name)
dead=b/'mem-editable-binding.json'
if dead.exists(): dead.unlink(); print('deleted',dead)
reg_path=b/'registry.json'
reg=json.loads(reg_path.read_text(encoding='utf-8'))
lsr=reg.get('last_switch_result') or {}
lsr.pop('mem_editable_binding',None); reg['last_switch_result']=lsr
reg_path.write_text(json.dumps(reg,ensure_ascii=False,indent=2),encoding='utf-8'); print('key removed')"
```

删除后立即验证 `GET http://localhost:6002/health` 仍 `body_runtime.healthy=true`、`violations=[]`。

### E 技能治理

- **运行时 → 仓库入库**：`shutil.copytree` 到 `skills/<category>/<name>/`（运行时技能通常已在 `devops/` 分类下，保持相对路径）。
- **移除 deprecated**：删掉含 `deprecated: true` 的运行时技能目录。
- **必跑自检**（仓库硬性规则）：`pytest tests/test_skill_registry.py`，并核对变更检测统计（added/reparsed/reused/removed）符合预期。
- 注意：运行时技能数会因后台技能审查（agent 自建技能）在你操作期间变化，别用总数当判据，用**集合差**。

### B + F 测试现场与 worktree 注册（必须成对做）

**顺序不能颠倒**，否则会留下失效注册：

```bash
python -c "
import subprocess
wts=[r'<each stale worktree path>']
for w in wts:
    r=subprocess.run(['git','worktree','remove','--force',w],capture_output=True,text=True,encoding='utf-8',errors='replace')
    print('rc=%d'%r.returncode, (r.stdout or r.stderr).strip()[:110] or 'ok')"
```

```bash
# 再删目录
# 最后
git worktree prune
git worktree list     # 只应剩：主仓库 + active slot-A worktree（+ 有意保留的分支 worktree）
```

动手前确认无进程占用：枚举进程命令行是否包含目标目录（注意别把自己这条 grep 命令算进去）。

### C 日志轮转（要抢在看门狗前完成）

看门狗会在 `serve stop` 后 1–2 秒内自动拉起服务并重开日志文件，因此**不能在 stop 与 rotate 之间插入 sleep**。必须用单条脚本一气呵成：

```bash
python -c "
import subprocess,pathlib,datetime
vc=r'.venv\Scripts\vc.exe'
run=pathlib.Path.home()/'.VoidCube/run'
arch=run/'archive'; arch.mkdir(parents=True,exist_ok=True)
stamp=datetime.datetime.now().strftime('%Y%m%dT%H%M%S')
subprocess.run([vc,'serve','stop'],capture_output=True,text=True)
moved=[];failed=[]
for n in ['gateway.log','supervisor.log','memory.log']:
    src=run/n
    if not src.exists(): continue
    try:
        mb=round(src.stat().st_size/1048576,2)
        src.rename(arch/(n[:-4]+'-'+stamp+'.log')); moved.append((n,mb))
    except Exception as e:
        failed.append((n,type(e).__name__,str(e)[:70]))
print('rotated:',moved); print('failed:',failed)
subprocess.run([vc,'serve','start'],capture_output=True,text=True)
for stem in ['gateway','supervisor','memory']:
    for p in sorted(arch.glob(stem+'-*.log'),key=lambda x:x.stat().st_mtime,reverse=True)[3:]:
        p.unlink(); print('pruned',p.name)"
```

若被看门狗抢先占用文件，rename 抛异常 → 日志原样保留，**不会丢数据**；跳过即可，不要反复重试。

## 坑（本轮实际踩到）

1. **Windows 句柄占用导致目录删不掉**：`shutil.rmtree` 静默跳过、`rmdir /s /q`、`attrib`、`icacls`、改名、`robocopy /MIR` 全部报 `[WinError 5] 拒绝访问`，连读取 ACL 都被拒（`icacls <dir>` 返回 "Access is denied"）。这是**某进程把该目录当作 CWD 或持有句柄**的特征。
   → 不要死磕：记录残留体积与条目数，等宿主/相关进程重启后再删。本轮 `.test-tmp` 从 110.22 MB 降到 4.09 MB 后停手。
2. **terminal 相同命令会被去重**：重复执行完全相同的命令会返回 `duplicate_or_in_flight_action`（可能带 `state=succeeded`），**命令并未执行**。服务重启必须拆成两条独立命令，并以 `~/.VoidCube/run/*.pid` 数值变化确认。
3. **轮转 ≠ 释放磁盘**：日志移到 `run/archive/` 只是切分，空间仍被占用；真正释放要靠归档淘汰策略。报告时必须说清，别宣称"回收了 309 MB"。
4. **别用终端里的 PowerShell 内联 `$_`**：harness 会把 `$_` 替换成工作目录，导致语法错误。改用 `write_file` 写 `.ps1` 再 `powershell -File`。
5. **`python -c` 里 rglob 整个仓库输出会爆炸**（本轮打印了几万行 SKILL.md）。先限定目录，或先只统计数量。
6. **`git status` 突然变干净 ≠ 改动丢失**：先怀疑"被别的进程/agent 提交了"，不要当成工作丢失而慌乱重做。
   本轮实测：28 个未提交改动 + 新增技能被一个 agent 驱动的 `git add -A && git commit` 扫进了
   `7b655e3`，且提交信息只提"新增技能"。

```bash
git log --oneline -6
git show --stat --format="%H%n%an <%ae>%n%ci%n%s" HEAD   # 核对是否真的包含你的改动
# 逐文件确认改动仍在（标记串比 mtime 可靠）
python -c "
import pathlib
for f,needle in {'<file>':'<改动标记串>'}.items():
    p=pathlib.Path(f); print(f, p.exists() and needle in p.read_text(encoding='utf-8',errors='replace'))"
```

   **提交纪律（避免自己制造这种提交）**：agent 提交一律路径限定
   （`git add -- <paths>`），不要裸 `git add -A`；提交信息必须描述**实际**暂存内容，
   否则后续按提交信息 review 会整批漏掉产品代码改动。

## 判定「既有失败」而不是自己改坏的

回归出现红用例时，先证伪，别急着改自己的代码：

```bash
git status --porcelain -- <可疑文件>     # 空 = 文件未被改动
python -c "import pathlib; print(pathlib.Path('<文件>').read_bytes()[:6])"   # 看原始首字节
```

实例：`web/supervisor.html` 在 git 中未修改，但提交内容自带 UTF-8 BOM（`EF BB BF`），而 `ui_assets.py` 用 `utf-8` 解码 → `UI_HTML` 以 `\ufeff` 开头，断言 `startswith("<!doctype html>")` 失败。属**既有失败**。
修法优先选**更稳健的一侧**：改 `payload.decode("utf-8-sig")`（BOM 容错），而不是只删掉那个文件的 BOM。

## 验证清单

```bash
git status --short        # 只允许预期改动
git diff --stat
.venv/Scripts/python.exe scripts/python_architecture.py    # 八项 gate 全 ok
.venv/Scripts/python.exe -m compileall -q src/voidcube plugins scripts Mem/src/memai
git diff --check
.venv/Scripts/python.exe -m pytest <相关测试> -q
```

服务健康（四项都要看）：

```bash
python -c "import urllib.request,json
for n,u in [('gateway','http://localhost:6000/'),('memory','http://localhost:6001/health'),('supervisor','http://localhost:6002/health'),('goal_manager','http://localhost:6003/')]:
    d=json.load(urllib.request.urlopen(u,timeout=20)); print(n,'->',d.get('status'))"
```

## 相关技能

- 槽位元数据/重启相关坑：`devops/voidcube-supervisor-fix`
- 全栈健康自检：`devops/system-health-audit`
- 技能注册表机制：`devops/skills-system-diagnostics`

## `~/.VoidCube/run/archive/` 的淘汰策略（实测 2026-09-10）

- 活跃日志由 `RotatingFileHandler` 自我约束：`logging.max_log_size`（config 默认 10，代码兜底 5 MB）
  × `logging.backup_count`（默认 3），**不会无限增长**。
- `run/archive/` 只在**手动轮转**（`vc serve stop` → 改名 → `vc serve start`）时增长，没有自动淘汰，
  所以淘汰策略要靠这个流程本身保证：
  1. 保留最新 3 份（每服务各一份）
  2. 其余 gzip 压缩为 `.log.gz`（数据保留，实测压缩比约 20:1）
  3. 超过 30 天的 `.log.gz` 删除
- 实测：3 份活跃日志 308.74 MB → gzip 后 15.38 MB，回收 293.35 MB。
- 压缩写法坑：`shutil.copyfileobj` 的参数名是 `length` 而非 `chunk_size`（Python 3.14 直接 TypeError）；
  稳妥写法是按块 `read(1MB)` 再 `write`，并在失败时先清理半成品 `.gz`。

## `.test-tmp/` 删不掉：是所有权问题，不是"句柄占用"（实测）

- 现象：`shutil.rmtree` 报 `PermissionError [WinError 5] 拒绝访问`，`icacls` 也被拒，
  但 `psutil` 查不到任何进程的 cwd / open_files 命中。
- 真因：该目录树**不属于当前登录用户**——`takeown` 明确返回
  `The current logged on user does not have ownership privileges on the file (or folder)`。
  常见来源：以其他账户/服务身份运行的进程创建（本机是外部 agent / pytest 进程）。
- 处置顺序（先诊断再动手）：
  ```bash
  takeown /f <dir> /r /d y
  icacls <dir> /grant %USERNAME%:F /t /c
  rmdir /s /q <dir>
  ```
  第 1 步报"无所有权"时，必须在**管理员** shell 里执行，普通提权无效。
- 影响面：仓库 `.gitignore` 已含 `.test-tmp/`，残留不影响提交、打包与运行；可留待重启/提权后处理。
- 判据提醒：`psutil` 无命中**不能**得出"无进程占用"的结论——所有权/ACL 同样会产生 WinError 5。

## 自审补充：三个易漏点（2026-09-10 复查实测）

1. **"工作区干净"是瞬时结论**：外部进程会在你提交之后继续改仓库文件。判据是把技能文件 mtime 与
   `git log -1 --format=%ci` 对比（实测：提交 `21:56:12` 之后，`21:57:09` 有外部写入者改了
   仓库+运行时+manifest 却**不提交**）。所以收尾必须**再查一次** `git status --short`。
2. **构建 wheel 会重新生成构建残留**：`scripts/build_wheel.py` 会创建 `build/`、`dist/`、
   `voidcube_agent.egg-info/`，三者都被 .gitignore 忽略，所以 `git status` 看不见。
   做过打包验证后必须手动删除，否则又回到"构建产物堆积"状态。
3. **私有绝对路径泄漏扫描**：入库/回填技能前扫用户目录特征（`C:\Users\<用户名>`、
   仓库所在盘符路径等）。实测 7 个技能文件命中，其中含
   `src = r"C:/Users/<用户名>/.VoidCube/runtime/memory/memory.db"` 这类**可直接执行**的行，
   在别的机器上会指向错误路径；建议参数化为 `~/.VoidCube` / `%USERPROFILE%\.VoidCube`。
   本技能文档本身也避免写死真实用户名，只写通用占位。
4. **别对 `skills/` 跑 `compileall`**：技能目录里的 `scripts/*.py` 会生成 `__pycache__/*.pyc`，
   被计入 bundled 目录 hash → repo 与 runtime 立刻不符（且 .gitignore 让 `git status` 看不见）。
   实测就这样自伤过一次：`compileall skills` 之后两个技能目录出现 pyc，同步基线被污染。
   要校验脚本语法，请针对单个文件或用 `python -m py_compile` 后立即删除产物。
