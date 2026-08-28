from types import SimpleNamespace

from voidcube.interfaces.cli.turn.input_preparation import (
    CliTurnInputPreparationPorts,
    CliTurnInputPreparationRuntime,
)


def _ports(message, output, **overrides):
    values = {
        "message": message,
        "images": None,
        "conversation_history": [{"role": "assistant", "content": "before"}],
        "preprocess_images": lambda text, images: f"{text}|images:{len(images)}",
        "model": "model",
        "base_url": "https://example.test/v1",
        "api_key": "key",
        "cwd": lambda: "workspace",
        "should_emit": lambda: True,
        "emit": output.append,
        "sanitize": lambda value: value.replace("<bad>", "clean"),
    }
    values.update(overrides)
    return CliTurnInputPreparationPorts(**values)


def test_turn_input_preparation_projects_images_sanitization_and_begin_turn():
    output = []
    result = CliTurnInputPreparationRuntime(
        _ports(
            "hello<bad>",
            output,
            images=["one", "two"],
        )
    ).prepare()

    assert result.blocked_response is None
    assert result.message == "helloclean|images:2"
    assert result.turn_input.user_message == "helloclean|images:2"
    assert result.turn_input.prior_history == (
        {"role": "assistant", "content": "before"},
    )
    assert result.turn_input.conversation_history[-1] == {
        "role": "user",
        "content": "helloclean|images:2",
    }


def test_turn_input_preparation_uses_native_attachments_without_preanalysis():
    output = []
    attachment = {
        "kind": "local_image",
        "path": "sample.png",
        "mime_type": "image/png",
    }
    result = CliTurnInputPreparationRuntime(
        _ports(
            "inspect",
            output,
            images=["sample.png"],
            native_input_modalities=lambda *_args: ("image",),
            build_attachments=lambda _images: [attachment],
            preprocess_images=lambda *_args: (_ for _ in ()).throw(
                AssertionError("native image input must not be preprocessed")
            ),
        )
    ).prepare()

    assert result.message == "inspect"
    assert result.attachments == (attachment,)
    assert result.turn_input.conversation_history[-1] == {
        "role": "user",
        "content": "inspect",
        "attachments": [attachment],
    }


def test_turn_input_preparation_uses_confirmed_named_provider_capability():
    output = []
    attachment = {
        "kind": "local_image",
        "path": "sample.png",
        "mime_type": "image/png",
    }
    result = CliTurnInputPreparationRuntime(
        _ports(
            "inspect",
            output,
            images=["sample.png"],
            model="deepseek-v4-flash-vision-exp",
            native_input_modalities=(
                lambda model, _base_url: (
                    ("image",)
                    if model == "deepseek-v4-flash-vision-exp"
                    else ()
                )
            ),
            build_attachments=lambda _images: [attachment],
            preprocess_images=lambda *_args: (_ for _ in ()).throw(
                AssertionError("confirmed native image input must not use vision_analyze")
            ),
        )
    ).prepare()

    assert result.attachments == (attachment,)


def test_turn_input_preparation_expands_context_and_projects_warnings():
    output = []
    context = SimpleNamespace(
        expanded=True,
        blocked=False,
        references=["ref"],
        injected_tokens=12,
        warnings=["warning"],
        message="expanded message",
    )
    result = CliTurnInputPreparationRuntime(
        _ports(
            "use @file:main.py",
            output,
            context_length=lambda *_: 100,
            expand_context=lambda message, **kwargs: context,
        )
    ).prepare()

    assert result.message == "expanded message"
    assert result.turn_input.user_message == "expanded message"
    assert "12 tokens" in output[0]
    assert "warning" in output[1]


def test_turn_input_preparation_returns_blocked_response_without_turn_writeback():
    output = []
    context = SimpleNamespace(
        expanded=False,
        blocked=True,
        references=[],
        injected_tokens=100,
        warnings=["blocked warning"],
        message="original",
    )
    result = CliTurnInputPreparationRuntime(
        _ports(
            "use @file:secret",
            output,
            expand_context=lambda *_args, **_kwargs: context,
        )
    ).prepare()

    assert result.turn_input is None
    assert result.blocked_response == "blocked warning"
    assert output == ["  \033[2m⚠ blocked warning\033[0m"]
