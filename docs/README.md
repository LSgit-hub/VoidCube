# VoidCube 文档导航

更新日期：2026-07-19

本目录只维护三类资料：当前架构、当前工程说明、历史迁移记录。讨论或修改现役实现时，必须先使用当前架构文档；历史文档只用于解释旧术语为何存在，不能反向定义主逻辑。

## 文档优先级

| 优先级 | 文档 | 用途 |
| --- | --- | --- |
| 1 | [voidcube 架构基线](./voidcube架构基线.md) | 架构职责、双链路边界、身体治理和明确不做的能力 |
| 2 | [全链路问题清单](./全链路问题清单.md) | 当前仍存在的问题、已经收口的事项和必须守住的边界 |
| 2 | [项目文件架构说明](./项目文件架构说明.md) | 真实入口、目录职责、主要调用链和维护热点 |
| 2 | [开发与验证](./开发与验证.md) | 开发环境、smoke / 模块 / 全量测试和构建验收 |
| 3 | [内生驱动核心设计](./内生驱动核心设计.md) | API-B 内生驱动的组件级设计 |
| 3 | [CLI 展示与 gateway 双槽设计](./CLI展示与gateway双槽设计.md) | CLI 展示与 `user_chat` / `supervisor_task` 泳道协议 |
| 3 | [API 配置双槽与模型调用点](./API配置双槽与模型调用点.md) | API-A / API-B 模型配置归属 |
| 历史 | [全链条迁移日志](./全链条迁移日志.md) | 已退场术语和兼容面的迁移背景 |
| 方案 | [Mem LLM-First 改进方案](./mem-llm-first-redesign.md) | 尚需结合现状验收的 Mem 设计方案 |
| 方案 | [Mem 双层改造计划](./mem-two-tier-renovation-plan.md) | Mem 双层记忆的阶段计划 |
| 审计 | [内生驱动到身体升级差距](./endogenous-drive-body-upgrade-gap.md) | 历史差距分析；当前结论以问题清单为准 |

## 当前事实

- 唯一安装入口是 `voidcube.py`；交互式聊天继续进入 `VoidCube_cli/main.py -> cli.py -> run_agent.py`。
- 默认服务启动顺序是 `Gateway -> Memory -> Supervisor`。
- 用户链路和自主链路共享 API-A 能力与 Mem，但不共享交互语义；Web 小屋只观察自主链路。
- `cli.py` 和 `run_agent.py` 仍是主路径上的巨型模块，不能按历史兼容文件直接删除。后续拆分必须先迁移调用方和测试，再删除旧入口中的对应实现。
- CLI worktree 生命周期与自主改进 Git diff 已收口到 `VoidCube_cli/cli_handlers.py`；`cli.py` 不再保留同名遮蔽实现或无用的全局 worktree 缓存。
- 本地附件路径解析、文件拖放、图像参数收集与徽标格式化已收口到 `VoidCube_cli/attachments.py`；Windows 盘符路径和 Termux POSIX 示例均有独立回归测试。
- Agent 消息 surrogate / 非 ASCII 恢复统一由 `agent/message_sanitizer.py` 提供，`run_agent.py` 不再维护重复实现。
- 跨模块的内部 function-tool 标准化由 `agent/tool_schema.py` 唯一提供，主执行器只负责装配和调用。
- Chat Completions 请求准备已收口到纯输入输出模块 `agent/api_request.py`；`run_agent.py` 只快照当前运行态并编排调用，不再维护 Provider 参数、消息角色和 reasoning payload 的重复构建分支。
- Chat Completions 响应规范化已收口到 `agent/api_response.py`；主 Agent 与辅助调用共用可见文本清理和 reasoning 提取规则，tool-call / reasoning-details 的持久化结构也由该模块唯一生成。
- API 错误分类、展示摘要、限流上下文、Retry-After 和流中断判断统一由 `agent/error_classifier.py` 提供；CLI 不再探测 Agent 私有错误方法，主循环也不再保留重复提取实现或引用已删除的错误枚举。
- 凭据池恢复之后的重试动作由 `agent/retry_utils.py` 的不可变 `RetryDirective` 决定：限流回退、payload/context 恢复、非重试终止、连接重建和等待分支不再散落于主循环；confirmed billing 在凭据池与回退均不可用时不会继续重试同一凭据。
- context overflow 恢复计划已并入 `agent/context_compressor.py`：输出上限修正、真实窗口解析、probe tier 降级、压缩次数和探测持久化标志由不可变计划统一决定；命名压缩结果统一判断消息缩减或窗口降级是否取得进展，主循环只执行压缩、session 切换和状态输出。
- 记忆刷新回退、MoA 参考模型和聚合模型请求已统一通过 `agent/api_request.py` 构建；MoA 的自定义聚合模型会实际参与请求，输出上限、reasoning 和退役集成策略不再由工具私自拼装。
- API-A 主调用、辅助模型、模型切换和子 Agent 委派统一使用 OpenAI-compatible `chat.completions`；配置层不再保留协议探测、协议切换或专用响应适配字段。
- 辅助任务路由只读取显式调用参数与 `auxiliary.<task>`；配置版本 19 会把旧压缩摘要字段和辅助环境变量迁入该结构后删除旧来源，浏览器、Web、压缩器与启动器不再维护环境变量桥接或第二套摘要模型配置。
- 发行版本只在 `VoidCube_cli.__version__` 定义，setuptools、CLI、横幅和调试信息均读取该值；Python 3.11 最低版本由 `pyproject.toml`、开发文档与默认运行镜像共同约束。
- 国际化只由 `VoidCube_cli/i18n.py` 和 `VoidCube_cli/locales/*.json` 提供；旧的核心静态消息表及星号导出已删除，浏览器工具与其余 CLI 使用同一 locale、fallback 和格式化规则。
- `VoidCube_core` 根包只作为无副作用命名空间；常量、异常、日志、状态、时间与工具函数必须从所属子模块显式导入，不再维护失效 `__all__` 或星号重导出兼容面。
- 从未实际写文件的 `save_trajectories` 空实现已连同参数、帮助和死分支删除；仍可使用独立入口的 `save_sample` 导出单次样本。
- 主仓当前收集 848 项测试。日常修改先跑 smoke 和受影响模块，合并或发布前跑全量测试与构建。
- Mem 子系统当前有 108 项测试，需通过 `python -m pytest Mem/tests -q` 单独运行。

## 推荐阅读路径

定位架构职责：

1. `voidcube架构基线.md`
2. `全链路问题清单.md`
3. 相关组件代码和测试

定位实现入口：

1. `项目文件架构说明.md`
2. `voidcube.py`
3. `VoidCube_cli/main.py`
4. 目标子系统目录

追查旧名称：

1. 先在当前代码和问题清单确认它是否仍是正式协议
2. 仅在需要解释来源时读取 `全链条迁移日志.md`
3. 不把迁移文档中的兼容名称写回新接口、提示或主读模型

## 分阶段路线

### 阶段 1：工程基线

- 文档入口、架构事实和验证命令保持一致
- smoke 测试覆盖启动器、打包契约、核心自主闭环和工具分发
- 所有当前文档链接可解析，不引用已删除文件

### 阶段 2：主路径解耦

- 按调用关系拆分 `cli.py` 的会话 UI、命令分发、自主观察桥和 worktree 管理
- 按职责拆分 `run_agent.py` 的 provider 调用、工具循环、消息整理和会话持久化
- 每迁移一项职责，同轮删除原实现、旧参数和只服务旧路径的测试

### 阶段 3：运行态隔离

- 明确源码、可提交样例和本地运行数据的边界
- 将日志、会话、治理事件和构建产物统一收口到配置的数据目录
- 为现有本地数据提供一次性、可验证的迁移方式，不保留长期双写

### 阶段 4：子系统质量

- 按 Gateway、Memory、Supervisor、Execution、Agent、Tools 分组收敛接口
- 补齐失败路径、恢复路径和跨进程契约测试
- 对真实热点做性能测量后再优化，避免以文件体积代替运行瓶颈判断

### 阶段 5：发布验收

- 全量主仓与 Mem 测试通过
- wheel 构建、安装后导入和 console script 冒烟通过
- README、架构基线、问题清单与实际行为一致

## 维护规则

- 当前行为改变时，同一提交更新代码、聚焦测试和对应当前文档。
- 新实现替代旧路径后，删除旧分支、旧参数、旧提示和冗余测试；仅在外部持久化数据或公开协议确有迁移期时保留有期限的兼容层。
- 已完成事项移入问题清单的“已收口”；迁移原因只进入迁移日志，不在主设计中长期并列两套说法。
- 统计数字只用于描述审计时点，不作为架构约束；变更规模后应重新生成，而不是手工推断。
