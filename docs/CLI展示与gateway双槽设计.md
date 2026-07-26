# CLI 展示与 Gateway 双泳道设计

Gateway 的 agent scene 固定包含两个 lane：

- `user_chat`：用户在 CLI 中发起的会话、工具调用和结果。
- `supervisor_task`：Supervisor 转交给 API-A 的自主任务。

`InternalGateway` 只收集和投影各服务上报的 scene，不替代服务的状态机。CLI 的 `chat_render_state.py`、`chat_stream_processor.py` 和 `chat_stream_renderer.py` 负责展示，`cli.py` 负责接回调和命令。

两条泳道不能覆盖彼此的任务、计数或焦点；`/auto` 只影响自主泳道，不把用户输入切换成自主任务。

星子的 `daily_companion` / `auto_evolution` 是 Supervisor 模式，不是第三条 Agent lane。日常语音属于星子 companion session；API-A CLI 仍属于 `user_chat`。进入 Auto 后 Supervisor 停止 companion worker，并使用 `supervisor_task` 转交自主任务；退出后再恢复 companion worker。

UI 继续使用 `/ui/state` 快照和 `/ui/events` SSE。模式切换、麦克风开关和 TTS 控制使用 HTTP 命令，暂不增加 WebSocket。
