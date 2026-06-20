# CLIP 应用指南

CLIP 的实际应用和用例。

## 零样本图像分类

```python
import torch
import clip
from PIL import Image

model, preprocess = clip.load("ViT-B/32")

# 定义类别
categories = [
    "a photo of a dog",
    "a photo of a cat",
    "a photo of a bird",
    "a photo of a car",
    "a photo of a person"
]

# 准备图像
image = preprocess(Image.open("photo.jpg")).unsqueeze(0)
text = clip.tokenize(categories)

# 分类
with torch.no_grad():
    image_features = model.encode_image(image)
    text_features = model.encode_text(text)

    logits_per_image, _ = model(image, text)
    probs = logits_per_image.softmax(dim=-1).cpu().numpy()

# 打印结果
for category, prob in zip(categories, probs[0]):
    print(f"{category}: {prob:.2%}")
```

## 语义图像搜索

```python
# 索引图像
image_database = []
image_paths = ["img1.jpg", "img2.jpg", "img3.jpg"]

for img_path in image_paths:
    image = preprocess(Image.open(img_path)).unsqueeze(0)
    with torch.no_grad():
        features = model.encode_image(image)
        features /= features.norm(dim=-1, keepdim=True)
    image_database.append((img_path, features))

# 用文本搜索
query = "a sunset over mountains"
text_input = clip.tokenize([query])

with torch.no_grad():
    text_features = model.encode_text(text_input)
    text_features /= text_features.norm(dim=-1, keepdim=True)

# 查找匹配
similarities = []
for img_path, img_features in image_database:
    similarity = (text_features @ img_features.T).item()
    similarities.append((img_path, similarity))

# 按相似度排序
similarities.sort(key=lambda x: x[1], reverse=True)
for img_path, score in similarities[:3]:
    print(f"{img_path}: {score:.3f}")
```

## 内容审核

```python
# 定义安全类别
categories = [
    "safe for work content",
    "not safe for work content",
    "violent or graphic content",
    "hate speech or offensive content",
    "spam or misleading content"
]

text = clip.tokenize(categories)

# 检查图像
with torch.no_grad():
    logits, _ = model(image, text)
    probs = logits.softmax(dim=-1)

# 获取分类
max_idx = probs.argmax().item()
confidence = probs[0, max_idx].item()

if confidence > 0.7:
    print(f"Classified as: {categories[max_idx]} ({confidence:.2%})")
else:
    print(f"Uncertain classification (confidence: {confidence:.2%})")
```

## 图像到文本检索

```python
# 文本数据库
captions = [
    "A beautiful sunset over the ocean",
    "A cute dog playing in the park",
    "A modern city skyline at night",
    "A delicious pizza with toppings"
]

# 编码标题
caption_features = []
for caption in captions:
    text = clip.tokenize([caption])
    with torch.no_grad():
        features = model.encode_text(text)
        features /= features.norm(dim=-1, keepdim=True)
    caption_features.append(features)

caption_features = torch.cat(caption_features)

# 为图像查找匹配标题
with torch.no_grad():
    image_features = model.encode_image(image)
    image_features /= image_features.norm(dim=-1, keepdim=True)

similarities = (image_features @ caption_features.T).squeeze(0)
top_k = similarities.topk(3)

for idx, score in zip(top_k.indices, top_k.values):
    print(f"{captions[idx]}: {score:.3f}")
```

## 视觉问答

```python
# 创建是/否问题
image = preprocess(Image.open("photo.jpg")).unsqueeze(0)

questions = [
    "a photo showing people",
    "a photo showing animals",
    "a photo taken indoors",
    "a photo taken outdoors",
    "a photo taken during daytime",
    "a photo taken at night"
]

text = clip.tokenize(questions)

with torch.no_grad():
    logits, _ = model(image, text)
    probs = logits.softmax(dim=-1)

# 回答问题
for question, prob in zip(questions, probs[0]):
    answer = "Yes" if prob > 0.5 else "No"
    print(f"{question}: {answer} ({prob:.2%})")
```

## 图像去重

```python
# 检测重复/相似图像
def compute_similarity(img1_path, img2_path):
    img1 = preprocess(Image.open(img1_path)).unsqueeze(0)
    img2 = preprocess(Image.open(img2_path)).unsqueeze(0)

    with torch.no_grad():
        feat1 = model.encode_image(img1)
        feat2 = model.encode_image(img2)

        feat1 /= feat1.norm(dim=-1, keepdim=True)
        feat2 /= feat2.norm(dim=-1, keepdim=True)

        similarity = (feat1 @ feat2.T).item()

    return similarity

# 检查重复
threshold = 0.95
image_pairs = [("img1.jpg", "img2.jpg"), ("img1.jpg", "img3.jpg")]

for img1, img2 in image_pairs:
    sim = compute_similarity(img1, img2)
    if sim > threshold:
        print(f"{img1} and {img2} are duplicates (similarity: {sim:.3f})")
```

## 最佳实践

1. **使用描述性标签** - "a photo of X" 比仅 "X" 效果更好
2. **归一化嵌入** - 始终归一化以进行余弦相似度计算
3. **批量处理** - 一起处理多个图像/文本
4. **缓存嵌入** - 重新计算代价高昂
5. **设置适当阈值** - 在验证数据上测试
6. **使用 GPU** - 比 CPU 快 10-50 倍
7. **考虑模型大小** - ViT-B/32 是好的默认选择，ViT-L/14 用于最佳质量

## 资源

- **论文**：https://arxiv.org/abs/2103.00020
- **GitHub**：https://github.com/openai/CLIP
- **Colab**：https://colab.research.google.com/github/openai/clip/
