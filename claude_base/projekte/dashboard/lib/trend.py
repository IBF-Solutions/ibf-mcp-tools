"""Trend-Vergleichs-Helper fürs Morning-Dashboard.

Liefert absolute Zeitfenster (lokale Zeit), die für Vergleiche „heute" /
„gestern" / „letzte 7 Tage" verwendet werden. Bewusst absolute statt
relative Ranges, damit DST-Wechsel und Run-Uhrzeiten keine Verschiebung
verursachen.

Konventionen:
- „heute"      = today_start() bis now()
- „gestern"    = yesterday_start() bis yesterday_end() (voller Kalendertag)
- „letzte 7 Tage" = 7d_ago_start() bis yesterday_end()
                    (7 volle Kalendertage; heute zählt nicht mit, damit der
                    Vergleich konsistent ist, egal um wie viel Uhr der
                    Dashboard-Run startet)

`now()` ist als Funktion ausgelagert, damit Tests via Monkeypatch eine
fixierte Uhrzeit injizieren können.
"""

from __future__ import annotations

import datetime as _dt
from typing import Tuple

DateRange = Tuple[_dt.datetime, _dt.datetime]


def now() -> _dt.datetime:
    return _dt.datetime.now()


def today_start(_now: _dt.datetime | None = None) -> _dt.datetime:
    n = _now or now()
    return n.replace(hour=0, minute=0, second=0, microsecond=0)


def yesterday_start(_now: _dt.datetime | None = None) -> _dt.datetime:
    return today_start(_now) - _dt.timedelta(days=1)


def yesterday_end(_now: _dt.datetime | None = None) -> _dt.datetime:
    """Letzter Mikrosekunden-Tick von gestern -- exklusiv-zu-heute-Grenze."""
    return today_start(_now) - _dt.timedelta(microseconds=1)


def days_ago_start(n: int, _now: _dt.datetime | None = None) -> _dt.datetime:
    """Anfang des Tages, der n Tage vor heute liegt."""
    if n < 0:
        raise ValueError(f"days_ago_start: n muss >= 0 sein (war {n})")
    return today_start(_now) - _dt.timedelta(days=n)


def range_today(_now: _dt.datetime | None = None) -> DateRange:
    n = _now or now()
    return today_start(n), n


def range_yesterday(_now: _dt.datetime | None = None) -> DateRange:
    return yesterday_start(_now), yesterday_end(_now)


def range_last_7d(_now: _dt.datetime | None = None) -> DateRange:
    """Die letzten 7 vollen Kalendertage: gestern und 6 davor.

    Heute selbst ist NICHT enthalten -- damit der 7d-Schnitt nicht
    angefangen-Tagsteil-Bias bekommt. Werte werden später durch 7
    geteilt für den Tagesdurchschnitt.
    """
    return days_ago_start(7, _now), yesterday_end(_now)


def range_yesterday_until_now_time(_now: _dt.datetime | None = None) -> DateRange:
    """Gestern 00:00 bis gestern zur jetzigen Uhrzeit -- für faire
    „heute-bisher vs. gestern-bisher"-Vergleiche, sodass nicht ein
    Vormittagsstand gegen einen vollen Tag verglichen wird."""
    n = _now or now()
    return yesterday_start(n), n - _dt.timedelta(days=1)


def range_n_days_back(days_back: int, _now: _dt.datetime | None = None) -> DateRange:
    """Generischer Vergleichs-Range: der Kalendertag, der `days_back` Tage
    in der Vergangenheit liegt. days_back=1 -> gestern, days_back=7 ->
    heute vor einer Woche."""
    if days_back < 1:
        raise ValueError(f"days_back muss >= 1 sein (war {days_back})")
    start = days_ago_start(days_back, _now)
    end = start + _dt.timedelta(days=1) - _dt.timedelta(microseconds=1)
    return start, end


def delta_pct(current: float, baseline: float) -> float | None:
    """Relative Änderung in Prozent. None wenn baseline 0 ist (Division
    nicht definiert)."""
    if baseline == 0:
        return None
    return (current - baseline) / baseline * 100.0


def status_for(current: float, *, warn: float, alert: float) -> str:
    """Vergibt OK/WARN/ALERT anhand absoluter Schwellen."""
    if current >= alert:
        return "ALERT"
    if current >= warn:
        return "WARN"
    return "OK"
