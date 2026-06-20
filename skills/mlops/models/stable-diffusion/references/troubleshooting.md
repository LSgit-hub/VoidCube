# Stable Diffusion 故障排除指南

## 安装问题

### 包冲突

**错误**：`ImportError: cannot import name 'cached_download' from 'huggingface_hub'`

**修复**：
```bash
# 更新 huggingface_hub
pip install --upgrade huggingface_hub

# 重新安装 diffusers
pip install --upgrade diffusers
```

### xFormers 安装失败

**错误**：`RuntimeError: CUDA error: no kernel image is available for execution`

**修复**：
```bash
# 检查 CUDA 版本
nvcc --version

# 安装匹配的 xformers
pip install xformers --index-url https://download.pytorch.org/whl/cu121  # 对于 CUDA 12.1

# 或从源码构建
pip install -v -U git+https://github.com/facebookresearch/xformers.git@main#egg=xformers
```

### Torch/CUDA 不匹配

**错误**：`RuntimeError: CUDA error: CUBLAS_STATUS_NOT_INITIALIZED`

**修复**：
```bash
# 检查版本
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"

# 使用正确的 CUDA 重新安装 PyTorch
pip uninstall torch torchvision
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

## 内存问题

### CUDA 内存不足

**错误**：`torch.cuda.OutOfMemoryError: CUDA out of memory`

**解决方案**：

```python
# 解决方案 1：启用 CPU 卸载
pipe.enable_model_cpu_offload()

# 解决方案 2：顺序 CPU 卸载（更激进）
pipe.enable_sequential_cpu_offload()

# 解决方案 3：注意力切片
pipe.enable_attention_slicing()

# 解决方案 4：大图像的 VAE 切片
pipe.enable_vae_slicing()

# 解决方案 5：使用更低精度
pipe = DiffusionPipeline.from_pretrained(
    "model-id",
    torch_dtype=torch.float16  # 或 torch.bfloat16
)

# 解决方案 6：减少批量大小
image = pipe(prompt, num_images_per_prompt=1).images[0]

# 解决方案 7：生成更小图像
image = pipe(prompt, height=512, width=512).images[0]

# 解决方案 8：在生成之间清除缓存
import gc
torch.cuda.empty_cache()
gc.collect()
```

### 内存随时间增长

**问题**：每次生成内存使用增加

**修复**：
```python
import gc
import torch

def generate_with_cleanup(pipe, prompt, **kwargs):
    try:
        image = pipe(prompt, **kwargs).images[0]
        return image
    finally:
        # 生成后清除缓存
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()
```

### 大模型加载失败

**错误**：`RuntimeError: Unable to load model weights`

**修复**：
```python
# 使用低 CPU 内存模式
pipe = DiffusionPipeline.from_pretrained(
    "large-model-id",
    low_cpu_mem_usage=True,
    torch_dtype=torch.float16
)
```

## 生成问题

### 黑色图像

**问题**：输出图像完全黑色

**解决方案**：
```python
# 解决方案 1：禁用安全检查器
pipe.safety_checker = None

# 解决方案 2：检查 VAE 缩放
# 问题可能在 VAE 编码/解码
latents = latents / pipe.vae.config.scaling_factor  # 解码前

# 解决方案 3：确保正确的 dtype
pipe = pipe.to(dtype=torch.float16)
pipe.vae = pipe.vae.to(dtype=torch.float32)  # VAE 通常需要 fp32

# 解决方案 4：检查引导缩放
# 太高会导致问题
image = pipe(prompt, guidance_scale=7.5).images[0]  # 不是 20+
```

### 噪声/静态图像

**问题**：输出看起来像随机噪声

**解决方案**：
```python
# 解决方案 1：增加推理步数
image = pipe(prompt, num_inference_steps=50).images[0]

# 解决方案 2：检查调度器配置
pipe.scheduler = pipe.scheduler.from_config(pipe.scheduler.config)

# 解决方案 3：验证模型正确加载
print(pipe.unet)  # 应该显示模型架构
```

### 模糊图像

**问题**：输出图像质量低或模糊

**解决方案**：
```python
# 解决方案 1：使用更多步数
image = pipe(prompt, num_inference_steps=50).images[0]

# 解决方案 2：使用更好的 VAE
from diffusers import AutoencoderKL
vae = AutoencoderKL.from_pretrained("stabilityai/sd-vae-ft-mse")
pipe.vae = vae

# 解决方案 3：使用 SDXL 或细化器
pipe = DiffusionPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0"
)

# 解决方案 4：用 img2img 放大
upscale_pipe = StableDiffusionImg2ImgPipeline.from_pretrained(...)
upscaled = upscale_pipe(
    prompt=prompt,
    image=image.resize((1024, 1024)),
    strength=0.3
).images[0]
```

### 提示未被遵循

**问题**：生成的图像与提示不匹配

**解决方案**：
```python
# 解决方案 1：增加引导缩放
image = pipe(prompt, guidance_scale=10.0).images[0]

# 解决方案 2：使用负面提示
image = pipe(
    prompt="A red car",
    negative_prompt="blue, green, yellow, wrong color",
    guidance_scale=7.5
).images[0]

# 解决方案 3：使用提示加权
# 强调重要词汇
prompt = "A (red:1.5) car on a street"

# 解决方案 4：使用更长、更详细的提示
prompt = """
A bright red sports car, ferrari style, parked on a city street,
photorealistic, high detail, 8k, professional photography
"""
```

### 扭曲的脸/手

**问题**：脸和手看起来变形

**解决方案**：
```python
# 解决方案 1：使用负面提示
negative_prompt = """
bad hands, bad anatomy, deformed, ugly, blurry,
extra fingers, mutated hands, poorly drawn hands,
poorly drawn face, mutation, deformed face
"""

# 解决方案 2：使用面部专用模型
# ADetailer 或类似后处理

# 解决方案 3：使用 ControlNet 控制姿势
# 加载姿势估计和条件生成

# 解决方案 4：修复问题区域
mask = create_face_mask(image)
fixed = inpaint_pipe(
    prompt="beautiful detailed face",
    image=image,
    mask_image=mask
).images[0]
```

## 调度器问题

### 调度器不兼容

**错误**：`ValueError: Scheduler ... is not compatible with pipeline`

**修复**：
```python
from diffusers import EulerDiscreteScheduler

# 从配置创建调度器
pipe.scheduler = EulerDiscreteScheduler.from_config(
    pipe.scheduler.config
)

# 检查兼容的调度器
print(pipe.scheduler.compatibles)
```

### 错误的步数

**问题**：相同步数模型生成不同质量

**修复**：
```python
# 显式重置时间步
pipe.scheduler.set_timesteps(num_inference_steps)

# 检查调度器的步数
print(len(pipe.scheduler.timesteps))
```

## LoRA 问题

### LoRA 权重未加载

**错误**：`RuntimeError: Error(s) in loading state_dict for UNet2DConditionModel`

**修复**：
```python
# 检查权重文件格式
# 应该是 .safetensors 或 .bin

# 使用正确的键前缀加载
pipe.load_lora_weights(
    "path/to/lora",
    weight_name="lora.safetensors"
)

# 尝试加载到特定组件
pipe.unet.load_attn_procs("path/to/lora")
```

### LoRA 不影响输出

**问题**：有无 LoRA 生成的图像看起来相同

**修复**：
```python
# 融合 LoRA 权重
pipe.fuse_lora(lora_scale=1.0)

# 或显式设置缩放
pipe.set_adapters(["lora_name"], adapter_weights=[1.0])

# 验证 LoRA 已加载
print(list(pipe.unet.attn_processors.keys()))
```

### 多个 LoRA 冲突

**问题**：多个 LoRA 产生伪影

**修复**：
```python
# 使用不同适配器名称加载
pipe.load_lora_weights("lora1", adapter_name="style")
pipe.load_lora_weights("lora2", adapter_name="subject")

# 平衡权重
pipe.set_adapters(
    ["style", "subject"],
    adapter_weights=[0.5, 0.5]  # 降低权重
)

# 或在加载前使用 LoRA 合并
# 离线以适当比例合并 LoRA
```

## ControlNet 问题

### ControlNet 未条件化

**问题**：ControlNet 对输出无影响

**修复**：
```python
# 检查控制图像格式
# 应该是 RGB，匹配生成大小
control_image = control_image.resize((512, 512))

# 增加条件化缩放
image = pipe(
    prompt=prompt,
    image=control_image,
    controlnet_conditioning_scale=1.0,  # 尝试 0.5-1.5
    num_inference_steps=30
).images[0]

# 验证 ControlNet 已加载
print(pipe.controlnet)
```

### 控制图像预处理

**修复**：
```python
from controlnet_aux import CannyDetector

# 正确预处理
canny = CannyDetector()
control_image = canny(input_image)

# 确保正确格式
control_image = control_image.convert("RGB")
control_image = control_image.resize((512, 512))
```

## Hub/下载问题

### 模型下载失败

**错误**：`requests.exceptions.ConnectionError`

**修复**：
```bash
# 设置更长超时
export HF_HUB_DOWNLOAD_TIMEOUT=600

# 使用镜像（如果可用）
export HF_ENDPOINT=https://hf-mirror.com

# 或手动下载
huggingface-cli download stable-diffusion-v1-5/stable-diffusion-v1-5
```

### 缓存问题

**错误**：`OSError: Can't load model from cache`

**修复**：
```bash
# 清除缓存
rm -rf ~/.cache/huggingface/hub

# 或设置不同缓存位置
export HF_HOME=/path/to/cache

# 强制重新下载
pipe = DiffusionPipeline.from_pretrained(
    "model-id",
    force_download=True
)
```

### 门控模型访问被拒绝

**错误**：`401 Client Error: Unauthorized`

**修复**：
```bash
# 登录 Hugging Face
huggingface-cli login

# 或使用 token
pipe = DiffusionPipeline.from_pretrained(
    "model-id",
    token="hf_xxxxx"
)

# 首先在 Hub 网站上接受模型许可
```

## 性能问题

### 生成慢

**问题**：生成时间太长

**解决方案**：
```python
# 解决方案 1：使用更快的调度器
from diffusers import DPMSolverMultistepScheduler
pipe.scheduler = DPMSolverMultistepScheduler.from_config(
    pipe.scheduler.config
)

# 解决方案 2：减少步数
image = pipe(prompt, num_inference_steps=20).images[0]

# 解决方案 3：使用 LCM
from diffusers import LCMScheduler
pipe.load_lora_weights("latent-consistency/lcm-lora-sdxl")
pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
image = pipe(prompt, num_inference_steps=4, guidance_scale=1.0).images[0]

# 解决方案 4：启用 xFormers
pipe.enable_xformers_memory_efficient_attention()

# 解决方案 5：编译模型
pipe.unet = torch.compile(pipe.unet, mode="reduce-overhead", fullgraph=True)
```

### 首次生成慢

**问题**：第一张图像耗时更长

**修复**：
```python
# 预热模型
_ = pipe("warmup", num_inference_steps=1)

# 然后运行实际生成
image = pipe(prompt, num_inference_steps=50).images[0]

# 编译以加速后续运行
pipe.unet = torch.compile(pipe.unet)
```

## 调试技巧

### 启用调试日志

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# 或针对特定模块
logging.getLogger("diffusers").setLevel(logging.DEBUG)
logging.getLogger("transformers").setLevel(logging.DEBUG)
```

### 检查模型组件

```python
# 打印流水线组件
print(pipe.components)

# 检查模型配置
print(pipe.unet.config)
print(pipe.vae.config)
print(pipe.scheduler.config)

# 验证设备放置
print(pipe.device)
for name, module in pipe.components.items():
    if hasattr(module, 'device'):
        print(f"{name}: {module.device}")
```

### 验证输入

```python
# 检查图像尺寸
print(f"Height: {height}, Width: {width}")
assert height % 8 == 0, "Height must be divisible by 8"
assert width % 8 == 0, "Width must be divisible by 8"

# 检查提示分词
tokens = pipe.tokenizer(prompt, return_tensors="pt")
print(f"Token count: {tokens.input_ids.shape[1]}")  # SD 最大 77
```

### 保存中间结果

```python
def save_latents_callback(pipe, step_index, timestep, callback_kwargs):
    latents = callback_kwargs["latents"]

    # 解码并保存中间结果
    with torch.no_grad():
        image = pipe.vae.decode(latents / pipe.vae.config.scaling_factor).sample
    image = (image / 2 + 0.5).clamp(0, 1)
    image = image.cpu().permute(0, 2, 3, 1).numpy()[0]
    Image.fromarray((image * 255).astype("uint8")).save(f"step_{step_index}.png")

    return callback_kwargs

image = pipe(
    prompt,
    callback_on_step_end=save_latents_callback,
    callback_on_step_end_tensor_inputs=["latents"]
).images[0]
```

## 获取帮助

1. **文档**：https://huggingface.co/docs/diffusers
2. **GitHub Issues**：https://github.com/huggingface/diffusers/issues
3. **Discord**：https://discord.gg/diffusers
4. **论坛**：https://discuss.huggingface.co

### 报告问题

包括：
- Diffusers 版本：`pip show diffusers`
- PyTorch 版本：`python -c "import torch; print(torch.__version__)"`
- CUDA 版本：`nvcc --version`
- GPU 型号：`nvidia-smi`
- 完整错误回溯
- 最小可复现代码
- 使用的模型名称/ID
