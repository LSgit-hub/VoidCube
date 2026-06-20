# Axolotl - API

**页面：** 150

---

## cli.cloud.modal_

**URL：** https://docs.axolotl.ai/docs/api/cli.cloud.modal_.html

**内容：**
- cli.cloud.modal_
- 类
  - ModalCloud
- 函数
  - run_cmd

来自 CLI 的 Modal Cloud 支持

Modal Cloud 实现。

在文件夹内运行命令，成功前重新加载 Modal Volume 并在成功时提交。

**示例：**

示例 1 (python)：
```python
cli.cloud.modal_.ModalCloud(config, app=None)
```

示例 2 (python)：
```python
cli.cloud.modal_.run_cmd(cmd, run_folder, volumes=None)
```

---

## core.trainers.base

**URL：** https://docs.axolotl.ai/docs/api/core.trainers.base.html

**内容：**
- core.trainers.base
- 类
  - AxolotlTrainer
    - 方法
      - log
        - 参数
      - push_to_hub
      - store_metrics
        - 参数

自定义训练器模块

扩展基础 Trainer 以添加 axolotl 辅助功能

在各种监视训练的对象上记录日志，包括存储的指标。

覆盖 push_to_hub 方法以便在将模型推送到 Hub 时强制添加标签。有关更多详细信息，请参阅 ~transformers.Trainer.push_to_hub。

使用指定的归约类型存储指标。

**示例：**

示例 1 (python)：
```python
core.trainers.base.AxolotlTrainer(
    *_args,
    bench_data_collator=None,
    eval_data_collator=None,
    dataset_tags=None,
    **kwargs,
)
```

示例 2 (python)：
```python
core.trainers.base.AxolotlTrainer.log(logs, start_time=None)
```

示例 3 (python)：
```python
core.trainers.base.AxolotlTrainer.push_to_hub(*args, **kwargs)
```

示例 4 (python)：
```python
core.trainers.base.AxolotlTrainer.store_metrics(
    metrics,
    train_eval='train',
    reduction='mean',
)
```

---

## prompt_strategies.input_output

**URL：** https://docs.axolotl.ai/docs/api/prompt_strategies.input_output.html

**内容：**
- prompt_strategies.input_output
- 类
  - RawInputOutputPrompter
  - RawInputOutputStrategy

prompt_strategies.input_output

纯输入/输出提示对模块

原始 I/O 数据的提示器

输入/输出对的提示策略类

**示例：**

示例 1 (python)：
```python
prompt_strategies.input_output.RawInputOutputPrompter()
```

示例 2 (python)：
```python
prompt_strategies.input_output.RawInputOutputStrategy(
    *args,
    eos_token=None,
    **kwargs,
)
```

---

## prompt_strategies.completion

**URL：** https://docs.axolotl.ai/docs/api/prompt_strategies.completion.html

**内容：**
- prompt_strategies.completion
- 类
  - CompletionPromptTokenizingStrategy
  - CompletionPrompter

prompt_strategies.completion

基本补全文本

补全提示的分词策略。

补全的提示器

**示例：**

示例 1 (python)：
```python
prompt_strategies.completion.CompletionPromptTokenizingStrategy(
    *args,
    max_length=None,
    **kwargs,
)
```

示例 2 (python)：
```python
prompt_strategies.completion.CompletionPrompter()
```

---

## utils.collators.core

**URL：** https://docs.axolotl.ai/docs/api/utils.collators.core.html

**内容：**
- utils.collators.core

基本共享整理器常量

---

## monkeypatch.data.batch_dataset_fetcher

**URL：** https://docs.axolotl.ai/docs/api/monkeypatch.data.batch_dataset_fetcher.html

**内容：**
- monkeypatch.data.batch_dataset_fetcher
- 函数
  - apply_multipack_dataloader_patch
  - patch_fetchers
  - patched_worker_loop
  - remove_multipack_dataloader_patch

monkeypatch.data.batch_dataset_fetcher

数据集获取器的猴子补丁，用于处理打包索引批次。

此补丁允许 DataLoader 正确处理包含多个打包序列 bin 的批次。

将补丁应用于 PyTorch 的 DataLoader 组件。

确保在工作进程中应用补丁的工作器循环。

移除猴子补丁并恢复原始 PyTorch DataLoader 行为。

**示例：**

示例 1 (python)：
```python
monkeypatch.data.batch_dataset_fetcher.apply_multipack_dataloader_patch()
```

示例 2 (python)：
```python
monkeypatch.data.batch_dataset_fetcher.patch_fetchers()
```

示例 3 (python)：
```python
monkeypatch.data.batch_dataset_fetcher.patched_worker_loop(*args, **kwargs)
```

示例 4 (python)：
```python
monkeypatch.data.batch_dataset_fetcher.remove_multipack_dataloader_patch()
```

---

## core.datasets.chat

**URL：** https://docs.axolotl.ai/docs/api/core.datasets.chat.html

**内容：**
- core.datasets.chat
- 类
  - TokenizedChatDataset

分词聊天数据集

**示例：**

示例 1 (python)：
```python
core.datasets.chat.TokenizedChatDataset(
    data,
    model_transform,
    *args,
    message_transform=None,
    formatter=None,
    process_count=None,
    keep_in_memory=False,
    **kwargs,
)
```

---

## utils.freeze

**URL：** https://docs.axolotl.ai/docs/api/utils.freeze.html

**内容：**
- utils.freeze
- 类
  - LayerNamePattern
    - 方法
      - match
- 函数
  - freeze_layers_except

按名称冻结/解冻参数的模块

表示层名称的正则表达式模式，可能包含参数索引范围。

检查给定的层名称是否匹配正则表达式模式。

参数： - name (str)：要检查的层名称。

返回： - bool：如果层名称匹配模式则为 True，否则为 False。

冻结给定模型的所有层，除了匹配给定正则表达式模式的层。模式中的句点被视为字面句点，而非通配符。

参数： - model (nn.Module)：要修改的 PyTorch 模型。 - regex_patterns (str 列表)：用于匹配要保持未冻结的层名称的正则表达式模式列表。注意，不能在模式中使用点作为通配符，因为它保留用于分隔层名称。此外，要匹配整个层名称，模式应以"^"开头并以"\("结尾，否则它将匹配层名称的任何部分。范围模式部分是可选的，它不会被编译为正则表达式，这意味着如果要匹配整个层名称，必须在范围模式之前放置"\)"。例如，["^model.embed_tokens.weight\([:32000]", "layers.2[0-9]+.block_sparse_moe.gate.[a-z]+\)"]

返回： None；模型就地修改。

**示例：**

示例 1 (python)：
```python
utils.freeze.LayerNamePattern(pattern)
```

示例 2 (python)：
```python
utils.freeze.LayerNamePattern.match(name)
```

示例 3 (python)：
```python
utils.freeze.freeze_layers_except(model, regex_patterns)
```

---

## monkeypatch.unsloth_

**URL：** https://docs.axolotl.ai/docs/api/monkeypatch.unsloth_.html

**内容：**
- monkeypatch.unsloth_

使用 unsloth 优化进行补丁的模块

---

## utils.schemas.datasets

**URL：** https://docs.axolotl.ai/docs/api/utils.schemas.datasets.html

**内容：**
- utils.schemas.datasets
- 类
  - DPODataset
  - KTODataset
  - PretrainingDataset
  - SFTDataset
    - 方法
      - handle_legacy_message_fields
  - StepwiseSupervisedDataset
  - UserDefinedDPOType

utils.schemas.datasets

数据集相关配置的 Pydantic 模型

DPO 配置子集

KTO 配置子集

预训练数据集配置子集

SFT 配置子集

处理旧版消息字段映射和新属性映射系统之间的向后兼容性。

逐步监督数据集配置子集

DPO 的用户定义类型

KTO 的用户定义类型

用户定义提示类型的结构

**示例：**

示例 1 (python)：
```python
utils.schemas.datasets.DPODataset()
```

示例 2 (python)：
```python
utils.schemas.datasets.KTODataset()
```

示例 3 (python)：
```python
utils.schemas.datasets.PretrainingDataset()
```

示例 4 (python)：
```python
utils.schemas.datasets.SFTDataset()
```

---

## core.chat.format.llama3x

**URL：** https://docs.axolotl.ai/docs/api/core.chat.format.llama3x.html

**内容：**
- core.chat.format.llama3x

core.chat.format.llama3x

用于 MessageContents 的 Llama 3.x 聊天格式化函数

---

## datasets

**URL：** https://docs.axolotl.ai/docs/api/datasets.html

**内容：**
- datasets
- 类
  - TokenizedPromptDataset
    - 参数

包含数据集功能的模块。

我们希望这是已加载现有数据集的包装器。让我们使用中间件的概念来包装每个数据集。稍后我们将使用整理器来填充数据集。

从文本文件流返回分词提示的数据集。

**示例：**

示例 1 (python)：
```python
datasets.TokenizedPromptDataset(
    prompt_tokenizer,
    dataset,
    process_count=None,
    keep_in_memory=False,
    **kwargs,
)
```

---

## prompt_strategies.bradley_terry.llama3

**URL：** https://docs.axolotl.ai/docs/api/prompt_strategies.bradley_terry.llama3.html

**内容：**
- prompt_strategies.bradley_terry.llama3
- 函数
  - icr

prompt_strategies.bradley_terry.llama3

具有 system、input、chosen、rejected 的数据集的 chatml 转换，以匹配 llama3 聊天模板

具有 system、input、chosen、rejected 的数据集的 chatml 转换，例如 https://huggingface.co/datasets/argilla/distilabel-intel-orca-dpo-pairs

**示例：**

示例 1 (python)：
```python
prompt_strategies.bradley_terry.llama3.icr(cfg, **kwargs)
```

---

## common.datasets

**URL：** https://docs.axolotl.ai/docs/api/common.datasets.html

**内容：**
- common.datasets
- 类
  - TrainDatasetMeta
- 函数
  - load_datasets
    - 参数
    - 返回
  - load_preference_datasets
    - 参数
    - 返回

数据集加载工具。

包含训练和验证数据集及元数据字段的数据类。

加载一个或多个训练或评估数据集，调用 axolotl.utils.data.prepare_datasets。可选地记录调试信息。

使用成对偏好数据为 RL 训练加载一个或多个训练或评估数据集，调用 axolotl.utils.data.rl.prepare_preference_datasets。可选地记录调试信息。

从数据集中随机采样 num_samples 个样本（带替换）。

**示例：**

示例 1 (python)：
```python
common.datasets.TrainDatasetMeta(
    train_dataset,
    eval_dataset=None,
    total_num_steps=None,
)
```

示例 2 (python)：
```python
common.datasets.load_datasets(cfg, cli_args=None, debug=False)
```

示例 3 (python)：
```python
common.datasets.load_preference_datasets(cfg, cli_args=None)
```

示例 4 (python)：
```python
common.datasets.sample_dataset(dataset, num_samples)
```

---

## cli.train

**URL：** https://docs.axolotl.ai/docs/api/cli.train.html

**内容：**
- cli.train
- 函数
  - do_cli
    - 参数
  - do_train
    - 参数

在模型上运行训练的 CLI。

解析 axolotl 配置、CLI 参数，并调用 do_train。

通过首先加载 axolotl 配置中指定的数据集，然后调用 axolotl.train.train 来训练 transformers 模型。训练完成后还运行插件管理器的 post_train_unload。

**示例：**

示例 1 (python)：
```python
cli.train.do_cli(config=Path('examples/'), **kwargs)
```

示例 2 (python)：
```python
cli.train.do_train(cfg, cli_args)
```

---

## cli.utils.fetch

**URL：** https://docs.axolotl.ai/docs/api/cli.utils.fetch.html

**内容：**
- cli.utils.fetch
- 函数
  - fetch_from_github
    - 参数

axolotl fetch CLI 命令的工具。

从 GitHub 仓库的特定目录同步文件。仅下载本地不存在或已更改的文件。

**示例：**

示例 1 (python)：
```python
cli.utils.fetch.fetch_from_github(dir_prefix, dest_dir=None, max_workers=5)
```

---

## utils.tokenization

**URL：** https://docs.axolotl.ai/docs/api/utils.tokenization.html

**内容：**
- utils.tokenization
- 函数
  - color_token_for_rl_debug
  - process_tokens_for_rl_debug

分词工具模块

根据令牌类型为令牌着色的辅助函数。

处理和着色令牌的辅助函数。

**示例：**

示例 1 (python)：
```python
utils.tokenization.color_token_for_rl_debug(
    decoded_token,
    encoded_token,
    color,
    text_only,
)
```

示例 2 (python)：
```python
utils.tokenization.process_tokens_for_rl_debug(
    tokens,
    color,
    tokenizer,
    text_only,
)
```

---

## core.trainers.grpo.sampler

**URL：** https://docs.axolotl.ai/docs/api/core.trainers.grpo.sampler.html

**内容：**
- core.trainers.grpo.sampler
- 类
  - SequenceParallelRepeatRandomSampler
    - 参数
    - 方法
      - set_epoch
        - 参数

core.trainers.grpo.sampler

重复随机采样器（类似于 https://github.com/huggingface/trl/blob/main/trl/trainer/grpo_trainer.py 中实现的），添加序列并行功能；即，在相同序列并行组中的 rank 之间复制数据。

具有序列并行性的 GRPO 训练采样器。

此采样器确保： - 相同序列并行 (SP) 组中的 rank 接收相同数据。 - 每个索引重复多次以采样不同的补全。 - 整个批次重复以在多次更新中重用。 - 数据在 SP 组之间正确分配。

在下表中，值表示数据集索引。每个 SP 组有 context_parallel_size = 2 个 GPU 一起处理相同数据。有 2 个 SP 组（SP0 和 SP1），world_size = 4 个总 GPU。

grad_accum=2 ▲ ▲ 0 0 [0 0 0 1 1 1] [2 2 2 3 3 3] <- SP 组获得不同数据 ▼ | 0 1 [0 0 0 1 1 1] [2 2 2 3 3 3] <- 每个 SP 组相同数据 GPU | | 1 2 [0 0 0 1 1 1] [2 2 2 3 3 3] <- 为迭代重复相同索引 num_iterations=2 ▼ 1 3 [0 0 0 1 1 1] [2 2 2 3 3 3] <- 使用梯度累积时

为此采样器设置 epoch。

**示例：**

示例 1 (python)：
```python
core.trainers.grpo.sampler.SequenceParallelRepeatRandomSampler(
    dataset,
    mini_repeat_count,
    world_size,
    rank,
    batch_size=1,
    repeat_count=1,
    context_parallel_size=1,
    shuffle=True,
    seed=0,
    drop_last=False,
)
```

示例 2 (unknown)：
```unknown
Sequence Parallel Groups
                                |       SP0        |       SP1        |
                                |  GPU 0  |  GPU 1 |  GPU 2  |  GPU 3 |
            global_step  step    <---> mini_repeat_count=3
                                    <----------> batch_size=2 per SP group
```

示例 3 (unknown)：
```unknown
2       4         [4 4 4  5 5 5]     [6 6 6  7 7 7]   <- 新批次数据索引
                 2       5         [4 4 4  5 5 5]     [6 6 6  7 7 7]
                                    ...
```

示例 4 (python)：
```python
core.trainers.grpo.sampler.SequenceParallelRepeatRandomSampler.set_epoch(epoch)
```

---

## evaluate

**URL：** https://docs.axolotl.ai/docs/api/evaluate.html

**内容：**
- evaluate
- 函数
  - evaluate
    - 参数
    - 返回
  - evaluate_dataset
    - 参数
    - 返回

模型评估模块。

在训练和验证数据集上评估模型。

评估单个数据集的辅助函数。

**示例：**

示例 1 (python)：
```python
evaluate.evaluate(cfg, dataset_meta)
```

示例 2 (python)：
```python
evaluate.evaluate_dataset(trainer, dataset, dataset_type, flash_optimum=False)
```

---

## utils.optimizers.adopt

**URL：** https://docs.axolotl.ai/docs/api/utils.optimizers.adopt.html

**内容：**
- utils.optimizers.adopt
- 函数
  - adopt

utils.optimizers.adopt

复制自 https://github.com/iShohei220/adopt

ADOPT: Modified Adam Can Converge with Any β2 with the Optimal Rate (2024) Taniguchi, Shohei and Harada, Keno and Minegishi, Gouki and Oshima, Yuta and Jeong, Seong Cheol and Nagahara, Go and Iiyama, Tomoshi and Suzuki, Masahiro and Iwasawa, Yusuke and Matsuo, Yutaka

执行 ADOPT 算法计算的函数式 API。

**示例：**

示例 1 (python)：
```python
utils.optimizers.adopt.adopt(
    params,
    grads,
    exp_avgs,
    exp_avg_sqs,
    state_steps,
    foreach=None,
    capturable=False,
    differentiable=False,
    fused=None,
    grad_scale=None,
    found_inf=None,
    has_complex=False,
    *,
    beta1,
    beta2,
    lr,
    clip_lambda,
    weight_decay,
    decouple,
    eps,
    maximize,
)
```

---

## prompt_tokenizers

**URL：** https://docs.axolotl.ai/docs/api/prompt_tokenizers.html

**内容：**
- prompt_tokenizers
- 类
  - AlpacaMultipleChoicePromptTokenizingStrategy
  - AlpacaPromptTokenizingStrategy
  - AlpacaReflectionPTStrategy
  - DatasetWrappingStrategy
  - GPTeacherPromptTokenizingStrategy
  - InstructionPromptTokenizingStrategy
  - InvalidDataException
  - JeopardyPromptTokenizingStrategy

包含 PromptTokenizingStrategy 和 Prompter 类的模块

Alpaca 多选提示的分词策略。

Alpaca 提示的分词策略。

Alpaca 反思提示的分词策略。

用于包装聊天消息数据集的抽象类

GPTeacher 提示的分词策略。

基于指令的提示的分词策略。

数据无效时抛出的异常

Jeopardy 提示的分词策略。

NomicGPT4All 提示的分词策略。

OpenAssistant 提示的分词策略。

分词策略的抽象类

反思提示的分词策略。

SummarizeTLDR 提示的分词策略。

解析分词提示并将分词的 input_ids、attention_mask 和 labels 附加到结果

返回分词提示函数的默认值

**示例：**

示例 1 (python)：
```python
prompt_tokenizers.AlpacaMultipleChoicePromptTokenizingStrategy(
    prompter,
    tokenizer,
    train_on_inputs=False,
    sequence_len=2048,
)
```

示例 2 (python)：
```python
prompt_tokenizers.AlpacaPromptTokenizingStrategy(
    prompter,
    tokenizer,
    train_on_inputs=False,
    sequence_len=2048,
)
```

示例 3 (python)：
```python
prompt_tokenizers.AlpacaReflectionPTStrategy(
    prompter,
    tokenizer,
    train_on_inputs=False,
    sequence_len=2048,
)
```

示例 4 (python)：
```python
prompt_tokenizers.DatasetWrappingStrategy()
```

---

## cli.art

**URL：** https://docs.axolotl.ai/docs/api/cli.art.html

**内容：**
- cli.art
- 函数
  - print_axolotl_text_art

Axolotl ASCII logo 工具。

打印 axolotl ASCII 艺术。

**示例：**

示例 1 (python)：
```python
cli.art.print_axolotl_text_art()
```

---

## utils.callbacks.perplexity

**URL：** https://docs.axolotl.ai/docs/api/utils.callbacks.perplexity.html

**内容：**
- utils.callbacks.perplexity
- 类
  - Perplexity
    - 方法
      - compute

utils.callbacks.perplexity

计算困惑度作为评估指标的回调。

按 https://huggingface.co/docs/transformers/en/perplexity 中的定义计算困惑度。这是一个自定义变体，不会重新分词输入或重新加载模型。

在序列的固定长度滑动窗口中计算困惑度。

**示例：**

示例 1 (python)：
```python
utils.callbacks.perplexity.Perplexity(tokenizer, max_seq_len, stride=512)
```

示例 2 (python)：
```python
utils.callbacks.perplexity.Perplexity.compute(model, references=None)
```

---

## cli.utils.train

**URL：** https://docs.axolotl.ai/docs/api/cli.utils.train.html

**内容：**
- cli.utils.train
- 函数
  - build_command
    - 参数
    - 返回
  - generate_config_files
    - 参数
  - launch_training

axolotl train CLI 命令的工具。

从基本命令和选项构建命令列表。

生成要处理的配置文件列表。生成一个元组，包含配置文件名和一个布尔值，指示这是一组配置（即扫描）。

使用给定配置执行训练。

**示例：**

示例 1 (python)：
```python
cli.utils.train.build_command(base_cmd, options)
```

示例 2 (python)：
```python
cli.utils.train.generate_config_files(config, sweep)
```

示例 3 (python)：
```python
cli.utils.train.launch_training(
    cfg_file,
    launcher,
    cloud,
    kwargs,
    launcher_args=None,
    use_exec=False,
)
```

---

## cli.vllm_serve

**URL：** https://docs.axolotl.ai/docs/api/cli.vllm_serve.html

**内容：**
- cli.vllm_serve
- 类
  - AxolotlScriptArguments
- 函数
  - do_vllm_serve
    - 返回

启动在线 RL 的 vllm 服务器的 CLI

VLLM 服务器的附加参数

启动用于在线 RL 的 LLM 模型服务的 VLLM 服务器

参数 :param cfg：YAML 配置的解析字典 :param cli_args：VllmServeCliArgs 类型的附加命令行参数字典

**示例：**

示例 1 (python)：
```python
cli.vllm_serve.AxolotlScriptArguments(
    reasoning_parser='',
    enable_reasoning=None,
)
```

示例 2 (python)：
```python
cli.vllm_serve.do_vllm_serve(config, cli_args)
```

---

## convert

**URL：** https://docs.axolotl.ai/docs/api/convert.html

**内容：**
- convert
- 类
  - FileReader
  - FileWriter
  - JsonParser
  - JsonToJsonlConverter
  - JsonlSerializer
  - StdoutWriter

包含文件读取器、文件写入器、JSON 解析器和 Jsonl 序列化器类的模块

读取文件并将其内容作为字符串返回

将字符串写入文件

将字符串解析为 JSON 并返回结果

将 JSON 文件转换为 JSONL

将 JSON 对象列表序列化为 JSONL 字符串

将字符串写入 stdout

**示例：**

示例 1 (python)：
```python
convert.FileReader()
```

示例 2 (python)：
```python
convert.FileWriter(file_path)
```

示例 3 (python)：
```python
convert.JsonParser()
```

示例 4 (python)：
```python
convert.JsonToJsonlConverter(
    file_reader,
    file_writer,
    json_parser,
    jsonl_serializer,
)
```

---

## monkeypatch.utils

**URL：** https://docs.axolotl.ai/docs/api/monkeypatch.utils.html

**内容：**
- monkeypatch.utils
- 函数
  - get_cu_seqlens
  - get_cu_seqlens_from_pos_ids
  - mask_2d_to_4d

猴子补丁的共享工具

使用 attn mask 为 flash attention 生成累积序列长度 mask

使用 pos ids 为 flash attention 生成累积序列长度 mask

将 attention_mask 从 [bsz, seq_len] 扩展为 [bsz, 1, tgt_seq_len, src_seq_len]。此扩展处理打包序列，以便序列在该序列内相互关注时共享相同的 attention mask 整数值。此扩展将 mask 转换为下三角形式以防止未来窥视。

**示例：**

示例 1 (python)：
```python
monkeypatch.utils.get_cu_seqlens(attn_mask)
```

示例 2 (python)：
```python
monkeypatch.utils.get_cu_seqlens_from_pos_ids(position_ids)
```

示例 3 (python)：
```python
monkeypatch.utils.mask_2d_to_4d(mask, dtype, tgt_len=None)
```

---

## prompt_strategies.pygmalion

**URL：** https://docs.axolotl.ai/docs/api/prompt_strategies.pygmalion.html

**内容：**
- prompt_strategies.pygmalion
- 类
  - PygmalionPromptTokenizingStrategy
  - PygmalionPrompter

prompt_strategies.pygmalion

包含 PygmalionPromptTokenizingStrategy 和 PygmalionPrompter 类的模块

Pygmalion 的分词策略。

Pygmalion 的提示器。

**示例：**

示例 1 (python)：
```python
prompt_strategies.pygmalion.PygmalionPromptTokenizingStrategy(
    prompter,
    tokenizer,
    *args,
    **kwargs,
)
```

示例 2 (python)：
```python
prompt_strategies.pygmalion.PygmalionPrompter(*args, **kwargs)
```

---

## utils.callbacks.mlflow_

**URL：** https://docs.axolotl.ai/docs/api/utils.callbacks.mlflow_.html

**内容：**
- utils.callbacks.mlflow_
- 类
  - SaveAxolotlConfigtoMlflowCallback

utils.callbacks.mlflow_

用于训练器回调的 MLFlow 模块

将 axolotl 配置保存到 mlflow 的回调

**示例：**

示例 1 (python)：
```python
utils.callbacks.mlflow_.SaveAxolotlConfigtoMlflowCallback(axolotl_config_path)
```

---

## loaders.adapter

**URL：** https://docs.axolotl.ai/docs/api/loaders.adapter.html

**内容：**
- loaders.adapter
- 函数
  - setup_quantized_meta_for_peft
  - setup_quantized_peft_meta_for_training

适配器加载功能，包括 LoRA / QLoRA 和相关工具

用虚拟函数替换 quant_state.to 以防止 PEFT 将 quant_state 移动到 meta 设备

用原始函数替换虚拟 quant_state.to 方法以允许训练继续

**示例：**

示例 1 (python)：
```python
loaders.adapter.setup_quantized_meta_for_peft(model)
```

示例 2 (python)：
```python
loaders.adapter.setup_quantized_peft_meta_for_training(model)
```

---

## cli.cloud.base

**URL：** https://docs.axolotl.ai/docs/api/cli.cloud.base.html

**内容：**
- cli.cloud.base
- 类
  - Cloud

来自 CLI 的云平台基类

云平台的抽象基类。

**示例：**

示例 1 (python)：
```python
cli.cloud.base.Cloud()
```

---

## monkeypatch.llama_attn_hijack_flash

**URL：** https://docs.axolotl.ai/docs/api/monkeypatch.llama_attn_hijack_flash.html

**内容：**
- monkeypatch.llama_attn_hijack_flash
- 函数
  - flashattn_forward_with_s2attn

monkeypatch.llama_attn_hijack_flash

llama 模型的 Flash attention 猴子补丁

输入形状：Batch x Time x Channel

来自：https://github.com/dvlab-research/LongLoRA/blob/main/llama_attn_replace.py

attention_mask：[bsz, q_len]

如果提供 max_seqlen，cu_seqlens 将被忽略

**示例：**

示例 1 (python)：
```python
monkeypatch.llama_attn_hijack_flash.flashattn_forward_with_s2attn(
    self,
    hidden_states,
    attention_mask=None,
    position_ids=None,
    past_key_value=None,
    output_attentions=False,
    use_cache=False,
    padding_mask=None,
    cu_seqlens=None,
    max_seqlen=None,
)
```

---

## monkeypatch.llama_patch_multipack

**URL：** https://docs.axolotl.ai/docs/api/monkeypatch.llama_patch_multipack.html

**内容：**
- monkeypatch.llama_patch_multipack

monkeypatch.llama_patch_multipack

修补的 LlamaAttention 使用 torch.nn.functional.scaled_dot_product_attention

---

## cli.inference

**URL：** https://docs.axolotl.ai/docs/api/cli.inference.html

**内容：**
- cli.inference
- 函数
  - do_cli
    - 参数
  - do_inference
    - 参数
  - do_inference_gradio
    - 参数
  - get_multi_line_input
    - 返回

在训练模型上运行推理的 CLI。

解析 axolotl 配置、CLI 参数，并调用 do_inference 或 do_inference_gradio。

在命令行循环运行推理。接受用户输入，（可选）应用聊天模板，并使用 axolotl 配置中指定的模型根据默认生成配置生成补全。

在 Gradio 界面中运行推理。接受用户输入，（可选）应用聊天模板，并使用 axolotl 配置中指定的模型根据默认生成配置生成补全。

从终端获取多行输入。

**示例：**

示例 1 (python)：
```python
cli.inference.do_cli(config=Path('examples/'), gradio=False, **kwargs)
```

示例 2 (python)：
```python
cli.inference.do_inference(cfg, cli_args)
```

示例 3 (python)：
```python
cli.inference.do_inference_gradio(cfg, cli_args)
```

示例 4 (python)：
```python
cli.inference.get_multi_line_input()
```

---

## loaders.tokenizer

**URL：** https://docs.axolotl.ai/docs/api/loaders.tokenizer.html

**内容：**
- loaders.tokenizer
- 函数
  - load_tokenizer
  - modify_tokenizer_files
    - 参数
    - 返回

分词器加载功能和相关工具

根据提供的配置加载和配置分词器。

修改分词器文件以替换 added_tokens 字符串，保存到输出目录，并返回修改后分词器的路径。

这仅适用于添加到分词器的保留令牌，不适用于已经是词汇一部分的令牌。

参考：https://github.com/huggingface/transformers/issues/27974#issuecomment-1854188941

**示例：**

示例 1 (python)：
```python
loaders.tokenizer.load_tokenizer(cfg)
```

示例 2 (python)：
```python
loaders.tokenizer.modify_tokenizer_files(
    tokenizer_path,
    token_mappings,
    output_dir,
)
```

---

## cli.utils.sweeps

**URL：** https://docs.axolotl.ai/docs/api/cli.utils.sweeps.html

**内容：**
- cli.utils.sweeps
- 函数
  - generate_sweep_configs
    - 参数
    - 返回
    - 示例

处理 axolotl train CLI 命令配置扫描的工具

通过将扫描应用于基本配置递归生成所有可能的配置。

sweeps_config = { 'learning_rate': [0.1, 0.01], '_': [ {'load_in_8bit': True, 'adapter': 'lora'}, {'load_in_4bit': True, 'adapter': 'qlora'} ] }

**示例：**

示例 1 (python)：
```python
cli.utils.sweeps.generate_sweep_configs(base_config, sweeps_config)
```

---

## prompt_strategies.dpo.chatml

**URL：** https://docs.axolotl.ai/docs/api/prompt_strategies.dpo.chatml.html

**内容：**
- prompt_strategies.dpo.chatml
- 函数
  - argilla_chat
  - icr
  - intel
  - ultra

prompt_strategies.dpo.chatml

chatml 的 DPO 策略

用于 argilla/dpo-mix-7k 对话

具有 system、input、chosen、rejected 的数据集的 chatml 转换，例如 https://huggingface.co/datasets/argilla/distilabel-intel-orca-dpo-pairs

用于 Intel Orca DPO 对

用于 ultrafeedback 二值化对话

**示例：**

示例 1 (python)：
```python
prompt_strategies.dpo.chatml.argilla_chat(cfg, **kwargs)
```

示例 2 (python)：
```python
prompt_strategies.dpo.chatml.icr(cfg, **kwargs)
```

示例 3 (python)：
```python
prompt_strategies.dpo.chatml.intel(cfg, **kwargs)
```

示例 4 (python)：
```python
prompt_strategies.dpo.chatml.ultra(cfg, **kwargs)
```

---

## cli.quantize

**URL：** https://docs.axolotl.ai/docs/api/cli.quantize.html

**内容：**
- cli.quantize
- 函数
  - do_quantize
    - 参数

使用 torchao 进行训练后量化模型的 CLI

量化模型的权重

**示例：**

示例 1 (python)：
```python
cli.quantize.do_quantize(config, cli_args)
```

---

## utils.dict

**URL：** https://docs.axolotl.ai/docs/api/utils.dict.html

**内容：**
- utils.dict
- 类
  - DictDefault
- 函数
  - remove_none_values

包含 DictDefault 类的模块

对于缺失键返回 None 而非返回空 Dict 的 Dict。

从类似字典的对象或列表中移除 null。这些可能由于数据集加载导致模式合并而出现。参见 https://github.com/axolotl-ai-cloud/axolotl/pull/2909

**示例：**

示例 1 (python)：
```python
utils.dict.DictDefault()
```

示例 2 (python)：
```python
utils.dict.remove_none_values(obj)
```

---

## API 参考

**URL：** https://docs.axolotl.ai/docs/api/

**内容：**
- API 参考
- 核心
- CLI
- 训练器
- 模型加载
- Mixin
- 上下文管理器
- 提示策略
- 内核
- 猴子补丁

训练核心功能

命令行界面

训练实现

加载和修补模型、分词器等的功能

用于增强训练器的 Mixin 类

用于改变训练器行为的上下文管理器

提示格式化策略

低级性能优化

模型优化的运行时补丁

Axolotl 配置的 Pydantic 数据模型

第三方集成和扩展

通用工具和共享功能

自定义模型实现

数据处理工具

---

## monkeypatch.lora_kernels

**URL：** https://docs.axolotl.ai/docs/api/monkeypatch.lora_kernels.html

**内容：**
- monkeypatch.lora_kernels
- 类
  - FakeMLP
- 函数
  - apply_lora_kernel_patches
    - 参数
    - 返回
    - 抛出
    - 注意
  - get_attention_cls_from_config

monkeypatch.lora_kernels

用于修补自定义 LoRA Triton 内核和 torch.autograd 函数的模块。

用于 triton 修补的占位符 MLP

将优化的 Triton 内核补丁应用于 PEFT 模型。

用 MLP 和注意力计算的优化实现修补 PEFT 模型。优化包括用于激活函数的自定义 Triton 内核和用于 LoRA 计算的专用 autograd 函数。

优化需要无 dropout 和无偏置项的 LoRA 适配器。如果不满足这些条件，函数将跳过修补。

通过检查模型配置获取适当的注意力类。使用动态导入支持遵循标准 transformers 命名约定的任何模型架构。

获取模型的层。处理纯文本和多模态模型。

无优化的输出投影原始实现。

无优化的 QKV 投影原始实现。

给定 axolotl 配置，此方法用优化的 LoRA 实现修补推断的注意力类前向传播。

它修改注意力类以使用优化的 QKV 和输出投影。原始实现被保留，如果需要可以恢复。

**示例：**

示例 1 (python)：
```python
monkeypatch.lora_kernels.FakeMLP(gate_proj, up_proj, down_proj)
```

示例 2 (python)：
```python
monkeypatch.lora_kernels.apply_lora_kernel_patches(model, cfg)
```

示例 3 (python)：
```python
monkeypatch.lora_kernels.get_attention_cls_from_config(cfg)
```

示例 4 (python)：
```python
monkeypatch.lora_kernels.get_layers(model)
```

---

## monkeypatch.stablelm_attn_hijack_flash

**URL：** https://docs.axolotl.ai/docs/api/monkeypatch.stablelm_attn_hijack_flash.html

**内容：**
- monkeypatch.stablelm_attn_hijack_flash
- 函数
  - repeat_kv
  - rotate_half

monkeypatch.stablelm_attn_hijack_flash

PyTorch StableLM Epoch 模型。

这相当于 torch.repeat_interleave(x, dim=1, repeats=n_rep)。隐藏状态从 (batch, num_key_value_heads, seqlen, head_dim) 变为 (batch, num_attention_heads, seqlen, head_dim)

旋转输入的一半隐藏维度。

**示例：**

示例 1 (python)：
```python
monkeypatch.stablelm_attn_hijack_flash.repeat_kv(hidden_states, n_rep)
```

示例 2 (python)：
```python
monkeypatch.stablelm_attn_hijack_flash.rotate_half(x)
```

---

## core.trainers.mixins.rng_state_loader

**URL：** https://docs.axolotl.ai/docs/api/core.trainers.mixins.rng_state_loader.html

**内容：**
- core.trainers.mixins.rng_state_loader
- 类
  - RngLoaderMixin

core.trainers.mixins.rng_state_loader

从检查点恢复错误的临时修复/覆盖

参见 https://github.com/huggingface/transformers/pull/37162

TODO：当上游将 PR 添加到发布版时移除

用于从检查点加载 RNG 状态的方法覆盖的 mixin

**示例：**

示例 1 (python)：
```python
core.trainers.mixins.rng_state_loader.RngLoaderMixin()
```

---

## core.trainers.utils

**URL：** https://docs.axolotl.ai/docs/api/core.trainers.utils.html

**内容：**
- core.trainers.utils

Axolotl 训练器的工具

---

## core.training_args

**URL：** https://docs.axolotl.ai/docs/api/core.training_args.html

**内容：**
- core.training_args
- 类
  - AxolotlCPOConfig
  - AxolotlKTOConfig
  - AxolotlORPOConfig
  - AxolotlPRMConfig
  - AxolotlRewardConfig
  - AxolotlTrainingArguments

额外的 axolotl 特定训练参数

CPO 训练的 CPO 配置

KTO 训练的 KTO 配置

ORPO 训练的 ORPO 配置

PRM 训练的 PRM 配置

Reward 训练的 Reward 配置

Causal 训练器的训练参数

此代码重复是因为 HF TrainingArguments 未使用默认值设置 output_dir，因此不能用作 mixin。

**示例：**

示例 1 (python)：
```python
core.training_args.AxolotlCPOConfig(simpo_gamma=None)
```

示例 2 (python)：
```python
core.training_args.AxolotlKTOConfig()
```

示例 3 (python)：
```python
core.training_args.AxolotlORPOConfig()
```

示例 4 (python)：
```python
core.training_args.AxolotlPRMConfig()
```

---

## monkeypatch.btlm_attn_hijack_flash

**URL：** https://docs.axolotl.ai/docs/api/monkeypatch.btlm_attn_hijack_flash.html

**内容：**
- monkeypatch.btlm_attn_hijack_flash

monkeypatch.btlm_attn_hijack_flash

cerebras btlm 模型的 Flash attention 猴子补丁

---

## prompt_strategies.dpo.passthrough

**URL：** https://docs.axolotl.ai/docs/api/prompt_strategies.dpo.passthrough.html

**内容：**
- prompt_strategies.dpo.passthrough

prompt_strategies.dpo.passthrough

DPO 提示策略直通/零处理策略

---

## kernels.swiglu

**URL：** https://docs.axolotl.ai/docs/api/kernels.swiglu.html

**内容：**
- kernels.swiglu
- 函数
  - swiglu_backward
    - 参数
    - 返回
  - swiglu_forward
    - 参数
    - 返回

SwiGLU Triton 内核定义模块。

参见"GLU Variants Improve Transformer" (https://arxiv.org/abs/2002.05202)。

感谢 unsloth (https://unsloth.ai/) 对此实现的启发。

使用原地操作的 SwiGLU 反向传播。

SwiGLU 前向传播。计算 SwiGLU 激活：x * sigmoid(x) * up，其中 x 是 gate 张量。

**示例：**

示例 1 (python)：
```python
kernels.swiglu.swiglu_backward(grad_output, gate, up)
```

示例 2 (python)：
```python
kernels.swiglu.swiglu_forward(gate, up)
```

---

## core.trainers.grpo.trainer

**URL：** https://docs.axolotl.ai/docs/api/core.trainers.grpo.trainer.html

**内容：**
- core.trainers.grpo.trainer
- 类
  - AxolotlGRPOSequenceParallelTrainer
    - 方法
      - get_train_dataloader
  - AxolotlGRPOTrainer

core.trainers.grpo.trainer

Axolotl GRPO 训练器（带和不带序列并行处理）

扩展基础 GRPOTrainer 以处理序列并行

获取训练数据加载器

扩展基础 GRPOTrainer 以添加 axolotl 辅助功能

**示例：**

示例 1 (python)：
```python
core.trainers.grpo.trainer.AxolotlGRPOSequenceParallelTrainer(
    model,
    reward_funcs,
    args=None,
    train_dataset=None,
    eval_dataset=None,
    processing_class=None,
    reward_processing_classes=None,
    callbacks=None,
    optimizers=(None, None),
    peft_config=None,
    optimizer_cls_and_kwargs=None,
)
```

示例 2 (python)：
```python
core.trainers.grpo.trainer.AxolotlGRPOSequenceParallelTrainer.get_train_dataloader(
)
```

示例 3 (python)：
```python
core.trainers.grpo.trainer.AxolotlGRPOTrainer(*args, **kwargs)
```

---

## prompt_strategies.user_defined

**URL：** https://docs.axolotl.ai/docs/api/prompt_strategies.user_defined.html

**内容：**
- prompt_strategies.user_defined
- 类
  - UserDefinedDatasetConfig
  - UserDefinedPromptTokenizationStrategy

prompt_strategies.user_defined

使用 YML 配置的用户定义提示

表示用户定义数据集类型的数据类配置

用户定义提示的提示分词策略

**示例：**

示例 1 (python)：
```python
prompt_strategies.user_defined.UserDefinedDatasetConfig(
    system_prompt='',
    field_system='system',
    field_instruction='instruction',
    field_input='input',
    field_output='output',
    format='{instruction} {input} ',
    no_input_format='{instruction} ',
    system_format='{system}',
)
```

示例 2 (python)：
```python
prompt_strategies.user_defined.UserDefinedPromptTokenizationStrategy(
    prompter,
    tokenizer,
    train_on_inputs=False,
    sequence_len=2048,
)
```

---

## utils.schemas.training

**URL：** https://docs.axolotl.ai/docs/api/utils.schemas.training.html

**内容：**
- utils.schemas.training
- 类
  - HyperparametersConfig
  - JaggedLRConfig
  - LrGroup

utils.schemas.training

训练超参数的 Pydantic 模型

训练超参数配置子集

JaggedLR 配置子集，可用于 ReLoRA 训练

自定义学习率组配置

**示例：**

示例 1 (python)：
```python
utils.schemas.training.HyperparametersConfig()
```

示例 2 (python)：
```python
utils.schemas.training.JaggedLRConfig()
```

示例 3 (python)：
```python
utils.schemas.training.LrGroup()
```

---

## utils.quantization

**URL：** https://docs.axolotl.ai/docs/api/utils.quantization.html

**内容：**
- utils.quantization
- 函数
  - convert_qat_model
  - get_quantization_config
    - 参数
    - 返回
    - 抛出
  - prepare_model_for_qat
    - 参数
    - 抛出

使用 torchao 进行量化（包括 QAT 和 PTQ）的工具。

此函数将具有假量化层的 QAT 模型转换回原始模型。

此函数用于构建训练后量化配置。

此函数用于通过将模型的线性层替换为假量化线性层，并可选地将嵌入权重替换为假量化嵌入权重，来为 QAT 准备模型。

此函数用于量化模型。

**示例：**

示例 1 (python)：
```python
utils.quantization.convert_qat_model(model, quantize_embedding=False)
```

示例 2 (python)：
```python
utils.quantization.get_quantization_config(
    weight_dtype,
    activation_dtype=None,
    group_size=None,
)
```

示例 3 (python)：
```python
utils.quantization.prepare_model_for_qat(
    model,
    weight_dtype,
    group_size=None,
    activation_dtype=None,
    quantize_embedding=False,
)
```

示例 4 (python)：
```python
utils.quantization.quantize_model(
    model,
    weight_dtype,
    group_size=None,
    activation_dtype=None,
    quantize_embedding=None,
)
```

---

## logging_config

**URL：** https://docs.axolotl.ai/docs/api/logging_config.html

**内容：**
- logging_config
- 类
  - AxolotlLogger
  - AxolotlOrWarnErrorFilter
  - ColorfulFormatter
- 函数
  - configure_logging

axolotl 的通用日志模块。

将过滤应用于非 axolotl 日志器的日志器。

允许任何 WARNING 或更高级别（除非被 LOG_LEVEL 覆盖）。允许 axolotl.* 在 INFO 或更高级别（除非被 AXOLOTL_LOG_LEVEL 覆盖）。丢弃所有其他记录（即默认情况下非 axolotl 的 INFO、DEBUG 等）。

按日志类型为日志消息添加着色的格式化器

使用默认日志配置

**示例：**

示例 1 (python)：
```python
logging_config.AxolotlLogger(name, level=logging.NOTSET)
```

示例 2 (python)：
```python
logging_config.AxolotlOrWarnErrorFilter(**kwargs)
```

示例 3 (python)：
```python
logging_config.ColorfulFormatter()
```

示例 4 (python)：
```python
logging_config.configure_logging()
```
