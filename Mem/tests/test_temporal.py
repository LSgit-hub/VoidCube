from datetime import datetime, timezone

from memai import TemporalNormalizer, TimePrecision


REFERENCE = datetime(2026, 3, 22, 12, 0, tzinfo=timezone.utc)


def test_normalizes_yesterday_in_english() -> None:
    span = TemporalNormalizer().normalize(
        "yesterday we discussed schema updates", REFERENCE
    )
    assert span.start.date().isoformat() == "2026-03-21"
    assert span.precision == TimePrecision.DAY


def test_normalizes_last_week_in_chinese() -> None:
    span = TemporalNormalizer().normalize("上周我们完成了第一版框架", REFERENCE)
    assert span.start.date().isoformat() == "2026-03-09"
    assert span.precision == TimePrecision.WEEK


def test_normalizes_recently_as_approx_range() -> None:
    span = TemporalNormalizer().normalize("最近我们一直在整理主线和支线", REFERENCE)
    assert span.precision == TimePrecision.APPROX
    assert (span.end - span.start).days >= 13
