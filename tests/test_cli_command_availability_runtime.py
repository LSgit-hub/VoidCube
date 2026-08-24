from voidcube.interfaces.cli.command_availability_runtime import (
    CliCommandAvailabilityPorts,
    CliCommandAvailabilityRuntime,
)


def test_command_availability_projects_only_fast_capability():
    runtime = CliCommandAvailabilityRuntime(
        CliCommandAvailabilityPorts(
            model=lambda: "test-model",
            supports_fast_mode=lambda model: model == "test-model",
        )
    )

    assert runtime.available("/fast") is True
    assert runtime.available("/help") is True


def test_command_availability_tracks_dynamic_model_callback():
    model = ["normal-model"]
    runtime = CliCommandAvailabilityRuntime(
        CliCommandAvailabilityPorts(
            model=lambda: model[0],
            supports_fast_mode=lambda value: value == "fast-model",
        )
    )

    assert runtime.fast_available() is False
    model[0] = "fast-model"
    assert runtime.fast_available() is True
