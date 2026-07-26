# VoidCube 架构基线

这份基线从当前实现提炼稳定边界：

- 唯一安装入口是 `voidcube.py`；服务启动顺序为 Gateway -> Memory -> Supervisor。
- Gateway 是跨进程调用 Executor 的唯一标准入口；Supervisor 进程内部通过 `VoidCubeExecutionFacade` 调用同一组 Adapter。
- Supervisor 负责 API-B 认知、治理和任务投影，不是长期执行器。
- Agent 可以持有可恢复会话、消息游标和临时上下文，但不拥有长期身份、治理裁决或跨会话事实真相。
- Memory Service 和 MemAI 持有长期记忆与治理事件；`AutonomousChainStore` 是可重建的工作投影。
- 星子由 Supervisor + Memory/MemAI + API-B 共同承载，不创建第二个独立 Agent 进程。
- 系统启动进入 `daily_companion`；星子周期读取 VoidCube 内部事件，意图不清或没有明确帮助价值时保持沉默。
- 自主链路门控初始为关闭；当前 `/auto` 是临时启用开关：它把星子从日常伴侣切换到 `auto_evolution`。`/auto-q` 是对应的临时停用开关：它收口 Auto 任务并返回日常模式。
- 日常与 Auto worker 互斥：Auto 不履行伴侣职责，也不持续感知实时用户行为。
- Memory 在现有 owner/workspace scope 上增加 `agent_interaction`、`companion`、`evolution` 三域；跨域传播只能使用可审计提升引用。
- Execution 作为 Supervisor 进程内服务挂载，不额外启动 Executor daemon。
- MemAI 运行代码固定从仓库共享 `Mem/src` 导入，不跟随 Body 槽位切换。
- 身体切换必须经过 candidate、probe、Governor、用户明确同意和 watch window。
- 自主进化不得绕过 candidate/probe/consent/watch/rollback 链直接覆盖活动身体。
- 所有模型请求使用 OpenAI-compatible Chat Completions；退役集成在源码、技能和 wheel 中保持零入口。

这些边界若发生变化，必须同步更新实现、测试和主架构文档。
