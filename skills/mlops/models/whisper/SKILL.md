---
name: whisper
description: OpenAI的通用语音识别模型。支持99种语言、转录、翻译成英语和语言识别。从tiny(39M参数)到large(1550M参数)共六种模型尺寸。用于语音转文本、播客转录或多语言音频处理。最适合鲁棒的多语言ASR。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [openai-whisper, transformers, torch]
metadata:
  VoidCube:
    tags: [Whisper, Speech Recognition, ASR, Multimodal, Multilingual, OpenAI, Speech-To-Text, Transcription, Translation, Audio Processing]

---

# Whisper - 鲁棒语音识别

OpenAI的多语言语音识别模型。

## 何时使用Whisper

**使用场景:**
- 语音转文本转录(99种语言)
- 播客/视频转录
- 会议记录自动化
- 翻译成英语
- 噪声音频转录
- 多语言音频处理

**指标**:
- **72,900+ GitHub星标**
- 支持99种语言
- 在680,000小时音频上训练
- MIT许可证

**替代方案:**
- **AssemblyAI**: 托管API,说话人分离
- **Deepgram**: 实时流式ASR
- **Google Speech-to-Text**: 基于云

## 快速开始

### 安装

```bash
# 需要Python 3.8-3.11
pip install -U openai-whisper

# 需要ffmpeg
# macOS: brew install ffmpeg
# Ubuntu: sudo apt install ffmpeg
# Windows: choco install ffmpeg
```

### 基本转录

```python
import whisper

# 加载模型
model = whisper.load_model("base")

# 转录
result = model.transcribe("audio.mp3")

# 打印文本
print(result["text"])

# 访问片段
for segment in result["segments"]:
    print(f"[{segment['start']:.2f}s - {segment['end']:.2f}s] {segment['text']}")
```

## 模型尺寸

```python
# 可用模型
models = ["tiny", "base", "small", "medium", "large", "turbo"]

# 加载特定模型
model = whisper.load_model("turbo")  # 最快,质量好
```

| 模型 | 参数量 | 仅英语 | 多语言 | 速度 | VRAM |
|-------|------------|--------------|--------------|-------|------|
| tiny | 39M | ✓ | ✓ | ~32x | ~1 GB |
| base | 74M | ✓ | ✓ | ~16x | ~1 GB |
| small | 244M | ✓ | ✓ | ~6x | ~2 GB |
| medium | 769M | ✓ | ✓ | ~2x | ~5 GB |
| large | 1550M | ✗ | ✓ | 1x | ~10 GB |
| turbo | 809M | ✗ | ✓ | ~8x | ~6 GB |

**推荐**: 使用`turbo`获得最佳速度/质量,使用`base`进行原型开发

## 转录选项

### 语言指定

```python
# 自动检测语言
result = model.transcribe("audio.mp3")

# 指定语言(更快)
result = model.transcribe("audio.mp3", language="en")

# 支持: en, es, fr, de, it, pt, ru, ja, ko, zh等89种语言
```

### 任务选择

```python
# 转录(默认)
result = model.transcribe("audio.mp3", task="transcribe")

# 翻译成英语
result = model.transcribe("spanish.mp3", task="translate")
# 输入: 西班牙语音频 → 输出: 英语文本
```

### 初始提示

```python
# 使用上下文提高准确性
result = model.transcribe(
    "audio.mp3",
    initial_prompt="This is a technical podcast about machine learning and AI."
)

# 有助于:
# - 技术术语
# - 专有名词
# - 领域特定词汇
```

### 时间戳

```python
# 词级时间戳
result = model.transcribe("audio.mp3", word_timestamps=True)

for segment in result["segments"]:
    for word in segment["words"]:
        print(f"{word['word']} ({word['start']:.2f}s - {word['end']:.2f}s)")
```

### 温度回退

```python
# 如果置信度低,用不同温度重试
result = model.transcribe(
    "audio.mp3",
    temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
)
```

## 命令行使用

```bash
# 基本转录
whisper audio.mp3

# 指定模型
whisper audio.mp3 --model turbo

# 输出格式
whisper audio.mp3 --output_format txt     # 纯文本
whisper audio.mp3 --output_format srt     # 字幕
whisper audio.mp3 --output_format vtt     # WebVTT
whisper audio.mp3 --output_format json    # 带时间戳的JSON

# 语言
whisper audio.mp3 --language Spanish

# 翻译
whisper spanish.mp3 --task translate
```

## 批量处理

```python
import os

audio_files = ["file1.mp3", "file2.mp3", "file3.mp3"]

for audio_file in audio_files:
    print(f"正在转录 {audio_file}...")
    result = model.transcribe(audio_file)

    # 保存到文件
    output_file = audio_file.replace(".mp3", ".txt")
    with open(output_file, "w") as f:
        f.write(result["text"])
```

## 实时转录

```python
# 对于流式音频,使用faster-whisper
# pip install faster-whisper

from faster_whisper import WhisperModel

model = WhisperModel("base", device="cuda", compute_type="float16")

# 流式转录
segments, info = model.transcribe("audio.mp3", beam_size=5)

for segment in segments:
    print(f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}")
```

## GPU加速

```python
import whisper

# 如果可用自动使用GPU
model = whisper.load_model("turbo")

# 强制CPU
model = whisper.load_model("turbo", device="cpu")

# 强制GPU
model = whisper.load_model("turbo", device="cuda")

# GPU上快10-20倍
```

## 与其他工具集成

### 字幕生成

```bash
# 生成SRT字幕
whisper video.mp4 --output_format srt --language English

# 输出: video.srt
```

### 与LangChain集成

```python
from langchain.document_loaders import WhisperTranscriptionLoader

loader = WhisperTranscriptionLoader(file_path="audio.mp3")
docs = loader.load()

# 在RAG中使用转录
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

vectorstore = Chroma.from_documents(docs, OpenAIEmbeddings())
```

### 从视频提取音频

```bash
# 使用ffmpeg提取音频
ffmpeg -i video.mp4 -vn -acodec pcm_s16le audio.wav

# 然后转录
whisper audio.wav
```

## 最佳实践

1. **使用turbo模型** - 英语的最佳速度/质量
2. **指定语言** - 比自动检测更快
3. **添加初始提示** - 改善技术术语
4. **使用GPU** - 快10-20倍
5. **批量处理** - 更高效
6. **转换为WAV** - 更好的兼容性
7. **分割长音频** - <30分钟的分块
8. **检查语言支持** - 质量因语言而异
9. **使用faster-whisper** - 比openai-whisper快4倍
10. **监控VRAM** - 根据硬件调整模型大小

## 性能

| 模型 | 实时因子(CPU) | 实时因子(GPU) |
|-------|------------------------|------------------------|
| tiny | ~0.32 | ~0.01 |
| base | ~0.16 | ~0.01 |
| turbo | ~0.08 | ~0.01 |
| large | ~1.0 | ~0.05 |

*实时因子: 0.1 = 比实时快10倍*

## 语言支持

支持最好的语言:
- 英语 (en)
- 西班牙语 (es)
- 法语 (fr)
- 德语 (de)
- 意大利语 (it)
- 葡萄牙语 (pt)
- 俄语 (ru)
- 日语 (ja)
- 韩语 (ko)
- 中文 (zh)

完整列表: 共99种语言

## 局限性

1. **幻觉** - 可能重复或编造文本
2. **长格式准确性** - 在>30分钟音频上退化
3. **说话人识别** - 无分离
4. **口音** - 质量变化
5. **背景噪声** - 可能影响准确性
6. **实时延迟** - 不适合实时字幕

## 资源

- **GitHub**: https://github.com/openai/whisper ⭐ 72,900+
- **论文**: https://arxiv.org/abs/2212.04356
- **模型卡**: https://github.com/openai/whisper/blob/main/model-card.md
- **Colab**: 在仓库中可用
- **许可证**: MIT

