"""GELF-Logger für den IBF-MCP-Server -- self-observability.

Sendet Lifecycle-, Auto-Detect-, Level-Change-, Tool-Call- und
Tool-Error-Events via GELF-UDP an `gld.ibf-solutions.com:12201`. App-Tag:
`ibf-mcp` (zur Abgrenzung von `app:ibf-dashboard` aus snapshot.py).

Spec + Doku: `claude_base/tools/claude/mcp-self-observability.md`

Auswertung in Graylog:
    app:ibf-mcp                                 -- alles
    app:ibf-mcp AND event_type:tool_call        -- nur Aufrufe
    app:ibf-mcp AND event_type:auto_detect      -- Client-Erkennungen
    app:ibf-mcp AND event_type:level_change     -- Konfig-Wechsel
    app:ibf-mcp AND mcp_session:"<iso-pid-hex>" -- alle Events einer Session

Konfiguration via ENV:
    IBF_MCP_LOG       on (default) | off
    IBF_MCP_LOG_ARGS  on (default) | off  -- Tool-Args mitloggen?
    IBF_MCP_LOG_HOST  Graylog-Host (default gld.ibf-solutions.com)
    IBF_MCP_LOG_PORT  GELF-UDP-Port (default 12201)
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
import socket
import sys
import time
from typing import Any

GRAYLOG_HOST = os.environ.get("IBF_MCP_LOG_HOST", "gld.ibf-solutions.com")
GRAYLOG_PORT = int(os.environ.get("IBF_MCP_LOG_PORT", "12201"))
APP_NAME = "ibf-mcp"

_LOG_ENABLED = os.environ.get("IBF_MCP_LOG", "on").strip().lower() not in (
    "off", "0", "false", "no",
)
_LOG_ARGS = os.environ.get("IBF_MCP_LOG_ARGS", "on").strip().lower() not in (
    "off", "0", "false", "no",
)

# Gemeinsame Session-ID für alle Events dieses Server-Lifetime.
# Format: ISO-Sekunde + PID + 4-Hex-Random -- garantiert eindeutig auch
# wenn ein Client mehrere MCP-Subprozesse parallel innerhalb derselben
# Sekunde startet (z.B. Open Code beobachtet 2026-05-06).
SESSION_ID = (
    f"{_dt.datetime.now().isoformat(timespec='seconds')}"
    f"-pid{os.getpid()}"
    f"-{secrets.token_hex(2)}"
)

# Felder/Args-Keys die immer redacted werden (auch wenn LOG_ARGS=on)
_REDACT_KEY_SUBSTRINGS = ("password", "token", "secret", "api_key", "apikey",
                          "auth", "credential")

# Tools deren Args grundsätzlich nie geloggt werden, auch nicht redacted
_NEVER_LOG_ARGS_TOOLS = {"authenticate"}

_HOST_NAME = socket.gethostname()

# Diagnose-Zähler (wird von ibf_status angezeigt)
_STATS = {
    "sent_ok": 0,
    "sent_fail": 0,
    "last_error": "",
    "last_event_ts": 0.0,
}


def is_enabled() -> bool:
    return _LOG_ENABLED


def get_stats() -> dict:
    """Diagnose-Stats für ibf_status."""
    return {
        "enabled": _LOG_ENABLED,
        "log_args": _LOG_ARGS,
        "target": f"{GRAYLOG_HOST}:{GRAYLOG_PORT}",
        "session_id": SESSION_ID,
        **_STATS,
    }


def _redact_args(args: dict | None) -> dict:
    if not args:
        return {}
    out: dict = {}
    for k, v in args.items():
        kl = k.lower()
        if any(s in kl for s in _REDACT_KEY_SUBSTRINGS):
            out[k] = "***"
        elif isinstance(v, str) and len(v) > 200:
            out[k] = v[:200] + "...[truncated]"
        else:
            out[k] = v
    return out


def _send(short_message: str, level: int = 6, **fields: Any) -> None:
    if not _LOG_ENABLED:
        return
    payload: dict = {
        "version": "1.1",
        "host": _HOST_NAME,
        "short_message": short_message,
        "timestamp": time.time(),
        "level": level,
        "_app": APP_NAME,
        # Field renamed from `_session_id` to `_mcp_session` (2026-05-06):
        # OpenSearch had auto-mapped `session_id` to type=date based on the
        # initial ISO-only format; after we extended the format with PID+hex
        # suffix, every event hit `mapper_parsing_exception` and was silently
        # dropped (12k+ failures). New name has no mapping baggage.
        "_mcp_session": SESSION_ID,
    }
    for k, v in fields.items():
        key = k if k.startswith("_") else f"_{k}"
        payload[key] = v
    try:
        data = json.dumps(payload, default=str).encode("utf-8")
        if len(data) > 8000:
            payload["short_message"] = payload["short_message"][:200] + "...[truncated]"
            # custom fields kürzen falls nötig
            for k in list(payload):
                if k.startswith("_") and isinstance(payload[k], str) and len(payload[k]) > 500:
                    payload[k] = payload[k][:500] + "...[truncated]"
            data = json.dumps(payload, default=str).encode("utf-8")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(data, (GRAYLOG_HOST, GRAYLOG_PORT))
        _STATS["sent_ok"] += 1
        _STATS["last_event_ts"] = time.time()
    except Exception as e:
        _STATS["sent_fail"] += 1
        _STATS["last_error"] = f"{type(e).__name__}: {e}"
        # Logger darf den MCP-Server NIE killen oder verzögern
        print(f"[ibf-mcp] WARN: GELF-log-send failed: {e}", file=sys.stderr)


def probe(test_id: str | None = None) -> dict:
    """Sendet zwei Test-Events (UDP + TCP) zur Diagnose und gibt
    Stats + aufgelöste Ziel-IP + Source-IP zurück."""
    tid_base = test_id or f"probe-{int(time.time())}"
    diag: dict = {"probe_test_id": tid_base}

    # DNS-Auflösung explizit
    try:
        diag["resolved_ip"] = socket.gethostbyname(GRAYLOG_HOST)
    except Exception as e:
        diag["resolved_ip"] = f"FAIL: {e}"

    # UDP-Send via reguläres log_lifecycle (zählt in _STATS)
    log_lifecycle("manual_probe", test_id=f"{tid_base}-udp",
                  source="logger.probe()-udp")

    # TCP-Probe direkt -- mit eigener Stat-Zählung
    tcp_payload = {
        "version": "1.1",
        "host": _HOST_NAME,
        "short_message": f"TCP-probe {tid_base}",
        "timestamp": time.time(),
        "level": 6,
        "_app": APP_NAME,
        "_mcp_session": SESSION_ID,
        "_event_type": "manual_probe",
        "_test_id": f"{tid_base}-tcp",
        "_source": "logger.probe()-tcp",
    }
    try:
        with socket.create_connection((GRAYLOG_HOST, GRAYLOG_PORT), timeout=3) as s:
            s.sendall(json.dumps(tcp_payload).encode("utf-8") + b"\x00")
            diag["tcp_send"] = "OK"
            try:
                diag["tcp_local_addr"] = "{}:{}".format(*s.getsockname())
            except Exception:
                pass
    except Exception as e:
        diag["tcp_send"] = f"FAIL: {type(e).__name__}: {e}"

    # UDP-Source-Probe -- welcher Source-Port + IP wird genutzt?
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((GRAYLOG_HOST, GRAYLOG_PORT))
            diag["udp_local_addr"] = "{}:{}".format(*s.getsockname())
    except Exception as e:
        diag["udp_local_addr"] = f"FAIL: {e}"

    return {**get_stats(), **diag}


def log_lifecycle(event: str, **context: Any) -> None:
    """Server-Lifecycle-Event: typisch 'startup'."""
    _send(f"lifecycle {event}", event_type="lifecycle",
          lifecycle_event=event, **context)


def log_auto_detect(client_name: str, client_version: str, profile_match: str,
                    changed: list[str]) -> None:
    """Auto-Client-Detection beim ersten Tool-Call (D14)."""
    _send(f"auto_detect client={client_name!r} v{client_version} profile={profile_match}",
          event_type="auto_detect",
          client_name=client_name,
          client_version=client_version,
          profile_match=profile_match,
          changed=(", ".join(changed) if changed else "(no changes)"))


def log_level_change(axis: str, old_value: Any, new_value: Any, source: str) -> None:
    """Doc-Level / Toolset / Readonly hat sich geändert."""
    _send(f"level_change {axis}: {old_value} -> {new_value} ({source})",
          event_type="level_change",
          axis=axis,
          old_value=str(old_value),
          new_value=str(new_value),
          source=source)


def log_tool_call(tool_name: str, args: dict | None = None,
                  duration_s: float | None = None) -> None:
    """Tool-Call abgeschlossen. Args redacted (wenn LOG_ARGS=on),
    `duration_s` als Float-Sekunden mit ms-Auflösung (3 Nachkommastellen
    in der short_message, voller Float im GELF-Feld)."""
    extra: dict = {"event_type": "tool_call", "tool_name": tool_name}
    if duration_s is not None:
        extra["duration_s"] = round(float(duration_s), 3)
    if not _LOG_ARGS or tool_name in _NEVER_LOG_ARGS_TOOLS or not args:
        msg = f"tool_call {tool_name}"
        if duration_s is not None:
            msg += f" ({extra['duration_s']}s)"
        _send(msg, **extra)
        return
    redacted = _redact_args(args)
    args_str = json.dumps(redacted, default=str)
    msg = f"tool_call {tool_name}"
    if duration_s is not None:
        msg += f" ({extra['duration_s']}s)"
    _send(f"{msg} args={args_str}", tool_args=args_str, **extra)


def log_tool_error(tool_name: str, exc: BaseException,
                   duration_s: float | None = None) -> None:
    """Exception in einem Tool-Call."""
    extra: dict = {
        "event_type": "tool_error",
        "tool_name": tool_name,
        "error_type": type(exc).__name__,
        "error_message": str(exc)[:500],
    }
    if duration_s is not None:
        extra["duration_s"] = round(float(duration_s), 3)
    _send(f"tool_error {tool_name}: {type(exc).__name__}: {exc}",
          level=3, **extra)
