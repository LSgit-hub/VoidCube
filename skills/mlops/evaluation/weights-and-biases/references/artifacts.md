# Artifacts 和模型注册表指南

使用 W&B Artifacts 进行数据版本控制和模型管理的完整指南。

## 目录
- 什么是 Artifacts
- 创建 Artifacts
- 使用 Artifacts
- 模型注册表
- 版本控制和血缘
- 最佳实践

## 什么是 Artifacts

Artifacts 是带有血缘跟踪的版本化数据集、模型或文件。

**关键特性：**
- 自动版本控制（v0、v1、v2...）
- 血缘跟踪（哪些运行产生/使用了 artifacts）
- 高效存储（去重）
- 协作（团队范围访问）
- 别名（latest、best、production）

**常见用例：**
- 数据集版本控制
- 模型检查点
- 预处理数据
- 评估结果
- 配置文件

## 创建 Artifacts

### 基本数据集 Artifact

```python
import wandb

run = wandb.init(project="my-project")

# 创建 artifact
dataset = wandb.Artifact(
    name='training-data',
    type='dataset',
    description='ImageNet 训练分割（带增强）',
    metadata={
        'size': '1.2M images',
        'format': 'JPEG',
        'resolution': '224x224'
    }
)

# 添加文件
dataset.add_file('data/train.csv')        # 单个文件
dataset.add_dir('data/images')            # 整个目录
dataset.add_reference('s3://bucket/data') # 云引用

# 记录 artifact
run.log_artifact(dataset)
wandb.finish()
```

### 模型 Artifact

```python
import torch
import wandb

run = wandb.init(project="my-project")

# 训练模型
model = train_model()

# 保存模型
torch.save(model.state_dict(), 'model.pth')

# 创建模型 artifact
model_artifact = wandb.Artifact(
    name='resnet50-classifier',
    type='model',
    description='在 ImageNet 上训练的 ResNet50',
    metadata={
        'architecture': 'ResNet50',
        'accuracy': 0.95,
        'loss': 0.15,
        'epochs': 50,
        'framework': 'PyTorch'
    }
)

# 添加模型文件
model_artifact.add_file('model.pth')

# 添加配置
model_artifact.add_file('config.yaml')

# 使用别名记录
run.log_artifact(model_artifact, aliases=['latest', 'best'])

wandb.finish()
```

### 预处理数据 Artifact

```python
import pandas as pd
import wandb

run = wandb.init(project="nlp-project")

# 预处理数据
df = pd.read_csv('raw_data.csv')
df_processed = preprocess(df)
df_processed.to_csv('processed_data.csv', index=False)

# 创建 artifact
processed_data = wandb.Artifact(
    name='processed-text-data',
    type='dataset',
    metadata={
        'rows': len(df_processed),
        'columns': list(df_processed.columns),
        'preprocessing_steps': ['lowercase', 'remove_stopwords', 'tokenize']
    }
)

processed_data.add_file('processed_data.csv')

# 记录 artifact
run.log_artifact(processed_data)
```

## 使用 Artifacts

### 下载并使用

```python
import wandb

run = wandb.init(project="my-project")

# 下载 artifact
artifact = run.use_artifact('training-data:latest')
artifact_dir = artifact.download()

# 使用文件
import pandas as pd
df = pd.read_csv(f'{artifact_dir}/train.csv')

# 使用 artifact 数据训练
model = train_model(df)
```

### 使用特定版本

```python
# 使用特定版本
artifact_v2 = run.use_artifact('training-data:v2')

# 使用别名
artifact_best = run.use_artifact('model:best')
artifact_prod = run.use_artifact('model:production')

# 从其他项目使用
artifact = run.use_artifact('team/other-project/model:latest')
```

### 检查 Artifact 元数据

```python
artifact = run.use_artifact('training-data:latest')

# 访问元数据
print(artifact.metadata)
print(f"Size: {artifact.metadata['size']}")

# 访问版本信息
print(f"Version: {artifact.version}")
print(f"Created at: {artifact.created_at}")
print(f"Digest: {artifact.digest}")
```

## 模型注册表

将模型链接到中央注册表以进行治理和部署。

### 创建模型注册表

```python
# 在 W&B UI 中：
# 1. 进入 "Registry" 标签
# 2. 创建新注册表："production-models"
# 3. 定义阶段：development、staging、production
```

### 将模型链接到注册表

```python
import wandb

run = wandb.init(project="training")

# 创建模型 artifact
model_artifact = wandb.Artifact(
    name='sentiment-classifier',
    type='model',
    metadata={'accuracy': 0.94, 'f1': 0.92}
)

model_artifact.add_file('model.pth')

# 记录 artifact
run.log_artifact(model_artifact)

# 链接到注册表
run.link_artifact(
    model_artifact,
    'model-registry/production-models',
    aliases=['staging']  # 部署到 staging
)

wandb.finish()
```

### 在注册表中提升模型

```python
# 从注册表检索模型
api = wandb.Api()
artifact = api.artifact('model-registry/production-models/sentiment-classifier:staging')

# 提升到生产环境
artifact.link('model-registry/production-models', aliases=['production'])

# 从生产环境降级
artifact.aliases = ['archived']
artifact.save()
```

### 从注册表使用模型

```python
import wandb

run = wandb.init()

# 下载生产模型
model_artifact = run.use_artifact(
    'model-registry/production-models/sentiment-classifier:production'
)

model_dir = model_artifact.download()

# 加载并使用
import torch
model = torch.load(f'{model_dir}/model.pth')
model.eval()
```

## 版本控制和血缘

### 自动版本控制

```python
# 首次记录：创建 v0
run1 = wandb.init(project="my-project")
dataset_v0 = wandb.Artifact('my-dataset', type='dataset')
dataset_v0.add_file('data_v1.csv')
run1.log_artifact(dataset_v0)

# 第二次记录同名：创建 v1
run2 = wandb.init(project="my-project")
dataset_v1 = wandb.Artifact('my-dataset', type='dataset')
dataset_v1.add_file('data_v2.csv')  # 不同内容
run2.log_artifact(dataset_v1)

# 第三次记录与 v1 相同内容：引用 v1（无新版本）
run3 = wandb.init(project="my-project")
dataset_v1_again = wandb.Artifact('my-dataset', type='dataset')
dataset_v1_again.add_file('data_v2.csv')  # 与 v1 相同内容
run3.log_artifact(dataset_v1_again)  # 仍是 v1，不创建 v2
```

### 跟踪血缘

```python
# 训练运行
run = wandb.init(project="my-project")

# 使用数据集（输入）
dataset = run.use_artifact('training-data:v3')
data = load_data(dataset.download())

# 训练模型
model = train(data)

# 保存模型（输出）
model_artifact = wandb.Artifact('trained-model', type='model')
torch.save(model.state_dict(), 'model.pth')
model_artifact.add_file('model.pth')
run.log_artifact(model_artifact)

# 血缘自动跟踪：
# training-data:v3 --> [run] --> trained-model:v0
```

### 查看血缘图

```python
# 在 W&B UI 中：
# Artifacts → 选择 artifact → Lineage 标签
# 显示：
# - 哪些运行产生了此 artifact
# - 哪些运行使用了此 artifact
# - 父/子 artifacts
```

## Artifact 类型

### 数据集 Artifacts

```python
# 原始数据
raw_data = wandb.Artifact('raw-data', type='dataset')
raw_data.add_dir('raw/')

# 处理后的数据
processed_data = wandb.Artifact('processed-data', type='dataset')
processed_data.add_dir('processed/')

# 训练/验证/测试分割
train_split = wandb.Artifact('train-split', type='dataset')
train_split.add_file('train.csv')

val_split = wandb.Artifact('val-split', type='dataset')
val_split.add_file('val.csv')
```

### 模型 Artifacts

```python
# 训练期间的检查点
checkpoint = wandb.Artifact('checkpoint-epoch-10', type='model')
checkpoint.add_file('checkpoint_epoch_10.pth')

# 最终模型
final_model = wandb.Artifact('final-model', type='model')
final_model.add_file('model.pth')
final_model.add_file('tokenizer.json')

# 量化模型
quantized = wandb.Artifact('quantized-model', type='model')
quantized.add_file('model_int8.onnx')
```

### 结果 Artifacts

```python
# 预测
predictions = wandb.Artifact('test-predictions', type='predictions')
predictions.add_file('predictions.csv')

# 评估指标
eval_results = wandb.Artifact('evaluation', type='evaluation')
eval_results.add_file('metrics.json')
eval_results.add_file('confusion_matrix.png')
```

## 高级模式

### 增量 Artifacts

增量添加文件而无需重新上传。

```python
run = wandb.init(project="my-project")

# 创建 artifact
dataset = wandb.Artifact('incremental-dataset', type='dataset')

# 增量添加文件
for i in range(100):
    filename = f'batch_{i}.csv'
    process_batch(i, filename)
    dataset.add_file(filename)

    # 记录进度
    if (i + 1) % 10 == 0:
        print(f"Added {i + 1}/100 batches")

# 记录完整 artifact
run.log_artifact(dataset)
```

### Artifact 表格

使用 W&B Tables 跟踪结构化数据。

```python
import wandb

run = wandb.init(project="my-project")

# 创建表格
table = wandb.Table(columns=["id", "image", "label", "prediction"])

for idx, (img, label, pred) in enumerate(zip(images, labels, predictions)):
    table.add_data(
        idx,
        wandb.Image(img),
        label,
        pred
    )

# 作为 artifact 记录
artifact = wandb.Artifact('predictions-table', type='predictions')
artifact.add(table, "predictions")
run.log_artifact(artifact)
```

### Artifact 引用

引用外部数据而不复制。

```python
# S3 引用
dataset = wandb.Artifact('s3-dataset', type='dataset')
dataset.add_reference('s3://my-bucket/data/', name='train')
dataset.add_reference('s3://my-bucket/labels/', name='labels')

# GCS 引用
dataset.add_reference('gs://my-bucket/data/')

# HTTP 引用
dataset.add_reference('https://example.com/data.zip')

# 本地文件系统引用（用于共享存储）
dataset.add_reference('file:///mnt/shared/data')
```

## 协作模式

### 团队数据集共享

```python
# 数据工程师创建数据集
run = wandb.init(project="data-eng", entity="my-team")
dataset = wandb.Artifact('shared-dataset', type='dataset')
dataset.add_dir('data/')
run.log_artifact(dataset, aliases=['latest', 'production'])

# ML 工程师使用数据集
run = wandb.init(project="ml-training", entity="my-team")
dataset = run.use_artifact('my-team/data-eng/shared-dataset:production')
data = load_data(dataset.download())
```

### 模型交接

```python
# 训练团队
train_run = wandb.init(project="model-training", entity="ml-team")
model = train_model()
model_artifact = wandb.Artifact('nlp-model', type='model')
model_artifact.add_file('model.pth')
train_run.log_artifact(model_artifact)
train_run.link_artifact(model_artifact, 'model-registry/nlp-models', aliases=['candidate'])

# 评估团队
eval_run = wandb.init(project="model-eval", entity="ml-team")
model_artifact = eval_run.use_artifact('model-registry/nlp-models/nlp-model:candidate')
metrics = evaluate_model(model_artifact)

if metrics['f1'] > 0.9:
    # 提升到生产环境
    model_artifact.link('model-registry/nlp-models', aliases=['production'])
```

## 最佳实践

### 1. 使用描述性名称

```python
# ✅ 好：描述性名称
wandb.Artifact('imagenet-train-augmented-v2', type='dataset')
wandb.Artifact('bert-base-sentiment-finetuned', type='model')

# ❌ 差：通用名称
wandb.Artifact('dataset1', type='dataset')
wandb.Artifact('model', type='model')
```

### 2. 添加全面的元数据

```python
model_artifact = wandb.Artifact(
    'production-model',
    type='model',
    description='用于产品分类的 ResNet50 分类器',
    metadata={
        # 模型信息
        'architecture': 'ResNet50',
        'framework': 'PyTorch 2.0',
        'pretrained': True,

        # 性能
        'accuracy': 0.95,
        'f1_score': 0.93,
        'inference_time_ms': 15,

        # 训练
        'epochs': 50,
        'dataset': 'imagenet',
        'num_samples': 1200000,

        # 业务上下文
        'use_case': 'e-commerce product classification',
        'owner': 'ml-team@company.com',
        'approved_by': 'data-science-lead'
    }
)
```

### 3. 为部署阶段使用别名

```python
# 开发
run.log_artifact(model, aliases=['dev', 'latest'])

# Staging
run.log_artifact(model, aliases=['staging'])

# 生产
run.log_artifact(model, aliases=['production', 'v1.2.0'])

# 归档旧版本
old_artifact = api.artifact('model:production')
old_artifact.aliases = ['archived-v1.1.0']
old_artifact.save()
```

### 4. 跟踪数据血缘

```python
def create_training_pipeline():
    run = wandb.init(project="pipeline")

    # 1. 加载原始数据
    raw_data = run.use_artifact('raw-data:latest')

    # 2. 预处理
    processed = preprocess(raw_data)
    processed_artifact = wandb.Artifact('processed-data', type='dataset')
    processed_artifact.add_file('processed.csv')
    run.log_artifact(processed_artifact)

    # 3. 训练模型
    model = train(processed)
    model_artifact = wandb.Artifact('trained-model', type='model')
    model_artifact.add_file('model.pth')
    run.log_artifact(model_artifact)

    # 血缘：raw-data → processed-data → trained-model
```

### 5. 高效存储

```python
# ✅ 好：引用大文件
large_dataset = wandb.Artifact('large-dataset', type='dataset')
large_dataset.add_reference('s3://bucket/huge-file.tar.gz')

# ❌ 差：上传巨大文件
# large_dataset.add_file('huge-file.tar.gz')  # 不要这样做

# ✅ 好：仅上传元数据
metadata_artifact = wandb.Artifact('dataset-metadata', type='dataset')
metadata_artifact.add_file('metadata.json')  # 小文件
```

## 资源

- **Artifacts 文档**：https://docs.wandb.ai/guides/artifacts
- **模型注册表**：https://docs.wandb.ai/guides/model-registry
- **最佳实践**：https://wandb.ai/site/articles/versioning-data-and-models-in-ml
