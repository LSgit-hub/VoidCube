---
name: record-browser-gif
description: 录制 VoidCube Desktop 或浏览器界面的真实交互 GIF，用于说明 UI 变化、工具流程或问题复现。需要录制演示、检查可视状态或把 GIF 附加到 Pull Request 时使用。
---

# 录制 VoidCube UI GIF

产出一个来自真实应用流程的本地 GIF。录制和发布分开处理：没有明确要求附加到 PR 时，只生成并验证本地文件，不修改远端状态。

## 录制前

1. 确认目标是 `desktop/` Electron 应用还是 VoidCube 的浏览器工具页面，并记录当前 commit。
2. Desktop 流程使用仓库已有依赖和脚本：

```powershell
Set-Location desktop
npm run build
npm run test:e2e
```

3. 浏览器流程优先使用 VoidCube 的 `browser_navigate`、`browser_snapshot`、`browser_click`、`browser_type` 和 `browser_vision` 工具。只有这些工具不可用时，才使用 `desktop/package.json` 中声明的 Playwright。
4. 使用独立的临时工作目录、浏览器上下文、端口和会话状态。不要把真实密钥、私人页面、通知或无关标签页录入视频。

## 录制原则

- 演示必须来自真实服务器、真实 UI 和真实工具入口；除非用户明确要求，不用 mock、fixture 或测试专用钩子冒充成功流程。
- 用唯一的可访问名称或稳定 DOM 状态等待页面就绪，不用固定 sleep 作为就绪判断。
- 一个故事包含三到六个有意义的状态，例如初始页、输入完成、执行中、成功或错误恢复。
- 录制前确认 viewport，保持整个故事尺寸一致；若展示错误或工具调用，打开详情让工具名、状态和结果可见。
- 原始视频、截图、脚本和 GIF 放在仓库已忽略的临时目录（例如 `.playwright-mcp/`）中。

## 编码与验证

仓库脚本位于 `skills/record-browser-gif/scripts/encode_gif.py`。运行前确认 `python`、`ffmpeg` 和 `ffprobe` 可用：

```powershell
$gif = (Resolve-Path skills/record-browser-gif).Path
python "$gif/scripts/encode_gif.py" `
  "C:\path\to\demo.webm" "C:\path\to\demo.gif" `
  --start 2 --end 32 --speed 2 --final-hold 3 `
  --fps 10 --max-width 1200 --colors 128
```

检查编码器的 JSON 输出、帧数、尺寸、时长和文件大小；实际查看 GIF 的代表帧，确认动画顺序、最后状态和敏感信息。运行该技能的单元测试：

```powershell
python -m unittest discover -s skills/record-browser-gif/scripts -p "test_*.py" -v
```

## PR 发布

只有用户要求附加 GIF 到 PR 时才执行发布。先确认录制 commit 仍是 PR 的最新 head。优先使用支持 `gh --attach` 的 GitHub CLI；不支持时使用独立的 assets 分支，不能把二进制文件提交到产品分支。发布后重新读取 PR body 和 head，确认图片链接、MIME 类型和 commit 对应关系。

报告 GIF 的绝对路径、录制入口、真实/模拟传输、commit、尺寸、时长和未完成的外部验证。
