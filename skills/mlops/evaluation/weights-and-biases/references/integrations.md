# 框架集成指南

将 W&B 与流行 ML 框架集成的完整指南。

## 目录
- HuggingFace Transformers
- PyTorch Lightning
- Keras/TensorFlow
- Fast.ai
- XGBoost/LightGBM
- PyTorch 原生
- 自定义集成

## HuggingFace Transformers

### 自动集成

```python
from transformers import Trainer, TrainingArguments
import wandb

# 初始化 W&B
wandb.init(project="hf-transformers", name="bert-finetuning")

# 带 W&B 的训练参数
training_args = TrainingArguments(
    output_dir="./results",
    report_to="wandb",  # 启用 W&B 日志
    run_name="bert-base-finetuning",

    # 训练参数
    num_train_epochs=3,
    per_device_train_batch_size=16,
    per_device_eval_batch_size=64,
    learning_rate=2e-5,

    # 日志
    logging_dir="./logs",
    logging_steps=100,
    logging_first_step=True,

    # 评估
    evaluation_strategy="steps",
    eval_steps=500,
    save_steps=500,

    # 其他
    load_best_model_at_end=True,
    metric_for_best_model="eval_accuracy"
)

# Trainer 自动记录到 W&B
trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=train_dataset,
    eval_dataset=eval_dataset,
    compute_metrics=compute_metrics
)

# 训练（自动记录指标）
trainer.train()

# 结束 W&B 运行
wandb.finish()
```

### 自定义日志

```python
from transformers import Trainer, TrainingArguments
from transformers.integrations import WandbCallback
import wandb

class CustomWandbCallback(WandbCallback):
    def on_evaluate(self, args, state, control, metrics=None, **kwargs):
        super().on_evaluate(args, state, control, metrics, **kwargs)

        # 记录自定义指标
        wandb.log({
            "custom/eval_score": metrics["eval_accuracy"] * 100,
            "custom/epoch": state.epoch
        })

# 使用自定义回调
trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=train_dataset,
    eval_dataset=eval_dataset,
    callbacks=[CustomWandbCallback()]
)
```

### 将模型记录到注册表

```python
from transformers import Trainer, TrainingArguments

training_args = TrainingArguments(
    output_dir="./results",
    report_to="wandb",
    load_best_model_at_end=True
)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=train_dataset,
    eval_dataset=eval_dataset
)

trainer.train()

# 将最终模型保存为 artifact
model_artifact = wandb.Artifact(
    'hf-bert-model',
    type='model',
    description='在情感分析上微调的 BERT'
)

# 保存模型文件
trainer.save_model("./final_model")
model_artifact.add_dir("./final_model")

# 记录 artifact
wandb.log_artifact(model_artifact, aliases=['best', 'production'])
wandb.finish()
```

## PyTorch Lightning

### 基本集成

```python
import pytorch_lightning as pl
from pytorch_lightning.loggers import WandbLogger
import wandb

# 创建 W&B logger
wandb_logger = WandbLogger(
    project="lightning-demo",
    name="resnet50-training",
    log_model=True,  # 将模型检查点记录为 artifacts
    save_code=True   # 将代码保存为 artifact
)

# Lightning 模块
class LitModel(pl.LightningModule):
    def __init__(self, learning_rate=0.001):
        super().__init__()
        self.save_hyperparameters()
        self.model = create_model()

    def training_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self.model(x)
        loss = F.cross_entropy(y_hat, y)

        # 记录指标（自动发送到 W&B）
        self.log('train/loss', loss, on_step=True, on_epoch=True)
        self.log('train/accuracy', accuracy(y_hat, y), on_epoch=True)

        return loss

    def validation_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self.model(x)
        loss = F.cross_entropy(y_hat, y)

        self.log('val/loss', loss, on_step=False, on_epoch=True)
        self.log('val/accuracy', accuracy(y_hat, y), on_epoch=True)

        return loss

    def configure_optimizers(self):
        return torch.optim.Adam(self.parameters(), lr=self.hparams.learning_rate)

# 带 W&B logger 的 Trainer
trainer = pl.Trainer(
    logger=wandb_logger,
    max_epochs=10,
    accelerator="gpu",
    devices=1
)

# 训练（自动记录指标）
trainer.fit(model, datamodule=dm)

# 结束 W&B 运行
wandb.finish()
```

### 记录媒体

```python
class LitModel(pl.LightningModule):
    def validation_step(self, batch, batch_idx):
        x, y = batch
        y_hat = self.model(x)

        # 记录图像（仅第一个批次）
        if batch_idx == 0:
            self.logger.experiment.log({
                "examples": [wandb.Image(img) for img in x[:8]]
            })

        return loss

    def on_validation_epoch_end(self):
        # 记录混淆矩阵
        cm = compute_confusion_matrix(self.all_preds, self.all_targets)

        self.logger.experiment.log({
            "confusion_matrix": wandb.plot.confusion_matrix(
                probs=None,
                y_true=self.all_targets,
                preds=self.all_preds,
                class_names=self.class_names
            )
        })
```

### 超参数扫描

```python
import pytorch_lightning as pl
from pytorch_lightning.loggers import WandbLogger
import wandb

# 定义扫描
sweep_config = {
    'method': 'bayes',
    'metric': {'name': 'val/accuracy', 'goal': 'maximize'},
    'parameters': {
        'learning_rate': {'min': 1e-5, 'max': 1e-2, 'distribution': 'log_uniform'},
        'batch_size': {'values': [16, 32, 64]},
        'hidden_size': {'values': [128, 256, 512]}
    }
}

sweep_id = wandb.sweep(sweep_config, project="lightning-sweeps")

def train():
    # 初始化 W&B
    run = wandb.init()

    # 获取超参数
    config = wandb.config

    # 创建 logger
    wandb_logger = WandbLogger()

    # 使用扫描参数创建模型
    model = LitModel(
        learning_rate=config.learning_rate,
        hidden_size=config.hidden_size
    )

    # 使用扫描批大小创建数据模块
    dm = DataModule(batch_size=config.batch_size)

    # 训练
    trainer = pl.Trainer(logger=wandb_logger, max_epochs=10)
    trainer.fit(model, dm)

# 运行扫描
wandb.agent(sweep_id, function=train, count=30)
```

## Keras/TensorFlow

### 使用回调

```python
import tensorflow as tf
from wandb.keras import WandbCallback
import wandb

# 初始化 W&B
wandb.init(
    project="keras-demo",
    config={
        "learning_rate": 0.001,
        "epochs": 10,
        "batch_size": 32
    }
)

config = wandb.config

# 构建模型
model = tf.keras.Sequential([
    tf.keras.layers.Dense(128, activation='relu'),
    tf.keras.layers.Dropout(0.2),
    tf.keras.layers.Dense(10, activation='softmax')
])

model.compile(
    optimizer=tf.keras.optimizers.Adam(config.learning_rate),
    loss='sparse_categorical_crossentropy',
    metrics=['accuracy']
)

# 使用 W&B 回调训练
history = model.fit(
    x_train, y_train,
    validation_data=(x_val, y_val),
    epochs=config.epochs,
    batch_size=config.batch_size,
    callbacks=[
        WandbCallback(
            log_weights=True,      # 记录模型权重
            log_gradients=True,    # 记录梯度
            training_data=(x_train, y_train),
            validation_data=(x_val, y_val),
            labels=class_names
        )
    ]
)

# 将模型保存为 artifact
model.save('model.h5')
artifact = wandb.Artifact('keras-model', type='model')
artifact.add_file('model.h5')
wandb.log_artifact(artifact)

wandb.finish()
```

### 自定义训练循环

```python
import tensorflow as tf
import wandb

wandb.init(project="tf-custom-loop")

# 模型、优化器、损失
model = create_model()
optimizer = tf.keras.optimizers.Adam(1e-3)
loss_fn = tf.keras.losses.SparseCategoricalCrossentropy()

# 指标
train_loss = tf.keras.metrics.Mean(name='train_loss')
train_accuracy = tf.keras.metrics.SparseCategoricalAccuracy(name='train_accuracy')

@tf.function
def train_step(x, y):
    with tf.GradientTape() as tape:
        predictions = model(x, training=True)
        loss = loss_fn(y, predictions)

    gradients = tape.gradient(loss, model.trainable_variables)
    optimizer.apply_gradients(zip(gradients, model.trainable_variables))

    train_loss(loss)
    train_accuracy(y, predictions)

# 训练循环
for epoch in range(EPOCHS):
    train_loss.reset_states()
    train_accuracy.reset_states()

    for step, (x, y) in enumerate(train_dataset):
        train_step(x, y)

        # 每 100 步记录一次
        if step % 100 == 0:
            wandb.log({
                'train/loss': train_loss.result().numpy(),
                'train/accuracy': train_accuracy.result().numpy(),
                'epoch': epoch,
                'step': step
            })

    # 记录 epoch 指标
    wandb.log({
        'epoch/train_loss': train_loss.result().numpy(),
        'epoch/train_accuracy': train_accuracy.result().numpy(),
        'epoch': epoch
    })

wandb.finish()
```

## Fast.ai

### 使用回调

```python
from fastai.vision.all import *
from fastai.callback.wandb import *
import wandb

# 初始化 W&B
wandb.init(project="fastai-demo")

# 创建数据加载器
dls = ImageDataLoaders.from_folder(
    path,
    train='train',
    valid='valid',
    bs=64
)

# 使用 W&B 回调创建 learner
learn = vision_learner(
    dls,
    resnet34,
    metrics=accuracy,
    cbs=WandbCallback(
        log_preds=True,     # 记录预测
        log_model=True,     # 将模型记录为 artifact
        log_dataset=True    # 将数据集记录为 artifact
    )
)

# 训练（自动记录指标）
learn.fine_tune(5)

wandb.finish()
```

## XGBoost/LightGBM

### XGBoost

```python
import xgboost as xgb
import wandb

# 初始化 W&B
run = wandb.init(project="xgboost-demo", config={
    "max_depth": 6,
    "learning_rate": 0.1,
    "n_estimators": 100
})

config = wandb.config

# 创建 DMatrix
dtrain = xgb.DMatrix(X_train, label=y_train)
dval = xgb.DMatrix(X_val, label=y_val)

# XGBoost 参数
params = {
    'max_depth': config.max_depth,
    'learning_rate': config.learning_rate,
    'objective': 'binary:logistic',
    'eval_metric': ['logloss', 'auc']
}

# W&B 自定义回调
def wandb_callback(env):
    """将 XGBoost 指标记录到 W&B。"""
    for metric_name, metric_value in env.evaluation_result_list:
        wandb.log({
            f"{metric_name}": metric_value,
            "iteration": env.iteration
        })

# 使用回调训练
model = xgb.train(
    params,
    dtrain,
    num_boost_round=config.n_estimators,
    evals=[(dtrain, 'train'), (dval, 'val')],
    callbacks=[wandb_callback],
    verbose_eval=10
)

# 保存模型
model.save_model('xgboost_model.json')
artifact = wandb.Artifact('xgboost-model', type='model')
artifact.add_file('xgboost_model.json')
wandb.log_artifact(artifact)

wandb.finish()
```

### LightGBM

```python
import lightgbm as lgb
import wandb

run = wandb.init(project="lgbm-demo")

# 创建数据集
train_data = lgb.Dataset(X_train, label=y_train)
val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)

# 参数
params = {
    'objective': 'binary',
    'metric': ['binary_logloss', 'auc'],
    'learning_rate': 0.1,
    'num_leaves': 31
}

# 自定义回调
def log_to_wandb(env):
    """将 LightGBM 指标记录到 W&B。"""
    for entry in env.evaluation_result_list:
        dataset_name, metric_name, metric_value, _ = entry
        wandb.log({
            f"{dataset_name}/{metric_name}": metric_value,
            "iteration": env.iteration
        })

# 训练
model = lgb.train(
    params,
    train_data,
    num_boost_round=100,
    valid_sets=[train_data, val_data],
    valid_names=['train', 'val'],
    callbacks=[log_to_wandb]
)

# 保存模型
model.save_model('lgbm_model.txt')
artifact = wandb.Artifact('lgbm-model', type='model')
artifact.add_file('lgbm_model.txt')
wandb.log_artifact(artifact)

wandb.finish()
```

## PyTorch 原生

### 训练循环集成

```python
import torch
import torch.nn as nn
import torch.optim as optim
import wandb

# 初始化 W&B
wandb.init(project="pytorch-native", config={
    "learning_rate": 0.001,
    "epochs": 10,
    "batch_size": 32
})

config = wandb.config

# 模型、损失、优化器
model = create_model()
criterion = nn.CrossEntropyLoss()
optimizer = optim.Adam(model.parameters(), lr=config.learning_rate)

# 监视模型（记录梯度和参数）
wandb.watch(model, criterion, log="all", log_freq=100)

# 训练循环
for epoch in range(config.epochs):
    model.train()
    train_loss = 0.0
    correct = 0
    total = 0

    for batch_idx, (data, target) in enumerate(train_loader):
        data, target = data.to(device), target.to(device)

        # 前向传播
        optimizer.zero_grad()
        output = model(data)
        loss = criterion(output, target)

        # 反向传播
        loss.backward()
        optimizer.step()

        # 跟踪指标
        train_loss += loss.item()
        _, predicted = output.max(1)
        total += target.size(0)
        correct += predicted.eq(target).sum().item()

        # 每 100 批次记录一次
        if batch_idx % 100 == 0:
            wandb.log({
                'train/loss': loss.item(),
                'train/batch_accuracy': 100. * correct / total,
                'epoch': epoch,
                'batch': batch_idx
            })

    # 验证
    model.eval()
    val_loss = 0.0
    val_correct = 0
    val_total = 0

    with torch.no_grad():
        for data, target in val_loader:
            data, target = data.to(device), target.to(device)
            output = model(data)
            loss = criterion(output, target)

            val_loss += loss.item()
            _, predicted = output.max(1)
            val_total += target.size(0)
            val_correct += predicted.eq(target).sum().item()

    # 记录 epoch 指标
    wandb.log({
        'epoch/train_loss': train_loss / len(train_loader),
        'epoch/train_accuracy': 100. * correct / total,
        'epoch/val_loss': val_loss / len(val_loader),
        'epoch/val_accuracy': 100. * val_correct / val_total,
        'epoch': epoch
    })

# 保存最终模型
torch.save(model.state_dict(), 'model.pth')
artifact = wandb.Artifact('final-model', type='model')
artifact.add_file('model.pth')
wandb.log_artifact(artifact)

wandb.finish()
```

## 自定义集成

### 通用框架集成

```python
import wandb

class WandbIntegration:
    """通用 W&B 集成包装器。"""

    def __init__(self, project, config):
        self.run = wandb.init(project=project, config=config)
        self.config = wandb.config
        self.step = 0

    def log_metrics(self, metrics, step=None):
        """记录训练指标。"""
        if step is None:
            step = self.step
            self.step += 1

        wandb.log(metrics, step=step)

    def log_images(self, images, caption=""):
        """记录图像。"""
        wandb.log({
            caption: [wandb.Image(img) for img in images]
        })

    def log_table(self, data, columns):
        """记录表格数据。"""
        table = wandb.Table(columns=columns, data=data)
        wandb.log({"table": table})

    def save_model(self, model_path, metadata=None):
        """将模型保存为 artifact。"""
        artifact = wandb.Artifact(
            'model',
            type='model',
            metadata=metadata or {}
        )
        artifact.add_file(model_path)
        self.run.log_artifact(artifact)

    def finish(self):
        """结束 W&B 运行。"""
        wandb.finish()

# 使用
wb = WandbIntegration(project="my-project", config={"lr": 0.001})

# 训练循环
for epoch in range(10):
    # 你的训练代码
    loss, accuracy = train_epoch()

    # 记录指标
    wb.log_metrics({
        'train/loss': loss,
        'train/accuracy': accuracy
    })

# 保存模型
wb.save_model('model.pth', metadata={'accuracy': 0.95})
wb.finish()
```

## 资源

- **集成指南**：https://docs.wandb.ai/guides/integrations
- **HuggingFace**：https://docs.wandb.ai/guides/integrations/huggingface
- **PyTorch Lightning**：https://docs.wandb.ai/guides/integrations/lightning
- **Keras**：https://docs.wandb.ai/guides/integrations/keras
- **示例**：https://github.com/wandb/examples
