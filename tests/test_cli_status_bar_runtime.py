from dataclasses import replace

from VoidCube_cli.cli_status_bar_runtime import (
    CliStatusBarPorts,
    CliStatusBarRuntime,
)


def _runtime(
    *,
    visible=True,
    picker_open=False,
    snapshot=None,
    width=80,
    active=False,
    middle=(),
    git=(),
    fallback="fallback",
):
    return CliStatusBarRuntime(
        CliStatusBarPorts(
            status_bar_visible=lambda: visible,
            model_picker_open=lambda: picker_open,
            snapshot=lambda: snapshot or {"model_short": "model", "context_percent": 60},
            terminal_width=lambda: width,
            agent_active=lambda: active,
            middle_fragments=lambda _active: middle,
            git_fragments=lambda: git,
            fallback_text=lambda: fallback,
        )
    )


def test_status_bar_is_hidden_during_picker_or_when_disabled():
    assert _runtime(visible=False).build() == []
    assert _runtime(picker_open=True).build() == []


def test_status_bar_builds_model_context_and_middle_fragments():
    fragments = _runtime(
        snapshot={"model_short": "api-a", "context_percent": 85},
        middle=(("middle", "MEM"),),
    ).build()

    rendered = "".join(text for _, text in fragments)
    assert "api-a" in rendered
    assert "85%" in rendered
    assert "MEM" in rendered
    assert "A✓" not in rendered
    assert any("#FF6B6B" in style for style, _ in fragments)


def test_status_bar_places_git_at_the_right_and_fits_width():
    fragments = _runtime(
        snapshot={"model_short": "model", "context_percent": None},
        width=80,
        middle=(("middle", "MEM"),),
        git=(("git", "Git <main>"),),
    ).build()

    rendered = "".join(text for _, text in fragments)
    assert "Git <main>" in rendered
    assert len(rendered) <= 80

    narrow = _runtime(
        snapshot={"model_short": "a-very-long-model-name", "context_percent": None},
        width=24,
        middle=(("middle", "MEM"),),
        git=(("git", "Git <main>"),),
    ).build()
    assert len("".join(text for _, text in narrow)) <= 24


def test_status_bar_uses_fallback_when_a_port_fails():
    runtime = _runtime()
    runtime.ports = replace(
        runtime.ports,
        snapshot=lambda: (_ for _ in ()).throw(RuntimeError("broken")),
    )

    assert runtime.build() == [("class:status-bar", " fallback ")]


def test_status_bar_trims_wide_unicode_by_terminal_cells():
    fragments = _runtime(
        snapshot={"model_short": "模型模型模型", "context_percent": None},
        width=10,
    ).build()

    from VoidCube_cli.terminal_text_layout import display_width

    assert display_width("".join(text for _, text in fragments)) <= 10


def test_status_bar_projects_explicit_exit_state():
    runtime = _runtime(width=20)
    runtime.ports = replace(runtime.ports, closing=lambda: True)

    rendered = "".join(text for _, text in runtime.build())

    assert "退出中" in rendered
