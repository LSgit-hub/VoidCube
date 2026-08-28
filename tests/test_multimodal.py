from __future__ import annotations

import pytest

from voidcube.infrastructure.llm.multimodal import (
    MAX_IMAGES_PER_REQUEST,
    attachments_from_paths,
    build_user_content_with_images,
    configured_model_capabilities,
    image_attachments_from_paths,
    native_input_modalities,
    supports_native_image_input,
)


pytestmark = pytest.mark.unit


def test_deepseek_vision_exposes_native_image_input_capability():
    assert supports_native_image_input(
        "deepseek",
        "deepseek-v4-flash-vision-exp",
        configured_capabilities={"image_input": True},
    )
    assert not supports_native_image_input(
        "deepseek-v",
        "deepseek-v4-flash-vision-exp",
    )
    assert not supports_native_image_input("deepseek", "deepseek-v4-flash")
    assert supports_native_image_input(
        "custom",
        "vision-model",
        configured_capabilities={"image_input": True},
    )


def test_local_image_metadata_is_rebuilt_into_api_only_data_url(tmp_path):
    image = tmp_path / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nvisual-payload")

    attachments = image_attachments_from_paths([image])
    content = build_user_content_with_images("inspect", attachments)

    assert attachments[0]["path"] == str(image.resolve())
    assert "base64" not in str(attachments)
    assert content[0] == {"type": "text", "text": "inspect"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_native_image_metadata_rejects_unsupported_image_content(tmp_path):
    image = tmp_path / "misleading.png"
    image.write_bytes(b"not-an-image")

    with pytest.raises(ValueError, match="JPEG, PNG, GIF, or WebP"):
        image_attachments_from_paths([image])


def test_native_image_request_rejects_more_than_supported_image_count(tmp_path):
    image = tmp_path / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nvisual-payload")
    attachment = image_attachments_from_paths([image])[0]

    with pytest.raises(ValueError, match=f"at most {MAX_IMAGES_PER_REQUEST}"):
        build_user_content_with_images(
            "inspect",
            [attachment] * (MAX_IMAGES_PER_REQUEST + 1),
        )


def test_model_capabilities_are_resolved_per_input_modality():
    assert native_input_modalities(
        "custom",
        "native-media",
        configured_capabilities={
            "image_input": True,
            "audio_input": True,
            "video_input": False,
        },
    ) == frozenset({"image", "audio"})


def test_named_provider_capabilities_are_loaded_for_the_effective_runtime_key(
    monkeypatch,
):
    config = {
        "providers": {
            "deepseek-v": {
                "model_capabilities": {
                    "deepseek-v4-flash-vision-exp": {"image_input": True}
                }
            }
        }
    }
    monkeypatch.setattr(
        "voidcube.infrastructure.config.configuration.load_config",
        lambda: config,
    )

    capabilities = configured_model_capabilities(
        "deepseek-v",
        "deepseek-v4-flash-vision-exp",
    )

    assert capabilities == {"image_input": True}
    assert native_input_modalities(
        "deepseek-v",
        "deepseek-v4-flash-vision-exp",
        configured_capabilities=capabilities,
    ) == frozenset({"image"})


def test_native_media_content_uses_one_attachment_builder(tmp_path):
    image = tmp_path / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nvisual")
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 12)
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"video")

    attachments = attachments_from_paths([image, audio, video])
    from voidcube.infrastructure.llm.multimodal import (
        build_user_content_with_attachments,
    )

    content = build_user_content_with_attachments(
        "inspect all",
        attachments,
        native_modalities={"image", "audio", "video"},
    )

    assert [item["type"] for item in content] == [
        "text",
        "image_url",
        "input_audio",
        "video_url",
    ]
    assert content[2]["input_audio"]["format"] == "wav"
