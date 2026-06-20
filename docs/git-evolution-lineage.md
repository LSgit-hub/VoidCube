# Git Evolution Lineage

## 1. 文档定位

本文是 [voidcube架构基线.md](./voidcube架构基线.md) 的 Git 演化谱系扩展说明。

路径级的子 Agent / 母体边界见 [agent-evolution-boundary.md](./agent-evolution-boundary.md)。本文只定义 Git 谱系如何记录，不重复展开路径归属。

它只回答一个问题：

**VoidCube 在自进化中如何使用 Git 记录候选体来源、差异、验证证据和回滚点。**

Git 不替代 Mem，不替代监督者，不替代 body registry。Git 负责代码与配置变更谱系；Mem 负责长期记忆、治理理由、裁决历史与身份连续性。

## 2. Git 在自进化中的角色

Git 适合承担：

- 候选体代码来源
- worktree 隔离
- branch / commit / tag 谱系
- diff 审查材料
- probe 前后的快照
- 回滚到已知稳定版本
- 将学习结论、执行任务与代码变更关联

Git 不负责：

- 判断是否应该切换
- 判断候选体是否可信
- 保存长期身份真相
- 保存唯一运行状态
- 替代 Mem 记录治理理由

一句话边界：

**Git 记录“具体改了什么”；Mem / 监督者判断“为什么改、能不能切、什么时候切”。**

## 3. 推荐分支与引用语义

| Git 对象 | 语义 |
| --- | --- |
| `main` 或稳定分支 | 当前母体稳定代码基线。 |
| `evolution/<task-id>` | 某个自进化任务的候选变更分支。 |
| `body/slot-A`、`body/slot-B` | 可选槽位引用，用于标记某槽位当前 materialized 来源。 |
| `stable/<version>` | 通过治理并稳定运行后的稳定标签或分支。 |
| `rollback/<task-id>` | 可选回滚引用，记录回退点或恢复动作。 |

实现上不强制所有引用都必须存在。Phase 1 可以先记录 commit hash 与 diff 摘要，后续再逐步引入分支/标签策略。

Phase 1 推荐优先使用项目仓库自身的分支 / ref 管理子 Agent 谱系，而不是在每个子 Agent worktree 内部再创建独立 Git 仓库。子 Agent 应保持独立 `worktree/runtime/logs/meta`，但 Git 真相源仍归项目仓库统一治理。

这样做的好处是：

- 避免嵌套 `.git` 与母体仓库冲突
- Mem / 监督者只需要审计一个 Git 根
- 回滚点、diff 和 stable ref 更容易统一记录
- 后续仍可用 `git worktree` 派生更强隔离，而不是拆成多个仓库

同仓库分支不代表边界混用。`body_upgrade` / `body_switch` 必须通过 `git_lineage.changed_files` 证明候选变更只落在子 Agent 身体边界内；涉及 supervisor、executor、gateway、body registry、Mem、CLI、docs 或 tests 的变更不能混入正式身体切换。

## 4. 自进化 Git 流程

推荐流程：

```text
自学系统产出建议
  -> Mem 记录学习结论
  -> 监督者创建/批准 evolution task
  -> executor 创建 evolution/<task-id> 或独立 worktree
  -> executor 应用变更
  -> 运行测试与 probe
  -> 记录 diff / commit / probe report
  -> 监督者审查证据
  -> approve_with_watch 后切换 active body
  -> watch-window 成功后标记 stable
  -> 执行结果与 Git 谱系写回 Mem
```

失败路径：

```text
测试失败 / probe 失败 / watch-window 失败
  -> 保留失败 branch / commit / logs
  -> 记录失败原因
  -> 必要时 rollback 到 retired stable commit
  -> 将失败证据写回 Mem
```

## 5. 候选体元数据

每个候选体进入 `candidate` 或 `probe` 时，应尽量记录：

- `task_id`
- `source_branch`
- `source_commit`
- `candidate_branch`
- `candidate_commit`
- `active_ref`
- `rollback_ref`
- `diff_summary`
- `changed_files`
- `test_report_ref`
- `probe_report_ref`
- `rollback_commit`
- `created_by`
- `created_at`

这些字段可以先落在 body slot `meta`、probe report 或 Mem 治理记录中。长期目标是三者互相可引用：

- body registry 知道当前槽位来自哪个 Git 引用
- Mem 知道为什么允许这个引用进入 probe 或 active
- Git 知道该引用对应哪个自进化任务

## 6. 切换前 Git 审查要求

正式 `probe -> active` 前，监督者至少应能看到：

- 候选 commit
- 与当前 active commit 的 diff 摘要
- 测试结果
- probe report
- 目标改进说明
- 风险说明
- 回滚 commit

缺少这些证据时，监督者应输出 `defer` 或 `reject`，而不是放行正式切换。

正式批准后，Mem / 监督者应生成一份 `SelfEvolutionExecutionRequest`，作为交给 executor 的唯一正式执行交接单。

这份交接单至少包含：

- `task_id`
- `kind`
- `target_slot_id`
- `git_lineage.source_branch`
- `git_lineage.source_commit`
- `git_lineage.candidate_branch`
- `git_lineage.candidate_commit`
- `git_lineage.active_ref`
- `git_lineage.rollback_ref`
- `git_lineage.rollback_commit`
- `git_lineage.diff_summary`
- `git_lineage.changed_files`
- `probe_report_ref`
- `idle_window_evidence`
- `governor_decision`
- `rollback_plan`

executor 可以提供测试、验收、排障、应急接口，但正式身体切换必须消费这类已批准交接单，而不是消费人工手动命令本身。

## 7. 回滚语义

回滚不是简单 `git reset`。

正式回滚应同时满足：

- body registry 恢复到旧 active slot
- active body pointer 指向旧稳定体
- gateway activation 指向恢复体
- Git rollback point 可追踪
- 回滚原因写回 Mem
- 失败候选体保留可诊断证据

Git 负责提供可回退的代码点；执行器负责运行时切回；Mem / 监督者负责确认为什么要回滚。

## 8. Phase 1 最小实现状态

第一阶段不必一次性实现完整分支体系。当前最小闭环按以下顺序推进：

| 项目 | 状态 |
| --- | --- |
| self-evolution task 批准时生成 `SelfEvolutionExecutionRequest` | 已建立初始合约。 |
| body slot meta 记录 branch/ref + commit lineage | 已建立 `source_branch/source_commit`、`candidate_branch/candidate_commit`、`active_ref`、`rollback_ref/rollback_commit`、`diff_summary`、`changed_files`。 |
| probe report 写入 `candidate_commit` 与 `changed_files` | 已建立初始字段，并可从 slot meta 自动继承。 |
| 轻量 governor history 写入 `diff_summary`、`probe_report_ref`、`rollback_commit` 与边界摘要 | 已建立 `evolution_lineage` 摘要，并包含 `evolution_boundary`；该记录为 best-effort，不等同于完整 Mem 能力。 |
| executor / body registry 在 prepare/candidate 阶段记录当前 Git branch 和 `git rev-parse HEAD` | 已建立初始自动采集；显式传入值优先，非 Git 环境不阻断。 |
| body registry 在 prepare/candidate 阶段从 Git diff 自动采集 `changed_files` | 已建立初始采集；显式传入值优先，非 Git 环境或同 commit diff 不阻断。 |
| 切换成功后记录 active ref / active commit | 已在 active slot、active body pointer 与 `last_switch_result` 中记录。 |
| body self-evolution 必须携带 `changed_files` 并校验子 Agent / 母体边界 | 已建立，缺失 `changed_files` 或越界母体路径都会阻断正式 body handoff。 |
| 边界违规导致的 defer 写入轻量治理历史 | 已建立 best-effort `boundary_defer` 记录；写入失败不阻断 review 主链路。 |
| watch-window 成功后可选择打 `stable/<version>` tag | 待实现。 |

这样就能先回答：

- 当前 active 是哪次代码状态
- 候选体从哪里来
- 它改了什么
- 为什么允许切换
- 失败时回到哪里

## 9. 一句话结论

Git 是 VoidCube 自进化的谱系和回滚骨架；Mem 是长期记忆与治理灵魂，需要作为独立主线继续补完整，当前轻量治理历史只是未来 Mem 契约的适配层；监督者是判断者；执行器是动作执行者。正式身体切换必须由 Mem / 监督者基于 Git 证据、probe 证据和运行协议裁决触发，而不是由人工手动切换触发。
