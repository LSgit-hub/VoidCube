from __future__ import annotations

import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr
from imaplib import IMAP4, IMAP4_SSL
from smtplib import SMTP, SMTP_SSL
from typing import Any, Mapping


_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@dataclass(frozen=True, slots=True)
class MailSettings:
    enabled: bool
    display_name: str
    address: str
    username: str
    password: str
    imap_host: str
    imap_port: int
    imap_use_ssl: bool
    smtp_host: str
    smtp_port: int
    smtp_use_ssl: bool
    smtp_use_starttls: bool
    inbox_folder: str
    sent_folder: str
    fetch_limit: int
    poll_interval_seconds: int
    signature: str

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "MailSettings":
        return cls(
            enabled=bool(mapping.get("enabled", False)),
            display_name=str(mapping.get("display_name") or "星子邮件"),
            address=str(mapping.get("address") or ""),
            username=str(mapping.get("username") or ""),
            password=str(mapping.get("password") or ""),
            imap_host=str(mapping.get("imap_host") or ""),
            imap_port=max(1, int(mapping.get("imap_port", 993))),
            imap_use_ssl=bool(mapping.get("imap_use_ssl", True)),
            smtp_host=str(mapping.get("smtp_host") or ""),
            smtp_port=max(1, int(mapping.get("smtp_port", 587))),
            smtp_use_ssl=bool(mapping.get("smtp_use_ssl", False)),
            smtp_use_starttls=bool(mapping.get("smtp_use_starttls", True)),
            inbox_folder=str(mapping.get("inbox_folder") or "INBOX"),
            sent_folder=str(mapping.get("sent_folder") or "Sent"),
            fetch_limit=max(1, min(100, int(mapping.get("fetch_limit", 20)))),
            poll_interval_seconds=max(10, int(mapping.get("poll_interval_seconds", 120))),
            signature=str(mapping.get("signature") or ""),
        )

    def to_public_dict(self, *, managed: bool = False) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "display_name": self.display_name,
            "address": self.address,
            "username": self.username,
            "password_set": bool(self.password),
            "imap_host": self.imap_host,
            "imap_port": self.imap_port,
            "imap_use_ssl": self.imap_use_ssl,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "smtp_use_ssl": self.smtp_use_ssl,
            "smtp_use_starttls": self.smtp_use_starttls,
            "inbox_folder": self.inbox_folder,
            "sent_folder": self.sent_folder,
            "fetch_limit": self.fetch_limit,
            "poll_interval_seconds": self.poll_interval_seconds,
            "signature": self.signature,
            "configured": self.is_configured(),
            "managed": managed,
        }

    def is_configured(self) -> bool:
        return bool(
            self.enabled
            and self.imap_host
            and self.smtp_host
            and (self.username or self.address)
        )


def _safe_decode_header(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_text_preview(message: Any, *, limit: int = 220) -> str:
    if hasattr(message, "is_multipart") and message.is_multipart():
        for part in message.walk():
            content_type = str(part.get_content_type() or "").lower()
            disposition = str(part.get_content_disposition() or "").lower()
            if disposition == "attachment" or content_type not in {"text/plain", "text/html"}:
                continue
            payload = part.get_content()
            if isinstance(payload, str) and payload.strip():
                text = payload.strip()
                return re.sub(r"\s+", " ", text)[:limit]
        return ""
    payload = message.get_content() if hasattr(message, "get_content") else ""
    if isinstance(payload, str):
        return re.sub(r"\s+", " ", payload.strip())[:limit]
    return ""


def _extract_text_body(message: Any, *, limit: int = 100_000) -> str:
    """Return the readable text body while skipping attachments."""
    if hasattr(message, "is_multipart") and message.is_multipart():
        html_fallback = ""
        for part in message.walk():
            content_type = str(part.get_content_type() or "").lower()
            disposition = str(part.get_content_disposition() or "").lower()
            if disposition == "attachment":
                continue
            payload = part.get_content()
            if not isinstance(payload, str) or not payload.strip():
                continue
            if content_type == "text/plain":
                return payload.strip()[:limit]
            if content_type == "text/html" and not html_fallback:
                html_fallback = re.sub(r"\s+", " ", payload.strip())
        return html_fallback[:limit]
    payload = message.get_content() if hasattr(message, "get_content") else ""
    return payload.strip()[:limit] if isinstance(payload, str) else ""


def _message_record(
    message: Any,
    message_id: str,
    *,
    include_body: bool = False,
    flags: str = "",
) -> dict[str, Any]:
    record = {
        "message_id": str(message_id),
        "uid": str(message_id),
        "subject": _safe_decode_header(str(message.get("subject") or "")),
        "from": _safe_decode_header(str(message.get("from") or "")),
        "to": _safe_decode_header(str(message.get("to") or "")),
        "cc": _safe_decode_header(str(message.get("cc") or "")),
        "reply_to": _safe_decode_header(str(message.get("reply-to") or "")),
        "date": str(message.get("date") or ""),
        "message_id_header": str(message.get("message-id") or ""),
        "references": str(message.get("references") or ""),
        "preview": _extract_text_preview(message),
        "content_type": str(message.get_content_type() or ""),
        "flags": str(flags or ""),
    }
    if include_body:
        record["body"] = _extract_text_body(message)
    return record


def _raw_message_from_fetch(parts: Any) -> bytes | None:
    if not parts:
        return None
    for item in parts:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def _normalize_email_addresses(addresses: str) -> list[str]:
    values = [item.strip() for item in str(addresses or "").split(",") if item.strip()]
    normalized: list[str] = []
    for value in values:
        parsed = parseaddr(value)[1] or value
        if not _EMAIL_RE.fullmatch(parsed):
            raise ValueError(f"invalid email address: {value}")
        normalized.append(parsed)
    return normalized


def build_mail_overview(settings: MailSettings, *, managed: bool = False) -> dict[str, Any]:
    return settings.to_public_dict(managed=managed)


def _connect_imap(settings: MailSettings, *, timeout_seconds: float = 12.0):
    if settings.imap_use_ssl:
        return IMAP4_SSL(settings.imap_host, settings.imap_port, timeout=timeout_seconds)
    return IMAP4(settings.imap_host, settings.imap_port, timeout=timeout_seconds)


def _connect_smtp(settings: MailSettings, *, timeout_seconds: float = 12.0):
    if settings.smtp_use_ssl:
        return SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=timeout_seconds)
    return SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout_seconds)


def fetch_mail_messages(
    settings: MailSettings,
    *,
    folder: str | None = None,
    limit: int | None = None,
    unread_only: bool = False,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    if not settings.is_configured():
        raise RuntimeError("mail is not configured")
    max_items = max(1, min(100, int(limit or settings.fetch_limit)))
    folder_name = str(folder or settings.inbox_folder or "INBOX")
    messages: list[dict[str, Any]] = []
    with _connect_imap(settings, timeout_seconds=timeout_seconds) as client:
        client.login(settings.username or settings.address, settings.password)
        client.select(folder_name)
        status, data = client.search(None, "UNSEEN" if unread_only else "ALL")
        if status != "OK":
            raise RuntimeError(f"imap search failed: {status}")
        ids = [item for item in (data[0].split() if data and data[0] else []) if item]
        for message_id in reversed(ids[-max_items:]):
            fetch_status, parts = client.fetch(message_id, "(RFC822)")
            if fetch_status != "OK" or not parts:
                continue
            raw = _raw_message_from_fetch(parts)
            if not raw:
                continue
            parsed = BytesParser(policy=policy.default).parsebytes(raw)
            normalized_id = message_id.decode("ascii", "ignore") if isinstance(message_id, bytes) else str(message_id)
            messages.append(_message_record(parsed, normalized_id))
    return {
        "folder": folder_name,
        "unread_only": bool(unread_only),
        "messages": messages,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def search_mail_messages(
    settings: MailSettings,
    *,
    query: str,
    folder: str | None = None,
    limit: int | None = None,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    """Search IMAP message text and return matching message overviews."""
    if not settings.is_configured():
        raise RuntimeError("mail is not configured")
    search_text = str(query or "").strip()
    if not search_text:
        raise ValueError("query is required")
    max_items = max(1, min(100, int(limit or settings.fetch_limit)))
    folder_name = str(folder or settings.inbox_folder or "INBOX")
    messages: list[dict[str, Any]] = []
    with _connect_imap(settings, timeout_seconds=timeout_seconds) as client:
        client.login(settings.username or settings.address, settings.password)
        client.select(folder_name)
        status, data = client.search(None, "TEXT", search_text)
        if status != "OK":
            raise RuntimeError(f"imap search failed: {status}")
        ids = [item for item in (data[0].split() if data and data[0] else []) if item]
        for message_id in reversed(ids[-max_items:]):
            fetch_status, parts = client.fetch(message_id, "(RFC822)")
            if fetch_status != "OK":
                continue
            raw = _raw_message_from_fetch(parts)
            if not raw:
                continue
            parsed = BytesParser(policy=policy.default).parsebytes(raw)
            normalized_id = message_id.decode("ascii", "ignore") if isinstance(message_id, bytes) else str(message_id)
            messages.append(_message_record(parsed, normalized_id))
    return {
        "folder": folder_name,
        "query": search_text,
        "messages": messages,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_mail_message(
    settings: MailSettings,
    *,
    message_id: str,
    folder: str | None = None,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    """Fetch one message, including its readable body and reply headers."""
    if not settings.is_configured():
        raise RuntimeError("mail is not configured")
    normalized_id = str(message_id or "").strip()
    if not normalized_id:
        raise ValueError("message_id is required")
    folder_name = str(folder or settings.inbox_folder or "INBOX")
    with _connect_imap(settings, timeout_seconds=timeout_seconds) as client:
        client.login(settings.username or settings.address, settings.password)
        client.select(folder_name)
        fetch_status, parts = client.fetch(normalized_id, "(RFC822 FLAGS)")
        if fetch_status != "OK":
            raise RuntimeError(f"imap fetch failed: {fetch_status}")
        raw = _raw_message_from_fetch(parts)
        if not raw:
            raise RuntimeError("mail message body was empty")
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        flags = ""
        for item in parts or []:
            if isinstance(item, tuple) and item and isinstance(item[0], bytes):
                flags = item[0].decode("ascii", "ignore")
                break
    return {
        "folder": folder_name,
        "message": _message_record(parsed, normalized_id, include_body=True, flags=flags),
    }


def mark_mail_message_read(
    settings: MailSettings,
    *,
    message_id: str,
    folder: str | None = None,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    if not settings.is_configured():
        raise RuntimeError("mail is not configured")
    normalized_id = str(message_id or "").strip()
    if not normalized_id:
        raise ValueError("message_id is required")
    folder_name = str(folder or settings.inbox_folder or "INBOX")
    with _connect_imap(settings, timeout_seconds=timeout_seconds) as client:
        client.login(settings.username or settings.address, settings.password)
        client.select(folder_name)
        status, _ = client.store(normalized_id, "+FLAGS", "\\Seen")
        if status != "OK":
            raise RuntimeError(f"imap mark read failed: {status}")
    return {"status": "marked_read", "folder": folder_name, "message_id": normalized_id}


def send_mail_message(
    settings: MailSettings,
    *,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    if not settings.is_configured():
        raise RuntimeError("mail is not configured")
    recipients = _normalize_email_addresses(to)
    if not recipients:
        raise ValueError("to is required")
    message = EmailMessage()
    message["Subject"] = str(subject or "").strip() or "(no subject)"
    message["From"] = settings.address or settings.username or settings.display_name
    message["To"] = ", ".join(recipients)
    cc_recipients = _normalize_email_addresses(cc) if cc and str(cc).strip() else []
    bcc_recipients = _normalize_email_addresses(bcc) if bcc and str(bcc).strip() else []
    if cc_recipients:
        message["Cc"] = ", ".join(cc_recipients)
    if bcc_recipients:
        message["Bcc"] = ", ".join(bcc_recipients)
    if in_reply_to and str(in_reply_to).strip():
        message["In-Reply-To"] = str(in_reply_to).strip()
    if references and str(references).strip():
        message["References"] = str(references).strip()
    message.set_content(
        (body or "").rstrip() + ("\n\n" + settings.signature.strip() if settings.signature.strip() else "")
    )

    with _connect_smtp(settings, timeout_seconds=timeout_seconds) as client:
        client.ehlo()
        if settings.smtp_use_starttls and not settings.smtp_use_ssl:
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if settings.username or settings.password:
            client.login(settings.username or settings.address, settings.password)
        client.send_message(message)
    return {
        "status": "sent",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "to": recipients,
        "subject": str(message["Subject"] or ""),
    }


def reply_mail_message(
    settings: MailSettings,
    *,
    message_id: str,
    body: str,
    folder: str | None = None,
    cc: str | None = None,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    original = fetch_mail_message(
        settings, message_id=message_id, folder=folder, timeout_seconds=timeout_seconds
    )["message"]
    reply_to = str(original.get("reply_to") or original.get("from") or "").strip()
    addresses = [address for _, address in getaddresses([reply_to]) if address]
    recipient = addresses[0] if addresses else parseaddr(reply_to)[1]
    if not recipient:
        raise ValueError("original message has no reply address")
    subject = str(original.get("subject") or "").strip()
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject
    message_id_header = str(original.get("message_id_header") or "").strip()
    references = " ".join(
        item for item in [str(original.get("references") or "").strip(), message_id_header]
        if item
    )
    result = send_mail_message(
        settings,
        to=recipient,
        subject=subject,
        body=body,
        cc=cc,
        in_reply_to=message_id_header,
        references=references,
        timeout_seconds=timeout_seconds,
    )
    result.update({"status": "replied", "in_reply_to": message_id_header, "folder": folder or settings.inbox_folder})
    return result


def test_mail_connectivity(
    settings: MailSettings,
    *,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    if not settings.is_configured():
        raise RuntimeError("mail is not configured")
    report: dict[str, Any] = {"configured": True, "imap_ok": False, "smtp_ok": False}
    with _connect_imap(settings, timeout_seconds=timeout_seconds) as client:
        client.login(settings.username or settings.address, settings.password)
        client.select(settings.inbox_folder or "INBOX")
        report["imap_ok"] = True
        try:
            status, data = client.status(settings.inbox_folder or "INBOX", "(MESSAGES UNSEEN)")
            if status == "OK" and data:
                report["status"] = data[0].decode("utf-8", "ignore") if isinstance(data[0], bytes) else str(data[0])
        except Exception:
            report["status"] = "imap_status_unavailable"
    with _connect_smtp(settings, timeout_seconds=timeout_seconds) as client:
        client.ehlo()
        if settings.smtp_use_starttls and not settings.smtp_use_ssl:
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if settings.username or settings.password:
            client.login(settings.username or settings.address, settings.password)
        report["smtp_ok"] = True
    return report
