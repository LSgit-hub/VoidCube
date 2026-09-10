---
name: supervisor-media-debug
description: 调试 VoidCube 多媒体展示板白屏问题。当 media_display 推送成功但 Web UI 显示白屏时使用。
---

# VoidCube 多媒体展示板调试

## 常见问题与解决

### 问题：媒体推送成功但显示白屏

**症状**：`media_display` 返回 `status=ok`，但 Web UI 展示板空白

**根因**：Supervisor 服务未运行，SSE/HTTP 端点不可达

## 诊断步骤

### 1. 检查 Supervisor 服务端口（默认 6002）

```python
check_port(host="localhost", port=6002)
```

### 2. 确认服务进程

```bash
ps aux | grep -E "(supervisor|python)" | grep -v grep
```

### 3. 检查网络监听

```bash
netstat -tlnp 2>/dev/null | grep 6002
```

## 解决方案

### 启动 Supervisor 服务

```python
python -c "from systems.supervisor.supervisor import main; import asyncio; asyncio.run(main())"
```

后台运行：
```bash
python -c "from systems.supervisor.supervisor import main; import asyncio; asyncio.run(main())" &
```

## 端口配置

- 默认端口：6002
- 可通过环境变量 `SUPERVISOR_PORT` 覆盖
- 媒体端点：`http://127.0.0.1:6002/ui/media/enqueue`

## 测试流程

```python
# 1. 先确认服务运行
# 2. 推送测试内容
media_display(
    content="<h1>测试</h1>",
    media_type="html",
    title="测试标题"
)
# 3. 检查返回 status=ok
# 4. 用户查看 Web UI 展示板
```

## 注意事项

- `auto_play=true` 会尝试自动展开面板，但部分浏览器需要用户手动展开
- 外部图片/网页可能因 CORS 或网络限制无法加载
- PDF 需要浏览器支持 PDF 嵌入查看
- 面板收起时内容不可见，需用户手动展开查看

## 相关文件

- 工具实现：`/workspace/tools/media_tool.py`
- 服务实现：`/workspace/systems/supervisor/ui_runtime.py`
- 配置模型：`/workspace/systems/supervisor/config_models.py`
