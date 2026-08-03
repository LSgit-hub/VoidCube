import pytest

from VoidCube_cli.cli_tui_layout_metrics_runtime import (
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

    assert runtime.input_rule_height("top", width=40) == 1
    assert runtime.input_rule_height("bottom", width=40) == 0
    assert runtime.agent_spacer_height(width=80) == 1
    assert runtime.spinner_height(width=80) == 1
    assert runtime.agent_spacer_height(width=40) == 0
    assert runtime.spinner_height(width=40) == 0


def test_layout_metrics_reject_unknown_rule_position():
    with pytest.raises(ValueError, match="Unknown input rule position"):
        _runtime().input_rule_height("middle", width=80)
