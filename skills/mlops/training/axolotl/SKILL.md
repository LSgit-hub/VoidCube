---
name: axolotl
description: 使用 Axolotl 进行 LLM 微调的参考流程，覆盖 YAML 配置、LoRA/QLoRA 和常见训练验证；具体参数必须以已安装版本的 Axolotl 文档和 CLI 校验结果为准。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [axolotl, torch, transformers, datasets, peft, accelerate, deepspeed]
metadata:
  VoidCube:
    tags: [Fine-Tuning, Axolotl, LLM, LoRA, QLoRA, DPO, KTO, ORPO, GRPO, YAML, HuggingFace, DeepSpeed, Multimodal]

---

# Axolotl 技能

本技能提供配置和验证参考，不保证跨版本参数兼容。执行训练前先锁定 Axolotl、Transformers、Torch 版本，并运行配置校验。

## 最小流程

1. 准备模型和数据集，确认许可证、显存和 tokenizer 来源。
2. 从最小 YAML 开始，使用安装版本提供的示例目录。
3. 先运行 `axolotl train config.yml --help`，确认当前版本的校验选项，再执行配置校验。
4. 先运行短步数验证数据格式和保存路径，再开始完整训练。

最小 LoRA 配置骨架：

```yaml
base_model: <model-id-or-local-path>
model_type: AutoModelForCausalLM
tokenizer_type: AutoTokenizer
load_in_4bit: true
adapter: lora
lora_r: 16
lora_alpha: 32
lora_dropout: 0.05
datasets:
  - path: <jsonl-path-or-dataset-id>
    type: completion
    field: text
sequence_len: 2048
output_dir: ./outputs/axolotl-lora
micro_batch_size: 1
gradient_accumulation_steps: 8
num_epochs: 1
learning_rate: 0.0002
```

## 何时使用此技能

此技能应在以下情况下触发:
- 使用axolotl工作
- 询问axolotl功能或API
- 实现axolotl解决方案
- 调试axolotl代码
- 学习axolotl最佳实践

## 快速参考

### 常见模式

**模式1:** 要验证训练作业是否存在可接受的数据传输速度,运行NCCL测试可以帮助定位瓶颈,例如:

```
./build/all_reduce_perf -b 8 -e 128M -f 2 -g 3
```

**模式2:** 在Axolotl yaml中配置模型使用FSDP。例如:

```
fsdp_version: 2
fsdp_config:
  offload_params: true
  state_dict_type: FULL_STATE_DICT
  auto_wrap_policy: TRANSFORMER_BASED_WRAP
  transformer_layer_cls_to_wrap: LlamaDecoderLayer
  reshard_after_forward: true
```

**模式3:** context_parallel_size应该是GPU总数的除数。例如:

```
context_parallel_size
```

**模式4:** 例如: - 使用8个GPU且无序列并行: 每步处理8个不同批次 - 使用8个GPU且context_parallel_size=4: 每步仅处理2个不同批次(每个分割到4个GPU) - 如果每个GPU的micro_batch_size为2,全局批大小从16减少到4

```
context_parallel_size=4
```

**模式5:** 在配置中设置save_compressed: true可以以压缩格式保存模型,这会: - 减少约40%的磁盘空间使用 - 保持与vLLM的兼容性以进行加速推理 - 保持与llmcompressor的兼容性以进行进一步优化(例如:量化)

```
save_compressed: true
```

**模式6:** 注意 不必将集成放在integrations文件夹中。它可以位于任何位置,只要它安装在python环境中的包中。参见此仓库示例: https://github.com/axolotl-ai-cloud/diff-transformer

```
integrations
```

**模式7:** 处理单样本和批量数据。 - 单样本: sample['input_ids']是list[int] - 批量数据: sample['input_ids']是list[list[int]]

```
utils.trainer.drop_long_seq(sample, sequence_len=2048, min_sequence_len=2)
```

### 示例代码模式

**示例1** (python):
```python
cli.cloud.modal_.ModalCloud(config, app=None)
```

**示例2** (python):
```python
cli.cloud.modal_.run_cmd(cmd, run_folder, volumes=None)
```

**示例3** (python):
```python
core.trainers.base.AxolotlTrainer(
    *_args,
    bench_data_collator=None,
    eval_data_collator=None,
    dataset_tags=None,
    **kwargs,
)
```

**示例4** (python):
```python
core.trainers.base.AxolotlTrainer.log(logs, start_time=None)
```

**示例5** (python):
```python
prompt_strategies.input_output.RawInputOutputPrompter()
```

## 参考文件

此技能在`references/`中包含综合文档:

- **api.md** - API文档
- **dataset-formats.md** - 数据集格式文档
- **other.md** - 其他文档

需要详细信息时使用`view`读取特定参考文件。

## 使用此技能

### 对于初学者
从getting_started或tutorials参考文件开始学习基础概念。

### 对于特定功能
使用相应的类别参考文件(api、guides等)获取详细信息。

### 对于代码示例
上面的快速参考部分包含从官方文档提取的常见模式。

## 资源

### references/
从官方来源提取的组织文档。这些文件包含:
- 详细说明
- 带语言注释的代码示例
- 原始文档链接
- 用于快速导航的目录

### scripts/
在此添加常见自动化任务的辅助脚本。

### assets/
在此添加模板、样板或示例项目。

## 注意事项

- 此技能是从官方文档自动生成的
- 参考文件保留了源文档的结构和示例
- 代码示例包含语言检测以实现更好的语法高亮
- 快速参考模式从文档中的常见用法示例提取

## 更新

要使用更新的文档刷新此技能:
1. 使用相同配置重新运行爬虫
2. 技能将使用最新信息重建

