"""Dashboard-Rendering -- ASCII (Default) und HTML.

Nimmt die Section-Objekte der collectors entgegen und produziert einen
zusammengefassten Output-String.

ANSI-Farben für Status werden nur in ASCII benutzt; in HTML wird
CSS-Klassen-basiert gerendert.
"""

from __future__ import annotations

import datetime as dt
import html as _html
from dataclasses import is_dataclass

# ASCII / ANSI ----------------------------------------------------------------

_RESET = "\x1b[0m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_RED = "\x1b[31m"
_BOLD = "\x1b[1m"

_STATUS_COLOR = {
    "OK": _GREEN,
    "WARN": _YELLOW,
    "ALERT": _RED,
    "DEGRADED": _YELLOW,
    "SPLIT": _RED,
    "ERROR": _RED,
}


def colorize(status: str, *, color: bool = True) -> str:
    if not color:
        return status
    return f"{_STATUS_COLOR.get(status, '')}{status}{_RESET}"


def banner(text: str, *, color: bool = True) -> str:
    line = "=" * (len(text) + 4)
    if color:
        return f"{_BOLD}{text}{_RESET}\n{line}"
    return f"{text}\n{line}"


def render_ascii(*, sections: dict, color: bool = True) -> str:
    """Vollständiger Dashboard-ASCII-Output.

    `sections` ist ein dict; jeder Eintrag ist eines von:
      * `(sec_obj, render_fn)` -- erfolgreich gesammelt, wird gerendert
      * `(None, error_str)`    -- Fehler / Timeout, wird als Block mit
                                  Fehlermeldung gerendert (sichtbar!
                                  vorher silent gedroppt)
      * `None`                  -- gar nicht angefragt
    """
    parts: list[str] = []
    now_s = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    parts.append(banner(f"IBF Morning Dashboard -- {now_s}", color=color))
    parts.append("")

    for label, item in sections.items():
        if item is None:
            continue
        sec_obj, render_fn_or_err = item
        if sec_obj is None:
            err_str = render_fn_or_err if isinstance(render_fn_or_err, str) else "unknown error"
            parts.append(f"=== {label.upper()} -- [nicht verfügbar] ===")
            parts.append(f"  {err_str}")
            parts.append("")
            continue
        parts.append(render_fn_or_err(sec_obj))
        parts.append("")

    actionable = _collect_actionable(sections)
    if actionable:
        parts.append(banner("HEUTE BEACHTEN", color=color))
        for a in actionable:
            parts.append(f"  • {a}")

    text = "\n".join(parts)
    if not color:
        return text
    # Status-Token kolorieren
    for st in ("ALERT", "WARN", "DEGRADED", "SPLIT", "ERROR", "OK"):
        text = text.replace(f" {st}", f" {colorize(st, color=color)}")
    return text


def _collect_actionable(sections: dict) -> list[str]:
    """Sammelt 1-Zeilen-Hinweise zu allem was != OK ist."""
    out: list[str] = []
    for label, item in sections.items():
        if not item:
            continue
        sec_obj, render_fn_or_err = item
        if sec_obj is None:
            # Fehler-Sektion: in Aktion-Liste mit aufnehmen
            err = render_fn_or_err if isinstance(render_fn_or_err, str) else "?"
            out.append(f"[{label}] Sektion nicht verfügbar: {err}")
            continue
        # generisch durch dataclass-Felder gehen
        if not is_dataclass(sec_obj):
            continue
        for fld in sec_obj.__dataclass_fields__:
            v = getattr(sec_obj, fld)
            if fld == "rows":
                for r in v or []:
                    if getattr(r, "status", "OK") in ("ALERT", "WARN"):
                        out.append(f"[{label}] {r.name}={r.today:.0f} ({r.status})")
            elif fld == "must_run_missing" and v:
                for m in v:
                    out.append(f"[{label}] VM offline: {m}")
            elif fld == "missing" and v:
                for vmid, name in v:
                    out.append(f"[{label}] kein Backup: vmid={vmid} {name}")
            elif fld == "failed_24h" and v:
                for vmid, st in v:
                    out.append(f"[{label}] Backup fail: vmid={vmid} {st}")
            elif fld == "suspect_tunnels" and v:
                for t in v:
                    out.append(f"[{label}] IPSec-Tunnel verdächtig: {t}")
            elif fld == "wan_reachable" and v:
                for ip, ok in v:
                    if not ok:
                        out.append(f"[{label}] WAN-IP unreachable: {ip}")
            elif fld == "cluster_health" and v not in ("OK", "?"):
                out.append(f"[{label}] Cluster: {v}")
            elif fld == "rate_status" and v in ("ALERT", "WARN"):
                out.append(f"[{label}] Graylog-Rate: {v}")
            elif fld == "notifications_urgent" and isinstance(v, int) and v > 0:
                out.append(f"[{label}] Graylog Urgent-Notifications: {v}")
            elif fld == "servers_other" and v:
                for n, st in v:
                    out.append(f"[{label}] Server {n}: {st}")
    return out


# HTML -----------------------------------------------------------------------

_HTML_HEAD = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>IBF Morning Dashboard</title>
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 2em; max-width: 1100px; }
  h1 { font-size: 1.4em; margin-bottom: .2em; }
  h2 { margin-top: 1.5em; padding-bottom: 0.2em; border-bottom: 1px solid #ccc; font-size: 1.1em; }
  table { border-collapse: collapse; margin: .5em 0; }
  th, td { padding: 4px 10px; text-align: left; border-bottom: 1px solid #eee; font-variant-numeric: tabular-nums; }
  th { background: #f6f6f6; font-weight: 600; }
  td.num { text-align: right; }
  .status-OK     { color: #1a7f37; font-weight: 600; }
  .status-WARN   { color: #b87a00; font-weight: 600; }
  .status-ALERT  { color: #cc0000; font-weight: 700; }
  .status-DEGRADED { color: #b87a00; font-weight: 600; }
  .status-SPLIT  { color: #cc0000; font-weight: 700; }
  .delta-up      { color: #cc0000; }
  .delta-down    { color: #1a7f37; }
  ul.actions     { background: #fff7d6; padding: 1em 1em 1em 2em; border-left: 4px solid #b87a00; }
  small { color: #666; }
</style>
</head>
<body>"""

_HTML_FOOT = "</body></html>"


def _h(s) -> str:
    return _html.escape(str(s))


def _status_span(s: str) -> str:
    return f'<span class="status-{_h(s)}">{_h(s)}</span>'


def _delta_span(p) -> str:
    if p is None:
        return '<small>n/a</small>'
    cls = "delta-up" if p > 0 else "delta-down"
    return f'<span class="{cls}">{p:+.0f}%</span>'


def _render_security_html(sec) -> str:
    out = ['<h2>Security</h2>',
           '<table><tr><th>Metric</th><th class="num">Heute</th>'
           '<th class="num">Gestern bis jetzt</th><th class="num">7d-avg</th>'
           '<th>Status</th><th>Δ-yest</th><th>Δ-7d</th></tr>']
    for r in sec.rows:
        out.append(
            f'<tr><td>{_h(r.name)}</td>'
            f'<td class="num">{r.today:,.0f}</td>'
            f'<td class="num">{r.yesterday:,.0f}</td>'
            f'<td class="num">{r.week_avg:,.1f}</td>'
            f'<td>{_status_span(r.status)}</td>'
            f'<td>{_delta_span(r.delta_yesterday_pct)}</td>'
            f'<td>{_delta_span(r.delta_week_pct)}</td></tr>')
    out.append('</table>')
    if sec.top_srcips_today:
        out.append('<p><strong>Top Source-IPs (Brute-Force heute):</strong></p>')
        out.append('<table><tr><th>IP</th><th class="num">Count</th></tr>')
        for ip, n in sec.top_srcips_today[:10]:
            out.append(f'<tr><td>{_h(ip)}</td><td class="num">{n}</td></tr>')
        out.append('</table>')
    if sec.top_users_today:
        out.append('<p><strong>Top User-Versuche:</strong></p>')
        out.append('<table><tr><th>User</th><th class="num">Count</th></tr>')
        for u, n in sec.top_users_today[:10]:
            out.append(f'<tr><td>{_h(u)}</td><td class="num">{n}</td></tr>')
        out.append('</table>')
    return "\n".join(out)


def _render_infra_html(sec) -> str:
    out = ['<h2>Infrastruktur</h2>']
    out.append(f'<p>Cluster: {_status_span(sec.cluster_health)} '
               f'<small>({_h(sec.cluster_note)})</small></p>')
    out.append('<table><tr><th>Metric</th><th class="num">Heute</th>'
               '<th class="num">Gestern</th><th class="num">7d-avg</th><th>Status</th></tr>')
    for r in sec.rows:
        out.append(f'<tr><td>{_h(r.name)}</td><td class="num">{r.today:.0f}</td>'
                   f'<td class="num">{r.yesterday:.0f}</td>'
                   f'<td class="num">{r.week_avg:.1f}</td>'
                   f'<td>{_status_span(r.status)}</td></tr>')
    out.append('</table>')
    if sec.storage:
        out.append('<p><strong>Storage:</strong></p>')
        out.append('<table><tr><th>Pool</th><th>Node</th><th class="num">Used</th><th class="num">Size</th><th>Status</th></tr>')
        for s in sec.storage:
            out.append(f'<tr><td>{_h(s.name)}</td><td>{_h(s.node)}</td>'
                       f'<td class="num">{s.pct_used:.1f}% ({s.used_gb:.0f} GB)</td>'
                       f'<td class="num">{s.total_gb:.0f} GB</td>'
                       f'<td>{_status_span(s.status)}</td></tr>')
        out.append('</table>')
    if sec.must_run_missing:
        out.append('<p><strong>⚠ Production-VMs nicht laufend:</strong></p><ul>')
        for m in sec.must_run_missing:
            out.append(f'<li>{_h(m)}</li>')
        out.append('</ul>')
    if sec.surprise_running:
        out.append('<p><small>Laufend, aber nicht im Inventar:</small></p><ul>')
        for s in sec.surprise_running:
            out.append(f'<li><small>{_h(s)}</small></li>')
        out.append('</ul>')
    if sec.stale_snapshots:
        out.append(f'<p><small>Stale Snapshots: {len(sec.stale_snapshots)}</small></p>')
    return "\n".join(out)


def _render_backups_html(sec) -> str:
    out = ['<h2>Backups (letzte 24h)</h2>',
           f'<p>{_h(sec.summary)}</p>']
    if sec.failed_24h:
        out.append('<p><strong>⚠ Fehlgeschlagen:</strong></p><ul>')
        for vmid, st in sec.failed_24h:
            out.append(f'<li>vmid={vmid} status={_h(st)}</li>')
        out.append('</ul>')
    if sec.missing:
        out.append('<p><strong>⚠ Production-VMs ohne Backup:</strong></p><ul>')
        for vmid, name in sec.missing:
            out.append(f'<li>vmid={vmid} {_h(name)}</li>')
        out.append('</ul>')
    return "\n".join(out)


def _render_network_html(sec) -> str:
    out = [f'<h2>Network -- {_status_span(sec.overall_status)}</h2>']
    out.append('<table><tr><th>WAN-IP</th><th>:443</th></tr>')
    for ip, ok in sec.wan_reachable:
        sym = "✓" if ok else "✖"
        cls = "status-OK" if ok else "status-ALERT"
        out.append(f'<tr><td>{_h(ip)}</td>'
                   f'<td><span class="{cls}">{sym}</span></td></tr>')
    out.append('</table>')
    out.append('<table><tr><th>IPSec-Tunnel</th><th class="num">Phase-1-Errors heute</th></tr>')
    for t, n in sec.tunnel_phase1_errors_today.items():
        cls = "status-ALERT" if n > 100 else ""
        out.append(f'<tr><td>{_h(t)}</td>'
                   f'<td class="num"><span class="{cls}">{n}</span></td></tr>')
    out.append('</table>')
    return "\n".join(out)


def _render_cloud_html(sec) -> str:
    out = ['<h2>Cloud (Hetzner)</h2>']
    if not sec.available:
        out.append(f'<p><small>nicht verfügbar: {_h(sec.error)}</small></p>')
        return "\n".join(out)
    out.append(f'<p>Servers: <strong>{sec.servers_running}/{sec.servers_total}</strong> running '
               f'&middot; Volumes: <strong>{sec.volumes_attached}/{sec.volumes_total}</strong> attached</p>')
    if sec.servers_other:
        out.append('<p><strong>⚠ Nicht-running:</strong></p><ul>')
        for n, st in sec.servers_other:
            out.append(f'<li>{_h(n)}: {_h(st)}</li>')
        out.append('</ul>')
    return "\n".join(out)


def _render_logs_html(sec) -> str:
    dy = (f"{sec.delta_yesterday_pct:+.0f}%"
          if sec.delta_yesterday_pct is not None else "n/a")
    return f"""<h2>Logs (Graylog-Health)</h2>
<table>
  <tr><th>Indexer</th><td>{_status_span(sec.indexer.upper())}</td></tr>
  <tr><th>Notifications urgent / normal</th><td>{sec.notifications_urgent} / {sec.notifications_normal}</td></tr>
  <tr><th>Messages heute</th><td>{sec.msg_count_today:,}</td></tr>
  <tr><th>Messages gestern bis jetzt</th><td>{sec.msg_count_yesterday:,} ({dy})</td></tr>
  <tr><th>7d-avg/Tag</th><td>{sec.msg_count_7d_avg:,.0f}</td></tr>
  <tr><th>Status</th><td>{_status_span(sec.rate_status)}</td></tr>
</table>"""


_RENDERERS = {
    "security": _render_security_html,
    "infra": _render_infra_html,
    "backups": _render_backups_html,
    "network": _render_network_html,
    "cloud": _render_cloud_html,
    "logs": _render_logs_html,
}


def render_html(*, sections: dict) -> str:
    parts = [_HTML_HEAD]
    now_s = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    parts.append(f"<h1>IBF Morning Dashboard <small>{now_s}</small></h1>")

    actionable = _collect_actionable(sections)
    if actionable:
        parts.append("<ul class='actions'>")
        for a in actionable:
            parts.append(f"<li>{_h(a)}</li>")
        parts.append("</ul>")

    for label, item in sections.items():
        if not item:
            continue
        sec_obj, render_fn_or_err = item
        if sec_obj is None:
            err_str = render_fn_or_err if isinstance(render_fn_or_err, str) else "unknown error"
            parts.append(f'<h2>{_h(label).capitalize()} '
                         f'<span class="status-ALERT">[nicht verfügbar]</span></h2>')
            parts.append(f'<p><small>{_h(err_str)}</small></p>')
            continue
        renderer = _RENDERERS.get(label)
        if renderer is None:
            continue
        try:
            parts.append(renderer(sec_obj))
        except Exception as e:
            parts.append(f"<h2>{_h(label)}</h2><p><small>render error: {_h(e)}</small></p>")
    parts.append(_HTML_FOOT)
    return "\n".join(parts)
