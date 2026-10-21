from datetime import date, datetime, timezone

from services.periods import period


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_evening_boston_is_still_today_not_tomorrow_utc():
    # 21:15 EDT on Tue Oct 20 2026 == 01:15 UTC Wed Oct 21
    p = period("Today", now=at("2026-10-21T01:15:00"))
    assert p.start_date == date(2026, 10, 20)


def test_week_is_sunday_to_saturday():
    p = period("This Week", now=at("2026-10-21T15:00:00"))  # Wed
    assert (p.start_date, p.end_date) == (date(2026, 10, 18), date(2026, 10, 24))
    lw = period("Last Week", now=at("2026-10-21T15:00:00"))
    assert (lw.start_date, lw.end_date) == (date(2026, 10, 11), date(2026, 10, 17))


def test_utc_bounds_follow_dst():
    summer = period("Today", now=at("2026-07-01T15:00:00"))
    winter = period("Today", now=at("2026-12-01T15:00:00"))
    assert summer.start_utc.hour == 4  # EDT
    assert winter.start_utc.hour == 5  # EST


def test_dst_change_day_is_23_hours():
    p = period("Custom", custom=(date(2026, 3, 8), date(2026, 3, 8)))
    assert (p.end_utc - p.start_utc).total_seconds() == 23 * 3600


def test_month_presets():
    p = period("Last Month", now=at("2026-03-10T15:00:00"))
    assert (p.start_date, p.end_date) == (date(2026, 2, 1), date(2026, 2, 28))
    t = period("This Month", now=at("2026-12-10T15:00:00"))
    assert t.end_date == date(2026, 12, 31)


def test_future_presets_and_contains():
    now = at("2026-10-21T15:00:00")
    assert period("Tomorrow", now=now).start_date == date(2026, 10, 22)
    n7 = period("Next 7 Days", now=now)
    assert (n7.start_date, n7.end_date) == (date(2026, 10, 21), date(2026, 10, 27))
    rem = period("Remaining This Week", now=now)
    assert rem.end_date == date(2026, 10, 24)
    assert period("Today", now=now).contains(at("2026-10-21T23:30:00"))  # 19:30 Boston
    assert not period("Today", now=now).contains(at("2026-10-22T04:30:00"))  # 00:30 next day Boston
