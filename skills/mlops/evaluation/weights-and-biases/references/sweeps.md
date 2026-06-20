# 全面超参数扫描指南

使用 W&B Sweeps 进行超参数优化的完整指南。

## 目录
- 扫描配置
- 搜索策略
- 参数分布
- 提前终止
- 并行执行
- 高级模式
- 实际示例

## 扫描配置

### 基本扫描配置

```python
sweep_config = {
    'method': 'bayes',  # 搜索策略
    'metric': {
        'name': 'val/accuracy',
        'goal': 'maximize'  # 或 'minimize'
    },
    'parameters': {
        'learning_rate': {
            'distribution': 'log_uniform',
            'min': 1e-5,
            'max': 1e-1
        },
        'batch_size': {
            'values': [16, 32, 64, 128]
        }
    }
}

# 初始化扫描
sweep_id = wandb.sweep(sweep_config, project="my-project")
```

### 完整配置示例

```python
sweep_config = {
    # 必需：搜索方法
    'method': 'bayes',

    # 必需：优化指标
    'metric': {
        'name': 'val/f1_score',
        'goal': 'maximize'
    },

    # 必需：要搜索的参数
    'parameters': {
        # 连续参数
        'learning_rate': {
            'distribution': 'log_uniform',
            'min': 1e-5,
            'max': 1e-1
        },

        # 离散值
        'batch_size': {
            'values': [16, 32, 64, 128]
        },

        # 类别
        'optimizer': {
            'values': ['adam', 'sgd', 'rmsprop', 'adamw']
        },

        # 均匀分布
        'dropout': {
            'distribution': 'uniform',
            'min': 0.1,
            'max': 0.5
        },

        # 整数范围
        'num_layers': {
            'distribution': 'int_uniform',
            'min': 2,
            'max': 10
        },

        # 固定值（跨运行不变）
        'epochs': {
            'value': 50
        }
    },

    # 可选：提前终止
    'early_terminate': {
        'type': 'hyperband',
        'min_iter': 5,
        's': 2,
        'eta': 3,
        'max_iter': 27
    }
}
```

## 搜索策略

### 1. 网格搜索

穷举搜索所有组合。

```python
sweep_config = {
    'method': 'grid',
    'parameters': {
        'learning_rate': {
            'values': [0.001, 0.01, 0.1]
        },
        'batch_size': {
            'values': [16, 32, 64]
        },
        'optimizer': {
            'values': ['adam', 'sgd']
        }
    }
}

# 总运行数：3 × 3 × 2 = 18 次运行
```

**优点：**
- 全面搜索
- 可重现结果
- 无随机性

**缺点：**
- 随参数指数增长
- 对连续参数效率低
- 超过 3-4 个参数不可扩展

**何时使用：**
- 参数少（< 4）
- 全是离散值
- 需要完全覆盖

### 2. 随机搜索

随机采样参数组合。

```python
sweep_config = {
    'method': 'random',
    'parameters': {
        'learning_rate': {
            'distribution': 'log_uniform',
            'min': 1e-5,
            'max': 1e-1
        },
        'batch_size': {
            'values': [16, 32, 64, 128, 256]
        },
        'dropout': {
            'distribution': 'uniform',
            'min': 0.0,
            'max': 0.5
        },
        'num_layers': {
            'distribution': 'int_uniform',
            'min': 2,
            'max': 8
        }
    }
}

# 运行 100 次随机试验
wandb.agent(sweep_id, function=train, count=100)
```

**优点：**
- 可扩展到多参数
- 可无限运行
- 常快速找到好解

**缺点：**
- 不从之前运行学习
- 可能错过最优区域
- 结果随随机种子变化

**何时使用：**
- 多参数（> 4）
- 快速探索
- 预算有限

### 3. 贝叶斯优化（推荐）

从之前的试验学习以采样有希望的区域。

```python
sweep_config = {
    'method': 'bayes',
    'metric': {
        'name': 'val/loss',
        'goal': 'minimize'
    },
    'parameters': {
        'learning_rate': {
            'distribution': 'log_uniform',
            'min': 1e-5,
            'max': 1e-1
        },
        'weight_decay': {
            'distribution': 'log_uniform',
            'min': 1e-6,
            'max': 1e-2
        },
        'dropout': {
            'distribution': 'uniform',
            'min': 0.1,
            'max': 0.5
        },
        'num_layers': {
            'values': [2, 3, 4, 5, 6]
        }
    }
}
```

**优点：**
- 样本效率最高
- 从过去试验学习
- 聚焦有希望区域

**缺点：**
- 初始随机探索阶段
- 可能陷入局部最优
- 每次迭代较慢

**何时使用：**
- 昂贵的训练运行
- 需要最佳性能
- 计算预算有限

## 参数分布

### 连续分布

```python
# 对数均匀：适合学习率、正则化
'learning_rate': {
    'distribution': 'log_uniform',
    'min': 1e-6,
    'max': 1e-1
}

# 均匀：适合 dropout、动量
'dropout': {
    'distribution': 'uniform',
    'min': 0.0,
    'max': 0.5
}

# 正态分布
'parameter': {
    'distribution': 'normal',
    'mu': 0.5,
    'sigma': 0.1
}

# 对数正态分布
'parameter': {
    'distribution': 'log_normal',
    'mu': 0.0,
    'sigma': 1.0
}
```

### 离散分布

```python
# 固定值
'batch_size': {
    'values': [16, 32, 64, 128, 256]
}

# 整数均匀
'num_layers': {
    'distribution': 'int_uniform',
    'min': 2,
    'max': 10
}

# 量化均匀（步长）
'layer_size': {
    'distribution': 'q_uniform',
    'min': 32,
    'max': 512,
    'q': 32  # 步长 32：32, 64, 96, 128...
}

# 量化对数均匀
'hidden_size': {
    'distribution': 'q_log_uniform',
    'min': 32,
    'max': 1024,
    'q': 32
}
```

### 类别参数

```python
# 优化器
'optimizer': {
    'values': ['adam', 'sgd', 'rmsprop', 'adamw']
}

# 模型架构
'model': {
    'values': ['resnet18', 'resnet34', 'resnet50', 'efficientnet_b0']
}

# 激活函数
'activation': {
    'values': ['relu', 'gelu', 'silu', 'leaky_relu']
}
```

## 提前终止

提前停止表现不佳的运行以节省计算。

### Hyperband

```python
sweep_config = {
    'method': 'bayes',
    'metric': {'name': 'val/accuracy', 'goal': 'maximize'},
    'parameters': {...},

    # Hyperband 提前终止
    'early_terminate': {
        'type': 'hyperband',
        'min_iter': 3,      # 终止前最小迭代次数
        's': 2,             # 括号计数
        'eta': 3,           # 下采样率
        'max_iter': 27      # 最大迭代次数
    }
}
```

**工作原理：**
- 在括号中运行试验
- 每轮保留前 1/eta 表现者
- 提前淘汰底部表现者

### 自定义终止

```python
def train():
    run = wandb.init()

    for epoch in range(MAX_EPOCHS):
        loss = train_epoch()
        val_acc = validate()

        wandb.log({'val/accuracy': val_acc, 'epoch': epoch})

        # 自定义早停
        if epoch > 5 and val_acc < 0.5:
            print("Early stop: Poor performance")
            break

        if epoch > 10 and val_acc > best_acc - 0.01:
            print("Early stop: No improvement")
            break
```

## 训练函数

### 基本模板

```python
def train():
    # 初始化 W&B 运行
    run = wandb.init()

    # 获取超参数
    config = wandb.config

    # 使用配置构建模型
    model = build_model(
        hidden_size=config.hidden_size,
        num_layers=config.num_layers,
        dropout=config.dropout
    )

    # 创建优化器
    optimizer = create_optimizer(
        model.parameters(),
        name=config.optimizer,
        lr=config.learning_rate,
        weight_decay=config.weight_decay
    )

    # 训练循环
    for epoch in range(config.epochs):
        # 训练
        train_loss, train_acc = train_epoch(
            model, optimizer, train_loader, config.batch_size
        )

        # 验证
        val_loss, val_acc = validate(model, val_loader)

        # 记录指标
        wandb.log({
            'train/loss': train_loss,
            'train/accuracy': train_acc,
            'val/loss': val_loss,
            'val/accuracy': val_acc,
            'epoch': epoch
        })

    # 记录最终模型
    torch.save(model.state_dict(), 'model.pth')
    wandb.save('model.pth')

    # 结束运行
    wandb.finish()
```

### 使用 PyTorch

```python
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
import wandb

def train():
    run = wandb.init()
    config = wandb.config

    # 数据
    train_loader = DataLoader(
        train_dataset,
        batch_size=config.batch_size,
        shuffle=True
    )

    # 模型
    model = ResNet(
        num_classes=config.num_classes,
        dropout=config.dropout
    ).to(device)

    # 优化器
    if config.optimizer == 'adam':
        optimizer = torch.optim.Adam(
            model.parameters(),
            lr=config.learning_rate,
            weight_decay=config.weight_decay
        )
    elif config.optimizer == 'sgd':
        optimizer = torch.optim.SGD(
            model.parameters(),
            lr=config.learning_rate,
            momentum=config.momentum,
            weight_decay=config.weight_decay
        )

    # 调度器
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=config.epochs
    )

    # 训练
    for epoch in range(config.epochs):
        model.train()
        train_loss = 0.0

        for data, target in train_loader:
            data, target = data.to(device), target.to(device)

            optimizer.zero_grad()
            output = model(data)
            loss = nn.CrossEntropyLoss()(output, target)
            loss.backward()
            optimizer.step()

            train_loss += loss.item()

        # 验证
        model.eval()
        val_loss, val_acc = validate(model, val_loader)

        # 步进调度器
        scheduler.step()

        # 记录
        wandb.log({
            'train/loss': train_loss / len(train_loader),
            'val/loss': val_loss,
            'val/accuracy': val_acc,
            'learning_rate': scheduler.get_last_lr()[0],
            'epoch': epoch
        })
```

## 并行执行

### 多个代理

并行运行扫描代理以加速搜索。

```python
# 初始化扫描一次
sweep_id = wandb.sweep(sweep_config, project="my-project")

# 并行运行多个代理
# 代理 1（终端 1）
wandb.agent(sweep_id, function=train, count=20)

# 代理 2（终端 2）
wandb.agent(sweep_id, function=train, count=20)

# 代理 3（终端 3）
wandb.agent(sweep_id, function=train, count=20)

# 总计：跨 3 个代理 60 次运行
```

### 多 GPU 执行

```python
import os

def train():
    # 获取可用 GPU
    gpu_id = os.environ.get('CUDA_VISIBLE_DEVICES', '0')

    run = wandb.init()
    config = wandb.config

    # 在特定 GPU 上训练
    device = torch.device(f'cuda:{gpu_id}')
    model = model.to(device)

    # ... 其余训练 ...

# 在不同 GPU 上运行代理
# 终端 1
# CUDA_VISIBLE_DEVICES=0 wandb agent sweep_id

# 终端 2
# CUDA_VISIBLE_DEVICES=1 wandb agent sweep_id

# 终端 3
# CUDA_VISIBLE_DEVICES=2 wandb agent sweep_id
```

## 高级模式

### 嵌套参数

```python
sweep_config = {
    'method': 'bayes',
    'metric': {'name': 'val/accuracy', 'goal': 'maximize'},
    'parameters': {
        'model': {
            'parameters': {
                'type': {
                    'values': ['resnet', 'efficientnet']
                },
                'size': {
                    'values': ['small', 'medium', 'large']
                }
            }
        },
        'optimizer': {
            'parameters': {
                'type': {
                    'values': ['adam', 'sgd']
                },
                'lr': {
                    'distribution': 'log_uniform',
                    'min': 1e-5,
                    'max': 1e-1
                }
            }
        }
    }
}

# 访问嵌套配置
def train():
    run = wandb.init()
    model_type = wandb.config.model.type
    model_size = wandb.config.model.size
    opt_type = wandb.config.optimizer.type
    lr = wandb.config.optimizer.lr
```

### 条件参数

```python
sweep_config = {
    'method': 'bayes',
    'parameters': {
        'optimizer': {
            'values': ['adam', 'sgd']
        },
        'learning_rate': {
            'distribution': 'log_uniform',
            'min': 1e-5,
            'max': 1e-1
        },
        # 仅在 optimizer == 'sgd' 时使用
        'momentum': {
            'distribution': 'uniform',
            'min': 0.5,
            'max': 0.99
        }
    }
}

def train():
    run = wandb.init()
    config = wandb.config

    if config.optimizer == 'adam':
        optimizer = torch.optim.Adam(
            model.parameters(),
            lr=config.learning_rate
        )
    elif config.optimizer == 'sgd':
        optimizer = torch.optim.SGD(
            model.parameters(),
            lr=config.learning_rate,
            momentum=config.momentum  # 条件参数
        )
```

## 实际示例

### 图像分类

```python
sweep_config = {
    'method': 'bayes',
    'metric': {
        'name': 'val/top1_accuracy',
        'goal': 'maximize'
    },
    'parameters': {
        # 模型
        'architecture': {
            'values': ['resnet50', 'resnet101', 'efficientnet_b0', 'efficientnet_b3']
        },
        'pretrained': {
            'values': [True, False]
        },

        # 训练
        'learning_rate': {
            'distribution': 'log_uniform',
            'min': 1e-5,
            'max': 1e-2
        },
        'batch_size': {
            'values': [16, 32, 64, 128]
        },
        'optimizer': {
            'values': ['adam', 'sgd', 'adamw']
        },
        'weight_decay': {
            'distribution': 'log_uniform',
            'min': 1e-6,
            'max': 1e-2
        },

        # 正则化
        'dropout': {
            'distribution': 'uniform',
            'min': 0.0,
            'max': 0.5
        },
        'label_smoothing': {
            'distribution': 'uniform',
            'min': 0.0,
            'max': 0.2
        },

        # 数据增强
        'mixup_alpha': {
            'distribution': 'uniform',
            'min': 0.0,
            'max': 1.0
        },
        'cutmix_alpha': {
            'distribution': 'uniform',
            'min': 0.0,
            'max': 1.0
        }
    },
    'early_terminate': {
        'type': 'hyperband',
        'min_iter': 5
    }
}
```

### NLP 微调

```python
sweep_config = {
    'method': 'bayes',
    'metric': {'name': 'eval/f1', 'goal': 'maximize'},
    'parameters': {
        # 模型
        'model_name': {
            'values': ['bert-base-uncased', 'roberta-base', 'distilbert-base-uncased']
        },

        # 训练
        'learning_rate': {
            'distribution': 'log_uniform',
            'min': 1e-6,
            'max': 1e-4
        },
        'per_device_train_batch_size': {
            'values': [8, 16, 32]
        },
        'num_train_epochs': {
            'values': [3, 4, 5]
        },
        'warmup_ratio': {
            'distribution': 'uniform',
            'min': 0.0,
            'max': 0.1
        },
        'weight_decay': {
            'distribution': 'log_uniform',
            'min': 1e-4,
            'max': 1e-1
        },

        # 优化器
        'adam_beta1': {
            'distribution': 'uniform',
            'min': 0.8,
            'max': 0.95
        },
        'adam_beta2': {
            'distribution': 'uniform',
            'min': 0.95,
            'max': 0.999
        }
    }
}
```

## 最佳实践

### 1. 从小规模开始

```python
# 初始探索：随机搜索，20 次运行
sweep_config_v1 = {
    'method': 'random',
    'parameters': {...}
}
wandb.agent(sweep_id_v1, train, count=20)

# 精细搜索：贝叶斯，缩小范围
sweep_config_v2 = {
    'method': 'bayes',
    'parameters': {
        'learning_rate': {
            'min': 5e-5,  # 从 1e-6 到 1e-4 缩小
            'max': 1e-4
        }
    }
}
```

### 2. 使用对数尺度

```python
# ✅ 好：学习率使用对数尺度
'learning_rate': {
    'distribution': 'log_uniform',
    'min': 1e-6,
    'max': 1e-2
}

# ❌ 差：线性尺度
'learning_rate': {
    'distribution': 'uniform',
    'min': 0.000001,
    'max': 0.01
}
```

### 3. 设置合理范围

```python
# 基于先验知识设定范围
'learning_rate': {'min': 1e-5, 'max': 1e-3},  # Adam 典型值
'batch_size': {'values': [16, 32, 64]},       # GPU 内存限制
'dropout': {'min': 0.1, 'max': 0.5}           # 太高会损害训练
```

### 4. 监控资源使用

```python
def train():
    run = wandb.init()

    # 记录系统指标
    wandb.log({
        'system/gpu_memory_allocated': torch.cuda.memory_allocated(),
        'system/gpu_memory_reserved': torch.cuda.memory_reserved()
    })
```

### 5. 保存最佳模型

```python
def train():
    run = wandb.init()
    best_acc = 0.0

    for epoch in range(config.epochs):
        val_acc = validate(model)

        if val_acc > best_acc:
            best_acc = val_acc
            # 保存最佳检查点
            torch.save(model.state_dict(), 'best_model.pth')
            wandb.save('best_model.pth')
```

## 资源

- **扫描文档**：https://docs.wandb.ai/guides/sweeps
- **配置参考**：https://docs.wandb.ai/guides/sweeps/configuration
- **示例**：https://github.com/wandb/examples/tree/master/examples/wandb-sweeps
