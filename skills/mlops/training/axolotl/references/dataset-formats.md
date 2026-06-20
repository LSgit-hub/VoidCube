# Axolotl - 数据集格式

**页面：** 9

---

## 自定义预分词数据集

**URL：** https://docs.axolotl.ai/docs/dataset-formats/tokenized.html

**内容：**
- 自定义预分词数据集

**示例：**

示例 1 (yaml)：
```yaml
datasets:
  - path: /path/to/your/file.jsonl
    ds_type: json
    type:
```

示例 2 (json)：
```json
{"input_ids":[271,299,99],"attention_mask":[1,1,1],"labels":[271,-100,99]}
{"input_ids":[87,227,8383,12],"attention_mask":[1,1,1,1],"labels":[87,227,8383,12]}
```

---

## 数据集格式

**URL：** https://docs.axolotl.ai/docs/dataset-formats/index.html

**内容：**
- 数据集格式
- 预训练
  - 从 Hugging Face hub 数据集预训练
  - 从本地数据集文件预训练
  - 无流式预训练
  - 预训练数据集配置提示
    - 设置 max_steps
    - Group_by_length
  - 参考
- 监督微调 (SFT)

Axolotl 是一个训练框架，旨在通过简单地传递配置 yaml 文件使过程对用户方便而灵活。

由于 Axolotl 中有很多可用选项，本指南旨在简化用户体验，帮助选择正确的选择。

Axolotl 支持 3 种训练方法：预训练、监督微调和基于偏好的后训练（例如 DPO、ORPO、PRM）。每种方法都有自己的数据集格式，如下所述。

本指南主要使用 JSONL 作为介绍。请参考数据集加载文档了解如何从其他来源加载数据集。

对于 pretraining_dataset：具体来说，请参考预训练部分。

当旨在训练大型文本语料库数据集时，预训练是您的首选。由于这些数据集的大小，在开始训练之前下载整个数据集将非常耗时。Axolotl 支持流式传输，一次只将批次加载到内存中。

预训练数据集的示例格式如下：

通常建议将数据集保存为 .jsonl，因为其灵活性和简单性。

Axolotl 支持从 Hugging Face hub 仓库或本地文件加载。

例如，要使用 Hugging Face 数据集 hf_org/name 进行训练，可以传递以下配置：

给定几个语料库文件：A.jsonl、B.jsonl 和 C.jsonl，您的配置将如下所示：

虽然我们推荐 .jsonl，您也可以使用 Dataset.load_dataset 支持的其他格式（csv、parquet、arrow、SQL、Webdataset）

如果数据集很小并且可以完全加载到内存中，运行预训练的另一种方法是使用 completion 格式。这意味着整个数据集是预分词的，而不是按需流式分词。

这样做的一个好处是分词可以在仅 CPU 的机器上单独执行，然后传输到 GPU 机器进行训练以节省成本。

仅对于 completion，Axolotl 会将超过上下文长度的文本分割成多个较小的提示。如果您希望 pretraining_dataset 也有此功能，请告诉我们或帮助提交 PR！

当对大型数据集使用流式传输时，Axolotl 无法预先知道数据集有多大，也不知道何时停止。

因此，必须在配置中设置 max_steps: int 才能运行预训练，以便 Axolotl 知道何时停止训练。

一步等于 sequence_len * micro_batch_size * gradient_accumulation_steps * total_num_gpus 个 token。

如果从 Hugging Face hub 下载，建议关闭此选项，因为它会下载整个数据集，这可能非常大。

请参阅此处的文档。

监督微调是训练模型响应指令或聊天输入的过程。

由于数据集格式多种多样，Axolotl 尝试支持公共数据集中可用的大多数格式。

Axolotl 提供四种加载数据集的方法，但是，从您可用的数据集反向推导使用哪种方法更容易。

流程图如下：

您是否已经对数据集进行了分词？如果是，请查看预分词数据集。

您是否想自己格式化数据集并手动选择要屏蔽的每个部分？如果是，请查看无模板数据集

您的数据集是否为"conversation"格式，包含 list[messages]？如果是，请查看对话数据集

您的数据集是否为"instruct"格式，包含 { instruction, response }？如果是，请查看指令数据集

如果您完成了流程图但没有找到匹配的，建议将数据集预处理为上述之一或在 Github Discussion 上创建主题。

您可以在每种方法内或跨方法混合搭配，在多种数据集上训练模型。

当您想带自己的分词数据集时，我们建议此方法。

Axolotl 期望数据集有三个键：

确保将 BOS/EOS token 添加到提示中并适当屏蔽。

此配置如下所示：

参考：预分词数据集文档。

当您想对提示格式化、特殊 token 和屏蔽进行细粒度控制，同时让 Axolotl 处理分词时，我们推荐此方法。如果您的数据集具有跨样本不同的独特提示，且单一通用模板无法满足，这非常有用。

在下面的示例中，您可以看到没有正确的结构。同时，它非常灵活，因为提示的外观没有约束。

每个提示必须有一个名为 segments 的键，它是 { text, label } 的列表。

参考：无模板文档。

对话消息是消息列表，通常包含 role 和 content 键。

趣闻：Axolotl 将"chat"消息同义地称为对话消息，因为 FastChat 最初使用此术语构建了广泛使用的 fastchat 对话方法来格式化聊天消息，这在 chat_templates 创建之前。

当前最流行和方便的推理方法是使用 chat_templates 来格式化提示。Axolotl 支持使用 chat_templates 进行训练，以确保模型在相同环境中执行推理。

以下是 chat_template 的快速概述：chat_template 是一个 Jinja2 模板，将消息列表格式化为提示。

格式化为名为 ChatML 的流行模板的提示示例如下：

单个提示（美化打印）：

ChatML 模板如下：

上述提示格式化到此模板将产生：

通过使用分隔符（<|im_start|> 和 <|im_end|>），提示分隔不同的说话者，帮助模型识别哪部分属于谁。

具有以下格式的旧对话数据集通俗地称为 sharegpt 数据集。

较新的对话数据集通常遵循 OpenAI 格式。

Axolotl 支持两者，并允许自定义任何类型的键。

要正确使用此方法，识别三件事很重要：

您想使用哪个 chat_template？

数据集中的键是什么，可能的角色是什么？例如，在 OpenAI 格式中，键分别是 messages、role 和 content，而可能的角色是 system、user 和 assistant。

您想屏蔽什么？例如，仅 assistant 消息、仅最后一条消息或什么都不屏蔽。

有很多 chat_template。Axolotl 支持常见的：支持的聊天模板。例如，要使用 ChatML，应该是 chat_template: chatml。

但是，也可以通过指定 chat_template: tokenizer_default 使用分词器中已配置的模板。如果您想要回退（以防某些分词器没有预配置），可以执行 chat_template: tokenizer_default_fallback_chatml 在未找到分词器模板时回退到 ChatML 模板。

最后但强大的一种方法是带您自己的模板。可以通过以下方式设置：

我们目前默认使用 OpenAI 格式作为数据集键，所以如果这是您当前的数据集格式，这里没有什么要做的。

如果您的数据集格式不同，以下是您应该检查的键（及其默认值）：

在某些 chat_template（例如 Gemma）中，角色被硬编码为 user 和 assistant。因此，您可能发现有必要将数据集中的角色映射到上述角色。我们目前有一些适用于常见数据集的默认值，但如果您收到 KeyError，则需要为您的角色添加映射。以下是它的示例：

在上面的示例中，所有 gpt 和 model 值都转换为 assistant。所有 human 值都转换为 user。

chat_template 的常见用例是用于聊天消息，因此，屏蔽所有非 assistant 消息是常见的。Assistant 消息是指您希望模型学习的机器人消息。

要在所有 assistant 消息上训练，您需要设置以下配置。

train_on_eos 配置意味着它将屏蔽所有非 assistant 轮次的 EOS token。其他选项是：all 和 last 来选择要训练的 EOS。

也许您想在 assistant 和 narrator 角色上训练，只需将 narrator 添加到 roles_to_train 列表。您还需要将其添加到上面的角色映射中。

由于 chat_template 可能使用与分词器 EOS 不同的硬编码 EOS/EOT token，强烈建议设置它们。例如，ChatML 使用 <|im_end|> 来结束轮次。

完成上述所有步骤后，您可以将所有这些配置组合在一起，为您的自定义数据集形成定制配置。

如果将此配置应用于上面的示例数据集，输出将如下所示（可以通过 axolotl preprocess config.yaml --debug 检索）：

第一个数字指的是标签，第二个指的是 token_id。例如，-100 标签出现在非 assistant 部分，意味着它们被屏蔽。对于 assistant 部分，标签与 token_id 相同。

如果在预处理期间有很多 Could not find content __ boundary 警告，请查看 chat_templates 的 FAQ 部分。

请参阅此处的文档。

指令数据集用于训练指令遵循模型，包含一个提示（包含指令）和单个响应。与可能是多轮的聊天数据集相比，指令数据集通常是单轮的。

一个示例是称为 Alpaca 的常见格式：

使用这些键，可以基于它构建提示。

可以这样配置：

Axolotl 支持多种指令数据集。所有这些都可以在指令数据集文档中找到，以及它们各自的类型和示例行格式。

由于指令格式的无数可能性，Axolotl 允许自定义您自己的指令格式，而无需直接深入代码。

在下面的示例中，使用示例行以 mistral_v1 格式输出。

配置设置 field_instruction 实际上名为 input，field_input 为空，因为此示例中没有输入。通常，instruction 可以被认为是向模型提出的问题，input 是附加信息，output 是响应。没有必要有 input 或 system。最后，最重要的部分是了解您希望它看起来像什么格式，以及如何将其自定义到您的用例。

参考：自定义指令提示格式文档。

由于有多种 RLHF 方法及其各自的数据集要求。请参阅 RLHF 文档了解更多详情。

**示例：**

示例 1 (json)：
```json
{"text": "first row"}
{"text": "second row"}
...
```

示例 2 (yaml)：
```yaml
pretraining_dataset: hf_org/name
```

示例 3 (yaml)：
```yaml
pretraining_dataset:
  - path: json
    data_files:
      - A.jsonl
      - B.jsonl
      - C.jsonl
```

示例 4 (yaml)：
```yaml
datasets:
  - path: hf_org/name
    type: completion
```

---

## 对话

**URL：** https://docs.axolotl.ai/docs/dataset-formats/conversation.html

**内容：**
- 对话
- chat_template
  - 从 sharegpt 迁移
  - 示例
    - 在最后一条消息上训练
    - 覆盖默认聊天模板
    - 使用带回退的默认聊天模板
    - 自定义 Jinja 模板
    - 使用不同 EOT 和 EOS token 的模板
    - 使用工具调用

聊天模板策略使用 jinja2 模板将消息列表转换为提示。支持使用分词器的模板、支持的模板或自定义 jinja2。

请参阅配置以获取完整配置和支持的模板。

大多数配置可以如下调整：

我们建议查看以下示例以了解其他用例。

（旧版）在 tokenizer_config.json 中使用默认聊天模板处理 OpenAI 消息格式，仅在最后一条消息上训练。

如果您收到类似"chat_template choice is tokenizer_default but tokenizer's chat_template is null."的错误，这意味着分词器没有默认 chat_template。请按照以下示例设置自定义 chat_template。

使用 gemma 聊天模板覆盖 tokenizer_config.json 的聊天模板处理 OpenAI 消息格式，在所有 assistant 消息上训练。

如果您想使用内置 chat_template，请使用 chat_template: tokenizer_default（这是默认设置）。

使用 tokenizer_config.json 的聊天模板或 chatml 作为回退（如果前者的聊天模板不存在），处理 OpenAI 消息格式，在所有 assistant 消息上训练。

在 OpenAI 消息格式上使用自定义 jinja 模板，在所有 assistant 消息上训练。

请确保您的 tokenizer.eos_token 与模板中的 EOS（序列结束）token 相同。否则，在 special_tokens: 下设置 eos_token。

请参阅配置文档以获取"turn"、"last"和"all"选项用于训练 token 的详细解释。

使用 eot_tokens 要求 chat_template 中存在的每个 token 都是分词器中的单个 token。否则，分词器将分割 token 并导致意外行为。

您可以在 tokens: 下添加这些 token 作为新 token，或（推荐）通过 added_tokens_overrides: 覆盖未使用的 added_tokens。请参阅配置了解更多详情。

如果 EOS token 仅出现在提示末尾，train_on_eos: last 等同于 train_on_eos: turn。因此，通常您可以将它们保留为默认值并省略它们。

不是通过系统提示传递工具，另一种方法是将工具放在单独的列中，并通过 chat_template 加载，让模板动态构建它。

工具需要遵循 JSON schema。

如果您有同名但不同 dtype 的工具参数（如"time": string 和"time": number），请将 arguments: 保存为 JSON 字符串以防止数据集出现转换问题。

Llama4 的示例配置：

查看您使用的 chat_template 是否支持工具以及工具答案的预期角色。在上面的示例中，对于 llama4 模板，工具答案预期在 tool 或 ipython 角色中。

（高级）对对话中要训练的 token 和轮次使用细粒度控制

对于如下数据样本：

配置如下：

不必同时设置 message_field_training 和 message_field_training_detail。

（仅适用于 Qwen3 模板）启用推理分割，其中推理从内容中分割出来并作为单独的字段传递到模板中。

例如，内容可以如下：

分割后，它将如下所示：

ShareGPT 已弃用！请参阅 chat_template 部分。

**示例：**

示例 1 (json)：
```json
{"messages": [{"role": "...", "content": "..."}, {"role": "...", "content": "..."}, ...]}
```

示例 2 (yaml)：
```yaml
# 旧
chat_template: chatml
datasets:
  - path: ...
    type: sharegpt
    conversation: chatml

# 新（如果使用分词器的 chat_template）
datasets:
  - path: ...
    type: chat_template

    field_messages: conversations
    message_property_mappings:
      role: from
      content: value

# 新（如果设置新的 chat_template 如 chatml、gemma 等）
chat_template: chatml
datasets:
  - path: ...
    type: chat_template

    field_messages: conversations
    message_property_mappings:
      role: from
      content: value
```

示例 3 (yaml)：
```yaml
datasets:
  - path: ...
    type: chat_template
    roles_to_train:
    train_on_eos:
```

示例 4 (yaml)：
```yaml
chat_template: gemma # 这覆盖分词器的 chat_template
datasets:
  - path: ...
    type: chat_template
    roles_to_train: ["assistant"]  # 默认值
```

---

## 预训练

**URL：** https://docs.axolotl.ai/docs/dataset-formats/pretraining.html

**内容：**
- 预训练

对于预训练，没有提示模板或角色。唯一必需的字段是 text：

Axolotl 通常将整个数据集加载到内存中。这对大型数据集来说是个挑战。使用以下配置启用流式传输：

**示例：**

示例 1 (json)：
```json
{"text": "first row"}
{"text": "second row"}
...
```

示例 2 (yaml)：
```yaml
pretraining_dataset:
  - name:
    path:
    split:
    text_column: # 数据集中包含数据的列，通常是 `text`
    type: pretrain
    trust_remote_code:
    skip: # 从开头跳过的数据行数
```

---

## 无模板

**URL：** https://docs.axolotl.ai/docs/dataset-formats/template_free.html

**内容：**
- 无模板
- 背景
  - 屏蔽输入
  - 您可能不想要提示模板
  - input_output 格式
- 用法
  - 1. 准备数据
  - 2. 使用 type: input_output
  - 3. 检查提示

Axolotl 最受欢迎的功能之一是设置以下配置值：

如果您声明数据集格式如 alpaca 或 chatml，axolotl 知道什么是输入（即人类）与输出（即助手），并屏蔽输入标签，以便您的模型可以专注于仅预测输出。

但是，有很多情况您不想使用这些格式或模板之一。这是因为它们可以：

您可以通过使用 input_output 格式构建没有模板的提示，在配置文件中设置 type: input_output：

与也是无模板的 type: completion 不同，type: input_output 允许您屏蔽文本的片段。有关其工作原理的更多详情如下所述。

这是您可以使用 input_output 格式的方式：

要使用 input_output 格式，将数据收集为以下格式到 jsonl 文件中（下面是文件 output.jsonl` 第一行的美化打印）：

当您想屏蔽文本片段以便模型不在其上训练时，设置 label:false。需要注意的一些事项：

[!重要] 1. EOS、BOS、空格、换行等完全由您决定。Axolotl 按原样连接所有片段。分词器不添加任何额外内容。注意我自己添加了空格、换行、<s>（BOS）和 </s>（EOS）。2. 确保检查具体化输出以验证提示按您喜欢的方式组装。

让我们通过在 axolotl 配置中设置 type: input_output 来使用 output.jsonl 文件具体化数据：

您可以使用以下命令具体化数据。--debug 标志将打印 token 以及标签，以便您可以验证正确的项目被忽略：

格式是 decoded_token(label, token_id)，例如，<s>(1, 1) 表示 token 是 <s>，标签是 1，token_id 是 1。当标签是 -100 时，该 token 在训练中被忽略。

这是检查具体化输出的另一种方式：

我们可以通过将标签与每个 token 比较来检查正确的 token 被忽略：

如果我们查看输入数据，上表似乎是正确的！（jsonl 版本在下面重复以供参考）：

**示例：**

示例 1 (yaml)：
```yaml
train_on_inputs: false
```

示例 2 (yaml)：
```yaml
train_on_inputs: false # 屏蔽数据片段
datasets:
  - path: output.jsonl
    type: input_output  # 使用无模板提示构建
```

示例 3 (bash)：
```bash
$ head -n1 output.jsonl | python -m json.tool
```

示例 4 (unknown)：
```unknown
{
    "segments": [
        {
            "label": true,
            "text": "<s>Hello\n"
        },
        {
            "label": true,
            "text": "hi there!. "
        },
        {
            "label": false,
            "text": "goodbye "
        },
        {
            "label": true,
            "text": "farewell</s>"
        }
    ]
}
```

---

## 数据集格式

**URL：** https://docs.axolotl.ai/docs/dataset-formats/

**内容：**
- 数据集格式
- 预训练
  - 从 Hugging Face hub 数据集预训练
  - 从本地数据集文件预训练
  - 无流式预训练
  - 预训练数据集配置提示
    - 设置 max_steps
    - Group_by_length
  - 参考
- 监督微调 (SFT)

Axolotl 是一个训练框架，旨在通过简单地传递配置 yaml 文件使过程对用户方便而灵活。

由于 Axolotl 中有很多可用选项，本指南旨在简化用户体验，帮助选择正确的选择。

Axolotl 支持 3 种训练方法：预训练、监督微调和基于偏好的后训练（例如 DPO、ORPO、PRM）。每种方法都有自己的数据集格式，如下所述。

本指南主要使用 JSONL 作为介绍。请参考数据集加载文档了解如何从其他来源加载数据集。

对于 pretraining_dataset：具体来说，请参考预训练部分。

当旨在训练大型文本语料库数据集时，预训练是您的首选。由于这些数据集的大小，在开始训练之前下载整个数据集将非常耗时。Axolotl 支持流式传输，一次只将批次加载到内存中。

预训练数据集的示例格式如下：

通常建议将数据集保存为 .jsonl，因为其灵活性和简单性。

Axolotl 支持从 Hugging Face hub 仓库或本地文件加载。

例如，要使用 Hugging Face 数据集 hf_org/name 进行训练，可以传递以下配置：

给定几个语料库文件：A.jsonl、B.jsonl 和 C.jsonl，您的配置将如下所示：

虽然我们推荐 .jsonl，您也可以使用 Dataset.load_dataset 支持的其他格式（csv、parquet、arrow、SQL、Webdataset）

如果数据集很小并且可以完全加载到内存中，运行预训练的另一种方法是使用 completion 格式。这意味着整个数据集是预分词的，而不是按需流式分词。

这样做的一个好处是分词可以在仅 CPU 的机器上单独执行，然后传输到 GPU 机器进行训练以节省成本。

仅对于 completion，Axolotl 会将超过上下文长度的文本分割成多个较小的提示。如果您希望 pretraining_dataset 也有此功能，请告诉我们或帮助提交 PR！

当对大型数据集使用流式传输时，Axolotl 无法预先知道数据集有多大，也不知道何时停止。

因此，必须在配置中设置 max_steps: int 才能运行预训练，以便 Axolotl 知道何时停止训练。

一步等于 sequence_len * micro_batch_size * gradient_accumulation_steps * total_num_gpus 个 token。

如果从 Hugging Face hub 下载，建议关闭此选项，因为它会下载整个数据集，这可能非常大。

请参阅此处的文档。

监督微调是训练模型响应指令或聊天输入的过程。

由于数据集格式多种多样，Axolotl 尝试支持公共数据集中可用的大多数格式。

Axolotl 提供四种加载数据集的方法，但是，从您可用的数据集反向推导使用哪种方法更容易。

流程图如下：

您是否已经对数据集进行了分词？如果是，请查看预分词数据集。

您是否想自己格式化数据集并手动选择要屏蔽的每个部分？如果是，请查看无模板数据集

您的数据集是否为"conversation"格式，包含 list[messages]？如果是，请查看对话数据集

您的数据集是否为"instruct"格式，包含 { instruction, response }？如果是，请查看指令数据集

如果您完成了流程图但没有找到匹配的，建议将数据集预处理为上述之一或在 Github Discussion 上创建主题。

您可以在每种方法内或跨方法混合搭配，在多种数据集上训练模型。

当您想带自己的分词数据集时，我们建议此方法。

Axolotl 期望数据集有三个键：

确保将 BOS/EOS token 添加到提示中并适当屏蔽。

此配置如下所示：

参考：预分词数据集文档。

当您想对提示格式化、特殊 token 和屏蔽进行细粒度控制，同时让 Axolotl 处理分词时，我们推荐此方法。如果您的数据集具有跨样本不同的独特提示，且单一通用模板无法满足，这非常有用。

在下面的示例中，您可以看到没有正确的结构。同时，它非常灵活，因为提示的外观没有约束。

每个提示必须有一个名为 segments 的键，它是 { text, label } 的列表。

参考：无模板文档。

对话消息是消息列表，通常包含 role 和 content 键。

趣闻：Axolotl 将"chat"消息同义地称为对话消息，因为 FastChat 最初使用此术语构建了广泛使用的 fastchat 对话方法来格式化聊天消息，这在 chat_templates 创建之前。

当前最流行和方便的推理方法是使用 chat_templates 来格式化提示。Axolotl 支持使用 chat_templates 进行训练，以确保模型在相同环境中执行推理。

以下是 chat_template 的快速概述：chat_template 是一个 Jinja2 模板，将消息列表格式化为提示。

格式化为名为 ChatML 的流行模板的提示示例如下：

单个提示（美化打印）：

ChatML 模板如下：

上述提示格式化到此模板将产生：

通过使用分隔符（<|im_start|> 和 <|im_end|>），提示分隔不同的说话者，帮助模型识别哪部分属于谁。

具有以下格式的旧对话数据集通俗地称为 sharegpt 数据集。

较新的对话数据集通常遵循 OpenAI 格式。

Axolotl 支持两者，并允许自定义任何类型的键。

要正确使用此方法，识别三件事很重要：

您想使用哪个 chat_template？

数据集中的键是什么，可能的角色是什么？例如，在 OpenAI 格式中，键分别是 messages、role 和 content，而可能的角色是 system、user 和 assistant。

您想屏蔽什么？例如，仅 assistant 消息、仅最后一条消息或什么都不屏蔽。

有很多 chat_template。Axolotl 支持常见的：支持的聊天模板。例如，要使用 ChatML，应该是 chat_template: chatml。

但是，也可以通过指定 chat_template: tokenizer_default 使用分词器中已配置的模板。如果您想要回退（以防某些分词器没有预配置），可以执行 chat_template: tokenizer_default_fallback_chatml 在未找到分词器模板时回退到 ChatML 模板。

最后但强大的一种方法是带您自己的模板。可以通过以下方式设置：

我们目前默认使用 OpenAI 格式作为数据集键，所以如果这是您当前的数据集格式，这里没有什么要做的。

如果您的数据集格式不同，以下是您应该检查的键（及其默认值）：

在某些 chat_template（例如 Gemma）中，角色被硬编码为 user 和 assistant。因此，您可能发现有必要将数据集中的角色映射到上述角色。我们目前有一些适用于常见数据集的默认值，但如果您收到 KeyError，则需要为您的角色添加映射。以下是它的示例：

在上面的示例中，所有 gpt 和 model 值都转换为 assistant。所有 human 值都转换为 user。

chat_template 的常见用例是用于聊天消息，因此，屏蔽所有非 assistant 消息是常见的。Assistant 消息是指您希望模型学习的机器人消息。

要在所有 assistant 消息上训练，您需要设置以下配置。

train_on_eos 配置意味着它将屏蔽所有非 assistant 轮次的 EOS token。其他选项是：all 和 last 来选择要训练的 EOS。

也许您想在 assistant 和 narrator 角色上训练，只需将 narrator 添加到 roles_to_train 列表。您还需要将其添加到上面的角色映射中。

由于 chat_template 可能使用与分词器 EOS 不同的硬编码 EOS/EOT token，强烈建议设置它们。例如，ChatML 使用 <|im_end|> 来结束轮次。

完成上述所有步骤后，您可以将所有这些配置组合在一起，为您的自定义数据集形成定制配置。

如果将此配置应用于上面的示例数据集，输出将如下所示（可以通过 axolotl preprocess config.yaml --debug 检索）：

第一个数字指的是标签，第二个指的是 token_id。例如，-100 标签出现在非 assistant 部分，意味着它们被屏蔽。对于 assistant 部分，标签与 token_id 相同。

如果在预处理期间有很多 Could not find content __ boundary 警告，请查看 chat_templates 的 FAQ 部分。

请参阅此处的文档。

指令数据集用于训练指令遵循模型，包含一个提示（包含指令）和单个响应。与可能是多轮的聊天数据集相比，指令数据集通常是单轮的。

一个示例是称为 Alpaca 的常见格式：

使用这些键，可以基于它构建提示。

可以这样配置：

Axolotl 支持多种指令数据集。所有这些都可以在指令数据集文档中找到，以及它们各自的类型和示例行格式。

由于指令格式的无数可能性，Axolotl 允许自定义您自己的指令格式，而无需直接深入代码。

在下面的示例中，使用示例行以 mistral_v1 格式输出。

配置设置 field_instruction 实际上名为 input，field_input 为空，因为此示例中没有输入。通常，instruction 可以被认为是向模型提出的问题，input 是附加信息，output 是响应。没有必要有 input 或 system。最后，最重要的部分是了解您希望它看起来像什么格式，以及如何将其自定义到您的用例。

参考：自定义指令提示格式文档。

由于有多种 RLHF 方法及其各自的数据集要求。请参阅 RLHF 文档了解更多详情。

**示例：**

示例 1 (json)：
```json
{"text": "first row"}
{"text": "second row"}
...
```

示例 2 (yaml)：
```yaml
pretraining_dataset: hf_org/name
```

示例 3 (yaml)：
```yaml
pretraining_dataset:
  - path: json
    data_files:
      - A.jsonl
      - B.jsonl
      - C.jsonl
```

示例 4 (yaml)：
```yaml
datasets:
  - path: hf_org/name
    type: completion
```

---

## 数据集格式

**URL：** https://docs.axolotl.ai/docs/dataset-formats

**内容：**
- 数据集格式
- 预训练
  - 从 Hugging Face hub 数据集预训练
  - 从本地数据集文件预训练
  - 无流式预训练
  - 预训练数据集配置提示
    - 设置 max_steps
    - Group_by_length
  - 参考
- 监督微调 (SFT)

Axolotl 是一个训练框架，旨在通过简单地传递配置 yaml 文件使过程对用户方便而灵活。

由于 Axolotl 中有很多可用选项，本指南旨在简化用户体验，帮助选择正确的选择。

Axolotl 支持 3 种训练方法：预训练、监督微调和基于偏好的后训练（例如 DPO、ORPO、PRM）。每种方法都有自己的数据集格式，如下所述。

本指南主要使用 JSONL 作为介绍。请参考数据集加载文档了解如何从其他来源加载数据集。

对于 pretraining_dataset：具体来说，请参考预训练部分。

当旨在训练大型文本语料库数据集时，预训练是您的首选。由于这些数据集的大小，在开始训练之前下载整个数据集将非常耗时。Axolotl 支持流式传输，一次只将批次加载到内存中。

预训练数据集的示例格式如下：

通常建议将数据集保存为 .jsonl，因为其灵活性和简单性。

Axolotl 支持从 Hugging Face hub 仓库或本地文件加载。

例如，要使用 Hugging Face 数据集 hf_org/name 进行训练，可以传递以下配置：

给定几个语料库文件：A.jsonl、B.jsonl 和 C.jsonl，您的配置将如下所示：

虽然我们推荐 .jsonl，您也可以使用 Dataset.load_dataset 支持的其他格式（csv、parquet、arrow、SQL、Webdataset）

如果数据集很小并且可以完全加载到内存中，运行预训练的另一种方法是使用 completion 格式。这意味着整个数据集是预分词的，而不是按需流式分词。

这样做的一个好处是分词可以在仅 CPU 的机器上单独执行，然后传输到 GPU 机器进行训练以节省成本。

仅对于 completion，Axolotl 会将超过上下文长度的文本分割成多个较小的提示。如果您希望 pretraining_dataset 也有此功能，请告诉我们或帮助提交 PR！

当对大型数据集使用流式传输时，Axolotl 无法预先知道数据集有多大，也不知道何时停止。

因此，必须在配置中设置 max_steps: int 才能运行预训练，以便 Axolotl 知道何时停止训练。

一步等于 sequence_len * micro_batch_size * gradient_accumulation_steps * total_num_gpus 个 token。

如果从 Hugging Face hub 下载，建议关闭此选项，因为它会下载整个数据集，这可能非常大。

请参阅此处的文档。

监督微调是训练模型响应指令或聊天输入的过程。

由于数据集格式多种多样，Axolotl 尝试支持公共数据集中可用的大多数格式。

Axolotl 提供四种加载数据集的方法，但是，从您可用的数据集反向推导使用哪种方法更容易。

流程图如下：

您是否已经对数据集进行了分词？如果是，请查看预分词数据集。

您是否想自己格式化数据集并手动选择要屏蔽的每个部分？如果是，请查看无模板数据集

您的数据集是否为"conversation"格式，包含 list[messages]？如果是，请查看对话数据集

您的数据集是否为"instruct"格式，包含 { instruction, response }？如果是，请查看指令数据集

如果您完成了流程图但没有找到匹配的，建议将数据集预处理为上述之一或在 Github Discussion 上创建主题。

您可以在每种方法内或跨方法混合搭配，在多种数据集上训练模型。

当您想带自己的分词数据集时，我们建议此方法。

Axolotl 期望数据集有三个键：

确保将 BOS/EOS token 添加到提示中并适当屏蔽。

此配置如下所示：

参考：预分词数据集文档。

当您想对提示格式化、特殊 token 和屏蔽进行细粒度控制，同时让 Axolotl 处理分词时，我们推荐此方法。如果您的数据集具有跨样本不同的独特提示，且单一通用模板无法满足，这非常有用。

在下面的示例中，您可以看到没有正确的结构。同时，它非常灵活，因为提示的外观没有约束。

每个提示必须有一个名为 segments 的键，它是 { text, label } 的列表。

参考：无模板文档。

对话消息是消息列表，通常包含 role 和 content 键。

趣闻：Axolotl 将"chat"消息同义地称为对话消息，因为 FastChat 最初使用此术语构建了广泛使用的 fastchat 对话方法来格式化聊天消息，这在 chat_templates 创建之前。

当前最流行和方便的推理方法是使用 chat_templates 来格式化提示。Axolotl 支持使用 chat_templates 进行训练，以确保模型在相同环境中执行推理。

以下是 chat_template 的快速概述：chat_template 是一个 Jinja2 模板，将消息列表格式化为提示。

格式化为名为 ChatML 的流行模板的提示示例如下：

单个提示（美化打印）：

ChatML 模板如下：

上述提示格式化到此模板将产生：

通过使用分隔符（<|im_start|> 和 <|im_end|>），提示分隔不同的说话者，帮助模型识别哪部分属于谁。

具有以下格式的旧对话数据集通俗地称为 sharegpt 数据集。

较新的对话数据集通常遵循 OpenAI 格式。

Axolotl 支持两者，并允许自定义任何类型的键。

要正确使用此方法，识别三件事很重要：

您想使用哪个 chat_template？

数据集中的键是什么，可能的角色是什么？例如，在 OpenAI 格式中，键分别是 messages、role 和 content，而可能的角色是 system、user 和 assistant。

您想屏蔽什么？例如，仅 assistant 消息、仅最后一条消息或什么都不屏蔽。

有很多 chat_template。Axolotl 支持常见的：支持的聊天模板。例如，要使用 ChatML，应该是 chat_template: chatml。

但是，也可以通过指定 chat_template: tokenizer_default 使用分词器中已配置的模板。如果您想要回退（以防某些分词器没有预配置），可以执行 chat_template: tokenizer_default_fallback_chatml 在未找到分词器模板时回退到 ChatML 模板。

最后但强大的一种方法是带您自己的模板。可以通过以下方式设置：

我们目前默认使用 OpenAI 格式作为数据集键，所以如果这是您当前的数据集格式，这里没有什么要做的。

如果您的数据集格式不同，以下是您应该检查的键（及其默认值）：

在某些 chat_template（例如 Gemma）中，角色被硬编码为 user 和 assistant。因此，您可能发现有必要将数据集中的角色映射到上述角色。我们目前有一些适用于常见数据集的默认值，但如果您收到 KeyError，则需要为您的角色添加映射。以下是它的示例：

在上面的示例中，所有 gpt 和 model 值都转换为 assistant。所有 human 值都转换为 user。

chat_template 的常见用例是用于聊天消息，因此，屏蔽所有非 assistant 消息是常见的。Assistant 消息是指您希望模型学习的机器人消息。

要在所有 assistant 消息上训练，您需要设置以下配置。

train_on_eos 配置意味着它将屏蔽所有非 assistant 轮次的 EOS token。其他选项是：all 和 last 来选择要训练的 EOS。

也许您想在 assistant 和 narrator 角色上训练，只需将 narrator 添加到 roles_to_train 列表。您还需要将其添加到上面的角色映射中。

由于 chat_template 可能使用与分词器 EOS 不同的硬编码 EOS/EOT token，强烈建议设置它们。例如，ChatML 使用 <|im_end|> 来结束轮次。

完成上述所有步骤后，您可以将所有这些配置组合在一起，为您的自定义数据集形成定制配置。

如果将此配置应用于上面的示例数据集，输出将如下所示（可以通过 axolotl preprocess config.yaml --debug 检索）：

第一个数字指的是标签，第二个指的是 token_id。例如，-100 标签出现在非 assistant 部分，意味着它们被屏蔽。对于 assistant 部分，标签与 token_id 相同。

如果在预处理期间有很多 Could not find content __ boundary 警告，请查看 chat_templates 的 FAQ 部分。

请参阅此处的文档。

指令数据集用于训练指令遵循模型，包含一个提示（包含指令）和单个响应。与可能是多轮的聊天数据集相比，指令数据集通常是单轮的。

一个示例是称为 Alpaca 的常见格式：

使用这些键，可以基于它构建提示。

可以这样配置：

Axolotl 支持多种指令数据集。所有这些都可以在指令数据集文档中找到，以及它们各自的类型和示例行格式。

由于指令格式的无数可能性，Axolotl 允许自定义您自己的指令格式，而无需直接深入代码。

在下面的示例中，使用示例行以 mistral_v1 格式输出。

配置设置 field_instruction 实际上名为 input，field_input 为空，因为此示例中没有输入。通常，instruction 可以被认为是向模型提出的问题，input 是附加信息，output 是响应。没有必要有 input 或 system。最后，最重要的部分是了解您希望它看起来像什么格式，以及如何将其自定义到您的用例。

参考：自定义指令提示格式文档。

由于有多种 RLHF 方法及其各自的数据集要求。请参阅 RLHF 文档了解更多详情。

**示例：**

示例 1 (json)：
```json
{"text": "first row"}
{"text": "second row"}
...
```

示例 2 (yaml)：
```yaml
pretraining_dataset: hf_org/name
```

示例 3 (yaml)：
```yaml
pretraining_dataset:
  - path: json
    data_files:
      - A.jsonl
      - B.jsonl
      - C.jsonl
```

示例 4 (yaml)：
```yaml
datasets:
  - path: hf_org/name
    type: completion
```

---

## 指令微调

**URL：** https://docs.axolotl.ai/docs/dataset-formats/inst_tune.html

**内容：**
- 指令微调
- alpaca
- jeopardy
- oasst
- gpteacher
- reflection
- explainchoice
- concisechoice
- summarizetldr
- alpaca_chat

instruction; input(可选)

instruction; input(可选)

带 reflect 的 instruction; input(可选)

question, choices, (solution OR explanation)

question, choices, (solution OR explanation)

alpaca chat 的基本指令

alpaca chat 的问答

alpaca chat 的问答，用于简洁答案

alpaca chat 的问答，用于 load_camel_ai

支持包含系统提示的 open orca 数据集，instruct

文章的上下文问答

上下文问答（替代）

文章的上下文问答，带有上下文无答案的默认响应

指令和修订

instruction，添加额外的 eos token

对于为指令目的预处理的数据集：

您可以在 YAML 配置中使用此示例：

请参阅此处的完整配置选项。

**示例：**

示例 1 (json)：
```json
{"instruction": "...", "input": "...", "output": "..."}
```

示例 2 (json)：
```json
{"question": "...", "category": "...", "answer": "..."}
```

示例 3 (json)：
```json
{"INSTRUCTION": "...", "RESPONSE": "..."}
```

示例 4 (json)：
```json
{"instruction": "...", "input": "...", "response": "..."}
```

---

## 逐步监督格式

**URL：** https://docs.axolotl.ai/docs/dataset-formats/stepwise_supervised.html

**内容：**
- 逐步监督格式
- 逐步监督
  - 示例

逐步监督格式专为思维链 (COT) 推理数据集设计，其中每个示例包含多个补全步骤和每个步骤的偏好标签。

这是逐步监督数据集条目的简单示例：

**示例：**

示例 1 (json)：
```json
{
  "prompt": "Which number is larger, 9.8 or 9.11?",
  "completions": [
    "The fractional part of 9.8 is 0.8, while the fractional part of 9.11 is 0.11.",
    "Since 0.11 is greater than 0.8, the number 9.11 is larger than 9.8."
  ],
  "labels": [true, false]
}
```

---
