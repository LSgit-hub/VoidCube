"""自审/诊断类 Tier2 记忆隔离迁移的回归测试。"""

from __future__ import annotations

import sqlite3

from memai.migrations.schema import MemoryDatabaseBootstrap


def _connection(tmp_path):
    connection = sqlite3.connect(tmp_path / "diag.db")
    connection.execute(
        "CREATE TABLE compressed_memories ("
        "memory_id TEXT, title TEXT, hidden INTEGER DEFAULT 0, "
        "owner_id TEXT, workspace_id TEXT, memory_domain TEXT)"
    )
    connection.execute(
        "CREATE TABLE memory_deletion_audit ("
        "audit_id TEXT PRIMARY KEY, memory_domain TEXT, target_kind TEXT, "
        "target_hash TEXT, reason TEXT, deleted_counts TEXT, "
        "owner_id TEXT, workspace_id TEXT, created_at TEXT)"
    )
    return connection


def _insert(connection, memory_id, title, hidden=0):
    connection.execute(
        "INSERT INTO compressed_memories "
        "(memory_id, title, hidden, owner_id, workspace_id, memory_domain) "
        "VALUES (?, ?, ?, 'local-user', 'VoidCube', 'agent_interaction')",
        (memory_id, title, hidden),
    )


def test_quarantine_hides_self_audit_memories_only(tmp_path):
    connection = _connection(tmp_path)
    try:
        cursor = connection.cursor()
        _insert(cursor, "m1", "记忆系统健康审计 2026-09-04：主压缩链停滞")
        _insert(cursor, "m2", "技能库审计中 pytest 未能运行")
        _insert(cursor, "m3", "用户偏好：喜欢周杰伦的歌")  # 正常记忆，不得误伤
        _insert(cursor, "m4", "记忆系统自检：数据层完整", hidden=1)  # 已隔离

        quarantined = MemoryDatabaseBootstrap._quarantine_diagnostic_memories(cursor)

        assert quarantined == 2
        hidden = dict(
            cursor.execute(
                "SELECT memory_id, hidden FROM compressed_memories"
            ).fetchall()
        )
        assert hidden["m1"] == 1
        assert hidden["m2"] == 1
        assert hidden["m3"] == 0
        assert hidden["m4"] == 1

        audits = cursor.execute(
            "SELECT audit_id, reason, target_kind FROM memory_deletion_audit"
        ).fetchall()
        assert len(audits) == 2
        assert all(row[2] == "compressed_memory_quarantine" for row in audits)
        assert all("self-audit" in row[1] for row in audits)

        # 幂等：再次执行不再改动
        assert MemoryDatabaseBootstrap._quarantine_diagnostic_memories(cursor) == 0
    finally:
        connection.close()


def test_quarantine_keeps_data_recoverable(tmp_path):
    """隔离是置位而非删除：行与标题必须原样保留，便于回溯/恢复。"""
    connection = _connection(tmp_path)
    try:
        cursor = connection.cursor()
        _insert(cursor, "m1", "记忆系统全面自检：整体 7.8/10")

        MemoryDatabaseBootstrap._quarantine_diagnostic_memories(cursor)

        row = cursor.execute(
            "SELECT title FROM compressed_memories WHERE memory_id = 'm1'"
        ).fetchone()
        assert row is not None
        assert row[0] == "记忆系统全面自检：整体 7.8/10"
    finally:
        connection.close()
