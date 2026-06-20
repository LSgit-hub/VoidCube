# Segment Anything 高级用法指南

## SAM 2（视频分割）

### 概述

SAM 2 将 SAM 扩展到视频分割，采用流式内存架构：

```bash
pip install git+https://github.com/facebookresearch/segment-anything-2.git
```

### 视频分割

```python
from sam2.build_sam import build_sam2_video_predictor

predictor = build_sam2_video_predictor("sam2_hiera_l.yaml", "sam2_hiera_large.pt")

# 用视频初始化
predictor.init_state(video_path="video.mp4")

# 在第一帧添加提示
predictor.add_new_points(
    frame_idx=0,
    obj_id=1,
    points=[[100, 200]],
    labels=[1]
)

# 在视频中传播
for frame_idx, masks in predictor.propagate_in_video():
    # masks 包含所有跟踪对象的分割
    process_frame(frame_idx, masks)
```

### SAM 2 vs SAM 比较

| 功能 | SAM | SAM 2 |
|---------|-----|-------|
| 输入 | 仅图像 | 图像 + 视频 |
| 架构 | ViT + 解码器 | Hiera + 内存 |
| 内存 | 每图像 | 流式内存库 |
| 跟踪 | 否 | 是，跨帧 |
| 模型 | ViT-B/L/H | Hiera-T/S/B+/L |

## Grounded SAM（文本提示分割）

### 设置

```bash
pip install groundingdino-py
pip install git+https://github.com/facebookresearch/segment-anything.git
```

### 文本到掩码流水线

```python
from groundingdino.util.inference import load_model, predict
from segment_anything import sam_model_registry, SamPredictor
import cv2

# 加载 Grounding DINO
grounding_model = load_model("groundingdino_swint_ogc.pth", "GroundingDINO_SwinT_OGC.py")

# 加载 SAM
sam = sam_model_registry["vit_h"](checkpoint="sam_vit_h_4b8939.pth")
predictor = SamPredictor(sam)

def text_to_mask(image, text_prompt, box_threshold=0.3, text_threshold=0.25):
    """从文本描述生成掩码。"""
    # 从文本获取边界框
    boxes, logits, phrases = predict(
        model=grounding_model,
        image=image,
        caption=text_prompt,
        box_threshold=box_threshold,
        text_threshold=text_threshold
    )

    # 用 SAM 生成掩码
    predictor.set_image(image)

    masks = []
    for box in boxes:
        # 将归一化框转换为像素坐标
        h, w = image.shape[:2]
        box_pixels = box * np.array([w, h, w, h])

        mask, score, _ = predictor.predict(
            box=box_pixels,
            multimask_output=False
        )
        masks.append(mask[0])

    return masks, boxes, phrases

# 使用
image = cv2.imread("image.jpg")
image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

masks, boxes, phrases = text_to_mask(image, "person . dog . car")
```

## 批量处理

### 高效多图像处理

```python
import torch
from segment_anything import SamPredictor, sam_model_registry

class BatchedSAM:
    def __init__(self, checkpoint, model_type="vit_h", device="cuda"):
        self.sam = sam_model_registry[model_type](checkpoint=checkpoint)
        self.sam.to(device)
        self.predictor = SamPredictor(self.sam)
        self.device = device

    def process_batch(self, images, prompts):
        """处理多个图像及对应提示。"""
        results = []

        for image, prompt in zip(images, prompts):
            self.predictor.set_image(image)

            if "point" in prompt:
                masks, scores, _ = self.predictor.predict(
                    point_coords=prompt["point"],
                    point_labels=prompt["label"],
                    multimask_output=True
                )
            elif "box" in prompt:
                masks, scores, _ = self.predictor.predict(
                    box=prompt["box"],
                    multimask_output=False
                )

            results.append({
                "masks": masks,
                "scores": scores,
                "best_mask": masks[np.argmax(scores)]
            })

        return results

# 使用
batch_sam = BatchedSAM("sam_vit_h_4b8939.pth")

images = [cv2.imread(f"image_{i}.jpg") for i in range(10)]
prompts = [{"point": np.array([[100, 100]]), "label": np.array([1])} for _ in range(10)]

results = batch_sam.process_batch(images, prompts)
```

### 并行自动掩码生成

```python
from concurrent.futures import ThreadPoolExecutor
from segment_anything import SamAutomaticMaskGenerator

def generate_masks_parallel(images, num_workers=4):
    """并行生成多个图像的掩码。"""
    # 注意：每个工作器需要自己的模型实例
    def worker_init():
        sam = sam_model_registry["vit_b"](checkpoint="sam_vit_b_01ec64.pth")
        return SamAutomaticMaskGenerator(sam)

    generators = [worker_init() for _ in range(num_workers)]

    def process_image(args):
        idx, image = args
        generator = generators[idx % num_workers]
        return generator.generate(image)

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        results = list(executor.map(process_image, enumerate(images)))

    return results
```

## 自定义集成

### FastAPI 服务

```python
from fastapi import FastAPI, File, UploadFile
from pydantic import BaseModel
import numpy as np
import cv2
import io

app = FastAPI()

# 一次性加载模型
sam = sam_model_registry["vit_h"](checkpoint="sam_vit_h_4b8939.pth")
sam.to("cuda")
predictor = SamPredictor(sam)

class PointPrompt(BaseModel):
    x: int
    y: int
    label: int = 1

@app.post("/segment/point")
async def segment_with_point(
    file: UploadFile = File(...),
    points: list[PointPrompt] = []
):
    # 读取图像
    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    # 设置图像
    predictor.set_image(image)

    # 准备提示
    point_coords = np.array([[p.x, p.y] for p in points])
    point_labels = np.array([p.label for p in points])

    # 生成掩码
    masks, scores, _ = predictor.predict(
        point_coords=point_coords,
        point_labels=point_labels,
        multimask_output=True
    )

    best_idx = np.argmax(scores)

    return {
        "mask": masks[best_idx].tolist(),
        "score": float(scores[best_idx]),
        "all_scores": scores.tolist()
    }

@app.post("/segment/auto")
async def segment_automatic(file: UploadFile = File(...)):
    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    mask_generator = SamAutomaticMaskGenerator(sam)
    masks = mask_generator.generate(image)

    return {
        "num_masks": len(masks),
        "masks": [
            {
                "bbox": m["bbox"],
                "area": m["area"],
                "predicted_iou": m["predicted_iou"],
                "stability_score": m["stability_score"]
            }
            for m in masks
        ]
    }
```

### Gradio 界面

```python
import gradio as gr
import numpy as np

# 加载模型
sam = sam_model_registry["vit_h"](checkpoint="sam_vit_h_4b8939.pth")
predictor = SamPredictor(sam)

def segment_image(image, evt: gr.SelectData):
    """在点击点分割对象。"""
    predictor.set_image(image)

    point = np.array([[evt.index[0], evt.index[1]]])
    label = np.array([1])

    masks, scores, _ = predictor.predict(
        point_coords=point,
        point_labels=label,
        multimask_output=True
    )

    best_mask = masks[np.argmax(scores)]

    # 在图像上叠加掩码
    overlay = image.copy()
    overlay[best_mask] = overlay[best_mask] * 0.5 + np.array([255, 0, 0]) * 0.5

    return overlay

with gr.Blocks() as demo:
    gr.Markdown("# SAM 交互式分割")
    gr.Markdown("点击对象以分割它")

    with gr.Row():
        input_image = gr.Image(label="输入图像", interactive=True)
        output_image = gr.Image(label="分割图像")

    input_image.select(segment_image, inputs=[input_image], outputs=[output_image])

demo.launch()
```

## 微调 SAM

### LoRA 微调（实验性）

```python
from peft import LoraConfig, get_peft_model
from transformers import SamModel

# 加载模型
model = SamModel.from_pretrained("facebook/sam-vit-base")

# 配置 LoRA
lora_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules=["qkv"],  # 注意力层
    lora_dropout=0.1,
    bias="none",
)

# 应用 LoRA
model = get_peft_model(model, lora_config)

# 训练循环（简化）
optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)

for batch in dataloader:
    outputs = model(
        pixel_values=batch["pixel_values"],
        input_points=batch["input_points"],
        input_labels=batch["input_labels"]
    )

    # 自定义损失（例如，与真值的 IoU 损失）
    loss = compute_loss(outputs.pred_masks, batch["gt_masks"])
    loss.backward()
    optimizer.step()
    optimizer.zero_grad()
```

### MedSAM（医学影像）

```python
# MedSAM 是为医学图像微调的 SAM
# https://github.com/bowang-lab/MedSAM

from segment_anything import sam_model_registry, SamPredictor
import torch

# 加载 MedSAM 检查点
medsam = sam_model_registry["vit_b"](checkpoint="medsam_vit_b.pth")
medsam.to("cuda")

predictor = SamPredictor(medsam)

# 处理医学图像
# 如需要，将灰度转换为 RGB
medical_image = cv2.imread("ct_scan.png", cv2.IMREAD_GRAYSCALE)
rgb_image = np.stack([medical_image] * 3, axis=-1)

predictor.set_image(rgb_image)

# 使用框提示分割（医学影像常用）
masks, scores, _ = predictor.predict(
    box=np.array([x1, y1, x2, y2]),
    multimask_output=False
)
```

## 高级掩码处理

### 掩码细化

```python
import cv2
from scipy import ndimage

def refine_mask(mask, kernel_size=5, iterations=2):
    """使用形态学操作细化掩码。"""
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))

    # 闭合小孔
    closed = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, kernel, iterations=iterations)

    # 移除小噪声
    opened = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel, iterations=iterations)

    return opened.astype(bool)

def fill_holes(mask):
    """填充掩码中的孔。"""
    filled = ndimage.binary_fill_holes(mask)
    return filled

def remove_small_regions(mask, min_area=100):
    """移除小的不连通区域。"""
    labeled, num_features = ndimage.label(mask)
    sizes = ndimage.sum(mask, labeled, range(1, num_features + 1))

    # 仅保留大于 min_area 的区域
    mask_clean = np.zeros_like(mask)
    for i, size in enumerate(sizes, 1):
        if size >= min_area:
            mask_clean[labeled == i] = True

    return mask_clean
```

### 掩码到多边形转换

```python
import cv2

def mask_to_polygons(mask, epsilon_factor=0.01):
    """将二值掩码转换为多边形坐标。"""
    contours, _ = cv2.findContours(
        mask.astype(np.uint8),
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    polygons = []
    for contour in contours:
        epsilon = epsilon_factor * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, epsilon, True)
        polygon = approx.squeeze().tolist()
        if len(polygon) >= 3:  # 有效多边形
            polygons.append(polygon)

    return polygons

def polygons_to_mask(polygons, height, width):
    """将多边形转换回二值掩码。"""
    mask = np.zeros((height, width), dtype=np.uint8)
    for polygon in polygons:
        pts = np.array(polygon, dtype=np.int32)
        cv2.fillPoly(mask, [pts], 1)
    return mask.astype(bool)
```

### 多尺度分割

```python
def multiscale_segment(image, predictor, point, scales=[0.5, 1.0, 2.0]):
    """在多个尺度生成掩码并组合。"""
    h, w = image.shape[:2]
    masks_all = []

    for scale in scales:
        # 调整图像大小
        new_h, new_w = int(h * scale), int(w * scale)
        scaled_image = cv2.resize(image, (new_w, new_h))
        scaled_point = (point * scale).astype(int)

        # 分割
        predictor.set_image(scaled_image)
        masks, scores, _ = predictor.predict(
            point_coords=scaled_point.reshape(1, 2),
            point_labels=np.array([1]),
            multimask_output=True
        )

        # 将掩码调整回原始大小
        best_mask = masks[np.argmax(scores)]
        original_mask = cv2.resize(best_mask.astype(np.uint8), (w, h)) > 0.5

        masks_all.append(original_mask)

    # 组合掩码（多数投票）
    combined = np.stack(masks_all, axis=0)
    final_mask = np.sum(combined, axis=0) >= len(scales) // 2 + 1

    return final_mask
```

## 性能优化

### TensorRT 加速

```python
import tensorrt as trt
import pycuda.driver as cuda
import pycuda.autoinit

def export_to_tensorrt(onnx_path, engine_path, fp16=True):
    """将 ONNX 模型转换为 TensorRT 引擎。"""
    logger = trt.Logger(trt.Logger.WARNING)
    builder = trt.Builder(logger)
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
    parser = trt.OnnxParser(network, logger)

    with open(onnx_path, 'rb') as f:
        if not parser.parse(f.read()):
            for error in range(parser.num_errors):
                print(parser.get_error(error))
            return None

    config = builder.create_builder_config()
    config.max_workspace_size = 1 << 30  # 1GB

    if fp16:
        config.set_flag(trt.BuilderFlag.FP16)

    engine = builder.build_engine(network, config)

    with open(engine_path, 'wb') as f:
        f.write(engine.serialize())

    return engine
```

### 内存高效推理

```python
class MemoryEfficientSAM:
    def __init__(self, checkpoint, model_type="vit_b"):
        self.sam = sam_model_registry[model_type](checkpoint=checkpoint)
        self.sam.eval()
        self.predictor = None

    def __enter__(self):
        self.sam.to("cuda")
        self.predictor = SamPredictor(self.sam)
        return self

    def __exit__(self, *args):
        self.sam.to("cpu")
        torch.cuda.empty_cache()

    def segment(self, image, points, labels):
        self.predictor.set_image(image)
        masks, scores, _ = self.predictor.predict(
            point_coords=points,
            point_labels=labels,
            multimask_output=True
        )
        return masks, scores

# 使用上下文管理器（自动清理）
with MemoryEfficientSAM("sam_vit_b_01ec64.pth") as sam:
    masks, scores = sam.segment(image, points, labels)
# CUDA 内存自动释放
```

## 数据集生成

### 创建分割数据集

```python
import json

def generate_dataset(images_dir, output_dir, mask_generator):
    """从图像生成分割数据集。"""
    annotations = []

    for img_path in Path(images_dir).glob("*.jpg"):
        image = cv2.imread(str(img_path))
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        # 生成掩码
        masks = mask_generator.generate(image)

        # 过滤高质量掩码
        good_masks = [m for m in masks if m["predicted_iou"] > 0.9]

        # 保存标注
        for i, mask_data in enumerate(good_masks):
            annotation = {
                "image_id": img_path.stem,
                "mask_id": i,
                "bbox": mask_data["bbox"],
                "area": mask_data["area"],
                "segmentation": mask_to_rle(mask_data["segmentation"]),
                "predicted_iou": mask_data["predicted_iou"],
                "stability_score": mask_data["stability_score"]
            }
            annotations.append(annotation)

    # 保存数据集
    with open(output_dir / "annotations.json", "w") as f:
        json.dump(annotations, f)

    return annotations
```
