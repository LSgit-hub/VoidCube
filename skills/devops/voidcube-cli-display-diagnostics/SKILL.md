---
name: voidcube-cli-display-diagnostics
description: 排查 VoidCube CLI/桌面版的显示控制命令与 emoji 渲染异常。当用户问"哪个命令开关思考内容/工具信息显示"、"emoji/符号显示不正常"、"输入栏错位/乱码"时使用。
---

# VoidCube CLI 显示控制与 emoji 渲染诊断

## 触发条件
- 用户问 CLI 里哪个命令控制思考内容、工具调用、上下文状态栏的显示开关
- 用户报告输入栏 / 桌面版中 emoji、特殊符号显示不正常（错位、截断、方框、乱码）
- 需要区分 `/reasoning`、`/verbose`、`/statusbar` 等易混命令

## 一、显示控制命令速查（代码位置仅供参考，以实际为准）

三个易混命令，功能完全不同：

| 命令 | 控制内容 | 取值 |
|------|---------|------|
| `/reasoning` | 模型思考过程(think 内容)显示 + 推理努力等级 | show/hide/on/off, none/minimal/low/medium/high/xhigh |
| `/verbose` | 工具调用/命令回显进度显示 | 循环档位: off → new → all → verbose → off |
| `/statusbar` | 底部上下文/模型状态栏 | 开关 |

关键实现位置：
- reasoning 命令：`src/voidcube/interfaces/cli/commands/handlers/reasoning.py`
  - `show`/`on` 开思考显示并持久化；`hide`/`off` 关；空参数显示当前状态
- verbose 命令：`src/voidcube/interfaces/cli/application.py` 的 `_toggle_verbose()`
  - 循环 `["off", "new", "all", "verbose"]`，verbose 档会开 DEBUG 日志
  - off 档 = 静默模式，只显示最终回答
- 命令目录：`src/voidcube/interfaces/cli/commands/catalog.py`（CommandDef 列表，COMMAND_REGISTRY 从第 84 行开始）
  - 2026-08-27 实测行号：`reasoning` ≈ 145-151，`statusbar` ≈ 155-156，`verbose` ≈ 157-165
  - 注意：search_files 内容搜索对该文件会返回 0 匹配（`CommandDef\(`、命令名等明确存在的模式也搜不到），项目根目录搜索可能超时；核实命令定义直接用 read_file 读 COMMAND_REGISTRY 区块，别用内容搜索

## 二、emoji 显示异常诊断（核心经验）

### 根因 1：wcwidth 版本过旧（影响 CLI 输入栏）
prompt_toolkit 用 `get_cwidth()` 计算字符宽度，控制光标定位、换行、输入框高度。

**关键机制（实测确认）**：prompt_toolkit 从 `wcwidth` 包导入的是**单字符函数** `wcwidth`（`from wcwidth import wcwidth`），并且 `get_cwidth` 对多字符字符串是**逐字符求和**（`sum(self[c] for c in string)`，见 `prompt_toolkit/utils.py` 的 `_CharSizesCache.__missing__`）。所以：
- ZWJ 序列 👨👩👧 的 `get_cwidth` 恒为 6（2+0+2+0+2），升级 wcwidth **无法**改变——这是 prompt_toolkit 固有设计，两版本一致
- 真正随版本变化的是单字符宽度表

**0.8.2 vs 0.2.14 实测差异**（逐字符 `wcwidth`）：
- 😀👍🚀👨👩👧❤ 等：两版本都是 2 / 1，无差异
- **区域指示符 `🇨`（U+1F1E8）：0.8.2 = 2，0.2.14 = 1** ← 唯一实际差异
- 因此国旗 🇨🇳：0.8.2 算 4，0.2.14 算 2（正确）

**验证方法**（用单字符 `wcwidth`，不是 `wcswidth`）：
```python
from wcwidth import wcwidth
print(wcwidth('🇨'))   # 0.8.2=2, 0.2.14=1
```

**修复**（注意 pip 版本坑）：
```bash
pip install -U wcwidth   # ❌ 无效！pip 按版本号比较认为 0.8.2 已是最新（0.8.2 > 0.2.14）
pip install "wcwidth==0.2.14"   # ✅ 必须显式指定版本号
```
升级后 prompt_toolkit 的 `get_cwidth('🇨🇳')` 从 4 修正为 2。

### 根因 2：桌面版字体回退缺失（影响 desktop/xterm.js）
桌面版 `desktop/src/renderer/src/main.ts` 用 xterm.js + node-pty 渲染：
```js
fontFamily: '"Cascadia Code", "JetBrains Mono", "SFMono-Regular", Consolas, monospace'
```
编程等宽字体不含 emoji 字形，浏览器回退到系统 emoji 字体后宽度与 xterm.js 按等宽测量的不一致 → 方框/错位。

**修复**：fontFamily 末尾追加 emoji 回退字体（已实测构建通过）：
```js
fontFamily: '"Cascadia Code", "JetBrains Mono", "SFMono-Regular", Consolas, "Segoe UI Emoji", "Apple Color Emoji", monospace'
```
emoji 字体要放在 Consolas 之后、`monospace` 之前。改完跑 `npm run typecheck && npm run build`，并 grep 构建产物确认字体串已进入（`grep -o "Segoe UI Emoji" out/renderer/assets/*.js`）。

### 根因 3：Windows PTY 编码链路（桌面版 emoji 变乱码）
node-pty → xterm.js 字节流编码不对齐时 UTF-8 多字节字符被截断。检查 desktop 侧 env 是否含 `PYTHONUTF8=1`（正常应已设置）。

### 根因 4：输入路径代理对被拆散 → "两个问号"（��）（2026-08-27 实测确认）
用户报告"输入 emoji 显示为俩个问号"，如"麦克风🎤"显示成"麦克风��"。

**机制**：`��` = 两个 U+FFFD 替换字符。emoji 是 astral 字符，UTF-16 下是代理对：
```
🎤 = U+1F3A4 = 代理对 D83C DFA4
孤立高代理 D83C 单独 UTF-8 编码 → U+FFFD（一个 �）
孤立低代理 DFA4 单独 UTF-8 编码 → U+FFFD（一个 �）
```
代理对被拆散后各自编码就变成两个 �。node 实测确认：
```js
Buffer.from(String.fromCharCode(0xD83C), 'utf8').toString('utf8') // "\uFFFD"
```

**输入链路**：Electron keydown → `evaluateKeyboardEvent`（`desktop/node_modules/@xterm/xterm/src/common/input/Keyboard.ts`）→ node-pty → Python stdin。
- `evaluateKeyboardEvent` 对非功能键走 default 分支，主要用 `ev.keyCode` 构建字符（`String.fromCharCode(ev.keyCode)`）
- emoji 的 keyCode = 0（非 BMP），必须靠 `ev.key` 完整字符串或 IME composition 事件才能拿到完整代理对
- 任一环节只取了代理对一半（如只取 charCode 的 BMP 部分），传到 Python 就是两个孤立代理 → 两个 �

**修复方向**：
1. 检查 `desktop/src/renderer/src/main.ts` 的 onKey/onData 挂接，确保非 BMP 字符用 `ev.key` 完整字符串传给 pty.write，而不是 keyCode
2. 防御性复健：pty.write 前检测孤立代理对并重组（遍历 charCodeAt，遇 D800-DBFF 检查下一位是否 DC00-DFFF）
3. node-pty spawn env 需含 `PYTHONUTF8=1`（Python 侧编码才不是问题）

### 根因 5：xterm.js 宽度表过旧（UnicodeV6 → Unicode11 addon）
xterm 6.0.0 默认 UnicodeV6（Unicode 6.0 宽度表），对新字符宽度判定落后。修复：装 `@xterm/addon-unicode11@0.9.0`，在 main.ts 加载并切换：
```js
term.loadAddon(new Unicode11Addon());
term.unicode.activeVersion = '11';
```
**重要实测结论**：该版本 xterm 对 astral emoji（🎤😀🇨 等）在 V6 和 V11 下 buffer 宽度都是 w2（`unicodeService.wcwidth(0x1F3A4)=2`），两版无差异——所以"只显示一半"对普通 emoji 不是宽度表问题，更可能是字体回退（根因 2）或 VS16 变体/ZWJ 的 join 逻辑。Unicode11 主要修正较新的 BMP 窄字符/符号，别指望它单独解决 emoji 半截。

**验证技巧（内部 API，公开接口没有）**：
- `term.unicode.wcwidth()` 不存在；`term.unicode.versions` 是索引对象（key 为 '0','1'），不是 provider map
- 正确姿势：`term._core.unicodeService.wcwidth(0x1F3A4)` / `.charProperties(cp, preceding)` + `UnicodeService.extractWidth(props)`
- buffer 实测：`term.buffer.active.getLine(0).getCell(i).getWidth()` / `.getChars()`
- **src 与 lib 不一致坑**：`node_modules/@xterm/xterm/src/common/input/UnicodeV6.ts` 源码对 0x1F3A4 逻辑上应返回 1，但运行时返回 2（lib/xterm.js 是压缩产物且与 src 不同步）——以运行时实测为准，别信 src 源码推断

## 三、验证步骤
1. 先确认环境版本：`pip show wcwidth`、检查 xterm.js `package.json` version
2. 跑上面的 wcwidth 测试脚本对比 0.8.2 vs 0.2.14
3. 输入栏修复后输入 👨👩👧❤️1️⃣🇨🇳 实测光标与显示
4. 桌面版改字体后用彩色 emoji 实测

## 陷阱
- `wcwidth` 0.8.x 并不是比 0.2.x 新！0.8.2 是 2018 年版本，0.2.14 才是 2024 新版本（版本号跳变，易误判）；因此 `pip install -U wcwidth` 是 no-op，必须 `pip install "wcwidth==0.2.14"`
- 验证必须用 prompt_toolkit 实际使用的**单字符 `wcwidth`**（逐字符求和），不要用 `wcswidth`——后者两版本对序列都返回正确值，会得出"无需升级"的错误结论
- ZWJ 序列（👨👩👧）在 `get_cwidth` 下恒为 6，这是 prompt_toolkit 固有行为，升级 wcwidth 解决不了；实测影响有限（终端按 glyph 渲染），勿在此过度投入
- 简单 emoji（😀👍🚀）两个版本都算 2，测试需用旗帜 `🇨` 这类能暴露 0.8.2/0.2.14 差异的字符
- 输入栏和桌面版是两套渲染体系，问题根因不互通，需分别诊断
- "两个问号"（��）不是显示问题而是输入路径编码问题：emoji 代理对被拆散后各自变 U+FFFD；排查方向是 xterm 键盘事件/IME composition 是否把完整代理对传给 pty.write，而非查字体或 wcwidth
- 诊断 xterm 宽度用内部 `term._core.unicodeService`，公开 `term.unicode` 没有 wcwidth；`term.unicode.versions` 的 key 是 '0'/'1' 这种索引，不是版本号
- xterm npm 包的 src/ 源码与运行时 lib/ 行为可能不一致（src 说 return 1，实测 return 2），一切以运行时 buffer 实测为准
- `@xterm/addon-unicode11` 对 astral emoji（🎤😀）的宽度与 V6 相同（都是 2），升级它解决不了 emoji 半截；它修的是其他新 Unicode 字符
- 修 main.ts 输入路径/字体后要 `npm run typecheck && npm run build` 并用 grep 构建产物验证改动确实进入（`grep -o "unicode11\|Segoe UI Emoji" out/renderer/assets/*.js`）
- `get_cwidth` 有缓存（`_CHAR_SIZES_CACHE`），改 wcwidth 后需重启 CLI 生效
- patch 工具对 main.ts 的 lint 可能误报 `voidcubeDesktop` 类型 / NodeListOf 错误——以 `npm run typecheck`（exit 0）为准，勿被误导
