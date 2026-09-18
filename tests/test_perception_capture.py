from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from voidcube.systems.perception import MssScreenCapture, ScreenCaptureUnavailable


def test_capture_config_validates_monitor_and_roi() -> None:
    with pytest.raises(ValueError, match="monitor"):
        MssScreenCapture(monitor=-1)
    with pytest.raises(ValueError, match="exactly"):
        MssScreenCapture(roi={"left": 0, "top": 0, "width": 10})
    with pytest.raises(ValueError, match="positive"):
        MssScreenCapture(roi={"left": 0, "top": 0, "width": 0, "height": 10})


def test_capture_backend_is_lazy_and_reports_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "mss", None)

    with pytest.raises(ScreenCaptureUnavailable, match="optional 'mss'"):
        MssScreenCapture().capture()


def test_capture_reads_pixels_without_exposing_control_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeShot:
        width = 2
        height = 1
        raw = b"pixels"

    class FakeBackend:
        monitors = [{}, {"left": 0, "top": 0, "width": 2, "height": 1}]

        def grab(self, area: dict[str, int]) -> FakeShot:
            assert area == {"left": 0, "top": 0, "width": 2, "height": 1}
            return FakeShot()

        def close(self) -> None:
            pass

    fake_mss = ModuleType("mss")
    fake_mss.mss = lambda: FakeBackend()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mss", fake_mss)

    capture = MssScreenCapture()
    frame = capture.capture()

    assert frame.pixels == b"pixels"
    assert frame.width == 2
    assert frame.height == 1
    assert not hasattr(capture, "click")
    capture.close()


def test_screen_frame_encodes_raw_bgra_for_vision() -> None:
    from voidcube.systems.perception import ScreenFrame

    frame = ScreenFrame(b"\x00\x00\xff\xff", 1, 1, 1, __import__("datetime").datetime.now())

    assert frame.to_png().startswith(b"\x89PNG")
