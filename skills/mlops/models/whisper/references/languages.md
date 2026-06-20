# Whisper 语言支持指南

Whisper 多语言能力完整指南。

## 支持的语言（共 99 种）

### 顶级支持（WER < 10%）

- 英语 (en)
- 西班牙语 (es)
- 法语 (fr)
- 德语 (de)
- 意大利语 (it)
- 葡萄牙语 (pt)
- 荷兰语 (nl)
- 波兰语 (pl)
- 俄语 (ru)
- 日语 (ja)
- 韩语 (ko)
- 中文 (zh)

### 良好支持（WER 10-20%）

- 阿拉伯语 (ar)
- 土耳其语 (tr)
- 越南语 (vi)
- 瑞典语 (sv)
- 芬兰语 (fi)
- 捷克语 (cs)
- 罗马尼亚语 (ro)
- 匈牙利语 (hu)
- 丹麦语 (da)
- 挪威语 (no)
- 泰语 (th)
- 希伯来语 (he)
- 希腊语 (el)
- 印尼语 (id)
- 马来语 (ms)

### 完整列表（99 种语言）

南非荷兰语、阿尔巴尼亚语、阿姆哈拉语、阿拉伯语、亚美尼亚语、阿萨姆语、阿塞拜疆语、巴什基尔语、巴斯克语、白俄罗斯语、孟加拉语、波斯尼亚语、布列塔尼语、保加利亚语、缅甸语、粤语、加泰罗尼亚语、中文、克罗地亚语、捷克语、丹麦语、荷兰语、英语、爱沙尼亚语、法罗语、芬兰语、法语、加利西亚语、格鲁吉亚语、德语、希腊语、古吉拉特语、海地克里奥尔语、豪萨语、夏威夷语、希伯来语、印地语、匈牙利语、冰岛语、印尼语、意大利语、日语、爪哇语、卡纳达语、哈萨克语、高棉语、韩语、老挝语、拉丁语、拉脱维亚语、林加拉语、立陶宛语、卢森堡语、马其顿语、马达加斯加语、马来语、马拉雅拉姆语、马耳他语、毛利语、马拉地语、摩尔多瓦语、蒙古语、缅甸语、尼泊尔语、挪威语、新挪威语、奥克语、普什图语、波斯语、波兰语、葡萄牙语、旁遮普语、普什图语、罗马尼亚语、俄语、梵语、塞尔维亚语、绍纳语、信德语、僧伽罗语、斯洛伐克语、斯洛文尼亚语、索马里语、西班牙语、巽他语、斯瓦希里语、瑞典语、他加禄语、塔吉克语、泰米尔语、鞑靼语、泰卢固语、泰语、藏语、土耳其语、土库曼语、乌克兰语、乌尔都语、乌兹别克语、越南语、威尔士语、意第绪语、约鲁巴语

## 使用示例

### 自动检测语言

```python
import whisper

model = whisper.load_model("turbo")

# 自动检测语言
result = model.transcribe("audio.mp3")

print(f"Detected language: {result['language']}")
print(f"Text: {result['text']}")
```

### 指定语言（更快）

```python
# 指定语言以加快转录
result = model.transcribe("audio.mp3", language="es")  # 西班牙语
result = model.transcribe("audio.mp3", language="fr")  # 法语
result = model.transcribe("audio.mp3", language="ja")  # 日语
```

### 翻译为英语

```python
# 将任何语言翻译为英语
result = model.transcribe(
    "spanish_audio.mp3",
    task="translate"  # 翻译为英语
)

print(f"Original language: {result['language']}")
print(f"English translation: {result['text']}")
```

## 语言特定技巧

### 中文

```python
# 中文使用大模型效果更好
model = whisper.load_model("large")

result = model.transcribe(
    "chinese_audio.mp3",
    language="zh",
    initial_prompt="这是一段关于技术的讨论"  # 上下文有帮助
)
```

### 日语

```python
# 日语受益于初始提示
result = model.transcribe(
    "japanese_audio.mp3",
    language="ja",
    initial_prompt="これは技術的な会議の録音です"
)
```

### 阿拉伯语

```python
# 阿拉伯语：使用大模型获得最佳结果
model = whisper.load_model("large")

result = model.transcribe(
    "arabic_audio.mp3",
    language="ar"
)
```

## 模型大小建议

| 语言层级 | 推荐模型 | WER |
|---------------|-------------------|-----|
| 顶级 (en, es, fr, de) | base/turbo | < 10% |
| 良好 (ar, tr, vi) | medium/large | 10-20% |
| 低资源 | large | 20-30% |

## 各语言性能

### 英语

- **tiny**: WER ~15%
- **base**: WER ~8%
- **small**: WER ~5%
- **medium**: WER ~4%
- **large**: WER ~3%
- **turbo**: WER ~3.5%

### 西班牙语

- **tiny**: WER ~20%
- **base**: WER ~12%
- **medium**: WER ~6%
- **large**: WER ~4%

### 中文

- **small**: WER ~15%
- **medium**: WER ~8%
- **large**: WER ~5%

## 最佳实践

1. **使用仅英语模型** - 对小模型（tiny/base）更好
2. **指定语言** - 比自动检测更快
3. **添加初始提示** - 提高技术术语准确性
4. **使用更大模型** - 用于低资源语言
5. **在样本上测试** - 质量因口音/方言而异
6. **考虑音频质量** - 清晰音频 = 更好结果
7. **检查语言代码** - 使用 ISO 639-1 代码（2 个字母）

## 语言检测

```python
# 仅检测语言（不转录）
import whisper

model = whisper.load_model("base")

# 加载音频
audio = whisper.load_audio("audio.mp3")
audio = whisper.pad_or_trim(audio)

# 制作 log-Mel 频谱图
mel = whisper.log_mel_spectrogram(audio).to(model.device)

# 检测语言
_, probs = model.detect_language(mel)
detected_language = max(probs, key=probs.get)

print(f"Detected language: {detected_language}")
print(f"Confidence: {probs[detected_language]:.2%}")
```

## 资源

- **论文**：https://arxiv.org/abs/2212.04356
- **GitHub**：https://github.com/openai/whisper
- **模型卡**：https://github.com/openai/whisper/blob/main/model-card.md
