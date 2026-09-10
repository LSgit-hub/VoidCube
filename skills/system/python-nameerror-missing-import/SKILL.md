---
name: python-nameerror-missing-import
description: 诊断 Python 运行时的 NameError（如 `name 'json' is not defined`）——某个模块用到了标准库/第三方模块却没有 import 它。当工具在深层调用链里抛 NameError、而明显的主模块又确实有 import 时使用。核心是用 AST（而非正则）精确定位"用了某名字但没 import"的文件。
---

# 诊断 NameError: name 'X' is not defined（缺 import）

## 触发条件

- Python 运行时抛 `NameError: name 'X' is not defined`（X 通常是标准库如 `json`、`os`、`re`，或第三方模块）。
- 错误发生在工具/深层调用链内部，被外层 `except Exception as exc` 包装成 `Error executing tool '...': name 'X' is not defined` 之类的字符串。
- 你检查了报错最直接的那个模块，发现它**明明有** `import X`，于是困惑。

## 核心判断

`NameError: name 'X' is not defined` 意味着：**某个已加载模块的代码里引用了 `X`，但该模块的命名空间里没有 `X`**。两种典型来源：

1. 该模块确实忘了 `import X`（最常见）。
2. 该模块用了 `from X import a,b,c`，但代码里又直接写了 `X.xxx`（部分导入，没导入模块名本身）。

由于工具执行路径会动态 import 一串模块，报错的真正位置往往不在你第一眼看的主模块，而在被它间接 import 的某个文件里。

## 步骤

1. **定位错误包装点**，确认是执行阶段还是显示/格式化阶段抛的。在 VoidCube 里，`run_agent.py` 的 `f"Error executing tool '{call.name}': {exc}"` 包装的是 try 块内工具执行异常；`finally` 里的显示函数抛错则不在该包装里。读包装点上下文判断。

2. **不要用正则扫描**。正则 `\bX\s*\.` 或 `X\.\w+` 会误报 docstring、注释、字符串字面量（如 `"auth.json"` 会匹配到 `json.`）。实测会从几十个文件里筛出一堆假阳性。

3. **用 AST 精确扫描**（AST 天然排除 docstring 和注释，docstring 是字符串常量，不会被解析成 Name/Attribute 节点）。脚本见下方。

4. **区分真实模块使用 vs 局部变量名**。AST 扫描结果里，`del json, timeout`、函数参数/局部变量 `json`、mock 的 `captured.append((url, json, headers))` 都是变量名不是模块，属误报。看代码行语义排除。

5. **检查重复副本**。修复主源码后错误仍在，检查运行时实际加载的是哪个副本：
   - `build/lib/...`（打包副本，wheel/sdist 构建产物）
   - site-packages 里 `pip install` 的安装副本
   - 全盘 `find / -name '<file>.py' -path '*<pkg>*'` 找所有同路径文件，逐一确认是否也有同样的 bug。

6. **用全新解释器验证修复**（重新 import 文件），确认模块命名空间里真的有 X：
   ```bash
   .venv/bin/python -c "import sys; sys.path.insert(0,'.'); import <module> as m; print('X' in vars(sys.modules['<module>']))"
   ```

7. **注意进程缓存**。长驻进程在启动时就把模块缓存进 `sys.modules`，改文件不会热重载。如果修复后在同一会话里调用工具仍报同样的错，不是修错了，是进程缓存了旧模块——需要重启运行时（开新会话）才生效。用全新解释器复现（步骤 6）就能区分「修好了」和「进程没重载」。

## AST 精确扫描脚本

```python
import os, ast

skip_dirs = {'__pycache__', '.git', 'node_modules', '.venv'}
targets = []
for root, dirs, files in os.walk('/workspace'):
    dirs[:] = [d for d in dirs if d not in skip_dirs]
    for fn in files:
        if not fn.endswith('.py'):
            continue
        p = os.path.join(root, fn)
        try:
            src = open(p, encoding='utf-8').read()
        except Exception:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        imports_x = any(
            (isinstance(n, ast.Import) and any(a.name == 'X' for a in n.names))
            or (isinstance(n, ast.ImportFrom) and n.module == 'X')
            for n in ast.walk(tree)
        )
        if imports_x:
            continue
        uses = [(n.lineno, ast.get_source_segment(src, n)) for n in ast.walk(tree)
                if isinstance(n, ast.Name) and n.id == 'X']
        if uses:
            targets.append((p, uses))

for p, uses in targets:
    print(f"\n{p}")
    for ln, seg in uses[:8]:
        print(f"   L{ln}: {seg}")
```

把 `X` 换成实际报错的名字（如 `json`）。跑两遍：先扫主源码树，再单独扫 `.venv`/`site-packages`（第三方库里的 `json` 参数名/variable 会大量出现，需人工判断是否 VoidCube 自己的代码）。

## 坑

- 正则法误报 docstring/注释/字符串（`auth.json`、Usage 示例里的 `json.dumps`、注释里的 `json.dumps()`）。
- 误把局部变量名当模块使用（`del json, timeout`、mock 参数）。
- 只修主源码树，漏了 `build/lib/` 或安装副本——运行时可能加载的就是副本。
- 长驻进程 `sys.modules` 缓存导致「修了还报错」，误判为没修好。

## 验证

- 用全新解释器重新 import 修复文件，确认 `'X' in vars(module)` 为 True。
- 复现原调用路径，确认 NameError 消失、暴露出下一个独立错误（说明这层已修通）。
- 若涉及打包，按仓库规则用构建脚本（如 `scripts/build_wheel.py`）重新构建，而非只手动改 `build/lib` 副本。
