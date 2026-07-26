# Modal 故障排除指南

## 安装问题

### 身份验证失败

**错误**：`modal setup` 无法完成或令牌无效

**解决方案**：
```bash
# 重新认证
modal token new

# 检查当前令牌
modal config show

# 通过环境变量设置令牌
export MODAL_TOKEN_ID=ak-...
export MODAL_TOKEN_SECRET=as-...
```

### 包安装问题

**错误**：`pip install modal` 失败

**解决方案**：
```bash
# 升级 pip
pip install --upgrade pip

# 使用特定 Python 版本安装
python3.14 -m pip install modal

# 从 wheel 安装
pip install modal --prefer-binary
```

## 容器镜像问题

### 镜像构建失败

**错误**：`ImageBuilderError: Failed to build image`

**解决方案**：
```python
# 固定包版本以避免冲突
image = modal.Image.debian_slim().pip_install(
    "torch==2.1.0",
    "transformers==4.36.0",  # 固定版本
    "accelerate==0.25.0"
)

# 使用兼容的 CUDA 版本
image = modal.Image.from_registry(
    "nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04",  # 匹配 PyTorch CUDA
    add_python="3.14"
)
```

### 依赖冲突

**错误**：`ERROR: Cannot install package due to conflicting dependencies`

**解决方案**：
```python
# 分层安装依赖
base = modal.Image.debian_slim().pip_install("torch")
ml = base.pip_install("transformers")  # 在 torch 之后安装

# 使用 uv 进行更好的解析
image = modal.Image.debian_slim().uv_pip_install(
    "torch", "transformers"
)
```

### 大镜像构建超时

**错误**：镜像构建超过时间限制

**解决方案**：
```python
# 拆分为多个层（更好的缓存）
base = modal.Image.debian_slim().pip_install("torch")  # 缓存
ml = base.pip_install("transformers", "datasets")      # 缓存
app = ml.copy_local_dir("./src", "/app")               # 代码变更时重新构建

# 在构建期间下载模型，而非运行时
image = modal.Image.debian_slim().pip_install("transformers").run_commands(
    "python -c 'from transformers import AutoModel; AutoModel.from_pretrained(\"bert-base\")'"
)
```

## GPU 问题

### GPU 不可用

**错误**：`RuntimeError: CUDA not available`

**解决方案**：
```python
# 确保指定 GPU
@app.function(gpu="T4")  # 必须指定 GPU
def my_function():
    import torch
    assert torch.cuda.is_available()

# 检查镜像中的 CUDA 兼容性
image = modal.Image.from_registry(
    "nvidia/cuda:12.1.0-cudnn8-devel-ubuntu22.04",
    add_python="3.14"
).pip_install(
    "torch",
    index_url="https://download.pytorch.org/whl/cu121"  # 匹配 CUDA
)
```

### GPU 内存不足

**错误**：`torch.cuda.OutOfMemoryError: CUDA out of memory`

**解决方案**：
```python
# 使用更大的 GPU
@app.function(gpu="A100-80GB")  # 更多显存
def train():
    pass

# 启用内存优化
@app.function(gpu="A100")
def memory_optimized():
    import torch
    torch.backends.cuda.enable_flash_sdp(True)

    # 使用梯度检查点
    model.gradient_checkpointing_enable()

    # 混合精度
    with torch.autocast(device_type="cuda", dtype=torch.float16):
        outputs = model(**inputs)
```

### 分配了错误的 GPU

**错误**：获得了与请求不同的 GPU

**解决方案**：
```python
# 使用严格 GPU 选择
@app.function(gpu="H100!")  # H100! 防止自动升级到 H200

# 指定确切的内存变体
@app.function(gpu="A100-80GB")  # 不只是 "A100"

# 在运行时检查 GPU
@app.function(gpu="A100")
def check_gpu():
    import subprocess
    result = subprocess.run(["nvidia-smi"], capture_output=True, text=True)
    print(result.stdout)
```

## 冷启动问题

### 冷启动缓慢

**问题**：首次请求耗时过长

**解决方案**：
```python
# 保持容器预热
@app.function(
    container_idle_timeout=600,  # 保持预热 10 分钟
    keep_warm=1                  # 始终保持 1 个容器就绪
)
def low_latency():
    pass

# 在容器启动时加载模型
@app.cls(gpu="A100")
class Model:
    @modal.enter()
    def load(self):
        # 这在容器启动时运行一次，而非每次请求
        self.model = load_heavy_model()

# 在卷中缓存模型
volume = modal.Volume.from_name("models", create_if_missing=True)

@app.function(volumes={"/cache": volume})
def cached_model():
    if os.path.exists("/cache/model"):
        model = load_from_disk("/cache/model")
    else:
        model = download_model()
        save_to_disk(model, "/cache/model")
        volume.commit()
```

### 容器频繁重启

**问题**：容器被频繁杀死和重启

**解决方案**：
```python
# 增加内存
@app.function(memory=32768)  # 32GB RAM
def memory_heavy():
    pass

# 增加超时
@app.function(timeout=3600)  # 1 小时
def long_running():
    pass

# 优雅处理信号
import signal

def handler(signum, frame):
    cleanup()
    exit(0)

signal.signal(signal.SIGTERM, handler)
```

## 卷问题

### 卷更改未持久化

**错误**：写入卷的数据消失

**解决方案**：
```python
volume = modal.Volume.from_name("my-volume", create_if_missing=True)

@app.function(volumes={"/data": volume})
def write_data():
    with open("/data/file.txt", "w") as f:
        f.write("data")

    # 关键：提交更改！
    volume.commit()
```

### 卷读取显示过期数据

**错误**：从卷读取过时数据

**解决方案**：
```python
@app.function(volumes={"/data": volume})
def read_data():
    # 重新加载以获取最新数据
    volume.reload()

    with open("/data/file.txt", "r") as f:
        return f.read()
```

### 卷挂载失败

**错误**：`VolumeError: Failed to mount volume`

**解决方案**：
```python
# 确保卷存在
volume = modal.Volume.from_name("my-volume", create_if_missing=True)

# 使用绝对路径
@app.function(volumes={"/data": volume})  # 不是 "./data"
def my_function():
    pass

# 在仪表板中检查卷
# modal volume list
```

## Web 端点问题

### 端点返回 502

**错误**：网关超时或错误网关

**解决方案**：
```python
# 增加超时
@app.function(timeout=300)  # 5 分钟
@modal.web_endpoint()
def slow_endpoint():
    pass

# 对长时间操作返回流式响应
from fastapi.responses import StreamingResponse

@app.function()
@modal.asgi_app()
def streaming_app():
    async def generate():
        for i in range(100):
            yield f"data: {i}\n\n"
            await process_chunk(i)
    return StreamingResponse(generate(), media_type="text/event-stream")
```

### 端点不可访问

**错误**：404 或无法访问端点

**解决方案**：
```bash
# 检查部署状态
modal app list

# 重新部署
modal deploy my_app.py

# 检查日志
modal app logs my-app
```

### CORS 错误

**错误**：跨域请求被阻止

**解决方案**：
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

web_app = FastAPI()
web_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.function()
@modal.asgi_app()
def cors_enabled():
    return web_app
```

## 密钥问题

### 密钥未找到

**错误**：`SecretNotFound: Secret 'my-secret' not found`

**解决方案**：
```bash
# 通过 CLI 创建密钥
modal secret create my-secret KEY=value

# 列出密钥
modal secret list

# 检查密钥名称是否完全匹配
```

### 密钥值不可访问

**错误**：环境变量为空

**解决方案**：
```python
# 确保密钥已附加
@app.function(secrets=[modal.Secret.from_name("my-secret")])
def use_secret():
    import os
    value = os.environ.get("KEY")  # 使用 get() 处理缺失情况
    if not value:
        raise ValueError("KEY not set in secret")
```

## 调度问题

### 计划任务未运行

**错误**：Cron 任务未执行

**解决方案**：
```python
# 验证 cron 语法
@app.function(schedule=modal.Cron("0 0 * * *"))  # 每天 UTC 午夜
def daily_job():
    pass

# 检查时区（Modal 使用 UTC）
# "0 8 * * *" = UTC 上午 8 点，而非本地时间

# 确保应用已部署
# modal deploy my_app.py
```

### 任务多次运行

**问题**：计划任务执行次数超过预期

**解决方案**：
```python
# 实现幂等性
@app.function(schedule=modal.Cron("0 * * * *"))
def hourly_job():
    job_id = get_current_hour_id()
    if already_processed(job_id):
        return
    process()
    mark_processed(job_id)
```

## 调试技巧

### 启用调试日志

```python
import logging
logging.basicConfig(level=logging.DEBUG)

@app.function()
def debug_function():
    logging.debug("Debug message")
    logging.info("Info message")
```

### 查看容器日志

```bash
# 流式日志
modal app logs my-app

# 查看特定函数
modal app logs my-app --function my_function

# 查看历史日志
modal app logs my-app --since 1h
```

### 本地测试

```python
# 在不使用 Modal 的情况下本地运行函数
if __name__ == "__main__":
    result = my_function.local()  # 在你的机器上运行
    print(result)
```

### 检查容器

```python
@app.function(gpu="T4")
def debug_environment():
    import subprocess
    import sys

    # 系统信息
    print(f"Python: {sys.version}")
    print(subprocess.run(["nvidia-smi"], capture_output=True, text=True).stdout)
    print(subprocess.run(["pip", "list"], capture_output=True, text=True).stdout)

    # CUDA 信息
    import torch
    print(f"CUDA available: {torch.cuda.is_available()}")
    print(f"CUDA version: {torch.version.cuda}")
    print(f"GPU: {torch.cuda.get_device_name(0)}")
```

## 常见错误消息

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| `FunctionTimeoutError` | 函数超过超时时间 | 增加 `timeout` 参数 |
| `ContainerMemoryExceeded` | OOM 被杀 | 增加 `memory` 参数 |
| `ImageBuilderError` | 构建失败 | 检查依赖，固定版本 |
| `ResourceExhausted` | 无可用 GPU | 使用 GPU 回退，稍后重试 |
| `AuthenticationError` | 无效令牌 | 运行 `modal token new` |
| `VolumeNotFound` | 卷不存在 | 使用 `create_if_missing=True` |
| `SecretNotFound` | 密钥不存在 | 通过 CLI 创建密钥 |

## 获取帮助

1. **文档**：https://modal.com/docs
2. **示例**：https://github.com/modal-labs/modal-examples
3. **Discord**：https://discord.gg/modal
4. **状态**：https://status.modal.com

### 报告问题

包含以下信息：
- Modal 客户端版本：`modal --version`
- Python 版本：`python --version`
- 完整错误回溯
- 最小可复现代码
- 相关的 GPU 类型
