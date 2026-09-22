# -*- coding: utf-8 -*-
"""手工技能验证探针（可复用，2026-09-22 实测跑通）。

用法:
    cd /f/My_code/VScode_py/VoidCube
    .venv/Scripts/python.exe <此文件> [技能名 ...]

不传技能名时：只做整体体检（发现域规模、运行时独有技能、manifest 三方对账、sync 影响面）。
传技能名时：额外做单体验证（注册中心/提示词/skill_view/目录名一致性）。

只读为主；唯一的写操作是 E2E 探针（复制一个临时技能再删除），用 try/finally 保证清理。
"""
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1] if (Path(__file__).resolve().parents[1] / "src").is_dir() else Path("F:/My_code/VScode_py/VoidCube")
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from voidcube.extensions.skills import registry as R
from voidcube.extensions.skills.catalog import get_all_skills_dirs, parse_frontmatter
from voidcube.extensions.skills.sync import sync_skills
from voidcube.extensions.skills.tool import _find_all_skills, skill_view, skills_list
from voidcube.runtime.agent import prompt_builder as PB

HOME = Path.home() / ".VoidCube" / "skills"
MANIFEST = HOME / ".bundled_manifest"


def h_dir(d: Path, lf: bool = False) -> str:
    """复刻 sync.py::_hash —— 排序 rglob 全部文件，先相对路径字节再文件字节。"""
    h = hashlib.md5()
    for p in sorted(x for x in d.rglob("*") if x.is_file()):
        b = p.read_bytes()
        if lf:
            b = b.replace(b"\r\n", b"\n")
        h.update(str(p.relative_to(d)).encode("utf-8"))
        h.update(b)
    return h.hexdigest()


def collect(root: Path) -> dict:
    """返回 {技能名: 技能目录}。注意存的是**目录**不是 SKILL.md 文件——
    存文件会让 h_dir 的 rglob 空转、hash 全变成 md5("") 这个假值。"""
    out = {}
    for p in root.rglob("SKILL.md"):
        try:
            fm = parse_frontmatter(p.read_text(encoding="utf-8", errors="replace")) or {}
        except OSError:
            continue
        name = fm.get("name") if isinstance(fm, dict) else None
        out[name or p.parent.name] = p.parent
    return out


def read_manifest() -> dict:
    if not MANIFEST.exists():
        return {}
    d = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip()] = v.strip()
    return d


def section(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def main(names):
    mf = read_manifest()
    home, repo = collect(HOME), collect(REPO / "skills")

    section("1. 发现域")
    print("repo skills dir:", REPO / "skills")
    for d in get_all_skills_dirs():
        print("  root:", d, "exists:", d.exists())
    allsk = _find_all_skills()
    ns = [s["name"] for s in allsk]
    print(f"_find_all_skills() = {len(allsk)} 个 | 仓库 {len(repo)} | 运行时 {len(home)} | manifest {len(mf)}")
    print("重名:", [n for n in set(ns) if ns.count(n) > 1] or "无")

    section("2. 运行时独有技能（= 手工添加的候选）")
    for n in sorted(set(home) - set(repo)):
        p = home[n]
        age = (time.time() - p.stat().st_mtime) / 86400
        print(f"  {n:42s} {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M} "
              f"({age:5.1f}天前) {'在manifest' if n in mf else '不在manifest(sync 不碰)'}")
    print("  仓库独有:", sorted(set(repo) - set(home)) or "无")

    section("3. manifest 三方对账（raw + LF 双 hash）")
    for n in sorted(set(home) & set(repo)):
        if n not in mf:
            continue
        raw_rt, lf_rt = h_dir(home[n]), h_dir(home[n], True)
        raw_rp, lf_rp = h_dir(repo[n]), h_dir(repo[n], True)
        if mf[n] == raw_rt == raw_rp:
            continue
        verdict = ("纯 EOL 假象" if lf_rt == lf_rp == mf[n]
                   else "manifest 陈旧(仓库改了没 sync → 以后仓库更新永不下发)" if raw_rt == raw_rp
                   else "真实内容分叉(以运行时为准上游化)")
        print(f"  {n}: {verdict}")
        print(f"     manifest={mf[n]}  runtime raw/lf={raw_rt}/{lf_rt}  repo raw/lf={raw_rp}/{lf_rp}")

    section("4. sync_skills 影响面（只读行为，CLI 启动时同样会跑）")
    res = sync_skills(quiet=True)
    print(" ", res)
    for n in res.get("user_modified", []):
        print(f"    user_modified: {n} → 用第 3 节判定是 manifest 陈旧还是真实分叉")

    section("5. 注册中心")
    db = R.registry_path()
    print("registry:", db, "exists:", db.exists())
    print("refresh_catalog_index():", R.refresh_catalog_index())
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("select count(*) from skills")
    print("库中总行数:", cur.fetchone()[0], "（双根索引，含 home+repo 两条同名记录属正常）")
    for n in names:
        cur.execute("select directory_name,frontmatter_name,category,source,deprecated,supersedes "
                    "from skills where directory_name=? or frontmatter_name=?", (n, n))
        rows = [dict(r) for r in cur.fetchall()]
        print(f"  [{n}] registry 命中 {len(rows)} 行")
        for r in rows:
            print("     ", r)
    conn.close()

    section("6. 提示词 / skill_view / skills_list")
    PB.clear_skills_system_prompt_cache()
    full = PB.build_skills_system_prompt()
    idx = PB.build_skills_system_prompt(mode="index")
    print(f"full {len(full)} 字符 / index {len(idx)} 字符（省 {100*(1-len(idx)/max(len(full),1)):.0f}%）"
          "  ← 中文描述按 '.' 取首句基本失效，别信文档里的压缩数字")
    lst = skills_list()
    lst = lst if isinstance(lst, str) else json.dumps(lst, ensure_ascii=False)
    for n in names:
        raw = skill_view(n)
        ok = err = None
        try:
            d = json.loads(raw) if isinstance(raw, str) else raw
            ok, err = d.get("success"), d.get("error")
        except Exception:
            pass
        print(f"  [{n}] full={n in full} index={n in idx} skills_list={n in lst} "
              f"skill_view(success={ok} err={err})")
    # 目录名 vs frontmatter name 一致性
    for n in names:
        p = home.get(n)
        if p and p.name != n:
            print(f"  ⚠️ [{n}] 目录名 {p.name} != frontmatter name → 提示词展示目录名，请统一")

    section("7. E2E：复制 → 索引 → 提示词 → 摘除")
    probe_dir = HOME / "devops" / "zz-probe-manual-skill"
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
        (probe_dir / "SKILL.md").write_text(
            "---\nname: zz-probe-manual-skill\ncategory: devops\n"
            "description: 一次性探针，验证手工复制是否被索引与提示词发现。\n---\n\n# 探针\n",
            encoding="utf-8")
        print("复制后:", R.refresh_catalog_index())
        PB.clear_skills_system_prompt_cache()
        print("提示词可见:", "zz-probe-manual-skill" in PB.build_skills_system_prompt(mode="index"))
    finally:
        if probe_dir.exists():
            shutil.rmtree(probe_dir)
        print("删除后:", R.refresh_catalog_index())
        PB.clear_skills_system_prompt_cache()
        print("已从提示词摘除:", "zz-probe-manual-skill" not in PB.build_skills_system_prompt(mode="index"))
        print("残留目录:", probe_dir.exists())


if __name__ == "__main__":
    main(sys.argv[1:])
