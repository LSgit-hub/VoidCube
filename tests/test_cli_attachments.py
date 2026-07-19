from pathlib import Path

import pytest

from VoidCube_cli.attachments import (
    _collect_query_images,
    _detect_file_drop,
    _format_image_attachment_badges,
    _resolve_attachment_path,
    _should_auto_attach_clipboard_image_on_paste,
    _split_path_input,
    _termux_example_image_path,
)


pytestmark = pytest.mark.smoke


@pytest.mark.unit
def test_split_path_input_supports_quoted_and_escaped_spaces():
    assert _split_path_input('"/tmp/my image.png" describe it') == (
        "/tmp/my image.png",
        "describe it",
    )
    assert _split_path_input("/tmp/my\\ image.png describe it") == (
        "/tmp/my image.png",
        "describe it",
    )


@pytest.mark.unit
def test_resolve_attachment_path_uses_terminal_cwd(monkeypatch, tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))

    assert _resolve_attachment_path("image.png") == image.resolve()


@pytest.mark.unit
def test_detect_file_drop_accepts_platform_absolute_path(tmp_path):
    image = tmp_path / "image with space.png"
    image.write_bytes(b"png")

    detected = _detect_file_drop(f'"{image}" describe it')

    assert detected == {
        "path": image.resolve(),
        "is_image": True,
        "remainder": "describe it",
    }


@pytest.mark.unit
def test_collect_query_images_deduplicates_drop_and_explicit_argument(tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"png")

    message, images = _collect_query_images(f'"{image}" inspect', str(image))

    assert message == "inspect"
    assert images == [image.resolve()]


@pytest.mark.unit
def test_collect_query_images_rejects_non_image_argument(tmp_path):
    text_file = tmp_path / "notes.txt"
    text_file.write_text("notes", encoding="utf-8")

    with pytest.raises(ValueError, match="Not a supported image file"):
        _collect_query_images("inspect", str(text_file))


@pytest.mark.unit
def test_attachment_badges_adapt_to_terminal_width():
    images = [Path("first.png"), Path("second.png")]

    assert _format_image_attachment_badges(images, 2, width=40) == (
        "[📎 2 images attached]"
    )
    assert _format_image_attachment_badges(images, 2, width=100) == (
        "[📎 Image #1] [📎 Image #2]"
    )


@pytest.mark.unit
def test_clipboard_auto_attach_requires_image_only_paste():
    assert _should_auto_attach_clipboard_image_on_paste("  ") is True
    assert _should_auto_attach_clipboard_image_on_paste("text") is False


@pytest.mark.unit
def test_termux_example_path_is_always_posix():
    assert _termux_example_image_path("sample.png") == (
        "~/storage/shared/Pictures/sample.png"
    )
