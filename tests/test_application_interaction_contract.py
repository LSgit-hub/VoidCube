from __future__ import annotations

import pytest

from voidcube.domain.contracts.interaction import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    ClarificationDecision,
    ClarificationRequest,
    ClarificationStatus,
    resolve_approval,
    resolve_clarification,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_approval_resolution_is_explicit_and_fails_closed() -> None:
    request = ApprovalRequest(command="rm -rf target", description="destructive")

    approved = resolve_approval(
        request,
        lambda received: ApprovalDecision(
            ApprovalStatus.APPROVED,
            reason=received.description,
        ),
    )
    missing = resolve_approval(request, None)
    invalid = resolve_approval(request, lambda _request: "deny")
    invalid_status = resolve_approval(
        request,
        lambda _request: ApprovalDecision("approved"),
    )

    assert approved.approved is True
    assert approved.reason == "destructive"
    assert missing.status is ApprovalStatus.UNAVAILABLE
    assert missing.approved is False
    assert invalid.status is ApprovalStatus.UNAVAILABLE
    assert invalid.approved is False
    assert invalid_status.status is ApprovalStatus.UNAVAILABLE


def test_approval_sink_failure_becomes_unavailable_decision() -> None:
    def fail(_request):
        raise RuntimeError("renderer stopped")

    decision = resolve_approval(
        ApprovalRequest(command="format disk", description="destructive"),
        fail,
    )

    assert decision.status is ApprovalStatus.UNAVAILABLE
    assert decision.approved is False
    assert "renderer stopped" in decision.reason


def test_clarification_resolution_preserves_request_and_status() -> None:
    request = ClarificationRequest.create(
        "Which environment?",
        ["staging", "production"],
    )
    received = []

    decision = resolve_clarification(
        request,
        lambda value: (
            received.append(value)
            or ClarificationDecision(
                ClarificationStatus.ANSWERED,
                answer="staging",
            )
        ),
    )

    assert received == [request]
    assert request.options == ("staging", "production")
    assert decision.response_for_agent() == "staging"
    assert ClarificationRequest.create("One option", "staging").options == (
        "staging",
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ClarificationStatus.CANCELLED, "cancelled"),
        (ClarificationStatus.TIMED_OUT, "time limit"),
    ],
)
def test_non_answer_clarification_statuses_have_agent_guidance(status, expected) -> None:
    response = ClarificationDecision(status).response_for_agent()

    assert expected in response
    assert "best judgement" in response


def test_invalid_clarification_answers_are_unavailable() -> None:
    request = ClarificationRequest.create("Which environment?")

    empty = resolve_clarification(
        request,
        lambda _request: ClarificationDecision(
            ClarificationStatus.ANSWERED,
            answer="  ",
        ),
    )
    invalid = resolve_clarification(request, lambda _request: "staging")
    invalid_status = resolve_clarification(
        request,
        lambda _request: ClarificationDecision("answered", answer="staging"),
    )
    invalid_answer = resolve_clarification(
        request,
        lambda _request: ClarificationDecision(
            ClarificationStatus.ANSWERED,
            answer=3,
        ),
    )
    missing = resolve_clarification(request, None)

    assert empty.status is ClarificationStatus.UNAVAILABLE
    assert invalid.status is ClarificationStatus.UNAVAILABLE
    assert invalid_status.status is ClarificationStatus.UNAVAILABLE
    assert invalid_answer.status is ClarificationStatus.UNAVAILABLE
    assert missing.status is ClarificationStatus.UNAVAILABLE
