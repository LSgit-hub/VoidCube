---
name: windows-automation
description: Automate Windows desktop applications through the uiautomation toolset, including launching processes, finding windows, inspecting controls, entering text, clicking buttons, sending keys, and verifying results. Use only on Windows when desktop UI interaction is required.
---

# Windows应用自动化控制技能

## 技能概述

提供一套系统化的Windows应用自动化控制能力，支持以下完整流程：
1. **应用启动与窗口管理**
2. **控件扫描与分析**
3. **智能识别（UIA + OCR）**
4. **控件操作执行**
5. **流程控制与错误处理**

## 工具集

本技能依赖 `uiautomation` 工具集，包含以下工具：

| 工具名称 | 功能描述 |
|---------|---------|
| `uia_start_process` | 启动应用程序 |
| `uia_find_window` | 查找窗口 |
| `uia_list_controls` | 扫描控件树 |
| `uia_click_button` | 点击按钮 |
| `uia_set_text` | 设置文本 |
| `uia_get_text` | 获取文本 |
| `uia_send_keys` | 发送键盘操作 |
| `uia_window_action` | 窗口操作 |
| `uia_mouse_click` | 鼠标点击 |

## 使用流程

### 标准自动化流程

```
┌─────────────────────────────────────────────────────────────────┐
│                    Windows应用自动化流程                        │
├─────────────────────────────────────────────────────────────────┤
│  Step 1: 启动应用                                              │
│     uia_start_process(executable, wait_for_window=True)        │
│                          ↓                                     │
│  Step 2: 定位窗口                                              │
│     uia_find_window(name="应用标题")                            │
│                          ↓                                     │
│  Step 3: 扫描控件                                              │
│     uia_list_controls(window_name, search_depth=3)             │
│                          ↓                                     │
│  Step 4: 分析识别                                              │
│     - 分析控件树找到目标控件                                    │
│     - 记录控件名称、类型、AutomationId                          │
│                          ↓                                     │
│  Step 5: 执行操作                                              │
│     - uia_click_button / uia_set_text / uia_send_keys          │
│                          ↓                                     │
│  Step 6: 验证结果                                              │
│     - uia_get_text 获取反馈                                    │
│     - 判断操作是否成功                                          │
└─────────────────────────────────────────────────────────────────┘
```

## 详细使用指南

### 阶段一：应用启动

**启动应用并等待窗口出现：**
```json
{
  "thought": "启动目标应用程序",
  "name": "uia_start_process",
  "query_language": "Chinese",
  "params": {
    "executable": "notepad.exe",
    "wait_for_window": true,
    "timeout": 10
  }
}
```

**检查窗口是否存在：**
```json
{
  "thought": "验证应用是否成功启动",
  "name": "uia_find_window",
  "query_language": "Chinese",
  "params": {
    "name": "无标题 - 记事本"
  }
}
```

### 阶段二：控件扫描

**扫描窗口控件树：**
```json
{
  "thought": "扫描应用窗口的所有控件，了解界面结构",
  "name": "uia_list_controls",
  "query_language": "Chinese",
  "params": {
    "window_name": "无标题 - 记事本",
    "search_depth": 3
  }
}
```

**控件类型说明：**
- `WindowControl`: 窗口
- `ButtonControl`: 按钮
- `EditControl`: 编辑框
- `TextControl`: 文本显示
- `MenuControl`: 菜单
- `ComboBoxControl`: 下拉框
- `CheckBoxControl`: 复选框

### 阶段三：智能分析

**分析策略：**

1. **查找按钮**：搜索 `ControlTypeName == "ButtonControl"`
2. **查找输入框**：搜索 `ControlTypeName == "EditControl"`
3. **查找菜单**：搜索 `ControlTypeName == "MenuControl"`
4. **通过名称定位**：使用 `Name` 属性匹配
5. **通过AutomationId定位**：使用 `automation_id` 精确匹配

### 阶段四：执行操作

**点击按钮：**
```json
{
  "thought": "点击'保存'按钮",
  "name": "uia_click_button",
  "query_language": "Chinese",
  "params": {
    "window_name": "无标题 - 记事本",
    "button_name": "保存(S)",
    "search_depth": 3
  }
}
```

**输入文本：**
```json
{
  "thought": "在编辑框中输入内容",
  "name": "uia_set_text",
  "query_language": "Chinese",
  "params": {
    "window_name": "无标题 - 记事本",
    "text": "自动化测试内容",
    "search_depth": 2
  }
}
```

**键盘快捷键：**
```json
{
  "thought": "使用Ctrl+S快捷键保存",
  "name": "uia_send_keys",
  "query_language": "Chinese",
  "params": {
    "window_name": "无标题 - 记事本",
    "keys": "{CTRL}s"
  }
}
```

**窗口操作：**
```json
{
  "thought": "最大化窗口以便更好地查看",
  "name": "uia_window_action",
  "query_language": "Chinese",
  "params": {
    "window_name": "无标题 - 记事本",
    "action": "maximize"
  }
}
```

### 阶段五：结果验证

**获取文本验证：**
```json
{
  "thought": "验证操作结果",
  "name": "uia_get_text",
  "query_language": "Chinese",
  "params": {
    "window_name": "保存",
    "control_name": "文件名(N):",
    "search_depth": 2
  }
}
```

## 实用示例

### 示例1：自动化填写表单

```json
// 1. 启动表单应用
{"name": "uia_start_process", "params": {"executable": "C:\\Apps\\FormApp.exe", "wait_for_window": true}}

// 2. 扫描控件
{"name": "uia_list_controls", "params": {"window_name": "数据录入表单", "search_depth": 3}}

// 3. 填写姓名
{"name": "uia_set_text", "params": {"window_name": "数据录入表单", "text": "张三", "control_name": "姓名"}}

// 4. 填写邮箱
{"name": "uia_set_text", "params": {"window_name": "数据录入表单", "text": "zhangsan@example.com", "control_name": "邮箱"}}

// 5. 选择性别
{"name": "uia_click_button", "params": {"window_name": "数据录入表单", "button_name": "男"}}

// 6. 提交表单
{"name": "uia_click_button", "params": {"window_name": "数据录入表单", "button_name": "提交"}}
```

### 示例2：自动化文档处理

```json
// 1. 打开Word
{"name": "uia_start_process", "params": {"executable": "winword.exe", "wait_for_window": true}}

// 2. 新建文档（Ctrl+N）
{"name": "uia_send_keys", "params": {"window_name": "Microsoft Word", "keys": "{CTRL}n"}}

// 3. 输入内容
{"name": "uia_set_text", "params": {"window_name": "文档1 - Word", "text": "自动化生成的文档内容"}}

// 4. 保存文档
{"name": "uia_send_keys", "params": {"window_name": "文档1 - Word", "keys": "{CTRL}s"}}

// 5. 在另存为对话框中输入文件名
{"name": "uia_set_text", "params": {"window_name": "另存为", "text": "自动化报告.docx", "control_name": "文件名"}}

// 6. 点击保存按钮
{"name": "uia_click_button", "params": {"window_name": "另存为", "button_name": "保存(S)"}}
```

## 高级技巧

### 1. 控件定位优先级

| 优先级 | 定位方式 | 稳定性 | 说明 |
|-------|---------|--------|------|
| 1 | AutomationId | 最高 | 控件唯一标识符 |
| 2 | ClassName + Name | 高 | 组合定位 |
| 3 | Name | 中 | 可能重复 |
| 4 | 坐标定位 | 低 | 分辨率敏感 |

### 2. 等待策略

**方式一：轮询等待窗口**
```json
{
  "name": "uia_start_process",
  "params": {
    "executable": "slow_app.exe",
    "wait_for_window": true,
    "timeout": 30
  }
}
```

**方式二：延迟操作**（通过代码执行工具）
```python
import time
time.sleep(2)  # 等待2秒
```

### 3. 错误处理模式

```json
// 推荐的重试模式
{
  "thought": "尝试点击按钮，最多重试3次",
  "name": "uia_click_button",
  "params": {
    "window_name": "目标窗口",
    "button_name": "目标按钮"
  }
}
// 如果失败，分析错误信息并尝试其他定位方式
```

### 4. 组合操作

**使用键盘快捷键加速流程：**
- `{CTRL}s`: 保存
- `{CTRL}c`: 复制
- `{CTRL}v`: 粘贴
- `{ENTER}`: 回车确认
- `{TAB}`: 切换焦点
- `{ESC}`: 取消

## 注意事项

### 权限要求
- 某些应用需要管理员权限才能自动化控制
- 建议以管理员身份运行VoidCube

### 应用兼容性
- 支持 Win32、WPF、UWP 应用
- 部分老旧应用可能不支持UI Automation
- 对于不支持UIA的应用，建议使用坐标定位或OCR

### 性能优化
- 减少不必要的控件扫描
- 使用精确的定位条件（AutomationId优先）
- 避免频繁的窗口切换

### 稳定性建议
1. **添加适当延迟**：操作之间添加0.5-2秒延迟
2. **验证每一步**：关键操作后验证结果
3. **处理异常情况**：准备备选方案
4. **记录日志**：便于调试和追踪

## 故障排除

### 常见问题

| 问题 | 原因 | 解决方案 |
|-----|------|---------|
| 找不到窗口 | 窗口标题不正确 | 使用uia_find_window测试不同标题 |
| 控件无法定位 | 搜索深度不足 | 增加search_depth参数 |
| 点击无效 | 控件不可见或被遮挡 | 先激活窗口 |
| 权限错误 | 没有足够权限 | 以管理员身份运行 |

### 调试技巧

1. **使用uia_list_controls获取控件信息**
2. **记录每个步骤的返回结果**
3. **检查窗口标题是否动态变化**
4. **使用Windows SDK的Inspect.exe工具查看控件属性**

## 扩展能力

未来可以扩展以下功能：
- **OCR识别模块**：处理不支持UIA的应用
- **图像比对**：验证界面状态
- **智能等待**：基于条件的等待机制
- **流程模板**：预定义的自动化流程
- **错误恢复**：自动重试和回滚机制

---

**使用建议**：将此技能与 `execute_code` 工具结合使用，可以实现更复杂的流程控制逻辑，如循环、条件判断、错误处理等。
