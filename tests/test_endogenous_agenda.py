from voidcube.systems.supervisor.endogenous_agenda import build_agenda_graph


def test_agenda_graph_projects_needs_intents_signals_and_evidence_edges():
    result = build_agenda_graph(
        deliberation_dict={
            "adaptive_policy": {
                "preferred_focus": "truthfulness",
                "candidate_throttle": 0.4,
                "observation_bias": 0.8,
            },
            "needs": [
                {
                    "need_type": "repair_truthfulness",
                    "urgency": 0.9,
                    "severity": 0.8,
                    "rationale": "recent references need review",
                }
            ],
            "intents": [
                {
                    "intent_type": "repair_reference_alignment",
                    "candidate_kind": "truthfulness_review",
                    "priority": 0.75,
                    "source_needs": ["repair_truthfulness"],
                    "target_horizon": "now",
                }
            ],
            "signals": [
                {
                    "signal_type": "truthfulness_alert",
                    "priority": 0.6,
                    "related_intent": "repair_reference_alignment",
                    "message": "alignment is weak",
                }
            ],
        },
        evidence_graph={
            "nodes": [
                {
                    "topic": "external_research",
                    "net_signal": 0.3,
                    "avg_confidence": 0.8,
                }
            ]
        },
    )

    assert result["focus"] == "truthfulness"
    assert result["current_topics"][0]["status"] == "supported"
    assert result["unresolved_gaps"][0]["gap"] == "repair_truthfulness"
    assert result["recommended_directions"][0]["task_type"] == "review"
    assert {edge["relation"] for edge in result["relation_edges"]} == {
        "elevates_direction",
        "amplifies_direction",
        "shapes_focus",
    }
    assert result["evidence_to_gap_edges"][0]["from"] == "external_research"
    assert result["direction_task_links"][0]["to_candidate_kind"] == "truthfulness_review"


def test_agenda_graph_uses_default_focus_and_discards_invalid_rows():
    result = build_agenda_graph(
        deliberation_dict={
            "needs": [None, {"need_type": "", "urgency": 1.0}],
            "intents": [None, {"intent_type": "", "priority": 1.0}],
            "signals": [None, {"signal_type": "", "priority": 1.0}],
        },
        evidence_graph={"nodes": [None, {"topic": ""}]},
    )

    assert result["focus"] == "observation"
    assert result["current_topics"] == []
    assert result["unresolved_gaps"] == []
    assert result["recommended_directions"] == []
    assert result["active_signals"] == []
