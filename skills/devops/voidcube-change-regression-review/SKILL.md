---
name: voidcube-change-regression-review
category: devops
description: 复查 VoidCube 最近一轮代码改动（提交或工作区）引入的回归、死代码与一致性缺口。当用户说“复查/再检查一下这轮优化有没有问题”“review your recent changes”“提交后验证”时使用。核心是跑全量 pytest 而非只跑目标用例——目标用例会漏掉被破坏的无关测试。
---

# VoidCube 最近改动回归复查

## 触发条件
- 用户：“你已经做了/进行了一轮优化改进，复查一遍看看有什么问题”
- 任一功能实现提交后需要自检
- 怀疑某提交只跑了目标用例、未跑全量

## 环境要点
- 仓库 `F:\My_code\VScode_py\VoidCube`，Python 一律用 `.venv/Scripts/python.exe`
- git 输出可能分页：命令尾部加 `| cat`
- `search_files` 在大目录会超时；目录遍历用 terminal

## 步骤 1：锁定“这轮”范围
```bash
git log --pretty=format:'%h | %ad | %s' --date=format:'%Y-%m-%d %H:%M' -12 | cat
git status --short
for c in <最近若干提交>; do git show --stat --oneline $c | cat; done
```
配合 `mem_search` / `mem_timeline` 确认用户所指的“一轮”具体是哪些提交（时间戳：mem 是 UTC，git 是本地 +08:00，差 8 小时）。

## 步骤 2：逐提交看 diff，而不是只看提交标题
`git show <commit> -- <paths> | cat`
重点识别“标题描述之外”的改动：一个“重构 X”的提交若夹带 Y/Z 文件，就是可疑点（会违背 AGENTS.md 规则 6：提交信息须描述实际改动范围）。

先确认当前状态再安排提交拆分：上一轮计划中的“未提交 P0/P1”可能已被后台流程或协作会话提交。用 `git status -sb`、`git log -12`、`git diff --stat` 和 `git diff-tree --name-status -r <commit>` 重新锁定事实；工作区干净时不要重复提交或凭旧上下文寻找不存在的未提交改动。若一个提交同时包含多个主题，但提交标题准确覆盖全部主题、测试和工作区均正常，不要为了形式强行改写历史。

## 步骤 3：符号残留 / 死代码审计（重构后必做）
对提交中被移除或改名的符号，全仓库确认无残留引用，并主动找**新产生的孤儿**：
```bash
for s in <被删/改名符号...>; do grep -rn "$s" src/ tests/ --include=*.py; done
```
- 常见新孤儿：原被私有调用方使用的公共函数（如 `detect_provider_for_model` / `search_models_dev` 在 model_switch 停止引用后即成死代码）
- 只写不读的实例属性：`grep -rn "<属性名>" . --include=*.py` 全仓库若仅一处赋值 = 死属性（本轮 `_last_memory_prefetch_error`）。按 AGENTS.md 规则 2 应删或补消费方。

## 步骤 4：新增日志调用必须确认 logger 已定义
把 `except Exception: pass` 改成 `logger.xxx(...)` 时，如果该模块没有 `logger = logging.getLogger(__name__)`，会在 except 触发时抛 NameError。
```bash
for f in <被改文件...>; do grep -n "logger = logging.getLogger" "$f"; done
```

## 步骤 5（最关键）：跑全量 pytest，不是只跑目标用例
```bash
.venv/Scripts/python.exe -m pytest tests/ -q 2>&1 | tail -45   # 本仓库约 16 分钟
```
**这是本类复查最核心的一步。** 实测：一个 CLI 重构提交只更新了自己的测试，却因改了 catalog 补全行为破坏了 `tests/test_commands_autocomplete.py` —— 只跑目标用例完全发现不了。

长门禁必须等待真实结束后再下结论。工具前台超时或中途只看到“仍在运行/已到某百分比”都不能算失败；改用后台任务并等待最终退出码和汇总行。不要用 `pytest | tail; echo $?` 判断结果，因为管道可能掩盖 pytest 的非零退出。

## 步骤 6：区分“确定性回归” vs “flaky / 环境”
对每个失败用例单独复跑：
```bash
.venv/Scripts/python.exe -m pytest '<file>::<test>' -q
```
- 单独跑仍失败 = **真回归**，必须修（本轮 0.47s 稳定失败的那个）
- 单独跑通过 = 并发/环境 flaky（本轮 live 三服务 lifespan 测试在全量并发跑时 DELETE 得 404，单独跑 200），报告中标注即可，**不要**当回归去改代码

## 步骤 7：运行时健康快照（改动涉及服务/记忆时）
一次拉全 gateway/memory/supervisor/goal_manager。关注：
- memory `/health` 的 `commit_revision` 只是记忆库**写入修订号**，**递增不能证明新代码已加载**；判断是否加载须结合服务 PID/启动时间、模块来源路径、构建版本或源码提交标识（该判据的更正过程见 `system-health-audit`）
- `maintenance.tier2_bridge.state` 与 `consecutive_failures`
- `agent_outbox` 死信/stale 计数

若 wheel/build 验收失败，先把它与代码测试结果分栏：Windows `WinError 145`、Permission denied 或目录非空通常表示历史构建产物/文件句柄占用，不等价于代码回归。先检查是否有 Nuitka/compiler 进程和具体残留目录；不要为了让验收变绿直接删除未知目录或改构建逻辑。记录为环境阻塞，必要时在明确授权后再清理并重跑 wheel。

运行时健康不能只看 HTTP 200：对 memory 还要读取 bridge 的 `last_tier2_bridge_result`、`eligible_candidate_count`、`consecutive_rejections`、outbox pending/dead-letter；将“有大量候选但 bridge 最近成功且失败计数为 0”判为已收敛但仍有历史积压，不能误报为服务故障。对 turns 的 pending 数量要结合保护窗口、按日分布和最近 scope 成功证据判断。

## Pitfalls（本轮踩过或差点踩的假阳性/陷阱）
- **locale 键不能只靠 grep 判对错**：locale 是嵌套 JSON（`translations.prompts.<key>`），`translate("prompts.<key>")` 按点拆路径查找。文件里在 prompts 段内写裸 `"use_model_to_switch_models"` 是**正确**的；若写成带 `"prompts."` 前缀反而双前缀失配。判定必须用真实引擎：
  ```python
  from voidcube.interfaces.cli.i18n import get_i18n
  i = get_i18n(); i.set_locale('zh_CN'); print(repr(i.translate('prompts.<key>')))
  ```
- **幽灵 pyc 检测别写错父目录**：源文件在 `__pycache__` 的**父目录**，不是 `__pycache__` 内。脚本用 `src=os.path.join(root, name+'.py')` 会把全部 pyc 误判为 ghost（本轮误报 716 个）；正确写法 `src=os.path.join(os.path.dirname(root), name+'.py')`。
- **全量 pytest 不支持 `--timeout`**：本仓库未装 pytest-timeout，带该参数会 “unrecognized arguments” 秒退。
- **后台任务去重**：连续 `process action=poll` 同一 session 会返回 `duplicate_or_in_flight_action`，改用 `process action=list` 或 `wait`。
- **顶层 health 只有 healthy/degraded 二值**：像 `tier2_bridge.state=warning` 不体现在 memory 顶层 `status`，别据此判定故障（warning 是刻意的、不拉红服务的状态）。
- **`.test-tmp` 的 Permission denied / WinError 5 是所有权问题**，属历史遗留，不是本类回归。

## 报告模板
按严重度输出：
1. 🔴 确定性回归：失败用例名 + 现象 + 根因 + 已验证修法（最好先本地复现修法再给结论）
2. 🟡 死代码 / 口径缺口（只写不读属性、孤儿函数、参数边界、与主题不一致的残留）
3. 🟢 流程治理（提交范围与信息不符、漏跑全量测试）
再列“已验证通过”：架构 gate 8/8、compileall、打包契约 + 技能注册、目标用例、全量 `N passed`、服务健康；最后给下一步建议并询问是否动手。

## 交叉引用
- 架构/卫生（幽灵 pyc、构建残留、技能差异）：`voidcube-architecture-audit`
- 全栈服务健康：`system-health-audit`
- `/model` 等内建命令改动的链路地图：`voidcube-cli-command-refactor`
