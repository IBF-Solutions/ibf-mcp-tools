"""Logs-Sektion: Graylog-Health.

Liest direkt vom Graylog REST -- damit das Dashboard auch dann eine
Aussage trifft, wenn Graylog Probleme hat (URGENT-Notifications,
Indexer-Status, Message-Rate eingebrochen).
"""

from __future__ import annotations

import base64
import dataclasses
import json
import ssl
import urllib.error
import urllib.request

import keyring

from .. import trend
from .. import graylog_api as gl

API = "https://gld.ibf-solutions.com/api"


def _request(path: str) -> dict:
    tok = keyring.get_password("graylog", "ibf")
    auth = base64.b64encode(f"{tok}:token".encode()).decode()
    req = urllib.request.Request(f"{API}{path}", headers={
        "Authorization": f"Basic {auth}",
        "X-Requested-By": "ibf-dashboard",
        "Accept": "application/json",
    })
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
        return json.loads(r.read())


@dataclasses.dataclass
class LogsSection:
    indexer: str                          # green / yellow / red
    notifications_urgent: int
    notifications_normal: int
    notifications_titles: list[str]       # nur die urgent + first paar
    msg_count_today: int
    msg_count_yesterday: int
    msg_count_7d_avg: float
    rate_status: str
    delta_yesterday_pct: float | None


def collect() -> LogsSection:
    try:
        sys_data = _request("/system")
    except Exception:
        sys_data = {}
    try:
        notif = _request("/system/notifications") or {}
    except Exception:
        notif = {}

    indexer_color = "?"
    try:
        idx = _request("/system/indexer/cluster/health")
        indexer_color = (idx.get("status") or "?").lower()
    except Exception:
        pass

    n_list = notif.get("notifications", [])
    urgent = sum(1 for n in n_list if (n.get("severity") or "").upper() == "URGENT")
    normal = sum(1 for n in n_list if (n.get("severity") or "").upper() == "NORMAL")
    titles = [str(n.get("type") or "?") for n in n_list[:5]]

    s, u = trend.range_today()
    today_n = gl.count("*", since=s, until=u)
    s, u = trend.range_yesterday_until_now_time()
    yest_n = gl.count("*", since=s, until=u)
    s, u = trend.range_last_7d()
    week_total = gl.count("*", since=s, until=u)
    week_avg = week_total / 7.0

    delta_y = trend.delta_pct(today_n, yest_n)
    if delta_y is not None and delta_y < -30:
        rate_status = "ALERT"   # Rate gegenüber gestern um >30 % eingebrochen -> Logs verloren?
    elif urgent > 0 or indexer_color in ("red", "yellow"):
        rate_status = "ALERT" if (urgent > 0 or indexer_color == "red") else "WARN"
    else:
        rate_status = "OK"

    return LogsSection(
        indexer=indexer_color,
        notifications_urgent=urgent,
        notifications_normal=normal,
        notifications_titles=titles,
        msg_count_today=today_n,
        msg_count_yesterday=yest_n,
        msg_count_7d_avg=round(week_avg, 0),
        rate_status=rate_status,
        delta_yesterday_pct=delta_y,
    )


def render_text(sec: LogsSection) -> str:
    out = ["=== LOGS (Graylog-Health) ==="]
    out.append(f"  Indexer:        {sec.indexer.upper()}")
    out.append(f"  Notifications:  urgent={sec.notifications_urgent}  "
               f"normal={sec.notifications_normal}")
    if sec.notifications_titles:
        out.append(f"  Top types:      {', '.join(sec.notifications_titles)}")
    dy = (f"{sec.delta_yesterday_pct:+.0f}%"
          if sec.delta_yesterday_pct is not None else "n/a")
    out.append(f"  Messages today: {sec.msg_count_today:,}  "
               f"(gestern bis jetzt: {sec.msg_count_yesterday:,}, "
               f"Δ {dy})")
    out.append(f"  7d-avg/Tag:     {sec.msg_count_7d_avg:,.0f}")
    out.append(f"  Status:         {sec.rate_status}")
    return "\n".join(out)


if __name__ == "__main__":
    print(render_text(collect()))
