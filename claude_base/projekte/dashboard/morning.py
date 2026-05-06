"""IBF Morning Dashboard -- CLI entry point.

Usage:
    python morning.py                    # ASCII auf stdout, alle Sektionen
    python morning.py --html out.html    # HTML in Datei
    python morning.py --section security
    python morning.py --no-snapshot      # ohne GELF-Push
    python morning.py --no-color
    python morning.py --skip cloud,logs  # einzelne Sektionen weglassen

Trend-Vergleich:
- "Heute"     = today 00:00 bis jetzt
- "Gestern"   = gestern 00:00 bis gestern zur jetzigen Uhrzeit (fair)
- "7d-avg"    = letzten 7 vollen Kalendertage geteilt durch 7

Snapshots werden nach jedem Run als GELF-Messages an Graylog gepusht
(`app:ibf-dashboard`), damit die Trend-Vergleiche der nächsten Runs
auch eigene Werte sehen können.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Lokales Package
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import render, snapshot                               # noqa: E402
from lib.collectors import (                                    # noqa: E402
    backups, cloud, infra, logs, network, security,
)


COLLECTORS = {
    "security": (security.collect, security.render_text),
    "infra":    (infra.collect,    infra.render_text),
    "backups":  (backups.collect,  backups.render_text),
    "network":  (network.collect,  network.render_text),
    "cloud":    (cloud.collect,    cloud.render_text),
    "logs":     (logs.collect,     logs.render_text),
}


def _push_section_to_graylog(label: str, sec_obj, run_id: str) -> int:
    """Schickt alle numerischen / einfach serialisierbaren Felder einer
    Section als Metric-Messages an Graylog. Komplexe Felder (Listen mit
    Tupeln) werden auf Counts reduziert."""
    if not dataclasses.is_dataclass(sec_obj):
        return 0
    metrics: dict[str, float | int | str] = {}
    for fld in sec_obj.__dataclass_fields__:
        v = getattr(sec_obj, fld)
        if fld == "rows" and isinstance(v, list):
            for r in v:
                if hasattr(r, "name") and hasattr(r, "today"):
                    metrics[f"{r.name}_today"] = r.today
                    metrics[f"{r.name}_yesterday"] = r.yesterday
                    metrics[f"{r.name}_7d_avg"] = r.week_avg
                    metrics[f"{r.name}_status"] = r.status
        elif isinstance(v, (int, float, str, bool)):
            metrics[fld] = v
        elif isinstance(v, list):
            metrics[f"{fld}_count"] = len(v)
    return snapshot.send_metrics(metrics, section=label, run_id=run_id)


def _run_collector(label: str):
    fn, render_fn = COLLECTORS[label]
    t0 = time.monotonic()
    try:
        sec_obj = fn()
        elapsed = time.monotonic() - t0
        return label, sec_obj, render_fn, elapsed, None
    except Exception as e:
        elapsed = time.monotonic() - t0
        return label, None, render_fn, elapsed, e


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="IBF Morning Dashboard")
    p.add_argument("--section", help="nur diese eine Sektion ausführen")
    p.add_argument("--skip", help="Komma-Liste von Sektionen die übersprungen werden")
    p.add_argument("--html", metavar="FILE",
                   help="HTML-Output in diese Datei schreiben (sonst stdout ASCII)")
    p.add_argument("--no-snapshot", action="store_true",
                   help="kein GELF-Push am Ende")
    p.add_argument("--no-color", action="store_true",
                   help="ASCII-Output ohne ANSI-Farben")
    p.add_argument("--sequential", action="store_true",
                   help="Collectors sequenziell statt parallel laufen lassen")
    args = p.parse_args(argv)

    if args.section:
        labels = [args.section]
    else:
        labels = list(COLLECTORS.keys())
    if args.skip:
        skips = {s.strip() for s in args.skip.split(",") if s.strip()}
        labels = [l for l in labels if l not in skips]

    sections: dict = {}
    timings: list[tuple[str, float]] = []
    errors: list[tuple[str, Exception]] = []

    def _record(label, obj, render_fn, elapsed, err):
        if obj is not None:
            sections[label] = (obj, render_fn)
        else:
            err_msg = f"{type(err).__name__}: {err}" if err else "no data returned"
            sections[label] = (None, err_msg)
        timings.append((label, elapsed))
        if err:
            errors.append((label, err))

    if args.sequential or len(labels) == 1:
        for l in labels:
            _record(*_run_collector(l))
    else:
        with ThreadPoolExecutor(max_workers=len(labels)) as ex:
            futures = [ex.submit(_run_collector, l) for l in labels]
            for f in futures:
                _record(*f.result())

    # Output
    if args.html:
        Path(args.html).write_text(render.render_html(sections=sections),
                                   encoding="utf-8")
        print(f"[OK] HTML written: {args.html}", file=sys.stderr)
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
        out = render.render_ascii(sections=sections, color=not args.no_color)
        print(out)

    # Snapshot push
    if not args.no_snapshot:
        run_id = snapshot.make_run_id()
        pushed = 0
        for label, item in sections.items():
            if not item:
                continue
            sec_obj, _ = item
            if sec_obj is None:   # Fehler-Sektion -- nichts zum Pushen
                continue
            try:
                pushed += _push_section_to_graylog(label, sec_obj, run_id)
            except Exception as e:
                print(f"[WARN] snapshot push '{label}' failed: {e}",
                      file=sys.stderr)
        print(f"[OK] snapshot pushed: {pushed} metrics, run_id={run_id}",
              file=sys.stderr)

    # Diagnostics
    print("\n[timings] " + "  ".join(f"{l}={t:.1f}s" for l, t in timings),
          file=sys.stderr)
    if errors:
        for l, e in errors:
            print(f"[error]  {l}: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
