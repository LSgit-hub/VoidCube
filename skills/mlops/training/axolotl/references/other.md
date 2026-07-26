# Axolotl - 其他

**页面：** 26

---

## 混合精度训练

**URL：** https://docs.axolotl.ai/docs/mixed_precision.html

**内容：**
- 混合精度训练
- 1 FP16 混合精度
  - 1.1 概述
  - 1.2 配置
  - 1.3 FP16 注意事项
- 2 BF16 混合精度
  - 2.1 概述
  - 2.2 配置
- 3 FP8 混合精度
  - 3.1 什么是 FP8？

混合精度训练使用较低精度的数据类型来减少内存使用并提高训练速度，同时保持模型质量。Axolotl 支持多种混合精度格式：

FP16 是传统的半精度格式，在旧 GPU 上受支持，但数值稳定性可能不如 BF16。

BF16（Brain Float 16）提供比 FP16 更好的数值稳定性，是现代 GPU 推荐的混合精度格式。它提供与 FP32 相同的动态范围，同时使用一半的内存。

FP8 支持是实验性的，需要兼容的硬件（H100、H200）和带有 TorchAO 的最新 PyTorch 版本。

FP8（8 位浮点）可以比 FP16/BF16 提供显著的时间节省，同时保持训练稳定性。Axolotl 的实现使用 PyTorch 的 TorchAO 库，采用"tensorwise"缩放策略。

添加到您的 YAML 配置：

torch.compile 对 FP8 性能至关重要

FP8 训练需要 torch_compile: true 才能看到有意义的加速。没有编译，FP8 实际上可能比 FP16/BF16 更慢并使用更多内存。

对于 FSDP（完全分片数据并行）训练：

始终验证您的混合精度设置：

请参阅 examples/llama-3/3b-fp8-fsdp2.yaml 获取优化的示例配置。启用 FP8 混合精度 + FP8 all-gather 训练可使相对较小的（3B 参数）模型的每秒迭代次数比 BF16 快约 10%

有关多 GPU 训练的更多信息，请参阅我们的多 GPU 指南。

**示例：**

示例 1 (yaml)：
```yaml
# 自动 BF16 检测（推荐）
bf16: auto

# 或显式启用
bf16: true

# 用于 BF16 评估
bf16: full  # 等同于 HF trainer 中的 bf16_full_eval
```

示例 2 (yaml)：
```yaml
# 启用 FP8 混合精度
fp8: true

# 可选：为 FSDP all-gather 操作启用 FP8
fp8_enable_fsdp_float8_all_gather: true

# 启用 torch.compile（FP8 加速几乎总是必需的）
torch_compile: true
```

示例 3 (yaml)：
```yaml
fp8: true
fp8_enable_fsdp_float8_all_gather: true

torch_compile: true

# FSDP 配置
fsdp_version: 2
fsdp_config:
  offload_params: false
  cpu_ram_efficient_loading: true
  auto_wrap_policy: TRANSFORMER_BASED_WRAP
  transformer_layer_cls_to_wrap: LlamaDecoderLayer
  state_dict_type: FULL_STATE_DICT
  reshard_after_forward: true
```

---

## FAQ

**URL：** https://docs.axolotl.ai/docs/faq.html

**内容：**
- FAQ
  - 通用
  - 聊天模板

问：训练器停止了几分钟没有进展。

答：通常是 GPU 之间通信的问题。请参阅 NCCL 文档

答：这通常发生在系统 RAM 不足时。

问：使用 deepspeed 时 exitcode: -7

答：尝试升级 deepspeed：pip install -U deepspeed

问：AttributeError: 'DummyOptim' object has no attribute 'step'

问：使用单 GPU 和 deepspeed 时 ModuleNotFoundError: No module named 'mpi4py'

答：您可能在使用单 GPU 的 deepspeed。请删除 yaml 文件中的 deepspeed: 部分或 --deepspeed CLI 标志。

问：代码卡在保存预处理数据集。

答：这通常是 GPU 的问题。可以通过设置 os 环境变量 CUDA_VISIBLE_DEVICES=0 来解决。如果您在 runpod 上，这通常是 pod 的问题。启动新的 pod 应该可以解决。

问：在合并适配器/加载适配器时收到 torch.Size 检查点和模型不匹配错误。

答：这可能是由于词汇大小不匹配。默认情况下，如果分词器的 token 比模型多，Axolotl 会扩展模型的嵌入。请使用 axolotl merge-lora 命令合并适配器，而不是使用自己的脚本。

另一方面，如果模型的 token 比分词器多，Axolotl 不会缩小模型的嵌入，除非在配置中设置 shrink_embeddings: true。

问：如何通过自定义 python 脚本调用 Axolotl？

答：由于 Axolotl 只是 Python，请参阅 src/axolotl/cli/main.py 了解每个命令是如何调用的。

问：如何知道 fsdp_transformer_layer_cls_to_wrap 使用什么值？

答：这是要用 FSDP 包装的 transformer 层的类名。例如，对于 LlamaForCausalLM，值是 LlamaDecoderLayer。要为特定模型找到这个值，请检查模型的 PreTrainedModel 定义，并在 transformers 库中的 modeling_<model_name>.py 文件中查找 _no_split_modules 变量。

问：ValueError: Asking to pad but the tokenizer does not have a padding token. Please select a token to use as pad_token

答：这是因为分词器没有填充 token。请通过以下方式向分词器添加填充 token：

问：使用 preprocess CLI 时出现 IterableDataset 错误或 KeyError: 'input_ids'

答：这是因为您可能分别对 pretraining_dataset: 或 skip_prepare_dataset: true 使用了 preprocess CLI。请直接使用 axolotl train CLI，因为这些数据集是按需准备的。

问：vLLM 无法与 Axolotl 一起工作

答：我们目前推荐 torch 2.6.0 用于 vllm。请确保使用正确的版本。对于 Docker，请使用 main-py3.14-cu124-2.6.0 标签。

问：FA2 2.8.0 在 CUDA 12.4 上出现 undefined symbol 运行时错误

答：FA2 2.8.0 在 CUDA 12.4 上似乎有 wheel 问题。尝试使用 CUDA 12.6 或降级到 FA2 2.7.4。请参考上游问题：https://github.com/Dao-AILab/flash-attention/issues/1717。

问：我们可以为 VLM 训练混合文本和文本+图像数据集吗？

答：可以，对于较新的 VLM 架构可以。不适用的是 LLaVA / Pixtral 架构。如果您发现某个不工作，请告诉我们！

问：为什么 memory/max_* 与 nvidia-smi 不同？

答：我们使用 torch API 检索此信息。您可以参阅 https://docs.pytorch.org/docs/stable/notes/cuda.html#cuda-memory-management 了解更多信息。

问：jinja2.exceptions.UndefinedError: 'dict object' has no attribute 'content' / 'role' / ____

答：这意味着在构建 chat_template 提示时，所述属性不存在属性映射。例如，如果没有属性 'content'，请检查您是否在 message_property_mappings 下为 content 添加了正确的映射。

问：Empty template generated for turn ___

答：该轮次的内容为空。

问：Could not find content start/end boundary for turn __

答：无法检测特定轮次的开始/结束。请确保您已根据 chat_template 设置了 eos_token。否则，这可能是 chat_template 没有为每轮使用正确的边界（如 system）。在罕见情况下，请确保您的内容不是 [[dummy_message]]。请让我们知道这种情况。

问：Content end boundary is before start boundary for turn ___

答：这是不应发生的边缘情况。如果发生这种情况，请创建 Issue。

问：Content end boundary is the same as start boundary for turn ___. This is likely an empty turn.

答：这可能是空轮次。

问：EOS token 被错误地屏蔽或未被屏蔽 / EOS token __ not found in chat template.

答：可能有两个原因：

问："chat_template choice is tokenizer_default but tokenizer's chat_template is null. Please add a chat_template in tokenizer config"

答：这是因为分词器没有聊天模板。请在分词器配置中添加聊天模板。请参阅 chat_template 了解更多详情。

问：EOT token(s) 被错误地屏蔽或未被屏蔽 / EOT token __ not found in chat template.

答：可能有两个原因：

问：EOT token encoding failed. Please check if the token is valid and can be encoded.

答：可能是分词器或 unicode 编码的问题。请提出带有导致问题的 EOT token 和分词器示例的问题。

问：EOT token __ is encoded as multiple tokens.

答：这是因为 EOT token 被编码为多个 token，这可能导致意外行为。请在 tokens: 下添加它，或（推荐）通过 added_tokens_overrides: 覆盖未使用的 added_tokens。

问：Conflict between train_on_eos and train_on_eot. eos_token is in eot_tokens and train_on_eos != train_on_eot

答：这是因为 EOS token 在 eot_tokens: 中，而 train_on_eos: 和 train_on_eot: 之间存在不匹配。这将导致一个覆盖另一个。请确保 train_on_eos: 和 train_on_eot: 相同，或从 eot_tokens: 中删除 EOS token。

问：如果未提供 eot_tokens:，会发生什么？

答：如果未提供 eot_tokens:，默认行为与之前相同。用于分隔轮次的 EOS token 根据轮次是否可训练来屏蔽/取消屏蔽。

在内部，eot_tokens: tokenizer.eos_token 和 train_on_eot: train_on_eos（默认为 turn）。此转换有助于阐明 EOT/EOS token 的命名和行为。

问：Data processing error: CAS service error

答：尝试使用 export HF_HUB_DISABLE_XET=1 禁用 XET

问：torch._inductor.exc.LoweringException: NoValidChoicesError: No choices to select, please consider adding ATEN into max_autotune_gemm_backends config (defined in torch/_inductor/config.py) to allow at least one choice.

答：根据 torch 版本，您可能需要在 YAML 中包含：

**问：ValueError("Backward pass should have cleared tracker of all tensors")

答：这可能是由于在 CUDA 流中使用现代 OffloadActivations 上下文管理器的边缘情况。如果遇到此错误，您可以尝试在 YAML 中使用 naive 实现 offload_activations: legacy。

**问：Error parsing tool_calls arguments as JSON.

答：将字符串参数解析为 dict 时出错。请检查您的数据集和错误消息以获取更多详情。

**示例：**

示例 1 (yaml)：
```yaml
special_tokens:
  # str. 如果不确定，设置为与 `eos_token` 相同。
  pad_token: "..."
```

示例 2 (yaml)：
```yaml
flex_attn_compile_kwargs:
  dynamic: false
  mode: max-autotune-no-cudagraphs
```

---

## 安装

**URL：** https://docs.axolotl.ai/docs/installation.html

**内容：**
- 安装
- 1 要求
- 2 安装方法
  - 2.1 PyPI 安装（推荐）
  - 2.2 uv 安装
  - 2.3 边缘/开发版本
  - 2.4 Docker
- 3 云环境
  - 3.1 云 GPU 提供商
  - 3.2 Google Colab

本指南涵盖了为您的环境安装和设置 Axolotl 的所有方法。

请确保在本地环境中安装 Axolotl 之前已安装 Pytorch。

按照以下地址的说明操作：https://pytorch.org/get-started/locally/

对于 Blackwell GPU，请使用 Pytorch 2.7.0 和 CUDA 12.8。

我们使用 --no-build-isolation 来检测已安装的 PyTorch 版本（如果已安装），以免覆盖它，并设置特定于 PyTorch 版本或其他已安装共依赖项的正确依赖版本。

uv 是一个用 Rust 构建的快速、可靠的 Python 包安装器和解析器。它比 pip 提供显著的性能改进，并提供更好的依赖解析，使其成为复杂环境的绝佳选择。

如果尚未安装，请安装 uv

选择要与 PyTorch 一起使用的 CUDA 版本；例如 cu124、cu126、cu128，然后创建 venv 并激活

安装 PyTorch - 推荐 PyTorch 2.6.0

从 PyPi 安装 axolotl

对于版本之间的最新功能：

对于使用 Docker 进行开发：

对于 Blackwell GPU，请使用 axolotlai/axolotl:main-py3.14-cu128-2.7.0 或云变体 axolotlai/axolotl-cloud:main-py3.14-cu128-2.7.0。

请参阅 Docker 文档了解可用的不同 Docker 镜像的更多信息。

对于支持 Docker 的提供商：

请参阅第 6 节了解 Mac 特定问题。

我们推荐使用 WSL2（Windows Subsystem for Linux）或 Docker。

安装 PyTorch：https://pytorch.org/get-started/locally/

（可选）登录 Hugging Face：

如果遇到安装问题，请参阅我们的 FAQ 和调试指南。

**示例：**

示例 1 (bash)：
```bash
pip3 install -U packaging setuptools wheel ninja
pip3 install --no-build-isolation axolotl[flash-attn,deepspeed]
```

示例 2 (bash)：
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
```

示例 3 (bash)：
```bash
export UV_TORCH_BACKEND=cu126
uv venv --no-project --relocatable
source .venv/bin/activate
```

示例 4 (bash)：
```bash
uv pip install packaging setuptools wheel
uv pip install torch==2.6.0
uv pip install awscli pydantic
```

---

## 数据集预处理

**URL：** https://docs.axolotl.ai/docs/dataset_preprocessing.html

**内容：**
- 数据集预处理
- 概述
  - 预处理有什么好处？
  - 有哪些边缘情况？

数据集预处理是 Axolotl 根据您配置的每个数据集以及数据集格式和提示策略来执行的步骤：

数据集的处理可以通过两种方式之一发生：

当交互式训练或进行扫描时（例如您经常重新启动训练器），处理数据集通常会很慢。预处理将根据依赖训练参数的哈希缓存分词/格式化的数据集，以便在可能时智能地从缓存中拉取。

缓存的路径由 dataset_prepared_path: 控制，通常在示例 YAML 中留空，因为这会导致更稳健的解决方案，防止意外重用缓存数据。

如果 dataset_prepared_path: 留空，训练时，处理后的数据集将缓存到默认路径 ./last_run_prepared/，但会忽略那里已缓存的任何内容。通过显式设置 dataset_prepared_path: ./last_run_prepared，训练器将使用缓存中的任何预处理数据。

假设您正在编写自定义提示策略或使用用户定义的提示模板。因为训练器无法轻易检测到这些更改，我们无法更改预处理数据集的计算哈希值。

如果您设置了 dataset_prepared_path: ... 并更改了提示模板逻辑，它可能不会获取您所做的更改，您将在旧提示上进行训练。

---

## 推理和合并

**URL：** https://docs.axolotl.ai/docs/inference.html

**内容：**
- 推理和合并
- 1 快速开始
  - 1.1 基本推理
- 2 高级用法
  - 2.1 Gradio 界面
  - 2.2 基于文件的提示
  - 2.3 内存优化
- 3 合并 LoRA 权重
  - 3.1 合并的内存管理
- 4 分词

本指南涵盖如何使用训练好的模型进行推理，包括模型加载、交互式测试、合并适配器和常见故障排除步骤。

使用训练时使用的相同配置进行推理/合并。

启动交互式 Web 界面：

从文本文件处理提示：

对于大型模型或有限内存：

将 LoRA 适配器与基础模型合并：

训练和推理之间的分词不匹配是常见的问题来源。

在模型输入之前通过解码 token 验证推理分词

比较训练和推理之间的 token ID

在 YAML 中配置特殊 token：

有关更多详情，请参阅我们的调试指南。

**示例：**

示例 1 (bash)：
```bash
axolotl inference your_config.yml --lora-model-dir="./lora-output-dir"
```

示例 2 (bash)：
```bash
axolotl inference your_config.yml --base-model="./completed-model"
```

示例 3 (bash)：
```bash
axolotl inference your_config.yml --gradio
```

示例 4 (bash)：
```bash
cat /tmp/prompt.txt | axolotl inference your_config.yml \
  --base-model="./completed-model" --prompter=None
```

---

## 多模态 / 视觉语言模型 (BETA)

**URL：** https://docs.axolotl.ai/docs/multimodal.html

**内容：**
- 多模态 / 视觉语言模型 (BETA)
- 支持的模型
- 用法
  - Mllama
  - Llama4
  - Pixtral
  - Llava-1.5
  - Mistral-Small-3.1
  - Magistral-Small-2509
  - Voxtral

多模态支持有限，没有完全的功能对等性。

以下是微调多模态模型所需的超参数。

请参阅 examples 文件夹获取完整配置。

我们的一些 chat_template 已扩展以支持更广泛的数据集类型。这不应破坏任何现有配置。

目前，我们不会根据 sequence_len 截断或丢弃样本，因为每种架构处理非文本 token 的方式不同。我们正在寻求这方面的帮助。

请确保通过 pip install 'mistral-common[opencv]==1.8.5' 安装视觉库

请确保通过 pip install 'mistral-common[opencv]==1.8.5' 安装视觉库

请确保通过 pip3 install librosa==0.11.0 'mistral_common[audio]==1.8.3' 安装音频库

Gemma3-1B 模型是纯文本模型，请作为常规文本模型训练。

对于多模态 4B/12B/27B 模型，使用以下配置：

模型的初始损失和梯度范数会非常高。我们怀疑这是由于视觉层中的 Conv 造成的。

请确保通过 pip3 install timm==1.0.17 安装 timm

请确保通过 pip3 install num2words==0.5.14 安装 num2words

请通过 pip3 uninstall -y causal-conv1d 卸载 causal-conv1d

对于多模态数据集，我们采用类似于 OpenAI 消息格式的扩展 chat_template 格式。

为了向后兼容：

对于图像加载，您可以在 content 中与 "type": "image" 一起使用以下键：

对于音频加载，您可以在 content 中与 "type": "audio" 一起使用以下键：

您可能需要通过 pip3 install librosa==0.11.0 安装 librosa。

目前这还没有很好地测试。我们欢迎贡献者！

对于视频加载，您可以在 content 中与 "type": "video" 一起使用以下键：

以下是多模态数据集的示例：

PIL 无法使用 requests 在 url 检索文件。请检查拼写错误。一个替代原因是请求被服务器阻止。

**示例：**

示例 1 (yaml)：
```yaml
processor_type: AutoProcessor

skip_prepare_dataset: true
remove_unused_columns: false  # 保留列，因为训练期间处理图像嵌入时需要它们
sample_packing: false  # 多模态尚不支持

chat_template:  # 如果指定，请参阅下一节

# 示例数据集
datasets:
  - path: HuggingFaceH4/llava-instruct-mix-vsft
    type: chat_template
    split: train[:1%]

# （可选）如果使用 lora，仅微调语言模型，
# 保持视觉模型和视觉塔冻结
# load_in_8bit: true
adapter: lora
lora_target_modules: 'model.language_model.layers.[\d]+.(mlp|cross_attn|self_attn).(up|down|gate|q|k|v|o)_proj'

# （可选）如果要将图像调整为固定大小
image_size: 512
image_resize_algorithm: bilinear
```

示例 2 (yaml)：
```yaml
base_model: meta-llama/Llama-3.2-11B-Vision-Instruct

chat_template: llama3_2_vision
```

示例 3 (yaml)：
```yaml
base_model: meta-llama/Llama-4-Scout-17B-16E-Instruct

chat_template: llama4
```

示例 4 (yaml)：
```yaml
base_model: mistralai/Pixtral-12B-2409

chat_template: pixtral
```

---

## 奖励建模

**URL：** https://docs.axolotl.ai/docs/reward_modelling.html

**内容：**
- 奖励建模
  - 概述
  - （结果）奖励模型
  - 过程奖励模型 (PRM)

奖励建模是一种用于训练模型预测给定输入的奖励或价值的技术。这在强化学习场景中特别有用，模型需要评估其动作或预测的质量。我们支持 trl 支持的奖励建模技术。

结果奖励模型使用包含用户和模型之间整个交互的偏好注释的数据进行训练（例如，而不是每轮或每步）。为了提高训练稳定性，您可以使用 center_rewards_coefficient 参数来鼓励均零奖励输出（请参阅 TRL 文档）。

Bradley-Terry 聊天模板期望以下格式的单轮对话：

请查看我们的 PRM 博客。

过程奖励模型使用包含一系列交互中每一步的偏好注释的数据进行训练。通常，PRM 被训练为在推理轨迹的每一步上提供奖励信号，并用于下游强化学习。

请参阅 stepwise_supervised 了解数据集格式的更多详情。

**示例：**

示例 1 (yaml)：
```yaml
base_model: google/gemma-2-2b
model_type: AutoModelForSequenceClassification
num_labels: 1
tokenizer_type: AutoTokenizer

reward_model: true
chat_template: gemma
datasets:
  - path: argilla/distilabel-intel-orca-dpo-pairs
    type: bradley_terry.chat_template

val_set_size: 0.1
eval_steps: 100
```

示例 2 (json)：
```json
{
    "system": "...", // 可选
    "input": "...",
    "chosen": "...",
    "rejected": "..."
}
```

示例 3 (yaml)：
```yaml
base_model: Qwen/Qwen2.5-3B
model_type: AutoModelForTokenClassification
num_labels: 2

process_reward_model: true
datasets:
  - path: trl-lib/math_shepherd
    type: stepwise_supervised
    split: train

val_set_size: 0.1
eval_steps: 100
```

---

## RLHF (Beta)

**URL：** https://docs.axolotl.ai/docs/rlhf.html

**内容：**
- RLHF (Beta)
- 概述
- 使用 Axolotl 进行 RLHF
  - DPO
    - chatml.argilla
    - chatml.argilla_chat
    - chatml.icr
    - chatml.intel
    - chatml.prompt_pairs
    - chatml.ultra

来自人类反馈的强化学习是一种使用人类反馈从数据优化语言模型的方法。各种方法包括但不限于：

这是一个 BETA 功能，许多功能尚未完全实现。我们鼓励提交新的 PR 来改进集成和功能。

我们依赖 TRL 库来实现各种 RL 训练方法，我们将其包装以在 axolotl 中公开。每种方法都有自己支持的数据集加载方式和提示格式。

您可以通过进入 src/axolotl/prompt_strategies/{method} 找到每种方法支持的内容，其中 {method} 是我们支持的方法之一。type: 可以从 {method}.{function_name} 检索。

DPO 支持以下类型和以下数据集格式：

对于自定义行为，

输入格式是简单的 JSON 输入，具有基于上述配置的可自定义字段。

由于 IPO 只是使用不同损失函数的 DPO，DPO 的所有支持数据集格式也支持 IPO。

论文：https://arxiv.org/abs/2403.07691

ORPO 支持以下类型和以下数据集格式：

KTO 支持以下类型和以下数据集格式：

对于自定义行为，

输入格式是简单的 JSON 输入，具有基于上述配置的可自定义字段。

请查看我们的 GRPO cookbook。

在最新的 GRPO 实现中，vLLM 用于显著加速训练期间的轨迹生成。在此示例中，我们使用 4 个 GPU - 2 个用于训练，2 个用于 vLLM：

请确保通过在安装 axolotl 时将其作为额外组件包含来安装正确版本的 vLLM，例如 pip install axolotl[vllm]。

您的 vLLM 实例现在将尝试启动，是时候利用剩余的两个 GPU 开始训练了。在另一个终端中，执行：

由于 TRL 使用 vLLM 的实现，vLLM 实例必须使用最后 N 个 GPU 而不是前 N 个 GPU。这就是为什么在上面的示例中，我们对 vLLM 实例使用 CUDA_VISIBLE_DEVICES=2,3。

GRPO 使用自定义奖励函数和转换。请在本地准备好它们。

例如，要加载 OpenAI 的 GSM8K 并为补全使用随机奖励：

要查看自定义奖励函数的其他示例，请参阅 TRL GRPO 文档。

要查看所有配置，请参阅 TRLConfig。

DAPO 论文以及随后的 Dr. GRPO 论文提出了 GRPO 的替代损失函数，以补救较长响应中的惩罚。

有关更多信息，请参阅 GRPO 文档。

SimPO 使用 CPOTrainer 但使用替代损失函数。

此方法使用与 DPO 相同的数据集格式。

TRL 支持为依赖参考模型的 RL 训练范式自动解包 PEFT 模型。这显著减少了内存压力，因为不需要加载额外的参考模型，并且可以通过禁用 PEFT 适配器获得参考模型对数概率。默认情况下启用此功能。要关闭它，请传递以下配置：

**示例：**

示例 1 (yaml)：
```yaml
rl: dpo
datasets:
  - path: Intel/orca_dpo_pairs
    split: train
    type: chatml.intel
  - path: argilla/ultrafeedback-binarized-preferences
    split: train
    type: chatml
```

示例 2 (json)：
```json
{
    "system": "...", // 可选
    "instruction": "...",
    "chosen_response": "...",
    "rejected_response": "..."
}
```

示例 3 (json)：
```json
{
    "chosen": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."}
    ],
    "rejected": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."}
    ]
}
```

示例 4 (json)：
```json
{
    "system": "...", // 可选
    "input": "...",
    "chosen": "...",
    "rejected": "..."
}
```

---

## LoRA 优化

**URL：** https://docs.axolotl.ai/docs/lora_optims.html

**内容：**
- LoRA 优化
- 用法
- 要求
- 实现细节
  - 自定义 autograd 函数
  - Triton 内核
  - 集成
- 未来工作

受 Unsloth 启发，我们为 LoRA 和 QLoRA 微调实现了两个优化，支持单 GPU 和多 GPU（包括 DDP、DeepSpeed 和 FSDP2 设置）训练。这些包括 (1) SwiGLU 和 GEGLU 激活函数 Triton 内核，以及 (2) LoRA MLP 和注意力自定义 autograd 函数。我们的目标是利用算子融合和张量重用来提高速度并减少这些计算的前向和反向传播期间的内存使用。

我们目前支持几种常见的模型架构，包括（但不限于）：

我们支持的模型集目前受限于我们的注意力修补策略，该策略假设（并替换）用于查询/键/值和输出投影的特定代码块：

其中 apply_qkv 和 apply_o 在 axolotl.kernels.lora 模块中定义。

我们欢迎测试其他模型架构和/或 PR 来扩展我们的修补逻辑以兼容更多架构。

请查看我们的 LoRA 优化博客。

这些优化可以在您的 Axolotl 配置 YAML 文件中启用。lora_mlp_kernel 选项启用优化的 MLP 路径，而 lora_qkv_kernel 和 lora_o_kernel 分别启用融合的查询-键-值投影和优化的输出投影。

目前，LoRA 内核不支持 RLHF 训练，仅支持 SFT。

具有使用 Dropout 或有偏置项的预存在 LoRA 适配器的模型可能需要在没有这些功能的情况下重新微调才能有用。

LoRA MLP autograd 函数优化整个 MLP 计算路径。它将 LoRA 和基础权重计算融合在一起，并为整个 MLP 块提供单一、高效的反向传播。

对于注意力组件，通过处理查询、键和值投影的函数以及处理输出投影的函数提供类似的优化。它们设计为通过一些猴子修补逻辑与现有的 transformers 注意力实现一起工作。

两个激活函数（SwiGLU 和 GeGLU）使用 Triton 内核实现，以提高速度和内存性能。这些内核处理前向和反向传播。

自定义 autograd 函数和 Triton 内核设计为协同工作。autograd 函数管理高级计算流程和梯度跟踪，同时调用 Triton 内核进行激活函数计算。在反向传播期间，内核计算激活输出和所需梯度，autograd 函数随后使用这些来计算整个计算路径的最终梯度。

**示例：**

示例 1 (python)：
```python
ORIGINAL_QKV_CODE = """
    query_states = self.q_proj(hidden_states).view(hidden_shape).transpose(1, 2)
    key_states = self.k_proj(hidden_states).view(hidden_shape).transpose(1, 2)
    value_states = self.v_proj(hidden_states).view(hidden_shape).transpose(1, 2)
""".lstrip(
    "\n"
)

ORIGINAL_O_CODE = """
    attn_output = self.o_proj(attn_output)
""".lstrip(
    "\n"
)
```

示例 2 (python)：
```python
PATCHED_QKV_CODE = """
    query_states, key_states, value_states = self.apply_qkv(hidden_states)
    query_states = query_states.view(hidden_shape).transpose(1, 2)
    key_states = key_states.view(hidden_shape).transpose(1, 2)
    value_states = value_states.view(hidden_shape).transpose(1, 2)
""".lstrip(
    "\n"
)

PATCHED_O_CODE = """
    attn_output = self.apply_o(attn_output)
""".lstrip(
    "\n"
)
```

示例 3 (yaml)：
```yaml
lora_mlp_kernel: true
lora_qkv_kernel: true
lora_o_kernel: true
```

---

## 使用 torchao 量化

**URL：** https://docs.axolotl.ai/docs/quantize.html

**内容：**
- 使用 torchao 量化
- 在 Axolotl 中配置量化

量化是一种降低模型内存占用的技术，可能以准确性或模型性能为代价。我们支持使用 torchao 库量化模型。量化支持训练后量化 (PTQ) 和量化感知训练 (QAT)。

我们目前不支持 GGUF/GPTQ、EXL2 等量化技术。

量化使用配置文件中的 quantization 键进行配置。

量化完成后，您的量化模型将保存在 {output_dir}/quantized 目录中。

您还可以使用 quantize 命令量化已使用 QAT 训练的模型 - 您可以通过使用用于训练模型的现有 QAT 配置文件来执行此操作：

这确保使用与训练模型相同的量化配置来量化模型。

如果您配置了使用 hub_model_id 推送到 hub，您的模型 hub 名称将附加量化模式，例如 axolotl-ai-cloud/qat-nvfp4-llama3B 将变为 axolotl-ai-cloud/qat-nvfp4-llama3B-nvfp4w

**示例：**

示例 1 (yaml)：
```yaml
base_model: # 要量化的模型路径。
quantization:
  activation_dtype: # Optional[str] = "int8". 用于激活量化的假量化布局。有效选项是 "int4"、"int8"、"float8"
  weight_dtype: # Optional[str] = "int8". 用于权重量化的假量化布局。有效选项是 "int4"、"fp8" 和 "nvfp4"。
  group_size: # Optional[int] = 32. 每组假量化的元素数
  quantize_embedding: # Optional[bool] = False. 是否量化嵌入层。

output_dir:  # 输出目录路径。
```

示例 2 (yaml)：
```yaml
# qat.yml
qat:
  activation_dtype: int8
  weight_dtype: int4
  group_size: 256

output_dir: # 训练期间使用的输出目录路径，其中已保存最终检查点。
```

示例 3 (bash)：
```bash
axolotl quantize qat.yml
```

---

## NCCL

**URL：** https://docs.axolotl.ai/docs/nccl.html

**内容：**
- NCCL

NVIDIA NCCL 是一个库，用于促进和优化多 GPU 通信操作，如广播、all-gather、reduce、all-reduce 等。广泛地说，NCCL 配置高度依赖环境，并通过多个环境变量进行配置。常见的 NCCL 相关问题发生在长时间运行的操作超时导致训练过程中止时：

通常，此超时将在 30 分钟（默认设置）后发生，并伴随着低于平均水平的功耗和接近 100% 的 GPU 利用率，然后才会引发错误。Nvidia 建议禁用 PCI 访问控制服务 (ACS) 作为可能的解决方案（如果这对您可用）。

通过 NVLink 强制跨 GPU 通信可能有帮助，而无需增加超时。要验证您的配置是否利用 NVLink，请运行以下命令：

要强制 NCCL 使用 NVLink，只需在环境中设置：

如果您的环境中 NVLink 不可用，下表中还有 NCCL_P2P_LEVEL 的其他选项：

要验证您的训练作业是否存在可接受的数据传输速度，运行 NCCL 测试可以帮助查明瓶颈，例如：

在调试 NCCL 通信超时时，在 PyTorch 和 NCCL 中激活额外的日志记录可能很有用：

最后，如果您认为训练作业需要更多时间，可以通过在 Axolotl 配置中设置 ddp_timeout 值将超时增加到 30 分钟以上。请参阅 PyTorch init_process_group 获取此值的文档。

**示例：**

示例 1 (unknown)：
```unknown
Watchdog caught collective operation timeout: WorkNCCL(SeqNum=42, OpType=ALLGATHER, Timeout(ms)=1800000) ran for 1806948 milliseconds before timing out.
```

示例 2 (bash)：
```bash
nvidia-smi nvlink --status
```

示例 3 (bash)：
```bash
export NCCL_P2P_LEVEL=NVL
```

示例 4 (bash)：
```bash
./build/all_reduce_perf -b 8 -e 128M -f 2 -g 3
```

---

## 多节点

**URL：** https://docs.axolotl.ai/docs/multi-node.html

**内容：**
- 多节点
- Accelerate
- Raytrain
- Torchrun
  - 选项 1：带启动器参数的新 Axolotl CLI（推荐）
  - 选项 2：直接 torchrun（旧版）

以下是在 Axolotl 中进行多节点训练的三种方式。

每台机器需要一份 Axolotl 副本，我们建议使用相同的提交以确保兼容性。

您还需要在每台机器上为模型使用相同的配置文件。

确保主机器可被其他机器访问。

您需要为 accelerate 创建配置，可以通过使用 accelerate config 并按照说明操作，或者您可以使用以下预设之一：

~/.cache/huggingface/accelerate/default_config.yaml

在 Axolotl yaml 中配置模型使用 FSDP。例如：

现在您只需像往常一样在每台机器上使用 accelerate 启动，一旦您在每台机器上启动了 accelerate，进程就会开始。

请参阅此处的 ray train 文档。

如果您使用 Infiniband，我们推荐 torchrun 以利用全带宽。

设置以下环境变量（根据您的系统更改 buffersize/socketname）：

在每个节点上运行以下命令：

请确保替换占位符变量：

新的 CLI 方法（选项 1）是推荐的，因为它提供一致的参数处理，并与其他 Axolotl CLI 功能无缝协作。

有关可用配置的更多信息，请参阅此处的 Pytorch 文档

**示例：**

示例 1 (yaml)：
```yaml
compute_environment: LOCAL_MACHINE
debug: false
distributed_type: FSDP
downcast_bf16: 'no'
machine_rank: 0 # 为主机器设置为 0，其他机器递增一
main_process_ip: 10.0.0.4 # 设置为主机器的 IP
main_process_port: 5000
main_training_function: main
mixed_precision: bf16
num_machines: 2 # 更改为机器数量
num_processes: 4 # 这是 GPU 总数，（例如：如果您有 2 台机器，每台 4 个 GPU，则填 8）
rdzv_backend: static
same_network: true
tpu_env: []
tpu_use_cluster: false
tpu_use_sudo: false
use_cpu: false
```

示例 2 (yaml)：
```yaml
fsdp_version: 2
fsdp_config:
  offload_params: true
  state_dict_type: FULL_STATE_DICT
  auto_wrap_policy: TRANSFORMER_BASED_WRAP
  transformer_layer_cls_to_wrap: LlamaDecoderLayer
  reshard_after_forward: true
```

示例 3 (bash)：
```bash
export NCCL_IB_DISABLE=0
export NCCL_SOCKET_IFNAME="eth0,en,eth,em,bond"
export NCCL_BUFFSIZE=2097152
```

示例 4 (bash)：
```bash
axolotl train config.yaml --launcher torchrun -- --nnodes $num_nodes --nproc_per_node $gpu_per_node --rdzv_id $rdzv_id --rdzv_backend c10d --rdzv_endpoint "$head_node_ip:$head_node_port"
```

---

## 数据集加载

**URL：** https://docs.axolotl.ai/docs/dataset_loading.html

**内容：**
- 数据集加载
- 概述
- 加载数据集
  - 本地数据集
    - 文件
    - 目录
      - 加载整个目录
      - 加载目录中的特定文件
  - HuggingFace Hub
    - 上传的文件夹

数据集可以根据保存方式（文件扩展名）和存储位置以多种不同方式加载。

我们使用 datasets 库加载数据集，并混合使用 load_dataset 和 load_from_disk 来加载它们。

您可能会认出 load_dataset 和配置文件的数据集部分之间相似命名的配置。

不要被这里的众多选项所压倒。其中很多是可选的。实际上，最常用的配置是 path，有时还有 data_files。

这与 datasets.load_dataset 的 API 匹配，所以如果您熟悉它，您会感到宾至如归。

有关 HuggingFace 加载不同数据集类型的指南，请参阅此处。

有关配置的完整详情，请参阅 config-reference.qmd。

您可以通过在 datasets 下添加多个条目在配置文件中设置多个数据集。

要加载 JSON 文件，您可以这样做：

这转换为以下配置：

在上面的示例中，可以看到我们可以将 path 指向文件或目录以及 ds_type 来加载数据集。

这适用于 CSV、JSON、Parquet 和 Arrow 文件。

如果 path 指向文件且未指定 ds_type，我们将从文件扩展名自动推断数据集类型，因此您可以省略 ds_type。

如果您正在加载目录，可以将 path 指向目录。

然后，您有两个选项：

您不需要任何额外配置。

我们将尝试按以下顺序加载：- 使用 datasets.save_to_disk 保存的数据集 - 加载整个文件目录（如 parquet/arrow 文件）

提供 data_files 和要加载的文件列表。

您用于加载数据集的方法取决于数据集是如何创建的，是直接上传文件夹还是推送 HuggingFace 数据集。

如果您正在使用私有数据集，您需要在配置文件的根级别启用 hf_use_auth_token 标志。

这意味着数据集是上传到 Hub 的单个文件或文件。

这意味着数据集是作为 HuggingFace 数据集创建并通过 datasets.push_to_hub 推送到 Hub。

根据数据集，可能需要一些其他配置，如 name、split、revision、trust_remote_code 等。

通过 load_dataset 下的 storage_options 配置，您可以从远程文件系统（如 S3、GCS、Azure 和 OCI）加载数据集。

目前这是实验性的。如果遇到任何问题，请告诉我们！

提供商之间唯一的区别是您需要使用相应的协议前缀路径。

对于目录，我们通过 load_from_disk 加载。

使用 s3:// 前缀路径。

凭据按以下顺序拉取：

我们假设您已设置凭据且未使用匿名访问。如果您想使用匿名访问，请告诉我们！我们可能需要为此打开配置选项。

可以设置的其他环境变量可以在 boto3 文档中找到

使用 gs:// 或 gcs:// 前缀路径。

凭据按以下顺序加载：

使用 adl:// 前缀路径。

确保您设置了以下环境变量：

使用 abfs:// 或 az:// 前缀路径。

确保您设置了以下环境变量：

可以设置的其他环境变量可以在 adlfs 文档中找到

使用 oci:// 前缀路径。

它将尝试按以下顺序读取：

其他环境变量：

请参阅 ocifs 文档。

路径应以 https:// 开头。

这必须是公开可访问的。

现在您知道如何加载数据集，您可以在数据集格式文档中了解更多关于如何将特定数据集格式加载到目标输出格式的信息。

**示例：**

示例 1 (yaml)：
```yaml
datasets:
  - path:
    name:
    data_files:
    split:
    revision:
    trust_remote_code:
```

示例 2 (yaml)：
```yaml
datasets:
  - path: /path/to/your/dataset
  - path: /path/to/your/other/dataset
```

示例 3 (python)：
```python
from datasets import load_dataset

dataset = load_dataset("json", data_files="data.json")
```

示例 4 (yaml)：
```yaml
datasets:
  - path: data.json
    ds_type: json
```

---

## 多 GPU

**URL：** https://docs.axolotl.ai/docs/multi-gpu.html

**内容：**
- 多 GPU
- 1 概述
- 2 DeepSpeed
  - 2.1 配置
  - 2.2 用法
  - 2.3 ZeRO 阶段
- 3 完全分片数据并行 (FSDP)
  - 3.1 从 FSDP1 迁移到 FSDP2
    - 3.1.1 配置映射
  - 3.2 FSDP1（已弃用）

本指南涵盖使用 Axolotl 进行多 GPU 设置的高级训练配置。

Axolotl 支持多种多 GPU 训练方法：

添加到您的 YAML 配置：

我们为以下提供默认配置：

选择在仍能适应 VRAM 的情况下卸载最少内存的配置以获得最佳性能。

从阶段 1 -> 阶段 2 -> 阶段 3 开始。

FSDP2 推荐给新用户。FSDP1 已弃用，将在 Axolotl 即将发布的版本中移除。

要将配置从 FSDP1 迁移到 FSDP2，您必须使用 fsdp_version 顶级配置字段指定 FSDP 版本，并按照下面的配置字段映射更新字段名称。

有关更多详情，请参阅 torchtitan 仓库中的迁移指南。在 Axolotl 中，如果您使用以下 FSDP1 配置：

您可以迁移到以下 FSDP2 配置：

使用 fsdp 配置 FSDP 已弃用，将在 Axolotl 即将发布的版本中移除。请改为使用 fsdp_config。

我们通过 ring-flash-attention 项目支持序列并行 (SP)。这允许将序列跨 GPU 分割，这在单个序列在模型训练期间导致 OOM 错误时很有用。

请参阅我们的专门指南了解更多信息。

有关将 FSDP 与 QLoRA 结合使用，请参阅我们的专门指南。

请参阅文档了解更多信息。

有关 NCCL 相关问题，请参阅我们的 NCCL 故障排除指南。

有关更详细的故障排除，请参阅我们的调试指南。

**示例：**

示例 1 (yaml)：
```yaml
deepspeed: deepspeed_configs/zero1.json
```

示例 2 (bash)：
```bash
# 获取 deepspeed 配置（如果尚未存在）
axolotl fetch deepspeed_configs

# 通过配置传递参数
axolotl train config.yml

# 通过 cli 传递参数
axolotl train config.yml --deepspeed deepspeed_configs/zero1.json
```

示例 3 (yaml)：
```yaml
fsdp_version: 1
fsdp_config:
  fsdp_offload_params: false
  fsdp_cpu_ram_efficient_loading: true
  fsdp_auto_wrap_policy: TRANSFORMER_BASED_WRAP
  fsdp_transformer_layer_cls_to_wrap: Qwen3DecoderLayer
  fsdp_state_dict_type: FULL_STATE_DICT
  fsdp_sharding_strategy: FULL_SHARD
```

示例 4 (yaml)：
```yaml
fsdp_version: 2
fsdp_config:
  offload_params: false
  cpu_ram_efficient_loading: true
  auto_wrap_policy: TRANSFORMER_BASED_WRAP
  transformer_layer_cls_to_wrap: Qwen3DecoderLayer
  state_dict_type: FULL_STATE_DICT
  reshard_after_forward: true
```

---

## Ray Train

**URL：** https://docs.axolotl.ai/docs/ray-integration.html

**内容：**
- Ray Train
- Ray 集群设置
- 健全性检查
- 使用 Ray Train 配置训练
- 启动训练

Axolotl 支持使用 Ray 作为 accelerate 的替代方案来编排训练。这对于多节点训练特别有用，因为您只需在单个节点中设置代码和依赖项，并像使用单个节点一样启动训练。

使用 --use-ray CLI 标志，Axolotl 将使用 Ray Train 的 TorchTrainer 运行训练。

使用 Ray Train 集成的先决条件是在您想要的节点上设置 Ray 集群。有关如何开始使用 ray 集群的详细指南，请查看此处的官方 Ray 文档。

每个 Ray 集群有一个头节点和一组工作节点。头节点就像任何其他工作节点，但它还运行与调度和编排相关的某些特殊进程。启用 Ray 的脚本在头节点上运行，根据它们请求的资源（CPU 数量、GPU 等），将被调度在工作节点上运行某些任务。有关 Ray 集群背后关键概念的更多信息，您可以参考此文档。

要对 ray 集群是否正确设置运行健全性检查，请在头节点上执行以下命令：

输出应该有 Ray 集群的摘要 - 集群中所有节点的列表、集群中的 CPU 和 GPU 数量等。例如，如果您有一个带有 1 个仅 CPU 头节点和 2 个 4xL40S 工作节点的集群，输出可能如下所示：

您还应该能够在 Ray 仪表板上看到相同的内容。

您可以在 configs/llama-3/lora-1b-ray.yaml 找到示例配置。

这里要注意的关键参数是：

您只需在头节点上运行以下命令：

这将在头节点上启动训练，工作节点将由 Ray Train 自动调度在适当的头节点或工作节点上运行。

您还可以在 Ray 仪表板上监控训练进度。

回到带有 1 个头节点和 2 个 4xL40S 工作节点的 Ray 集群示例，假设您想使用所有 8 个 GPU。您只需设置 ray_num_workers: 8 并运行前面的命令。集群选项卡将显示以下内容：

**示例：**

示例 1 (unknown)：
```unknown
Node status
---------------------------------------------------------------
Active:
 1 head
Idle:
 2 4xL40S:48CPU-384GB
Pending:
 (no pending nodes)
Recent failures:
 (no failures)

Resources
---------------------------------------------------------------
Usage:
 0.0/96.0 CPU
 0.0/8.0 GPU
 0B/800.00GiB memory
 0B/229.57GiB object_store_memory

Demands:
 (no resource demands)
```

示例 2 (yaml)：
```yaml
use_ray: true
ray_num_workers: 4
# 可选
resources_per_worker:
    GPU: 1
```

示例 3 (yaml)：
```yaml
resources_per_worker:
    accelerator_type:L40S: 0.001
```

示例 4 (bash)：
```bash
axolotl train examples/llama-3/lora-1b-ray.yml --use-ray
```

---

## 序列并行

**URL：** https://docs.axolotl.ai/docs/sequence_parallelism.html

**内容：**
- 序列并行
- 何时使用序列并行
- 配置
- 实现细节
- 要求
- 限制
- 示例
- 使用序列并行的样本打包
- 对批次大小的影响

序列并行是一种将序列跨多个 GPU 分割的技术，允许您训练在单个 GPU 上无法容纳的超长序列。每个 GPU 处理序列的不同部分，结果通过环形通信模式聚合。

在以下情况下使用序列并行：

要启用序列并行，将以下内容添加到配置文件：

context_parallel_size 应该是 GPU 总数的除数。例如：

启用序列并行时：

要使用序列并行，您需要：

这将训练 Llama 3 8B 模型，上下文长度为 8K，每个序列跨 2 个 GPU 分割为 2 个长度为 4096 的子序列。

序列并行与 Axolotl 的样本打包功能兼容。当同时使用这两个功能时：

使用序列并行时，您的有效全局批次大小除以 context_parallel_size。这是因为：

例如：- 使用 8 个 GPU 且无序列并行：每步处理 8 个不同批次 - 使用 8 个 GPU 且 context_parallel_size=4：仅处理 2 个不同批次（每个跨 4 个 GPU 分割）- 如果您的每 GPU micro_batch_size 为 2，全局批次大小从 16 减少到 4

**示例：**

示例 1 (yaml)：
```yaml
# 设置为可用 GPU 数量的除数（> 1）
context_parallel_size: 4  # 将序列跨 4 个 GPU 分割
# 可选；跨键维度步进。较大的值使用更多内存，但应该使训练更快。
heads_k_stride: 1
# 可选；"varlen_llama3" 或 "batch_ring" 之一。默认为
# 当 `sample_packing: true` 时为 "varlen_llama3"，否则为 "batch_ring"。
ring_attn_func:
```

示例 2 (yaml)：
```yaml
base_model: meta-llama/Llama-3-8B-Instruct
sequence_len: 8192

...

context_parallel_size: 4  # 将每个序列分割为 4 部分，每个 GPU 一部分
# 可选；跨键维度步进。较大的值使用更多内存，但应该使训练更快。
heads_k_stride: 1
# 可选；"varlen_llama3" 或 "batch_ring" 之一。默认为
# 当 `sample_packing: true` 时为 "varlen_llama3"，否则为 "batch_ring"。
ring_attn_func:

...
```

---

## 量化感知训练 (QAT)

**URL：** https://docs.axolotl.ai/docs/qat.html

**内容：**
- 量化感知训练 (QAT)
- 概述
- 在 Axolotl 中配置 QAT

量化感知训练 (QAT) 是一种通过在训练期间对模型权重（以及可选的激活）应用"假"量化来提高量化模型准确性的技术。这种假量化允许模型调整量化引入的噪声，因此当模型最终被量化时，准确性损失最小化。我们使用 torchao 中实现的量化技术为 axolotl 提供 QAT 和训练后量化 (PTQ) 支持。

我们建议查看 torchtune 库中优秀的 QAT 教程和 torchao 库中的 QAT 文档以获取更多详情。

要在 axolotl 中启用 QAT，将以下内容添加到配置文件：

我们支持以下量化模式：

完成训练后，您必须使用与训练模型相同的量化配置来量化模型。您可以使用 quantize 命令执行此操作。

**示例：**

示例 1 (yaml)：
```yaml
qat:
  activation_dtype: # Optional[str] = "int8". 用于激活量化的假量化布局。有效选项是 "int4"、"int8"、"float8"
  weight_dtype: # Optional[str] = "int8". 用于权重量化的假量化布局。有效选项是 "int4"、"fp8" 和 "nvfp4"。
  group_size: # Optional[int] = 32. 每组假量化的元素数
  fake_quant_after_n_steps: # Optional[int] = None. 在多少步后应用假量化
```

---

## FSDP + QLoRA

**URL：** https://docs.axolotl.ai/docs/fsdp_qlora.html

**内容：**
- FSDP + QLoRA
- 背景
- 用法
- 为 FSDP2 启用交换
- 示例配置
- 参考
- 脚注

使用 FSDP 和 QLoRA 对于在消费级 GPU 上微调大型（70b+ 参数）LLM 至关重要。例如，您可以使用 FSDP + QLoRA 在两个 24GB GPU 上训练 70b 模型。

下面，我们描述如何在 Axolotl 中使用此功能。

要使用 FSDP 启用 QLoRA，您需要执行以下步骤：

![提示] 除了阅读这些说明外，请参阅示例配置文件。

如果即使在 FSDP 的 CPU 卸载后可用内存仍然不足，您可以通过在 FSDP 配置中设置 cpu_offload_pin_memory: false 和 offload_params: true 来启用交换内存使用。

这禁用内存固定，允许 FSDP 使用磁盘交换空间作为回退。禁用内存固定本身会产生性能开销，实际使用交换会增加更多，但它可能使在资源受限系统上训练否则会导致 OOM 错误的更大模型成为可能。

examples/llama-2/qlora-fsdp.yml 包含如何在 axolotl 中启用 QLoRA + FSDP 的示例。

这是由 Answer.AI 团队的这项工作启用的。↩︎

---

## 自定义集成

**URL：** https://docs.axolotl.ai/docs/custom_integrations.html

**内容：**
- 自定义集成
- Cut Cross Entropy
  - 要求
  - 安装
  - 用法
  - 支持的模型
  - 引用
- DenseMixer
- Axolotl 的扩散 LM 训练插件
  - 概述

Axolotl 通过集成添加自定义功能。它们位于 src/axolotl/integrations 目录中。

要启用它们，请查看各自的文档。

Cut Cross Entropy (CCE) 通过优化损失计算期间的交叉熵操作来减少 VRAM 使用。

请参阅 https://github.com/apple/ml-cross-entropy

如果您还没有，请运行以下命令安装 cut_cross_entropy[transformers]。

请参阅此处的参考

只需将以下内容添加到您的 axolotl YAML 配置：

请参阅此处的参考

此插件在 Axolotl 中使用受 LLaDA（大型语言扩散模型）启发的方法启用扩散语言模型训练。

LLaDA 是一种基于扩散的语言模型训练方法，使用：- 训练期间的随机 token 掩码而不是下一个 token 预测 - 双向注意力以允许模型关注完整上下文 - 基于掩码概率的重要性加权以实现稳定训练

这种方法可以产生具有更好双向上下文理解的更稳健的语言模型。

该插件随 Axolotl 一起提供。请参阅我们的安装文档。

使用示例配置训练（Llama‑3.2 1B）：- 预训练：axolotl train examples/llama-3/diffusion-3.2-1b-pretrain.yaml - SFT：axolotl train examples/llama-3/diffusion-3.2-1b-sft.yaml

您还可以修改现有配置以启用/自定义扩散训练。

将以下内容添加到您的 Axolotl 配置：

并配置嵌套的 diffusion 块（显示默认值）：

任何支持 4D 注意力掩码的模型应该开箱即用。如果不支持，请创建 issue 或提交 PR！

在训练期间，token 被随机掩码：- 从 [0, 1] 均匀采样时间步 t - 计算掩码概率：p = (1 - eps) * t + eps - 以概率 p 随机掩码 token

损失仅在掩码 token 上计算，带有（可选）重要性加权：

当 diffusion.generate_samples: true 时，插件在训练期间生成样本：

样本记录到控制台和 wandb（如果启用）。

扩散推理集成到标准 Axolotl CLI 中。使用您训练时的相同配置并运行：

可选，传递 --gradio 使用简单的 Web 界面。

交互控制（在提示前加上命令）：- :complete N → 补全模式，追加 N 个新掩码 token（默认 64）- :mask R → 随机掩码模式，目标掩码率 R 在 [0.0, 1.0]

插件添加（或修改）多个指标以跟踪扩散训练：

请参阅此处的参考

请参阅 https://github.com/ironjr/grokfast

请参阅此处的参考

示例数据集可在 axolotl-ai-co/evolkit-logprobs-pipeline-75k-v2-sample 找到

请参阅此处的参考

使用 Neural Magic 的 LLMCompressor 在 Axolotl 中微调稀疏化模型。

此集成允许在 Axolotl 训练框架内微调使用 LLMCompressor 稀疏化的模型。通过将 LLMCompressor 的模型压缩能力与 Axolotl 的分布式训练管道相结合，用户可以大规模高效微调稀疏模型。

它使用 Axolotl 的插件系统挂钩到微调流程，同时在训练期间保持稀疏性。

带有 llmcompressor 额外组件的 Axolotl：

需要 llmcompressor >= 0.5.1

这将安装使用集成微调稀疏化模型所需的所有依赖项。

要使用此集成启用稀疏微调，请在 Axolotl 配置中包含插件：

此插件本身不应用剪枝或稀疏化 — 它旨在微调已经稀疏化的模型。

预稀疏化的检查点可以：- 使用 LLMCompressor 生成 - 从 Neural Magic 的 Hugging Face 页面下载 - 您自己创建的任何具有兼容稀疏模式的自定义 LLM

要了解有关编写和自定义 LLMCompressor 配方的更多信息，请参阅官方文档：https://github.com/vllm-project/llm-compressor/blob/main/README.md

在配置中设置 save_compressed: true 可以以压缩格式保存模型，这：- 减少约 40% 的磁盘空间使用 - 保持与 vLLM 的兼容性以加速推理 - 保持与 llmcompressor 的兼容性以进一步优化（例如：量化）

使用稀疏模型时强烈推荐此选项，以最大化模型压缩的好处。

请参阅 examples/llama-3/sparse-finetuning.yaml 获取完整示例。

微调稀疏模型后，您可以利用 vLLM 进行高效推理。您还可以使用 LLMCompressor 在推理前对微调后的稀疏模型应用额外量化，以获得更大的性能优势：

有关 vLLM 功能和高级配置选项的更多详情，请参阅官方 vLLM 文档。

有关可用稀疏性和量化模式、微调配方和使用示例的详情，请访问官方 LLMCompressor 仓库：

https://github.com/vllm-project/llm-compressor

请参阅此处的参考

使用流行的 lm-evaluation-harness 库在模型上运行评估。

请参阅 https://github.com/EleutherAI/lm-evaluation-harness

请参阅此处的参考

Liger Kernel 为 LLM 训练提供高效的 Triton 内核，提供：

请参阅 https://github.com/linkedin/Liger-Kernel

请参阅此处的参考

由 Eric Hartford、Lucas Atkins、Fernando Fernandes、David Golchinfar 编写

此插件包含基于信噪比 (SNR) 冻结模型底部部分模块的代码。

请参阅 https://github.com/cognitivecomputations/spectrum

Spectrum 是用于扫描和评估大型语言模型层信噪比 (SNR) 的工具。通过识别具有最高 SNR 的前 n% 层，您可以优化训练效率。

请参阅此处的参考

插件可用于通过挂钩自定义训练管道的行为。请参阅 axolotl.integrations.BasePlugin 了解可能的挂钩。

要添加新集成，请按照以下步骤操作：

请参阅 src/axolotl/integrations/cut_cross_entropy 获取最小集成示例。

如果无法加载集成，请确保您以可编辑模式 pip 安装。

并在配置文件中正确拼写集成名称。

不必将集成放在 integrations 文件夹中。它可以位于任何位置，只要它安装在您的 python 环境中的包中。

请参阅此仓库获取示例：https://github.com/axolotl-ai-cloud/diff-transformer

**示例：**

示例 1 (bash)：
```bash
python scripts/cutcrossentropy_install.py | sh
```

示例 2 (bash)：
```bash
pip3 uninstall -y cut-cross-entropy && pip3 install "cut-cross-entropy[transformers] @ git+https://github.com/axolotl-ai-cloud/ml-cross-entropy.git@8a1a0ec"
```

示例 3 (yaml)：
```yaml
plugins:
  - axolotl.integrations.cut_cross_entropy.CutCrossEntropyPlugin
```

示例 4 (unknown)：
```unknown
@article{wijmans2024cut,
  author       = {Erik Wijmans and
                  Brody Huval and
                  Alexander Hertzberg and
                  Vladlen Koltun and
                  Philipp Kr\"ahenb\"uhl},
  title        = {Cut Your Losses in Large-Vocabulary Language Models},
  journal      = {arXiv},
  year         = {2024},
  url          = {https://arxiv.org/abs/2411.09009},
}
```

---

## 配置参考

**URL：** https://docs.axolotl.ai/docs/config-reference.html

**内容：**
- 配置参考

**示例：**

示例 1 (yaml)：
```yaml
# 允许使用 cli 覆盖 yml 配置
strict: bool | None = False
# 从特定检查点目录恢复
resume_from_checkpoint: str | None
# 如果未设置 resume_from_checkpoint 并且您只是想让它从中断处开始。
# 在不同模型之间启用此选项时要小心。
auto_resume_from_checkpoints: bool | None
# 当添加新 token 时，将模型嵌入调整为 32 的倍数。这被
# 报告可以在某些模型上提高训练速度
resize_token_embeddings_to_32x: bool | None
mean_resizing_embeddings: bool | None = False

# 是否将嵌入缩小到 len(tokenizer)。默认情况下，我们不会缩小。
shrink_embeddings: bool | None
# 使用 PEFT 时不要将嵌入向上转换为 float32。对低 VRAM GPU 有用
embeddings_skip_upcast: bool | None
# 随机重新初始化模型权重而不是加载预训练权重
reinit_weights: bool | None

# 用于训练的自定义训练器类模块
trainer_cls: str | None

# 使用 RL 训练：'dpo'、'ipo'、'kto'、'simpo'、'orpo'、'grpo'
rl: RLType | None

trl: TRLConfig | None
  # 对于 TRLConfig：
  # RL 训练的 Beta 参数。与 `rl_beta` 相同。使用
  beta: float | None
  # RL 训练补全的最大长度。
  max_completion_length: int | None

  # 是否为 RL 训练使用 VLLM。
  use_vllm: bool = False
  # 要使用的 VLLM 模式，'server' 或 'colocate' 之一
  vllm_mode: Literal['server', 'colocate'] | None
  # 要连接的 vLLM 服务器主机。
  vllm_server_host: str | None = 0.0.0.0
  # 要连接的 vLLM 服务器端口。
  vllm_server_port: int | None = 8000
  # 等待 vLLM 服务器响应的总超时（秒）。
  vllm_server_timeout: int | None
  # vLLM 引导解码的正则表达式。
  vllm_guided_decoding_regex: str | None

  # 要加载的奖励函数列表。路径必须可从当前目录导入。
  reward_funcs: list[str] | None
  # 奖励函数的奖励权重列表。
  reward_weights: list[float] | None
  # 要采样的生成数量。
  num_generations: int | None
  # 是否记录补全。
  log_completions: bool | None = False
  # 当 log_completions 为 True 时要打印的补全数量。
  num_completions_to_print: int | None
  # 控制重要性采样比率是在 `'token'` 还是
  # `'sequence'` 级别计算。对于 GSPO，使用 `sequence`，默认为 None，对应于
  # 原始 GRPO 论文。
  importance_sampling_level: Literal['sequence', 'token'] | None

  # 是否同步参考模型。
  sync_ref_model: bool | None = False
  # 参考模型的混合 alpha。
  ref_model_mixup_alpha: float | None = 0.9
  # 参考模型的同步步数。
  ref_model_sync_steps: int | None = 64
  # 是否按标准差缩放奖励。
  scale_rewards: bool = True

  # GRPO 策略的采样温度。
  temperature: float | None
  # 生成策略的 top-p 采样概率。
  top_p: float | None
  # 生成策略的 top-k 采样。
  top_k: int | None
  # 生成策略的最小概率。
  min_p: float | None
  # 出现在提示和生成文本中的 token 的惩罚。
  repetition_penalty: float | None
  # GRPO 每批次的迭代次数 (μ)。
  num_iterations: int | None
  # GRPO 算法中剪切的 epsilon 值。
  epsilon: float | None
  # GRPO 算法中剪切的上界 epsilon 值。
  epsilon_high: float | None
  # 是否为 GRPO 使用 Liger 损失。
  use_liger_loss: bool | None
  # 要使用的损失公式。支持值：grpo、bnpo、dr_grpo。
  loss_type: str | None
  # 是否从损失计算中排除截断的补全。
  mask_truncated_completions: bool = False
  # 为 vLLM 启用睡眠模式以在空闲时卸载 VRAM
  vllm_enable_sleep_mode: bool | None

vllm: VllmConfig | None
  # 对于 VllmConfig：
  # VLLM 使用的设备
  device: str | None = auto
  # VLLM 的张量并行大小
  tensor_parallel_size: int | None
  # VLLM 的数据并行大小
  data_parallel_size: int | None
  # VLLM 的 GPU 内存利用率
  gpu_memory_utilization: float | None = 0.9
  # VLLM 的数据类型
  dtype: str | None = auto
  # VLLM 模型上下文的最大长度
  max_model_len: int | None
  # 为 VLLM 启用前缀缓存
  enable_prefix_caching: bool | None
  # vLLM 服务器启动的主机
  host: str | None = 0.0.0.0
  # vLLM 服务器启动的端口
  port: int | None = 8000

  # 为 VLLM 启用推理
  enable_reasoning: bool | None
  # VLLM 的推理解析器
  reasoning_parser: str | None

qat: QATConfig | None
  # 对于 QATConfig：
  # 用于激活量化的假量化布局。
  activation_dtype: TorchAOQuantDType | None
  # 用于权重量化的假量化布局。
  weight_dtype: TorchAOQuantDType = TorchAOQuantDType.int8
  # 量化嵌入
  quantize_embedding: bool | None = False
  # 每组假量化的元素数
  group_size: int | None = 32
  # 在多少步后应用假量化
  fake_quant_after_n_steps: int | None

quantization: PTQConfig | None
  # 对于 PTQConfig：
  # 用于权重量化的假量化布局。
  weight_dtype: TorchAOQuantDType = TorchAOQuantDType.int8
  # 用于激活量化的假量化布局。
  activation_dtype: TorchAOQuantDType | None
  # 是否量化嵌入层。
  quantize_embedding: bool | None
  # 每组假量化的元素数
  group_size: int | None = 32

# 奖励建模：`True` 或 `False`
reward_model: bool | None
# 过程奖励建模：`True` 或 `False`
process_reward_model: bool | None
# 激励奖励模型输出均零奖励的系数（由
# https://huggingface.co/papers/2312.09244，Eq. 2 提出）。推荐值：`0.01`。
center_rewards_coefficient: float | None
num_labels: int | None

# 是否在 DPO 训练器中执行加权
dpo_use_weighting: bool | None
dpo_use_logits_to_keep: bool | None
dpo_label_smoothing: float | None
dpo_norm_loss: bool | None
dpo_padding_free: bool | None
dpo_generate_during_eval: bool | None

# 用于微调模型的一个或多个数据集列表
datasets: Annotated[list[SFTDataset | DPODataset | KTODataset | StepwiseSupervisedDataset], MinLen(1)] | None
  # 对于 SFTDataset：
  # HuggingFace 数据集仓库 | s3:// | gs:// | 本地文件或目录路径
  path: str | None
  # 要加载的数据集拆分名称
  split: str | None
  # 用于训练的提示类型。[alpaca, gpteacher, oasst, reflection]
  type: str | UserDefinedPrompterType | None
    # 对于 UserDefinedPrompterType：
    # 自定义用户指令提示
    system_prompt: str | None
    # 使用 {system} 作为要替换的键
    system_format: str | None
    field_system: str | None
    field_instruction: str | None
    field_input: str | None
    field_output: str | None

    # 可自定义为单行或多行。使用 {instruction}/{input} 作为要
    # 替换的键。'format' 可以包含 {input}
    format: str | None
    # 'no_input_format' 不能包含 {input}
    no_input_format: str | None
  input_transform: str | None
  # 将数据集分割为 N 片（与 shards_idx 一起使用）
  shards: int | None
  # 要使用的分片数据集索引
  shards_idx: int | None
  # 为内存效率按 N 个顺序块处理数据集（与
  # `shards` 互斥）
  preprocess_shards: int | None
  conversation: str | None

  # 用于训练的聊天模板名称，支持以下值：
  # tokenizer_default：使用 tokenizer_config.json 中可用的聊天模板。
  # 如果分词器中没有聊天模板，将引发错误。这是默认值。
  # alpaca/inst/chatml/gemma/cohere/llama3/phi_3/deepseek_v2/jamba：这些聊天模板
  # 在 axolotl 代码库的 src/axolotl/utils/chat_templates.py 中可用。
  # tokenizer_default_fallback_*：其中 * 是如果分词器没有聊天模板则回退
  # 到的聊天模板名称，否则默认为分词器。例如
  # tokenizer_default_fallback_chatml。jinja：使用自定义 jinja 模板作为聊天
  # 模板。自定义 jinja 模板应在 chat_template_jinja 字段中提供。
  chat_template: ChatTemplate | str | None
  # 自定义 jinja 聊天模板或 jinja 文件路径。仅在 `chat_template:
  # jinja` 或为空时使用。
  chat_template_jinja: str | None
  # 源数据文件路径
  data_files: str | list[str] | None
  input_format: str | None
  # 要加载的数据集配置名称
  name: str | None
  # 当路径是文件时定义数据类型
  ds_type: str | None
  # 仅对于 `completion` 数据集，使用提供的字段而不是 `text` 列
  field: str | None
  field_human: str | None
  field_model: str | None
  # 包含消息的键（默认："messages"）
  field_messages: str | None
  # 包含工具的键（默认："tools"）。必须是 list[dict] 并遵循 [JSON
  # schema](https://json-schema.org/learn/getting-started-step-by-step)。
  field_tools: str | None
  # 包含推理轨迹的键（默认："reasoning_content"）。
  field_thinking: str | None
  # 聊天模板期望的指示推理轨迹的键。
  template_thinking_key: str | None

  message_field_role: str | None

  message_field_content: str | None
  # 从输入数据集到聊天模板的属性映射。（默认：
  # message_property_mappings={'role':'role', 'content':'content'}）如果属性存在
  # 于模板中但不在映射中，系统将尝试直接
  # 使用属性名作为键从消息中加载它。示例：在下面的映射中，
  # 'from' 从输入数据集加载并用作 'role'，而 'value' 加载并
  # 在聊天模板中用作 'content'。
  message_property_mappings: dict[str, str] | None
  # 消息轮次中通过布尔值指示轮次的 token 是否
  # 应该被考虑用于训练的键。用于选择性训练某些轮次
  # 除了 `roles_to_train`。
  message_field_training: str | None
  # 消息轮次中包含训练详情的键。用于
  # 选择性训练轮次中的某些 token。键的值是 List[Dict]
  # 包含 `begin_offset`（内容中的起始字符索引）、`end_offset`（内容中的
  # 结束字符索引）和 `train`（是否训练的布尔值）。
  message_field_training_detail: str | None
  # （仅适用于 Qwen3 模板）是否根据
  # 分隔标签内的推理轨迹分割助手内容
  split_thinking: bool | None
  logprobs_field: str | None
  temperature: float | None
  # 要训练的角色。来自这些角色的 token 将被考虑用于损失。
  roles_to_train: list[str] | None
  # 对话中要训练哪些 EOS token。可能的值是：all：训练
  # 所有 EOS token，turn（默认）：训练每个可训练
  # 轮次末尾的 EOS token，last：训练对话中的最后一个 EOS token
  train_on_eos: Literal['all', 'turn', 'last'] | None
  # 消息中的角色映射。格式是 {target_role: [source_roles]}。所有
  # 源角色将映射到目标角色。默认是：user: ["human",
  # "user"], assistant: ["gpt", "assistant"], system: ["system"], tool: ["tool"]
  roles: dict[str, list[str]] | None
  # 是否从数据集中删除系统轮次。仅适用于 chat_template。
  # 这不会删除 chat_template 中的默认系统消息（如果存在）。如果
  # 您希望删除，我们建议使用删除了默认系统
  # 消息的自定义 jinja 模板或添加内容为空的系统轮次。
  drop_system_message: bool | None
  # 对不受信任的源信任远程代码
  trust_remote_code: bool | None = False
  # 从 Hugging Face Hub 加载时要使用的数据集特定版本。
  # 这可以是提交哈希、标签或分支名称。如果未指定，将使用最新版本。
  # 此参数对本地数据集忽略。
  revision: str | None

  # 对于 DPODataset：
  path: str | None
  split: str | None
  type: UserDefinedDPOType | str | None
    # 对于 UserDefinedDPOType：
    field_system: str | None
    field_prompt: str | None
    field_chosen: str | None
    field_rejected: str | None
    prompt_format: str | None
    chosen_format: str | None
    rejected_format: str | None
  data_files: list[str] | None
  revision: str | None
  field_messages: str | None

  # 对于 KTODataset：
  path: str | None
  split: str | None
  type: UserDefinedKTOType | str | None
    # 对于 UserDefinedKTOType：
    field_system: str | None
    field_prompt: str | None
    field_completion: str | None
    field_label: bool | None
    prompt_format: str | None
    completion_format: str | None
  data_files: list[str] | None
  trust_remote_code: bool | None = False
  revision: str | None

  # 对于 StepwiseSupervisedDataset：
  path: str | None
  split: str | None
  data_files: list[str] | None
  revision: str | None
  step_separator: str | None
  max_completion_length: int | None
  train_on_last_step_only: bool | None

# 用于评估模型的一个或多个数据集列表。您可以使用
# test_datasets 或 val_set_size，但不能同时使用两者。
test_datasets: Annotated[list[SFTDataset | DPODataset | KTODataset | StepwiseSupervisedDataset], MinLen(1)] | None
  # 对于 SFTDataset：
  # HuggingFace 数据集仓库 | s3:// | gs:// | 本地文件或目录路径
  path: str | None
  # 要加载的数据集拆分名称
  split: str | None
  # 用于训练的提示类型。[alpaca, gpteacher, oasst, reflection]
  type: str | UserDefinedPrompterType | None
    # 对于 UserDefinedPrompterType：
    # 自定义用户指令提示
    system_prompt: str | None
    # 使用 {system} 作为要替换的键
    system_format: str | None
    field_system: str | None
    field_instruction: str | None
    field_input: str | None
    field_output: str | None

    # 可自定义为单行或多行。使用 {instruction}/{input} 作为要
    # 替换的键。'format' 可以包含 {input}
    format: str | None
    # 'no_input_format' 不能包含 {input}
    no_input_format: str | None
  input_transform: str | None
  # 将数据集分割为 N 片（与 shards_idx 一起使用）
  shards: int | None
  # 要使用的分片数据集索引
  shards_idx: int | None
  # 为内存效率按 N 个顺序块处理数据集（与
  # `shards` 互斥）
  preprocess_shards: int | None
  conversation: str | None

  # 用于训练的聊天模板名称，支持以下值：
  # tokenizer_default：使用 tokenizer_config.json 中可用的聊天模板。
  # 如果分词器中没有聊天模板，将引发错误。这是默认值。
  # alpaca/inst/chatml/gemma/cohere/llama3/phi_3/deepseek_v2/jamba：这些聊天模板
  # 在 axolotl 代码库的 src/axolotl/utils/chat_templates.py 中可用。
  # tokenizer_default_fallback_*：其中 * 是如果分词器没有聊天模板则回退
  # 到的聊天模板名称，否则默认为分词器。例如
  # tokenizer_default_fallback_chatml。jinja：使用自定义 jinja 模板作为聊天
  # 模板。自定义 jinja 模板应在 chat_template_jinja 字段中提供。
  chat_template: ChatTemplate | str | None
```
