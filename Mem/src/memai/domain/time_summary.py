"""Pure contracts and deterministic calendar rules for time-summary indexes."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class SessionSnapshotChanged(RuntimeError):
    """Raised when turns change while a session summary is being generated."""


class DaySnapshotChanged(RuntimeError):
    """Raised when session summaries change while a day is being summarized."""


class CalendarSnapshotChanged(RuntimeError):
    """Raised when calendar child summaries change during aggregation."""


@dataclass(frozen=True, slots=True)
class SessionTurn:
    turn_id: str
    speaker: str
    text: str
    timestamp: str

    @property
    def evidence_hash(self) -> str:
        return _sha256(
            _canonical_json(
                {
                    "turn_id": self.turn_id,
                    "speaker": self.speaker,
                    "text": self.text,
                    "timestamp": self.timestamp,
                }
            )
        )

    def as_prompt_item(self) -> dict[str, str]:
        return {
            "turn_id": self.turn_id,
            "speaker": self.speaker,
            "timestamp": self.timestamp,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class DaySessionSummary:
    summary_id: str
    session_id: str
    period_start: str
    period_end: str
    title: str
    summary: str
    outcomes: tuple[str, ...]
    open_questions: tuple[str, ...]
    content_hash: str
    version: int

    def as_prompt_item(self) -> dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "session_id": self.session_id,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "title": self.title,
            "summary": self.summary,
            "outcomes": list(self.outcomes),
            "open_questions": list(self.open_questions),
            "content_hash": self.content_hash,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class CalendarChildSummary:
    """A direct child summary used by week and month indexes."""

    summary_id: str
    bucket_key: str
    period_start: str
    period_end: str
    title: str
    summary: str
    outcomes: tuple[str, ...]
    open_questions: tuple[str, ...]
    content_hash: str
    version: int

    def as_prompt_item(self) -> dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "bucket_key": self.bucket_key,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "title": self.title,
            "summary": self.summary,
            "outcomes": list(self.outcomes),
            "open_questions": list(self.open_questions),
            "content_hash": self.content_hash,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class TimeSummaryDraft:
    title: str
    summary: str
    outcomes: tuple[str, ...]
    open_questions: tuple[str, ...]

    @property
    def content_hash(self) -> str:
        return _sha256(
            _canonical_json(
                {
                    "title": self.title,
                    "summary": self.summary,
                    "outcomes": self.outcomes,
                    "open_questions": self.open_questions,
                }
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "outcomes": list(self.outcomes),
            "open_questions": list(self.open_questions),
        }


def normalize_time_summary(
    payload: Mapping[str, Any],
    *,
    summary_type: str,
) -> TimeSummaryDraft:
    title = str(payload.get("title") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    label = summary_type.capitalize()
    if not title or not summary:
        raise ValueError(f"{label} summary requires a non-empty title and summary")
    if len(title) > 300:
        raise ValueError(f"{label} summary title exceeds 300 characters")
    if len(summary) > 8000:
        raise ValueError(f"{label} summary exceeds 8000 characters")
    return TimeSummaryDraft(
        title=title,
        summary=summary,
        outcomes=_string_items(
            payload.get("outcomes"),
            summary_type=summary_type,
            field="outcomes",
        ),
        open_questions=_string_items(
            payload.get("open_questions"),
            summary_type=summary_type,
            field="open_questions",
        ),
    )


def normalize_session_summary(payload: Mapping[str, Any]) -> TimeSummaryDraft:
    return normalize_time_summary(payload, summary_type="session")


def normalize_day_summary(payload: Mapping[str, Any]) -> TimeSummaryDraft:
    return normalize_time_summary(payload, summary_type="day")


def normalize_week_summary(payload: Mapping[str, Any]) -> TimeSummaryDraft:
    return normalize_time_summary(payload, summary_type="week")


def normalize_month_summary(payload: Mapping[str, Any]) -> TimeSummaryDraft:
    return normalize_time_summary(payload, summary_type="month")


def session_source_hash(turns: Sequence[SessionTurn]) -> str:
    return _sha256(_canonical_json([turn.as_prompt_item() for turn in turns]))


def day_source_hash(summaries: Sequence[DaySessionSummary]) -> str:
    return _sha256(_canonical_json([summary.as_prompt_item() for summary in summaries]))


def calendar_source_hash(summaries: Sequence[CalendarChildSummary]) -> str:
    return _sha256(_canonical_json([summary.as_prompt_item() for summary in summaries]))


def resolve_time_summary_timezone(timezone_name: str):
    normalized = str(timezone_name or "").strip()
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        fixed_offsets = {
            "Asia/Shanghai": timedelta(hours=8),
            "UTC": timedelta(0),
            "Etc/UTC": timedelta(0),
        }
        if normalized in fixed_offsets:
            return timezone(fixed_offsets[normalized], name=normalized)
        raise ValueError(
            f"Time-summary timezone data is unavailable for {normalized!r}"
        ) from exc


def day_bucket_for_timestamp(timestamp: str, *, timezone_name: str) -> str:
    zone = resolve_time_summary_timezone(timezone_name)
    return parse_timestamp(timestamp).astimezone(zone).date().isoformat()


def day_period(day_key: str, *, timezone_name: str) -> tuple[str, str]:
    try:
        bucket_date = date.fromisoformat(str(day_key))
    except ValueError as exc:
        raise ValueError("Day summary bucket must use YYYY-MM-DD") from exc
    if bucket_date.isoformat() != str(day_key):
        raise ValueError("Day summary bucket must use YYYY-MM-DD")
    zone = resolve_time_summary_timezone(timezone_name)
    start = datetime.combine(bucket_date, time.min, tzinfo=zone)
    end = datetime.combine(bucket_date + timedelta(days=1), time.min, tzinfo=zone)
    return start.isoformat(), end.isoformat()


def week_bucket_for_timestamp(timestamp: str, *, timezone_name: str) -> str:
    local_date = parse_timestamp(timestamp).astimezone(
        resolve_time_summary_timezone(timezone_name)
    ).date()
    iso_year, iso_week, _ = local_date.isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def week_period(week_key: str, *, timezone_name: str) -> tuple[str, str]:
    match = re.fullmatch(r"(\d{4})-W(\d{2})", str(week_key))
    if not match:
        raise ValueError("Week summary bucket must use YYYY-Www")
    year, week = int(match.group(1)), int(match.group(2))
    try:
        monday = date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise ValueError("Week summary bucket is not a valid ISO week") from exc
    zone = resolve_time_summary_timezone(timezone_name)
    start = datetime.combine(monday, time.min, tzinfo=zone)
    end = start + timedelta(days=7)
    return start.isoformat(), end.isoformat()


def month_bucket_for_timestamp(timestamp: str, *, timezone_name: str) -> str:
    local_date = parse_timestamp(timestamp).astimezone(
        resolve_time_summary_timezone(timezone_name)
    ).date()
    return f"{local_date.year:04d}-{local_date.month:02d}"


def month_period(month_key: str, *, timezone_name: str) -> tuple[str, str]:
    match = re.fullmatch(r"(\d{4})-(\d{2})", str(month_key))
    if not match:
        raise ValueError("Month summary bucket must use YYYY-MM")
    year, month = int(match.group(1)), int(match.group(2))
    try:
        month_start = date(year, month, 1)
    except ValueError as exc:
        raise ValueError("Month summary bucket is not a valid calendar month") from exc
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    zone = resolve_time_summary_timezone(timezone_name)
    start = datetime.combine(month_start, time.min, tzinfo=zone)
    end = datetime.combine(next_month, time.min, tzinfo=zone)
    return start.isoformat(), end.isoformat()


def json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def turn_sort_key(turn: SessionTurn) -> tuple[datetime, str]:
    return parse_timestamp(turn.timestamp), turn.turn_id


def day_source_sort_key(
    summary: DaySessionSummary,
) -> tuple[datetime, datetime, str, str]:
    return (
        parse_timestamp(summary.period_start),
        parse_timestamp(summary.period_end),
        summary.session_id,
        summary.summary_id,
    )


def parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _string_items(
    value: Any,
    *,
    summary_type: str,
    field: str,
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{summary_type.capitalize()} summary {field} must be an array")
    items: list[str] = []
    for raw in value:
        item = str(raw or "").strip()
        if item and item not in items:
            items.append(item)
    if len(items) > 50:
        raise ValueError(f"{summary_type.capitalize()} summary {field} exceeds 50 items")
    return tuple(items)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
