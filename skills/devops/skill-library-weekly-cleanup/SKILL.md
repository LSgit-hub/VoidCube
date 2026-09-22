---
name: skill-library-weekly-cleanup
description: VoidCube 技能库每周清理流程（列库→查 deprecated→查待补全→查重复子集→查依赖→出报告）。当收到"技能库每周清理"定时任务或用户要求清理/审计技能库时使用。
category: devops
---

# 技能库每周清理（skill-library-weekly-cleanup）

定时任务「技能库每周清理」的标准执行流程。目标：以硬证据清理无效技能，宁可不删不可误删，产出 `~/.VoidCube/skills_audit_report.md`。

## 触发条件
- 定时任务「技能库每周清理」到期
- 用户说"清理技能库 / 审计技能库 / 技能太多了"

## 关键事实（务必先读）
1. **deprecated 标记不在 SKILL.md frontmatter 里**，只在 `~/.VoidCube/.skills_registry.sqlite3` 的 `skills.deprecated` 列。
   → 用 `grep '^deprecated: true' SKILL.md` 扫描会**漏检**，必须查 registry。frontmatter 只有 name/description/category/version/dependencies 等。
2. 双目录：运行时 `~/.VoidCube/skills/`（`source=home`, priority=0，覆盖优先）与仓库 bundled `F:/My_code/VScode_py/VoidCube/skills/`（`source=repo`, priority=1）。bundled 清单在 `~/.VoidCube/skills/.bundled_manifest`（`name:md5` 行）。
3. 删除前必须确认技能**不在 `.bundled_manifest`** 中，否则删了会被重新物化。
4. 删除统一走 `skill_manage(action='delete', name=...)`，它会同步清 registry（行数应减少对应数量）。
5. 参考型技能（mlops/*，声明 torch/vllm/peft 等第三方依赖但未安装）**不是无效技能**，依赖按需安装，禁止以"依赖未安装"为由删除。

## 步骤

### 0. 计数基线
```
# 文件数
search_files(pattern='SKILL.md', target='files', path='~/.VoidCube/skills')
# 可见数（应等于文件数 − deprecated 隐藏数）
skills_list()
# registry 行数
python -c "import sqlite3,os;c=sqlite3.connect(os.path.expanduser('~/.VoidCube/.skills_registry.sqlite3'));print(c.execute('select count(*) from skills').fetchone()[0])"
```

### 1. 列出所有技能（双目录并集）
```python
import os,re,glob,hashlib
home=os.path.expanduser('~/.VoidCube/skills'); repo='F:/My_code/VScode_py/VoidCube/skills'
def scan(root):
    out={}
    for p in glob.glob(os.path.join(root,'**','SKILL.md'),recursive=True):
        t=open(p,encoding='utf-8',errors='replace').read()
        m=re.search(r'^name\s*:\s*(.*)$',t.split('---',2)[1],re.M)
        out[m.group(1).strip() if m else os.path.basename(os.path.dirname(p))]=(p,hashlib.md5(t.encode()).hexdigest())
    return out
```

### 2. 查 deprecated 并删除
```python
import sqlite3,os
c=sqlite3.connect(os.path.expanduser('~/.VoidCube/.skills_registry.sqlite3')); c.row_factory=sqlite3.Row
for r in c.execute("select file_path,deprecated,supersedes,source from skills where deprecated=1"): print(dict(r))
```
- 命中即候选删除，先核对 `supersedes` 指向的替代技能确实存在且覆盖其内容。
- 确认不在 `.bundled_manifest` 后执行 `skill_manage(action='delete', name=<name>)`。

### 3. 查文档待补全 / 占位 / 空壳
扫描 `TODO|FIXME|待补全|待完善|占位符|placeholder|未验证|stub|coming soon`，并对 <1500 chars 的文件通读。
**已知误报**（不要删）：`github-code-review` 的 TODO/FIXME（grep 命令内容）、`voidcube-architecture` 的 占位符/stub（压缩占位摘要）、`audiocraft` 的 placeholder=（Gradio 参数）、`goal-manager-plugin-testing` 的 未验证（状态语义）、`python-multiple-version-resolution` 的 占位符（WindowsApps 假入口）。

### 4. 查重复 / 子集
```python
import difflib,itertools
# 对全部 SKILL.md 正文两两算 difflib.SequenceMatcher ratio，>0.45 才需人工判
```
互补组合（保留）：guidance/outlines、llama-cpp/gguf-quantization、axolotl/trl/peft/fsdp、voidcube-architecture 系列四个、bilibili/browser-silence/media-display。

### 5. 查工具依赖存在性
- 正则提取技能正文里的 `src/voidcube/...`、`tests/...`、`plugins/...` 路径，在 worktree（`~/.VoidCube/runtime/body/slots/slot-A/worktree/`）与主仓库双查存在性；命令示例里的假文件名（test_auth.py 等）忽略。
- 核对工具集：`src/voidcube/extensions/tools/toolsets.py`；memory 工具 `mem_search/mem_timeline/mem_remember` 由 `plugins/memory/mem/__init__.py` 定义；goal_* 由 `plugins/goal_manager/` 提供。
- 第三方 pip 依赖缺失 → 仅记录，不删除（见关键事实 5）。

### 6. 落盘报告
写 `~/.VoidCube/skills_audit_report.md`：审计前后数量表、逐步骤结果、删除清单（含依据）、保留确认、遗留观察、验证结论。
删除数必须与 registry 行数变化一致。

## 校验
- 删除后：`skills_list` 可见数 == 文件数（无 deprecated 隐藏项）
- registry 无 `deprecated=1` 残留、无指向已删文件的行
- 报告落盘且含删除数与依据

## 本轮基线（2026-09-14，历史值，勿当现值）
- 运行时 59（删 1 后）/ bundled 55（全部为逐字节镜像）/ 可见 59 / registry 114
- 删除项：`agent-context-optimization`（deprecated，被 `context-optimization` 取代，home-only）

## 当前实测基线（2026-09-22 探针复核，用前请刷新）
- 运行时 79 SKILL.md / 仓库 73 / manifest 73 条目 / registry 152 行（home 79 + repo 73，双根索引是设计非 bug）
- 运行时独有（不在仓库/manifest，手工或 Agent 专属）6 个：bilibili-media-playback、browser-media-silence、manual-skill-validation、skill-library-weekly-cleanup、voidcube-change-regression-review、voidcube-cli-command-refactor
- `sync_skills()` 的 `user_modified` 列表需逐项判"manifest 陈旧"还是"真实分叉"，判别方法见 `manual-skill-validation` 第 2 步（raw / LF 归一化双 hash 对照）
- 数量每次清理都会变，以本节命令实测为准，不要引用本节的数字当结论
