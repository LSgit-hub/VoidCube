---
name: voidcube-architecture-audit
category: devops
description: VoidCube 仓库架构整理/重构后的自检流程。当用户说"整理/调整了 agent 文件架构，检查一下有没有问题"或架构改动后需要验收时使用。包含 ARCHITECTURE.md 规定的验收命令、幽灵 pyc 检测、构建残留扫描和技能目录差异核对。
---

# VoidCube 架构整理后自检

## 触发条件
- 用户整理/重构了 VoidCube 文件架构后要求复查
- 任何涉及模型、鉴权、请求协议、技能或打包边界的改动（AGENTS.md 规则 3）
- 架构迁移、目录收拢后的验收

## 环境要点
- 仓库路径: `F:\My_code\VScode_py\VoidCube`（Windows，git-bash 风格 `/f/My_code/VScode_py/VoidCube`）
- 所有 Python 命令必须用项目虚拟环境: `.venv/Scripts/python.exe`（ARCHITECTURE.md 第 5 节明确要求）
- 注意: `search_files` 工具在 Windows 大目录上会超时（60s 上限），目录遍历用 terminal + find/ls 代替

## 步骤 1: 快速摸底
```bash
cd /f/My_code/VScode_py/VoidCube
git status --short          # 确认整理是否已提交
git log --oneline -8        # 看最近的整理提交
ls scripts/                 # 确认验收脚本存在
```
顶层残留目录检查: `build/ dist/ voidcube_agent.egg-info/ Mem/build/ Mem/dist-mem/ Mem/src/memai.egg-info/`（一般被 .gitignore 忽略，git 干净但物理残留）。

## 步骤 2: 官方验收命令（ARCHITECTURE.md 第 5 节，必须全部通过）
```bash
.venv/Scripts/python.exe scripts/python_architecture.py          # 架构 gate: 退役包/根CLI/层级依赖/源码布局导入
.venv/Scripts/python.exe -m compileall -q src/voidcube plugins scripts
.venv/Scripts/python.exe -m pytest tests/test_packaging_contract.py -q
.venv/Scripts/python.exe scripts/build_wheel.py --outdir .test-tmp/canonical-wheel
```
成功标志: 架构 gate 全 ok、compileall 无错、契约测试全过、wheel "Successfully built + Verified"。
预期结果（2026-08 基线）: 8/8 契约测试通过，wheel 版本 1.0.0。

## 步骤 3: 幽灵 pyc 检测（无对应 .py 的缓存残留）
```bash
find . -name "*.pyc" -not -path "./.venv/*" -not -path "*/node_modules/*" -not -path "./.git/*" | while read f; do
  base=$(basename "$f" | sed 's/\.cpython-[0-9]*\.pyc$/.py')
  dir=$(dirname "$f")/../
  [ -f "${dir}${base}" ] || echo "GHOST: $f"
done
```
- 高发位置: `Mem/src/memai/__pycache__/`、`Mem/tests/__pycache__/`、`plugins/memory/mem/__pycache__/`
- 已知幽灵: `database`、`governance_repository`、`repository`、`storage`（Mem），`governor_bridge`、`host_integration`（plugins/memory/mem）
- 顺带注意 cpython-311 与 cpython-314 两代缓存混存 = 曾跨 Python 版本运行的历史痕迹
- 确认不被 git 跟踪: `git ls-files | grep -c "\.pyc$"` 应为 0（.gitignore 已含 `__pycache__/` 和 `*.pyc`）

## 步骤 4: 技能目录两侧差异核对
```bash
# 仓库 skills 是发布资产；~/.VoidCube/skills 是运行时技能（含本地自建）
ls skills/                                    # 仓库侧
ls ~/.VoidCube/skills/                        # 运行时侧
cat ~/.VoidCube/skills/.bundled_manifest      # 已登记技能清单
```
判断规则:
- 运行时多出的技能且不在 .bundled_manifest 中 = 本地自建技能（正常，不入 wheel，无需处理）
- 仓库里有但运行时没有 = 同步问题，需跑技能同步
- 媒体/自建类技能（media、windows-automation、diagnostics 等）通常只在运行时侧存在，属设计如此
- 仓库只有 5 类（devops/github/mlops/self-learning/system），运行时 8 类 = 正常差异

## 步骤 5: 报告格式
按"总体结论 + 问题清单"输出:
1. 官方验收结果逐条 PASS（架构 gate / compileall / 契约测试 / wheel parity / git 干净）
2. 问题按严重度排序: 幽灵 pyc（缓存残留）→ 构建残留目录 → 版本混存 → 技能差异（标注"需用户确认是否设计如此"）
3. 结尾给出下一步建议并询问是否代清理

## Pitfalls
- git status 干净 ≠ 没有残留: .gitignore 会掩盖 build/、__pycache__、egg-info 等垃圾，必须物理检查目录
- 不要用 `python` 直接跑验收脚本，必须 `.venv/Scripts/python.exe`（Windows 下全局 python 版本/依赖不对）
- `git ls-files Mem/` 看 Mem 被跟踪的顶层结构，排除 Mem/build、Mem/dist-mem 等未跟踪产物
- 技能差异核对时先查 .bundled_manifest，别直接断定"缺技能"——本地自建技能不入 manifest 是正常设计

## 验证完成标准
- 四项官方验收全过 + 幽灵 pyc 清单给出 + 构建残留定位 + 技能差异归类完毕
- 修复后的清理命令: `find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +`（执行前需用户确认）
