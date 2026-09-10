---
name: voidcube-context-compression-debug
description: 诊断 VoidCube 的 context 压缩机制提前触发 / context_length 被判定为 128K 的问题。当用户抱怨"压缩太早"、"context 一直显示 128K"、"到 50% 就压缩"、"自动配置一点用没有"、"startup_probe/adaptive_by_model 无效"时使用。揭示 context_length 解析链路、startup_probe 设计缺陷、adaptive_by_model 为 no-op 的根因，并给出配置覆盖与代码修复两类方案。
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

**但必须区分两种情况（这次实测的关键补充）：**
1. **config 已有覆盖值时，不会落 128K。** 只要 `providers.<p>.model_context_lengths[model]` 存在，runner 会用 `configured_context_length()` 读到它并作为 `config_context_length` 传入 → `get_model_context_length` 步骤0 命中 → `source='config'`，`detection_known=True`。**`get_model_context_length` 本身不会去读 config 的 `model_context_lengths`，它只认 `config_context_length` 参数**——读 config 这步是 runner 在调 `for_model` 之前用 `configured_context_length` 完成的。
2. **真正的自动识别对非官方实验模型往往全部失效。** `deepseek-v4-flash-vision-exp` 这类自定义 ID：端点 /models 无 context_length、startup_probe 因 max_tokens=1M 被拒返回 None、本地探测不适用 → 最终仍落 128K（fallback_endpoint）。所以 config 里出现的 `1000000 / 0.70 / 0.12 / 30` 这样的组合，**极可能是用户执行过 `/context 1M` 手动写入的，而非自动识别结果**——不要误判为"自动识别已生效"。

## /context 命令写入特征（识别手动覆盖）
`/context <128K|256K|512K|1M>` 命令（`src/voidcube/interfaces/cli/commands/handlers/context.py` + `registry.py`）会调用 `save_context_length(provider, model, context_length)`：
- 用 `ContextCompressionPolicy.recommended_settings(context_length)` 自动生成推荐 profile（1M → `{threshold_percent:0.70, target_ratio:0.12, protect_last_n:30}`；512K→{0.65,0.14,28}；128K→{0.50,0.20,20}）。
- 写入 config：`providers.<active_provider>.model_context_lengths[model]` 和 `context.compression_profiles.<active_provider>[model]`。
- **签名特征**：若 config 里 `model_context_lengths`/`compression_profiles` 的 `threshold_percent`/`target_ratio`/`protect_last_n` 与 `recommended_settings(len)` 的返回值精确吻合，就说明该值是 `/context` 手动写入，不是自动识别。

## 关键文件（Windows 仓库 `F:/My_code/VScode_py/VoidCube/`）
- `src/voidcube/infrastructure/providers/model_metadata.py`（~1095 行）— context 长度解析、探测、缓存
  - `CONTEXT_PROBE_TIERS = [128_000, 64_000, 32_000, 16_000, 8_000]`（约 71-74 行）
  - `STARTUP_PROBE_OUTPUT_BUDGET = 1024`（约 82 行）——已从 1M 调小，避免撞 provider 输出上限（2026-09-06 修复）
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
`probe_endpoint_context_length` 现已改为**级输入窗口探测**：用小输出预算 `STARTUP_PROBE_OUTPUT_BUDGET`（=1024）+ 分级长输入（`CONTEXT_PROBE_TIERS`），成功返回被接受的最大档，不再用输出上限当窗口（2026-09-06 已修复）。
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

### 6. 决定性验证：venv 探针脚本（确认到底用哪个值/source）
判断阶段 3 的解析结果不能只靠猜，用项目 venv 实跑最可靠：
```python
# 临时脚本，用 ./.venv/Scripts/python.exe 跑（Windows）
import sys, yaml
sys.path.insert(0, r"F:/.../VoidCube/src")
from voidcube.runtime.agent.context_policy import configured_context_length, ContextCompressionPolicy
from voidcube.infrastructure.providers.model_metadata import get_model_context_length
cfg = yaml.safe_load(open(r"C:/Users/<user>/.VoidCube/config.yaml", encoding="utf-8"))
# ① config 是否被读到
print(configured_context_length(cfg, provider="deepseek-v", model="deepseek-v4-flash-vision-exp", base_url="https://api.deepseek.com/v1"))
# ② config_context_length 覆盖 vs None（None 会落多少）
print(get_model_context_length(m, base_url=b, config_context_length=1_000_000, provider=p, with_source=True, startup_probe=True))
print(get_model_context_length(m, base_url=b, config_context_length=None, provider=p, with_source=True, startup_probe=True))  # 预期 (128000,'fallback_endpoint')
# ③ 最终策略
print(ContextCompressionPolicy.for_model(m, threshold_percent=0.70, target_ratio=0.12, protect_last_n=30, base_url=b, config_context_length=1_000_000, provider=p, startup_probe=True).as_dict())
```
关键判读：`get_model_context_length` 会按 base_url 匹配兜底（`_is_custom_endpoint`），用 provider 名匹配不到也能靠 base_url 命中 config entry。测试后删除临时脚本。

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
1. 修 `probe_endpoint_context_length`——**已完成（2026-09-06）**：改用小输出预算 `STARTUP_PROBE_OUTPUT_BUDGET`=1024 + 分级输入（`_build_probe_input` 按 `CONTEXT_PROBE_TIERS` 构造长输入），直接从输入侧探测窗口；输出上限错误只降档不入库，不再误判。
2. 为已知 provider 加内置 context 表（如 `_resolve_deepseek_context_length` 仿 `_resolve_nous_context_length`）
3. 删除或真正实现 `adaptive_by_model`
4. 可选：把 `source`（如 `fallback_endpoint`）透出到 UI，让用户看出是估算还是真实

### 辅助压缩模型窗口不足（压缩器不可行告警）
另一种独立场景：启动时报 "Compression model (...) context is X tokens, but the main model's compression threshold is Y tokens. Context compression will not be possible — the content to summarise will exceed the auxiliary model's context window."
- 位置：`runtime/agent/runner.py` 约 1428 行 `if aux_context < threshold`。
- 根因：`aux_context = get_model_context_length(aux_model, config_context_length=auxiliary.compression.context_length)`。若 config 未设 `auxiliary.compression.context_length`，则步骤0 config override 跳过 → 端点 /models 无 context_length → 落 128K 兜底 → 判定 128K < threshold。
- 触发规律：主模型 context 越大（threshold = threshold_percent × context 越高）越易触发；辅助压缩模型默认 auto 会解析到主模型，窗口本应与主模型一致，却因缺 context_length 被误判成 128K。
- 修复：在 `~/.VoidCube/config.yaml` 的 `auxiliary.compression` 段加 `context_length: <主模型窗口>`（如 `1000000`）。`get_model_context_length` 步骤0 命中 → aux_context 与主模型一致 → 校验通过。备选：降顶层 `compression.threshold`（不推荐，会浪费主模型窗口）。
- 坑：config.yaml 通常为 CRLF，用 patch 工具改这一段会使其行尾变 LF（造成文件内混合行尾），`yaml.safe_load` 仍可正常解析，且该 config.yaml 在 `~/.VoidCube/` 不在 git 仓库内，无 git diff 困扰。

### 重要警告
- `estimate_request_tokens_rough` 会统计 tool schema——几十个启用的 toolset 可能吃掉 20-30K token。**仅调大 threshold 可能不够**，若用户启用工具多需一并考虑。
- **「过晚压缩」比「提前压缩」更危险**：若 config 里 `model_context_lengths`/`/context` 被设成远大于模型真实窗口的值（如真实只有 128K，却被设成 1000000），压缩要到 `0.7×1000000=700K` 才触发 → 在模型真实上限处直接爆 → 请求被 API 拒、报 context 超限。**必须确认模型真实窗口**，或保守设为略低于真实窗口。对非官方实验 ID，宁可配小一点、触发早一点，也不要配 1M 导致根本不压缩。

### 7. startup_probe 成本权衡：为什么不要加更高档（用户强烈关心 token 消耗）
探测本质是**发长输入试探**，成本随目标档位线性上涨。用户明确反对「每次启动烧大量 token」，结论：
- **startup_probe 不是每次启动都跑。** 只有当 config 未配(`config_context_length`=None) + 持久缓存 miss + 端点 /models 元数据拿不到 + 非本地端点 时才会触发；config 已配的模型（如 deepseek 的 `model_context_lengths`）会在步骤0 短路，**零探测成本**。
- **探测成功会写缓存。** `probe_endpoint_context_length` 成功时 `save_context_length(model, base_url, len)` 写入 `context_length_cache.yaml`，下次启动命中 `persistent_cache`，不再探测——所以正常模型只探测一次；只有「识别失败的模型」每次启动都会重试（当前为小探测，成本可控）。
- **加高 CONTEXT_PROBE_TIERS 档位是坏主意（尤其对中等窗口模型）。** 若档位改成 `[1M,512K,256K,128K...]`，真实窗口 128K 的模型会 1M→512K→256K→128K 一连串大输入反复被拒（浪费 4 次大请求）；对 1M 模型虽一次命中但单次就发约 1M token 输入。**保持 128K 轻量下界是最佳平衡**——一次探测给「至少能装 128K」的有用下界，既指导压缩策略不崩又可控成本。
- 精确窗口（如 deepseek 1M）交给 config / `/context` 手动指定，零探测成本且最准；探测永远只是「兜底下界」。若想治「识别失败模型每次启动重试」，可给 probe 加失败冷却 TTL（可选优化，不影响 config 短路路径）。

### 8. /context 持久化闭环已实测确认（用户依赖手动方案，必须可靠）
`/context <128K|256K|512K|1M>` 写入后**能真正持久化到 config 文件**，重启自动生效：
- 链路：`handle_context_command` → `save_context_length(provider, model, len)` → `save_config()` → `atomic_yaml_write` **原子写盘**到 `$VOIDCUBE_HOME/config.yaml`。
- 写入两块：`providers.<active_provider>.model_context_lengths[model]=len` 和 `context.compression_profiles.<active_provider>[model]=recommended_settings(len)`。
- 端到端隔离实测（设临时 `VOIDCUBE_HOME` 指向 mktemp，不污染真实配置）：写盘→`load_config` 重新读回能带出 `model_context_lengths` 与 `compression_profiles` 精确值→`configured_context_length` 读到 `1000000`→`ContextCompressionPolicy.for_model` 得 `source='config'`、`threshold_tokens=700000`。闭环成立。
- **provider 名兜底匹配**：写入用 `runtime.active_provider`（`deepseek-v`），读取 `configured_context_length` 有 **base_url 兜底**——`get_model_context_length` 的 `_is_custom_endpoint`/`configured_context_length` 即使 provider 名显示成 `deepseek`/`dsv` 也能靠相同 base_url 命中同一 entry。所以重启读取不会因 provider 名细微差异而失效。（实测 `deepseek-v`/`deepseek`/`dsv` 三个名都读到 `1000000`。）
- 隔离验证技巧：`get_config_path()` 每次读 `os.getenv("VOIDCUBE_HOME")` 无缓存，设临时 env 即可完全隔离写读，避免污染真实 `~/.VoidCube/config.yaml`。

## 陷阱 / Pitfalls
- 仓库 `F:/.../VoidCube/config.yaml` 只是基线模板，不是用户实际运行配置；真实配置在 `$VOIDCUBE_HOME/config.yaml`（`C:/Users/<user>/.VoidCube/`）
- **先核实模型真实窗口，不要凭「非官方 ID」就断言查不到。** 曾误判 `deepseek-v4-flash-vision-exp` 为非官方/不可验证的实验 ID——实际 DeepSeek 官方文档明确发布该模型：**上下文 1M、输出上限 384K**（用户提供官方截图证实）。所以 config 里 `model_context_lengths: 1000000` 对该模型**是正确值**，不是「过度配置」。教训：先查 provider 官方模型页/价格页，再下结论；对真正无法验证的 ID 才降级为保守配置。
- `search_files` 跨盘符搜用户家目录常失效，用 `terminal` 的 `ls`/`echo`

## 验证
- 改 config 后重启，检查 `context_length_cache.yaml` 是否写入 `f"{model}@{base_url}"`
- 查看 UI 显示的 threshold 是否从 "64k(50%)" 变为真实值对应比例
- 确认不再在 64K 处触发压缩
