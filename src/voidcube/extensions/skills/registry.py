"""Rebuildable SQLite index for discovered skill metadata.

The filesystem remains authoritative for skill content and availability.  This
module stores parsed metadata and lifecycle overrides only, so the database can
be deleted and rebuilt without losing a skill.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ...infrastructure.config.runtime_paths import get_VoidCube_home
from .catalog import (
    extract_skill_conditions,
    extract_skill_description,
    parse_frontmatter,
)

logger = logging.getLogger(__name__)

REGISTRY_FILENAME = ".skills_registry.sqlite3"
SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class DiscoveryRoot:
    """A filesystem root and its existing discovery precedence."""

    path: Path
    source: str
    priority: int


SCHEMA = """
CREATE TABLE IF NOT EXISTS registry_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skills (
    file_path TEXT PRIMARY KEY,
    root_path TEXT NOT NULL,
    dir_path TEXT NOT NULL,
    directory_name TEXT NOT NULL,
    frontmatter_name TEXT NOT NULL,
    category TEXT,
    description TEXT NOT NULL,
    platforms_json TEXT NOT NULL,
    conditions_json TEXT NOT NULL,
    source TEXT NOT NULL,
    priority INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    deprecated INTEGER NOT NULL DEFAULT 0,
    supersedes TEXT,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(directory_name);
CREATE INDEX IF NOT EXISTS idx_skills_frontmatter_name ON skills(frontmatter_name);
CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category);
"""


def registry_path() -> Path:
    return get_VoidCube_home() / REGISTRY_FILENAME


def discovery_roots(paths: Iterable[str | Path]) -> list[DiscoveryRoot]:
    """Convert catalog-ordered directories into stable discovery roots."""
    result: list[DiscoveryRoot] = []
    seen: set[Path] = set()
    for index, raw_path in enumerate(paths):
        path = Path(raw_path).expanduser().resolve()
        if path in seen:
            continue
        seen.add(path)
        if index == 0:
            source = "home"
        elif index == 1:
            source = "repo"
        else:
            source = "external"
        result.append(DiscoveryRoot(path, source, len(result)))
    return result


def refresh_and_query(
    paths: Iterable[str | Path],
    *,
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Refresh catalog roots and return their indexed records."""
    roots = discovery_roots(paths)
    connection = open_registry(path)
    try:
        refresh_registry(roots, path=path, connection=connection)
        records = query_skills(connection, root_paths=[root.path for root in roots])
        # A temporarily unavailable root is retained for later reconciliation,
        # but stale records must never be exposed as usable skills.
        return [record for record in records if Path(record["file_path"]).is_file()]
    finally:
        connection.close()


def refresh_catalog_index(
    *,
    extra_paths: Iterable[str | Path] = (),
    path: str | Path | None = None,
) -> dict[str, int]:
    """Refresh the canonical catalog roots after a successful file mutation."""
    from .catalog import get_all_skills_dirs

    paths = list(get_all_skills_dirs())
    seen = {Path(item).expanduser().resolve() for item in paths}
    for extra in extra_paths:
        resolved = Path(extra).expanduser().resolve()
        if resolved not in seen:
            paths.append(resolved)
            seen.add(resolved)
    return refresh_registry(discovery_roots(paths), path=path)


def open_registry(path: str | Path | None = None) -> sqlite3.Connection:
    """Open and initialize a registry connection."""
    db_path = Path(path) if path is not None else registry_path()
    db_path = db_path.expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(SCHEMA)
    connection.execute(
        "INSERT OR REPLACE INTO registry_meta(key, value) VALUES('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    connection.commit()
    return connection


def _normalise_root(value: DiscoveryRoot | tuple[str | Path, str, int]) -> DiscoveryRoot:
    if isinstance(value, DiscoveryRoot):
        root = value
    else:
        path, source, priority = value
        root = DiscoveryRoot(Path(path), str(source), int(priority))
    return DiscoveryRoot(root.path.expanduser().resolve(), root.source, root.priority)


def _iter_skill_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return ()
    excluded = {".git", ".github", ".hub"}
    files = []
    # os.scandir 手动遍历：Windows 上比 pathlib rglob 快数倍，
    # 行为等价（不进入符号链接目录、排除目录相同、结果按相对路径排序）。
    stack: list[Path] = [root]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.name in excluded:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.name == "SKILL.md" and entry.is_file(follow_symlinks=False):
                        files.append(Path(entry.path))
        except OSError:
            continue
    return sorted(files, key=lambda item: str(item.relative_to(root)))


def _category_for(path: Path, root: Path) -> str | None:
    parts = path.parent.relative_to(root).parts
    return "/".join(parts[:-1]) if len(parts) > 1 else None


def _record_from_file(path: Path, root: DiscoveryRoot, content: bytes, stat: Any) -> dict[str, Any]:
    text = content.decode("utf-8")
    frontmatter, body = parse_frontmatter(text)
    directory_name = path.parent.name
    platforms = frontmatter.get("platforms") or []
    if isinstance(platforms, str):
        platforms = [platforms]
    return {
        "file_path": str(path.resolve()),
        "root_path": str(root.path),
        "dir_path": str(path.parent.resolve()),
        "directory_name": directory_name,
        "frontmatter_name": str(frontmatter.get("name") or directory_name),
        "category": _category_for(path, root.path),
        "description": extract_skill_description(frontmatter) or _body_description(body),
        "platforms_json": json.dumps([str(item).strip() for item in platforms if str(item).strip()], ensure_ascii=False),
        "conditions_json": json.dumps(extract_skill_conditions(frontmatter), ensure_ascii=False, sort_keys=True),
        "source": root.source,
        "priority": root.priority,
        "mtime_ns": int(stat.st_mtime_ns),
        "size": int(stat.st_size),
        "content_hash": hashlib.sha256(content).hexdigest(),
    }


def _body_description(body: str) -> str:
    """Match the legacy fallback: use the first non-heading body line."""
    for line in body.strip().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:1024]
    return ""


def upsert_skill(connection: sqlite3.Connection, record: Mapping[str, Any]) -> None:
    """Insert or update derived metadata while preserving lifecycle overrides."""
    columns = (
        "file_path", "root_path", "dir_path", "directory_name", "frontmatter_name",
        "category", "description", "platforms_json", "conditions_json", "source",
        "priority", "mtime_ns", "size", "content_hash",
    )
    values = tuple(record[column] for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns[1:])
    connection.execute(
        f"INSERT INTO skills ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(file_path) DO UPDATE SET {updates}, updated_at=datetime('now')",
        values,
    )


def query_skills(
    connection: sqlite3.Connection,
    *,
    name: str | None = None,
    category: str | None = None,
    root_paths: Iterable[str | Path] | None = None,
) -> list[dict[str, Any]]:
    """Return indexed records, ordered by discovery precedence and name."""
    clauses: list[str] = []
    params: list[str] = []
    if name is not None:
        clauses.append("(directory_name = ? OR frontmatter_name = ?)")
        params.extend((name, name))
    if category is not None:
        clauses.append("category = ?")
        params.append(category)
    if root_paths is not None:
        paths = [str(Path(path).expanduser().resolve()) for path in root_paths]
        if not paths:
            return []
        placeholders = ", ".join("?" for _ in paths)
        clauses.append(f"root_path IN ({placeholders})")
        params.extend(paths)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        "SELECT * FROM skills" + where + " ORDER BY priority, directory_name, file_path",
        params,
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["platforms"] = json.loads(item.pop("platforms_json"))
        item["conditions"] = json.loads(item.pop("conditions_json"))
        result.append(item)
    return result


def set_lifecycle_metadata(
    connection: sqlite3.Connection,
    file_path: str | Path,
    *,
    deprecated: bool | None = None,
    supersedes: str | None = None,
    clear_supersedes: bool = False,
) -> None:
    """Update human-maintained lifecycle fields for an indexed file."""
    updates: list[str] = []
    values: list[Any] = []
    if deprecated is not None:
        updates.append("deprecated = ?")
        values.append(int(deprecated))
    if supersedes is not None:
        updates.append("supersedes = ?")
        values.append(supersedes)
    elif clear_supersedes:
        updates.append("supersedes = NULL")
    if not updates:
        return
    values.append(str(Path(file_path).expanduser().resolve()))
    connection.execute(
        f"UPDATE skills SET {', '.join(updates)}, updated_at=datetime('now') WHERE file_path = ?",
        values,
    )
    connection.commit()


def refresh_registry(
    roots: Iterable[DiscoveryRoot | tuple[str | Path, str, int]],
    *,
    path: str | Path | None = None,
    connection: sqlite3.Connection | None = None,
) -> dict[str, int]:
    """Discover roots and refresh only new or content-changed skill files.

    ``connection`` 可选：由调用方复用时传入（热路径避免重复开库）；
    缺省时自建连接，并在结束时关闭。
    """
    normalised = [_normalise_root(root) for root in roots]
    stats = {"added": 0, "reparsed": 0, "reused": 0, "removed": 0, "errors": 0}
    own_connection = connection is None
    connection = connection or open_registry(path)
    try:
        with connection:
            for root in normalised:
                if not root.path.is_dir():
                    continue
                seen: set[str] = set()
                # 批量取该 root 全部已知记录（file_path -> (content_hash, mtime_ns, size)），
                # 避免热路径逐文件 SELECT。
                known = {
                    row[0]: (row[1], row[2], row[3])
                    for row in connection.execute(
                        "SELECT file_path, content_hash, mtime_ns, size FROM skills WHERE root_path = ?",
                        (str(root.path),),
                    )
                }
                for skill_file in _iter_skill_files(root.path):
                    # scandir 基于已 resolve 的 root 构造路径，本身就是规范绝对路径，
                    # 无需再 Path.resolve()（Windows 上每次 ~0.15ms，79 文件共 ~12ms）。
                    file_path = str(skill_file)
                    seen.add(file_path)
                    try:
                        stat = skill_file.stat()
                        existing = known.get(file_path)
                        # 快速路径：mtime+size 未变则内容必然未变，跳过读文件与哈希。
                        # 老记录 mtime_ns 为 NULL 时 int() 抛 TypeError，落入慢路径
                        # 读取内容比对 hash，一致则 UPDATE 补齐 mtime —— 优雅降级。
                        if (
                            existing is not None
                            and int(existing[1]) == int(stat.st_mtime_ns)
                            and int(existing[2]) == int(stat.st_size)
                        ):
                            stats["reused"] += 1
                            continue
                        content = skill_file.read_bytes()
                        content_hash = hashlib.sha256(content).hexdigest()
                        if existing is not None and existing[0] == content_hash:
                            connection.execute(
                                "UPDATE skills SET root_path=?, source=?, priority=?, mtime_ns=?, size=?, updated_at=datetime('now') WHERE file_path=?",
                                (str(root.path), root.source, root.priority, int(stat.st_mtime_ns), int(stat.st_size), file_path),
                            )
                            stats["reused"] += 1
                            continue
                        record = _record_from_file(skill_file, root, content, stat)
                        upsert_skill(connection, record)
                        stats["reparsed" if existing is not None else "added"] += 1
                    except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
                        stats["errors"] += 1
                        logger.debug("Could not index skill %s: %s", skill_file, exc)

                stale = connection.execute(
                    "SELECT file_path FROM skills WHERE root_path = ?", (str(root.path),)
                ).fetchall()
                for row in stale:
                    if row[0] not in seen:
                        connection.execute("DELETE FROM skills WHERE file_path = ?", (row[0],))
                        stats["removed"] += 1
    finally:
        if own_connection:
            connection.close()
    return stats


__all__ = [
    "DiscoveryRoot",
    "discovery_roots",
    "open_registry",
    "query_skills",
    "refresh_catalog_index",
    "refresh_and_query",
    "refresh_registry",
    "registry_path",
    "set_lifecycle_metadata",
    "upsert_skill",
]
