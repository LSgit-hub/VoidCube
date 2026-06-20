# AudioCraft 故障排除指南

## 安装问题

### 导入错误

**错误**：`ModuleNotFoundError: No module named 'audiocraft'`

**解决方案**：
```bash
# 从 PyPI 安装
pip install audiocraft

# 或从 GitHub 安装
pip install git+https://github.com/facebookresearch/audiocraft.git

# 验证安装
python -c "from audiocraft.models import MusicGen; print('OK')"
```

### FFmpeg 未找到

**错误**：`RuntimeError: ffmpeg not found`

**解决方案**：
```bash
# Ubuntu/Debian
sudo apt-get install ffmpeg

# macOS
brew install ffmpeg

# Windows（使用 conda）
conda install -c conda-forge ffmpeg

# 验证
ffmpeg -version
```

### PyTorch CUDA 不匹配

**错误**：`RuntimeError: CUDA error: no kernel image is available`

**解决方案**：
```bash
# 检查 CUDA 版本
nvcc --version
python -c "import torch; print(torch.version.cuda)"

# 安装匹配的 PyTorch
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121

# 对于 CUDA 11.8
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
```

### xformers 问题

**错误**：`ImportError: xformers` 相关错误

**解决方案**：
```bash
# 安装 xformers 以提高内存效率
pip install xformers

# 或禁用 xformers
export AUDIOCRAFT_USE_XFORMERS=0

# 在 Python 中
import os
os.environ["AUDIOCRAFT_USE_XFORMERS"] = "0"
from audiocraft.models import MusicGen
```

## 模型加载问题

### 加载时内存不足

**错误**：模型加载时 `torch.cuda.OutOfMemoryError`

**解决方案**：
```python
# 使用更小的模型
model = MusicGen.get_pretrained('facebook/musicgen-small')

# 先强制 CPU 加载
import torch
device = "cpu"
model = MusicGen.get_pretrained('facebook/musicgen-small', device=device)
model = model.to("cuda")

# 使用 HuggingFace 的 device_map
from transformers import MusicgenForConditionalGeneration
model = MusicgenForConditionalGeneration.from_pretrained(
    "facebook/musicgen-small",
    device_map="auto"
)
```

### 下载失败

**错误**：连接错误或下载不完整

**解决方案**：
```python
# 设置缓存目录
import os
os.environ["AUDIOCRAFT_CACHE_DIR"] = "/path/to/cache"

# 或对于 HuggingFace
os.environ["HF_HOME"] = "/path/to/hf_cache"

# 恢复下载
from huggingface_hub import snapshot_download
snapshot_download("facebook/musicgen-small", resume_download=True)

# 使用本地文件
model = MusicGen.get_pretrained('/local/path/to/model')
```

### 错误模型类型

**错误**：为任务加载了错误模型

**解决方案**：
```python
# 对于文本到音乐：使用 MusicGen
from audiocraft.models import MusicGen
model = MusicGen.get_pretrained('facebook/musicgen-medium')

# 对于文本到声音：使用 AudioGen
from audiocraft.models import AudioGen
model = AudioGen.get_pretrained('facebook/audiogen-medium')

# 对于旋律条件：使用旋律变体
model = MusicGen.get_pretrained('facebook/musicgen-melody')

# 对于立体声：使用立体声变体
model = MusicGen.get_pretrained('facebook/musicgen-stereo-medium')
```

## 生成问题

### 空或静音输出

**问题**：生成的音频是静音或非常安静

**解决方案**：
```python
import torch

# 检查输出
wav = model.generate(["upbeat music"])
print(f"Shape: {wav.shape}")
print(f"Max amplitude: {wav.abs().max().item()}")
print(f"Mean amplitude: {wav.abs().mean().item()}")

# 如果太安静，归一化
def normalize_audio(audio, target_db=-14.0):
    rms = torch.sqrt(torch.mean(audio ** 2))
    target_rms = 10 ** (target_db / 20)
    gain = target_rms / (rms + 1e-8)
    return audio * gain

wav_normalized = normalize_audio(wav)
```

### 质量差输出

**问题**：生成的音乐听起来不好或有噪音

**解决方案**：
```python
# 使用更大的模型
model = MusicGen.get_pretrained('facebook/musicgen-large')

# 调整生成参数
model.set_generation_params(
    duration=15,
    top_k=250,          # 增加以获得更多多样性
    temperature=0.8,    # 降低以获得更聚焦的输出
    cfg_coef=4.0        # 增加以获得更好的文本遵循
)

# 使用更好的提示
# 坏："music"
# 好："upbeat electronic dance music with synthesizers and punchy drums"

# 尝试多频带扩散
from audiocraft.models import MultiBandDiffusion
mbd = MultiBandDiffusion.get_mbd_musicgen()
tokens = model.generate_tokens(["prompt"])
wav = mbd.tokens_to_wav(tokens)
```

### 生成太短

**问题**：音频比预期短

**解决方案**：
```python
# 检查时长设置
model.set_generation_params(duration=30)  # 在生成前设置

# 在生成中验证
print(f"Duration setting: {model.generation_params}")

# 检查输出形状
wav = model.generate(["prompt"])
actual_duration = wav.shape[-1] / 32000
print(f"Actual duration: {actual_duration}s")

# 注意：最大时长通常为 30s
```

### 旋律条件失败

**错误**：旋律条件生成问题

**解决方案**：
```python
import torchaudio
from audiocraft.models import MusicGen

# 加载旋律模型（不是基础模型）
model = MusicGen.get_pretrained('facebook/musicgen-melody')

# 加载并准备旋律
melody, sr = torchaudio.load("melody.wav")

# 如果需要，重采样到模型采样率
if sr != 32000:
    resampler = torchaudio.transforms.Resample(sr, 32000)
    melody = resampler(melody)

# 确保形状正确 [batch, channels, samples]
if melody.dim() == 1:
    melody = melody.unsqueeze(0).unsqueeze(0)
elif melody.dim() == 2:
    melody = melody.unsqueeze(0)

# 将立体声转换为单声道
if melody.shape[1] > 1:
    melody = melody.mean(dim=1, keepdim=True)

# 使用旋律生成
model.set_generation_params(duration=min(melody.shape[-1] / 32000, 30))
wav = model.generate_with_chroma(["piano cover"], melody, 32000)
```

## 内存问题

### CUDA 内存不足

**错误**：`torch.cuda.OutOfMemoryError: CUDA out of memory`

**解决方案**：
```python
import torch

# 生成前清除缓存
torch.cuda.empty_cache()

# 使用更小的模型
model = MusicGen.get_pretrained('facebook/musicgen-small')

# 减少时长
model.set_generation_params(duration=10)  # 而不是 30

# 一次生成一个
for prompt in prompts:
    wav = model.generate([prompt])
    save_audio(wav)
    torch.cuda.empty_cache()

# 对于非常大的生成使用 CPU
model = MusicGen.get_pretrained('facebook/musicgen-small', device="cpu")
```

### 批量处理时内存泄漏

**问题**：内存随时间增长

**解决方案**：
```python
import gc
import torch

def generate_with_cleanup(model, prompts):
    results = []

    for prompt in prompts:
        with torch.no_grad():
            wav = model.generate([prompt])
            results.append(wav.cpu())

        # 清理
        del wav
        gc.collect()
        torch.cuda.empty_cache()

    return results

# 使用上下文管理器
with torch.inference_mode():
    wav = model.generate(["prompt"])
```

## 音频格式问题

### 错误采样率

**问题**：音频以错误速度播放

**解决方案**：
```python
import torchaudio

# MusicGen 输出为 32kHz
sample_rate = 32000

# AudioGen 输出为 16kHz
sample_rate = 16000

# 保存时始终使用正确速率
torchaudio.save("output.wav", wav[0].cpu(), sample_rate=sample_rate)

# 如需要则重采样
resampler = torchaudio.transforms.Resample(32000, 44100)
wav_resampled = resampler(wav)
```

### 立体声/单声道不匹配

**问题**：通道数错误

**解决方案**：
```python
# 检查模型类型
print(f"Audio channels: {wav.shape}")
# 单声道：[batch, 1, samples]
# 立体声：[batch, 2, samples]

# 将单声道转换为立体声
if wav.shape[1] == 1:
    wav_stereo = wav.repeat(1, 2, 1)

# 将立体声转换为单声道
if wav.shape[1] == 2:
    wav_mono = wav.mean(dim=1, keepdim=True)

# 使用立体声模型获得立体声输出
model = MusicGen.get_pretrained('facebook/musicgen-stereo-medium')
```

### 削波和失真

**问题**：音频有削波或失真

**解决方案**：
```python
import torch

# 检查削波
max_val = wav.abs().max().item()
print(f"Max amplitude: {max_val}")

# 归一化以防止削波
if max_val > 1.0:
    wav = wav / max_val

# 应用软削波
def soft_clip(x, threshold=0.9):
    return torch.tanh(x / threshold) * threshold

wav_clipped = soft_clip(wav)

# 生成时降低温度
model.set_generation_params(temperature=0.7)  # 更受控
```

## HuggingFace Transformers 问题

### 处理器错误

**错误**：MusicgenProcessor 问题

**解决方案**：
```python
from transformers import AutoProcessor, MusicgenForConditionalGeneration

# 加载匹配的处理器和模型
processor = AutoProcessor.from_pretrained("facebook/musicgen-small")
model = MusicgenForConditionalGeneration.from_pretrained("facebook/musicgen-small")

# 确保输入在同一设备上
inputs = processor(
    text=["prompt"],
    padding=True,
    return_tensors="pt"
).to("cuda")

# 检查处理器配置
print(processor.tokenizer)
print(processor.feature_extractor)
```

### 生成参数错误

**错误**：无效生成参数

**解决方案**：
```python
# HuggingFace 使用不同的参数名
audio_values = model.generate(
    **inputs,
    do_sample=True,           # 启用采样
    guidance_scale=3.0,       # CFG（不是 cfg_coef）
    max_new_tokens=256,       # Token 限制（不是 duration）
    temperature=1.0
)

# 从时长计算 token
# ~每秒 50 个 token
duration_seconds = 10
max_tokens = duration_seconds * 50
audio_values = model.generate(**inputs, max_new_tokens=max_tokens)
```

## 性能问题

### 生成慢

**问题**：生成时间太长

**解决方案**：
```python
# 使用更小的模型
model = MusicGen.get_pretrained('facebook/musicgen-small')

# 减少时长
model.set_generation_params(duration=10)

# 使用 GPU
model.to("cuda")

# 如果可用，启用 flash attention
# （需要兼容硬件）

# 批量多个提示
prompts = ["prompt1", "prompt2", "prompt3"]
wav = model.generate(prompts)  # 单批比循环更快

# 使用 compile（PyTorch 2.0+）
model.lm = torch.compile(model.lm)
```

### CPU 回退

**问题**：生成在 CPU 而非 GPU 上运行

**解决方案**：
```python
import torch

# 检查 CUDA 可用性
print(f"CUDA available: {torch.cuda.is_available()}")
print(f"CUDA device: {torch.cuda.get_device_name(0)}")

# 显式移动到 GPU
model = MusicGen.get_pretrained('facebook/musicgen-small')
model.to("cuda")

# 验证模型设备
print(f"Model device: {next(model.lm.parameters()).device}")
```

## 常见错误消息

| 错误 | 原因 | 解决方案 |
|-------|-------|----------|
| `CUDA out of memory` | 模型太大 | 使用更小模型，减少时长 |
| `ffmpeg not found` | FFmpeg 未安装 | 安装 FFmpeg |
| `No module named 'audiocraft'` | 未安装 | `pip install audiocraft` |
| `RuntimeError: Expected 3D tensor` | 输入形状错误 | 检查张量维度 |
| `KeyError: 'melody'` | 旋律模型错误 | 使用 musicgen-melody |
| `Sample rate mismatch` | 音频格式错误 | 重采样到模型速率 |

## 获取帮助

1. **GitHub Issues**：https://github.com/facebookresearch/audiocraft/issues
2. **HuggingFace 论坛**：https://discuss.huggingface.co
3. **论文**：https://arxiv.org/abs/2306.05284

### 报告问题

包括：
- Python 版本
- PyTorch 版本
- CUDA 版本
- AudioCraft 版本：`pip show audiocraft`
- 完整错误回溯
- 最小可复现代码
- 硬件（GPU 型号、VRAM）
