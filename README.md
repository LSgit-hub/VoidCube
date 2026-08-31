# VoidCube

VoidCube 是一个本地运行的 Python 智能体系统：API-A Agent 负责 CLI 对话；Supervisor、Memory/MemAI 与 API-B 共同承载“星子”这一监督与伴侣人格。API-B 获准的后台任务统一派给配置好的员工代理执行并回写结果，不影响 API-B Agent用户通道。
<img width="1138" height="737" alt="屏幕截图 2026-08-31 125607" src="https://github.com/user-attachments/assets/b5b6046c-6964-4a71-ad71-296b9a2f91b2" />

## 当前运行模型

- `daily_companion`：系统启动后的默认模式。星子常驻运行，只读取 VoidCube 内部事件，等待或响应用户语音；只有证据充分且确有帮助时才提醒。
- `auto_evolution`：用户执行 `/auto` 后进入。暂停伴侣辅助和实时用户感知，执行记忆整理、自学习、自计划以及受治理的身体进化。`/auto-q` 收口任务并返回日常模式。

星子不是第二个独立 Agent 进程。它由 Supervisor 的判断与治理、Memory/MemAI 的记忆、API-B 的后台推理共同组成；用户对话由 API-A Agent 完成，API-B 的后台副作用由隔离员工代理完成。

## 模型 Provider 池

`/api` 是统一模型配置入口：`[1] 添加 Provider` 配置远程 OpenAI-compatible Provider，`[2] 本地模型（Ollama）` 探测本机 Ollama 并把模型目录写入同一个 `providers` 池。保存后，API-A、API-B 和员工代理都从该池选择 Provider/模型；Ollama 使用 `auth_mode: none` 和默认地址 `http://localhost:11434/v1`，不建立 CLI 专用旁路。

## 记忆边界

Memory Service 使用单一存储和统一备份，但所有读写都带有 `owner_id`、`workspace_id`、`memory_domain` 与服务端 actor 权限校验。当前域为：

- `agent_interaction`：用户与 API-A 的会话、工具结果和任务事实。
- `companion`：日常星子语音语义、用户目标理解和伴侣偏好。
- `evolution`：Auto 模式的计划、实验、治理和身体 lineage。

日常星子可只读交互域；API-A 默认看不到伴侣域；Auto 只读写进化域。API-B 获准任务通过员工队列执行并以 `autonomous_task_id` 回写。跨域信息必须通过可审计的提升引用传播，不能直接复制整段对话。

### 交付边界

Web 交付面板只服务日常 Assist 的用户可见临时产物，例如媒体播放、论文链接和即时文件。Auto 模式的自学习、研究和自改进由员工代理执行，结果回写 canonical task、治理事件和 `evolution` Mem，不进入 Web 交付面板。

## 主要目录

```text
VoidCube/
├─ src/voidcube/         唯一运行时包：domain、application、runtime、interfaces、systems、extensions
│  ├─ domain/             Agent/session/execution 领域模型与稳定 contracts
│  ├─ application/        对话、会话、调度和自治用例编排
│  ├─ runtime/agent/      API-A Agent runner、client/session bootstrap、回合运行时和执行期展示
│  ├─ interfaces/cli/     CLI launcher、application host 和配置交互边界
│  ├─ infrastructure/     Provider、配置、持久化、网络和执行适配器
│  ├─ systems/            Supervisor、evolution、research 和 voice 产品系统
│  └─ extensions/         plugin、skill、tool manifest/registry
├─ Mem/src/memai/         持久化记忆领域、应用、仓储、索引和可选 HTTP 服务
├─ plugins/memory/mem/    Agent 侧 Mem 插件注册、配置和协议适配
├─ tests/                 本地测试（不随远程仓库分发）
└─ docs/                  本地架构与开发文档（不随远程仓库分发）
```

运行时只从 `voidcube` 规范包加载；`VoidCube_app`、`VoidCube_cli`、`VoidCube_core`、顶层 `agent`、`tools` 和历史 `systems` 包不再属于发行版，也不提供兼容入口。`AIAgent` runner 唯一实现位于 `src/voidcube/runtime/agent/runner.py`。

目录职责、依赖方向和分阶段迁移方案见 [ARCHITECTURE.md](ARCHITECTURE.md)。

架构边界和 wheel 契约可在本地用 `scripts/python_architecture.py`、`tests/test_packaging_contract.py` 和 `scripts/build_wheel.py` 复现；同一组检查也由 CI 自动执行。

## 安装与验证

项目需要 Python 3.14.x。开发安装：

```bash
python -m pip install -e ".[all,dev]"
python -m pytest -q
python -m pytest Mem/tests -q
```

Windows 桌面端还需在系统 `PATH` 中提供 Tirith 安全扫描器。当前验证版本来自官方 `sheeki03/tirith` npm 包：

```powershell
npm install -g tirith@0.3.3
tirith check --json --non-interactive --shell posix -- "echo hello"
```

Linux 和 macOS 在未找到 Tirith 时会校验发布校验和并安装到 VoidCube 运行目录；也可以通过 `TIRITH_BIN` 显式指定已安装的可执行文件。

根目录 `tests/` 仅保留在开发机用于本地验证，已加入 `.gitignore`，不会提交到远程仓库；`Mem/tests/` 仍是远程保留的记忆领域测试。

## 桌面端

`desktop/` 提供 Electron 跨平台桌面容器：上半区加载 Supervisor Web UI，下半区通过 `xterm.js + node-pty` 运行真实 VoidCube CLI，并支持拖动调整分区、连接状态、页面重载和 CLI 重启。

```bash
cd desktop
npm install
npm run dev
```

开发、测试、环境变量和打包说明见 [desktop/README.md](desktop/README.md)。

语音能力是可选依赖：`edge-tts`、`faster-whisper`、`sherpa-onnx`、`numpy`、`sounddevice`、`soundfile`；支持按钮单轮会话和显式启停的持续监听，默认唤醒词为“你好，星子”。两种入口共用持久麦克风流和 Silero VAD，在连续 3 秒静音时提交完整话语，再进行声纹、STT、API-B 和 TTS；按钮会话点击后直接聆听并在一轮后结束，持续监听由本地 KWS 唤醒并在回复后返回待唤醒。STT 在未配置远程地址时使用本地 `faster-whisper base/int8`，默认以“你好 星子 西子 VoidCube 语音系统”为热词，也可通过 `VOIDCUBE_STT_HOTWORDS` 调整或切换至 OpenAI-compatible 音频转写端点；播放默认由 `soundfile + sounddevice` 在进程内完成，`ffplay` 仅作为外部播放器回退。声纹采用 3D-Speaker CAM++ 的 192 维说话人嵌入和三段录入模板，只承担本机说话人过滤；可通过 UI 或 `/voice/fingerprint` 临时关闭，关闭不会删除模板。原始音频默认不保留。

模型、鉴权、协议、技能或打包相关改动还必须运行：

```bash
python -m pytest tests/test_integration_policy.py tests/test_packaging_contract.py -q
```

完整架构和当前实现差距见开发机本地 `docs/` 目录；该目录与 `tests/` 一样不随远程仓库分发。

## License

[MIT License](LICENSE) —— 本项目采用 MIT 许可证开源，允许自由使用、修改和分发。
