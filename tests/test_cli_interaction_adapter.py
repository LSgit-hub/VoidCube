from __future__ import annotations

import queue
import threading
from types import SimpleNamespace

import pytest

import voidcube.interfaces.cli.interaction_adapter as adapter
from voidcube.domain.contracts.interaction import (
    ApprovalRequest,
    ApprovalStatus,
    ClarificationRequest,
    ClarificationStatus,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


class _ImmediateQueue:
    response = None

    def put(self, value) -> None:
        self.response = value

    def get(self, timeout=None):
        del timeout
        if self.response is None:
            raise queue.Empty
        return self.response


def _host(**values):
    invalidations = []
    defaults = {
        "_clarify_state": None,
        "_clarify_freetext": False,
        "_clarify_deadline": 0,
        "_approval_state": None,
        "_approval_deadline": 0,
        "_approval_lock": threading.Lock(),
        "_invalidate": lambda: invalidations.append(True),
    }
    defaults.update(values)
    return SimpleNamespace(**defaults), invalidations


def test_clarification_adapter_maps_answer_without_leaking_queue(monkeypatch) -> None:
    answer_queue = _ImmediateQueue()
    answer_queue.response = "staging"
    monkeypatch.setattr(adapter.queue, "Queue", lambda: answer_queue)
    host, invalidations = _host()
    request = ClarificationRequest.create(
        "Which environment?",
        ["staging", "production"],
    )

    decision = adapter.clarification_sink(
        host,
        request,
        timeout=120,
        notify_timeout=lambda _timeout: pytest.fail("unexpected timeout"),
    )

    assert decision.status is ClarificationStatus.ANSWERED
    assert decision.answer == "staging"
    assert host._clarify_state["request"] is request
    assert host._clarify_state["choices"] == ["staging", "production"]
    assert invalidations == [True]


def test_clarification_timeout_clears_adapter_state(monkeypatch) -> None:
    monkeypatch.setattr(adapter.queue, "Queue", _ImmediateQueue)
    host, _ = _host()
    notified = []

    decision = adapter.clarification_sink(
        host,
        ClarificationRequest.create("Which environment?"),
        timeout=0,
        notify_timeout=notified.append,
    )

    assert decision.status is ClarificationStatus.TIMED_OUT
    assert host._clarify_state is None
    assert host._clarify_freetext is False
    assert host._clarify_deadline == 0
    assert notified == [0]


def test_approval_adapter_returns_explicit_decision(monkeypatch) -> None:
    approval_queue = _ImmediateQueue()
    approval_queue.response = ApprovalStatus.APPROVED.value
    monkeypatch.setattr(adapter.queue, "Queue", lambda: approval_queue)
    host, _ = _host()
    request = ApprovalRequest(command="rm -rf target", description="destructive")

    decision = adapter.approval_sink(
        host,
        request,
        timeout=60,
        notify_timeout=lambda: pytest.fail("unexpected timeout"),
    )

    assert decision.status is ApprovalStatus.APPROVED
    assert host._approval_state is None
    assert host._approval_deadline == 0


def test_approval_selection_expands_long_command_before_deciding() -> None:
    response_queue = _ImmediateQueue()
    host, _ = _host(
        _approval_state={
            "request": ApprovalRequest("x" * 71, "destructive"),
            "choices": ["approved", "denied", "view"],
            "selected": 2,
            "response_queue": response_queue,
        }
    )

    adapter.handle_approval_selection(host)

    assert host._approval_state["show_full"] is True
    assert host._approval_state["choices"] == ["approved", "denied"]
    assert host._approval_state["selected"] == 1
    assert response_queue.response is None

    adapter.handle_approval_selection(host)

    assert response_queue.response == ApprovalStatus.DENIED.value
    assert host._approval_state is None


def test_approval_display_reads_structured_request() -> None:
    host, _ = _host(
        _approval_state={
            "request": ApprovalRequest("rm -rf target", "destructive"),
            "choices": ["approved", "denied"],
            "selected": 0,
        }
    )

    rendered = "".join(text for _style, text in adapter.approval_display_fragments(host))

    assert "Dangerous Command" in rendered
    assert "rm -rf target" in rendered
    assert "Approve" in rendered
    assert "Deny" in rendered


def test_approval_display_aligns_borders_with_wide_characters(monkeypatch) -> None:
    from collections import namedtuple

    from voidcube.interfaces.cli.terminal_text_layout import display_width

    size = namedtuple("Size", "columns lines")
    monkeypatch.setattr(
        "voidcube.interfaces.cli.interaction_adapter.shutil.get_terminal_size",
        lambda _fallback: size(100, 40),
    )
    host, _ = _host(
        _approval_state={
            "request": ApprovalRequest(
                "删除 目标目录 🎯 --force",
                "这是一段包含中文和 emoji 的描述文字，用于验证终端宽度对齐。",
            ),
            "choices": ["approved", "denied"],
            "selected": 1,
        }
    )

    fragments = adapter.approval_display_fragments(host)
    visual_lines: list[str] = []
    current = ""
    for _style, text in fragments:
        current += text
        if "\n" in text:
            parts = current.split("\n")
            if parts[0]:
                visual_lines.append(parts[0])
            current = parts[-1]
    if current:
        visual_lines.append(current)

    widths = {display_width(line) for line in visual_lines if line}
    assert widths
    assert len(widths) == 1


def test_approval_command_truncation_respects_cell_width(monkeypatch) -> None:
    from collections import namedtuple

    from voidcube.interfaces.cli.terminal_text_layout import display_width

    size = namedtuple("Size", "columns lines")
    monkeypatch.setattr(
        "voidcube.interfaces.cli.interaction_adapter.shutil.get_terminal_size",
        lambda _fallback: size(100, 40),
    )
    long_command = "中" * 80
    host, _ = _host(
        _approval_state={
            "request": ApprovalRequest(long_command, "destructive"),
            "choices": ["approved", "denied", "view"],
            "selected": 0,
        }
    )

    rendered = "".join(text for _style, text in adapter.approval_display_fragments(host))

    assert long_command not in rendered
    assert "..." in rendered
    # the preview still fits inside the panel width when full text is hidden
    preview_fragment = next(
        text for _style, text in adapter.approval_display_fragments(host) if "..." in text
    )
    assert display_width(preview_fragment) <= 74
