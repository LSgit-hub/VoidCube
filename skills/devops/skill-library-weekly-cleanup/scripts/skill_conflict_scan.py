#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""技能库冲突/重复体检（只读，不写任何文件）。

用法（仓库根，项目虚拟环境）:
    .venv/Scripts/python.exe skills/devops/skill-library-weekly-cleanup/scripts/skill_conflict_scan.py

检查项:
  A 名称口径三方一致性：提示词清单(目录名) / skills_list(frontmatter name) / skill_view(只认目录名)
  B 内容重复 vs 互补：difflib 相似度 + 子集占比（≥0.6 疑似真冗余）
  C 注册类一致性：deprecated 残留 / supersedes 指向失效 / 同名 home-repo content_hash 不一致
  D 技能名 vs 工具名重合
  E 触发语重合（描述里的"当…时"短语被多个技能声明）

注意:
  - 本脚本只读；不要对技能目录跑 compileall 或任何会生成 __pycache__ 的命令（会污染同步基线）。
  - 输出里的名称一律用**目录名**指向技能，方便直接 skill_view。
"""
from __future__ import annotations

import difflib
import json
import re
import sqlite3
import sys
from pathlib import Path

_CANDIDATE = Path(__file__).resolve().parents[4]
REPO = _CANDIDATE if (_CANDIDATE / "src").is_dir() else Path("F:/My_code/VScode_py/VoidCube")
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from voidcube.extensions.skills.catalog import parse_frontmatter  # noqa: E402
from voidcube.extensions.skills.tool import skill_view, skills_list  # noqa: E402
from voidcube.runtime.agent import prompt_builder as PB  # noqa: E402

SK = REPO / "skills"
HOMESK = Path.home() / ".VoidCube" / "skills"
_MARKER_EXT = {".md", ".py", ".json", ".yaml", ".yml", ".txt", ".toml"}


def _fm(p: Path) -> dict:
    try:
        fm = parse_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        fm = None
    if isinstance(fm, tuple):
        fm = fm[0]
    return fm if isinstance(fm, dict) else {}


def collect(root: Path) -> list[dict]:
    out = []
    for p in root.rglob("SKILL.md"):
        text = p.read_text(encoding="utf-8", errors="replace")
        fm = _fm(p)
        out.append(
            {
                "dir": p.parent.name,
                "rel": p.parent.relative_to(root).as_posix(),
                "name": str(fm.get("name") or p.parent.name),
                "desc": str(fm.get("description") or ""),
                "text": text,
            }
        )
    return out


def section(title: str) -> None:
    print("\n" + "=" * 72 + f"\n{title}\n" + "=" * 72)


def main() -> int:
    repo = collect(SK)
    print(f"仓库技能 {len(repo)} 个 | 运行时技能 {len(collect(HOMESK))} 个")

    section("A 名称口径三方一致性（目录名 != frontmatter name）")
    PB.clear_skills_system_prompt_cache()
    full = PB.build_skills_system_prompt()
    lst = skills_list()
    lst = lst if isinstance(lst, str) else json.dumps(lst, ensure_ascii=False)
    bad = []
    for it in repo:
        if it["dir"] == it["name"]:
            continue
        raw = skill_view(it["dir"])
        try:
            ok_dir = (json.loads(raw) if isinstance(raw, str) else raw).get("success")
        except Exception:
            ok_dir = "?"
        raw2 = skill_view(it["name"])
        try:
            ok_fm = (json.loads(raw2) if isinstance(raw2, str) else raw2).get("success")
        except Exception:
            ok_fm = "?"
        bad.append((it["rel"], it["dir"], it["name"],
                    f"- {it['dir']}:" in full or f"- {it['dir']}:" in full,
                    it["name"] in lst,
                    not ok_fm))
    print(f"违例 {len(bad)} / {len(repo)}")
    print("  （提示词用目录名；skills_list 用 frontmatter name；skill_view 只认目录名）")
    for rel, d, n, in_prompt, in_list, view_by_fm_fails in bad:
        print(f"  {rel}: 目录={d} name={n} | 提示词含目录名={in_prompt} | "
              f"skills_list 含 name={in_list} | skill_view(name) 失败={view_by_fm_fails}")
    print("  危害：按 skills_list 给的名字调 skill_view 会 not found（技能看得见打不开）")
    print("  修法（产品决策）：统一两侧命名；改 frontmatter name 时**必须重算 manifest 键**，避免留孤儿键")

    section("B 内容重复 vs 互补（difflib ≥0.30，含子集占比）")
    pairs = []
    for i, a in enumerate(repo):
        for b in repo[i + 1:]:
            r = difflib.SequenceMatcher(None, a["text"][:6000], b["text"][:6000]).ratio()
            if r < 0.30:
                continue
            la = [l.strip() for l in a["text"].splitlines() if len(l.strip()) > 25]
            lb = {l.strip() for l in b["text"].splitlines() if l.strip()}
            c = sum(1 for l in la if l in lb) / max(len(la), 1)
            pairs.append((round(r, 3), round(c, 2), a["rel"], b["rel"]))
    pairs.sort(reverse=True)
    for r, c, x, y in pairs:
        tag = "  ← 疑似子集(真冗余)" if c >= 0.6 else "  ← 互补/相近(保留)"
        print(f"  {r} 子集占比={c}  {x} vs {y}{tag}")
    print(f"  命中 {len(pairs)} 对（阈值内无命中 = 无重复）")

    section("C 注册类一致性")
    db = Path.home() / ".VoidCube" / ".skills_registry.sqlite3"
    if not db.exists():
        print("  registry 不存在:", db)
    else:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("select * from skills")]
        conn.close()
        print(f"  registry 行数 {len(rows)}；deprecated=1 {sum(1 for r in rows if r.get('deprecated'))} 行")
        names = {r["frontmatter_name"] for r in rows} | {r["directory_name"] for r in rows}
        for r in rows:
            s = r.get("supersedes")
            if s and s not in names:
                print(f"  ⚠️ supersedes 指向不存在: {r['directory_name']} -> {s}")
        by = {}
        for r in rows:
            by.setdefault(r["frontmatter_name"], []).append(r)
        for n, v in by.items():
            if len(v) > 1 and len({x["content_hash"] for x in v}) > 1:
                print(f"  ⚠️ 同名不同内容: {n} " + str([(x['source'], str(x['content_hash'])[:8]) for x in v]))

    section("D 技能名 vs 工具名重合")
    tools = set()
    for p in (REPO / "src/voidcube/extensions/tools").rglob("*.py"):
        tools |= set(re.findall(r'name="([a-z][a-z_0-9]*)"', p.read_text(encoding="utf-8", errors="replace")))
    sks = {it["name"] for it in repo} | {it["dir"] for it in repo}
    print(f"  工具名 {len(tools)} 个；重合: {sorted(sks & tools) or '无'}")

    section("E 触发语重合")
    trig = {}
    for it in repo:
        for m in re.findall(r"当[^，。；]{2,30}", it["desc"]):
            trig.setdefault(m.strip(), []).append(it["name"])
    same = {k: v for k, v in trig.items() if len(v) > 1}
    for k, v in same.items():
        print(f"  ⚠️ 触发语「{k}」被多个技能声明: {v}")
    print(f"  完全相同的触发语组数: {len(same)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
