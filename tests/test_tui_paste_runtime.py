from pathlib import Path
from types import SimpleNamespace

from VoidCube_cli.tui_paste_runtime import PasteRuntimePorts, TuiPasteRuntime


class _Buffer:
    def __init__(self, text="", cursor_position=0):
        self.text = text
        self.cursor_position = cursor_position

    def insert_text(self, value):
        self.text = self.text[: self.cursor_position] + value + self.text[self.cursor_position :]
        self.cursor_position += len(value)


def _runtime(calls):
    return TuiPasteRuntime(
        PasteRuntimePorts(
            should_attach_clipboard_image=lambda text: not text.strip(),
            attach_clipboard_image=lambda: calls.append("attach") or True,
            write_paste_file=lambda text, number: calls.append(("write", text, number))
            or Path(f"/tmp/paste-{number}.txt"),
            invalidate=lambda _event: calls.append("invalidate"),
        )
    )


def test_bracketed_paste_normalizes_and_compacts_large_text():
    calls = []
    buffer = _Buffer("prefix", 6)
    event = SimpleNamespace(data="\r\none\r\ntwo\r\nthree\r\nfour\r\nfive", current_buffer=buffer)

    _runtime(calls).handle_bracketed_paste(event)

    assert calls == [("write", "\none\ntwo\nthree\nfour\nfive", 1)]
    assert buffer.text.startswith("prefix\n[Pasted text #1: 6 lines")


def test_image_gestures_and_text_changed_fallback_use_same_runtime():
    calls = []
    runtime = _runtime(calls)
    event = SimpleNamespace(data=" ", current_buffer=_Buffer())
    runtime.handle_bracketed_paste(event)
    runtime.handle_image_paste(event)
    assert calls[:3] == ["attach", "invalidate", "attach"]

    buffer = _Buffer("a\nb\nc\nd\ne\nf", 11)
    runtime.handle_text_changed(buffer)
    assert ("write", "a\nb\nc\nd\ne\nf", 1) in calls
    assert buffer.text.startswith("[Pasted text #1: 6 lines")
