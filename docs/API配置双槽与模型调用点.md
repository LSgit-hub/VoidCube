# API 配置双槽与模型调用点

API-A 和 API-B 是逻辑职责槽位：

| 槽位 | 用途 | 代码消费者 |
| --- | --- | --- |
| API-A | 用户对话、工具调用、自主任务执行 | `run_agent.py`、自主执行组件 |
| API-B | 日常伴侣对话/提醒判断、记忆抽取/压缩、Auto 治理判断 | `systems/memory`、`systems/supervisor`、辅助客户端 |

Provider 配置位于用户 `VOIDCUBE_HOME/config.yaml`，secret 位于对应 `.env`。请求统一由 `agent/api_request.py` 和 MemAI 的 OpenAI-compatible client 构造 Chat Completions；Provider 解析不能通过协议探测偷偷切换消息格式。

API-B 不是只在 `/auto` 时工作的后台模型。日常模式由它处理星子语音文本、内部事件理解和克制的提醒判断；Auto 模式则改用自学习/进化 prompt 和 `evolution` 记忆域。两种模式不能共享未标注的对话上下文。

扩展新模型时应同时检查 `agent/integration_policy.py`、`VoidCube_cli/providers.py`、`Mem/src/memai/model_config.py` 和 `tests/test_integration_policy.py`。
