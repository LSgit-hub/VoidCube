from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from memai.schema import CertaintyState, MemoryKind, ProfileMemory
from systems.memory.config import MemoryServiceConfig
from systems.memory.memory_service import MemoryService, RecallRequest, TurnPairCreate
from systems.memory.profile_capture import capture_explicit_user_profile
from systems.memory.profile_store import upsert_profile_memory
from systems.memory.database import open_memory_sqlite


pytestmark = pytest.mark.unit


def _service(tmp_path) -> MemoryService:
    return MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))


async def _add_pair(
    service: MemoryService,
    *,
    text: str,
    write_id: str,
    session_id: str = "profile-session",
    assistant: str = "已了解。",
    owner_id: str = "owner-a",
    workspace_id: str = "workspace-a",
):
    return await service.add_turn_pair(
        TurnPairCreate(
            session_id=session_id,
            user_content=text,
            assistant_content=assistant,
            write_id=write_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
    )


@pytest.mark.parametrize(
    ("text", "predicate", "value"),
    [
        ("我叫李硕", "preferred_name", "李硕"),
        ("Please call me Rowan.", "preferred_name", "Rowan"),
        ("请一直用中文回复", "preferred_language", "中文"),
        ("I prefer Podman as the container runtime.", "container_runtime", "Podman"),
        ("我偏好使用 VS Code 作为编辑器", "editor", "VS Code"),
        ("我的时区是 Asia/Shanghai", "timezone", "Asia/Shanghai"),
        ("请记住，我住在上海", "location", "上海"),
        ("我是一名后端工程师", "occupation", "后端工程师"),
        ("我对花生过敏", "allergy", "花生"),
    ],
)
def test_conservative_capture_accepts_explicit_stable_user_facts(
    text, predicate, value
):
    capture = capture_explicit_user_profile(
        text,
        turn_id="turn-profile",
        timestamp=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )

    assert capture.action == "upsert"
    assert [(item.predicate, item.value) for item in capture.profiles] == [
        (predicate, value)
    ]
    assert capture.profiles[0].certainty_state == CertaintyState.CONFIRMED
    assert capture.profiles[0].evidence_refs == [
        "turn:turn-profile",
        "signal:user_explicit_profile",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "也许我喜欢简洁回答。",
        "这次请简洁回答。",
        "请记住今天我住在杭州。",
        "我可能会改用 Docker。",
        "请记住部署前备份。",
        "我喜欢 Python。",
        "请记住，我的密码是 super-secret。",
        "系统推测用户偏好中文。",
        "I am happy.",
        "Call me when ready.",
        "删除工作目录。",
        "我住在 user@example.com。",
    ],
)
def test_conservative_capture_rejects_uncertain_temporary_sensitive_or_untyped_text(
    text,
):
    capture = capture_explicit_user_profile(
        text,
        turn_id="turn-ignored",
        timestamp=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )

    assert capture.action == "none"
    assert capture.profiles == ()


def test_conservative_capture_recognizes_specific_and_full_revocation():
    specific = capture_explicit_user_profile(
        "忘掉我的名字和语言偏好",
        turn_id="turn-revoke-specific",
        timestamp=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    everything = capture_explicit_user_profile(
        "忘掉关于我的所有信息",
        turn_id="turn-revoke-all",
        timestamp=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )

    assert specific.revoke_predicates == ("preferred_name", "preferred_language")
    assert everything.revoke_predicates == ("*",)


@pytest.mark.asyncio
async def test_turn_pair_atomically_captures_profiles_with_evidence_and_idempotency(
    tmp_path,
):
    service = _service(tmp_path)
    text = "我叫李硕；我偏好使用 Podman 作为容器运行时；请一直用中文回复。"

    first = await _add_pair(service, text=text, write_id="profile-write-1")
    repeated = await _add_pair(service, text=text, write_id="profile-write-1")

    conn = open_memory_sqlite(service._db_path)
    try:
        rows = conn.execute(
            "SELECT predicate, value, certainty_state, evidence_refs, source_turns "
            "FROM profile_memories WHERE owner_id = 'owner-a' "
            "AND workspace_id = 'workspace-a' ORDER BY predicate"
        ).fetchall()
        user_metadata = json.loads(
            conn.execute(
                "SELECT metadata FROM turns WHERE turn_id = ?",
                (first["turn_ids"]["user"],),
            ).fetchone()[0]
        )
    finally:
        conn.close()

    assert first["turn_ids"] == repeated["turn_ids"]
    assert first["profile_settlement"] == {
        "action": "upserted",
        "predicates": [
            "preferred_name",
            "container_runtime",
            "preferred_language",
        ],
        "inserted": 3,
    }
    assert repeated["profile_settlement"]["inserted"] == 0
    assert first["identity_settlement"] is None
    assert user_metadata.get("identity_experience") is not True
    assert [(row[0], row[1], row[2]) for row in rows] == [
        ("container_runtime", "Podman", "confirmed"),
        ("preferred_language", "中文", "confirmed"),
        ("preferred_name", "李硕", "confirmed"),
    ]
    expected_evidence = [
        f"turn:{first['turn_ids']['user']}",
        "signal:user_explicit_profile",
    ]
    assert all(json.loads(row[3]) == expected_evidence for row in rows)
    assert all(json.loads(row[4]) == [first["turn_ids"]["user"]] for row in rows)


@pytest.mark.asyncio
async def test_repeated_confirmation_from_new_turn_merges_profile_evidence(tmp_path):
    service = _service(tmp_path)
    first = await _add_pair(
        service,
        text="请一直用中文回复。",
        write_id="language-first",
    )
    second = await _add_pair(
        service,
        text="我确认仍然希望用中文回复。",
        write_id="language-second",
        session_id="later-session",
    )

    conn = open_memory_sqlite(service._db_path)
    try:
        row = conn.execute(
            "SELECT certainty_state, evidence_refs, source_turns "
            "FROM profile_memories WHERE predicate = 'preferred_language' "
            "AND status = 'active'"
        ).fetchone()
    finally:
        conn.close()

    assert row[0] == "confirmed"
    assert json.loads(row[1]) == [
        f"turn:{first['turn_ids']['user']}",
        "signal:user_explicit_profile",
        f"turn:{second['turn_ids']['user']}",
    ]
    assert json.loads(row[2]) == [
        first["turn_ids"]["user"],
        second["turn_ids"]["user"],
    ]


@pytest.mark.asyncio
async def test_profile_revision_supersedes_old_value_and_recalls_across_sessions(
    tmp_path,
):
    service = _service(tmp_path)
    first = await _add_pair(
        service,
        text="我偏好使用 Podman 作为容器运行时。",
        write_id="runtime-podman",
        session_id="older-session",
    )
    await _add_pair(
        service,
        text="我现在改用 Docker 作为容器运行时。",
        write_id="runtime-docker",
        session_id="new-session",
    )

    conn = open_memory_sqlite(service._db_path)
    try:
        rows = conn.execute(
            "SELECT memory_id, value, status, supersedes FROM profile_memories "
            "WHERE predicate = 'container_runtime' ORDER BY valid_from"
        ).fetchall()
    finally:
        conn.close()
    recalled = await service.recall(
        RecallRequest(
            query="我的首选容器运行时是什么",
            memory_type="profile",
            include_tier1=False,
            owner_id="owner-a",
            workspace_id="workspace-a",
        )
    )

    assert [(row[1], row[2]) for row in rows] == [
        ("Podman", "superseded"),
        ("Docker", "active"),
    ]
    assert json.loads(rows[1][3]) == [rows[0][0]]
    assert first["turn_ids"]["user"]
    assert [item["value"] for item in recalled["results"]] == ["Docker"]


@pytest.mark.asyncio
async def test_name_profile_supports_natural_cross_session_recall(tmp_path):
    service = _service(tmp_path)
    await _add_pair(
        service,
        text="我的名字是李硕。",
        write_id="name-profile",
        session_id="name-session",
    )

    recalled = await service.recall(
        RecallRequest(
            query="我叫什么名字",
            memory_type="profile",
            include_tier1=False,
            owner_id="owner-a",
            workspace_id="workspace-a",
        )
    )

    assert recalled["results"][0]["predicate"] == "preferred_name"
    assert recalled["results"][0]["value"] == "李硕"
    assert recalled["results"][0]["source_turns"]


@pytest.mark.asyncio
async def test_profile_revocation_is_scoped_idempotent_and_blocks_old_reingestion(
    tmp_path,
):
    service = _service(tmp_path)
    owner_a = await _add_pair(service, text="我叫李硕。", write_id="name-owner-a")
    await _add_pair(
        service,
        text="我叫另一位用户。",
        write_id="name-owner-b",
        session_id="other-session",
        workspace_id="workspace-b",
    )
    recalled_before_revocation = await service.recall(
        RecallRequest(
            query="我叫什么名字 李硕",
            owner_id="owner-a",
            workspace_id="workspace-a",
        )
    )
    conn = open_memory_sqlite(service._db_path)
    try:
        stamp = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, compressed_at, "
            "status, weight, owner_id, workspace_id) "
            "VALUES ('derived-name-event', 'event', '用户称呼', '用户称呼是李硕', "
            "?, ?, 0.8, 0.9, '[]', '[]', ?, ?, 'active', 0.8, 'owner-a', 'workspace-a')",
            (
                stamp,
                stamp,
                json.dumps([owner_a["turn_ids"]["user"]]),
                stamp,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    revoked = await _add_pair(
        service,
        text="忘掉我的名字。",
        write_id="revoke-name",
        session_id="revoke-session",
    )
    recalled_after_revocation = await service.recall(
        RecallRequest(
            query="我叫什么名字 李硕",
            owner_id="owner-a",
            workspace_id="workspace-a",
        )
    )
    repeated = await _add_pair(
        service,
        text="忘掉我的名字。",
        write_id="revoke-name",
        session_id="revoke-session",
    )

    old_time = datetime.now(timezone.utc) - timedelta(days=30)
    old_profile = ProfileMemory.create(
        memory_kind=MemoryKind.FACT,
        subject="user",
        predicate="preferred_name",
        value="李硕",
        summary="用户明确要求称呼为李硕。",
        confidence=0.95,
        certainty_state=CertaintyState.CONFIRMED,
        valid_from=old_time,
        evidence_refs=["turn:old-archived-turn"],
        source_turns=["old-archived-turn"],
    )
    conn = open_memory_sqlite(service._db_path)
    try:
        old_written = upsert_profile_memory(
            conn,
            old_profile,
            owner_id="owner-a",
            workspace_id="workspace-a",
            now=datetime.now(timezone.utc).isoformat(),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT workspace_id, value FROM profile_memories "
            "WHERE predicate = 'preferred_name' ORDER BY workspace_id"
        ).fetchall()
        tombstone = conn.execute(
            "SELECT source_turn_id, evidence_turns FROM profile_memory_tombstones "
            "WHERE owner_id = 'owner-a' AND workspace_id = 'workspace-a' "
            "AND predicate = 'preferred_name'"
        ).fetchone()
        audit_count = conn.execute(
            "SELECT COUNT(*) FROM memory_deletion_audit "
            "WHERE target_kind = 'profile_predicate'"
        ).fetchone()[0]
        trace = conn.execute(
            "SELECT result_count, selected_results FROM recall_traces "
            "WHERE trace_id = ?",
            (recalled_before_revocation["trace_id"],),
        ).fetchone()
        derived_count = conn.execute(
            "SELECT COUNT(*) FROM compressed_memories "
            "WHERE memory_id = 'derived-name-event'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert revoked["profile_settlement"]["action"] == "revoked"
    assert repeated["profile_settlement"]["action"] == "already_revoked"
    assert recalled_after_revocation["results"] == []
    assert old_written == 0
    assert rows == [("workspace-b", "另一位用户")]
    assert tombstone[0] == revoked["turn_ids"]["user"]
    assert json.loads(tombstone[1])
    assert audit_count == 1
    assert trace == (0, "[]")
    assert derived_count == 0


@pytest.mark.asyncio
async def test_new_explicit_value_after_revocation_reactivates_profile(tmp_path):
    service = _service(tmp_path)
    await _add_pair(service, text="我叫旧名字。", write_id="old-name")
    await _add_pair(
        service,
        text="忘掉我的名字。",
        write_id="remove-name",
        session_id="remove-session",
    )
    restored = await _add_pair(
        service,
        text="以后请叫我新名字。",
        write_id="new-name",
        session_id="new-name-session",
    )

    conn = open_memory_sqlite(service._db_path)
    try:
        rows = conn.execute(
            "SELECT value, status FROM profile_memories "
            "WHERE predicate = 'preferred_name'"
        ).fetchall()
        tombstones = conn.execute(
            "SELECT COUNT(*) FROM profile_memory_tombstones "
            "WHERE predicate = 'preferred_name'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert restored["profile_settlement"]["action"] == "upserted"
    assert rows == [("新名字", "active")]
    assert tombstones == 1


@pytest.mark.asyncio
async def test_assistant_inference_never_creates_user_profile(tmp_path):
    service = _service(tmp_path)

    result = await _add_pair(
        service,
        text="我们继续处理这个任务。",
        assistant="我推测你喜欢中文和简洁回答。",
        write_id="assistant-inference",
    )
    conn = open_memory_sqlite(service._db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM profile_memories").fetchone()[0]
    finally:
        conn.close()

    assert result["profile_settlement"] == {"action": "none"}
    assert count == 0
