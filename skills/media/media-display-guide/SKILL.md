---
name: media-display-guide
category: media
description: 交付展板（media_display）使用指南，支持文本、HTML、图片、视频推送
---

# 交付展板（media_display）使用指南

## 功能概述
将Agent生成的内容（文本、HTML、图片、视频）推送到Web UI展示板。

## 支持的媒体类型

### 1. 文本内容 (text)
```python
media_display(
    content="# 标题\n\n这是内容",
    media_type: "text",
    title: "展示标题",
    view_mode: "fit"
)
```

### 2. HTML内容 (html)
```python
media_display(
    content="<html>...</html>",
    media_type: "html",
    title: "HTML报告",
    auto_open: True
)
```

### 3. 图片 (image)
```python
# 方式1：远程URL
media_display(
    url: "https://example.com/image.png",
    media_type: "image",
    title: "图片标题"
)

# 方式2：生成后推送
image_generate(prompt="描述")
media_display(file_path="生成的图片路径")
```

### 4. 视频 (video)
```python
# 方式1：公开视频URL
media_display(
    url: "https://example.com/video.mp4",
    media_type: "video",
    title: "视频标题"
)

# 方式2：B站视频（需正确BV号）
media_playlist(
    items=[{"url": "https://www.bilibili.com/video/BVxxxxx", "media_type": "bilibili"}]
)
```

## 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| content | string | 否 | 内联文本/HTML内容 |
| file_path | string | 否 | 本地文件路径 |
| url | string | 否 | 远程媒体URL |
| media_type | string | 是 | text/html/image/video/auto |
| title | string | 否 | 展示标题 |
| auto_open | bool | 否 | 是否自动展开面板（默认true） |
| view_mode | string | 否 | fit/actual/fill（默认fit） |

## 最佳实践

### 1. 内容优先级
1. `file_path` > `url` > `content`
2. 优先使用文件路径（更稳定）
3. 远程URL需确保可访问

### 2. 自动展开
- `auto_open: True` - 推送后立即展开展示板
- `auto_open: False` - 仅推送，不自动展开

### 3. 显示模式
- `fit` - 保持比例适应（推荐）
- `actual` - 原始尺寸
- `fill` - 填充整个区域

### 4. 历史记录
- 每次推送都会记录到历史
- 可通过 `delivery_id` 追踪
- 最新推送自动成为当前显示

## 测试流程
```python
# 1. 文本测试
media_display(content="# 测试", media_type: "text")

# 2. HTML测试
media_display(content="<h1>测试</h1>", media_type: "html")

# 3. 图片测试
media_display(url: "https://picsum.photos/800/400", media_type: "image")

# 4. 视频测试
media_display(url: "https://example.com/video.mp4", media_type: "video")
```

## 常见问题

### 1. 文件不存在
```
error: "交付文件不存在: /path/to/file"
```
- 确保文件路径正确
- 使用绝对路径
- 检查文件是否已生成

### 2. 视频链接失效
- B站视频可能需要登录
- 某些视频可能已下架
- 使用正确的BV号（见bilibili-video-playback技能）

### 3. 白屏显示
- 检查Content-Type是否正确
- HTML内容需完整DOCTYPE声明
- 可能是媒体类型识别错误

## 相关文件
- `AGENTS.md` - 项目规则
- `bilibili-video-playback` - B站视频播放技能
- `media_playlist` - 批量推送媒体

## 变更记录
- 2026-08-11: 创建技能，记录媒体推送经验