from voidcube.systems.voice.tts import sanitize_speech_text


def test_speech_sanitizer_removes_markdown_without_losing_words() -> None:
    speech = sanitize_speech_text("**重点**：~~旧内容~~，见 [文档](https://example.com)。\n- 下一步。")
    assert "重点" in speech and "旧内容" in speech and "文档" in speech
    assert "https://" not in speech and "**" not in speech and "~~" not in speech
    assert "下一步" in speech


def test_speech_sanitizer_handles_code_urls_and_symbols() -> None:
    speech = sanitize_speech_text("`a+b` 计算 3 + 2 = 5，联系 a@b.com，进度 80%。\n```python\nprint('x')\n```")
    assert "代码内容已省略" in speech
    assert "加" in speech and "等于" in speech and "艾特" in speech and "百分之" in speech
    assert "`" not in speech


def test_speech_sanitizer_keeps_plain_chinese_punctuation() -> None:
    assert sanitize_speech_text("你好，星子！") == "你好，星子！"


def test_speech_sanitizer_distinguishes_minus_and_division() -> None:
    speech = sanitize_speech_text("3 - 2 = 1，6 / 2 = 3")
    assert "减" in speech
    assert "除以" in speech
