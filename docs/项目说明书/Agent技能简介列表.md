# Agent 技能简介列表

> 共 **31** 个技能，按功能分类整理。每个技能的 SKILL.md 位于 `skills/<分类>/<技能名>/SKILL.md`。

---

## 一、GitHub 系列（6 个）

| 序号 | 技能名 | 描述 |
|------|--------|------|
| 1 | **github-auth** | 使用 git 或 gh CLI 为 Agent 设置 GitHub 认证。涵盖 HTTPS 令牌、SSH 密钥、凭据助手和 `gh auth`，带有自动检测流程。 |
| 2 | **github-repo-management** | 克隆、创建、fork、配置和管理 GitHub 仓库。管理远程、密钥、发布和工作流。 |
| 3 | **github-pr-workflow** | 完整的 Pull Request 生命周期——创建分支、提交变更、打开 PR、监控 CI 状态、自动修复失败并合并。 |
| 4 | **github-code-review** | 通过分析 git diff 审查代码变更，在 PR 上留下内联评论，执行彻底的推送前审查。 |
| 5 | **github-issues** | 创建、管理、分类和关闭 GitHub Issue。搜索已有 Issue、添加标签、分配人员并链接到 PR。 |
| 6 | **codebase-inspection** | 使用 pygount 检查和分析代码库，进行 LOC 计数、语言分解和代码与注释比率统计。 |

---

## 二、MLOps — 推理部署（6 个）

| 序号 | 技能名 | 描述 |
|------|--------|------|
| 7 | **vllm** | 使用 vLLM 的 PagedAttention 和连续批处理高吞吐量服务 LLM。支持 OpenAI 兼容端点、量化（GPTQ/AWQ/FP8）和张量并行。 |
| 8 | **llama-cpp** | 在 CPU、Apple Silicon 和消费级 GPU 上运行 LLM 推理，无需 NVIDIA 硬件。支持 GGUF 量化（1.5-8 位）。 |
| 9 | **gguf** | GGUF 格式和 llama.cpp 量化，用于高效的 CPU/GPU 推理。在消费级硬件、Apple Silicon 上部署模型。 |
| 10 | **guidance** | 使用正则表达式和语法控制 LLM 输出，保证有效的 JSON/XML/代码生成——Microsoft Research 的约束生成框架。 |
| 11 | **outlines** | 在生成期间保证有效的 JSON/XML/代码结构，使用 Pydantic 模型实现类型安全输出——dottxt.ai 的结构化生成库。 |
| 12 | **obliteratus** | 使用机制可解释性技术（差分均值、SVD、LEACE 等）从开放权重 LLM 中移除拒绝行为。9 种 CLI 方法、28 个分析模块。 |

---

## 三、MLOps — 训练微调（6 个）

| 序号 | 技能名 | 描述 |
|------|--------|------|
| 13 | **unsloth** | 使用 Unsloth 快速微调——2-5 倍更快训练、50-80% 更少内存、LoRA/QLoRA 优化。 |
| 14 | **axolotl** | 使用 Axolotl 微调 LLM——YAML 配置、100+ 模型、LoRA/QLoRA、DPO/KTO/ORPO/GRPO、多模态支持。 |
| 15 | **trl-fine-tuning** | 使用 TRL 通过强化学习微调 LLM——SFT、DPO、PPO/GRPO 及奖励模型训练。 |
| 16 | **peft** | LoRA、QLoRA 和 25+ 方法的参数高效微调。训练 <1% 参数，精度损失最小。HuggingFace 官方库。 |
| 17 | **pytorch-fsdp** | 使用 PyTorch FSDP 进行完全分片数据并行训练——参数分片、混合精度、CPU 卸载、FSDP2。 |
| 18 | **grpo-rl-training** | 使用 TRL 进行 GRPO/RL 微调的专家指导，用于推理和任务特定模型训练。 |

---

## 四、MLOps — 模型使用（5 个）

| 序号 | 技能名 | 描述 |
|------|--------|------|
| 19 | **stable-diffusion** | 通过 HuggingFace Diffusers 使用 Stable Diffusion 进行文本到图像生成、图像到图像转换、修复。 |
| 20 | **whisper** | OpenAI 通用语音识别模型。支持 99 种语言、转录、翻译。从 tiny(39M) 到 large(1550M) 六种尺寸。 |
| 21 | **clip** | OpenAI 的视觉-语言模型。零样本图像分类、图文匹配、跨模态检索。4 亿图文对训练。 |
| 22 | **segment-anything** | 基础模型，用于零样本图像分割。支持点/框/掩码提示分割任意对象。 |
| 23 | **audiocraft** | 音频生成 PyTorch 库——文本转音乐（MusicGen）、文本转声音（AudioGen）、旋律条件音乐生成。 |

---

## 五、MLOps — 评估 / 研究 / 云（4 个）

| 序号 | 技能名 | 描述 |
|------|--------|------|
| 24 | **lm-evaluation-harness** | 在 60+ 学术基准（MMLU、HumanEval、GSM8K、TruthfulQA 等）上评估 LLM。EleutherAI 的行业标准。 |
| 25 | **weights-and-biases** | W&B 实验跟踪平台——自动日志、实时可视化训练、超参数扫描优化、模型注册表管理。 |
| 26 | **dspy** | 使用声明式编程构建复杂 AI 系统，自动优化提示——Stanford NLP 的系统化 LM 编程框架。 |
| 27 | **modal** | 无服务器 GPU 云平台。按需 GPU 访问、ML 模型部署为 API、批处理作业自动扩展。 |

---

## 六、DevOps / 工具链（2 个）

| 序号 | 技能名 | 描述 |
|------|--------|------|
| 28 | **huggingface-hub** | Hugging Face Hub CLI (`hf`)——搜索、下载和上传模型与数据集，管理仓库，用 SQL 查询数据集，部署推理端点。 |
| 29 | **webhook-subscriptions** | 创建和管理 Webhook 订阅，实现事件驱动的 Agent 激活。外部服务变更自动触发 Agent 运行。 |

---

## 七、系统增强（2 个）

| 序号 | 技能名 | 描述 |
|------|--------|------|
| 30 | **self-learning** | 精妙的自学习系统——设定学习时间，自动搜索先进成熟技术，智能判断记忆价值，持续优化学习笔记。 |
| 31 | **windows-automation** | Windows 应用自动化控制——应用启动与窗口管理、控件扫描分析、UIA+OCR 智能识别、控件操作执行。 |

---

## 技能目录结构

```
skills/
├── github/
│   ├── github-auth/           # GitHub 认证
│   ├── github-repo-management/ # 仓库管理
│   ├── github-pr-workflow/    # PR 工作流
│   ├── github-code-review/    # 代码审查
│   ├── github-issues/         # Issue 管理
│   └── codebase-inspection/   # 代码库检查
├── mlops/
│   ├── inference/
│   │   ├── vllm/              # vLLM 推理
│   │   ├── llama-cpp/         # CPU/GPU 推理
│   │   ├── gguf/              # GGUF 量化
│   │   ├── guidance/          # 约束生成
│   │   ├── outlines/          # 结构化生成
│   │   └── obliteratus/       # 移除拒绝行为
│   ├── training/
│   │   ├── unsloth/           # 快速微调
│   │   ├── axolotl/           # YAML 配置微调
│   │   ├── trl-fine-tuning/   # RL 微调
│   │   ├── peft/              # 参数高效微调
│   │   ├── pytorch-fsdp/      # 分布式训练
│   │   └── grpo-rl-training/  # GRPO 训练
│   ├── models/
│   │   ├── stable-diffusion/  # 图像生成
│   │   ├── whisper/           # 语音识别
│   │   ├── clip/              # 视觉语言
│   │   ├── segment-anything/  # 图像分割
│   │   └── audiocraft/        # 音频生成
│   ├── evaluation/
│   │   ├── lm-evaluation-harness/ # 基准评估
│   │   └── weights-and-biases/    # 实验跟踪
│   ├── research/
│   │   └── dspy/              # 声明式 AI
│   └── cloud/
│       └── modal/             # 无服务器 GPU
├── devops/
│   └── webhook-subscriptions/ # Webhook 订阅
├── self-learning/             # 自学习系统
└── windows-automation/        # Windows 自动化
```
