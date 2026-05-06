"""GELF-Sender für Dashboard-Metriken (Variante D: Graylog als Snapshot-Store).

Sendet strukturierte Messages an den IBF-Graylog (UDP 12201, GELF 1.1),
sodass der nächste Dashboard-Run via `graylog_search_messages` auf die Werte
von gestern / letzte Woche zugreifen kann -- ohne separate Persistenz-Schicht.

Message-Schema:
    short_message:      "dashboard <section>/<name>=<value>"
    host:               socket.gethostname()  (welcher Client hat gemessen)
    timestamp:          Unix-Sekunden des Send-Zeitpunkts
    level:              6 (info)
    _app:               "ibf-dashboard"           (Filter-Anker)
    _metric_name:       z.B. "admin_login_failed_count"
    _metric_value:      Zahl oder String
    _metric_section:    "security" | "infra" | "backups" | ...
    _dashboard_run_id:  ISO-Timestamp -- alle Metriken eines Runs gleich
                        (zur Korrelation aller Werte eines Snapshots)
    _<custom>:          beliebige zusätzliche Felder via `extra=`

Aufrufe:
    send_metric("admin_login_failed_count", 6789, section="security")
    send_metrics({"a": 1, "b": 2}, section="infra")
    python snapshot.py            # Probe-Send für Diagnose
"""

from __future__ import annotations

import datetime as dt
import json
import socket
import time
from typing import Any, Mapping

GRAYLOG_HOST = "gld.ibf-solutions.com"
GRAYLOG_PORT = 12201  # GELF UDP
APP_NAME = "ibf-dashboard"
MAX_UDP_PAYLOAD = 8000  # konservativ unterhalb der ~8.2 KB MTU-Grenze


def _build_payload(name: str, value: Any, *, section: str,
                   run_id: str, extra: Mapping[str, Any] | None) -> bytes:
    msg: dict[str, Any] = {
        "version": "1.1",
        "host": socket.gethostname(),
        "short_message": f"dashboard {section}/{name}={value}",
        "timestamp": time.time(),
        "level": 6,
        "_app": APP_NAME,
        "_metric_name": name,
        "_metric_value": value,
        "_metric_section": section,
        "_dashboard_run_id": run_id,
    }
    if extra:
        for k, v in extra.items():
            key = k if k.startswith("_") else f"_{k}"
            msg[key] = v
    return json.dumps(msg, default=str).encode("utf-8")


def make_run_id() -> str:
    """ISO-Timestamp ohne Sub-Sekunden -- als gemeinsamer Run-Identifier."""
    return dt.datetime.now().isoformat(timespec="seconds")


def send_metric(name: str, value: Any, *, section: str = "general",
                run_id: str | None = None,
                extra: Mapping[str, Any] | None = None,
                host: str = GRAYLOG_HOST,
                port: int = GRAYLOG_PORT) -> None:
    """Schickt eine Metric als GELF-UDP-Datagram. Fire-and-forget."""
    rid = run_id or make_run_id()
    payload = _build_payload(name, value, section=section, run_id=rid, extra=extra)
    if len(payload) > MAX_UDP_PAYLOAD:
        raise ValueError(
            f"GELF payload {len(payload)} bytes > {MAX_UDP_PAYLOAD}; "
            "chunking nicht implementiert -- Wert kürzen oder TCP nutzen.")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(payload, (host, port))


def send_metrics(metrics: Mapping[str, Any], *, section: str,
                 run_id: str | None = None,
                 extra: Mapping[str, Any] | None = None,
                 host: str = GRAYLOG_HOST,
                 port: int = GRAYLOG_PORT) -> int:
    """Sendet mehrere Metriken einer Sektion mit gemeinsamer run_id.
    Returns Anzahl gesendeter Messages."""
    rid = run_id or make_run_id()
    n = 0
    for name, value in metrics.items():
        send_metric(name, value, section=section, run_id=rid,
                    extra=extra, host=host, port=port)
        n += 1
    return n


if __name__ == "__main__":
    import sys
    rid = make_run_id()
    test_value = int(time.time()) % 100000
    send_metric("probe_test", test_value, section="probe", run_id=rid,
                extra={"probe_human": "GELF-Probe aus snapshot.py"})
    print(f"[OK] sent  metric_name=probe_test  value={test_value}  run_id={rid}")
    print(f"[verify]  Graylog-Suche:")
    print(f"          app:{APP_NAME} AND metric_name:probe_test "
          f"AND dashboard_run_id:\"{rid}\"")
