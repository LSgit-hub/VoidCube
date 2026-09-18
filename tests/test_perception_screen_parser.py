from __future__ import annotations

import pytest

from voidcube.systems.perception import (
    OmniParserAdapter,
    ParsedScreen,
    ScreenParserUnavailable,
    normalize_screen_parser_output,
)


def test_normalize_screen_parser_output_keeps_read_only_ui_facts() -> None:
    parsed = normalize_screen_parser_output(
        {
            "elements": [{"type": "button", "label": "导出", "bbox": [1, 2, 3, 4], "click": "ignored"}],
            "visible_text": "预算表",
            "confidence": 0.8,
        },
        backend="omniparser",
    )

    assert isinstance(parsed, ParsedScreen)
    assert parsed.visible_text == ("预算表",)
    assert parsed.elements == ({"type": "button", "label": "导出", "bbox": [1, 2, 3, 4]},)
    assert "click" not in parsed.as_dict()["elements"][0]


def test_omniparser_adapter_supports_injected_backend_and_no_actions() -> None:
    class Backend:
        def parse(self, image_bytes: bytes) -> dict:
            assert image_bytes == b"frame"
            return {"ui_elements": [{"element_type": "dialog", "text": "错误"}]}

    parsed = OmniParserAdapter(Backend()).parse(b"frame")

    assert parsed.backend == "omniparser"
    assert parsed.elements == ({"element_type": "dialog", "text": "错误"},)
    assert not hasattr(OmniParserAdapter, "click")


def test_omniparser_adapter_reports_invalid_backend_output() -> None:
    with pytest.raises(ScreenParserUnavailable, match="must be a mapping"):
        OmniParserAdapter(lambda _image: "not structured").parse(b"frame")
