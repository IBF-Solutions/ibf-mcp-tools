"""Tests für lib/trend.py -- v.a. Range-Berechnung an kritischen Tagen
(DST-Wechsel, Monatswechsel, Schaltjahr)."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import trend  # noqa: E402


def _at(*args) -> dt.datetime:
    return dt.datetime(*args)


def test_today_start_strips_time_components():
    n = _at(2026, 5, 5, 14, 37, 12, 999999)
    assert trend.today_start(n) == _at(2026, 5, 5, 0, 0, 0)


def test_range_today_endet_bei_now():
    n = _at(2026, 5, 5, 14, 37, 12)
    start, end = trend.range_today(n)
    assert start == _at(2026, 5, 5, 0, 0, 0)
    assert end == n


def test_range_yesterday_ist_kompletter_kalendertag():
    n = _at(2026, 5, 5, 14, 37, 12)
    start, end = trend.range_yesterday(n)
    assert start == _at(2026, 5, 4, 0, 0, 0)
    assert end == _at(2026, 5, 4, 23, 59, 59, 999999)


def test_range_yesterday_ueber_monatswechsel():
    n = _at(2026, 6, 1, 9, 0, 0)
    start, end = trend.range_yesterday(n)
    assert start == _at(2026, 5, 31, 0, 0, 0)
    assert end == _at(2026, 5, 31, 23, 59, 59, 999999)


def test_range_yesterday_ueber_jahreswechsel():
    n = _at(2027, 1, 1, 0, 0, 1)
    start, end = trend.range_yesterday(n)
    assert start == _at(2026, 12, 31, 0, 0, 0)
    assert end == _at(2026, 12, 31, 23, 59, 59, 999999)


def test_range_last_7d_enthaelt_genau_7_tage_und_endet_gestern():
    n = _at(2026, 5, 5, 9, 0, 0)
    start, end = trend.range_last_7d(n)
    assert start == _at(2026, 4, 28, 0, 0, 0)
    assert end == _at(2026, 5, 4, 23, 59, 59, 999999)
    span_days = (end - start).total_seconds() / 86400
    assert 6.99 < span_days < 7.0


def test_range_last_7d_beruehrt_heute_NICHT():
    n = _at(2026, 5, 5, 23, 59, 0)
    _, end = trend.range_last_7d(n)
    assert end < trend.today_start(n)


def test_range_n_days_back_genau_ein_kalendertag():
    n = _at(2026, 5, 5, 9, 0, 0)
    start, end = trend.range_n_days_back(7, n)
    assert start == _at(2026, 4, 28, 0, 0, 0)
    assert end == _at(2026, 4, 28, 23, 59, 59, 999999)
    span = end - start
    assert dt.timedelta(hours=23, minutes=59, seconds=59) <= span < dt.timedelta(days=1)


def test_range_n_days_back_einen_tag_zurueck_ist_gestern():
    n = _at(2026, 5, 5, 9, 0, 0)
    assert trend.range_n_days_back(1, n) == trend.range_yesterday(n)


def test_days_ago_start_invalid_n():
    try:
        trend.days_ago_start(-1, _at(2026, 5, 5))
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError erwartet bei negativem n")


def test_n_days_back_invalid():
    try:
        trend.range_n_days_back(0, _at(2026, 5, 5))
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError erwartet bei days_back<1")


def test_range_yesterday_dst_uebergang_oktober_2026():
    """Letzter Sonntag im Oktober 2026 = 25.10. (DST-Ende in Mitteleuropa).
    Range darf den DST-Wechsel nicht zerschießen -- voller Kalendertag bleibt
    voller Kalendertag, auch wenn er real 25h hat (naive datetime ignoriert
    DST, aber die Range-Grenzen müssen sauber bleiben)."""
    n = _at(2026, 10, 26, 8, 0, 0)
    start, end = trend.range_yesterday(n)
    assert start == _at(2026, 10, 25, 0, 0, 0)
    assert end == _at(2026, 10, 25, 23, 59, 59, 999999)


def test_range_yesterday_until_now_time():
    n = _at(2026, 5, 5, 10, 18, 0)
    start, end = trend.range_yesterday_until_now_time(n)
    assert start == _at(2026, 5, 4, 0, 0, 0)
    assert end == _at(2026, 5, 4, 10, 18, 0)


def test_delta_pct_basic():
    assert trend.delta_pct(150, 100) == 50.0
    assert trend.delta_pct(50, 100) == -50.0
    assert trend.delta_pct(100, 100) == 0.0


def test_delta_pct_baseline_zero():
    assert trend.delta_pct(10, 0) is None
    assert trend.delta_pct(0, 0) is None


def test_status_for_thresholds():
    assert trend.status_for(50, warn=100, alert=1000) == "OK"
    assert trend.status_for(100, warn=100, alert=1000) == "WARN"
    assert trend.status_for(999, warn=100, alert=1000) == "WARN"
    assert trend.status_for(1000, warn=100, alert=1000) == "ALERT"
    assert trend.status_for(99999, warn=100, alert=1000) == "ALERT"


def main():
    fns = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  OK    {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
