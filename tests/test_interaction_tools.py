from __future__ import annotations

import json

import pytest

from VoidCube_app.interaction_contract import (
    ApprovalDecision,
    ApprovalStatus,
    ClarificationDecision,
    ClarificationStatus,
)
from tools.approval import check_all_command_guards
from tools.clarify_tool import clarify_tool


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_clarify_tool_calls_sink_with_canonical_options() -> None:
    requests = []

    result = json.loads(
        clarify_tool(
            "Deploy where?",
            ["east", "west"],
            sink=lambda request: (
                requests.append(request)
                or ClarificationDecision(
                    ClarificationStatus.ANSWERED,
                    answer="east",
                )
            ),
        )
    )

    assert requests[0].question == "Deploy where?"
    assert requests[0].options == ("east", "west")
    assert result == {
        "success": True,
        "status": "answered",
        "question": "Deploy where?",
        "options": ["east", "west"],
        "answer": "east",
    }


def test_clarify_tool_reports_missing_interaction_port() -> None:
    result = json.loads(clarify_tool("Deploy where?"))

    assert result["success"] is False
    assert result["status"] == "unavailable"
    assert result["answer"] == "Interactive clarification is unavailable."


def test_safe_command_does_not_request_approval() -> None:
    result = check_all_command_guards(
        "echo ok",
        approval_sink=lambda _request: pytest.fail("safe command requested approval"),
    )

    assert result["allowed"] is True
    assert result["approval_required"] is False
    assert result["approval_status"] == "approved"


@pytest.mark.parametrize(
    ("sink", "status"),
    [
        (None, "unavailable"),
        (lambda _request: ApprovalDecision(ApprovalStatus.DENIED), "denied"),
        (lambda _request: "deny", "unavailable"),
    ],
)
def test_dangerous_command_denies_missing_rejected_or_invalid_decisions(sink, status) -> None:
    result = check_all_command_guards("rm -rf target", approval_sink=sink)

    assert result["allowed"] is False
    assert result["approval_required"] is True
    assert result["approval_status"] == status


def test_dangerous_command_runs_only_after_explicit_approval() -> None:
    requests = []

    result = check_all_command_guards(
        "rm -rf target",
        approval_sink=lambda request: (
            requests.append(request)
            or ApprovalDecision(ApprovalStatus.APPROVED)
        ),
    )

    assert result["allowed"] is True
    assert result["approval_required"] is True
    assert result["approval_status"] == "approved"
    assert requests[0].command == "rm -rf target"
