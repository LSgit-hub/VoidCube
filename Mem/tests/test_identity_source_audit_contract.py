import sqlite3

from scripts.audit_identity_sources import audit


def test_audit_exposes_non_mutating_migration_policy(tmp_path):
    db_path = tmp_path / "identity.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE compressed_memories (
                memory_id TEXT PRIMARY KEY,
                identity_layer TEXT,
                origin_type TEXT,
                origin_id TEXT,
                evidence_refs TEXT,
                source_turns TEXT,
                identity_metadata TEXT
            );
            CREATE TABLE turns (
                turn_id TEXT PRIMARY KEY,
                speaker TEXT,
                metadata TEXT
            );
            INSERT INTO compressed_memories VALUES
                ('anchor', 'founding', NULL, NULL, '[]', '[]', '{}'),
                ('user-event', 'experience', 'conversation', 'turn:user-1', '[]', '[]', '{}');
            INSERT INTO turns VALUES ('user-1', 'user', '{}');
            """
        )
        connection.commit()
    finally:
        connection.close()

    before = sqlite3.connect(db_path)
    try:
        before_count = before.execute("SELECT COUNT(*) FROM compressed_memories").fetchone()[0]
    finally:
        before.close()

    result = audit(db_path)

    assert result["migration_policy"]["automatic_migration"] is False
    assert result["classification_counts"] == {
        "founding": 1,
        "relationship_history_candidate": 1,
    }
    after = sqlite3.connect(db_path)
    try:
        assert after.execute("SELECT COUNT(*) FROM compressed_memories").fetchone()[0] == before_count
    finally:
        after.close()
