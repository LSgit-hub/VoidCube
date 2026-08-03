from VoidCube_cli.cli_runtime_credentials import (
    CliRuntimeCredentialsPorts,
    CliRuntimeCredentialsRuntime,
)


def _ports(runtime, current=None):
    return CliRuntimeCredentialsPorts(
        requested_provider="custom",
        explicit_api_key=None,
        explicit_base_url=None,
        current=current or {
            "model": "provider-model",
            "api_key": "old-key",
            "base_url": "https://old.example/v1",
            "provider": "custom",
            "command": None,
            "args": [],
        },
        resolve_provider=lambda **_: runtime,
    )


def test_runtime_credentials_projects_custom_endpoint_without_key():
    result = CliRuntimeCredentialsRuntime(
        _ports({
            "provider": "custom",
            "base_url": "https://local.example/v1",
            "api_key": "",
            "model": "local-model",
            "source": "config",
            "args": ["--stdio"],
        })
    ).resolve()

    assert result.ready is True
    assert result.api_key == "no-key-required"
    assert result.model == "local-model"
    assert result.args == ("--stdio",)
    assert result.credentials_changed is True
    assert result.routing_changed is True


def test_runtime_credentials_reports_missing_openrouter_key_without_mutating_state():
    result = CliRuntimeCredentialsRuntime(
        _ports({
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "",
        })
    ).resolve()

    assert result.ready is False
    assert result.error.startswith("Provider resolver returned an empty API key")


def test_runtime_credentials_marks_runtime_model_and_normalization_changes():
    result = CliRuntimeCredentialsRuntime(
        _ports({
            "provider": "custom",
            "base_url": "https://custom.example/v1",
            "api_key": "key",
            "model": "  vendor/model  ",
        }, current={
            "model": "old-model",
            "api_key": "key",
            "base_url": "https://custom.example/v1",
            "provider": "custom",
            "command": None,
            "args": [],
        })
    ).resolve()

    assert result.ready is True
    assert result.model == "vendor/model"
    assert result.model_changed is True


def test_runtime_credentials_formats_resolver_failure():
    result = CliRuntimeCredentialsRuntime(
        CliRuntimeCredentialsPorts(
            requested_provider="custom",
            explicit_api_key=None,
            explicit_base_url=None,
            current={},
            resolve_provider=lambda **_: (_ for _ in ()).throw(ValueError("bad auth")),
            format_error=lambda error: f"formatted: {error}",
        )
    ).resolve()

    assert result.ready is False
    assert result.error == "formatted: bad auth"
