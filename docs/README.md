# VoidCube 文档导航

更新日期：2026-07-23

本目录只保留现役架构、工程说明和仍在使用的专项设计。已完成的改造计划、阶段差距报告和迁移流水不留在主干；需要追溯时使用 Git 历史。

## 阅读顺序

| 优先级 | 文档 | 回答的问题 |
| --- | --- | --- |
| 1 | [架构基线](./voidcube架构基线.md) | 当前组件职责、运行链路、门控语义和不可破坏的边界 |
| 1 | [项目架构与逻辑架构](./项目架构与逻辑架构.md) | 系统总览、进程拓扑、业务链路、数据流和故障边界 |
| 1 | [当前问题](./全链路问题清单.md) | 现在仍需解决什么，以及下一阶段为何这样排序 |
| 2 | [项目文件架构](./项目文件架构说明.md) | 真实入口、目录职责和主要调用关系 |
| 2 | [开发与验证](./开发与验证.md) | 环境、测试分层、构建和提交前检查 |
| 3 | [内生驱动核心设计](./内生驱动核心设计.md) | API-B 认知判断、治理输出和程序护栏 |
| 3 | [CLI 展示与 Gateway 双泳道](./CLI展示与gateway双槽设计.md) | `user_chat` 与 `supervisor_task` 的展示隔离 |
| 3 | [API 配置与模型调用点](./API配置双槽与模型调用点.md) | API-A、API-B 配置归属和调用边界 |

## 当前主线

- 唯一安装入口是 `voidcube.py`，交互链路继续进入 `VoidCube_cli/main.py -> cli.py -> run_agent.py`。
- 默认服务启动顺序是 `Gateway -> Memory -> Supervisor`；Executor 由 Supervisor 进程挂载并注册到 Gateway。
- Supervisor 启动后只运行基础健康检查、服务注册恢复和身体观察等基线任务，自主链路门控默认关闭；结构化记忆维护由 Memory Service 独占。
- `/auto` 是当前临时启用开关：它启用 Supervisor 的内生驱动与治理复核循环，并启动当前 CLI 内的 API-A 自主执行组件。
- `/auto-q` 是对应的临时停用开关：它停止自主链路周期任务并中断当前自主任务；基础服务和用户主 CLI 保持可用。
- `/auto` 不是用户对话模式。用户请求始终走 `user_chat`，自主任务执行走 `supervisor_task`，两条泳道不得互相覆盖。
- Web 小屋只观察 API-B 判断、转交、执行回报和 Mem 回流，不承担用户聊天或人工队列控制。
- 新身体必须经过候选物化、probe、Governor 审查和用户同意后才能激活；用户同意门不能被自动化绕过。
- 模型请求统一使用 OpenAI-compatible Chat Completions；项目退役集成在源码、可加载技能和 wheel 中必须保持零入口。
- Agent 会话持久化由 `agent/session_persistence.py` 统一负责；共享/请求级客户端和连接清理由 `agent/client_lifecycle.py` 统一负责；请求线程、超时、流式重连与非流式降级由 `agent/chat_transport.py` 统一负责。
- 流式 chunk 装配由 `agent/stream_response.py` 统一负责；工具调用的解析、顺序/并发调度、计时与中断补位由 `agent/tool_execution.py` 统一负责。`run_agent.py` 不再持有这些职责的第二套实现。
- CLI 流式状态由 `VoidCube_cli/chat_render_state.py` 持有，标签过滤与缓冲转换由 `chat_stream_processor.py` 负责，终端边框、颜色和输出顺序由 `chat_stream_renderer.py` 负责；`cli.py` 只保留真实回调入口。
- CLI slash/path 判定、中央 alias 规范化、慢命令状态以及 quick/plugin/skill/前缀优先级由 `VoidCube_cli/command_router.py` 统一负责；失效的 `/cron`、`/insights` 兼容入口已删除。
- CLI 内建命令由 `VoidCube_cli/command_execution.py` 的不可变 spec 表分发；同一模块统一管理 busy 状态、嵌套恢复和异常收尾，`process_command()` 只协调内建执行与动态路由。
- Agent 默认 `mem` Provider 经 Gateway 调用 Memory Service，不创建本地库或运行第二套 Pipeline；完成轮次写回 Tier 1，服务不可用时显式降级。
- MemAI 的运行导入源固定为仓库共享 `Mem/src`，不跟随 Body 槽位切换；服务启动会校验导入路径并写入 `runtime/memory/mem-source-binding.json` 审计。
- Gateway 代理会原样转发重复查询参数；统一 recall 对“刚才/刚刚/方才”类请求只检索近期 Tier 1，并提高同义概念与新近度权重。
- Supervisor 的任务状态和 SelfLearning 结论先写治理事件，再发布本地投影；Supervisor 内部执行统一经过 `VoidCubeExecutionFacade -> Adapter`。
- CLI 的 Gateway 地址统一来自服务配置；工具并行结果保持原调用顺序，Agent 中断覆盖实际工具 worker，含附件的中断输入保持 payload 与顺序。

## 当前协作合同

- 跨进程服务调用经过 Gateway；Supervisor 同进程执行经过 Facade；两条路径落到同一 Adapter。
- 长期记忆与治理历史由 Memory/MemAI 持有，Agent 会话和 Supervisor Store 只是各自用途下的工作状态或投影。
- Tier 1 -> Tier 2、结构化维护、工具调度、终端审批、CLI busy 输入各有唯一所有者，不在调用方复制实现。
- 运行态只写入 `VOIDCUBE_HOME` canonical layout；旧路径只允许一次性迁移器读取。

## 维护规则

- 当前行为改变时，同一阶段更新代码、聚焦测试和对应现役文档。
- 问题清单只记录未完成事项，不保存完成日志；完成原因与历史差异由 Git 承担。
- 新设计替代旧路径后，删除旧分支、旧参数、旧提示和冗余测试。
- 专项文档只有在仍定义现役协议时保留；纯计划在完成或失真后删除，不改名为“历史资料”继续堆积。
- 统计数字不作为架构事实，测试数量通过命令实时获取。
