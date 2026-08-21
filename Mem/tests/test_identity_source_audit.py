from scripts.audit_identity_sources import classify_identity_record


def test_audit_accepts_only_complete_stellar_first_person_evidence():
    record = {
        "memory_id": "legacy-self",
        "identity_layer": "experience",
        "origin_type": "self_authored_experience",
        "identity_metadata": '{"agency":"chosen"}',
    }
    turns = [
        {
            "turn_id": "self-turn",
            "speaker": "agent",
            "metadata": (
                '{"verified":true,"self_authored_identity":true,'
                '"verified_by":"stellar_companion",'
                '"self_claim":"我选择记住它",'
                '"what_changed":"我理解发生了变化",'
                '"continuity_impact":"它连接了我的现在"}'
            ),
        }
    ]

    result = classify_identity_record(record, turns)

    assert result["classification"] == "self_experience_candidate"
    assert result["speaker"] == ["agent"]
    assert result["verified"] is True
    assert result["self_authored_identity"] is True
    assert result["self_fields"] == {
        "self_claim": True,
        "what_changed": True,
        "continuity_impact": True,
    }


def test_audit_rejects_user_turn_even_when_legacy_flags_are_present():
    record = {
        "memory_id": "legacy-user",
        "identity_layer": "experience",
        "origin_type": "conversation",
        "identity_metadata": "{}",
    }
    turns = [
        {
            "turn_id": "user-turn",
            "speaker": "user",
            "metadata": (
                '{"verified":true,"self_authored_identity":true,'
                '"verified_by":"stellar_companion",'
                '"self_claim":"用户要求我记住",'
                '"what_changed":"系统记录增加",'
                '"continuity_impact":"无"}'
            ),
        }
    ]

    result = classify_identity_record(record, turns)

    assert result["classification"] == "relationship_history_candidate"


def test_audit_separates_founding_and_governance_records():
    founding = classify_identity_record(
        {
            "memory_id": "anchor",
            "identity_layer": "founding",
            "origin_type": "identity_anchor",
        }
    )
    governance = classify_identity_record(
        {
            "memory_id": "revision",
            "identity_layer": "governance_history",
            "origin_type": "identity_revision",
        }
    )

    assert founding["classification"] == "founding"
    assert governance["classification"] == "governance_history_candidate"
