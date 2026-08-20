from datetime import UTC, datetime

from app.utils.temporal import resolve_date_phrase


def test_today(frozen_now):
    since, until = resolve_date_phrase("today", frozen_now, "UTC")
    assert since.date() == frozen_now.date()
    assert until.date() == frozen_now.date()
    assert since.hour == 0 and until.hour == 23


def test_tomorrow(frozen_now):
    since, _ = resolve_date_phrase("tomorrow", frozen_now, "UTC")
    assert since.date().isoformat() == "2026-08-21"


def test_yesterday(frozen_now):
    since, _ = resolve_date_phrase("yesterday", frozen_now, "UTC")
    assert since.date().isoformat() == "2026-08-19"


def test_next_week_is_next_monday_through_sunday(frozen_now):
    # frozen_now is Thursday 2026-08-20 -> this week's Monday is 2026-08-17
    since, until = resolve_date_phrase("next week", frozen_now, "UTC")
    assert since.date().isoformat() == "2026-08-24"  # next Monday
    assert until.date().isoformat() == "2026-08-30"  # next Sunday
    assert since.weekday() == 0
    assert until.weekday() == 6


def test_last_week(frozen_now):
    since, until = resolve_date_phrase("last week", frozen_now, "UTC")
    assert since.date().isoformat() == "2026-08-10"
    assert until.date().isoformat() == "2026-08-16"


def test_next_weekday_non_inclusive_of_today(frozen_now):
    # frozen_now is a Thursday; "next Thursday" must NOT resolve to today.
    since, _ = resolve_date_phrase("next thursday", frozen_now, "UTC")
    assert since.date().isoformat() == "2026-08-27"


def test_next_tuesday(frozen_now):
    since, until = resolve_date_phrase("next tuesday", frozen_now, "UTC")
    assert since.date().isoformat() == "2026-08-25"
    assert since.weekday() == 1
    assert until.date() == since.date()


def test_bare_weekday_name(frozen_now):
    since, _ = resolve_date_phrase("monday", frozen_now, "UTC")
    assert since.weekday() == 0


def test_this_month(frozen_now):
    since, until = resolve_date_phrase("this month", frozen_now, "UTC")
    assert since.date().isoformat() == "2026-08-01"
    assert until.date().isoformat() == "2026-08-31"


def test_last_month(frozen_now):
    since, until = resolve_date_phrase("last month", frozen_now, "UTC")
    assert since.date().isoformat() == "2026-07-01"
    assert until.date().isoformat() == "2026-07-31"


def test_last_month_across_year_boundary():
    jan_now = datetime(2026, 1, 15, tzinfo=UTC)
    since, until = resolve_date_phrase("last month", jan_now, "UTC")
    assert since.date().isoformat() == "2025-12-01"
    assert until.date().isoformat() == "2025-12-31"


def test_next_month_across_year_boundary():
    dec_now = datetime(2026, 12, 15, tzinfo=UTC)
    since, until = resolve_date_phrase("next month", dec_now, "UTC")
    assert since.date().isoformat() == "2027-01-01"
    assert until.date().isoformat() == "2027-01-31"


def test_unresolvable_phrase_returns_none(frozen_now):
    since, until = resolve_date_phrase("sometime soonish", frozen_now, "UTC")
    assert since is None and until is None


def test_empty_phrase_returns_none(frozen_now):
    assert resolve_date_phrase(None, frozen_now, "UTC") == (None, None)
    assert resolve_date_phrase("", frozen_now, "UTC") == (None, None)


def test_timezone_shifts_day_boundary():
    # 2026-08-20 23:30 UTC is already 2026-08-21 in UTC+2 (e.g. Europe/Berlin in summer).
    now_utc = datetime(2026, 8, 20, 23, 30, tzinfo=UTC)
    since, _ = resolve_date_phrase("today", now_utc, "Europe/Berlin")
    # "today" in Berlin local time is the 21st; bounds are still returned in the input tz (UTC).
    assert since.astimezone(UTC).date().isoformat() == "2026-08-20"
