# VoidCube

VoidCube 是一个本地运行的 Python 智能体系统：API-A Agent 负责 CLI 对话、工具调用和任务执行；Supervisor、Memory/MemAI 与 API-B 共同承载“星子”这一监督与伴侣人格。

## 当前运行模型

- `daily_companion`：系统启动后的默认模式。星子常驻运行，只读取 VoidCube 内部事件，等待或响应用户语音；只有证据充分且确有帮助时才提醒。
- `auto_evolution`：用户执行 `/auto` 后进入。暂停伴侣辅助和实时用户感知，执行记忆整理、自学习、自计划以及受治理的身体进化。`/auto-q` 收口任务并返回日常模式。

星子不是第二个独立 Agent 进程。它由 Supervisor 的判断与治理、Memory/MemAI 的记忆、API-B 的后台推理共同组成；实际工具副作用仍由 API-A Agent 和 Execution 完成。

## 记忆边界

Memory Service 使用单一存储和统一备份，但所有读写都带有 `owner_id`、`workspace_id`、`memory_domain` 与服务端 actor 权限校验。当前域为：

- `agent_interaction`：用户与 API-A 的会话、工具结果和任务事实。
- `companion`：日常星子语音语义、用户目标理解和伴侣偏好。
- `evolution`：Auto 模式的计划、实验、治理和身体 lineage。

日常星子可只读交互域；API-A 默认看不到伴侣域；Auto 只读写进化域。跨域信息必须通过可审计的提升引用传播，不能直接复制整段对话。

## 主要目录

```text
VoidCube/
├─ agent/                 API-A Agent、上下文、工具回合和记忆接入
├─ tools/                 工具注册、安全、审批和执行后端
├─ systems/gateway/       服务发现、路由、活动与任务泳道
├─ systems/memory/        Memory Service、召回、压缩、Profile 和索引
├─ systems/supervisor/    日常伴侣、Auto 驱动、治理投影和 UI
├─ systems/voice/         录音、声纹、STT/TTS 和可中断会话
├─ systems/execution/     Execution Facade、Adapter 与身体执行
├─ Mem/src/memai/         MemAI 领域包
├─ tests/                 主仓测试
└─ docs/                  架构与开发文档
```

## 安装与验证

项目需要 Python 3.14.x。开发安装：

```bash
python -m pip install -e ".[all,dev]"
python -m pytest -q
python -m pytest Mem/tests -q
```

语音能力是可选依赖：`edge-tts`、`faster-whisper`、`sherpa-onnx`、`numpy`、`sounddevice`、`soundfile`；支持按钮单轮会话和显式启停的持续监听，默认唤醒词为“你好，星子”。两种入口共用持久麦克风流和 Silero VAD，在连续 3 秒静音时提交完整话语，再进行声纹、STT、API-B 和 TTS；按钮会话点击后直接聆听并在一轮后结束，持续监听由本地 KWS 唤醒并在回复后返回待唤醒。STT 在未配置远程地址时使用本地 `faster-whisper base/int8`，默认以“你好 星子 西子 VoidCube 语音系统”为热词，也可通过 `VOIDCUBE_STT_HOTWORDS` 调整或切换至 OpenAI-compatible 音频转写端点；播放默认由 `soundfile + sounddevice` 在进程内完成，`ffplay` 仅作为兼容回退。声纹采用 3D-Speaker CAM++ 的 192 维说话人嵌入和三段录入模板，只承担本机说话人过滤；可通过 UI 或 `/voice/fingerprint` 临时关闭，关闭不会删除模板。原始音频默认不保留。

模型、鉴权、协议、技能或打包相关改动还必须运行：

```bash
python -m pytest tests/test_integration_policy.py tests/test_packaging_contract.py -q
```

完整架构和当前实现差距见 [docs/项目架构与逻辑架构.md](docs/项目架构与逻辑架构.md)。

## License

MIT
