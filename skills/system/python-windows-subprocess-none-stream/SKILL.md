---
name: python-windows-subprocess-none-stream
description: 诊断和修复 Windows 上 subprocess.run(text=True) 因本地编码解码失败导致 stdout/stderr 变成 None 的静默缺陷——典型症状是 AttributeError 'NoneType' object has no attribute 'strip'，同时子进程真实输出丢失。当日志出现该 AttributeError、或看到 "Unexpected git error running ..." / 子进程输出莫名为空时使用。
category: system
created: 2026-09-10
updated: 2026-09-10
tags: [python, windows, subprocess, encoding, diagnosis]
---

# Windows 下 subprocess text 模式产生的 None 流

## 触发条件

- 日志出现 `AttributeError: 'NoneType' object has no attribute 'strip'`，栈顶指向 `result.stdout.strip()` / `result.stderr.strip()`
- 外层日志形如 `Unexpected git error running git add -A: 'NoneType' object has no attribute 'strip'`
- 子进程输出「莫名是空的」，但子进程本身返回 rc=0
- 运行在 Windows + 中文区域（`locale.getpreferredencoding(False)` 为 `cp936`）

## 根因（实测机制）

`subprocess.run(..., capture_output=True, text=True)` 未显式指定 `encoding` 时，**使用进程本地编码**（Windows 中文环境 = cp936）。当子进程写出的字节不是合法本地编码时：

- 解码异常发生在内部 `_readerthread` 线程里，由 threading 的 excepthook 打印后**被吞掉**；
- `subprocess.run()` **仍然正常返回**（rc 可能是 0）；
- 但**对应的流变成 `None`**。

实测确认（本轮用子进程写 `b"\x80\xff\xfe\x81"` 验证）：

```
stderr 写非法字节 -> rc 0 | stdout '' | stderr None
stdout 写非法字节 -> rc 0 | stdout None | stderr ''
```

因此这是**静默降级**：调用方拿到 None 而不是异常，真实错误文本丢失。

在 VoidCube 的实际触发点：`checkpoint_manager._run_git` 执行 `git add -A`，git 把诊断写进 stderr（如 `error: short read while indexing nul` / `unable to index file 'nul'` / `fatal: adding files failed`），其中含非 cp936 字节 → `stderr=None` → `.strip()` 崩溃 → checkpoint 写入失败，且真实 git 错误被 AttributeError 顶掉。

## 复现配方（先证实机制，再改代码）

```bash
# 1) 看本地编码
python -c "import locale; print(locale.getpreferredencoding(False))"

# 2) 子进程写非法字节
python -c "
import pathlib
pathlib.Path('probe_child.py').write_text(
    'import sys\n'
    'target=sys.argv[1]\n'
    'payload=b\"\\x80\\xff\\xfe\\x81\"\n'
    's=sys.stderr if target==\"stderr\" else sys.stdout\n'
    's.buffer.write(payload); s.buffer.flush()\n', encoding='utf-8')

# 3) 观察两种流的 None 行为
python -c "
import subprocess,sys
for t in ['stderr','stdout']:
    r=subprocess.run([sys.executable,'probe_child.py',t],capture_output=True,text=True)
    print(t,'rc',r.returncode,'stdout',repr(r.stdout),'stderr',repr(r.stderr))"
```

预期看到 `Exception in thread Thread-N (_readerthread) ... UnicodeDecodeError: 'gbk' codec can't decode ...`，且对应流为 `None`。

注意：直接用 `python -c "... b'\x80' ..."` 传字节字面量会被 shell/编码二次破坏（本轮踩过），**务必写成子进程脚本文件**。

### 更推荐：确定性复现（不依赖本机 locale）

上面的配方假设本机是 cp936，换台机器（Linux/UTF-8 环境）就复现不了，写回归测试时会失效。
**显式传一个窄编码**即可强制触发，跨平台稳定：

```python
import subprocess, sys

CHILD = [sys.executable, "-c",
         "import sys; sys.stderr.buffer.write(bytes([0x80, 0xFF, 0xFE])); "
         "sys.stderr.buffer.flush()"]

# 窄编码 + 无 errors -> 流变 None（复现缺陷）
assert subprocess.run(CHILD, capture_output=True, text=True,
                      encoding="cp936").stderr is None

# errors="replace" -> 永远是 str（契约）
r = subprocess.run(CHILD, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
assert isinstance(r.stdout, str) and isinstance(r.stderr, str)
```

`bytes([...])` 写在 `-c` 里是纯 ASCII，不会被 shell 二次编码破坏 —— 比写临时脚本文件更省事。

副作用：读取线程的 `UnicodeDecodeError` traceback 会打到**父进程 stderr**，测试输出会有噪声，
属正常现象；不要试图用 capsys 断言它，也不代表测试失败。

## 修复（⚠️ 2026-09-10 修正：不要一律强制 utf-8）

崩溃安全的**必要不变量是 `errors=`**（让解码永不失败），**不是**某个具体 encoding。
按输出用途分两类，混用会制造新的回归：

```python
# A) git / 诊断路径：输出本就是 UTF-8，指定 utf-8 正确
result = subprocess.run(
    cmd, capture_output=True, text=True,
    encoding="utf-8", errors="replace",
    timeout=timeout, env=env, cwd=working_dir,
)

# B) 展示路径（git_display / status / clipboard / ssh / docker 等）：
#    保留 locale 编码，只加 errors —— 中文仍能正确显示
result = subprocess.run(
    cmd, capture_output=True, text=True,
    errors="replace",
    timeout=timeout, cwd=working_dir,
)

stdout = (result.stdout or "").strip()   # 防御性兜底，防 mock/包装/未来回归
stderr = (result.stderr or "").strip()
```

为什么不能一律 utf-8：中文 Windows 上 cp936 是**合法**的本地编码，很多命令（`dir`、
`wmic`、本地化工具、部分 git 输出）会写 cp936 字节。强制 utf-8 后这些字节全部变成
`U+FFFD` 替换符 —— 从"偶发崩溃"变成"中文永远显示为乱码"，是更差的回归。

`errors="replace"` 单独就能消除 None 崩溃；`encoding` 只在**确认输出是 UTF-8** 时才加。
另：`or ""` 不能单独收工，它只掩盖症状、真实错误文本仍会丢失，必须配合 `errors=`。

### 先找既有约定，别自己发明

动手前先 grep 同类调用是否已有统一写法。VoidCube 实测 `body_registry._run_git` **早就**
带 `encoding="utf-8", errors="replace"`，说明仓库既有约定就是"两个都写"，漏网的是
`checkpoint_manager` / `terminal_tool`。沿用既有约定比自创方案更安全。

## 批量收敛（AST 定位 + 精确插入）

83 处 / 26 文件需要改时，不要手改也不要整文件重写。用 AST 定位**到行**再文本插入：

```python
import ast, pathlib
for p in pathlib.Path("src/voidcube").rglob("*.py"):
    src = p.read_text(encoding="utf-8")
    tree = ast.parse(src)
    targets = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call): continue
        f = n.func
        if not (isinstance(f, ast.Attribute) and f.attr == "run"): continue
        kw = {k.arg: k.value for k in n.keywords if k.arg}
        t = kw.get("text")
        # 只看字面 text=True 且缺 errors 的调用
        if isinstance(t, ast.Constant) and t.value is True and "errors" not in kw:
            targets.add(t.lineno)          # 取 kwarg 值节点的行号，不是调用起始行
    if not targets: continue
    lines = src.splitlines()
    for ln in sorted(targets):
        if "text=True" in lines[ln - 1]:
            lines[ln - 1] = lines[ln - 1].replace(
                "text=True", 'text=True, errors="replace"', 1)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
```

要点：
- 用 **kwarg 值节点的 `lineno`**（`text` 对应的 `True` 所在行），不是 `n.lineno`；否则多行调用会插错位置。
- 同行内联写法（`capture_output=True, text=True, timeout=3,`）用 `replace(..., 1)` 直接就地插入；
  先 dry-run 统计"非 `text=True,` 单行"的数量，确认都能安全处理再落盘。
- `Path.write_text` 在 Windows 会把 `\n` 翻译成 `\r\n`，所以**不会**造成行尾churn；
  但若用 `open(..., newline="")` 写就会，注意别把 diff 变成整文件重写。
- 落盘后用同一个 AST 检查断言残留违规点 == 0。

## 防回归：全树 AST 守卫测试

比"给某个函数写单测"更有效的是把不变量固化成树级守卫：

```python
def _text_mode_run_calls_missing_errors(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call): continue
        f = n.func
        if not (isinstance(f, ast.Attribute) and f.attr == "run"): continue
        kw = {k.arg: k.value for k in n.keywords if k.arg}
        t = kw.get("text")
        if isinstance(t, ast.Constant) and t.value is True and "errors" not in kw:
            out.append(n.lineno)
    return out
# 断言 src/voidcube 下所有 text=True 调用都声明了 errors
```

守卫断言 `errors`（崩溃安全不变量）而不是 `encoding` —— 后者按路径而异，写死会误报。

## 定位影响面

```bash
# 统计所有未指定 encoding 的 text=True 调用点
rg -n "text=True" src/ | wc -l
rg -n "text=True" src/
```

高风险（捕获任意子进程输出，而非固定 ASCII 元数据）：checkpoint/git 封装、terminal 工具、
exploration/executor、host executor、安全扫描器封装。

**统计口径要看「缺 `errors` 的调用数」，不是 `text=True` 的总数。** VoidCube 实测：
`text=True` 共 112 处 / 约 40 文件，但**真正缺 `errors` 的只有 83 处 / 26 文件**——
其余已按既有约定写过。先跑 AST 统计拿到准确数字，再决定工作量，
不要被 112 这个总数吓到而只改高风险几个（那只会在下次换个入口复发）。

## 为何必须补回归测试

这个缺陷的危险在于「不报错、只是内容变 None」，普通单测（子进程只输出 ASCII）永远测不出来。新增测试要**真的让子进程写非法字节**，断言：

- 调用不抛异常；
- 返回的文本不是 None；
- 非法字节被替换而不是丢失整段输出。

## 坑

1. **别把 `stderr=None` 当成「git 没有输出」**——它可能是「输出解不出码」。两者语义完全不同，前者会让人误判成命令成功且无警告。
2. **只加 `or ""` 会掩盖症状**：不再崩，但真实错误文本仍然丢失。至少要配 `errors="replace"`。
3. **但也不要一律强制 `encoding="utf-8"`**：在中文 Windows 上会把合法的 cp936 输出变成 `U+FFFD`，
   把"偶发崩溃"换成"中文永远乱码"。只有确认输出是 UTF-8（git/诊断）才指定 utf-8。
4. **先 grep 既有约定**：同类调用可能早就统一过写法（如 VoidCube 的 `body_registry._run_git`），
   照抄既有约定，而不是自创一套。
5. **`PYTHONUTF8=1` / `PYTHONIOENCODING` 能临时绕过**，但不能作为修复——它依赖运行环境，服务子进程、定时任务、被 spawn 的子进程未必继承。
6. **用 `bytes([0x80, 0xFF])` 而不是 `b"\x80"` 字面量**：前者纯 ASCII，可安全放进 `-c`；后者会被 shell/编码二次破坏。
7. 同一类缺陷也会出现在 `text=True` 的 `check_output` / `Popen.communicate`。
8. **它是复发型缺陷，不是一次性事故**：VoidCube 的同一 AttributeError 在 4 周内出现 4 次
   （08-05、08-08、08-21、09-10）。排查时按时间切片统计历史出现次数，
   能判断"是新问题还是老大难"，并据此决定是否上树级 AST 守卫。

## 验证

```bash
.venv/Scripts/python.exe -m pytest <新增的回归测试> -q
.venv/Scripts/python.exe -m compileall -q src/voidcube
git diff --check
# 重启服务后确认目标日志不再出现该 AttributeError
```
