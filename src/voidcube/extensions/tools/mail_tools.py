"""Agent tools for managing the configured mailbox.

The Supervisor UI and these tools share the same persisted
``supervisor.service_runtime.mail`` configuration.  Secrets are read locally
and are never included in tool schemas or results.
"""

from __future__ import annotations

import json
from typing import Any

from .registry import registry
from ...systems.supervisor.mail_runtime import (
    MailSettings,
    fetch_mail_message,
    fetch_mail_messages,
    mark_mail_message_read,
    reply_mail_message,
    search_mail_messages,
    send_mail_message,
)


def _mail_settings() -> MailSettings:
    from ...infrastructure.config.configuration import load_config

    config = load_config()
    supervisor = config.get("supervisor") if isinstance(config, dict) else {}
    service_runtime = supervisor.get("service_runtime") if isinstance(supervisor, dict) else {}
    mail = service_runtime.get("mail") if isinstance(service_runtime, dict) else {}
    return MailSettings.from_mapping(mail if isinstance(mail, dict) else {})


def _mail_available() -> bool:
    try:
        return _mail_settings().is_configured()
    except Exception:
        return False


def _json_result(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _handle_mail_list(args: dict[str, Any], **_: Any) -> str:
    settings = _mail_settings()
    return _json_result(fetch_mail_messages(
        settings,
        folder=args.get("folder") or None,
        limit=args.get("limit"),
        unread_only=bool(args.get("unread_only", False)),
    ))


def _handle_mail_search(args: dict[str, Any], **_: Any) -> str:
    return _json_result(search_mail_messages(
        _mail_settings(),
        query=args.get("query", ""),
        folder=args.get("folder") or None,
        limit=args.get("limit"),
    ))


def _handle_mail_read(args: dict[str, Any], **_: Any) -> str:
    return _json_result(fetch_mail_message(
        _mail_settings(),
        message_id=args.get("message_id", ""),
        folder=args.get("folder") or None,
    ))


def _handle_mail_send(args: dict[str, Any], **_: Any) -> str:
    return _json_result(send_mail_message(
        _mail_settings(),
        to=args.get("to", ""),
        subject=args.get("subject", ""),
        body=args.get("body", ""),
        cc=args.get("cc") or None,
        bcc=args.get("bcc") or None,
    ))


def _handle_mail_reply(args: dict[str, Any], **_: Any) -> str:
    return _json_result(reply_mail_message(
        _mail_settings(),
        message_id=args.get("message_id", ""),
        body=args.get("body", ""),
        folder=args.get("folder") or None,
        cc=args.get("cc") or None,
    ))


def _handle_mail_mark_read(args: dict[str, Any], **_: Any) -> str:
    return _json_result(mark_mail_message_read(
        _mail_settings(),
        message_id=args.get("message_id", ""),
        folder=args.get("folder") or None,
    ))


MAIL_LIST_SCHEMA = {
    "description": "读取邮箱中的最新邮件摘要。可只看未读邮件。",
    "parameters": {
        "type": "object",
        "properties": {
            "folder": {"type": "string", "description": "IMAP 文件夹，默认收件箱"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "unread_only": {"type": "boolean", "description": "只返回未读邮件", "default": False},
        },
    },
}

MAIL_SEARCH_SCHEMA = {
    "description": "按关键词搜索邮箱邮件主题、正文和头部文本，返回匹配摘要。",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词", "minLength": 1},
            "folder": {"type": "string", "description": "IMAP 文件夹，默认收件箱"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        },
        "required": ["query"],
    },
}

MAIL_READ_SCHEMA = {
    "description": "读取一封邮件的完整可读正文和回复所需的邮件头信息。",
    "parameters": {
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "mail_list_messages 或 mail_search 返回的 message_id"},
            "folder": {"type": "string", "description": "邮件所在 IMAP 文件夹，默认收件箱"},
        },
        "required": ["message_id"],
    },
}

MAIL_SEND_SCHEMA = {
    "description": "发送一封邮件。发送前确认收件人、主题和正文，避免误发。",
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "收件人邮箱地址，多个地址用逗号分隔"},
            "subject": {"type": "string", "description": "邮件主题"},
            "body": {"type": "string", "description": "邮件正文"},
            "cc": {"type": "string", "description": "抄送地址，可选"},
            "bcc": {"type": "string", "description": "密送地址，可选"},
        },
        "required": ["to", "subject", "body"],
    },
}

MAIL_REPLY_SCHEMA = {
    "description": "回复指定邮件，自动使用原邮件的回复地址、主题和引用头。",
    "parameters": {
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "要回复的邮件 message_id"},
            "body": {"type": "string", "description": "回复正文"},
            "folder": {"type": "string", "description": "原邮件所在 IMAP 文件夹，默认收件箱"},
            "cc": {"type": "string", "description": "抄送地址，可选"},
        },
        "required": ["message_id", "body"],
    },
}

MAIL_MARK_READ_SCHEMA = {
    "description": "将指定邮件标记为已读。",
    "parameters": {
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "要标记的邮件 message_id"},
            "folder": {"type": "string", "description": "邮件所在 IMAP 文件夹，默认收件箱"},
        },
        "required": ["message_id"],
    },
}


registry.register(
    name="mail_list_messages",
    toolset="mail",
    schema=MAIL_LIST_SCHEMA,
    handler=_handle_mail_list,
    check_fn=_mail_available,
    effect="read_only",
)
registry.register(
    name="mail_search",
    toolset="mail",
    schema=MAIL_SEARCH_SCHEMA,
    handler=_handle_mail_search,
    check_fn=_mail_available,
    effect="read_only",
)
registry.register(
    name="mail_read_message",
    toolset="mail",
    schema=MAIL_READ_SCHEMA,
    handler=_handle_mail_read,
    check_fn=_mail_available,
    effect="read_only",
)
registry.register(
    name="mail_send",
    toolset="mail",
    schema=MAIL_SEND_SCHEMA,
    handler=_handle_mail_send,
    check_fn=_mail_available,
    effect="non_idempotent_write",
)
registry.register(
    name="mail_reply",
    toolset="mail",
    schema=MAIL_REPLY_SCHEMA,
    handler=_handle_mail_reply,
    check_fn=_mail_available,
    effect="non_idempotent_write",
)
registry.register(
    name="mail_mark_read",
    toolset="mail",
    schema=MAIL_MARK_READ_SCHEMA,
    handler=_handle_mail_mark_read,
    check_fn=_mail_available,
    effect="idempotent_write",
)


__all__ = [
    "MAIL_LIST_SCHEMA",
    "MAIL_SEARCH_SCHEMA",
    "MAIL_READ_SCHEMA",
    "MAIL_SEND_SCHEMA",
    "MAIL_REPLY_SCHEMA",
    "MAIL_MARK_READ_SCHEMA",
]
