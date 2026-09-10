"""时间戳时区归一化迁移的回归测试。"""

from __future__ import annotations

import sqlite3

from memai.migrations.schema import MemoryDatabaseBootstrap


def _cursor_with(tmp_path):
    connection = sqlite3.connect(tmp_path / "tz.db")
    connection.execute(
        "CREATE TABLE turns (turn_id TEXT, timestamp TEXT, last_decay_at TEXT)"
    )
    connection.execute("CREATE TABLE turns_archive (turn_id TEXT, timestamp TEXT)")
    return connection


def test_normalizes_offset_timestamps_to_canonical_utc(tmp_path):
    connection = _cursor_with(tmp_path)
    try:
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO turns VALUES (?, ?, ?)",
            ("local", "2026-08-27T18:26:14.993915+08:00", "2026-08-27T18:26:14.993915+08:00"),
        )
        cursor.execute(
            "INSERT INTO turns VALUES (?, ?, ?)",
            ("canonical", "2026-09-10T11:34:36.629507+00:00", None),
        )
        cursor.execute(
            "INSERT INTO turns VALUES (?, ?, ?)",
            ("naive", "2026-09-07 05:05:46", None),
        )

        normalized = MemoryDatabaseBootstrap._normalize_timestamp_timezones(cursor)

        # 两条 +08:00 值被改写；规范值与 naive 值不动
        assert normalized == 2
        row = cursor.execute(
            "SELECT timestamp, last_decay_at FROM turns WHERE turn_id = 'local'"
        ).fetchone()
        # 时刻不变：18:26+08:00 == 10:26+00:00
        assert row[0] == "2026-08-27T10:26:14.993915+00:00"
        assert row[1] == "2026-08-27T10:26:14.993915+00:00"

        unchanged = cursor.execute(
            "SELECT timestamp FROM turns WHERE turn_id = 'canonical'"
        ).fetchone()[0]
        assert unchanged == "2026-09-10T11:34:36.629507+00:00"

        naive = cursor.execute(
            "SELECT timestamp FROM turns WHERE turn_id = 'naive'"
        ).fetchone()[0]
        assert naive == "2026-09-07 05:05:46"

        # 幂等：再跑一次不再改动任何行
        assert MemoryDatabaseBootstrap._normalize_timestamp_timezones(cursor) == 0
    finally:
        connection.close()


def test_normalization_makes_string_comparisons_consistent(tmp_path):
    """归一化后，字符串比较与真实时间先后一致（此前会因偏移差 8 小时而错序）。"""
    connection = _cursor_with(tmp_path)
    try:
        cursor = connection.cursor()
        # 真实先后：earlier(+08:00 12:00 == 04:00Z) 早于 later(05:00Z)
        cursor.execute(
            "INSERT INTO turns VALUES (?, ?, ?)", ("earlier", "2026-08-27T12:00:00+08:00", None)
        )
        cursor.execute(
            "INSERT INTO turns VALUES (?, ?, ?)", ("later", "2026-08-27T05:00:00+00:00", None)
        )

        before = [
            row[0]
            for row in cursor.execute(
                "SELECT turn_id FROM turns ORDER BY timestamp ASC"
            ).fetchall()
        ]
        # 纯字符串比较给出 '05:00+00:00' < '12:00+08:00'，与真实时刻顺序相反
        assert before == ["later", "earlier"]

        MemoryDatabaseBootstrap._normalize_timestamp_timezones(cursor)

        after = [
            row[0]
            for row in cursor.execute(
                "SELECT turn_id FROM turns ORDER BY timestamp ASC"
            ).fetchall()
        ]
        # 归一化后（12:00+08:00 → 04:00+00:00）字符串序与真实时刻一致
        assert after == ["earlier", "later"]
    finally:
        connection.close()
