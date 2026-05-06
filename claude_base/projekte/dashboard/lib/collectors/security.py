"""Security-Sektion: Brute-Force / IPS / IPSec-Errors / Auffällige Logins.

Liest aus Graylog (FortiGate-Stream). Vergleicht heute mit gestern und
dem 7-Tage-Schnitt. Trend-Berechnung in `lib.trend`, API-Calls in
`lib.graylog_api`.
"""

from __future__ import annotations

import dataclasses

from .. import graylog_api as gl
from .. import trend


# FortiGate-LogIDs (aus den Live-Logs der FG-120G v7.2.13 verifiziert)
LOGID_ADMIN_LOGIN_FAILED = "0100032002"
LOGID_ADMIN_LOGIN_DISABLED = "0100032021"
LOGID_ADMIN_LOGIN_SUCCESS = "0100032001"
LOGID_IPSEC_PHASE1_ERROR = "0101037124"


@dataclasses.dataclass
class MetricRow:
    name: str
    today: float
    yesterday: float
    week_avg: float
    status: str
    delta_yesterday_pct: float | None
    delta_week_pct: float | None
    note: str = ""


@dataclasses.dataclass
class SecuritySection:
    rows: list[MetricRow]
    top_srcips_today: list[tuple[str, int]]
    top_users_today: list[tuple[str, int]]


def _row(name: str, query: str, *, warn: float, alert: float,
         note: str = "") -> MetricRow:
    s, u = trend.range_today()
    today_n = gl.count(query, since=s, until=u)
    # Fairer Vergleich: gestern bis zur jetzigen Uhrzeit (Apples-to-Apples)
    s, u = trend.range_yesterday_until_now_time()
    yesterday_n = gl.count(query, since=s, until=u)
    s, u = trend.range_last_7d()
    week_total = gl.count(query, since=s, until=u)
    week_avg = week_total / 7.0

    return MetricRow(
        name=name,
        today=float(today_n),
        yesterday=float(yesterday_n),
        week_avg=round(week_avg, 1),
        status=trend.status_for(today_n, warn=warn, alert=alert),
        delta_yesterday_pct=trend.delta_pct(today_n, yesterday_n),
        delta_week_pct=trend.delta_pct(today_n, week_avg),
        note=note,
    )


def collect() -> SecuritySection:
    """Sammelt alle Security-Metriken. Sequenziell -- bei Bedarf später
    via ThreadPoolExecutor parallelisieren (Graylog kann das ab)."""
    rows = [
        _row("admin_login_failed",
             query=f'logid:{LOGID_ADMIN_LOGIN_FAILED}',
             warn=100, alert=1000,
             note="FortiGate-Web-Mgmt -- Brute-Force-Indikator"),
        _row("admin_login_disabled",
             query=f'logid:{LOGID_ADMIN_LOGIN_DISABLED}',
             warn=20, alert=200,
             note="3-Strike-Lockout aktiv"),
        _row("admin_login_success",
             query=f'logid:{LOGID_ADMIN_LOGIN_SUCCESS}',
             warn=20, alert=50,
             note="nur audit-User erwartet -- alles drüber prüfen"),
        _row("ipsec_phase1_errors",
             query=f'logid:{LOGID_IPSEC_PHASE1_ERROR}',
             warn=100, alert=1000,
             note="IPSec-Verhandlungs-Fehler -- meist Konfig-Mismatch"),
    ]

    s, u = trend.range_today()
    top_ips = gl.top_values(
        query=f'logid:{LOGID_ADMIN_LOGIN_FAILED}',
        field="srcip", since=s, until=u, size=10, fetch_cap=10000)
    top_users = gl.top_values(
        query=f'logid:{LOGID_ADMIN_LOGIN_FAILED}',
        field="user", since=s, until=u, size=10, fetch_cap=10000)

    return SecuritySection(
        rows=rows,
        top_srcips_today=top_ips,
        top_users_today=top_users,
    )


def render_text(sec: SecuritySection) -> str:
    """Provisorischer ASCII-Output (echter Renderer kommt später in lib/render.py)."""
    lines = ["=== SECURITY ==="]
    lines.append(f"  {'Metric':30s}  {'Today':>10s}  {'Yest.':>10s}  {'7d-avg':>10s}  Status  Δ-yest  Δ-7d")
    for r in sec.rows:
        dy = f"{r.delta_yesterday_pct:+.0f}%" if r.delta_yesterday_pct is not None else "  n/a"
        dw = f"{r.delta_week_pct:+.0f}%" if r.delta_week_pct is not None else "  n/a"
        lines.append(f"  {r.name:30s}  {r.today:>10.0f}  {r.yesterday:>10.0f}  "
                     f"{r.week_avg:>10.1f}  {r.status:6s}  {dy:>6s}  {dw:>6s}")
    if sec.top_srcips_today:
        lines.append("\n  Top-Source-IPs (Brute-Force heute):")
        for ip, n in sec.top_srcips_today[:5]:
            lines.append(f"    {ip:20s}  {n:>5d}")
    if sec.top_users_today:
        lines.append("\n  Top-Users (Brute-Force heute):")
        for u, n in sec.top_users_today[:5]:
            lines.append(f"    {u:20s}  {n:>5d}")
    return "\n".join(lines)


if __name__ == "__main__":
    sec = collect()
    print(render_text(sec))
