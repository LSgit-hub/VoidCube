from pathlib import Path

from VoidCube_cli.cli_tui_image_indicator_runtime import (
    CliTuiImageIndicatorPorts,
    CliTuiImageIndicatorRuntime,
)


def test_image_indicator_projects_attachment_badges():
    images = [Path("first.png")]
    runtime = CliTuiImageIndicatorRuntime(
        CliTuiImageIndicatorPorts(
            attached_images=lambda: images,
            image_counter=lambda: 3,
            format_badges=lambda paths, counter: f"{paths[0].name}:{counter}",
        )
    )

    assert runtime.visible() is True
    assert runtime.fragments() == [("class:image-badge", " first.png:3 ")]


def test_image_indicator_is_empty_without_attachments():
    runtime = CliTuiImageIndicatorRuntime(
        CliTuiImageIndicatorPorts(
            attached_images=lambda: [],
            image_counter=lambda: 0,
            format_badges=lambda _paths, _counter: "unused",
        )
    )

    assert runtime.visible() is False
    assert runtime.fragments() == []
