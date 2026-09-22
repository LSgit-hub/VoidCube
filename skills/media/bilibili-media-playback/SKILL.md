---
name: bilibili-media-playback
category: media
description: B站视频播放与媒体交付完整流程 — 包含BV号获取、媒体推送到展板和播放器的方法
---

# B站视频播放与媒体交付技能

## 核心流程

### 1. B站视频播放

#### 搜索视频
```python
browser_navigate("https://search.bilibili.com/all?keyword=关键词&order=pubdate")
```

#### 获取正确BV号（关键步骤）
```python
# 点击视频标题
browser_click("视频链接ref")

# 获取当前页面URL
url = browser_console("window.location.href")
# 格式：https://www.bilibili.com/video/BVxxxxxxxxx/?spm_id_from=...
# 提取BV号：URL中 /video/ 后的字符串，如 BV1xBuv6iEGF
```

#### 批量推送音乐播放列表
```python
media_playlist(
    items=[
        {"media_type": "bilibili", "title": "歌名1", "url": "BV号链接1"},
        {"media_type": "bilibili", "title": "歌名2", "url": "BV号链接2"},
        # ... 更多歌曲
    ],
    queue_mode: "replace"  # replace: 清空并播放第一首; enqueue: 追加到队列
)
```

#### 分P合集处理
- 分P合集视频（如音乐合辑）每个章节是独立视频
- 需要分别获取每个章节的BV号
- 或使用单首歌曲视频（更稳定）

#### 高播放量策略
- 搜索时使用高播放量过滤（百万+/千万+）
- 高播放量视频BV号更稳定，不易失效

#### 推送到播放器
```python
media_playlist(
    items=[{
        "auto_play": True,
        "media_type": "bilibili",
        "title": "视频标题",
        "url": "https://www.bilibili.com/video/BVxxxxxxxxx"
    }],
    queue_mode: "replace"
)
```

#### 常见错误
- ❌ 使用搜索列表显示的BV号（可能已过期）
- ❌ 凭记忆猜测BV号
- ✅ 必须从浏览器实际页面获取URL
- ✅ 使用 `browser_console("window.location.href")` 直接获取

---

### 2. 媒体交付到展板

#### 文本内容
```python
media_display(
    content="# 标题\n\n内容",
    media_type: "text",
    title: "展示标题",
    view_mode: "fit"
)
```

#### HTML内容
```python
media_display(
    content="<html>...</html>",
    media_type: "html",
    title: "HTML报告",
    auto_open: True
)
```

#### 图片
```python
# 远程URL
media_display(url: "https://example.com/image.png", media_type: "image")

# 本地文件
media_display(file_path: "/path/to/image.png", media_type: "image")
```

#### 视频（直接URL）
```python
media_display(
    url: "https://example.com/video.mp4",
    media_type: "video",
    title: "视频标题"
)
```

#### 参数说明
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| content | string | 否 | 内联文本/HTML内容 |
| file_path | string | 否 | 本地文件路径 |
| url | string | 否 | 远程媒体URL |
| media_type | string | 是 | text/html/image/video/auto |
| title | string | 否 | 展示标题 |
| auto_open | bool | 否 | 是否自动展开面板（默认true） |
| view_mode | string | 否 | fit/actual/fill（默认fit） |

#### 优先级规则
1. `file_path` > `url` > `content`
2. 优先使用文件路径（更稳定）
3. 远程URL需确保可访问

---

### 2.5 查看/播放指定UP主最新视频（空间页直连法，更稳）

搜索页/搜索API可能被风控（412），此时**直接访问UP主空间页**匿名可看，按最新发布排序：

```python
# 1. 获取UP主UID（若已知BV号反查）：
#    curl "https://api.bilibili.com/x/web-interface/view?bvid=BVxxxx" 
#    取 data.owner.mid 即 UID（公开接口，无需登录）

# 2. 直达空间投稿页（/video 会自动重定向到 /upload/video）
browser_navigate("https://space.bilibili.com/{UID}/video")
# 页面列表即最新发布排序，无需登录态，标题自带日期（如"8月26日 收评..."）

# 3. 用 browser_console 一次性提取所有视频BV链接（比点击卡片可靠）
#    注意：标准CSS选择器(.bili-video-card等)可能匹配不到，用全量a标签+BV正则
urls = browser_console('''(() => {
  const all = Array.from(document.querySelectorAll('a'));
  return all.filter(a => /BV[0-9A-Za-z]{10}/.test(a.href || ''))
            .map(a => ({href: a.href, text: (a.textContent || '').trim().slice(0, 50)}));
})()''')
# 结果里 href 即 https://www.bilibili.com/video/BVxxx/...，text 为标题，按日期筛选目标视频

# 4. 推送到播放器
media_playlist(
    items=[{
        "media_type": "bilibili",
        "title": "标题",
        "url": "https://www.bilibili.com/video/BVxxx"
    }],
    queue_mode: "replace"
)
```

**坑**：
- `browser_click` 点击视频卡片可能报 `Unknown ref` 或 `duplicate_or_in_flight_action`，改用 console 正则提取更稳
- 空间页卡片结构选择器（`.bili-video-card__info--tit` 等）实测匹配不到，直接扫全量 `a[href*="BV"]` 即可
- 页面出现 `(empty page)` 时重新 `browser_navigate` 一次再取快照

### 3. 完整示例：播放九哥谈股论金

```python
# 1. 直达空间页（UID=3493269333870814，已反查确认）
browser_navigate("https://space.bilibili.com/3493269333870814/video")

# 2. console提取BV链接（见2.5步骤3），按标题含"8月26日 收评"筛出目标

# 3. 推送到播放器
media_playlist(
    items=[{
        "media_type": "bilibili",
        "title": "九哥谈股论金 - 8月26日 收评",
        "url": "https://www.bilibili.com/video/BV1qF8o6eE5h"
    }],
    queue_mode: "replace"
)
```

---

## 关键规则总结

### B站视频播放
- 必须从浏览器实际页面获取BV号（搜索列表可能显示过期链接）
- 使用 `browser_console("window.location.href")` 直接获取URL
- 查指定UP主最新视频：优先空间页直连法（`space.bilibili.com/{UID}/video`），匿名可看、按最新发布排序，避开搜索页风控
- 空间页取BV链接：console 扫全量 `a` 标签 + `/BV[0-9A-Za-z]{10}/` 正则，比 `browser_click` 点击卡片更稳
- 不要依赖搜索列表显示的链接
- 不要猜测或回忆BV号
- 批量推送使用 `media_playlist` with `queue_mode: "replace"`
- 音乐合集建议用单首歌曲视频，避免分P问题
- 高播放量视频BV号更稳定（百万+/千万+优先）

### 媒体交付
- 文本/HTML用 `media_display`，视频用 `media_playlist`（播放器）或 `media_display`（展板）
- B站视频优先用 `media_playlist` 推送到播放器
- 展板展示用 `media_display`

---

## 相关工具
- `media_playlist` - 批量推送媒体到播放器
- `media_display` - 推送到交付展板
- `browser_console` - 获取页面URL

## 变更记录
- 2026-08-11: 创建技能，合并B站视频播放与媒体交付流程
- 2026-08-27: 新增空间页直连法（查指定UP主最新视频）：空间页匿名可看、console正则提取BV链接替代点击；记录 browser_click 失败与标准选择器不匹配的坑