# DPO 变体

TRL 中直接偏好优化损失变体完整指南。

## 概述

DPO 使用偏好数据（chosen/rejected 对）优化模型。TRL 支持 10+ 种损失变体以适应不同场景。

## 损失类型

### 1. Sigmoid（标准 DPO）

**公式**：`-log(sigmoid(β * logits))`

**何时使用**：默认选择，通用偏好对齐

**配置**：
```python
DPOConfig(
    loss_type="sigmoid",
    beta=0.1,  # KL 惩罚
    per_device_train_batch_size=64,
    learning_rate=1e-6
)
```

### 2. IPO（身份策略优化）

**公式**：`(logits - 1/(2β))²`

**何时使用**：更好的理论基础，减少过拟合

**配置**：
```python
DPOConfig(
    loss_type="ipo",
    beta=0.1,
    per_device_train_batch_size=90,
    learning_rate=1e-2
)
```

### 3. Hinge（SLiC）

**公式**：`ReLU(1 - β * logits)`

**何时使用**：基于边界的目标

**配置**：
```python
DPOConfig(
    loss_type="hinge",
    beta=0.1,
    per_device_train_batch_size=512,
    learning_rate=1e-4
)
```

### 4. Robust DPO

**公式**：带标签平滑的 Sigmoid，用于噪声鲁棒性

**何时使用**：噪声偏好标签

**配置**：
```python
DPOConfig(
    loss_type="robust",
    beta=0.01,
    label_smoothing=0.1,  # 噪声概率
    per_device_train_batch_size=16,
    learning_rate=1e-3,
    max_prompt_length=128,
    max_length=512
)
```

### 5. BCO Pair（二分类）

**公式**：训练二分类器（chosen=1, rejected=0）

**何时使用**：成对偏好数据

**配置**：
```python
DPOConfig(
    loss_type="bco_pair",
    beta=0.01,
    per_device_train_batch_size=128,
    learning_rate=5e-7,
    max_prompt_length=1536,
    max_completion_length=512
)
```

### 6. SPPO Hard

**公式**：将 chosen 推向 0.5，rejected 推向 -0.5

**何时使用**：纳什均衡，稀疏数据

**配置**：
```python
DPOConfig(
    loss_type="sppo_hard",
    beta=0.1
)
```

### 7. DiscoPOP

**公式**：对数比率调制损失

**何时使用**：自动损失发现

**配置**：
```python
DPOConfig(
    loss_type="discopop",
    beta=0.05,
    discopop_tau=0.05,
    per_device_train_batch_size=64,
    learning_rate=5e-7
)
```

### 8. APO Zero

**公式**：增加 chosen，减少 rejected 似然

**何时使用**：模型比获胜输出差

**配置**：
```python
DPOConfig(
    loss_type="apo_zero",
    beta=0.1,
    per_device_train_batch_size=64,
    learning_rate=2e-7,
    max_prompt_length=512,
    max_completion_length=512
)
```

### 9. APO Down

**公式**：两者都减少，强调 rejected 减少

**何时使用**：模型比获胜输出好

**配置**：
```python
DPOConfig(
    loss_type="apo_down",
    beta=0.1,
    # 与 apo_zero 相同的超参数
)
```

### 10. AOT & AOT Pair

**公式**：通过随机优势进行分布对齐

**何时使用**：
- `aot_pair`：成对偏好数据
- `aot`：非成对数据

**配置**：
```python
DPOConfig(
    loss_type="aot_pair",  # 或 "aot"
    beta=0.1,
    label_smoothing=0.0
)
```

## 多损失训练

组合多个损失：

```python
DPOConfig(
    loss_type=["sigmoid", "ipo"],
    loss_weights=[0.7, 0.3],  # 加权组合
    beta=0.1
)
```

## 关键参数

### Beta (β)

控制与参考模型的偏差：
- **更高**（0.5）：更保守，保持接近参考模型
- **更低**（0.01）：更激进对齐
- **默认**：0.1

### 标签平滑

用于鲁棒 DPO：
- **0.0**：无平滑（默认）
- **0.1-0.3**：中等噪声鲁棒性
- **0.5**：最大噪声容忍度

### 最大长度

- `max_prompt_length`：128-1536
- `max_completion_length`：128-512
- `max_length`：总序列（1024-2048）

## 比较表

| 损失 | 速度 | 稳定性 | 最佳用途 |
|------|-------|-----------|----------|
| Sigmoid | 快 | 好 | **通用** |
| IPO | 快 | 更好 | 过拟合问题 |
| Hinge | 快 | 好 | 边界目标 |
| Robust | 快 | 最佳 | 噪声数据 |
| BCO | 中 | 好 | 二分类 |
| DiscoPOP | 快 | 好 | 新架构 |
| APO | 快 | 好 | 模型质量匹配 |

## 参考文献

- DPO 论文：https://arxiv.org/abs/2305.18290
- IPO 论文：https://arxiv.org/abs/2310.12036
- TRL 文档：https://huggingface.co/docs/trl/dpo_trainer
