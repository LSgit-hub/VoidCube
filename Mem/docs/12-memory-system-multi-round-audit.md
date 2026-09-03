# 记忆系统多轮多角度审查总账

本文档是 VoidCube Mem 记忆系统的持续审查记录，不替代设计规范；它记录每轮检查的范围、证据、缺陷处理和交付门槛。每轮只从一个主角度分析，避免把同一组测试重复描述成不同结论。

## 交付目标

记忆系统达到以下工程化条件后，才可将审查总账标记为交付：

- 数据边界在 owner、workspace、memory_domain、global scope、source type 之间无越权入口。
- 召回结果遵守 active、visible、identity、evaluation、record filter、as-of 和时间意图约束。
- Tier 1、Tier 2、Profile、Archive、Promotion 和 Entity Graph 的事实来源、派生索引与生命周期状态一致。
- 生命周期状态、撤销、隐藏、恢复、归档、purge 和重建具备事务一致性、来源可追踪性和幂等性。
- 迁移、旧数据修复、备份恢复和重建路径可重复执行，不制造重复记录或幽灵索引。
- 失败路径有明确回滚、重试、隔离或审计记录；后台维护不会静默吞掉数据范围错误。
- 关键不变量有回归测试，完整测试、静态检查、编译检查和适用的退役入口扫描均有可复现证据。

## 审查方法

缺陷按影响分级：

- **P0**：跨用户/跨域泄露、不可逆数据破坏、无法启动或核心召回失效。
- **P1**：可稳定触发的错误召回、生命周期破坏、索引与事实源不一致或审计缺失。
- **P2**：边界条件错误、统计污染、迁移兼容问题或明显的性能退化。
- **P3**：可维护性、诊断信息或文档缺口，不改变数据正确性。

每个发现必须记录：触发条件、事实证据、影响、修复位置、回归测试、残余风险。修复主逻辑后删除失效分支和旧兼容入口，避免后续会话把旧逻辑重新当成主路径。

## 轮次计划

| 轮次 | 主角度 | 主要检查对象 | 交付证据 |
| --- | --- | --- | --- |
| R0 | 基线与范围 | 代码、schema、设计文档、现有测试、工作区状态 | 基线提交、文件清单、已知风险清单 |
| R1 | 数据边界与权限 | scope/domain、global scope、promotion、record filter、source type | 跨 owner/workspace/domain 负例测试 |
| R2 | 召回正确性 | Tier 1/Tier 2/Profile/Archive/Graph、排序、as-of、时间意图、上下文预算 | 召回真值与边界回归测试 |
| R3 | 生命周期与派生一致性 | compression、promotion、profile revoke、hidden/pin、entity graph、embeddings | 状态转换、幂等、重建前后等价测试 |
| R4 | 事务、并发与失败路径 | SQLite 事务、后台任务、重试、回滚、审计、连接关闭 | 注入失败、重复执行、并发隔离测试 |
| R5 | 迁移、运维与交付 | schema migration、backup/restore、wheel、退役入口、全量验证 | 迁移/恢复演练、扫描、全量测试报告 |
| R6 | 独立复核 | 反向审查已修复路径和残余风险 | 无未处理 P0/P1；P2/P3 有明确接受或计划 |

## 证据命令

默认使用项目虚拟环境：

```powershell
.\.venv\Scripts\python.exe -m pytest Mem/tests tests/test_memory_resource_contract.py tests/test_memory_session_rebinding.py -q
.\.venv\Scripts\python.exe -m ruff check Mem/src/memai tests/test_memory_resource_contract.py
.\.venv\Scripts\python.exe -m compileall -q Mem/src/memai
git diff --check
```

涉及模型、鉴权、请求协议、技能或打包时，追加项目规定的退役集成扫描、相关测试和 wheel 检查；仅修改记忆内部实现时，需在轮次记录“不适用”及理由，不能省略说明。

## 轮次记录

### R0：基线与范围

- 状态：**已完成**。
- 基线：`6c82ce6`，工作区初始干净。
- 已纳入范围：`Mem/src/memai/application`、`indexes`、`repository`、`migrations`、`transport`、`Mem/tests` 及根目录记忆契约测试。
- 现有关键风险：实体图、语义索引、FTS 和主表存在多个派生路径；需要逐轮验证，而不能只依赖召回 happy path。

### R1：数据边界与权限

- 状态：**已完成**。
- 重点：跨 scope/global fallback、promotion source existence、record filter 在图路径的闭包、evaluation 数据隔离。
- 证据：`tests/test_memory_domain_isolation.py` 15 passed；实体图端口鉴权负例通过；图端口现在统一经过 `_authorized_read_domains()` / `_authorized_write_domain()`。
- 发现并修复：实体图列表、邻居和重建端口原先可绕过 domain actor 策略（P1）。
- 通过条件：任何候选来源都必须同时满足 scope、domain、状态和授权约束；负例不能通过另一条派生路径返回。

### R2：召回正确性

- 状态：**已完成**。
- 重点：近期查询排序、founding identity、as-of、候选预取、去重、反馈分数和上下文长度。
- 证据：图、FTS、Tier 1/2、Profile、Archive、语义召回及主召回回归均通过；核心组合共 217 passed，且迁移测试后图召回顺序回归通过。
- 发现并修复：存在长词 FTS 锚点时仍启用两字中文 `LIKE` fallback，泛词会把候选提前纳入 Tier 2 并压制 graph tier（P1）。现在仅在没有长词锚点时启用 fallback。
- 通过条件：查询意图对应的时间/身份策略优先级稳定，候选池扩大不会绕过过滤条件。

### R3：生命周期与派生一致性

- 状态：**已完成**。
- 重点：升级 provenance、空 source_turns、hidden/pin/unpin、profile revoke、图重建幂等、embedding/FTS 失效。
- 证据：生命周期 provenance、隐藏、撤销、图重建和 embedding 清理均有负例覆盖，并通过核心记忆回归。
- 通过条件：重复执行和重建前后结果等价；隐藏、撤销、purge 后所有派生索引均不可返回已失效记录。

### R4：事务、并发与失败路径

- 状态：**已完成**。
- 重点：提交前失败、重试、连接关闭、后台任务并发写、scope 交错、审计记录完整性。
- 证据：新增生命周期派生图失败回归验证事务回滚和连接关闭；迁移、维护重试、并发语义索引和跨 scope 回归均通过。
- 发现并修复：生命周期原先只在成功路径提交/关闭连接，异常可能留下未回滚事务和连接泄漏（P1）。新增事务外壳，异常/取消统一 rollback + close。
- 通过条件：失败不产生半套主表/派生表状态；重试不会重复计数或丢失来源。

### R5：迁移、运维与交付

- 状态：**已完成**。
- 重点：旧 schema、备份恢复、graph/FTS/embedding 重建、wheel 内容和退役入口扫描。
- 证据：迁移/备份/恢复回归通过；`tests/test_packaging_contract.py` 24 passed，包含退役集成和 wheel 内容零入口契约；`scripts/build_wheel.py` 真实构建并验证 `dist/voidcube_agent-1.0.0-py3-none-any.whl` 成功。鉴权改动已执行该适用扫描。
- 通过条件：空库、旧库、恢复库均能通过同一组核心不变量；适用扫描零入口。

### R6：独立复核

- 状态：**已完成**。
- 重点：只看已修复代码的反例和残余风险，不重复执行同一轮的证明。
- 证据：按失败顺序复跑迁移 + 图召回组合、跨域 promotion 撤销和生命周期异常回滚；最终远程保留测试、记忆资源契约、会话重绑定和 wheel 契约共 164 passed；本地核心回归 217 passed；Ruff、compileall、`git diff --check` 和真实 wheel 构建均通过。
- 资产边界：root `tests/` 按仓库约定仅保留在开发机、不随远程仓库分发；`Mem/tests/` 与已跟踪的记忆资源契约测试承担远程交付门禁。当前本地回归仍纳入复核证据，但不改变该资产策略。
- 通过条件：没有未处理 P0/P1；所有 P2/P3 有证据、负责人和后续计划。

## 缺陷登记

| ID | 轮次 | 等级 | 缺陷/影响 | 修复与测试 | 状态 |
| --- | --- | --- | --- | --- | --- |
| AUDIT-001 | R0 | P1 | 既有图召回、生命周期和撤销路径需要统一边界审查 | R1-R3 已完成分轮验证，相关缺陷拆分为 AUDIT-002/003/005 | 已关闭 |
| AUDIT-002 | R1 | P1 | 实体图端口未统一执行 domain actor 鉴权，可绕过主召回边界 | `memory_service.py` 图端口鉴权；资源契约负例测试 | 已修复 |
| AUDIT-003 | R2 | P1 | 长词存在时两字 CJK FTS fallback 污染 Tier 2 候选并压制 graph tier | `lexical_index.py` 限制 fallback；图召回组合回归 | 已修复 |
| AUDIT-004 | R2/R4 | P2 | 服务配置构造改变进程级 host integration，测试顺序可改变语义召回策略 | 迁移测试作用域恢复 fixture；组合回归 | 已修复 |
| AUDIT-005 | R4 | P1 | 生命周期异常路径未统一 rollback/close，可能留下半事务和连接泄漏 | 生命周期事务外壳；派生图失败回归 | 已修复 |

## 变更纪律

审查期间保持每轮修改可定位；不在同一提交中混入无关重构。每轮完成后更新本文档，并在最终交付前保留测试命令和实际输出摘要。未提交的工作区变更不得被描述为已交付。
