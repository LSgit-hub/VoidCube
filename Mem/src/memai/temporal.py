from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .schema import TemporalSpan, TimePrecision, UTC


ISO_DATE_RE = re.compile(r"(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})")


def _start_of_day(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _end_of_day(value: datetime) -> datetime:
    return _start_of_day(value) + timedelta(days=1) - timedelta(microseconds=1)


def _start_of_week(value: datetime) -> datetime:
    day_start = _start_of_day(value)
    return day_start - timedelta(days=day_start.weekday())


def _end_of_week(value: datetime) -> datetime:
    return _start_of_week(value) + timedelta(days=7) - timedelta(microseconds=1)


def _start_of_month(value: datetime) -> datetime:
    day_start = _start_of_day(value)
    return day_start.replace(day=1)


def _end_of_month(value: datetime) -> datetime:
    start = _start_of_month(value)
    _, last_day = calendar.monthrange(start.year, start.month)
    return start.replace(
        day=last_day, hour=23, minute=59, second=59, microsecond=999999
    )


@dataclass(slots=True)
class TemporalMatch:
    span: TemporalSpan
    matched_text: str


class TemporalNormalizer:
    """Normalize common Chinese and English temporal expressions."""

    def normalize(
        self, text: str, reference_time: datetime | None = None
    ) -> TemporalSpan:
        reference = (
            reference_time.astimezone(UTC) if reference_time else datetime.now(tz=UTC)
        )
        cleaned = text.strip()

        exact_date = self._parse_exact_date(cleaned)
        if exact_date:
            return exact_date

        for matcher in (
            self._parse_relative_days,
            self._parse_weekly,
            self._parse_monthly,
            self._parse_recent,
        ):
            span = matcher(cleaned, reference)
            if span is not None:
                return span

        return TemporalSpan(
            start=_start_of_day(reference),
            end=_end_of_day(reference),
            precision=TimePrecision.APPROX,
            confidence=0.35,
            source_text=cleaned,
        )

    def _parse_exact_date(self, text: str) -> TemporalSpan | None:
        match = ISO_DATE_RE.search(text)
        if not match:
            return None
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        dt = datetime(year, month, day, tzinfo=UTC)
        return TemporalSpan(
            start=_start_of_day(dt),
            end=_end_of_day(dt),
            precision=TimePrecision.DAY,
            confidence=0.98,
            source_text=match.group(0),
        )

    def _parse_relative_days(
        self, text: str, reference: datetime
    ) -> TemporalSpan | None:
        if any(token in text for token in ("today", "Today", "今天")):
            return TemporalSpan(
                _start_of_day(reference),
                _end_of_day(reference),
                TimePrecision.DAY,
                0.97,
                "today",
            )
        if any(token in text for token in ("yesterday", "Yesterday", "昨天")):
            target = reference - timedelta(days=1)
            return TemporalSpan(
                _start_of_day(target),
                _end_of_day(target),
                TimePrecision.DAY,
                0.97,
                "yesterday",
            )
        if any(token in text for token in ("tomorrow", "Tomorrow", "明天")):
            target = reference + timedelta(days=1)
            return TemporalSpan(
                _start_of_day(target),
                _end_of_day(target),
                TimePrecision.DAY,
                0.92,
                "tomorrow",
            )

        ago_match = re.search(
            r"(?P<num>\d+)\s*(day|days)\s*ago", text, flags=re.IGNORECASE
        )
        zh_ago_match = re.search(r"(?P<num>\d+)天前", text)
        later_match = re.search(
            r"in\s*(?P<num>\d+)\s*(day|days)", text, flags=re.IGNORECASE
        )
        zh_later_match = re.search(r"(?P<num>\d+)天后", text)

        if ago_match or zh_ago_match:
            match = ago_match or zh_ago_match
            target = reference - timedelta(days=int(match.group("num")))
            return TemporalSpan(
                _start_of_day(target),
                _end_of_day(target),
                TimePrecision.DAY,
                0.93,
                match.group(0),
            )

        if later_match or zh_later_match:
            match = later_match or zh_later_match
            target = reference + timedelta(days=int(match.group("num")))
            return TemporalSpan(
                _start_of_day(target),
                _end_of_day(target),
                TimePrecision.DAY,
                0.88,
                match.group(0),
            )

        return None

    def _parse_weekly(self, text: str, reference: datetime) -> TemporalSpan | None:
        if any(token in text for token in ("this week", "This week", "本周", "这周")):
            return TemporalSpan(
                _start_of_week(reference),
                _end_of_week(reference),
                TimePrecision.WEEK,
                0.95,
                "this week",
            )
        if any(token in text for token in ("last week", "Last week", "上周")):
            target = reference - timedelta(days=7)
            return TemporalSpan(
                _start_of_week(target),
                _end_of_week(target),
                TimePrecision.WEEK,
                0.95,
                "last week",
            )

        ago_match = re.search(
            r"(?P<num>\d+)\s*(week|weeks)\s*ago", text, flags=re.IGNORECASE
        )
        zh_ago_match = re.search(r"(?P<num>\d+)周前", text)
        if ago_match or zh_ago_match:
            match = ago_match or zh_ago_match
            target = reference - timedelta(days=7 * int(match.group("num")))
            return TemporalSpan(
                _start_of_week(target),
                _end_of_week(target),
                TimePrecision.WEEK,
                0.9,
                match.group(0),
            )
        return None

    def _parse_monthly(self, text: str, reference: datetime) -> TemporalSpan | None:
        if any(
            token in text for token in ("this month", "This month", "本月", "这个月")
        ):
            return TemporalSpan(
                _start_of_month(reference),
                _end_of_month(reference),
                TimePrecision.MONTH,
                0.95,
                "this month",
            )
        if any(
            token in text
            for token in ("earlier this month", "Earlier this month", "这个月早些时候")
        ):
            start = _start_of_month(reference)
            end = _end_of_day(reference - timedelta(days=1))
            return TemporalSpan(
                start, end, TimePrecision.MONTH, 0.82, "earlier this month"
            )
        if any(token in text for token in ("last month", "Last month", "上个月")):
            month = reference.month - 1 or 12
            year = reference.year - 1 if reference.month == 1 else reference.year
            target = datetime(year, month, 1, tzinfo=UTC)
            return TemporalSpan(
                _start_of_month(target),
                _end_of_month(target),
                TimePrecision.MONTH,
                0.95,
                "last month",
            )

        ago_match = re.search(
            r"(?P<num>\d+)\s*(month|months)\s*ago", text, flags=re.IGNORECASE
        )
        zh_ago_match = re.search(r"(?P<num>\d+)个月前", text)
        if ago_match or zh_ago_match:
            match = ago_match or zh_ago_match
            count = int(match.group("num"))
            month_index = reference.month - count
            year = reference.year
            while month_index <= 0:
                month_index += 12
                year -= 1
            target = datetime(year, month_index, 1, tzinfo=UTC)
            return TemporalSpan(
                _start_of_month(target),
                _end_of_month(target),
                TimePrecision.MONTH,
                0.88,
                match.group(0),
            )
        return None

    def _parse_recent(self, text: str, reference: datetime) -> TemporalSpan | None:
        if any(
            token in text for token in ("recently", "lately", "最近", "前阵子", "近期")
        ):
            start = _start_of_day(reference - timedelta(days=14))
            end = _end_of_day(reference)
            return TemporalSpan(start, end, TimePrecision.APPROX, 0.7, "recently")
        return None
