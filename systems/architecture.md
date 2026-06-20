# VoidCube 服务化架构说明

`systems/` 目录的服务化目标架构已统一收口到：

- [docs/architecture-integration.md](../docs/architecture-integration.md)

这份入口文件只保留两点说明：

## 1. 当前目录的职责

`systems/` 用于承载 VoidCube 的服务化运行链路，包括：

- 内部网关
- 记忆服务
- 监督治理
- 身体运行时
- 服务化 Agent 实例

## 2. 架构约束

从当前版本开始，`systems/` 的目标方向以 `docs/architecture-integration.md` 为准，尤其是以下边界：

- 监督者是裁决者，不是长期执行器
- Agent 保持无状态
- 记忆服务承担长期持久化
- 自学系统只研究，不直接上线
- 自提升只在时间窗口内由裁决后放行

如果代码实现与该文档冲突，应优先按文档方向整理实现，而不是继续扩张旧职责。
