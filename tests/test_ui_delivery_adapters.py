import pytest
from fastapi import HTTPException

from voidcube.systems.supervisor.ui_delivery_adapters import normalize_delivery_body


def _body(**overrides):
    body = {
        "url": "https://example.com/report.pdf",
        "title": "报告",
        "type": "document",
    }
    body.update(overrides)
    return body


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mode", "auto_evolution"),
        ("stellar_mode", "auto"),
        ("requested_via", "autonomous_worker"),
        ("source_kind", "autonomous_chain"),
        ("autonomous_task_id", "task-1"),
    ],
)
def test_auto_delivery_is_rejected_at_http_boundary(field, value):
    with pytest.raises(HTTPException) as error:
        normalize_delivery_body(_body(**{field: value}))

    assert error.value.status_code == 403
    assert "回写 Mem" in str(error.value.detail)


def test_assist_delivery_keeps_source_audit_fields():
    normalized = normalize_delivery_body(
        _body(
            mode="daily_companion",
            requested_via="companion_media",
            source_task_id="assist-task-1",
        )
    )

    assert normalized["mode"] == "daily_companion"
    assert normalized["requested_via"] == "companion_media"
    assert normalized["source_task_id"] == "assist-task-1"
