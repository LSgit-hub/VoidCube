---
name: manual-skill-validation
category: devops
description: 探针法验证手工复制到 ~/.VoidCube/skills 的技能是否真实有效，并按“有用留/没用删/有问题修”裁定与修复——发现域/注册中心/提示词三处可见性、manifest 三方 hash 对账、E2E 复制-索引-摘除复现、文档引用与硬编码标识的外部核验、bundled 技能三方对齐（repo == runtime == manifest）。
---

# 手工技能验证（探针法）

当用户把技能**手工复制**（不是 skill_manage 创建）到 `~/.VoidCube/skills/` 后，判断"这些技能是否真实有效/可用"。

核心原则：**验证必须走真实代码路径，不能靠读文档或 grep 猜**。用户要的是"能不能用"，唯一可信证据是让运行时自己回答。

## 触发条件

- 用户说"我手动添加了一些技能""帮我看看这些技能是否有效可用"
- 用户重复追问同一句（通常意味着上一轮只做了静态 grep，没给实证）

## 判定框架（五问）

1. **文件在不在、合不合法**：SKILL.md 存在、frontmatter 有 name/description、目录名与 name 一致、无 `__pycache__`/`.bak`/`.DS_Store`
2. **发现域认不认**：`_find_all_skills()` 是否包含
3. **索引与提示词认不认**：registry(数据库) 收录？`build_skills_system_prompt()` 两种模式都在？
4. **调用链认不认**：`skill_view()` 返回 success=True 且 readiness=available；`skills_list()` 含之
5. **内容真不真**：文档引用的源码路径/行号是否还存在？硬编码的外部标识（BV号、UID、API 字段）是否仍有效？声称的数值效果是否有实测支撑？

第 5 条是"文档 stale"的地雷区，最容易漏：技能能加载 ≠ 内容还正确。

## 步骤

### 0. 环境

```bash
cd /f/My_code/VScode_py/VoidCube    # 仓库根
# 探针脚本一律放 .test-tmp/，用项目虚拟环境跑（AGENTS.md 规则 4）
# 脚本头部：sys.path.insert(0, "<repo>/src") 然后 import voidcube.*
```

### 1. 找出"用户手工加的"到底是哪些

不要凭用户口头列举，直接和 manifest、仓库求差集：

- `set(运行时技能名) - set(仓库技能名)` = 运行时独有（手工/Agent 专属技能）
- 再逐个看 `名字 in ~/.VoidCube/skills/.bundled_manifest` → 不在 = sync 永不管它（**实测：手工技能不会被 sync 覆盖或删除**）
- mtime 排序可定位"刚刚加进来的那个"

### 2. 三方 hash 对账（照抄 sync.py::_hash，否则结论无效）

```python
h = hashlib.md5()
for p in sorted(x for x in d.rglob("*") if x.is_file()):
    h.update(str(p.relative_to(d)).encode("utf-8"))   # 先相对路径字节
    h.update(p.read_bytes())                          # 再文件字节
```

- 同时算 **raw** 与 **LF 归一化后**（`b.replace(b"\r\n", b"\n")`）两个值
- `manifest == repo == runtime` → 三方一致
- LF 归一化后才相等 → **纯 EOL 假象**（`.gitattributes` 的 `eol=lf` 造成），不是内容分叉
- raw runtime == raw repo 但 manifest 不同 → **manifest 陈旧**（仓库改了没跑 sync），危害：该条目以后被永久判 user_modified、仓库更新永不下发
- 都不等 → **真实内容分叉**，需 diff 并以运行时为准上游化

### 3. E2E 复现"复制即生效"（这是最有力的证据）

在 `try/finally` 里做，finally 必须清理并复验：

1. 造一个探针技能目录（名字避开正式技能）
2. `R.refresh_catalog_index()` → 期望 `added=1`
3. `clear_skills_system_prompt_cache()` → `build_skills_system_prompt(mode="index")` 含探针
4. `finally`: rmtree → `refresh_catalog_index()` → 期望 `removed=1` 且提示词不再含探针、目录已消失

### 4. sync 影响面（只读，安全）

直接跑 `sync_skills(quiet=True)`（就是 CLI 启动时干的事），看返回字典：

```
{'copied': [], 'updated': [], 'skipped': N, 'user_modified': [...], 'cleaned': [], 'total_bundled': N}
```

- 手工技能不在 manifest → 既不出现在 copied/updated，也不会被 cleaned
- **`user_modified` 列表必须逐项查清**：是 manifest 陈旧 还是 真实分叉（用第 2 步的双 hash 判别）

### 5. 内容核验（文档 vs 现实）

- 抽码块里的 `src/...py`、函数名、行号，逐个 `test -e` / grep 确认
- 行号尤其不可信（代码会漂），命中"文件不存在"立即判 stale
- 外部标识做**真外部核验**，例如 B站：`curl "https://api.bilibili.com/x/web-interface/view?bvid=BVxxx"` 看标题是否与技能示例一致，`data.owner.mid` 反查 UID
- 声称的效果数值必须实测复现（字符数/token 数），不采信文档里的历史数字

### 6. 测试门禁

```bash
pytest tests/test_skill_registry.py tests/test_prompt_builder_skills_cache.py tests/test_skills_sync_contract.py -q
pytest tests/test_response_disposition.py tests/test_context_service.py -q
pytest Mem/tests -k memory --ignore=Mem/tests/test_identity_source_audit.py --ignore=Mem/tests/test_identity_source_audit_contract.py
```

`Mem/tests` 不 ignore 那两个文件会因缺 `scripts` 模块在**收集阶段**报 2 errors。

### 7. 报告结构

逐技能给"文件状态 / 发现域 / 索引与提示词 / skill_view / 内容真伪 / 测试"六项结论，再给 P1/P2/P3 问题清单和下一步建议。**用户要判断时不要顺手改代码**，先出证据再问是否修。

## 第二阶段：裁定与修复（用户说“有用就留下、没用就删除、有问题就修复一下”时）

用户会把裁决权直接交给你。顺序：**先出证据 → 再动手 → 每步复验**。

### 0. 先备份（不可谈判）

```
~/.VoidCube/runtime/backups/skills-manual-<stamp>/{runtime,repo}/
~/.VoidCube/runtime/backups/skills-manual-<stamp>/bundled_manifest.bak
```

把要改的技能整目录、仓库侧旧版、manifest 各留一份。**文件一旦被改坏就从备份重建，不要在坏文件上反复修补**（本会话反复修补把 122 行文件弄成 259 行，多花三轮）。

### 1. 逐技能裁定，判据要能落到证据

- **留**：①`skill_view` 可加载 ②文档引用的路径/符号/外部标识实测仍有效 ③有真实复用场景
- **删**：上面至少一条硬性不成立，或与既有技能实质重复（判定重复必须先 diff 两份 SKILL.md）
- **不要为了“有东西可删”而删**。本次 6 个手工技能全部达标 → 0 删除，如实向用户说明未删及原因，比凑数删一个更有价值

### 2. 修复分两类，处理方式不同

- **runtime-only 技能**（不在 manifest / 不在仓库）：直接改文件即可，sync 永不碰它。改完复核 `skill_view` + 两种模式提示词 + 目录内无 `__pycache__` 残留
- **bundled 技能**（仓库与 manifest 里都有）：**必须三方对齐**——改运行时副本 → 原样复制到仓库副本 → 重算 manifest 条目。重算要用 sync 模块自己的 helper，**不要手写 manifest**（格式是 `name:md5`、按 name 排序、行尾敏感）：

```python
from voidcube.extensions.skills.sync import _hash, _read_manifest, _write_manifest, sync_skills
mf = _read_manifest(HOME / ".bundled_manifest")
mf[name] = _hash(repo_dir)          # 以仓库副本 hash 为准
_write_manifest(HOME / ".bundled_manifest", mf)
```

### 3. 漂移的两类处置（判别方法见上一阶段第 2 步）

- **manifest 陈旧**（runtime == repo ≠ manifest）：只需 `mf[name] = _hash(repo_dir)`，内容不用动
- **真实分叉**（runtime ≠ repo）：以**运行时**为准上游化到仓库（运行时才是实际加载、被持续改进的版本），两侧 LF 归一后再写 manifest

### 4. 收敛判据（唯一合格线）

```python
sync_skills(quiet=True)["user_modified"] == []      # 且三方对账脚本不再输出任何条目
```

### 5. 收尾复核清单

- registry：`deprecated=0`、`refresh_catalog_index()` 的 reparsed 数与改动文件数相符
- 被改技能：`skill_view` success=True、readiness=available、full/index 两种提示词都可见
- E2E 探针：added=1 → 提示词可见 → removed=1、无残留目录
- 门禁：`pytest tests/test_skill_registry.py tests/test_prompt_builder_skills_cache.py tests/test_skills_sync_contract.py`
- 卫生：技能库无 `__pycache__` / `*.pyc` / `*.bak` / 探针目录（跑脚本和 py_compile 都会产生，必须清）
- 改过仓库副本就报告 git 影响：改动是未暂存的技能文件；顺带检查暂存区是否已同时含 `skills/` 与 `src/`（会被 pre-commit 守卫拒绝），把提交方式交给用户决定

## 坑（本会话实测踩到）

- **API 名字会变，先 grep 再 import**：`registry.registry_path()`（不是 `_skills_registry_path`）；`refresh_registry(roots)` 必须传 roots，日常用无参的 `refresh_catalog_index()` 更省事；`refresh_and_query(paths)` 必须传 paths；`clear_skills_system_prompt_cache()` **没有** `clear_snapshot` 参数（JSON 快照时代已废弃）
- **注册中心 = SQLite**：`~/.VoidCube/.skills_registry.sqlite3`，表 `skills`，列含 `directory_name / frontmatter_name / category / source / content_hash / deprecated / supersedes / updated_at`。查"弃用是否生效"要查库，不是 grep SKILL.md
- **双根索引是设计不是 bug**：库中行数 ≈ 仓库技能数 + 运行时技能数（同技能两条，home 优先）。别当重复 bug 去修
- **提示词按 `directory_name` 展示**，`_find_all_skills()`/`skills_list()` 用 frontmatter `name`。目录名与 name 不一致时：提示词的名字仍能被 `skill_view` 解析，但 frontmatter name 会 `not found` —— 校验技能时**目录名与 name 必须一致**
- **手工技能常见 CRLF**：加载无影响，但一旦要入库到仓库 `skills/`，`.gitattributes` 的 `eol=lf` 会让两侧 hash 永久分叉，入库前必须做 LF 归一化
- **编辑 CRLF 技能文件禁止直接 `replace(b"\n", b"\r\n")`，也别用 `open(..., newline="\r\n")` 写“仍然含 `\r` 的字符串”**：会把已有 `\r\n` 变成 `\r\r\n`，行数翻倍（本会话实测 122 行 → 251/259 行）。正确顺序：先把所有换行归一为 `\n`（`replace("\r\n","\n").replace("\r","\n")`，必要时先折叠 `\r\r`），再用 `newline="\r\n"` 或 `replace("\n","\r\n")` 写回；改完复核「裸 CR = 0、`\r\r\n` = 0、行数与预期一致」，损坏时直接从备份重建而不是反复修补。
- **探针脚本要 try/finally 清理**并复验 `removed=1`，否则会在技能库留下垃圾技能进提示词
- 用 `search_files` 在仓库根会超时，用 terminal 的 `grep -n`

## 实测基线（2026-09-22，R=仓库根）

- 运行时 79 技能 / 仓库 73 / manifest 73；运行时独有 6 个；`_find_all_skills()` 无重名
- registry 152 行（home + repo 双根）；`refresh_catalog_index()` 热调用 ~59 ms，`refresh_and_query` ~38 ms，`reused=152`
- 提示词体积：full 8605 字符 / index 7052 字符 → **只省 18%**。原因：`_build_skills_index_line` 按英文句号 "." 取首句、超 80 字符才截断，中文描述几乎无 ASCII 句号 → 截断对中文基本失效。这条常被文档夸大成"大幅压缩"，必须实测
- 手工技能验证通过的判据：`added=1` → 提示词可见 → `removed=1` 且目录消失

## 交叉引用

- 技能子系统内部机制（registry 表结构与双根索引、热路径、bundled 同步分支、EOL 陷阱、性能基准）：`skills-system-diagnostics`
- 周期性清理技能库（列库→查 deprecated→查重复→出报告）：`skill-library-weekly-cleanup`
- 改动后的回归复查（全量 pytest 门禁、flaky 判别）：`voidcube-change-regression-review`

## 变更记录

- 2026-09-22: 创建，系统化验证手动添加的技能
- 2026-09-22: 重写为探针法。补：五问判定框架、sync.py::_hash 复刻与 raw/LF 双 hash 判别、E2E 复制-索引-摘除复现、sync_skills 影响面与 user_modified 分类、API 名称坑（registry/refresh_*/clear_* 签名）、提示词按目录名展示、CRLF 入库风险、实测基线（index 仅省 18%）。删掉写死具体技能名的"典型案例"章节（会随技能库变化过时）。配套脚本见 scripts/verify_manual_skills.py
- 2026-09-22: 修脚本假告警（目录名一致性检查的条件误用 `p.parent.name`，会对每个技能误报）；补交叉引用；补 CRLF 编辑坑。实测脚本跑通：E2E 复制→索引→摘除 added=1/removed=1，无残留。
- 2026-09-22: 补“第二阶段：裁定与修复”——备份约定、留/删/修判据、runtime-only 与 bundled 的分类处置、manifest 重算（用 sync 的 helper 而非手写）、漂移两类处置、收敛判据 `user_modified == []`、收尾复核清单；description 扩到“裁定+修复”触发场景。
