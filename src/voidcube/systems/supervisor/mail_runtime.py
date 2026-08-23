from __future__ import annotations

import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
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


def _normalize_email_address(address: str) -> str:
    text = str(address or "").strip()
    if text and _EMAIL_RE.fullmatch(text):
        return text
    return text


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
        status, data = client.search(None, "ALL")
        if status != "OK":
            raise RuntimeError(f"imap search failed: {status}")
        ids = [item for item in (data[0].split() if data and data[0] else []) if item]
        for message_id in reversed(ids[-max_items:]):
            fetch_status, parts = client.fetch(message_id, "(RFC822)")
            if fetch_status != "OK" or not parts:
                continue
            raw = parts[0][1]
            if not raw:
                continue
            parsed = BytesParser(policy=policy.default).parsebytes(raw)
            messages.append({
                "message_id": message_id.decode("ascii", "ignore") if isinstance(message_id, bytes) else str(message_id),
                "uid": message_id.decode("ascii", "ignore") if isinstance(message_id, bytes) else str(message_id),
                "subject": _safe_decode_header(str(parsed.get("subject") or "")),
                "from": _safe_decode_header(str(parsed.get("from") or "")),
                "to": _safe_decode_header(str(parsed.get("to") or "")),
                "date": str(parsed.get("date") or ""),
                "preview": _extract_text_preview(parsed),
                "content_type": str(parsed.get_content_type() or ""),
            })
    return {
        "folder": folder_name,
        "messages": messages,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def send_mail_message(
    settings: MailSettings,
    *,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    if not settings.is_configured():
        raise RuntimeError("mail is not configured")
    recipient = _normalize_email_address(to)
    if not recipient:
        raise ValueError("to is required")
    message = EmailMessage()
    message["Subject"] = str(subject or "").strip() or "(no subject)"
    message["From"] = settings.address or settings.username or settings.display_name
    message["To"] = recipient
    if cc and str(cc).strip():
        message["Cc"] = str(cc).strip()
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
        "to": recipient,
        "subject": str(message["Subject"] or ""),
    }


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
