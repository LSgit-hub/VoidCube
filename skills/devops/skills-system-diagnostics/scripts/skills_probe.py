# -*- coding: utf-8 -*-
"""技能子系统诊断探针（可复用模板，2026-08 实测验证）。

用法: cd /f/My_code/VScode_py/VoidCube && .venv/Scripts/python.exe .test-tmp/skill_db_probe.py
注意: 如 ImportError, 先 grep 确认 prompt_builder 中提示词构建函数的确切名字
（历史上叫 build_skills_system_prompt，不是 _build_skills_prompt）。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path

# ── 1. 发现域规模 ───────────────────────────────
from voidcube.extensions.skills.catalog import get_all_skills_dirs, get_repo_skills_dir
from voidcube.extensions.skills.tool import _find_all_skills

print("=== 1. 发现域规模 ===")
print("get_repo_skills_dir() =", get_repo_skills_dir())  # 已知 bug: 返回自身目录
all_dirs = [d for d in get_all_skills_dirs() if d.exists()]
for d in all_dirs:
    n = len(list(d.rglob("SKILL.md")))
    print(f"  目录 {d}: {n} 个 SKILL.md")

t0 = time.perf_counter()
skills = _find_all_skills()
t_find = time.perf_counter() - t0
print(f"_find_all_skills() 返回 {len(skills)} 个技能, 耗时 {t_find*1000:.1f} ms")
categories = {}
for s in skills:
    categories.setdefault(s["category"], []).append(s["name"])
# None 分类会抛 TypeError, 必须 str() 包裹
print("分类分布:", {str(k): len(v) for k, v in sorted(categories.items(), key=lambda x: str(x[0]))})

# ── 2. 快照机制 ─────────────────────────────────
from voidcube.runtime.agent.prompt_builder import (
    _build_skills_manifest,
    _load_skills_snapshot,
    _skills_prompt_snapshot_path,
    _SKILLS_SNAPSHOT_VERSION,
    clear_skills_system_prompt_cache,
)

print("\n=== 2. 快照机制 ===")
snapshot_path = _skills_prompt_snapshot_path()
print("快照路径:", snapshot_path, "存在:", snapshot_path.exists())

t0 = time.perf_counter()
manifest = _build_skills_manifest(all_dirs)
print(f"manifest 构建耗时 {(time.perf_counter()-t0)*1000:.1f} ms")
t0 = time.perf_counter()
snap = _load_skills_snapshot(manifest)
print(f"快照加载(命中判定)耗时 {(time.perf_counter()-t0)*1000:.1f} ms, 命中: {snap is not None}")

# ── 3. 冷路径 vs 热路径 ─────────────────────────
from voidcube.runtime.agent.prompt_builder import build_skills_system_prompt

clear_skills_system_prompt_cache(clear_snapshot=True)
t0 = time.perf_counter()
cold = build_skills_system_prompt()
t_cold = time.perf_counter() - t0
print(f"冷路径(删快照后): {t_cold*1000:.1f} ms, 输出 {len(cold)} 字符")

t0 = time.perf_counter()
warm = build_skills_system_prompt()
t_warm = time.perf_counter() - t0
print(f"热路径(LRU命中): {t_warm*1000:.1f} ms, 一致: {cold == warm}")

# ── 4. 失效粒度 ─────────────────────────────────
print("=== 4. 失效粒度 ===")
modified = None
for d in all_dirs:
    for skill_md in d.rglob("SKILL.md"):
        if ".git" in skill_md.parts or ".hub" in skill_md.parts:
            continue
        try:
            os.utime(skill_md, None)  # 只改 mtime, 不改内容
            modified = skill_md
            break
        except OSError:
            continue
    if modified:
        break
if modified:
    manifest2 = _build_skills_manifest(all_dirs)
    print(f"touch {modified} 后 manifest 变化: {manifest != manifest2} (→ 快照失效粒度确认)")

# ── 5. skill_view 定位耗时 ──────────────────────
from voidcube.extensions.skills.tool import skill_view

target = skills[0]["name"] if skills else None
if target:
    t0 = time.perf_counter()
    r = skill_view(target)
    print(f"skill_view('{target}') 耗时 {(time.perf_counter()-t0)*1000:.1f} ms, 返回 {len(r)} 字符")
