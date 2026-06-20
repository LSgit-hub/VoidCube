---
name: stable-diffusion-image-generation
description: 通过HuggingFace Diffusers使用Stable Diffusion模型进行最先进的文本到图像生成。当需要从文本提示生成图像、执行图像到图像转换、修复或构建自定义扩散流水线时使用。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [diffusers>=0.30.0, transformers>=4.41.0, accelerate>=0.31.0, torch>=2.0.0]
metadata:
  VoidCube:
    tags: [Image Generation, Stable Diffusion, Diffusers, Text-to-Image, Multimodal, Computer Vision]

---

# Stable Diffusion 图像生成

使用HuggingFace Diffusers库生成图像的综合指南。

## 何时使用Stable Diffusion

**使用Stable Diffusion的场景:**
- 从文本描述生成图像
- 执行图像到图像转换(风格迁移、增强)
- 修复(填充掩码区域)
- 外扩(扩展图像边界)
- 创建现有图像的变体
- 构建自定义图像生成工作流

**关键特性:**
- **文本到图像**: 从自然语言提示生成图像
- **图像到图像**: 使用文本引导转换现有图像
- **修复**: 用上下文感知内容填充掩码区域
- **ControlNet**: 添加空间条件(边缘、姿态、深度)
- **LoRA支持**: 高效微调和风格适配
- **多种模型**: 支持SD 1.5、SDXL、SD 3.0、Flux

**替代方案:**
- **DALL-E 3**: 用于无需GPU的API生成
- **Midjourney**: 用于艺术风格化输出
- **Imagen**: 用于Google Cloud集成
- **Leonardo.ai**: 用于基于Web的创意工作流

## 快速开始

### 安装

```bash
pip install diffusers transformers accelerate torch
pip install xformers  # 可选: 内存高效注意力
```

### 基本文本到图像

```python
from diffusers import DiffusionPipeline
import torch

# 加载流水线(自动检测模型类型)
pipe = DiffusionPipeline.from_pretrained(
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
    torch_dtype=torch.float16
)
pipe.to("cuda")

# 生成图像
image = pipe(
    "A serene mountain landscape at sunset, highly detailed",
    num_inference_steps=50,
    guidance_scale=7.5
).images[0]

image.save("output.png")
```

### 使用SDXL(更高质量)

```python
from diffusers import AutoPipelineForText2Image
import torch

pipe = AutoPipelineForText2Image.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    torch_dtype=torch.float16,
    variant="fp16"
)
pipe.to("cuda")

# 启用内存优化
pipe.enable_model_cpu_offload()

image = pipe(
    prompt="A futuristic city with flying cars, cinematic lighting",
    height=1024,
    width=1024,
    num_inference_steps=30
).images[0]
```

## 架构概览

### 三支柱设计

Diffusers围绕三个核心组件构建:

```
Pipeline (编排)
├── Model (神经网络)
│   ├── UNet / Transformer (噪声预测)
│   ├── VAE (潜空间编码/解码)
│   └── Text Encoder (CLIP/T5)
└── Scheduler (去噪算法)
```

### 流水线推理流程

```
文本提示 → 文本编码器 → 文本嵌入
                                    ↓
随机噪声 → [去噪循环] ← 调度器
                      ↓
               预测噪声
                      ↓
              VAE解码器 → 最终图像
```

## 核心概念

### 流水线

流水线编排完整的工作流:

| 流水线 | 用途 |
|----------|---------|
| `StableDiffusionPipeline` | 文本到图像(SD 1.x/2.x) |
| `StableDiffusionXLPipeline` | 文本到图像(SDXL) |
| `StableDiffusion3Pipeline` | 文本到图像(SD 3.0) |
| `FluxPipeline` | 文本到图像(Flux模型) |
| `StableDiffusionImg2ImgPipeline` | 图像到图像 |
| `StableDiffusionInpaintPipeline` | 修复 |

### 调度器

调度器控制去噪过程:

| 调度器 | 步数 | 质量 | 用例 |
|-----------|-------|---------|----------|
| `EulerDiscreteScheduler` | 20-50 | 好 | 默认选择 |
| `EulerAncestralDiscreteScheduler` | 20-50 | 好 | 更多变化 |
| `DPMSolverMultistepScheduler` | 15-25 | 优秀 | 快速,高质量 |
| `DDIMScheduler` | 50-100 | 好 | 确定性 |
| `LCMScheduler` | 4-8 | 好 | 非常快 |
| `UniPCMultistepScheduler` | 15-25 | 优秀 | 快速收敛 |

### 切换调度器

```python
from diffusers import DPMSolverMultistepScheduler

# 切换为更快的生成
pipe.scheduler = DPMSolverMultistepScheduler.from_config(
    pipe.scheduler.config
)

# 现在用更少步数生成
image = pipe(prompt, num_inference_steps=20).images[0]
```

## 生成参数

### 关键参数

| 参数 | 默认值 | 描述 |
|-----------|---------|-------------|
| `prompt` | 必需 | 期望图像的文本描述 |
| `negative_prompt` | None | 图像中要避免的内容 |
| `num_inference_steps` | 50 | 去噪步数(更多=更好质量) |
| `guidance_scale` | 7.5 | 提示遵循度(典型值7-12) |
| `height`, `width` | 512/1024 | 输出尺寸(8的倍数) |
| `generator` | None | Torch生成器用于可复现性 |
| `num_images_per_prompt` | 1 | 批大小 |

### 可复现生成

```python
import torch

generator = torch.Generator(device="cuda").manual_seed(42)

image = pipe(
    prompt="A cat wearing a top hat",
    generator=generator,
    num_inference_steps=50
).images[0]
```

### 负面提示

```python
image = pipe(
    prompt="Professional photo of a dog in a garden",
    negative_prompt="blurry, low quality, distorted, ugly, bad anatomy",
    guidance_scale=7.5
).images[0]
```

## 图像到图像

使用文本引导转换现有图像:

```python
from diffusers import AutoPipelineForImage2Image
from PIL import Image

pipe = AutoPipelineForImage2Image.from_pretrained(
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
    torch_dtype=torch.float16
).to("cuda")

init_image = Image.open("input.jpg").resize((512, 512))

image = pipe(
    prompt="A watercolor painting of the scene",
    image=init_image,
    strength=0.75,  # 转换程度(0-1)
    num_inference_steps=50
).images[0]
```

## 修复

填充掩码区域:

```python
from diffusers import AutoPipelineForInpainting
from PIL import Image

pipe = AutoPipelineForInpainting.from_pretrained(
    "runwayml/stable-diffusion-inpainting",
    torch_dtype=torch.float16
).to("cuda")

image = Image.open("photo.jpg")
mask = Image.open("mask.png")  # 白色 = 修复区域

result = pipe(
    prompt="A red car parked on the street",
    image=image,
    mask_image=mask,
    num_inference_steps=50
).images[0]
```

## ControlNet

添加空间条件以实现精确控制:

```python
from diffusers import StableDiffusionControlNetPipeline, ControlNetModel
import torch

# 加载用于边缘条件的ControlNet
controlnet = ControlNetModel.from_pretrained(
    "lllyasviel/control_v11p_sd15_canny",
    torch_dtype=torch.float16
)

pipe = StableDiffusionControlNetPipeline.from_pretrained(
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
    controlnet=controlnet,
    torch_dtype=torch.float16
).to("cuda")

# 使用Canny边缘图像作为控制
control_image = get_canny_image(input_image)

image = pipe(
    prompt="A beautiful house in the style of Van Gogh",
    image=control_image,
    num_inference_steps=30
).images[0]
```

### 可用ControlNet

| ControlNet | 输入类型 | 用例 |
|------------|------------|----------|
| `canny` | 边缘图 | 保留结构 |
| `openpose` | 姿态骨架 | 人体姿态 |
| `depth` | 深度图 | 3D感知生成 |
| `normal` | 法线图 | 表面细节 |
| `mlsd` | 线段 | 建筑线条 |
| `scribble` | 粗略草图 | 草图到图像 |

## LoRA适配器

加载微调的风格适配器:

```python
from diffusers import DiffusionPipeline

pipe = DiffusionPipeline.from_pretrained(
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
    torch_dtype=torch.float16
).to("cuda")

# 加载LoRA权重
pipe.load_lora_weights("path/to/lora", weight_name="style.safetensors")

# 使用LoRA风格生成
image = pipe("A portrait in the trained style").images[0]

# 调整LoRA强度
pipe.fuse_lora(lora_scale=0.8)

# 卸载LoRA
pipe.unload_lora_weights()
```

### 多个LoRA

```python
# 加载多个LoRA
pipe.load_lora_weights("lora1", adapter_name="style")
pipe.load_lora_weights("lora2", adapter_name="character")

# 为每个设置权重
pipe.set_adapters(["style", "character"], adapter_weights=[0.7, 0.5])

image = pipe("A portrait").images[0]
```

## 内存优化

### 启用CPU卸载

```python
# 模型CPU卸载 - 不使用时将模型移至CPU
pipe.enable_model_cpu_offload()

# 顺序CPU卸载 - 更激进,更慢
pipe.enable_sequential_cpu_offload()
```

### 注意力切片

```python
# 通过分块计算注意力减少内存
pipe.enable_attention_slicing()

# 或指定块大小
pipe.enable_attention_slicing("max")
```

### xFormers内存高效注意力

```python
# 需要xformers包
pipe.enable_xformers_memory_efficient_attention()
```

### 大图像VAE切片

```python
# 分块解码潜空间用于大图像
pipe.enable_vae_slicing()
pipe.enable_vae_tiling()
```

## 模型变体

### 加载不同精度

```python
# FP16 (推荐用于GPU)
pipe = DiffusionPipeline.from_pretrained(
    "model-id",
    torch_dtype=torch.float16,
    variant="fp16"
)

# BF16 (更好精度,需要Ampere+ GPU)
pipe = DiffusionPipeline.from_pretrained(
    "model-id",
    torch_dtype=torch.bfloat16
)
```

### 加载特定组件

```python
from diffusers import UNet2DConditionModel, AutoencoderKL

# 加载自定义VAE
vae = AutoencoderKL.from_pretrained("stabilityai/sd-vae-ft-mse")

# 与流水线一起使用
pipe = DiffusionPipeline.from_pretrained(
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
    vae=vae,
    torch_dtype=torch.float16
)
```

## 批量生成

高效生成多个图像:

```python
# 多个提示
prompts = [
    "A cat playing piano",
    "A dog reading a book",
    "A bird painting a picture"
]

images = pipe(prompts, num_inference_steps=30).images

# 每个提示多个图像
images = pipe(
    "A beautiful sunset",
    num_images_per_prompt=4,
    num_inference_steps=30
).images
```

## 常见工作流

### 工作流1: 高质量生成

```python
from diffusers import StableDiffusionXLPipeline, DPMSolverMultistepScheduler
import torch

# 1. 加载带优化的SDXL
pipe = StableDiffusionXLPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    torch_dtype=torch.float16,
    variant="fp16"
)
pipe.to("cuda")
pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)
pipe.enable_model_cpu_offload()

# 2. 使用质量设置生成
image = pipe(
    prompt="A majestic lion in the savanna, golden hour lighting, 8k, detailed fur",
    negative_prompt="blurry, low quality, cartoon, anime, sketch",
    num_inference_steps=30,
    guidance_scale=7.5,
    height=1024,
    width=1024
).images[0]
```

### 工作流2: 快速原型

```python
from diffusers import AutoPipelineForText2Image, LCMScheduler
import torch

# 使用LCM进行4-8步生成
pipe = AutoPipelineForText2Image.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    torch_dtype=torch.float16
).to("cuda")

# 加载LCM LoRA用于快速生成
pipe.load_lora_weights("latent-consistency/lcm-lora-sdxl")
pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
pipe.fuse_lora()

# 约1秒生成
image = pipe(
    "A beautiful landscape",
    num_inference_steps=4,
    guidance_scale=1.0
).images[0]
```

## 常见问题

**CUDA内存不足:**
```python
# 启用内存优化
pipe.enable_model_cpu_offload()
pipe.enable_attention_slicing()
pipe.enable_vae_slicing()

# 或使用更低精度
pipe = DiffusionPipeline.from_pretrained(model_id, torch_dtype=torch.float16)
```

**黑色/噪声图像:**
```python
# 检查VAE配置
# 如需要绕过安全检查器
pipe.safety_checker = None

# 确保正确的dtype一致性
pipe = pipe.to(dtype=torch.float16)
```

**生成慢:**
```python
# 使用更快的调度器
from diffusers import DPMSolverMultistepScheduler
pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)

# 减少步数
image = pipe(prompt, num_inference_steps=20).images[0]
```

## 参考

- **[高级用法](references/advanced-usage.md)** - 自定义流水线、微调、部署
- **[故障排除](references/troubleshooting.md)** - 常见问题和解决方案

## 资源

- **文档**: https://huggingface.co/docs/diffusers
- **仓库**: https://github.com/huggingface/diffusers
- **模型中心**: https://huggingface.co/models?library=diffusers
- **Discord**: https://discord.gg/diffusers
