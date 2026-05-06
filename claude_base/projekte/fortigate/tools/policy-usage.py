#!/usr/bin/env python3
"""Policy-Usage-Analyzer fuer FortiGate-Policies (Graylog-only).

Liest aus IBF-Graylog (gld.ibf-solutions.com) wer/was/wohin ueber eine
FortiGate-Policy laeuft. Reine Lese-Schnittstelle, kein Live-FortiGate-
Zugriff. Setzt voraus, dass `set logtraffic all|enable` auf der Policy
gesetzt ist -- bei `logtraffic utm` + `utm-status disable` werden keine
Logs erzeugt und das Tool meldet 0 Treffer.

Token: Windows Credential Manager (`graylog`/`ibf`), via dashboard-Lib.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

# graylog_api Lib mitbenutzen (count + top_values via Aggregation-API).
_LIB = Path(__file__).resolve().parents[2] / "dashboard" / "lib"
sys.path.insert(0, str(_LIB))
import graylog_api as gl  # type: ignore  # noqa: E402

DEFAULT_SOURCE = "gw"
DEFAULT_LAST = "7d"
DEFAULT_TOP = 10

AGGREGATIONS: list[tuple[str, str]] = [
    ("Top dst-Ports",      "dstport"),
    ("Top Services",       "service"),
    ("Top dst-IP",         "dstip"),
    ("Top src-IP",         "srcip"),
    ("Top src-Interface",  "srcintf"),
    ("Top dst-Interface",  "dstintf"),
    ("Action",             "action"),
]


def parse_last(s: str) -> int:
    m = re.match(r"^(\d+)\s*([smhd]?)$", s.strip())
    if not m:
        sys.exit(f"[ERROR] Ungueltiges --last: {s!r} (z.B. '15m', '2h', '7d', '90s')")
    n, u = int(m.group(1)), m.group(2) or "s"
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[u]


def fmt_count(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def run_one(policy_id: str, since: dt.datetime, until: dt.datetime,
            source: str, top: int) -> None:
    base_q = f"source:{source} AND policyid:{policy_id}"

    total = gl.count(base_q, since=since, until=until)

    span = f"{since:%Y-%m-%d %H:%M} -> {until:%Y-%m-%d %H:%M}"
    print()
    print(f"=== Policy {policy_id}  source:{source}  {span} ===")

    if total == 0:
        any_total = gl.count(f"source:{source} AND _exists_:policyid",
                             since=since, until=until)
        print("  Treffer: 0  -- Policy hat im Zeitraum NICHTS geloggt")
        if any_total:
            print(f"  ({fmt_count(any_total)} policyid-Eintraege total von dieser Source)")
        print("  Moegliche Ursachen:")
        print("    - Policy ist tatsaechlich unbenutzt")
        print("    - 'set logtraffic disable' oder 'logtraffic utm' bei UTM-disable")
        print("      (Live-Hit-Counter via 'diagnose firewall iprope show 100004 <id>'")
        print("       gibt darueber Aufschluss -- ausserhalb dieses Tools)")
        print("    - Falsche Policy-ID oder falsche --source")
        return

    print(f"  Treffer: {fmt_count(total)}")

    last = gl.messages(base_q, since=since, until=until, limit=1,
                       fields="timestamp")
    if last:
        ts = last[0].get("timestamp", "?")
        print(f"  Letzter Hit: {ts}")

    for header, field in AGGREGATIONS:
        rows = gl.top_values(base_q, field, since=since, until=until, size=top)
        if not rows:
            continue
        print(f"\n  {header} (Top {len(rows)})")
        width = max(len(str(v)) for v, _ in rows)
        cwidth = max(len(fmt_count(c)) for _, c in rows)
        for v, c in rows:
            print(f"    {v:<{width}}  {fmt_count(c):>{cwidth}}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="policy-usage.py",
        description="FortiGate-Policy-Usage aus Graylog-Logs (read-only).",
        epilog=(
            "Beispiele:\n"
            "  policy-usage.py 893\n"
            "  policy-usage.py 617 1214 --last 30d --top 15\n"
            "  policy-usage.py 893 --source gw --last 24h\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("policy_ids", nargs="+", metavar="ID",
                   help="eine oder mehrere FortiGate-Policy-IDs (numerisch)")
    p.add_argument("--last", default=DEFAULT_LAST,
                   help=f"Zeitfenster, Format '15m','2h','7d','90s' "
                        f"(default {DEFAULT_LAST})")
    p.add_argument("--top", type=int, default=DEFAULT_TOP,
                   help=f"Top-N je Aggregation (default {DEFAULT_TOP})")
    p.add_argument("--source", default=DEFAULT_SOURCE,
                   help=f"Graylog-source-Filter (default '{DEFAULT_SOURCE}')")
    return p


def main() -> None:
    args = build_parser().parse_args()
    until = dt.datetime.now().astimezone()
    since = until - dt.timedelta(seconds=parse_last(args.last))
    for pid in args.policy_ids:
        if not pid.isdigit():
            print(f"[WARN] Policy-ID '{pid}' ist nicht numerisch -- skip.",
                  file=sys.stderr)
            continue
        try:
            run_one(pid, since, until, args.source, args.top)
        except Exception as e:
            print(f"[ERROR] Policy {pid}: {type(e).__name__}: {e}",
                  file=sys.stderr)
    print()


if __name__ == "__main__":
    main()
