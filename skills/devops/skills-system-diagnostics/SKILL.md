---
name: skills-system-diagnostics
description: 实测 VoidCube 技能子系统（发现/缓存/调用机制）并评估架构改动价值。当用户想评估"技能库/技能存数据库/技能索引"这类架构决策、或怀疑技能发现/快照缓存有 bug、或需要量化技能系统性能时使用。通过探针脚本直接调用真实代码路径测量，而非猜测。
---

# VoidCube 技能子系统诊断

技能系统 = Agent 的程序性记忆。诊断目标：量化发现/缓存/调用机制的真实成本，定位缺陷，为架构决策（如"技能存数据库"）提供实证依据。

## 触发条件
- 用户讨论技能库/技能入库/技能索引化等架构决策
- 怀疑技能发现机制有 bug（如目录解析错误、技能看不到）
- 需要量化技能扫描/缓存性能

## 核心文件（F:/My_code/VScode_py/VoidCube）
- `src/voidcube/extensions/skills/catalog.py` (467行) — 目录解析、frontmatter 解析、平台匹配
- `src/voidcube/extensions/skills/tool.py` (1255行) — `_find_all_skills`(418)、`skills_categories`(540)、`skills_list`(622)、`skill_view`(693)
- `src/voidcube/runtime/agent/prompt_builder.py` — `build_skills_system_prompt`(533，注意不是 `_build_skills_prompt`)、快照机制
- `src/voidcube/extensions/skills/{manager,guard,hub,sync}.py` — 管理/安全/安装

## 架构三要点（先读这些再动手）
1. **发现**：`get_all_skills_dirs()` = 本地 `~/.VoidCube/skills` → repo bundled → config external。每个 SKILL.md 的 YAML frontmatter 提供 name/description/platforms，缺失时回退正文首行，描述截断 1024 字符。
2. **调用**：三级渐进披露省 token——`skills_categories`(分类) → `skills_list`(名+描述) → `skill_view`(全文+关联文件)。LLM 靠系统提示词 `<available_skills>` 清单语义判断，命中后 skill_view 按需加载。
3. **缓存**：进程内 LRU(8条) + 磁盘快照 `~/.VoidCube/.skills_prompt_snapshot.json`(v2，manifest 指纹校验)。manifest = {相对路径: [mtime_ns, size]}。

## 诊断步骤（探针法）
1. **写探针脚本**调用真实代码路径测量（不要 grep 猜），放 `.test-tmp/` 下：
   - `sys.path.insert(0, 'src')` 后 import `voidcube.extensions.skills.*`
   - 测：目录解析结果、技能数、`_find_all_skills()` 耗时、manifest 构建耗时、冷路径（删快照）vs 热路径提示词构建耗时、skill_view 定位耗时
2. **测量失效粒度**：`os.utime(SKILL.md, None)` 只改 mtime 不改内容 → 重建 manifest 比较是否变化
3. **验证路径真相**：打印 `get_repo_skills_dir()` 实际返回值，与期望路径比对

## 已确认的已知缺陷与修复（2026-08 实测，46~48 技能规模）
1. ~~路径 bug~~ **已修复（2026-08-20）**：原 `catalog.py` 的 `Path(__file__).resolve().parent.parent` 从 `src/voidcube/extensions/skills/` 上溯两级只到 `src/voidcube/extensions`，导致 `get_repo_skills_dir()` 返回**自身目录**（`is_dir()` 恒真），顶层仓库 `skills/` 目录永远不会被扫描，仓库 bundled skills 与 home 技能完全割裂。修复：改为 `parents[4]`（= 仓库根，与 `sync.py:_bundled_dir()` 一致），并顺带修复**同模式两处**：
   - `hub.py:2056` `parent.parent` → `parents[4]`（optional-skills 推导错位到 `src/voidcube/extensions/`）
   - `browser_tool.py:1057` `parent.parent` → `parents[5]`（node_modules/.bin 推导错位；该文件比 skills/ 深一层）
   - 修复后实测：仓库顶层 `skills/` 32 个 SKILL.md 进入发现域，新增可见技能 `vector-databases`，去重后共 48 技能（31 个同名由 home 优先覆盖，语义不变）。
2. ~~快照失效粒度过粗~~ **已实施修复（2026-08-20，用户实施阶段 1-3）**：注册中心 `src/voidcube/extensions/skills/registry.py`（SQLite，354 行）取代 JSON 快照。`refresh_registry` 用 content_hash 增量（重解析只发生在内容变化时）、`upsert_skill` 保留生命周期覆盖、`refresh_and_query` 过滤失效根。写路径三处（hub/sync/manager）调用 `refresh_catalog_index`；CLI `clear_skills_system_prompt_cache()` 去掉了 `clear_snapshot` 参数。提示词主路径 = LRU → registry → 文件系统扫描（try/except 降级）。
3. **根级技能无分类**：直接放 `~/.VoidCube/skills/` 根下的技能（如 self-learning、media-display-testing、windows-automation）`category=None`，提示词里显示为 None。⚠️ 注册中心实施后此语义**发生漂移**（见下方审计清单 #1）——旧逻辑顶层技能 category = 目录名，registry `_category_for` 返回 None → 提示词里并入 `general`。

## 注册中心实施后的验收审计清单（2026-08-20 实证，可复用）
对"文件系统扫描 → 数据库索引"这类重构，改完**必须**做以下验证，本次实施逐条踩中：

1. **主路径与 fallback 语义一致性（最容易忽略）✅ 已解决，方向与初判相反**：registry 的 `_category_for` 对 `len(parts)==1` 返回 None（顶层技能 → 提示词 `or "general"`），旧快照逻辑则返回 `parts[0]`（目录名）。用户最终选择**不是**改回 `parts[0]`，而是把 fallback `_build_filesystem_entry`（prompt_builder.py）也统一为 `general`（注释明确这是有意的 prompt-only 展示标签），并新增测试 `test_root_skill_category_matches_registry_when_fallback_is_used` 锁死主/降级一致性。结论：顶层技能（self-learning/media-display-testing/windows-automation）永久归 `general`，属有意语义变更，红线 2（输出格式只含 name/description/category）未破坏。教训：**"不一致"的修复方向要交给用户裁决——统一到哪一侧是产品决策，不是技术正确性问题。**
2. **全量测试必须跑，改 API 必同步测试 ✅ 已解决**：终审时 `tests/test_prompt_builder_skills_cache.py` 已重写为 registry 语义（monkeypatch `_skills_registry_path` 到 tmp_path、验证 registry 是主数据源、验证 fallback 分类与 registry 一致），3 个测试全过；旧符号 `_skills_prompt_snapshot_path`/`_SKILLS_SNAPSHOT_VERSION` 引用已清除。终审状态：23 passed（skills/web/registry/runtime_paths 4 文件）+ 8 passed（打包契约）。
3. **性能回退无声 ✅ 已解决（P1，2026-08-20 完成）**：原 `refresh_registry` 每次全量 `read_bytes + sha256` 所有文件（无 mtime/size 快速跳过），终审实测热调用 56.4 / 63.1 / 138.7 ms vs 旧快照热路径 3.6ms（慢 15~38 倍）。P1 修复后热调用 **23.4 ms（-61%）**，读+哈希彻底移除。优化手段与 Windows 性能坑详见下方"SQLite 热路径优化（Windows 实测）"小节。教训：**性能回退无声是因为无基准守护**——修复性能问题时同步考虑固化宽松阈值基准（如 <100ms，勿设精确值防跨机抖动误报）；LRU 会掩盖热路径问题，LRU miss 时才暴露。
4. **无关文件被误删**：`git status` 看到 ARCHITECTURE.md 处于未提交删除状态——与本次实施无关，需向用户确认。
5. **AGENTS.md 规则可能被放宽**：本次为保留 fallback 增加了"兼容为回退拖地方案可保留"条款——属于合理放宽，审计时注意识别。

## SQLite 热路径优化（Windows 实测，P1 已验证，可复用）
对"文件系统扫描 → SQLite 索引"类设计做热路径优化时，方法论与 Windows 实测坑：

1. **先分解后优化（最重要）**：不要盲目改。分步计时每个环节（遍历/SQL/连接/解析）
   定位真实瓶颈。本次首轮优化（scandir+批量 SQL）后性能几乎没降——真正的大头是
   每文件 `Path.resolve()`（Windows 每次 ~0.15ms，79 文件 ~12ms，比 SQL 还贵），
   只有逐环节计时才发现。
2. **Windows 性能坑（实测数值，79 文件规模）**：
   - `Path.resolve()` ×79 ≈ 12.3ms ← 最隐蔽的大头；scandir 基于已 resolve 的 root
     构造路径，本身就是规范绝对路径，直接 `str(path)` 无需再 resolve
   - pathlib `rglob` 全树遍历 ≈ 18ms；`os.scandir` 手动递归 ≈ 13ms（省 5ms，行为等价：
     不进入符号链接、排除目录相同、结果按相对路径排序）
   - SQLite 连接建立 ≈ 5ms/次 → 读路径合并为单连接（refresh+query 各开一次 = 白丢 5ms；
     `refresh_registry` 加可选 `connection` 参数，外部传入时不自行关闭）
   - 逐行 SELECT ×79 ≈ 15ms → 每 root 一次批量 SELECT + dict 化 ≈ 0.5ms
3. **快速路径模式**：stat 先行，`mtime_ns+size` 与库中一致则跳过 read+hash（内容未变时
   哈希开销归零）。老记录 mtime 为 NULL 时 `int()` 抛 TypeError 被 except 捕获 →
   自然落入慢路径读内容比对 hash 后 UPDATE 补齐 = 零成本向前兼容（优雅降级）。
4. **行为等价验证（替换遍历/路径逻辑后必做）**：
   - 路径字符串一致性：scandir 路径 == resolve 路径 == 库中已存 file_path——不一致
     会导致 known dict 全 miss 退回慢路径，得到"假优化"（先验证再测速）
   - 四类检测：改内容→reparsed=1、还原→再次检测到、删文件→removed=1、恢复→added=1
   - 主调用方走通：`build_skills_system_prompt()` 正常输出
5. **设计取舍认知**：旧快照 3.6ms 是"零文件系统访问"信任模式，与"文件系统=对账基准"
   契约互斥；对账成本（scandir ~13ms）是契约固有下限。性能目标必须与设计契约对齐，
   不能拿契约外基准（3.6ms）当回归目标——向用户说明"为什么回不到 3.6ms"是必要交付。
6. **防回归基准测试（P1 后补，双测试互补，2026-08-20 实证）**：
   - 行为级强守卫（机器无关，比计时可靠）：monkeypatch `Path.read_bytes` 抛
     RuntimeError，热刷新仍应 reused=1——若有人回退到无条件读文件内容立即炸测试。
     注意慢路径唯一读文件点是 `refresh_registry` 里的 `skill_file.read_bytes()`
     （`_record_from_file` 接收 bytes 参数不读文件），patch 一个点即可全覆盖
   - 宽松计时阈值：30 文件热刷新 < 100ms，只防秒级退化（全量重读/重解析），
     不设精确值防跨机器抖动误报
   - 守卫有效性双向验证（防"测试形同虚设"）：`os.utime` touch 文件强制 mtime
     变化走慢路径 → 守卫应触发拦截；随后热路径 → 不应触发

## 路径推导修复范式（可复用）
仓库根相对路径推导错误时，用"parents[N] 阶梯法"定位正确层级：
```python
from pathlib import Path
f = Path('<目标文件>').resolve()
for i in range(7): print(f'parents[{i}]:', f.parents[i])
# 找到等于仓库根的 parents[N]，再验证其下目标目录 is_dir()
```
- `src/voidcube/extensions/skills/*.py`（catalog/hub/sync）→ `parents[4]` = 仓库根
- `src/voidcube/extensions/tools/browser/*.py` → `parents[5]` = 仓库根
- 修复后**必须 grep 兄弟实例**：`grep -rn "parent\.parent" src/voidcube/extensions/`（同一错误模式常被复制到多处）
- 验收：compileall + `tests/test_skills_and_web_local.py tests/test_skills_sync_contract.py tests/test_prompt_builder_skills_cache.py tests/test_runtime_paths.py`（21 个）+ 打包契约 8 个 + `scripts/build_wheel.py` 退役扫描（AGENTS.md 规则 3）

## 终审复查流程（用户实施完成后，可复用）
当用户宣布"完成了"且改动已提交（`git status` 干净），复查必须基于 git 历史而非工作区 diff：

1. **确认提交**：`git log --oneline -8` + `git show --stat HEAD`——核对 registry.py 新建、prompt_builder 大改、测试改动是否都进了一个提交（本次 6c6acdf：594+/320-，10 文件）。
2. **验证实施前三疑点**（见上审计清单）：分别用"实测输出对比"（`build_skills_system_prompt()` 看分类）、"跑全量测试"（门禁）、"计时探针"（热路径）验证，而不是静态读码下结论。
3. **阶段 4/5 完成度核验**：生命周期 API 是否真的接线——`grep -rn "set_lifecycle_metadata" src/voidcube/ --include="*.py"` 找调用方（本次：manager.py lifecycle action 内先 `refresh_catalog_index` 再 `set_lifecycle_metadata`，异常降级不崩溃 = 接线质量好）；阶段 5 退役扫描 = `scripts/build_wheel.py --outdir .test-tmp/...` 验证零入口。
4. **registry 数据规模核验**：79 条 = home 47 + repo 32 全量入库（不预先去重），同名技能在查询层按 priority 裁决——这是设计使然，不是 bug。
5. **判断报告结构**：确认事实（提交/编译/测试/退役扫描逐条）→ 三疑点逐条结论（解决/未解决+证据）→ 对用户核心判断的实证（性能回退无声=无基准守护、人工兜底有效=人主导+agent 分析是当前最优解）→ 残留问题按 P1/P2/P3 排序 → 下一步建议。用户只要判断时**不主动改代码**。

## 实测基准（2026-08，46 技能/2 目录）
旧 JSON 快照时代（已废弃，仅作对照）:
- 全量扫描 `_find_all_skills()`：182.5 ms
- manifest 构建：8.3 ms
- 冷路径提示词构建：37.2 ms（5470 字符输出）
- 热路径（LRU 命中）：3.6 ms
- skill_view 定位：57.1 ms

注册中心时代（2026-08-20 实测）:
- 冷 `refresh_and_query`：201.0 ms（79 条入库）
- 热调用（P1 前）：56.4 / 63.1 / 138.7 ms（内容未变仍全量 read+sha256；138ms 疑反病毒干扰）
- 热调用（P1 后）：23.4 ms（6 次均值 22.0~26.1，读+哈希开销为 0，reused=79）
- registry 文件：~90 KB，79 行 = home 47 + repo 32 全量（去重在查询层）
- 测试基线：44 passed（skills/web/registry/runtime_paths/打包契约 合并跑，含
  test_skill_registry.py 的 2 个热路径防回归守卫）+ wheel 退役扫描通过

## 价值评估结论（重要教训）
性能不足以成为架构改动的理由——37ms→3.6ms 在 LLM 调用面前是零头。**真正的价值在发现域一致性**：注册中心可修复路径 bug、补边界声明（category/deprecated/supersedes）、用精确匹配替代 rglob 模糊查找。先量化，再下结论，别预设"优化性能"这个动机。

## 陷阱
- **提交 tests/ 新文件会被 git 拒绝**：`.gitignore` 的 `/tests/*` 是准入制（仅豁免清单内
  文件可入库），新测试必须先加 `!/tests/test_xxx.py` 到豁免清单再 `git add`，否则报
  "paths are ignored"。已跟踪文件不受影响（gitignore 只管未跟踪）。豁免清单现状
  （2026-08-20 已补齐）：tests/ 10 个文件全部入库——豁免清单登记 8 个
  （含 P1 全部相关测试），另 2 个（test_auxiliary_infrastructure /
  test_logging_no_console_handler）是规则加入前的历史跟踪。新测试入库流程：
  验证通过 → 追加豁免 → git add → 提交。清单与 git ls-files 应保持一致，
  不一致即视为遗留问题
- **AGENTS.md 规则 5（2026-08-20 新增，强制流程）**：涉及技能发现、索引或
  registry 机制的改动，提交前必须运行 `pytest tests/test_skill_registry.py`
  （热路径强守卫 + 耗时基准），核对 added/reparsed/reused/removed 统计；
  性能回归不得靠临时脚本，一律以入库测试为准
- `build_skills_system_prompt` 无下划线前缀；探针脚本先 grep 函数名再 import，避免 ImportError
- 分类排序会因 `None` 值抛 TypeError（`sorted(categories.items())`）——用 `key=lambda x: str(x[0])`
- 探针 import 路径：必须 `sys.path.insert(0, 'src')`，用 `.venv/Scripts/python.exe` 跑（Windows git-bash）
- `search_files` 在仓库根目录会超时（60s），用 terminal 的 `grep -n` 代替
- 测试用项目虚拟环境 `.venv/Scripts/python.exe`（AGENTS.md 规则 4）

## bundled 技能同步机制与漂移排查（实测 2026-09-10）

同步入口：`src/voidcube/extensions/skills/sync.py::sync_skills`，每次 CLI 启动执行
（调用点 `interfaces/cli/entrypoints/session.py`，约 460 行）。

- 真源：**仓库 `skills/`**（`_bundled_dir()`，可用环境变量 `VOIDCUBE_BUNDLED_SKILLS` 覆盖）
- 目标：`~/.VoidCube/skills/`
- 状态文件：`~/.VoidCube/skills/.bundled_manifest`，格式 `name:md5(dir)`；
  `name` 取 SKILL.md frontmatter 的 `name`（**不是目录名**，例如目录 `mlops/inference/vllm` 的 name 是 `serving-llms-vllm`）

### 同步分支（必须理解，否则会误判"技能没同步"）
1. `name` 不在 manifest：目标不存在 → 复制；目标已存在 → 跳过（不改动），随后登记 source hash
2. `name` 在 manifest 且**目标目录不存在** → 只 `skipped += 1`，**永不恢复**（静默丢失，实锤缺口）
3. `name` 在 manifest 且 `hash(目标) != origin` → 判定 `user-modified`，**永远跳过**（此后仓库更新不再下发）
4. 其余：`source_hash != origin` 才更新（先备份 `.bak` 再覆盖）

### EOL 陷阱（本机实测，最容易被忽略）
`.gitattributes` 规定 `*.md` / `*.py` 为 `eol=lf`，但 Agent 在 Windows 上写文件常落 CRLF。
`_hash()` 读的是**原始字节** → CRLF 副本与 LF 基线 hash 必然不同 → 被永久误判为 `user-modified`。
修法：两侧统一归一化为 LF，并让 manifest 与之一致。

### Agent 自改 bundled 技能的副作用
用 `skill_manage` 改的是运行时副本，改完 hash 与 manifest 不符 → 之后仓库侧的更新**再也不会同步到运行时**。
正确做法：改完**同时**把仓库副本一起更新，并重算 manifest 条目（保持 repo == runtime == manifest 三者一致）。

### 漂移审计（只读，应长期分叉数为 0）
逐项比对 manifest 中每个 name 的 repo hash / runtime hash，输出 `NOT_IN_RUNTIME` / `DIVERGED` / `MISSING_IN_REPO`；
hash 必须用与 `sync.py::_hash` 完全相同的算法（`sorted(rglob('*'))` → 先相对路径字节、再文件字节）。
另需单独统计 manifest 与仓库的集合差，确认 bundled 齐全。

### 实测结果（2026-09-10）
- 修复前：1 处真损坏（`mlops/vector-databases` 运行时缺 SKILL.md，同步永不恢复）
  + 3 处漂移（1 处纯 EOL 假象、2 处真实内容差异）
- 修复后：分叉 0；仓库 55 / 运行时 58（运行时多出的是未入库的个人/Agent 使用约定技能）

## 对账与入库操作流程（2026-09-10 P7-B 实证，可复用）

适用场景：运行时技能与仓库 `skills/` 交付不一致——技能只在运行时、仓库有而运行时缺、
或两侧 hash 漂移。

### 0. 分类：先定边界，再动手（这是产品决策）
把运行时独有技能分三类，**分类结果必须让用户拍板**，不要自行决定哪些"值得"入库：
- 产品能力技能 → 入库仓库（随仓库/轮子交付）
- 个人工作流 / Agent 使用约定 → 留运行时，**不入库**
- 与既有技能重复 → 不重复入库；用
  `skill_manage(action='lifecycle', deprecated=True, supersedes=<保留的技能名>)`
  让旧技能指向新技能
本次实测：23 个运行时独有 → 20 入库 / 2 留运行时 / 1 弃用
（agent-context-optimization 是 context-optimization 的子集）。
判定重复**必须先 diff 两份 SKILL.md**：子集关系常是同一主题的早期版本，
且旧版本往往带着已过时的流程章节（本次旧版还留着"手工复制到 worktree"的废弃流程）。

### 1. 入库前做只读审计（不要凭目录名猜）
审计脚本输出三段（缺一不可）：
- A. manifest ↔ 仓库集合差 → bundled 是否齐全
- B. 逐项比对 repo hash / runtime hash vs manifest → `DIVERGED` / `NOT_IN_RUNTIME`
- C. 运行时独有（不在 manifest）→ 就是候选清单
hash 必须复刻 `sync.py::_hash`（排序 rglob 全部文件 → 先相对路径字节、再文件字节），
否则全部结论无效。

### 2. 复制入库：三重防护
- **name 冲突断言**：先算 `{frontmatter name}` 集合，与仓库现有技能重名则立即中止
  （重名会让 manifest / index 互相覆盖）
- **逐字节复制**（`shutil.copytree`），不做任何改写，保证 repo 与 runtime 初始 hash 相同
- 目标已存在则报 `ALREADY_EXISTS` 退出，绝不覆盖

### 3. EOL 归一化（本轮踩坑点：必须在提交前做）
`git add` 会提示 `CRLF will be replaced by LF`。这**不是无害提醒**：
`.gitattributes` 的 `*.md/*.py eol=lf` 会在后续 checkout 把仓库副本变成 LF，
而运行时副本仍是 CRLF → 两侧 hash 永久不符 → 之后仓库更新再也不会下发。
做法：入库后**立刻把 repo 与 runtime 两侧都归一化为 LF**（只处理
`.md/.py/.yaml/.yml/.json/.sh`），再断言两侧 hash 相等。

### 4. 漂移对齐：先判别假象还是真实差异
对 `DIVERGED` 项，用同一 hash 算法分别算 **raw** 与 **LF 归一化后**的值，与 manifest 比对：
- LF 归一化后 == manifest → 纯 EOL 假象，归一化即收敛，不要当成内容改动
- 否则是真实内容差异 → diff 两侧，**以运行时为准**对齐仓库（运行时的才是实际加载、
  并被 Agent 持续改进的版本），同步更新 manifest 条目
本次实测：1 处 EOL 假象（self-learning）+ 2 处真实差异
（其中 workspace-hygiene 运行时多 17 行后补章节，属应上游化的改进）。

### 5. 收尾验证与提交
- 重跑审计，**分叉数必须为 0**——这是唯一合格判据
- `pytest tests/test_skill_registry.py tests/test_skills_sync_contract.py tests/test_packaging_contract.py`
  （AGENTS.md 规则 5：改动技能发现/索引必须跑固定自检）
- 提交**仅含 skills/**（`git add -- skills`）；pre-commit 守卫拒绝 skills × src 混提；
  提交信息写清"新增 N 个 + 对齐 M 个"
- 改完 bundled 技能后 manifest 必须同步更新，保持 **repo == runtime == manifest** 三者一致，
  否则下次 sync 会把该技能判为 user-modified 并永久停止下发

## 交付路径：技能**不**随 wheel 交付（实测，极易误判）

- `sync.py::_bundled_dir()` = `Path(__file__).resolve().parents[4] / "skills"`。
  - 源码 / `pip install -e`（README 推荐的可编辑安装）：parents[4] 就是仓库根 → 仓库 `skills/` 生效，成立。
  - **非 editable 的 wheel 安装**：sync.py 位于 `site-packages/voidcube/extensions/skills/`，
    parents[4] = `Lib/`，其下没有 skills → `sync_skills()` 直接返回空，
    **新机器零技能且无任何报错（静默降级）**。
- 唯一可信的验证方式：构建并检查 wheel 内容，**不要只看测试**：
  ```bash
  .venv/Scripts/python.exe scripts/build_wheel.py --outdir <临时目录>
  # 再用 zipfile 统计 namelist() 里的 SKILL.md 数量
  ```
  实测：731 个条目、`SKILL.md` **0 个**（只有 `voidcube/extensions/skills/*.py` 代码）。
- **陷阱**：`tests/test_packaging_contract.py` 通过 ≠ 打包验证——该测试对 skills **零断言**。
  拿它支撑"技能会随包交付"是过度解读。
- 副作用：构建 wheel 会重新生成 `build/`、`dist/`、`voidcube_agent.egg-info/`
  （都被 .gitignore 忽略，`git status` 看不到），验证完必须手动清理。

## 元数据存放位置（grep 文件会误判）

- `deprecated` / `supersedes` **不在 SKILL.md frontmatter 里**，而在活注册表
  `~/.VoidCube/.skills_registry.sqlite3`（`REGISTRY_FILENAME = ".skills_registry.sqlite3"`）的
  `skills` 表：列含 `frontmatter_name, directory_name, category, source, content_hash,
  deprecated, supersedes, updated_at`。验证弃用是否生效要**查库**，不是 grep 技能文件。
- 索引会**同时收录仓库与运行时两个根**：行数 ≈ repo 技能数 + runtime 技能数
  （实测 113 = 55 + 58），同一 bundled 技能有两条同名记录。展示层去重（`skills_list` 无重复），
  但按行计数/排序的下游代码要留意这个冗余。
- 孤儿文件：`~/.VoidCube/skills/registry.sqlite3` 是旧版命名的遗留，**无任何代码引用**，
  与活注册表不是同一个文件，可清理。

## 自审清单：改完技能后逐项验证（每项都要有命令证据）

1. 提交范围纯净：`git show --name-only --format="" <sha>` 里非 `skills/` 的文件数必须为 0
2. EOL 真的落盘：`git cat-file -p HEAD:<path>` 统计 `\r\n`，应为 0
   （否则未来 checkout 变 LF 时会重新制造两侧分叉）
3. 没夹带垃圾：扫描 `skills/` 下 `__pycache__` / `*.pyc` / `*.bak` / `.DS_Store`。**特别注意**：不要对整个 `skills/` 跑 `compileall` 或任何会生成产物的命令——生成的 `__pycache__` 会被计入目录 hash 并污染同步基线（实测踩过）
4. 没泄漏操作者私有路径：扫用户目录特征（`C:\Users\<用户名>`、仓库盘符路径等）
   （实测 7 个技能文件命中，含 `src = r"C:/Users/<用户名>/.VoidCube/..."` 这类可直接执行的行）
5. 三侧一致：repo hash == runtime hash == manifest 条目
6. **有没有外部并发写入者**：把技能文件 mtime 与 `git log -1 --format=%ci` 对比。
   实测过：提交 `21:56:12` 之后，`21:57:09` 有外部进程改了仓库+运行时+manifest 却**不提交**，
   于是"工作区干净"只是瞬时结论——收尾必须再查一次 `git status --short`。

## 索引去重与优先级（实测 2026-09-11，修正一处误判）

- `registry.discovery_roots()` 按调用顺序给 root 赋 `priority`（`home`=0、`repo`=1、`external`=2），
  `query_skills` 为 `ORDER BY priority, directory_name, file_path`
  → **同一技能的运行时副本排在仓库副本之前**。
- `prompt_builder._skills_from_registry` 用 `seen_skill_names` 去重，键是 `directory_name`，
  **先到者胜** → prompt 索引里显示的是**运行时副本**。
  - 实测：仓库 55 + 运行时 58 = 注册表 113 行，但 `skills_list` 只输出 57 条，无重复。
- **修正**：注册表里"同名两行"**不是缺陷**，是"双根索引 + 优先级"的有意设计，
  且重复不会泄漏到 prompt（`skills_by_category` 已去重，且输出层还二次 `seen` 去重）。
  不要按"重复索引 bug"去修它。
- **重要副作用**：既然运行时副本在索引中胜出，**只改仓库对 bundled 技能在本机不生效**
  （除非 sync 覆盖运行时）。这从另一侧解释了为什么必须维持 repo == runtime == manifest：
  只改仓库等于无效改动；只改运行时会永久阻断仓库下发。
- 低危隐患（发现即记录，暂未改）：
  - 去重键是 `directory_name`，而工具侧（`tool.py`）用 `frontmatter_name` 标识技能名；
    若某技能"目录名不同但 frontmatter name 相同"，去重会失效并真的列出两条。
  - `_skills_from_registry` 的 `skill_entries`（含重复行）被赋值后**从未被消费**（死变量）；
    当前不影响输出，但若有人改用它做计数就会踩坑。

## 旧版孤儿注册表 registry.sqlite3（已清理）

- 位置：`~/.VoidCube/skills/registry.sqlite3`（75 行，mtime 2026-08-26）。
- 判据：当前 `REGISTRY_FILENAME = ".skills_registry.sqlite3"`，活注册表在
  `~/.VoidCube/.skills_registry.sqlite3`；全仓库（代码/配置/文档）搜索只剩测试里的同名**临时**文件
  （tmp_path），无任何对旧路径的引用 → 属旧版命名遗留。
- 处置：用 SQLite Online Backup API 备份到
  `~/.VoidCube/runtime/backups/skills-registry/registry-orphan-<stamp>.sqlite3`
  （integrity ok、75 行）后删除；刷新注册表后未再生成。
- 复核：删除后 skills 目录只剩 `.bundled_manifest` + 技能目录；`skills_list` 正常（57 条）。
