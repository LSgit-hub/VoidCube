from __future__ import annotations

import json
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import Mock, patch

from voidcube.extensions.tools import model_tools
from voidcube.extensions.tools.mail_tools import _mail_available
from voidcube.extensions.tools.model_tools import handle_function_call
from voidcube.extensions.tools.registry import registry
from voidcube.systems.supervisor.mail_runtime import (
    MailSettings,
    fetch_mail_message,
    reply_mail_message,
)


def _settings() -> MailSettings:
    return MailSettings.from_mapping(
        {
            "enabled": True,
            "address": "assistant@example.com",
            "username": "assistant@example.com",
            "password": "secret",
            "imap_host": "imap.example.com",
            "smtp_host": "smtp.example.com",
        }
    )


def _message_bytes() -> bytes:
    message = EmailMessage()
    message["Message-ID"] = "<original@example.com>"
    message["From"] = "User <user@example.com>"
    message["To"] = "assistant@example.com"
    message["Reply-To"] = "reply@example.com"
    message["Subject"] = "项目进展"
    message.set_content("请回复项目进展。")
    return message.as_bytes()


class _FakeImap:
    def __init__(self, *_args, **_kwargs):
        self.stored = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def login(self, *_args):
        return "OK", []

    def select(self, *_args):
        return "OK", []

    def fetch(self, *_args):
        return "OK", [(b"1 (RFC822 {0})", _message_bytes())]

    def store(self, *args):
        self.stored.append(args)
        return "OK", []


class _FakeSmtp:
    messages = []

    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def ehlo(self):
        return None

    def starttls(self, **_kwargs):
        return None

    def login(self, *_args):
        return None

    def send_message(self, message):
        self.messages.append(message)


def test_mail_toolset_is_discoverable_but_unconfigured_mail_is_hidden(monkeypatch):
    monkeypatch.setattr(
        "voidcube.extensions.tools.mail_tools._mail_settings",
        lambda: MailSettings.from_mapping({}),
    )
    definitions = model_tools.get_tool_definitions(enabled_toolsets=["mail"], quiet_mode=True)
    assert {item["function"]["name"] for item in definitions} == set()

    monkeypatch.setattr(
        "voidcube.extensions.tools.mail_tools._mail_settings",
        _settings,
    )
    definitions = model_tools.get_tool_definitions(enabled_toolsets=["mail"], quiet_mode=True)
    assert {
        "mail_list_messages",
        "mail_search",
        "mail_read_message",
        "mail_send",
        "mail_reply",
        "mail_mark_read",
    } == {item["function"]["name"] for item in definitions}
    assert _mail_available() is True


def test_fetch_mail_message_returns_readable_body_and_reply_headers():
    with patch("voidcube.systems.supervisor.mail_runtime._connect_imap", _FakeImap):
        result = fetch_mail_message(_settings(), message_id="1")

    assert result["message"]["body"] == "请回复项目进展。"
    assert result["message"]["message_id_header"] == "<original@example.com>"
    assert result["message"]["reply_to"] == "reply@example.com"


def test_mail_reply_preserves_thread_headers_and_recipient():
    _FakeSmtp.messages = []
    with patch("voidcube.systems.supervisor.mail_runtime._connect_imap", _FakeImap), patch(
        "voidcube.systems.supervisor.mail_runtime._connect_smtp", _FakeSmtp
    ):
        result = reply_mail_message(_settings(), message_id="1", body="收到，我会跟进。")

    sent = _FakeSmtp.messages[0]
    assert result["status"] == "replied"
    assert sent["To"] == "reply@example.com"
    assert sent["Subject"] == "Re: 项目进展"
    assert sent["In-Reply-To"] == "<original@example.com>"
    assert sent["References"] == "<original@example.com>"


def test_mail_tool_runs_through_unified_function_call_dispatch(monkeypatch):
    monkeypatch.setattr(
        "voidcube.extensions.tools.mail_tools._mail_settings",
        _settings,
    )
    fake = Mock()
    fake.__enter__ = Mock(return_value=fake)
    fake.__exit__ = Mock(return_value=False)
    fake.login.return_value = ("OK", [])
    fake.select.return_value = ("OK", [])
    fake.search.return_value = ("OK", [b"1"])
    fake.fetch.return_value = ("OK", [(b"1 (RFC822)", _message_bytes())])
    with patch("voidcube.systems.supervisor.mail_runtime._connect_imap", return_value=fake):
        result = json.loads(
            handle_function_call(
                "mail_list_messages",
                {"limit": 1},
                task_id="mail-tool-test",
            )
        )

    assert result["messages"][0]["subject"] == "项目进展"
    fake.login.assert_called_once()


def test_mail_send_validates_addresses_and_supports_multiple_recipients(monkeypatch):
    monkeypatch.setattr(
        "voidcube.extensions.tools.mail_tools._mail_settings",
        _settings,
    )
    _FakeSmtp.messages = []
    with patch("voidcube.systems.supervisor.mail_runtime._connect_smtp", _FakeSmtp):
        result = json.loads(
            registry.dispatch(
                "mail_send",
                {
                    "to": "one@example.com, two@example.com",
                    "subject": "通知",
                    "body": "内容",
                },
            )
        )

    assert result["to"] == ["one@example.com", "two@example.com"]
    assert _FakeSmtp.messages[0]["To"] == "one@example.com, two@example.com"


def test_mail_tools_have_expected_effect_classification():
    assert registry.get_effect("mail_list_messages") == "read_only"
    assert registry.get_effect("mail_read_message") == "read_only"
    assert registry.get_effect("mail_mark_read") == "idempotent_write"
    assert registry.get_effect("mail_send") == "non_idempotent_write"
    assert registry.get_effect("mail_reply") == "non_idempotent_write"
