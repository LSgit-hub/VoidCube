# Modal 高级使用指南

## 多 GPU 训练

### 单节点多 GPU

```python
import modal

app = modal.App("multi-gpu-training")
image = modal.Image.debian_slim().pip_install("torch", "transformers", "accelerate")

@app.function(gpu="H100:4", image=image, timeout=7200)
def train_multi_gpu():
    from accelerate import Accelerator

    accelerator = Accelerator()
    model, optimizer, dataloader = accelerator.prepare(model, optimizer, dataloader)

    for batch in dataloader:
        outputs = model(**batch)
        loss = outputs.loss
        accelerator.backward(loss)
        optimizer.step()
```

### DeepSpeed 集成

```python
image = modal.Image.debian_slim().pip_install(
    "torch", "transformers", "deepspeed", "accelerate"
)

@app.function(gpu="A100:8", image=image, timeout=14400)
def deepspeed_train(config: dict):
    from transformers import Trainer, TrainingArguments

    args = TrainingArguments(
        output_dir="/outputs",
        deepspeed="ds_config.json",
        fp16=True,
        per_device_train_batch_size=4,
        gradient_accumulation_steps=4
    )

    trainer = Trainer(model=model, args=args, train_dataset=dataset)
    trainer.train()
```

### 多 GPU 注意事项

对于会重新执行 Python 入口点的框架（如 PyTorch Lightning），请使用：
- `ddp_spawn` 或 `ddp_notebook` 策略
- 将训练作为子进程运行以避免问题

```python
@app.function(gpu="H100:4")
def train_with_subprocess():
    import subprocess
    subprocess.run(["python", "-m", "torch.distributed.launch", "train.py"])
```

## 高级容器配置

### 多阶段构建以优化缓存

```python
# 阶段 1：基础依赖（缓存）
base_image = modal.Image.debian_slim().pip_install("torch", "numpy", "scipy")

# 阶段 2：ML 库（单独缓存）
ml_image = base_image.pip_install("transformers", "datasets", "accelerate")

# 阶段 3：自定义代码（代码变更时重新构建）
final_image = ml_image.copy_local_dir("./src", "/app/src")
```

### 自定义 Dockerfile

```python
image = modal.Image.from_dockerfile("./Dockerfile")
```

### 从 Git 安装

```python
image = modal.Image.debian_slim().pip_install(
    "git+https://github.com/huggingface/transformers.git@main"
)
```

### 使用 uv 加速安装

```python
image = modal.Image.debian_slim().uv_pip_install(
    "torch", "transformers", "accelerate"
)
```

## 高级类模式

### 生命周期钩子

```python
@app.cls(gpu="A10G")
class InferenceService:
    @modal.enter()
    def startup(self):
        """容器启动时调用一次"""
        self.model = load_model()
        self.tokenizer = load_tokenizer()

    @modal.exit()
    def shutdown(self):
        """容器关闭时调用"""
        cleanup_resources()

    @modal.method()
    def predict(self, text: str):
        return self.model(self.tokenizer(text))
```

### 并发请求处理

```python
@app.cls(
    gpu="A100",
    allow_concurrent_inputs=20,  # 每个容器处理 20 个请求
    container_idle_timeout=300
)
class BatchInference:
    @modal.enter()
    def load(self):
        self.model = load_model()

    @modal.method()
    def predict(self, inputs: list):
        return self.model.batch_predict(inputs)
```

### 输入并发 vs 批处理

- **输入并发**：多个请求同时处理（异步 I/O）
- **动态批处理**：请求累积后一起处理（GPU 效率）

```python
# 输入并发 - 适合 I/O 密集型
@app.function(allow_concurrent_inputs=10)
async def fetch_data(url: str):
    async with aiohttp.ClientSession() as session:
        return await session.get(url)

# 动态批处理 - 适合 GPU 推理
@app.function()
@modal.batched(max_batch_size=32, wait_ms=100)
async def batch_embed(texts: list[str]) -> list[list[float]]:
    return model.encode(texts)
```

## 高级卷操作

### 卷操作

```python
volume = modal.Volume.from_name("my-volume", create_if_missing=True)

@app.function(volumes={"/data": volume})
def volume_operations():
    import os

    # 写入数据
    with open("/data/output.txt", "w") as f:
        f.write("Results")

    # 提交更改（持久化到卷）
    volume.commit()

    # 从远程重新加载（获取最新数据）
    volume.reload()
```

### 函数间共享卷

```python
shared_volume = modal.Volume.from_name("shared-data", create_if_missing=True)

@app.function(volumes={"/shared": shared_volume})
def writer():
    with open("/shared/data.txt", "w") as f:
        f.write("Hello from writer")
    shared_volume.commit()

@app.function(volumes={"/shared": shared_volume})
def reader():
    shared_volume.reload()  # 获取最新数据
    with open("/shared/data.txt", "r") as f:
        return f.read()
```

### 云存储桶挂载

```python
# 挂载 S3 存储桶
bucket = modal.CloudBucketMount(
    bucket_name="my-bucket",
    secret=modal.Secret.from_name("aws-credentials")
)

@app.function(volumes={"/s3": bucket})
def process_s3_data():
    # 像本地文件系统一样访问 S3 文件
    data = open("/s3/data.parquet").read()
```

## 函数组合

### 链式函数调用

```python
@app.function()
def preprocess(data):
    return cleaned_data

@app.function(gpu="T4")
def inference(data):
    return predictions

@app.function()
def postprocess(predictions):
    return formatted_results

@app.function()
def pipeline(raw_data):
    cleaned = preprocess.remote(raw_data)
    predictions = inference.remote(cleaned)
    results = postprocess.remote(predictions)
    return results
```

### 并行扇出

```python
@app.function()
def process_item(item):
    return expensive_computation(item)

@app.function()
def parallel_pipeline(items):
    # 扇出：并行处理所有项目
    results = list(process_item.map(items))
    return results
```

### Starmap 处理多参数

```python
@app.function()
def process(x, y, z):
    return x + y + z

@app.function()
def orchestrate():
    args = [(1, 2, 3), (4, 5, 6), (7, 8, 9)]
    results = list(process.starmap(args))
    return results
```

## 高级 Web 端点

### WebSocket 支持

```python
from fastapi import FastAPI, WebSocket

app = modal.App("websocket-app")
web_app = FastAPI()

@web_app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(f"Processed: {data}")

@app.function()
@modal.asgi_app()
def ws_app():
    return web_app
```

### 流式响应

```python
from fastapi.responses import StreamingResponse

@app.function(gpu="A100")
def generate_stream(prompt: str):
    for token in model.generate_stream(prompt):
        yield token

@web_app.get("/stream")
async def stream_response(prompt: str):
    return StreamingResponse(
        generate_stream.remote_gen(prompt),
        media_type="text/event-stream"
    )
```

### 身份验证

```python
from fastapi import Depends, HTTPException, Header

async def verify_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    if not verify_jwt(token):
        raise HTTPException(status_code=403)
    return token

@web_app.post("/predict")
async def predict(data: dict, token: str = Depends(verify_token)):
    return model.predict(data)
```

## 成本优化

### 合理选择 GPU

```python
# 推理：较小的 GPU 通常足够
@app.function(gpu="L40S")  # 48GB，推理的最佳性价比
def inference():
    pass

# 训练：较大的 GPU 提高吞吐量
@app.function(gpu="A100-80GB")
def training():
    pass
```

### GPU 回退以提高可用性

```python
@app.function(gpu=["H100", "A100", "L40S"])  # 按顺序尝试
def flexible_compute():
    pass
```

### 缩减至零

```python
# 默认行为：空闲时缩减至零
@app.function(gpu="A100")
def on_demand():
    pass

# 保持容器预热以降低延迟（成本更高）
@app.function(gpu="A100", keep_warm=1)
def always_ready():
    pass
```

### 批处理提高效率

```python
# 批量处理以减少冷启动
@app.function(gpu="A100")
def batch_process(items: list):
    return [process(item) for item in items]

# 比单独调用更好
results = batch_process.remote(all_items)
```

## 监控和可观测性

### 结构化日志

```python
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.function()
def structured_logging(request_id: str, data: dict):
    logger.info(json.dumps({
        "event": "inference_start",
        "request_id": request_id,
        "input_size": len(data)
    }))

    result = process(data)

    logger.info(json.dumps({
        "event": "inference_complete",
        "request_id": request_id,
        "output_size": len(result)
    }))

    return result
```

### 自定义指标

```python
@app.function(gpu="A100")
def monitored_inference(inputs):
    import time

    start = time.time()
    results = model.predict(inputs)
    latency = time.time() - start

    # 记录指标（在 Modal 仪表板中可见）
    print(f"METRIC latency={latency:.3f}s batch_size={len(inputs)}")

    return results
```

## 生产部署

### 环境隔离

```python
import os

env = os.environ.get("MODAL_ENV", "dev")
app = modal.App(f"my-service-{env}")

# 环境特定配置
if env == "prod":
    gpu_config = "A100"
    timeout = 3600
else:
    gpu_config = "T4"
    timeout = 300
```

### 零停机部署

Modal 自动处理零停机部署：
1. 新容器构建并启动
2. 流量逐渐切换到新版本
3. 旧容器排空现有请求
4. 旧容器终止

### 健康检查

```python
@app.function()
@modal.web_endpoint()
def health():
    return {
        "status": "healthy",
        "model_loaded": hasattr(Model, "_model"),
        "gpu_available": torch.cuda.is_available()
    }
```

## 沙箱

### 交互式执行环境

```python
@app.function()
def run_sandbox():
    sandbox = modal.Sandbox.create(
        app=app,
        image=image,
        gpu="T4"
    )

    # 在沙箱中执行代码
    result = sandbox.exec("python", "-c", "print('Hello from sandbox')")

    sandbox.terminate()
    return result
```

## 调用已部署的函数

### 从外部代码调用

```python
# 从任何 Python 脚本调用已部署的函数
import modal

f = modal.Function.lookup("my-app", "my_function")
result = f.remote(arg1, arg2)
```

### REST API 调用

```bash
# 通过 HTTPS 访问已部署的端点
curl -X POST https://your-workspace--my-app-predict.modal.run \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world"}'
```
