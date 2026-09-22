---
name: browser-media-silence
category: media
description: 使用浏览器工具访问媒体网站（B站等）时的静音规则，避免自动播放声音外放
---

# 浏览器媒体访问静音规则

## 问题背景

使用浏览器工具访问 B站 等媒体网站时，页面可能自动播放视频或广告，导致：
1. 声音外放，干扰用户
2. 后续用 `media_playlist` 推送时再次播放，造成声音重叠
3. 体验不专业

## 解决方案

**在 navigate 后立即执行静音操作**

### 标准流程

```python
# 1. 导航到页面
browser_navigate("https://www.bilibili.com/...")

# 2. 等待页面加载（snapshot 确认加载完成）
browser_snapshot()

# 3. 立即静音所有媒体元素
browser_console("""
(() => {
  const videos = document.querySelectorAll('video');
  videos.forEach(v => { v.muted = true; v.pause(); });
  const audios = document.querySelectorAll('audio');
  audios.forEach(a => { a.muted = true; a.pause(); });
  return `muted ${videos.length + audios.length} elements`;
})()
""")

# 4. 继续后续操作（提取 BV 号、点击等）
# ...

# 5. 只有当需要推送给用户时，才调用 media_playlist
media_playlist(items=[...], queue_mode="replace")
```

### 简化版（单行）

```python
browser_console("document.querySelectorAll('video,audio').forEach(e => { e.muted=true; e.pause(); })")
```

## 适用场景

- 访问 B站 搜索页、UP主空间页
- 访问任何可能有自动播放的媒体网站
- 使用浏览器工具查找视频/音乐链接时

## 不触发场景

- 纯文本/文档类页面（无媒体元素）
- 已确认无自动播放的页面

## 变更记录

- 2026-09-08: 创建，响应锚点关于 B站自动播放声音重叠的反馈