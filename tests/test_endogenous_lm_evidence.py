from systems.supervisor.endogenous_lm_evidence import (
    assemble_lm_evidence_packet,
    build_lm_evidence_packet,
    build_grounding_focus,
    build_lm_evidence_context,
)


def test_lm_evidence_packet_owner_composes_explicit_runtime_evidence_inputs():
    result = build_lm_evidence_packet(
        cognition_charter={},
        policy={"posture_profiles": {"balanced": {}}},
        deliberation_dict={
            "perception": {"active_sessions": 0},
            "world_model": {},
            "reflection": {},
            "adaptive_policy": {},
            "needs": [],
            "intents": [],
            "signals": [],
        },
        drive_input={"checks": {"memory": True}, "idle_seconds": {"memory": 4}},
        drive_context={"drive_history": {}, "completed_learning_tasks": []},
        memory_plan={"eligible_for_planning": True},
        self_learning_plan={},
        autonomous_improvement_plan={},
        shell_slot={},
        external_research_enabled=False,
        external_research_entries=[],
        external_research_files=[],
        repo_root=".",
    )

    assert result["plans"]["memory_maintenance"]["eligible_for_planning"] is True
    assert result["checks"] == {"memory": True}
    assert result["shell_body_profile"]["profile_status"] == "missing_worktree"


def test_lm_evidence_context_owner_composes_posture_and_cognition_layers():
    result = build_lm_evidence_context(
        policy={
            "posture_selection_mode": "manual",
            "active_posture_profile": "balanced",
            "posture_profiles": {"balanced": {"summary": "balanced"}},
        },
        deliberation_dict={
            "perception": {"user_mode": "quiet", "system_posture": "stable"},
            "world_model": {"self_confidence": 0.8},
            "reflection": {"dominant_constraint": "none"},
            "adaptive_policy": {"preferred_focus": "observation"},
            "needs": [],
            "intents": [],
            "signals": [],
        },
        drive_history={},
        recent_learning_evidence=[],
        external_research_evidence=[],
        shell_body_profile={},
        recent_reference_alignment={"available": False},
    )

    assert result["cognitive_posture"]["name"] == "balanced"
    assert result["grounding_focus"]["primary_evidence_nodes"] == []
    assert "current_judgement" in result["meta_cognition_profile"]
    assert result["api_b_judgement_snapshot"] == {}


def test_grounding_focus_projects_ranked_evidence_agenda_and_conflicts():
    result = build_grounding_focus(
        evidence_graph={
            "nodes": [
                {"topic": "low", "avg_confidence": 0.2},
                {"topic": "high", "avg_confidence": 0.9},
            ],
            "contradiction_edges": [
                {"from": "high", "to": "low", "relation": "contradicts"}
            ],
        },
        agenda_graph={
            "focus": "review",
            "unresolved_gaps": [
                {"gap": "small", "priority": 0.2},
                {"gap": "large", "priority": 0.8},
            ],
            "recommended_directions": [{"direction": "repair_truthfulness"}],
        },
        recent_reference_alignment={
            "primary_missing_evidence_node": "high",
            "primary_missing_agenda_node": "review",
        },
        evidence_credibility_summary={"weak_or_missing_channels": ["research"]},
    )

    assert result["primary_evidence_nodes"] == ["high", "low"]
    assert result["primary_agenda_nodes"] == ["focus:review", "large", "small"]
    assert result["contradictory_topics"] == ["high->low:contradicts"]
    assert result["grounding_gaps"] == [
        "missing_evidence:high",
        "missing_agenda:review",
    ]


def test_lm_evidence_packet_assembly_projects_context_and_bounds_lists():
    result = assemble_lm_evidence_packet(
        cognition_charter={},
        memory_plan={"eligible_for_planning": True},
        self_learning_plan={},
        autonomous_improvement_plan={},
        deliberation_dict={"needs": ["need"], "intents": ["intent"], "signals": ["signal"]},
        perception={"active_sessions": 0},
        world_model={},
        reflection={},
        adaptive_policy={},
        cognitive_posture={},
        grounding_focus={},
        self_iteration_hypotheses={},
        meta_cognition_profile={},
        api_b_judgement_snapshot={},
        self_model_snapshot={},
        evidence_credibility_summary={},
        task_type_priors={},
        evidence_channels={"research_digest": {"count": 1}},
        evidence_graph={},
        agenda_graph={},
        recent_reference_alignment={},
        proposal_drift_memory={},
        cognitive_assessment_memory={},
        self_iteration_trend_memory={},
        switch_self_regulation_memory={},
        post_task_effect_memory={},
        recent_learning_titles=[str(i) for i in range(10)],
        recent_learning_evidence=[],
        external_research_evidence=[],
        learning_backlog_titles=[str(i) for i in range(10)],
        body_improvement_backlog_titles=[str(i) for i in range(10)],
        api_b_judgement_tasks=[{"title": str(i)} for i in range(20)],
        checks={},
        idle_seconds={},
        shell_slot={},
        shell_body_profile={},
    )

    assert result["plans"]["memory_maintenance"]["eligible_for_planning"] is True
    assert result["needs"] == ["need"]
    assert result["long_tail_context"]["recent_learning_titles"] == [
        "0",
        "1",
        "2",
        "3",
        "4",
    ]
    assert len(result["recent_learning_titles"]) == 8
    assert len(result["learning_backlog_titles"]) == 8
    assert len(result["api_b_judgement_tasks"]) == 12
