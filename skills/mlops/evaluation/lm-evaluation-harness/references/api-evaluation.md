# API 模型评估

评估 OpenAI、Anthropic 和其他基于 API 的语言模型的指南。

## 概述

lm-evaluation-harness 通过统一的 `TemplateAPI` 接口支持评估基于 API 的模型。这允许对以下模型进行基准测试：
- OpenAI 模型（GPT-4、GPT-3.5 等）
- Anthropic 模型（Claude 3、Claude 2 等）
- 本地 OpenAI 兼容 API
- 自定义 API 端点

**为什么要评估 API 模型**：
- 对闭源模型进行基准测试
- 将 API 模型与开源模型进行比较
- 验证 API 性能
- 跟踪模型随时间的更新

## 支持的 API 模型

| 提供商 | 模型类型 | 请求类型 | 对数概率 |
|--------|----------|----------|----------|
| OpenAI (completions) | `openai-completions` | 全部 | ✅ 是 |
| OpenAI (chat) | `openai-chat-completions` | 仅 `generate_until` | ❌ 否 |
| Anthropic (completions) | `anthropic-completions` | 全部 | ❌ 否 |
| Anthropic (chat) | `anthropic-chat` | 仅 `generate_until` | ❌ 否 |
| 本地 (OpenAI 兼容) | `local-completions` | 取决于服务器 | 可变 |

**注意**：不提供对数概率的模型只能在生成任务上评估，不能在困惑度或对数似然任务上评估。

## OpenAI 模型

### 设置

```bash
export OPENAI_API_KEY=sk-...
```

### 补全模型（旧版）

**可用模型**：`davinci-002`、`babbage-002`

```bash
lm_eval --model openai-completions \
  --model_args model=davinci-002 \
  --tasks lambada_openai,hellaswag \
  --batch_size auto
```

**支持**：
- `generate_until`: ✅
- `loglikelihood`: ✅
- `loglikelihood_rolling`: ✅

### 聊天模型

**可用模型**：`gpt-4`、`gpt-4-turbo`、`gpt-3.5-turbo`

```bash
lm_eval --model openai-chat-completions \
  --model_args model=gpt-4-turbo \
  --tasks mmlu,gsm8k,humaneval \
  --num_fewshot 5 \
  --batch_size auto
```

**支持**：
- `generate_until`: ✅
- `loglikelihood`: ❌（无对数概率）
- `loglikelihood_rolling`: ❌

**重要**：聊天模型不提供对数概率，因此只能用于生成任务（MMLU、GSM8K、HumanEval），不能用于困惑度任务。

### 配置选项

```bash
lm_eval --model openai-chat-completions \
  --model_args \
    model=gpt-4-turbo,\
    base_url=https://api.openai.com/v1,\
    num_concurrent=5,\
    max_retries=3,\
    timeout=60,\
    batch_size=auto
```

**参数**：
- `model`：模型标识符（必需）
- `base_url`：API 端点（默认：OpenAI）
- `num_concurrent`：并发请求数（默认：5）
- `max_retries`：重试失败请求（默认：3）
- `timeout`：请求超时秒数（默认：60）
- `tokenizer`：使用的分词器（默认：匹配模型）
- `tokenizer_backend`：`"tiktoken"` 或 `"huggingface"`

### 成本管理

OpenAI 按令牌收费。运行前估算成本：

```python
# 粗略估算
num_samples = 1000
avg_tokens_per_sample = 500  # 输入 + 输出
cost_per_1k_tokens = 0.01  # GPT-3.5 Turbo

total_cost = (num_samples * avg_tokens_per_sample / 1000) * cost_per_1k_tokens
print(f"Estimated cost: ${total_cost:.2f}")
```

**节省成本的技巧**：
- 使用 `--limit N` 进行测试
- 先用 `gpt-3.5-turbo` 再用 `gpt-4`
- 将 `max_gen_toks` 设置为所需最小值
- 尽可能使用 `num_fewshot=0` 进行零样本评估

## Anthropic 模型

### 设置

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

### 衡全模型（旧版）

```bash
lm_eval --model anthropic-completions \
  --model_args model=claude-2.1 \
  --tasks lambada_openai,hellaswag \
  --batch_size auto
```

### 聊天模型（推荐）

**可用模型**：`claude-3-5-sonnet-20241022`、`claude-3-opus-20240229`、`claude-3-sonnet-20240229`、`claude-3-haiku-20240307`

```bash
lm_eval --model anthropic-chat \
  --model_args model=claude-3-5-sonnet-20241022 \
  --tasks mmlu,gsm8k,humaneval \
  --num_fewshot 5 \
  --batch_size auto
```

**别名**：`anthropic-chat-completions`（与 `anthropic-chat` 相同）

### 配置选项

```bash
lm_eval --model anthropic-chat \
  --model_args \
    model=claude-3-5-sonnet-20241022,\
    base_url=https://api.anthropic.com,\
    num_concurrent=5,\
    max_retries=3,\
    timeout=60
```

### 成本管理

Anthropic 定价（截至 2024 年）：
- Claude 3.5 Sonnet：$3.00 / 1M 输入，$15.00 / 1M 输出
- Claude 3 Opus：$15.00 / 1M 输入，$75.00 / 1M 输出
- Claude 3 Haiku：$0.25 / 1M 输入，$1.25 / 1M 输出

**预算友好的策略**：
```bash
# 先在小样本上测试
lm_eval --model anthropic-chat \
  --model_args model=claude-3-haiku-20240307 \
  --tasks mmlu \
  --limit 100

# 然后在最佳模型上运行完整评估
lm_eval --model anthropic-chat \
  --model_args model=claude-3-5-sonnet-20241022 \
  --tasks mmlu \
  --num_fewshot 5
```

## 本地 OpenAI 兼容 API

许多本地推理服务器提供 OpenAI 兼容的 API（vLLM、Text Generation Inference、llama.cpp、Ollama）。

### vLLM 本地服务器

**启动服务器**：
```bash
vllm serve meta-llama/Llama-2-7b-hf \
  --host 0.0.0.0 \
  --port 8000
```

**评估**：
```bash
lm_eval --model local-completions \
  --model_args \
    model=meta-llama/Llama-2-7b-hf,\
    base_url=http://localhost:8000/v1,\
    num_concurrent=1 \
  --tasks mmlu,gsm8k \
  --batch_size auto
```

### Text Generation Inference (TGI)

**启动服务器**：
```bash
docker run --gpus all --shm-size 1g -p 8080:80 \
  ghcr.io/huggingface/text-generation-inference:latest \
  --model-id meta-llama/Llama-2-7b-hf
```

**评估**：
```bash
lm_eval --model local-completions \
  --model_args \
    model=meta-llama/Llama-2-7b-hf,\
    base_url=http://localhost:8080/v1 \
  --tasks hellaswag,arc_challenge
```

### Ollama

**启动服务器**：
```bash
ollama serve
ollama pull llama2:7b
```

**评估**：
```bash
lm_eval --model local-completions \
  --model_args \
    model=llama2:7b,\
    base_url=http://localhost:11434/v1 \
  --tasks mmlu
```

### llama.cpp 服务器

**启动服务器**：
```bash
./server -m models/llama-2-7b.gguf --host 0.0.0.0 --port 8080
```

**评估**：
```bash
lm_eval --model local-completions \
  --model_args \
    model=llama2,\
    base_url=http://localhost:8080/v1 \
  --tasks gsm8k
```

## 自定义 API 实现

对于自定义 API 端点，继承 `TemplateAPI` 类：

### 创建 `my_api.py`

```python
from lm_eval.models.api_models import TemplateAPI
import requests

class MyCustomAPI(TemplateAPI):
    """自定义 API 模型。"""

    def __init__(self, base_url, api_key, **kwargs):
        super().__init__(base_url=base_url, **kwargs)
        self.api_key = api_key

    def _create_payload(self, messages, gen_kwargs):
        """创建 API 请求负载。"""
        return {
            "messages": messages,
            "api_key": self.api_key,
            **gen_kwargs
        }

    def parse_generations(self, response):
        """解析生成响应。"""
        return response.json()["choices"][0]["text"]

    def parse_logprobs(self, response):
        """解析对数概率（如果可用）。"""
        # 如果 API 不提供对数概率则返回 None
        logprobs = response.json().get("logprobs")
        if logprobs:
            return logprobs["token_logprobs"]
        return None
```

### 注册和使用

```python
from lm_eval import evaluator
from my_api import MyCustomAPI

model = MyCustomAPI(
    base_url="https://api.example.com/v1",
    api_key="your-key"
)

results = evaluator.simple_evaluate(
    model=model,
    tasks=["mmlu", "gsm8k"],
    num_fewshot=5,
    batch_size="auto"
)
```

## 比较 API 模型和开源模型

### 并排评估

```bash
# 评估 OpenAI GPT-4
lm_eval --model openai-chat-completions \
  --model_args model=gpt-4-turbo \
  --tasks mmlu,gsm8k,hellaswag \
  --num_fewshot 5 \
  --output_path results/gpt4.json

# 评估开源 Llama 2 70B
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-70b-hf,dtype=bfloat16 \
  --tasks mmlu,gsm8k,hellaswag \
  --num_fewshot 5 \
  --output_path results/llama2-70b.json

# 比较结果
python scripts/compare_results.py \
  results/gpt4.json \
  results/llama2-70b.json
```

### 典型比较

| 模型 | MMLU | GSM8K | HumanEval | 成本 |
|------|------|-------|-----------|------|
| GPT-4 Turbo | 86.4% | 92.0% | 67.0% | $$$$ |
| Claude 3 Opus | 86.8% | 95.0% | 84.9% | $$$$ |
| GPT-3.5 Turbo | 70.0% | 57.1% | 48.1% | $$ |
| Llama 2 70B | 68.9% | 56.8% | 29.9% | 免费（自托管） |
| Mixtral 8x7B | 70.6% | 58.4% | 40.2% | 免费（自托管） |

## 最佳实践

### 速率限制

遵守 API 速率限制：
```bash
lm_eval --model openai-chat-completions \
  --model_args \
    model=gpt-4-turbo,\
    num_concurrent=3,\  # 降低并发
    timeout=120 \  # 更长超时
  --tasks mmlu
```

### 可重现性

将温度设置为 0 以获得确定性结果：
```bash
lm_eval --model openai-chat-completions \
  --model_args model=gpt-4-turbo \
  --tasks mmlu \
  --gen_kwargs temperature=0.0
```

或使用 `seed` 进行采样：
```bash
lm_eval --model anthropic-chat \
  --model_args model=claude-3-5-sonnet-20241022 \
  --tasks gsm8k \
  --gen_kwargs temperature=0.7,seed=42
```

### 缓存

API 模型自动缓存响应以避免重复调用：
```bash
# 首次运行：发起 API 调用
lm_eval --model openai-chat-completions \
  --model_args model=gpt-4-turbo \
  --tasks mmlu \
  --limit 100

# 第二次运行：使用缓存（即时，免费）
lm_eval --model openai-chat-completions \
  --model_args model=gpt-4-turbo \
  --tasks mmlu \
  --limit 100
```

缓存位置：`~/.cache/lm_eval/`

### 错误处理

API 可能失败。使用重试：
```bash
lm_eval --model openai-chat-completions \
  --model_args \
    model=gpt-4-turbo,\
    max_retries=5,\
    timeout=120 \
  --tasks mmlu
```

## 故障排除

### "身份验证失败"

检查 API 密钥：
```bash
echo $OPENAI_API_KEY  # 应该输出 sk-...
echo $ANTHROPIC_API_KEY  # 应该输出 sk-ant-...
```

### "超出速率限制"

降低并发：
```bash
--model_args num_concurrent=1
```

或在请求之间添加延迟。

### "超时错误"

增加超时：
```bash
--model_args timeout=180
```

### "模型未找到"

对于本地 API，验证服务器是否运行：
```bash
curl http://localhost:8000/v1/models
```

### 成本失控

使用 `--limit` 进行测试：
```bash
lm_eval --model openai-chat-completions \
  --model_args model=gpt-4-turbo \
  --tasks mmlu \
  --limit 50  # 仅 50 个样本
```

## 高级功能

### 自定义头部

```bash
lm_eval --model local-completions \
  --model_args \
    base_url=http://api.example.com/v1,\
    header="Authorization: Bearer token,X-Custom: value"
```

### 禁用 SSL 验证（仅限开发）

```bash
lm_eval --model local-completions \
  --model_args \
    base_url=https://localhost:8000/v1,\
    verify_certificate=false
```

### 自定义分词器

```bash
lm_eval --model openai-chat-completions \
  --model_args \
    model=gpt-4-turbo,\
    tokenizer=gpt2,\
    tokenizer_backend=huggingface
```

## 参考资料

- OpenAI API：https://platform.openai.com/docs/api-reference
- Anthropic API：https://docs.anthropic.com/claude/reference
- TemplateAPI：`lm_eval/models/api_models.py`
- OpenAI 模型：`lm_eval/models/openai_completions.py`
- Anthropic 模型：`lm_eval/models/anthropic_llms.py`
