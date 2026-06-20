# Stable Diffusion 高级用法指南

## 自定义流水线

### 从组件构建

```python
from diffusers import (
    UNet2DConditionModel,
    AutoencoderKL,
    DDPMScheduler,
    StableDiffusionPipeline
)
from transformers import CLIPTextModel, CLIPTokenizer
import torch

# 单独加载组件
unet = UNet2DConditionModel.from_pretrained(
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
    subfolder="unet"
)
vae = AutoencoderKL.from_pretrained(
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
    subfolder="vae"
)
text_encoder = CLIPTextModel.from_pretrained(
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
    subfolder="text_encoder"
)
tokenizer = CLIPTokenizer.from_pretrained(
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
    subfolder="tokenizer"
)
scheduler = DDPMScheduler.from_pretrained(
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
    subfolder="scheduler"
)

# 组装流水线
pipe = StableDiffusionPipeline(
    unet=unet,
    vae=vae,
    text_encoder=text_encoder,
    tokenizer=tokenizer,
    scheduler=scheduler,
    safety_checker=None,
    feature_extractor=None,
    requires_safety_checker=False
)
```

### 自定义去噪循环

```python
from diffusers import DDIMScheduler, AutoencoderKL, UNet2DConditionModel
from transformers import CLIPTextModel, CLIPTokenizer
import torch

def custom_generate(
    prompt: str,
    num_steps: int = 50,
    guidance_scale: float = 7.5,
    height: int = 512,
    width: int = 512
):
    # 加载组件
    tokenizer = CLIPTokenizer.from_pretrained("openai/clip-vit-large-patch14")
    text_encoder = CLIPTextModel.from_pretrained("openai/clip-vit-large-patch14")
    unet = UNet2DConditionModel.from_pretrained("sd-model", subfolder="unet")
    vae = AutoencoderKL.from_pretrained("sd-model", subfolder="vae")
    scheduler = DDIMScheduler.from_pretrained("sd-model", subfolder="scheduler")

    device = "cuda"
    text_encoder.to(device)
    unet.to(device)
    vae.to(device)

    # 编码提示
    text_input = tokenizer(
        prompt,
        padding="max_length",
        max_length=77,
        truncation=True,
        return_tensors="pt"
    )
    text_embeddings = text_encoder(text_input.input_ids.to(device))[0]

    # 无条件嵌入用于无分类器引导
    uncond_input = tokenizer(
        "",
        padding="max_length",
        max_length=77,
        return_tensors="pt"
    )
    uncond_embeddings = text_encoder(uncond_input.input_ids.to(device))[0]

    # 连接用于批处理
    text_embeddings = torch.cat([uncond_embeddings, text_embeddings])

    # 初始化潜变量
    latents = torch.randn(
        (1, 4, height // 8, width // 8),
        device=device
    )
    latents = latents * scheduler.init_noise_sigma

    # 去噪循环
    scheduler.set_timesteps(num_steps)
    for t in scheduler.timesteps:
        latent_model_input = torch.cat([latents] * 2)
        latent_model_input = scheduler.scale_model_input(latent_model_input, t)

        # 预测噪声
        with torch.no_grad():
            noise_pred = unet(
                latent_model_input,
                t,
                encoder_hidden_states=text_embeddings
            ).sample

        # 无分类器引导
        noise_pred_uncond, noise_pred_cond = noise_pred.chunk(2)
        noise_pred = noise_pred_uncond + guidance_scale * (
            noise_pred_cond - noise_pred_uncond
        )

        # 更新潜变量
        latents = scheduler.step(noise_pred, t, latents).prev_sample

    # 解码潜变量
    latents = latents / vae.config.scaling_factor
    with torch.no_grad():
        image = vae.decode(latents).sample

    # 转换为 PIL
    image = (image / 2 + 0.5).clamp(0, 1)
    image = image.cpu().permute(0, 2, 3, 1).numpy()
    image = (image * 255).round().astype("uint8")[0]

    return Image.fromarray(image)
```

## IP-Adapter

使用图像提示与文本一起：

```python
from diffusers import StableDiffusionPipeline
from diffusers.utils import load_image
import torch

pipe = StableDiffusionPipeline.from_pretrained(
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
    torch_dtype=torch.float16
).to("cuda")

# 加载 IP-Adapter
pipe.load_ip_adapter(
    "h94/IP-Adapter",
    subfolder="models",
    weight_name="ip-adapter_sd15.bin"
)

# 设置 IP-Adapter 缩放
pipe.set_ip_adapter_scale(0.6)

# 加载参考图像
ip_image = load_image("reference_style.jpg")

# 使用图像 + 文本提示生成
image = pipe(
    prompt="A portrait in a garden",
    ip_adapter_image=ip_image,
    num_inference_steps=50
).images[0]
```

### 多个 IP-Adapter 图像

```python
# 使用多个参考图像
pipe.set_ip_adapter_scale([0.5, 0.7])

images = [
    load_image("style_reference.jpg"),
    load_image("composition_reference.jpg")
]

result = pipe(
    prompt="A landscape painting",
    ip_adapter_image=images,
    num_inference_steps=50
).images[0]
```

## SDXL Refiner

两阶段生成以获得更高质量：

```python
from diffusers import StableDiffusionXLPipeline, StableDiffusionXLImg2ImgPipeline
import torch

# 加载基础模型
base = StableDiffusionXLPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    torch_dtype=torch.float16,
    variant="fp16"
).to("cuda")

# 加载细化器
refiner = StableDiffusionXLImg2ImgPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-refiner-1.0",
    torch_dtype=torch.float16,
    variant="fp16"
).to("cuda")

# 使用基础生成（部分去噪）
image = base(
    prompt="A majestic eagle soaring over mountains",
    num_inference_steps=40,
    denoising_end=0.8,
    output_type="latent"
).images

# 使用细化器细化
refined = refiner(
    prompt="A majestic eagle soaring over mountains",
    image=image,
    num_inference_steps=40,
    denoising_start=0.8
).images[0]
```

## T2I-Adapter

轻量级条件化，无需完整 ControlNet：

```python
from diffusers import StableDiffusionXLAdapterPipeline, T2IAdapter
import torch

# 加载适配器
adapter = T2IAdapter.from_pretrained(
    "TencentARC/t2i-adapter-canny-sdxl-1.0",
    torch_dtype=torch.float16
)

pipe = StableDiffusionXLAdapterPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    adapter=adapter,
    torch_dtype=torch.float16
).to("cuda")

# 获取 canny 边缘
canny_image = get_canny_image(input_image)

image = pipe(
    prompt="A colorful anime character",
    image=canny_image,
    num_inference_steps=30,
    adapter_conditioning_scale=0.8
).images[0]
```

## 使用 DreamBooth 微调

在自定义主体上训练：

```python
from diffusers import StableDiffusionPipeline, DDPMScheduler
from diffusers.optimization import get_scheduler
import torch
from torch.utils.data import Dataset, DataLoader
from PIL import Image
import os

class DreamBoothDataset(Dataset):
    def __init__(self, instance_images_path, instance_prompt, tokenizer, size=512):
        self.instance_images_path = instance_images_path
        self.instance_prompt = instance_prompt
        self.tokenizer = tokenizer
        self.size = size

        self.instance_images = [
            os.path.join(instance_images_path, f)
            for f in os.listdir(instance_images_path)
            if f.endswith(('.png', '.jpg', '.jpeg'))
        ]

    def __len__(self):
        return len(self.instance_images)

    def __getitem__(self, idx):
        image = Image.open(self.instance_images[idx]).convert("RGB")
        image = image.resize((self.size, self.size))
        image = torch.tensor(np.array(image)).permute(2, 0, 1) / 127.5 - 1.0

        tokens = self.tokenizer(
            self.instance_prompt,
            padding="max_length",
            max_length=77,
            truncation=True,
            return_tensors="pt"
        )

        return {"image": image, "input_ids": tokens.input_ids.squeeze()}

def train_dreambooth(
    pretrained_model: str,
    instance_data_dir: str,
    instance_prompt: str,
    output_dir: str,
    learning_rate: float = 5e-6,
    max_train_steps: int = 800,
    train_batch_size: int = 1
):
    # 加载流水线
    pipe = StableDiffusionPipeline.from_pretrained(pretrained_model)

    unet = pipe.unet
    vae = pipe.vae
    text_encoder = pipe.text_encoder
    tokenizer = pipe.tokenizer
    noise_scheduler = DDPMScheduler.from_pretrained(pretrained_model, subfolder="scheduler")

    # 冻结 VAE 和文本编码器
    vae.requires_grad_(False)
    text_encoder.requires_grad_(False)

    # 创建数据集
    dataset = DreamBoothDataset(
        instance_data_dir, instance_prompt, tokenizer
    )
    dataloader = DataLoader(dataset, batch_size=train_batch_size, shuffle=True)

    # 设置优化器
    optimizer = torch.optim.AdamW(unet.parameters(), lr=learning_rate)
    lr_scheduler = get_scheduler(
        "constant",
        optimizer=optimizer,
        num_warmup_steps=0,
        num_training_steps=max_train_steps
    )

    # 训练循环
    unet.train()
    device = "cuda"
    unet.to(device)
    vae.to(device)
    text_encoder.to(device)

    global_step = 0
    for epoch in range(max_train_steps // len(dataloader) + 1):
        for batch in dataloader:
            if global_step >= max_train_steps:
                break

            # 将图像编码为潜变量
            latents = vae.encode(batch["image"].to(device)).latent_dist.sample()
            latents = latents * vae.config.scaling_factor

            # 采样噪声
            noise = torch.randn_like(latents)
            timesteps = torch.randint(0, noise_scheduler.num_train_timesteps, (latents.shape[0],))
            timesteps = timesteps.to(device)

            # 添加噪声
            noisy_latents = noise_scheduler.add_noise(latents, noise, timesteps)

            # 获取文本嵌入
            encoder_hidden_states = text_encoder(batch["input_ids"].to(device))[0]

            # 预测噪声
            noise_pred = unet(noisy_latents, timesteps, encoder_hidden_states).sample

            # 计算损失
            loss = torch.nn.functional.mse_loss(noise_pred, noise)

            # 反向传播
            loss.backward()
            optimizer.step()
            lr_scheduler.step()
            optimizer.zero_grad()

            global_step += 1

            if global_step % 100 == 0:
                print(f"Step {global_step}, Loss: {loss.item():.4f}")

    # 保存模型
    pipe.unet = unet
    pipe.save_pretrained(output_dir)
```

## LoRA 训练

使用低秩适应进行高效微调：

```python
from peft import LoraConfig, get_peft_model
from diffusers import StableDiffusionPipeline
import torch

def train_lora(
    base_model: str,
    train_dataset,
    output_dir: str,
    lora_rank: int = 4,
    learning_rate: float = 1e-4,
    max_train_steps: int = 1000
):
    pipe = StableDiffusionPipeline.from_pretrained(base_model)
    unet = pipe.unet

    # 配置 LoRA
    lora_config = LoraConfig(
        r=lora_rank,
        lora_alpha=lora_rank,
        target_modules=["to_q", "to_v", "to_k", "to_out.0"],
        lora_dropout=0.1
    )

    # 将 LoRA 应用于 UNet
    unet = get_peft_model(unet, lora_config)
    unet.print_trainable_parameters()  # 显示约 0.1% 可训练

    # 训练（类似于 DreamBooth 但仅 LoRA 参数）
    optimizer = torch.optim.AdamW(
        unet.parameters(),
        lr=learning_rate
    )

    # ... 训练循环 ...

    # 仅保存 LoRA 权重
    unet.save_pretrained(output_dir)
```

## 文本反转

通过嵌入学习新概念：

```python
from diffusers import StableDiffusionPipeline
import torch

# 加载带文本反转
pipe = StableDiffusionPipeline.from_pretrained(
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
    torch_dtype=torch.float16
).to("cuda")

# 加载学习的嵌入
pipe.load_textual_inversion(
    "sd-concepts-library/cat-toy",
    token="<cat-toy>"
)

# 在提示中使用
image = pipe("A photo of <cat-toy> on a beach").images[0]
```

## 量化

使用量化减少内存：

```python
from diffusers import BitsAndBytesConfig, StableDiffusionXLPipeline
import torch

# 8 位量化
quantization_config = BitsAndBytesConfig(load_in_8bit=True)

pipe = StableDiffusionXLPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    quantization_config=quantization_config,
    torch_dtype=torch.float16
)
```

### NF4 量化（4 位）

```python
quantization_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.float16
)

pipe = StableDiffusionXLPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    quantization_config=quantization_config
)
```

## 生产部署

### FastAPI 服务器

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from diffusers import DiffusionPipeline
import torch
import base64
from io import BytesIO

app = FastAPI()

# 启动时加载模型
pipe = DiffusionPipeline.from_pretrained(
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
    torch_dtype=torch.float16
).to("cuda")
pipe.enable_model_cpu_offload()

class GenerationRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    num_inference_steps: int = 30
    guidance_scale: float = 7.5
    width: int = 512
    height: int = 512
    seed: int = None

class GenerationResponse(BaseModel):
    image_base64: str
    seed: int

@app.post("/generate", response_model=GenerationResponse)
async def generate(request: GenerationRequest):
    try:
        generator = None
        seed = request.seed or torch.randint(0, 2**32, (1,)).item()
        generator = torch.Generator("cuda").manual_seed(seed)

        image = pipe(
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            num_inference_steps=request.num_inference_steps,
            guidance_scale=request.guidance_scale,
            width=request.width,
            height=request.height,
            generator=generator
        ).images[0]

        # 转换为 base64
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        image_base64 = base64.b64encode(buffer.getvalue()).decode()

        return GenerationResponse(image_base64=image_base64, seed=seed)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "healthy"}
```

### Docker 部署

```dockerfile
FROM nvidia/cuda:12.1-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y python3 python3-pip

WORKDIR /app

COPY requirements.txt .
RUN pip3 install -r requirements.txt

COPY . .

# 预下载模型
RUN python3 -c "from diffusers import DiffusionPipeline; DiffusionPipeline.from_pretrained('stable-diffusion-v1-5/stable-diffusion-v1-5')"

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Kubernetes 部署

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: stable-diffusion
spec:
  replicas: 2
  selector:
    matchLabels:
      app: stable-diffusion
  template:
    metadata:
      labels:
        app: stable-diffusion
    spec:
      containers:
      - name: sd
        image: your-registry/stable-diffusion:latest
        ports:
        - containerPort: 8000
        resources:
          limits:
            nvidia.com/gpu: 1
            memory: "16Gi"
          requests:
            nvidia.com/gpu: 1
            memory: "8Gi"
        env:
        - name: TRANSFORMERS_CACHE
          value: "/cache/huggingface"
        volumeMounts:
        - name: model-cache
          mountPath: /cache
      volumes:
      - name: model-cache
        persistentVolumeClaim:
          claimName: model-cache-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: stable-diffusion
spec:
  selector:
    app: stable-diffusion
  ports:
  - port: 80
    targetPort: 8000
  type: LoadBalancer
```

## 回调系统

监控和修改生成：

```python
from diffusers import StableDiffusionPipeline
from diffusers.callbacks import PipelineCallback
import torch

class ProgressCallback(PipelineCallback):
    def __init__(self):
        self.progress = []

    def callback_fn(self, pipe, step_index, timestep, callback_kwargs):
        self.progress.append({
            "step": step_index,
            "timestep": timestep.item()
        })

        # 可选地修改潜变量
        latents = callback_kwargs["latents"]

        return callback_kwargs

# 使用回调
callback = ProgressCallback()

image = pipe(
    prompt="A sunset",
    callback_on_step_end=callback.callback_fn,
    callback_on_step_end_tensor_inputs=["latents"]
).images[0]

print(f"Generation completed in {len(callback.progress)} steps")
```

### 提前停止

```python
def early_stop_callback(pipe, step_index, timestep, callback_kwargs):
    # 20 步后停止
    if step_index >= 20:
        pipe._interrupt = True
    return callback_kwargs

image = pipe(
    prompt="A landscape",
    num_inference_steps=50,
    callback_on_step_end=early_stop_callback
).images[0]
```

## 多 GPU 推理

### 设备映射自动

```python
from diffusers import StableDiffusionXLPipeline

pipe = StableDiffusionXLPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    device_map="auto",  # 自动跨 GPU 分配
    torch_dtype=torch.float16
)
```

### 手动分配

```python
from accelerate import infer_auto_device_map, dispatch_model

# 创建设备映射
device_map = infer_auto_device_map(
    pipe.unet,
    max_memory={0: "10GiB", 1: "10GiB"}
)

# 分配模型
pipe.unet = dispatch_model(pipe.unet, device_map=device_map)
```
