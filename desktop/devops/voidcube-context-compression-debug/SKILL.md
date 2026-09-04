---
name: voidcube-context-compression-debug
description: 诊断 VoidCube 的 context 压缩机制提前触发 / context_length 被判定为 128K 的问题。当用户抱怨"压缩太早"、"context 一直显示 128K"、"到 50% 就压缩"、"自动配置一点用没有"、"startup_probe/adaptive_by_model 无效"时使用。揭示 context_length 解析链路、startup_probe 设计缺陷、adaptive_by_model 为 no-op 的根因，并给出配置覆盖与代码修复两类方案。
tags: [debug, context, compression]
---

# VoidCube Context Compression 诊断

## 触发条件
- 用户反馈压缩太早触发，界面显示 "100% 到 50% (64k) 压缩"、"compaction approaching"
- context 长度一直显示 128K / 始终用 128K 判定
- 抱怨 `startup_probe`、`adaptive_by_model` 等"自动配置一点用没有"
- 模型是非官方 ID（如 `deepseek-v4-flash-vision-exp`），或自定义/中转 endpoint

## 核心结论（先记住根因模式）
**绝大多数情况下 `context_length` 落到 `fallback_endpoint` = 128000，是根因。**
`threshold_tokens = max(context_length * 0.50, MINIMUM_CONTEXT_LENGTH)`，所以 `0.5 × 128000 = 64000` → UI 显示 "64k threshold (50%)"。只要真实模型 context 长度没被解析到，永远按 128K 的 50% 提前压缩。

## 关键文件（Windows 仓库 `F:/My_code/VScode_py/VoidCube/`）
- `src/voidcube/infrastructure/providers/model_metadata.py`（~1095 行）— context 长度解析、探测、缓存
  - `CONTEXT_PROBE_TIERS = [128_000, 64_000, 32_000, 16_000, 8_000]`（约 71-74 行）
  - `STARTUP_CONTEXT_PROBE_LENGTH = 1_000_000`（约 82 行）
  - `DEFAULT_FALLBACK_CONTEXT = CONTEXT_PROBE_TIERS[0]` = 128_000（约 85 行）
  - `probe_endpoint_context_length`（约 540-596 行）
  - `save_context_length` / `get_cached_context_length`（约 599-644 行）— 缓存 key 格式 `f"{model}@{base_url}"`
- `src/voidcube/runtime/agent/context_policy.py`（~216 行）
  - `ContextCompressionPolicy` frozen dataclass：`threshold_percent=0.50`（26 行）、`target_ratio=0.20`（27 行）、`protect_last_n=20`（28 行）
  - `threshold_tokens = max(context_length * threshold_percent, MINIMUM_CONTEXT_LENGTH)`（34 行）
  - `configured_context_length`（120-217 行）— 接受的所有配置 key 结构
- `src/voidcube/runtime/agent/runner.py` — 运行时读取压缩配置（约 732-763 行）与触发点
- `src/voidcube/runtime/agent/tool_turn.py` — 轮末压缩检查（约 194-214 行）
- 用户实际配置：`$VOIDCUBE_HOME/config.yaml`（即 `~/.VoidCube/config.yaml`），非仓库模板！
- 缓存：`$VOIDCUBE_HOME/context_length_cache.yaml`

## 诊断步骤

### 1. 读用户真实配置，不是仓库模板
```bash
echo "VOIDCUBE_HOME=$VOIDCUBE_HOME"; echo $HOME
ls -la ~/.VoidCube/ 2>/dev/null
```
注意：`search_files` 搜 `C:/Users/.../config.yaml` 常返回 0（权限/索引限制），必须用 `terminal` + `ls` 定位用户配置文件。

### 2. 读 context 缓存，确认是否缺模型
看 `~/.VoidCube/context_length_cache.yaml` 里是否有目标模型；没有说明检测从未成功回写。

### 3. 追踪 get_model_context_length 的解析链路
需确定 `source`（config / persistent_cache / endpoint_metadata / local_probe / startup_probe / fallback_endpoint / fallback）。对远程 OpenAI 兼容端点，关键分支在 `_is_custom_endpoint`：
- 不是 OpenRouter → 走 `_is_custom_endpoint=True`
- OpenRouter 在线 `/models` 返回 `context_length`；DeepSeek 官方 `/models` 只返回 `{id, object, owned_by}`，**无 context_length**
- 不是 local → 跳过本地探测

### 4. startup_probe 为何必然失效（设计缺陷）
`probe_endpoint_context_length` 发请求带 `max_tokens: STARTUP_CONTEXT_PROBE_LENGTH`（= 1,000,000）。
- 对任何输出上限远小于 1M 的 provider，请求被拒，error 含 "max_tokens" 但不含 "context"
- 命中 model_metadata.py 约 587-592 行：
  ```python
  if "context" not in error_lower and ("max_tokens" in error_lower or "max completion" in error_lower or "output token" in error_lower):
      return None   # 设计上认为"输出上限错误"说明不了输入窗口 → 返回空
  ```
- 于是 probe 逻辑上**自相矛盾**：对输出受限的 provider 永远返回 None → 落入 fallback 128K。

### 5. adaptive_by_model 是纯 no-op
- runner.py 约 733 行：`str(_compression_cfg.get("adaptive_by_model", False)).lower() in ("true", "1", "yes")` 只赋值
- `context_policy.py` 里只在 `as_dict()` 序列化，**没有任何代码读它去改变 threshold_percent 或行为**
- config 注释本身就是 "reserved for opt-in model-specific tuning"（未实现）。

## 修复方案

### 方案 A（快速、推荐首选——改用户 config，覆盖 context 长度）
在 `~/.VoidCube/config.yaml` 加：
```yaml
providers:
  deepseek-v:
    base_url: https://api.deepseek.com/v1
    model_context_lengths:
      deepseek-v4-flash-vision-exp: 131072
```
或全局：`agent.model.context_length: 131072`。

`configured_context_length` 支持的结构（180 行附近）：
- `providers.<provider>.model_context_lengths`（map，canonical）或 `context_lengths`
- `providers.<provider>.models.<model>.context_length` 或 `models.<model>` 标量
- `providers.<provider>.model_catalog.models[]`（按 `id`/`name`/`model` 匹配）
- `providers.<provider>.model_capabilities.<model>`
- `_context_value_from_mapping` 键名：`context_length`、`context_window`、`context_window_size`、`max_context_tokens`、`max_context_length`、`max_model_len`、`max_input_tokens`、`input_token_limit`、`prompt_token_limit`
- 值支持 int 或 `"128k"`/`"131072"`/`"64M"` 字符串（正则 `(\d+(?:\.\d+)?)([kKmMbB])?`）

### 方案 B（改代码，需跑集成扫描+测试）
1. 修 `probe_endpoint_context_length`：用小输出预算（落在 provider 输出上限内）代替 1M
2. 为已知 provider 加内置 context 表（如 `_resolve_deepseek_context_length` 仿 `_resolve_nous_context_length`）
3. 删除或真正实现 `adaptive_by_model`
4. 可选：把 `source`（如 `fallback_endpoint`）透出到 UI，让用户看出是估算还是真实

### 重要警告
`estimate_request_tokens_rough` 会统计 tool schema——几十个启用的 toolset 可能吃掉 20-30K token。**仅调大 threshold 可能不够**，若用户启用工具多需一并考虑。

## 陷阱 / Pitfalls
- 仓库 `F:/.../VoidCube/config.yaml` 只是基线模板，不是用户实际运行配置；真实配置在 `$VOIDCUBE_HOME/config.yaml`（`C:/Users/<user>/.VoidCube/`）
- 非官方模型 ID（如 `deepseek-v4-flash-vision-exp`，官方是 `deepseek-chat`/`deepseek-reasoner`）无法在公开文档查到准确 context 长度 → 需用户确认，填错会导致真实超出被 API 拒
- `search_files` 跨盘符搜用户家目录常失效，用 `terminal` 的 `ls`/`echo`

## 验证
- 改 config 后重启，检查 `context_length_cache.yaml` 是否写入 `f"{model}@{base_url}"`
- 查看 UI 显示的 threshold 是否从 "64k(50%)" 变为真实值对应比例
- 确认不再在 64K 处触发压缩
