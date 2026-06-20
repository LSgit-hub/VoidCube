# Agent Evolution Boundary

## 1. 文档定位

本文是 [voidcube架构基线.md](./voidcube架构基线.md) 与 [git-evolution-lineage.md](./git-evolution-lineage.md) 的边界补充。

本文只定义子 Agent 身体进化与 VoidCube 母体项目代码的分界。VoidCube 母体就是本项目本身，由开发者按正常工程流程维护，不进入子 Agent 自进化自动执行链。

它只回答一个问题：

**在同一个 VoidCube 项目仓库中，如何区分“正在进化的子 Agent 身体”和“VoidCube 母体基础设施”，并防止两者在 Git 分支中互相污染。**

## 2. 核心结论

VoidCube 可以优先使用项目仓库分支 / ref 管理子 Agent 进化，不必为每个子 Agent 立即引入独立嵌套仓库。

但这要求建立一条硬规则：

**body upgrade / body switch 只能承载子 Agent 身体层变更；母体基础设施变更由开发者维护，不能混入身体切换。**

也就是说：

- Git branch / commit 记录“子 Agent 候选变更是什么”
- `changed_files` 判断“这批变更属于谁”
- `SelfEvolutionExecutionRequest.kind` 判断“这批变更要进入哪条执行链”
- Mem / 监督者判断“是否允许进入执行”
- executor 只执行已经被批准且未越界的交接单

## 3. 推荐边界

Phase 1 中，子 Agent 身体层默认包括：

| 路径 | 语义 |
| --- | --- |
| `agent/` | Agent 的核心推理、上下文、工具调度、模型适配等身体能力。 |
| `systems/agent/` | Agent 实例运行入口与身体 runtime 适配。 |
| `tools/` | Agent 可调用工具层。 |
| `skills/` | Agent 可加载能力包。 |
| `presets/` | Agent 可使用的运行/工具预设。 |
| `run_agent.py` | Agent 启动入口。 |

母体基础设施默认包括：

| 路径 | 语义 |
| --- | --- |
| `systems/supervisor/` | 监督者判断与治理交接。 |
| `systems/execution/` | 执行器动作面。 |
| `systems/gateway/` | 内部神经中枢与路由事实源。 |
| `systems/memory/`、`plugins/memory/`、`Mem/` | 记忆服务、治理记忆与长期记忆系统。 |
| `systems/body_registry.py`、`systems/lifecycle.py`、`systems/probe.py` | 身体槽位、状态机、探针与切换安全设施。 |
| `VoidCube_cli/` | 用户入口、测试、验收、应急操作面。 |
| `VoidCube_core/` | 母体通用核心能力。 |
| `docs/`、`tests/` | 文档与验证资产。 |

这不是永久不变的目录表，而是 Phase 1 的保守安全边界。后续如果要让某个目录进入子 Agent 进化范围，必须先更新本文与代码中的边界策略。

## 4. 为什么同仓库分支不会互相干扰

同仓库分支本身不等于混乱。混乱来自“没有任务类型与路径边界”。

VoidCube 的防干扰策略是三层：

1. **任务类型隔离**：`body_upgrade` / `body_switch` 只表达子 Agent 身体进化；母体系统升级必须使用非 body 类任务。
2. **路径边界隔离**：正式 body 自进化请求中的 `git_lineage.changed_files` 必须全部落在子 Agent 允许路径内。
3. **运行边界隔离**：每个子 Agent 应拥有独立 `worktree/runtime/logs/meta`，即使 Git 真相源来自同一项目仓库，运行态、日志、元数据也不互相覆盖。

因此，一个分支可以叫 `evolution/<task-id>`，但它能不能成为 body candidate，不看名字，而看：

- 它改了哪些文件
- 它是否有候选 commit 与回滚 commit
- 它是否通过测试与 probe
- 它是否由 Mem / 监督者批准
- 它是否被 executor 作为正式交接单消费

## 5. 混合变更如何处理

如果同一个候选分支同时修改了：

- `agent/stream_handler.py`
- `systems/body_registry.py`

那么它不能作为 `body_upgrade` 或 `body_switch` 被批准。

正确处理方式是拆成两条任务：

| 任务 | 内容 | 执行链 |
| --- | --- | --- |
| 子 Agent 身体进化 | `agent/stream_handler.py` | `body_upgrade` / `body_switch` |
| VoidCube 母体维护 | `systems/body_registry.py` | 开发者按项目工程流程维护，不进入 body 自进化链路 |

这样可以避免“改了母体基础设施，却被当成某个替身 Agent 的进化成果”。

## 6. Git 使用建议

Phase 1 推荐：

- `main` 保持母体稳定基线
- `evolution/<task-id>` 保存候选变更
- `body/slot-A`、`body/slot-B` 可标记槽位当前来源
- `stable/<version>` 标记长期稳定点
- `rollback/<task-id>` 记录恢复点或回滚动作

如果未来发现子 Agent 进化与母体开发频繁互相阻塞，可以再升级为：

- `git worktree` 为每个候选体提供独立工作目录
- 子 Agent 独立仓库或 subtree
- 更细粒度的 manifest，声明每个 body slot 可以读取哪些路径

但在 Phase 1，优先不要过早拆仓库。先让边界策略、changed_files 审计和正式交接单跑通。

## 7. 当前实现状态

当前代码已建立初始边界校验：

- `systems/evolution_boundary.py` 定义子 Agent 允许路径与母体禁止路径
- `SelfEvolutionExecutionRequest` 在 `body_upgrade` / `body_switch` 校验阶段要求并检查 `git_lineage.changed_files`
- 缺少 `changed_files` 的正式 body handoff 会被拒绝，因为监督者不能批准不可审计的黑盒 commit
- 若 body 自进化请求包含母体路径或未知路径，审批会失败，任务保持未执行状态
- 当前不提供母体自动升级任务类型；VoidCube 母体由开发者管理

这层校验不是替代 Mem 的判断，而是给 Mem / 监督者提供一个确定性安全闸门。

## 8. 一句话结论

子 Agent 可以在 VoidCube 项目仓库分支中进化，但正式 body 切换只能消费“路径边界干净”的候选变更；VoidCube 母体由开发者维护，不能伪装成某个替身 Agent 的身体进化。
