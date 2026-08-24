import pytest

from voidcube.interfaces.cli.tui.layout_metrics_runtime import (
    CliTuiLayoutMetricsPorts,
    CliTuiLayoutMetricsRuntime,
)


def _runtime(*, agent_running=False, spinner_visible=False):
    return CliTuiLayoutMetricsRuntime(
        CliTuiLayoutMetricsPorts(
            agent_running=lambda: agent_running,
            spinner_visible=lambda: spinner_visible,
        )
    )


def test_layout_metrics_preserve_compact_and_normal_heights():
    runtime = _runtime(agent_running=True, spinner_visible=True)

    assert runtime.input_rule_height("top", width=40, height=24) == 1
    assert runtime.input_rule_height("bottom", width=40, height=24) == 0
    assert runtime.agent_spacer_height(width=80, height=24) == 1
    assert runtime.spinner_height(width=80, height=24) == 1
    assert runtime.agent_spacer_height(width=40, height=24) == 0
    assert runtime.spinner_height(width=40, height=24) == 0


def test_layout_metrics_reduce_chrome_when_terminal_is_short():
    runtime = _runtime(agent_running=True, spinner_visible=True)

    assert runtime.minimal_chrome(width=100, height=12) is True
    assert runtime.input_rule_height("bottom", width=100, height=12) == 0
    assert runtime.agent_spacer_height(width=100, height=12) == 0
    assert runtime.spinner_height(width=100, height=12) == 0
    assert runtime.input_rule_height("top", width=100, height=15) == 0
    assert runtime.status_bar_visible(height=15) is False
    assert runtime.status_bar_visible(height=16) is True
    assert runtime.extended_panels_visible(width=100, height=19) is False
    assert runtime.extended_panels_visible(width=100, height=20) is True


def test_layout_metrics_reject_unknown_rule_position():
    with pytest.raises(ValueError, match="Unknown input rule position"):
        _runtime().input_rule_height("middle", width=80)
