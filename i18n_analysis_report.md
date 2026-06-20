# VoidCube CLI 多国语言支持分析报告

生成时间: 2026-05-24

## 📊 总体统计

| 指标 | 数量 |
|------|------|
| 代码中实际使用的翻译键 | 69 个 |
| 英文翻译文件中的键总数 | 443 个 |
| 中文翻译文件中的键总数 | 3,266 个 |
| 代码使用但缺失中文翻译 | 0 个 ✅ |
| 中文翻译为空的键 | 0 个 ✅ |

## 🎯 关键发现

### 1. **中文翻译覆盖率: 100%** ✅

所有代码中实际使用的翻译键都已在中文翻译文件中完整翻译，无缺失。

### 2. **英文翻译文件包含更多翻译键**

英文翻译文件（443个键）比代码实际使用（69个键）多很多，这表明：
- 英文翻译文件为未来的功能扩展预留了翻译键
- 部分翻译键可能尚未在代码中使用

### 3. **中文翻译文件包含丰富的术语词库**

中文翻译文件（3,266个键）远超英文文件，这部分主要是：
- prompts 部分的术语词库（ML/AI 相关术语的中文翻译）
- 为AI智能体提供丰富的上下文术语支持

## 📝 补充的翻译内容

### 4. **新增 memory 模块翻译** ✅

在分析过程中发现 `memory_setup.py` 中使用了 `memory.` 开头的翻译键，但这些键在翻译文件中缺失。已补充：

#### 英文翻译 (34个键)
```json
"memory": {
  "installing_deps": "Installing dependencies: {deps}",
  "uv_not_found": "uv not found",
  "install_uv": "Run: uv pip install uv",
  "re_run": "Then re-run this command",
  "installed": "Installed: {deps}",
  "failed_install": "Failed to install: {deps}",
  "install_failed": "Installation failed: {error}",
  "run_manually": "Or run manually: {cmd}",
  "dep_not_found": "External dependency not found: {dep}",
  "provider_not_found": "Provider not found: {provider}",
  "run_setup": "Run /memory setup to configure",
  "provider": "Provider: {name}",
  "activation_saved": "Activation saved",
  "no_providers": "No memory providers found",
  "install_plugin": "Run /memory setup to install a plugin",
  "builtin_only": "Built-in only (MEMORY.md / USER.md)",
  "saved_to_config": "Saved to config",
  "configuring": "Configuring: {name}",
  "failed_write_config": "Failed to write config: {error}",
  "config_saved": "Config saved",
  "api_keys_saved": "API keys saved",
  "start_new_session": "Start a new session to use",
  "status": "Memory Status",
  "builtin_always_active": "Built-in memory: always active",
  "none_builtin_only": "none (built-in only)",
  "plugin_installed": "Plugin installed",
  "status_available": "Status: available",
  "status_not_available": "Status: not available",
  "missing": "Missing:",
  "plugin_not_installed": "Plugin not installed",
  "install_plugin_to": "Install plugin for: {provider}",
  "installed_plugins": "Installed plugins",
  "active": " (active)",
  "setup": "Memory Setup"
}
```

#### 中文翻译 (34个键)
```json
"memory": {
  "installing_deps": "正在安装依赖: {deps}",
  "uv_not_found": "未找到 uv",
  "install_uv": "运行: uv pip install uv",
  "re_run": "然后重新运行此命令",
  "installed": "已安装: {deps}",
  "failed_install": "安装失败: {deps}",
  "install_failed": "安装失败: {error}",
  "run_manually": "或手动运行: {cmd}",
  "dep_not_found": "未找到外部依赖: {dep}",
  "provider_not_found": "未找到提供者: {provider}",
  "run_setup": "运行 /memory setup 进行配置",
  "provider": "提供者: {name}",
  "activation_saved": "激活已保存",
  "no_providers": "未找到内存提供者",
  "install_plugin": "运行 /memory setup 安装插件",
  "builtin_only": "仅内置 (MEMORY.md / USER.md)",
  "saved_to_config": "已保存到配置",
  "configuring": "正在配置: {name}",
  "failed_write_config": "写入配置失败: {error}",
  "config_saved": "配置已保存",
  "api_keys_saved": "API 密钥已保存",
  "start_new_session": "启动新会话以使用",
  "status": "内存状态",
  "builtin_always_active": "内置内存: 始终激活",
  "none_builtin_only": "无 (仅内置)",
  "plugin_installed": "插件已安装",
  "status_available": "状态: 可用",
  "status_not_available": "状态: 不可用",
  "missing": "缺失:",
  "plugin_not_installed": "插件未安装",
  "install_plugin_to": "安装插件用于: {provider}",
  "installed_plugins": "已安装的插件",
  "active": " (活动)",
  "setup": "内存设置"
}
```

## 📋 翻译文件结构

### 主要翻译类别

1. **commands** - CLI 命令描述和用法
2. **categories** - 命令分类标签
3. **banner** - 启动横幅信息
4. **toolsets** - 工具集描述
5. **errors** - 错误消息
6. **prompts** - 交互提示和用户消息
7. **process** - 后台进程相关
8. **language_command** - 语言切换命令
9. **usage** - 用法说明
10. **tips** - 提示信息
11. **auth** - 认证相关消息
12. **backup** - 备份相关
13. **common** - 通用词汇
14. **cli** - CLI 通用消息
15. **help** - 帮助信息
16. **time** - 时间相关
17. **ops** - 服务器运维工具
18. **memory** - 内存插件设置 ✅ 新增

## 🌐 国际化架构

### 语言切换机制

VoidCube CLI 支持多语言切换：

```python
# 设置语言
from VoidCube_cli.i18n import set_locale

# 切换到中文
set_locale("zh_CN")

# 切换到英文
set_locale("en_US")
```

### 语言检测优先级

1. 显式提供的语言参数
2. `VOIDCUBE_LANG` 环境变量
3. 配置文件中的 `display.language`
4. 系统 `LANG` 环境变量
5. 默认: `zh_CN` (中文)

### 使用翻译

```python
from VoidCube_cli.i18n import t

# 简单翻译
print(t("commands.new.description"))

# 带参数翻译
print(t("errors.file_not_found", path="/path/to/file"))
```

## ✨ 优化建议

### 1. **删除未使用的翻译键**

英文翻译文件中有大量未使用的翻译键，可以考虑：
- 定期审查并删除长期未使用的键
- 为翻译键添加注释说明其用途
- 使用翻译管理工具跟踪使用情况

### 2. **补充 prompts 术语库**

中文翻译文件的 prompts 部分包含丰富的 ML/AI 术语，建议：
- 定期更新以包含最新的AI术语
- 考虑添加行业特定术语
- 保持术语的一致性

### 3. **翻译质量保障**

建议添加：
- 翻译一致性检查脚本
- 关键翻译的人工审核流程
- 自动化测试确保翻译完整性

## 📈 性能指标

### 翻译系统性能

- **缓存策略**: 使用 LRU 缓存（256条）减少重复查询
- **加载策略**: 按需加载语言文件
- **回退机制**: 当翻译缺失时自动回退到英文

### 内存占用

- 单个语言文件大小: ~200KB (zh_CN), ~20KB (en_US)
- 建议加载策略: 启动时预加载默认语言

## 🎓 代码示例

### 完整的使用示例

```python
from VoidCube_cli.i18n import init_i18n, t, set_locale, get_available_locales

# 初始化（自动检测语言）
init_i18n()

# 获取可用语言
locales = get_available_locales()
print(f"支持的语言: {locales}")  # ['zh_CN', 'en_US']

# 切换语言
set_locale("zh_CN")

# 使用翻译
print(t("cli.welcome"))
# 输出: VoidCube 就绪。/help 查看命令。

# 带参数的翻译
print(t("errors.file_not_found", path="/tmp/test.txt"))
# 输出: 未找到文件: /tmp/test.txt
```

## 🔍 未来规划

### 短期优化 (1-3个月)
1. ✅ 完成 memory 模块翻译（已完成）
2. 优化翻译键命名规范
3. 添加翻译注释和文档

### 中期规划 (3-6个月)
1. 支持更多语言（日语、韩语等）
2. 翻译版本管理和回滚
3. 社区翻译贡献流程

### 长期愿景
1. 机器翻译辅助人工翻译
2. 实时翻译预览工具
3. 智能翻译建议系统

## 📝 总结

**VoidCube CLI 的多国语言支持已经相当完善，特别是中文翻译覆盖率达到了 100%。** 

项目为中文用户提供了：
- ✅ 完整的界面翻译
- ✅ 丰富的 AI/ML 术语库
- ✅ 良好的本地化体验
- ✅ 灵活的国际化架构

通过本次分析，我们补充了缺失的 memory 模块翻译，进一步提升了 CLI 的中文支持质量。

---
**分析工具**: analyze_i18n.py, analyze_used_keys.py, compare_structure.py
**生成文件**: used_translation_keys.txt, missing_zh_keys.txt, needs_translation.txt
**修改文件**:
- VoidCube_cli/locales/en_US.json (新增 memory 部分)
- VoidCube_cli/locales/zh_CN.json (新增 memory 部分)
