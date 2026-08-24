from types import SimpleNamespace

from voidcube.interfaces.cli.tui.paste_runtime import PasteRuntimePorts, TuiPasteRuntime


class _Buffer:
    def __init__(self, text="", cursor_position=0):
        self.text = text
        self.cursor_position = cursor_position

    def insert_text(self, value):
        self.text = self.text[: self.cursor_position] + value + self.text[self.cursor_position :]
        self.cursor_position += len(value)


def _runtime(calls, directory):
    return TuiPasteRuntime(
        PasteRuntimePorts(
            should_attach_clipboard_image=lambda text: not text.strip(),
            attach_clipboard_image=lambda: calls.append("attach") or True,
            paste_directory=directory,
            timestamp=lambda: "000000",
            invalidate=lambda _event: calls.append("invalidate"),
        )
    )


def test_bracketed_paste_normalizes_and_compacts_large_text(tmp_path):
    calls = []
    buffer = _Buffer("prefix", 6)
    event = SimpleNamespace(data="\r\none\r\ntwo\r\nthree\r\nfour\r\nfive", current_buffer=buffer)

    _runtime(calls, tmp_path).handle_bracketed_paste(event)

    assert calls == []
    paste_file = next(tmp_path.glob("paste_1_000000_*.txt"))
    assert paste_file.read_text(encoding="utf-8") == (
        "\none\ntwo\nthree\nfour\nfive"
    )
    assert buffer.text.startswith("prefix\n[Pasted text #1: 6 lines")


def test_bracketed_paste_and_text_changed_fallback_use_same_runtime(tmp_path):
    calls = []
    runtime = _runtime(calls, tmp_path)
    event = SimpleNamespace(data=" ", current_buffer=_Buffer())
    runtime.handle_bracketed_paste(event)
    assert calls[:2] == ["attach", "invalidate"]

    buffer = _Buffer("a\nb\nc\nd\ne\nf", 11)
    runtime.handle_text_changed(buffer)
    paste_file = next(tmp_path.glob("paste_1_000000_*.txt"))
    assert paste_file.read_text(encoding="utf-8") == (
        "a\nb\nc\nd\ne\nf"
    )
    assert buffer.text.startswith("[Pasted text #1: 6 lines")


def test_paste_files_are_unique_across_runtime_instances(tmp_path):
    first = _runtime([], tmp_path)
    second = _runtime([], tmp_path)
    text = "a\nb\nc\nd\ne\nf"

    first.handle_text_changed(_Buffer(text, len(text)))
    second.handle_text_changed(_Buffer(text, len(text)))

    assert len(list(tmp_path.glob("paste_1_000000_*.txt"))) == 2
