---
name: grpo-rl-training
description: 使用TRL进行GRPO/RL微调的专家指导,用于推理和任务特定模型训练
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [transformers>=4.47.0, trl>=0.14.0, datasets>=3.2.0, peft>=0.14.0, torch]
metadata:
  VoidCube:
    tags: [Post-Training, Reinforcement Learning, GRPO, TRL, RLHF, Reward Modeling, Reasoning, DPO, PPO, Structured Output]

---

# 使用TRL进行GRPO/RL训练

使用Transformer强化学习(TRL)库实现群组相对策略优化(GRPO)的专家级指导。此技能提供经过实战检验的模式、关键见解和生产就绪的工作流,用于使用自定义奖励函数微调语言模型。

## 何时使用此技能

在以下情况下使用GRPO训练:
- **强制特定输出格式**(如XML标签、JSON、结构化推理)
- **教授可验证任务**,具有客观正确性指标(数学、编码、事实核查)
- **通过奖励思维链模式改进推理能力**
- **将模型对齐到领域特定行为**,无需标注偏好数据
- **同时优化多个目标**(格式+正确性+风格)

**不要使用GRPO的场景:**
- 简单监督微调任务(改用SFT)
- 没有明确奖励信号的任务
- 已有高质量偏好对时(改用DPO/PPO)

---

## 核心概念

### 1. GRPO算法基础

**关键机制:**
- 为每个提示生成**多个补全**(组大小:4-16)
- 使用奖励函数比较组内的补全
- 更新策略以偏向组内高奖励响应

**与PPO的关键区别:**
- 无需单独的奖励模型
- 更样本高效(从组内比较学习)
- 更易实现和调试

**数学直觉:**
```
对于每个提示p:
  1. 生成N个补全: {c₁, c₂, ..., cₙ}
  2. 计算奖励: {r₁, r₂, ..., rₙ}
  3. 学习增加高奖励补全的概率
     相对于同组低奖励补全
```

### 2. 奖励函数设计哲学

**黄金法则:**
1. **组合多个奖励函数** - 每个处理一个方面(格式、正确性、风格)
2. **适当缩放奖励** - 更高权重=更强信号
3. **使用增量奖励** - 部分合规给予部分奖励
4. **独立测试奖励** - 隔离调试每个奖励函数

**奖励函数类型:**

| 类型 | 用例 | 示例权重 |
|------|----------|----------------|
| **正确性** | 可验证任务(数学、代码) | 2.0 (最高) |
| **格式** | 严格结构强制 | 0.5-1.0 |
| **长度** | 鼓励冗长/简洁 | 0.1-0.5 |
| **风格** | 惩罚不想要的模式 | -0.5到0.5 |

---

## 实现工作流

### 步骤1: 数据集准备

**关键要求:**
- 提示为聊天格式(带'role'和'content'的字典列表)
- 包含系统提示以设置期望
- 对于可验证任务,包含真实答案作为额外列

**示例结构:**
```python
from datasets import load_dataset, Dataset

SYSTEM_PROMPT = """
以以下格式响应:
<reasoning>
[你的逐步思考]
</reasoning>
<answer>
[最终答案]
</answer>
"""

def prepare_dataset(raw_data):
    """
    将原始数据转换为GRPO兼容格式。

    返回: 带列的数据集:
    - 'prompt': List[Dict]带role/content(系统+用户消息)
    - 'answer': str(真实值,可选但推荐)
    """
    return raw_data.map(lambda x: {
        'prompt': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': x['question']}
        ],
        'answer': extract_answer(x['raw_answer'])
    })
```

**专业提示:**
- 在系统提示中使用单样本或少样本示例用于复杂格式
- 保持提示简洁(max_prompt_length: 256-512 tokens)
- 训练前验证数据质量(垃圾进=垃圾出)

### 步骤2: 奖励函数实现

**模板结构:**
```python
def reward_function_name(
    prompts,        # List[List[Dict]]: 原始提示
    completions,    # List[List[Dict]]: 模型生成
    answer=None,    # Optional: 数据集的真实值
    **kwargs        # 额外数据集列
) -> list[float]:
    """
    评估补全并返回奖励。

    返回: 浮点数列表(每个补全一个)
    """
    # 提取补全文本
    responses = [comp[0]['content'] for comp in completions]

    # 计算奖励
    rewards = []
    for response in responses:
        score = compute_score(response)
        rewards.append(score)

    return rewards
```

**示例1: 正确性奖励(数学/编码)**
```python
def correctness_reward(prompts, completions, answer, **kwargs):
    """用高分奖励正确答案。"""
    responses = [comp[0]['content'] for comp in completions]
    extracted = [extract_final_answer(r) for r in responses]
    return [2.0 if ans == gt else 0.0
            for ans, gt in zip(extracted, answer)]
```

**示例2: 格式奖励(结构化输出)**
```python
import re

def format_reward(completions, **kwargs):
    """奖励XML类结构化格式。"""
    pattern = r'<reasoning>.*?</reasoning>\s*<answer>.*?</answer>'
    responses = [comp[0]['content'] for comp in completions]
    return [1.0 if re.search(pattern, r, re.DOTALL) else 0.0
            for r in responses]
```

**示例3: 增量格式奖励(部分奖励)**
```python
def incremental_format_reward(completions, **kwargs):
    """为格式合规性给予部分奖励。"""
    responses = [comp[0]['content'] for comp in completions]
    rewards = []

    for r in responses:
        score = 0.0
        if '<reasoning>' in r:
            score += 0.25
        if '</reasoning>' in r:
            score += 0.25
        if '<answer>' in r:
            score += 0.25
        if '</answer>' in r:
            score += 0.25
        # 惩罚结束标签后的额外文本
        if r.count('</answer>') == 1:
            extra_text = r.split('</answer>')[-1].strip()
            score -= len(extra_text) * 0.001
        rewards.append(score)

    return rewards
```

**关键见解:**
组合3-5个奖励函数以实现鲁棒训练。顺序不如信号多样性重要。

### 步骤3: 训练配置

**内存优化配置(小GPU)**
```python
from trl import GRPOConfig

training_args = GRPOConfig(
    output_dir="outputs/grpo-model",

    # 学习率
    learning_rate=5e-6,          # 更低=更稳定
    adam_beta1=0.9,
    adam_beta2=0.99,
    weight_decay=0.1,
    warmup_ratio=0.1,
    lr_scheduler_type='cosine',

    # 批设置
    per_device_train_batch_size=1,
    gradient_accumulation_steps=4,  # 有效批=4

    # GRPO特定
    num_generations=8,            # 组大小: 推荐8-16
    max_prompt_length=256,
    max_completion_length=512,

    # 训练时长
    num_train_epochs=1,
    max_steps=None,               # 或设置固定步数(如500)

    # 优化
    bf16=True,                    # A100/H100上更快
    optim="adamw_8bit",          # 内存高效优化器
    max_grad_norm=0.1,

    # 日志
    logging_steps=1,
    save_steps=100,
    report_to="wandb",            # 或"none"表示无日志
)
```

**高性能配置(大GPU)**
```python
training_args = GRPOConfig(
    output_dir="outputs/grpo-model",
    learning_rate=1e-5,
    per_device_train_batch_size=4,
    gradient_accumulation_steps=2,
    num_generations=16,           # 更大组=更好信号
    max_prompt_length=512,
    max_completion_length=1024,
    num_train_epochs=1,
    bf16=True,
    use_vllm=True,                # 用vLLM快速生成
    logging_steps=10,
)
```

**关键超参数:**

| 参数 | 影响 | 调优建议 |
|-----------|--------|---------------|
| `num_generations` | 比较的组大小 | 从8开始,如果GPU允许增加到16 |
| `learning_rate` | 收敛速度/稳定性 | 5e-6(安全),1e-5(更快,风险更大) |
| `max_completion_length` | 输出冗长性 | 匹配你的任务(推理512,短答案256) |
| `gradient_accumulation_steps` | 有效批大小 | 如果GPU内存有限则增加 |

### 步骤4: 模型设置和训练

**标准设置(Transformers)**
```python
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig
from trl import GRPOTrainer

# 加载模型
model_name = "Qwen/Qwen2.5-1.5B-Instruct"
model = AutoModelForCausalLM.from_pretrained(
    model_name,
    torch_dtype=torch.bfloat16,
    attn_implementation="flash_attention_2",  # 快2-3倍
    device_map="auto"
)

tokenizer = AutoTokenizer.from_pretrained(model_name)
tokenizer.pad_token = tokenizer.eos_token

# 可选: LoRA用于参数高效训练
peft_config = LoraConfig(
    r=16,                         # 秩(更高=更大容量)
    lora_alpha=32,               # 缩放因子(通常2*r)
    target_modules=[
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj"
    ],
    task_type="CAUSAL_LM",
    lora_dropout=0.05,
)

# 初始化训练器
trainer = GRPOTrainer(
    model=model,
    processing_class=tokenizer,
    reward_funcs=[
        incremental_format_reward,
        format_reward,
        correctness_reward,
    ],
    args=training_args,
    train_dataset=dataset,
    peft_config=peft_config,      # 移除用于全量微调
)

# 训练
trainer.train()

# 保存
trainer.save_model("final_model")
```

---

## 关键训练见解

### 1. 损失行为(预期模式)
- **损失从接近0开始并在训练期间增加**
- 这是正确的 - 损失测量与初始策略的KL散度
- 模型正在学习(偏离原始行为以优化奖励)
- 监控奖励指标而非损失以查看进度

### 2. 奖励跟踪
要关注的关键指标:
- `reward`: 所有补全的平均值
- `reward_std`: 组内多样性(应保持>0)
- `kl`: 与参考的KL散度(应适度增长)

**健康训练模式:**
```
步数   奖励    奖励标准差   KL
100    0.5       0.3          0.02
200    0.8       0.25         0.05
300    1.2       0.2          0.08  ← 良好进展
400    1.5       0.15         0.12
```

**警告信号:**
- 奖励标准差→0(模型坍缩到单一响应)
- KL爆炸(>0.5)(偏离太多,降低LR)
- 奖励卡住(奖励函数太严苛或模型容量问题)

### 3. 常见陷阱和解决方案

| 问题 | 症状 | 解决方案 |
|---------|---------|----------|
| **模式坍缩** | 所有补全相同 | 增加`num_generations`,添加多样性惩罚 |
| **无学习** | 平坦奖励 | 检查奖励函数逻辑,增加LR |
| **OOM错误** | GPU内存超限 | 减少`num_generations`,启用梯度检查点 |
| **训练慢** | <1 it/s | 启用`use_vllm=True`,使用Unsloth,减少序列长度 |
| **格式被忽略** | 模型不遵循结构 | 增加格式奖励权重,添加增量奖励 |

---

## 最佳实践检查清单

**训练前:**
- [ ] 验证数据集格式(提示为List[Dict])
- [ ] 在样本数据上测试奖励函数
- [ ] 从数据计算预期max_prompt_length
- [ ] 根据GPU内存选择合适的num_generations
- [ ] 设置日志(推荐wandb)

**训练中:**
- [ ] 监控奖励进展(应增加)
- [ ] 检查reward_std(应保持>0.1)
- [ ] 观察OOM错误(需要时减少批大小)
- [ ] 每50-100步采样生成
- [ ] 在留出集上验证格式合规性

**训练后:**
- [ ] 如果使用PEFT则合并LoRA权重
- [ ] 在多样化提示上测试
- [ ] 与基线模型比较
- [ ] 记录奖励权重和超参数
- [ ] 保存可复现配置

---

## 资源

**官方文档:**
- TRL GRPO训练器: https://huggingface.co/docs/trl/grpo_trainer
- DeepSeek R1论文: https://arxiv.org/abs/2501.12948
- Unsloth文档: https://docs.unsloth.ai/

**示例仓库:**
- Open R1实现: https://github.com/huggingface/open-r1
- TRL示例: https://github.com/huggingface/trl/tree/main/examples

**推荐阅读:**
- 智能体指令的渐进披露模式
- RL中的奖励塑形(Ng et al.)
- LoRA论文(Hu et al., 2021)
