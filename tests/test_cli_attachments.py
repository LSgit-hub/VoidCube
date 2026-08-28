from pathlib import Path

import pytest

from voidcube.interfaces.cli.attachments import (
    _collect_query_images,
    _detect_file_drop,
    _format_image_attachment_badges,
    _resolve_attachment_path,
    _should_auto_attach_clipboard_image_on_paste,
    _split_path_input,
    _termux_example_image_path,
)
from voidcube.interfaces.cli.commands.handlers.attachments import (
    AttachmentCommandPorts,
    ImageCommandPorts,
    ImageCommandText,
    PasteCommandPorts,
    PasteCommandText,
    handle_image_command,
    handle_attachment_command,
    handle_paste_command,
)
from voidcube.interfaces.cli.commands.router import parse_cli_command


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
def test_attachment_badges_truncate_wide_filenames_by_display_width():
    from voidcube.interfaces.cli.terminal_text_layout import display_width

    # 12 codepoints but 72 terminal cells — a naive len() truncation would
    # let it overrun the 20-cell badge slot.
    wide_name = "照片文件名称" * 6 + ".png"
    images = [Path(wide_name)]

    badge = _format_image_attachment_badges(images, 1, width=40)

    assert "..." in badge
    # Fixed chrome "[📎 name]" is 6 cells; the name must stay within 20.
    assert display_width(badge) <= 26


@pytest.mark.unit
def test_clipboard_auto_attach_requires_image_only_paste():
    assert _should_auto_attach_clipboard_image_on_paste("  ") is True
    assert _should_auto_attach_clipboard_image_on_paste("text") is False


@pytest.mark.unit
def test_termux_example_path_is_always_posix():
    assert _termux_example_image_path("sample.png") == (
        "~/storage/shared/Pictures/sample.png"
    )


def _paste_ports(
    output: list[str],
    *,
    termux: bool = False,
    has_image: bool = False,
    attach=lambda: False,
    count: int = 0,
) -> PasteCommandPorts:
    return PasteCommandPorts(
        is_termux=lambda: termux,
        has_clipboard_image=lambda: has_image,
        attach_clipboard_image=attach,
        attachment_count=lambda: count,
        emit=output.append,
        text=PasteCommandText(
            termux_unavailable="termux unavailable",
            extraction_failed="extraction failed",
            no_image="no image",
        ),
    )


@pytest.mark.unit
def test_paste_handler_stops_before_clipboard_check_on_termux():
    output: list[str] = []

    handle_paste_command(
        parse_cli_command("/paste"),
        ports=_paste_ports(
            output,
            termux=True,
            attach=lambda: pytest.fail("Termux must not extract clipboard"),
        ),
    )

    assert output == ["termux unavailable"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("has_image", "attached", "expected"),
    [
        (False, False, "no image"),
        (True, False, "extraction failed"),
        (True, True, "  📎 Image #3 attached from clipboard"),
    ],
)
def test_paste_handler_projects_clipboard_outcomes(
    has_image: bool,
    attached: bool,
    expected: str,
):
    output: list[str] = []

    handle_paste_command(
        parse_cli_command("/paste"),
        ports=_paste_ports(
            output,
            has_image=has_image,
            attach=lambda: attached,
            count=3,
        ),
    )

    assert output == [expected]


def _image_ports(
    output: list[str],
    attached: list[Path],
    *,
    termux: bool = False,
    resolved: Path | None = None,
) -> ImageCommandPorts:
    return ImageCommandPorts(
        is_termux=lambda: termux,
        split_path=_split_path_input,
        resolve_path=lambda _value: resolved,
        supported_extensions={".png", ".jpg"},
        append_attachment=attached.append,
        termux_example_path=_termux_example_image_path,
        emit=output.append,
        text=ImageCommandText(
            dim_prefix="<dim>",
            reset_suffix="</dim>",
            tip_prefix="Tip:",
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("termux", "expected_hint"),
    [(False, "/path/to/image.png"), (True, "~/storage/shared/Pictures/cat.png")],
)
def test_image_handler_projects_platform_usage_hint(
    termux: bool,
    expected_hint: str,
):
    output: list[str] = []

    handle_image_command(
        parse_cli_command("/image"),
        ports=_image_ports(output, [], termux=termux),
    )

    assert output == [
        f"  <dim>Usage: /image <path>  e.g. /image {expected_hint}</dim>"
    ]


@pytest.mark.unit
def test_image_handler_rejects_missing_and_unsupported_files():
    missing_output: list[str] = []
    unsupported_output: list[str] = []

    handle_image_command(
        parse_cli_command("/image Missing File.png"),
        ports=_image_ports(missing_output, []),
    )
    handle_image_command(
        parse_cli_command("/image notes.txt"),
        ports=_image_ports(unsupported_output, [], resolved=Path("notes.txt")),
    )

    assert missing_output == ["  <dim>(>_<) File not found: Missing</dim>"]
    assert unsupported_output == [
        "  <dim>(._.) Not a supported image file: notes.txt</dim>"
    ]


@pytest.mark.unit
def test_image_handler_attaches_path_and_preserves_trailing_prompt_case():
    output: list[str] = []
    attached: list[Path] = []
    image = Path("Mixed Image.PNG")

    handle_image_command(
        parse_cli_command('/image "Mixed Image.PNG" Describe CamelCase'),
        ports=_image_ports(output, attached, resolved=image),
    )

    assert attached == [image]
    assert output == [
        "  📎 Attached image: Mixed Image.PNG",
        (
            "  <dim>Now type your prompt (or use --image in single-query mode): "
            "Describe CamelCase</dim>"
        ),
    ]


@pytest.mark.unit
def test_image_handler_projects_termux_followup_for_attached_image():
    output: list[str] = []
    image = Path("sample.png")

    handle_image_command(
        parse_cli_command("/image sample.png"),
        ports=_image_ports(output, [], termux=True, resolved=image),
    )

    assert output == [
        "  📎 Attached image: sample.png",
        (
            "  <dim>Tip: type your next message, or run VoidCube chat -q "
            "--image ~/storage/shared/Pictures/sample.png \"What do you see?\"</dim>"
        ),
    ]


@pytest.mark.unit
def test_attachment_handler_accepts_audio_and_video_paths(tmp_path):
    output: list[str] = []
    attached: list[Path] = []
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio")

    handle_attachment_command(
        parse_cli_command(f'/attach "{audio}" inspect'),
        ports=AttachmentCommandPorts(
            split_path=_split_path_input,
            resolve_path=lambda value: audio if value == str(audio) else None,
            supported_extensions={".wav", ".mp4"},
            append_attachment=attached.append,
            emit=output.append,
        ),
    )

    assert attached == [audio]
    assert output == [f"  Attached: {audio.name}", "  Next prompt: inspect"]
