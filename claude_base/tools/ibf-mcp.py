#!/usr/bin/env python3
"""IBF combined MCP server -- Proxmox + Graylog in einem Prozess.

Spart ~1 Sekunde Startup gegenüber zwei getrennten MCPs (FastMCP-Lib wird nur
einmal geladen). Auth bleibt per Domain getrennt (proxmox / graylog).

Tool-Naming: proxmox_<name> bzw. graylog_<name>.

Start:        python ibf-mcp.py
Registrieren: python ibf-mcp.py --install
"""
import base64
import datetime as dt
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from ibf_mcp_auth import Auth
import mcp_logger as _logger

# ---------------------------------------------------------------------------
# Auth (zwei Instanzen -- pro Domain getrennt)
# ---------------------------------------------------------------------------

_auth_proxmox   = Auth("proxmox")
_auth_graylog   = Auth("graylog")
_auth_fortigate = Auth("fortigate")
# Mikrotik wurde 2026-05-05 in eigenständigen MCP-Server ausgegliedert:
# C:\Temp\claude\ibf\mikrotik\tools\mikrotik-mcp.py

# ---------------------------------------------------------------------------
# Proxmox config + token
# ---------------------------------------------------------------------------

PROXMOX_BASE = "https://192.168.10.1:8006/api2/json"
KNOWN_NODES  = ["k1-low", "k2", "k5"]
NODE_IPS     = {"k1-low": "192.168.10.1", "k2": "192.168.10.2", "k5": "192.168.10.5"}
SSH_KEY      = os.path.expandvars(r"%USERPROFILE%\.ssh\proxmox_claude")

_CTX_CONFIG = {
    "ibf":      ("proxmox-ibf",      r"\ibf"),
    "personal": ("proxmox-personal", r"\personal"),
}


_IBF_NETWORKS = ("10.10.40.0/21",)  # IBF-Firmennetz, deckt 10.10.40.x .. 10.10.47.x
                                    # (DNS-Suffix int.ibf-solutions.com)

def _detect_context() -> str:
    """Lokale Interfaces durchsuchen -- kein DNS/UDP.
    IBF: jede Adresse innerhalb _IBF_NETWORKS."""
    try:
        import socket
        from ipaddress import ip_address, ip_network
        nets = [ip_network(n) for n in _IBF_NETWORKS]
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            try:
                if any(ip_address(ip) in n for n in nets):
                    return "ibf"
            except ValueError:
                continue
        return "personal"
    except Exception:
        return "personal"


def _load_proxmox_token() -> str:
    token = os.environ.get("PROXMOX_TOKEN", "").strip()
    if token:
        return token
    try:
        import keyring
        ctx = _detect_context()
        service, _ = _CTX_CONFIG[ctx]
        token = keyring.get_password(service, "ibf")
        if token:
            return token.strip()
    except Exception:
        pass
    for env_path in [Path(__file__).parent / ".env", Path(r"C:\Temp\claude\.env")]:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("proxmox_token="):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("Kein Proxmox-Token gefunden.")


_PROXMOX_TOKEN_CACHE = None
def _get_proxmox_token() -> str:
    global _PROXMOX_TOKEN_CACHE
    if _PROXMOX_TOKEN_CACHE is None:
        _PROXMOX_TOKEN_CACHE = _load_proxmox_token()
    return _PROXMOX_TOKEN_CACHE


# ---------------------------------------------------------------------------
# Graylog config + token
# ---------------------------------------------------------------------------

GRAYLOG_BASE = "https://gld.ibf-solutions.com/api"


# ---------------------------------------------------------------------------
# FortiGate config (audit/audit, read-only)
# ---------------------------------------------------------------------------

FORTI_HOST = "10.10.40.1"
FORTI_PORT = 10022
FORTI_USER = "audit"
FORTI_PASS = "audit"  # read-only audit account; FortiGate-spezifisch, kein Geheimnis


# Mikrotik-Code: ausgelagert nach C:\Temp\claude\ibf\mikrotik\tools\mikrotik-mcp.py


def _load_graylog_token() -> str:
    token = os.environ.get("GRAYLOG_IBF", "").strip()
    if token:
        return token
    for env_path in [
        Path(__file__).parents[2] / ".env",
        Path(__file__).parents[1] / ".env",
        Path(r"C:\Temp\claude\.env"),
    ]:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("graylog_ibf="):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("Kein Graylog-Token gefunden.")


_GRAYLOG_TOKEN_CACHE = None
def _get_graylog_token() -> str:
    global _GRAYLOG_TOKEN_CACHE
    if _GRAYLOG_TOKEN_CACHE is None:
        _GRAYLOG_TOKEN_CACHE = _load_graylog_token()
    return _GRAYLOG_TOKEN_CACHE


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# 30s TTL Cache für ausgewählte Read-Endpunkte (cluster-weite Snapshots).
# Live-Daten wie tasks/snapshots werden NICHT gecached.
_CACHE_TTL = 30
_CACHEABLE_PATHS = {"/cluster/resources", "/cluster/status"}
_CACHE: dict = {}  # path -> (data, expiry_ts)


def _cache_get(path: str):
    import time
    entry = _CACHE.get(path)
    if entry and time.time() < entry[1]:
        return entry[0]
    return None


def _cache_put(path: str, data):
    import time
    _CACHE[path] = (data, time.time() + _CACHE_TTL)


def _cache_clear():
    _CACHE.clear()


def px(path: str, params=None, method="GET", body=None, use_cache: bool = True):
    """Proxmox API call. Cached für GET auf /cluster/resources und /cluster/status (TTL 30s).
    body kann auch list-Werte enthalten (doseq=True für Wiederholungs-Parameter wie command=)."""
    # Cache nur für GETs auf cacheable Paths ohne Query-Parameter
    cache_eligible = (
        use_cache and method == "GET" and not params and path in _CACHEABLE_PATHS
    )
    if cache_eligible:
        cached = _cache_get(path)
        if cached is not None:
            return cached

    url = PROXMOX_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    encoded = urllib.parse.urlencode(body, doseq=True).encode() if body is not None else None
    req = urllib.request.Request(url, data=encoded, method=method)
    req.add_header("Authorization", f"PVEAPIToken={_get_proxmox_token()}")
    req.add_header("Accept", "application/json")
    if encoded:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as r:
            txt = r.read().decode()
            if not txt:
                # Auch bei leerer Antwort: state-ändernde Methoden invalidieren Cache
                if method != "GET":
                    _cache_clear()
                return None
            data = json.loads(txt)
            result = data.get("data", data)
            if cache_eligible:
                _cache_put(path, result)
            elif method != "GET":
                # State-ändernde Aufrufe (POST/PUT/DELETE): Cache leeren,
                # damit cluster_status / list_vms direkt frische Daten sehen
                _cache_clear()
            return result
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {method} {path}: {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Proxmox nicht erreichbar: {e.reason}")


def gl(path: str, params: dict = None, method: str = "GET", body: dict = None):
    """Graylog API call."""
    url = GRAYLOG_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    data = json.dumps(body).encode() if body else None
    auth = base64.b64encode(f"{_get_graylog_token()}:token".encode()).decode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Accept", "application/json")
    req.add_header("X-Requested-By", "ibf-mcp")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {method} {path}: {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Graylog nicht erreichbar: {e.reason}")


# ---------------------------------------------------------------------------
# SSH helper (Proxmox)
# ---------------------------------------------------------------------------

def _forti_connect():
    """SSH-Connection zur FortiGate. Caller muss client.close() machen."""
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        FORTI_HOST, port=FORTI_PORT, username=FORTI_USER, password=FORTI_PASS,
        timeout=15, banner_timeout=15, look_for_keys=False, allow_agent=False,
    )
    return client


def _forti_run(cmd: str, timeout: int = 30) -> tuple:
    """Einzelnen Befehl auf der FortiGate ausführen. Returns (success, output)."""
    try:
        client = _forti_connect()
    except Exception as e:
        return False, f"connect: {e}"
    try:
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="ignore")
        err = stderr.read().decode("utf-8", errors="ignore")
        if err and not out:
            return False, err
        return True, out
    except Exception as e:
        return False, str(e)
    finally:
        try: client.close()
        except Exception: pass


def _forti_run_shell(commands: list, timeout: int = 60) -> tuple:
    """Mehrere Befehle in einem Shell-Session ausführen (für FortiGate-Sequenzen
    mit `config global`, Filter-Setup, dann Anzeige). Returns (success, output).
    """
    import time
    try:
        client = _forti_connect()
    except Exception as e:
        return False, f"connect: {e}"
    try:
        shell = client.invoke_shell()
        time.sleep(0.5)
        # Banner abfangen
        while shell.recv_ready():
            shell.recv(8192)

        output_parts = []
        for cmd in commands:
            shell.send(cmd + "\n")
            time.sleep(0.4)
            # Output sammeln bis kein neuer kommt (max timeout/len(commands) pro cmd)
            per_cmd_deadline = time.time() + (timeout / max(len(commands), 1))
            buf = ""
            while time.time() < per_cmd_deadline:
                if shell.recv_ready():
                    chunk = shell.recv(16384).decode("utf-8", errors="ignore")
                    buf += chunk
                else:
                    time.sleep(0.2)
                    if not shell.recv_ready():
                        # nochmal kurz prüfen
                        time.sleep(0.3)
                        if not shell.recv_ready():
                            break
            output_parts.append(buf)

        return True, "\n".join(output_parts)
    except Exception as e:
        return False, str(e)
    finally:
        try: client.close()
        except Exception: pass


def _ssh_run_node(ip: str, cmd: str, timeout: int = 120) -> str:
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(ip, port=22, username="root", key_filename=SSH_KEY,
                   look_for_keys=False, allow_agent=False, timeout=15, banner_timeout=15)
    try:
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        return out + (f"\n[STDERR] {err}" if err.strip() else "")
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Formatting helpers (shared)
# ---------------------------------------------------------------------------

def _fmt_bytes(b):
    if b is None:
        return "--"
    b = float(b)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def _fmt_uptime(secs):
    if not secs:
        return "--"
    secs = int(secs)
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    return f"{secs // 86400}d {(secs % 86400) // 3600}h"


def _fmt_ts(ts):
    if not ts:
        return "--"
    return dt.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_ts_ms(ts_ms):
    if not ts_ms:
        return "--"
    return dt.datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def _parse_range(last: str) -> int:
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    last = last.strip().lower()
    if last[-1] in units:
        try:
            return int(last[:-1]) * units[last[-1]]
        except ValueError:
            pass
    return int(last)


def _parse_when(s: str, *, end: bool = False) -> "dt.datetime | None":
    """Parst eine Zeitangabe in einen absoluten Timestamp.

    Akzeptiert: 'today', 'yesterday', 'now', '1h'/'30m'/'2d' (relativ ab jetzt),
    'YYYY-MM-DD', 'YYYY-MM-DD HH:MM[:SS]'. Bei `end=True` werden ungenaue
    Tagesangaben auf das Tagesende (23:59:59) gesetzt, sonst auf 00:00:00.
    Leerer String -> None (kein Filter).
    """
    if not s or not s.strip():
        return None
    s = s.strip().lower()
    now = dt.datetime.now()
    if s == "now":
        return now
    if s == "today":
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return base.replace(hour=23, minute=59, second=59) if end else base
    if s == "yesterday":
        base = (now - dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return base.replace(hour=23, minute=59, second=59) if end else base
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if len(s) >= 2 and s[-1] in units and s[:-1].isdigit():
        return now - dt.timedelta(seconds=int(s[:-1]) * units[s[-1]])
    for fmt, is_date_only in (
        ("%Y-%m-%d %H:%M:%S", False),
        ("%Y-%m-%d %H:%M", False),
        ("%Y-%m-%d", True),
    ):
        try:
            d = dt.datetime.strptime(s, fmt)
            if is_date_only and end:
                d = d.replace(hour=23, minute=59, second=59)
            return d
        except ValueError:
            continue
    raise ValueError(f"Konnte Zeitangabe nicht parsen: {s!r}")


def _forti_filter_log_by_time(raw: str, t_since, t_until, max_count: int) -> str:
    """Aus FortiGate-Log-Output (eine Zeile pro Eintrag mit `<n>: date=... time=...`)
    nur die Einträge im Zeitfenster behalten. Header/Footer (z.B. 'X logs found',
    Prompt) werden durchgereicht.
    """
    import re
    pat = re.compile(r"^\s*\d+:\s+date=(\d{4}-\d{2}-\d{2})\s+time=(\d{2}:\d{2}:\d{2})")
    head, kept, foot = [], [], []
    seen_first = False
    after_last = False
    for line in raw.split("\n"):
        m = pat.match(line)
        if m:
            seen_first = True
            ts = dt.datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
            if t_since and ts < t_since:
                continue
            if t_until and ts > t_until:
                continue
            kept.append(line)
            if len(kept) >= max_count:
                after_last = True
        elif not seen_first:
            head.append(line)
        elif after_last:
            foot.append(line)
    out = "\n".join(head + kept + foot)
    if not kept:
        rng = f"{t_since or '*'} – {t_until or '*'}"
        out += f"\n[INFO] Keine Einträge im Zeitfenster {rng}."
    return out


def _wait_task(node: str, upid: str, timeout: int = 600) -> str:
    import time
    encoded = urllib.parse.quote(upid, safe="")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = px(f"/nodes/{node}/tasks/{encoded}/status")
            if isinstance(s, dict) and s.get("status") == "stopped":
                return s.get("exitstatus", "OK")
        except Exception:
            break
        time.sleep(3)
    return "timeout"


# Bash-Script für Online-Filesystem-Extension. Wird via QEMU Guest Agent ausgeführt.
# Erkennt Standard-Debian-Layouts: direkte Partition (ext2/3/4, xfs, btrfs) ODER LVM-Root.
_FS_EXTEND_SCRIPT = r"""
set -e
ROOT_FS=$(findmnt -no SOURCE /)
[ -z "$ROOT_FS" ] && { echo "ERR: no root mount"; exit 2; }
echo "ROOT=$ROOT_FS"

extend_part() {
  local part=$1
  local disk=$(lsblk -no PKNAME "$part" 2>/dev/null)
  local pnum=$(echo "$part" | grep -oE '[0-9]+$')
  [ -z "$disk" ] || [ -z "$pnum" ] && return 0
  if command -v parted >/dev/null 2>&1; then
    # DOS-Layout: bei logischer Partition (>=5) muss erst der enclosing extended-Container wachsen.
    local ext_num
    ext_num=$(parted -s "/dev/$disk" print 2>/dev/null | awk '/extended/ {print $1; exit}')
    if [ -n "$ext_num" ] && [ "$ext_num" != "$pnum" ]; then
      parted -s --fix "/dev/$disk" resizepart "$ext_num" 100% 2>&1 | head -3 || true
    fi
    parted -s --fix "/dev/$disk" resizepart "$pnum" 100% 2>&1 | head -3 || true
    command -v partprobe >/dev/null && partprobe "/dev/$disk" 2>/dev/null \
      || command -v partx >/dev/null && partx -u "/dev/$disk" 2>/dev/null \
      || true
  elif command -v growpart >/dev/null 2>&1; then
    growpart "/dev/$disk" "$pnum" 2>&1 | head -2 || true
  else
    echo "ERR: weder parted noch growpart vorhanden (apt install parted)"
    return 1
  fi
}

extend_fs() {
  local fs=$1
  local t=$(blkid -o value -s TYPE "$fs" 2>/dev/null)
  case "$t" in
    ext2|ext3|ext4) resize2fs "$fs" ;;
    xfs) xfs_growfs / ;;
    btrfs) btrfs filesystem resize max / ;;
    *) echo "ERR: unknown fs '$t' on $fs"; return 1 ;;
  esac
}

# Direkte Partition
if [ -b "$ROOT_FS" ] && [[ "$ROOT_FS" =~ ^/dev/(sd|vd|xvd|nvme) ]]; then
  extend_part "$ROOT_FS"
  extend_fs "$ROOT_FS"
  echo "OK: direct partition $ROOT_FS extended"
  exit 0
fi

# LVM
if [[ "$ROOT_FS" == /dev/mapper/* ]] || [[ "$ROOT_FS" == /dev/dm-* ]]; then
  PV=$(pvs --noheadings -o pv_name 2>/dev/null | head -1 | xargs)
  [ -z "$PV" ] && { echo "ERR: no PV"; exit 2; }
  extend_part "$PV"
  pvresize "$PV"
  lvextend -l +100%FREE "$ROOT_FS"
  extend_fs "$ROOT_FS"
  echo "OK: LVM root $ROOT_FS extended (PV=$PV)"
  exit 0
fi

echo "UNKNOWN_LAYOUT: $ROOT_FS"
exit 2
"""


_MAINTENANCE_FILE = Path(os.path.expandvars(r"%USERPROFILE%")) / ".ibf_mcp_maintenance.json"


def _read_maintenance() -> dict:
    if not _MAINTENANCE_FILE.exists():
        return {}
    try:
        return json.loads(_MAINTENANCE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_maintenance(state: dict) -> None:
    _MAINTENANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _MAINTENANCE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _qemu_guest_wait_agent(node: str, vmid: int, timeout: int = 120) -> bool:
    """Wartet bis der QEMU Guest Agent in der VM erreichbar ist (z.B. nach Start).
    Returns True wenn ready, False bei Timeout.
    """
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            px(f"/nodes/{node}/qemu/{vmid}/agent/ping",
               method="POST", body={}, use_cache=False)
            return True
        except RuntimeError:
            pass
        time.sleep(2)
    return False


def _qemu_guest_exec(node: str, vmid: int, command_array: list, timeout: int = 90) -> tuple:
    """Führt Befehl im Gast über QEMU Guest Agent aus.
    Returns: (exitcode, stdout, stderr) oder (None, '', err) bei Fehler.
    """
    import time
    try:
        body = {"command": command_array}
        resp = px(f"/nodes/{node}/qemu/{vmid}/agent/exec", method="POST", body=body)
    except RuntimeError as e:
        return (None, "", f"Guest-Agent-Aufruf fehlgeschlagen: {e}")

    pid = resp.get("pid") if isinstance(resp, dict) else None
    if not pid:
        return (None, "", f"Kein PID erhalten: {resp}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        s = px(f"/nodes/{node}/qemu/{vmid}/agent/exec-status",
               params={"pid": pid}, use_cache=False)
        if isinstance(s, dict) and s.get("exited"):
            return (
                s.get("exitcode"),
                s.get("out-data", "") or "",
                s.get("err-data", "") or "",
            )
        time.sleep(1)
    return (None, "", "timeout beim Warten auf Guest-Agent")


def _parse_vm_disks(cfg: dict) -> dict:
    """Disk-Einträge der VM-Config parsen.
    Returns: {key: {'raw': '<config-string>', 'size_gb': float, 'storage': '<pool>'}}
    """
    disks = {}
    DISK_PREFIXES = ("scsi", "sata", "ide", "virtio", "rootfs", "mp",
                     "efidisk", "tpmstate", "unused")
    UNITS = {"K": 1/(1024*1024), "M": 1/1024, "G": 1.0, "T": 1024.0, "P": 1024*1024}
    for key, val in (cfg or {}).items():
        if not isinstance(val, str) or "size=" not in val:
            continue
        if not key.startswith(DISK_PREFIXES):
            continue
        size_gb = 0.0
        storage = ""
        # erste Komponente ist üblicherweise <storage>:<volume-id>
        first = val.split(",", 1)[0]
        if ":" in first:
            storage = first.split(":", 1)[0]
        for part in val.split(","):
            part = part.strip()
            if part.startswith("size="):
                s = part[5:]
                if s and s[-1].upper() in UNITS:
                    try:
                        size_gb = float(s[:-1]) * UNITS[s[-1].upper()]
                    except ValueError:
                        pass
                else:
                    try:
                        size_gb = float(s) / 1024 / 1024 / 1024
                    except ValueError:
                        pass
                break
        disks[key] = {"raw": val, "size_gb": size_gb, "storage": storage}
    return disks


def _parse_relative(value: str, current: float) -> float:
    """Parse '+2', '-1.5', '22' (absolute) oder '22.5' relativ zu current.
    + oder - am Anfang gefolgt von Ziffer = relativ.
    """
    s = str(value).strip().replace(",", ".")
    if len(s) >= 2 and s[0] in "+-" and (s[1].isdigit() or s[1] == "."):
        if s.startswith("+"):
            return current + float(s[1:])
        return current - float(s[1:])
    return float(s)


def _extract_flag(value: str, flag: str) -> tuple:
    """Sucht in value nach '+<flag>' und entfernt es. Returns (cleaned_value, found_bool).
    Erkennt z.B. '+10+fs', '+10 +fs', '+10,+fs'.
    """
    import re as _re
    if not value:
        return value, False
    pattern = rf"[\s,]*\+{_re.escape(flag)}\b"
    if _re.search(pattern, value, _re.IGNORECASE):
        return _re.sub(pattern, "", value, flags=_re.IGNORECASE).strip(), True
    return value, False


def _resolve_vm(vmid_or_name: str):
    """Akzeptiert VMID (numerisch) oder Substring auf Name (case-insensitive).
    Bei mehrdeutigem Namen Fehler mit Treffer-Liste.
    Returns: (vmid, node, vtype, name, status)
    """
    resources = px("/cluster/resources")
    s = str(vmid_or_name).strip()

    # exakte VMID-Übereinstimmung zuerst
    vm = next((r for r in resources
               if r.get("type") in ("qemu", "lxc") and str(r.get("vmid")) == s), None)
    if vm:
        return (vm["vmid"], vm["node"],
                "qemu" if vm["type"] == "qemu" else "lxc",
                vm.get("name", "?"), vm.get("status", "?"))

    # sonst Namens-Suche (Substring, case-insensitive)
    needle = s.lower()
    matches = [r for r in resources
               if r.get("type") in ("qemu", "lxc")
               and needle in (r.get("name") or "").lower()]
    if not matches:
        raise RuntimeError(f"Keine VM mit ID/Name '{vmid_or_name}' gefunden")
    if len(matches) > 1:
        names = ", ".join(f"{m['vmid']}={m.get('name','?')}" for m in matches)
        raise RuntimeError(f"Mehrdeutiger Name '{vmid_or_name}' — Treffer: {names}")
    vm = matches[0]
    return (vm["vmid"], vm["node"],
            "qemu" if vm["type"] == "qemu" else "lxc",
            vm.get("name", "?"), vm.get("status", "?"))


# ---------------------------------------------------------------------------
# Doc-Levels (Token-Reduktion via konfigurierbare Tool-Beschreibungen)
# ---------------------------------------------------------------------------
# Spec: claude_base/tools/claude/mcp-doc-levels.md
# Hierarchie: ENV IBF_MCP_DOC_LEVEL → Datei → Hartcoded "min".

import tempfile as _tempfile

_DOC_LEVEL_FILE = Path(_tempfile.gettempdir()) / "ibf_mcp_doc_level_default"
_DOC_LEVELS_VALID = ("full", "compact", "min")

# Mapping pro Tool: name -> dict(full=, compact=, minimal=). Wird beim
# Registrieren via _doc(...) gefüllt. Nötig für späteren Live-Wechsel.
_TOOL_DESCRIPTIONS: dict[str, dict[str, str]] = {}


def _resolve_doc_level() -> tuple[str, str]:
    """Returns (level, source). source ∈ {"env", "file", "default"}."""
    env_val = os.environ.get("IBF_MCP_DOC_LEVEL", "").strip().lower()
    if env_val in _DOC_LEVELS_VALID:
        return env_val, "env"
    if _DOC_LEVEL_FILE.exists():
        try:
            file_val = _DOC_LEVEL_FILE.read_text(encoding="utf-8").strip().lower()
            if file_val in _DOC_LEVELS_VALID:
                return file_val, "file"
        except Exception as e:
            print(f"[WARN] Doc-Level-File nicht lesbar: {e}", file=sys.stderr)
    return "min", "default"


DOC_LEVEL, _DOC_LEVEL_SOURCE = _resolve_doc_level()


def _doc(full: str, compact: str | None = None, minimal: str | None = None,
         _key: str | None = None) -> str:
    """Wählt Beschreibung anhand DOC_LEVEL.
    Optionaler `_key`: Tool-Name; wenn gesetzt, werden alle drei Varianten
    in `_TOOL_DESCRIPTIONS` registriert (für späteren Live-Wechsel).
    """
    minimal_eff = minimal or (compact.split("\n")[0] if compact else None) or full.split("\n")[0]
    compact_eff = compact or full
    if _key is not None:
        _TOOL_DESCRIPTIONS[_key] = {
            "full": full, "compact": compact_eff, "min": minimal_eff,
        }
    if DOC_LEVEL == "min":
        return minimal_eff
    if DOC_LEVEL == "compact":
        return compact_eff
    return full


# Server-Instructions: pro Doc-Level eigene Variante (das ist sehr token-
# intensiv weil bei jedem Request mit übertragen).
_INSTRUCTIONS_FULL = (
    "IBF Combined MCP -- Proxmox VE Cluster + Graylog Logs. "
    "WICHTIG: Prüfe bei JEDER Anfrage zuerst ob die jeweilige Domain authentifiziert ist. "
    "Erhältst du '__UNAVAILABLE__' zurück, ist diese Domain nicht authentifiziert -- "
    "verhalte dich dann so als gäbe es die zugehörigen Tools nicht. Frage den Nutzer nach "
    "dem Passwort und rufe `authenticate` auf. "
    "Vor jedem `proxmox_ssh_run`, `proxmox_ssh_apt_upgrade`, `proxmox_vm_run`, "
    "`proxmox_vm_apt`, `proxmox_vm_install_recipe`, `fortigate_run` oder `mikrotik_run` "
    "MUSST du den Nutzer explizit fragen "
    "'Darf ich folgenden Befehl auf [ziel] ausführen: [cmd]?' und erst nach Bestätigung ausführen. "
    "DESTRUKTIVE OPERATIONEN -- vor Ausführung IMMER explizit den Plan beschreiben und "
    "Rückfrage stellen ('soll ich das wirklich machen?'). Nicht nur 'ich werde X tun', sondern "
    "klare Frage UND Wartung auf Antwort. Betrifft: "
    "(a) `proxmox_vm_delete` -- löscht VM dauerhaft. "
    "(b) `proxmox_vm_change_id` mit `delete_original=True` oder Suffix `+delete`. "
    "(c) `proxmox_vm_delete_snapshot` mit `snapname='all'`. "
    "(d) `proxmox_evacuate_node` und `proxmox_restore_by_label` mit `dry_run=False` oder Suffix `+confirm`. "
    "(e) `proxmox_vm_power` mit `op='stop'` (Hart-Stop, nicht shutdown). "
    "(f) `proxmox_maintenance disable_all`. "
    "Bei Mehrfach-Operationen (Compound z.B. 'vm 100 +5gb+fs +reboot'): jede destruktive "
    "Stufe einzeln bestätigen lassen, nicht sammelweise."
)
_INSTRUCTIONS_COMPACT = (
    "IBF MCP (Proxmox + Graylog + FortiGate + Dashboard). "
    "Bei '__UNAVAILABLE__': Domain unauth, fragen+authenticate. "
    "Vor SSH/CLI/destruktivem Tool ('proxmox_*_run', '*_apt', 'vm_delete', "
    "'vm_change_id+delete', 'evacuate_node', 'vm_power op=stop', 'disable_all'): "
    "explizit Plan beschreiben + auf Bestätigung warten."
)
_INSTRUCTIONS_MIN = (
    "IBF MCP. Bei '__UNAVAILABLE__': authenticate. "
    "Destruktive/SSH/CLI-Tools: erst Bestätigung holen."
)


# ---------------------------------------------------------------------------
# Toolset + Readonly (Capability-Filter, Phase 2)
# ---------------------------------------------------------------------------
# Spec: claude_base/tools/claude/mcp-doc-levels.md §3
# IBF_MCP_TOOLSET=min|compact|full   ← Größe (welche Tools registriert werden)
# IBF_MCP_READONLY=1                  ← Capability (write-Tools blockieren)

# Kuratierte Whitelist für die typischsten Read-only-Sessions.
# Bewusst klein -- nur das was zum Status-Check und Log-Forensik nötig ist.
_TOOLSET_MIN = {
    "authenticate", "ibf_help", "ibf_status", "ibf_logs", "ibf_log_probe",
    "ibf_set_doc_level", "ibf_get_doc_level", "ibf_reload_tools",
    "ibf_set_toolset", "ibf_set_readonly",
    "dashboard_morning", "dashboard_section", "dashboard_history",
    "proxmox_cluster_status", "proxmox_list_vms", "proxmox_list_tasks",
    "fortigate_status",
    "graylog_count_messages", "graylog_top_values",
}
# Compact: alle Read-Tools, aber keine SSH/Run-Helpers
_TOOLSET_COMPACT = _TOOLSET_MIN | {
    "proxmox_get_task_log", "proxmox_list_storage", "proxmox_list_snapshots",
    "proxmox_get_vm_config",
    "fortigate_list_interfaces", "fortigate_list_policies",
    "fortigate_list_sessions", "fortigate_show_log",
    "graylog_search_messages", "graylog_system_status",
    "graylog_list_streams", "graylog_indexer_health",
}
TOOLSETS: dict[str, set[str] | None] = {
    "min":     _TOOLSET_MIN,
    "compact": _TOOLSET_COMPACT,
    "full":    None,   # None = alle Tools
}

_env_toolset = os.environ.get("IBF_MCP_TOOLSET", "").strip().lower()
if _env_toolset and _env_toolset in TOOLSETS:
    _TOOLSET_NAME = _env_toolset
    _TOOLSET_SOURCE = "env"
elif _env_toolset:
    print(f"[ibf-mcp] WARN: unbekannter IBF_MCP_TOOLSET={_env_toolset!r}, "
          f"falle zurück auf 'full'", file=sys.stderr)
    _TOOLSET_NAME = "full"
    _TOOLSET_SOURCE = "default(invalid env)"
else:
    _TOOLSET_NAME = "full"
    _TOOLSET_SOURCE = "default"
ACTIVE_TOOLSET: set[str] | None = TOOLSETS[_TOOLSET_NAME]

_env_readonly = os.environ.get("IBF_MCP_READONLY", "").strip().lower()
if _env_readonly:
    READONLY_MODE: bool = _env_readonly in ("1", "true", "yes", "on")
    _READONLY_SOURCE = "env"
else:
    READONLY_MODE = False
    _READONLY_SOURCE = "default"

_SKIPPED_TOOLS: list[tuple[str, str]] = []  # [(name, reason)] für Diagnose
# Vollregister aller Tool-Funktionen + Metadata, gefüllt vom tool()-Wrapper
# beim Server-Start. Wird für Live-Tool-(De)Registrierung gebraucht
# (`ibf_set_toolset`, `ibf_set_readonly`, Auto-Client-Detect).
_ALL_TOOLS: dict[str, dict] = {}

# Auto-Client-Detect: Mapping client-name-substring -> (doc, toolset, readonly).
# Wird beim ersten Tool-Call ausgewertet, ohne explizite ENV-Overrides.
_CLIENT_PROFILES: dict[str, tuple[str, str, bool]] = {
    "claude code":  ("compact", "full", False),
    "claude-code":  ("compact", "full", False),
    "claude.ai":    ("compact", "full", False),
    "open code":    ("min", "min", False),
    "opencode":     ("min", "min", False),
}
_AUTO_DETECTED_CLIENT = False
_DETECTED_CLIENT_NAME: str = ""           # gefüllt nach erstem Tool-Call
_DETECTED_CLIENT_VERSION: str = ""
_DETECTED_PROFILE: str = ""               # 'matched:<pattern>' | 'no-match' | 'no-info'


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("ibf", instructions=_doc(
    full=_INSTRUCTIONS_FULL,
    compact=_INSTRUCTIONS_COMPACT,
    minimal=_INSTRUCTIONS_MIN,
    _key="__instructions__",
))

# Diagnostic auf stderr beim Start -- damit man im MCP-Log sieht was aktiv ist
print(f"[ibf-mcp] doc-level={DOC_LEVEL!r}({_DOC_LEVEL_SOURCE})  "
      f"toolset={_TOOLSET_NAME!r}({_TOOLSET_SOURCE})  "
      f"readonly={'ON' if READONLY_MODE else 'off'}({_READONLY_SOURCE}) "
      f"-- auto-detect läuft beim ersten Tool-Call",
      file=sys.stderr)
_logger.log_lifecycle("startup",
                      doc_level=DOC_LEVEL, doc_level_source=_DOC_LEVEL_SOURCE,
                      toolset=_TOOLSET_NAME, toolset_source=_TOOLSET_SOURCE,
                      readonly=READONLY_MODE, readonly_source=_READONLY_SOURCE,
                      log_enabled=_logger.is_enabled())


import functools as _functools
import time as _time


def tool(*args, write: bool = False, **kwargs):
    """Drop-in-Replacement für FastMCP's tool-Decorator mit drei Filtern:

    1. TOOLSET-Whitelist: wenn `ACTIVE_TOOLSET` gesetzt und Tool-Name nicht
       drin -> nicht registriert (bleibt normale Funktion).
    2. READONLY-Mode: wenn `write=True` UND `READONLY_MODE` aktiv -> nicht
       registriert.
    3. Auto-Detect-Hook: jeder Tool-Aufruf prüft einmal pro Session den
       Client-Namen und wechselt ggf. doc-level/toolset/readonly.

    Alle Tool-Funktionen werden zusätzlich in `_ALL_TOOLS` registriert
    (Metadata für späteres Live-Re-Registrieren).
    """
    def deco(fn):
        name = fn.__name__
        _ALL_TOOLS[name] = {"fn": fn, "write": write,
                            "args": args, "kwargs": kwargs}
        if ACTIVE_TOOLSET is not None and name not in ACTIVE_TOOLSET:
            _SKIPPED_TOOLS.append((name, f"not in toolset={_TOOLSET_NAME!r}"))
            return fn
        if write and READONLY_MODE:
            _SKIPPED_TOOLS.append((name, "write=True + READONLY_MODE"))
            return fn

        @_functools.wraps(fn)
        def hooked(*a, **kw):
            _maybe_auto_detect_client()
            t0 = _time.perf_counter()
            try:
                result = fn(*a, **kw)
            except BaseException as e:
                _logger.log_tool_error(name, e,
                                       duration_s=_time.perf_counter() - t0)
                raise
            _logger.log_tool_call(name, kw if kw else None,
                                  duration_s=_time.perf_counter() - t0)
            return result

        return mcp.tool(*args, **kwargs)(hooked)
    return deco


def _apply_toolset_and_readonly_live() -> dict:
    """Re-evaluiert anhand `ACTIVE_TOOLSET` + `READONLY_MODE`, welche Tools
    registriert sein sollen, und gleicht `mcp._tool_manager._tools` an.
    Returns Stats-Dict für Diagnose."""
    tm = getattr(mcp, "_tool_manager", None)
    if tm is None:
        raise RuntimeError("FastMCP._tool_manager nicht gefunden")
    tools = tm._tools

    desired: set[str] = set()
    for name, meta in _ALL_TOOLS.items():
        if ACTIVE_TOOLSET is not None and name not in ACTIVE_TOOLSET:
            continue
        if meta["write"] and READONLY_MODE:
            continue
        desired.add(name)

    current = set(tools.keys())
    to_add = desired - current
    to_remove = current - desired

    for name in to_remove:
        del tools[name]
    for name in to_add:
        meta = _ALL_TOOLS[name]
        fn = meta["fn"]

        @_functools.wraps(fn)
        def hooked(*a, _fn=fn, _name=name, **kw):
            _maybe_auto_detect_client()
            t0 = _time.perf_counter()
            try:
                result = _fn(*a, **kw)
            except BaseException as e:
                _logger.log_tool_error(_name, e,
                                       duration_s=_time.perf_counter() - t0)
                raise
            _logger.log_tool_call(_name, kw if kw else None,
                                  duration_s=_time.perf_counter() - t0)
            return result

        mcp.tool(*meta["args"], **meta["kwargs"])(hooked)

    _SKIPPED_TOOLS.clear()
    for name, meta in _ALL_TOOLS.items():
        if name in desired:
            continue
        if ACTIVE_TOOLSET is not None and name not in ACTIVE_TOOLSET:
            _SKIPPED_TOOLS.append((name, f"not in toolset={_TOOLSET_NAME!r}"))
        elif meta["write"] and READONLY_MODE:
            _SKIPPED_TOOLS.append((name, "write=True + READONLY_MODE"))

    return {"added": len(to_add), "removed": len(to_remove),
            "current": len(tools), "skipped": len(_SKIPPED_TOOLS)}


def _maybe_auto_detect_client() -> None:
    """Beim ersten Tool-Call: Client-Name aus aktiver Session lesen, gegen
    `_CLIENT_PROFILES` matchen, ggf. doc-level/toolset/readonly upgraden.

    Idempotent (nur einmal pro Server-Lifetime). Explizite ENV-Overrides
    werden respektiert (nicht überschrieben).
    """
    global _AUTO_DETECTED_CLIENT, DOC_LEVEL, _DOC_LEVEL_SOURCE
    global _TOOLSET_NAME, _TOOLSET_SOURCE, ACTIVE_TOOLSET
    global READONLY_MODE, _READONLY_SOURCE
    global _DETECTED_CLIENT_NAME, _DETECTED_CLIENT_VERSION, _DETECTED_PROFILE
    if _AUTO_DETECTED_CLIENT:
        return

    explicit_doc = _DOC_LEVEL_SOURCE in ("env", "file")
    explicit_toolset = _TOOLSET_SOURCE == "env"
    explicit_ro = _READONLY_SOURCE == "env"

    try:
        rc = mcp._mcp_server.request_context
        sess = rc.session
        params = getattr(sess, "_client_params", None)
        info = getattr(params, "clientInfo", None) if params else None
        client_name_raw = (getattr(info, "name", "") or "") if info else ""
        client_version = (getattr(info, "version", "") or "") if info else ""
        client_name = client_name_raw.lower()
    except (LookupError, AttributeError):
        return

    _DETECTED_CLIENT_NAME = client_name_raw
    _DETECTED_CLIENT_VERSION = client_version

    if not client_name:
        _AUTO_DETECTED_CLIENT = True
        _DETECTED_PROFILE = "no-info"
        return

    profile = None
    matched_pattern = None
    for pattern, p in _CLIENT_PROFILES.items():
        if pattern in client_name:
            profile = p
            matched_pattern = pattern
            break

    if profile is None:
        _AUTO_DETECTED_CLIENT = True
        _DETECTED_PROFILE = "no-match"
        print(f"[ibf-mcp] auto-detect: client={client_name_raw!r} -> kein Profil-Match",
              file=sys.stderr)
        return

    _DETECTED_PROFILE = f"matched:{matched_pattern}"
    auto_src = f"auto:{matched_pattern}"
    new_doc, new_toolset, new_ro = profile
    changed = []
    # Variante B: Source wird IMMER auf auto:<pattern> gesetzt wenn das Profil
    # matcht, auch wenn der Wert dadurch nicht physisch verändert wird.
    # changed[] enthält nur tatsächliche Wert-Änderungen (relevant für
    # list_changed-Notification + stderr-Log).
    if not explicit_doc:
        _DOC_LEVEL_SOURCE = auto_src
        if DOC_LEVEL != new_doc:
            DOC_LEVEL = new_doc
            try:
                _apply_doc_level_live(new_doc)
            except Exception:
                pass
            changed.append(f"doc-level={new_doc}")
    if not explicit_toolset:
        _TOOLSET_SOURCE = auto_src
        if _TOOLSET_NAME != new_toolset:
            _TOOLSET_NAME = new_toolset
            ACTIVE_TOOLSET = TOOLSETS.get(new_toolset)
            changed.append(f"toolset={new_toolset}")
    if not explicit_ro:
        _READONLY_SOURCE = auto_src
        if READONLY_MODE != new_ro:
            READONLY_MODE = new_ro
            changed.append(f"readonly={'ON' if new_ro else 'off'}")

    if any(c.startswith(("toolset=", "readonly=")) for c in changed):
        try:
            _apply_toolset_and_readonly_live()
        except Exception as e:
            print(f"[ibf-mcp] WARN: apply nach auto-detect: {e}", file=sys.stderr)
    if changed:
        try:
            _send_tools_list_changed()
        except Exception:
            pass
        print(f"[ibf-mcp] auto-detected client={client_name!r}: {', '.join(changed)}",
              file=sys.stderr)
    _logger.log_auto_detect(client_name_raw, client_version,
                            f"matched:{matched_pattern}", changed)

    _AUTO_DETECTED_CLIENT = True


def _require_proxmox() -> str | None:
    if not _auth_proxmox.is_authenticated():
        return _auth_proxmox.UNAVAILABLE
    return None


def _require_graylog() -> str | None:
    if not _auth_graylog.is_authenticated():
        return _auth_graylog.UNAVAILABLE
    return None


def _require_fortigate() -> str | None:
    if not _auth_fortigate.is_authenticated():
        return _auth_fortigate.UNAVAILABLE
    return None


# _require_mikrotik: in mikrotik-mcp.py


_HELP_TEXT = """=== IBF Combined MCP -- Tool-Übersicht ===

AUTH
  authenticate(password)             Session authentifizieren (8h gültig)

PROXMOX -- LESEN
  proxmox_cluster_status             Cluster-Health, Quorum, RAM/CPU pro Node
  proxmox_list_vms                   VM/LXC-Inventar (Filter: node/status/type/name)
  proxmox_list_tasks                 Letzte Tasks (Filter: node/vmid/type/status/only_failed)
  proxmox_list_storage               Storage-Pools mit Belegung
  proxmox_list_snapshots             Snapshots aller oder einer VM
  proxmox_get_vm_config              Vollständige VM-Konfiguration
  proxmox_get_task_log               Detail-Log eines Tasks via UPID

PROXMOX -- VM/LXC STEUERN
  proxmox_vm_power(vmid, op)         op: start | stop | shutdown | reboot | suspend | resume
  proxmox_vm_clone                   VM klonen (Voll- oder Linked-Clone, optional starten)
  proxmox_vm_rename                  VM umbenennen. Suffix '+hostname' setzt auch OS-Hostname
  proxmox_vm_change_id               VMID wechseln (Clone + optional Delete Original).
                                       Suffixe in new_vmid: '+confirm', '+delete'
  proxmox_vm_delete                  VM löschen (DESTRUKTIV; force_stop=True bei laufender)
  proxmox_vm_migrate                 VM auf anderen Node verschieben
  proxmox_vm_set_config              Beliebige Config-Keys setzen (memory, cores, ...)
  proxmox_vm_set_ram(name, gb)       RAM in GB. '22' absolut, '+2'/'-1.5' relativ. Suffix '+reboot'
  proxmox_vm_set_cores(name, cores)  CPU-Cores. '8' absolut, '+2' relativ. Suffix '+reboot'
  proxmox_vm_set_disk(name, gb)      Disk wachsen lassen. '+10' relativ. Suffix '+fs' = FS extenden
  proxmox_vm_snapshot                Snapshot erstellen
  proxmox_vm_delete_snapshot         Snapshot löschen ('all' = alle)

PROXMOX -- AUSFÜHREN
  proxmox_ssh_run                    Befehl auf Proxmox-NODE (k1-low/k2/k5)
  proxmox_ssh_apt_upgrade            apt upgrade auf Node(s)
  proxmox_vm_run                     Befehl IM GAST der VM via Guest Agent
  proxmox_vm_apt(name, action)       apt im Gast: update,upgrade,dist-upgrade,install,autoremove,full
                                       Komma-Liste möglich. auto_start_stop=True bei stopped VM
  proxmox_vm_install_recipe          Vordefinierte Installations-Rezepte (docker, ...)

PROXMOX -- BULK / WORKFLOW
  proxmox_evacuate_node              Alle VMs eines Nodes verteilen (Suffix '+confirm', '+maintenance')
  proxmox_restore_by_label           VMs zurück nach Tag-Label (deadlock-frei iterativ)
  proxmox_maintenance                Wartungsmodus: enable | disable | disable_all | status

GRAYLOG
  graylog_system_status              Graylog-Server-Health, Notifications, Cluster-Nodes
  graylog_list_streams               Alle konfigurierten Streams
  graylog_search_messages            Log-Suche (Query, Source, Stream, Zeitfenster)
  graylog_count_messages             Schnelle Trefferzahl
  graylog_top_values                 Top-N Werte eines Feldes (Aggregation)
  graylog_indexer_health             OpenSearch/Elasticsearch-Backend, Index-Größen

FORTIGATE (audit-User, read-only)
  fortigate_status                   System-Status (Modell, Firmware, Hostname, Uptime)
  fortigate_list_interfaces          Network-Interfaces
  fortigate_list_policies            Firewall-Policies
  fortigate_list_sessions            Aktive Sessions (filter_src/filter_dst optional)
  fortigate_show_log                 Logs filtern (since/until/min_level/logid,
                                     Aliase: attack=utm-ips, virus=utm-virus, ...)
  fortigate_run                      Beliebiger CLI-Befehl (read-only audit-User)

DASHBOARD (Morning-Triage, projekte/dashboard/morning.py)
  dashboard_morning                  Status-Übersicht aller Sektionen (sections=
                                     all|critical|fast|<csv>, timeout_s=50)
  dashboard_section                  Einzelne Sektion: security|infra|backups|
                                     network|cloud|logs
  dashboard_history                  Historische Snapshot-Werte aus Graylog
                                     (Variante D, app:ibf-dashboard)

MIKROTIK -- separater MCP-Server: mcp__mikrotik__*
  Datei: C:/Temp/claude/ibf/mikrotik/tools/mikrotik-mcp.py
  Tools: status, export, neighbors, run (alle ohne Prefix da eigener Server)
  Auth-Domain: 'mikrotik' (über buddy mikrotik on aktivieren oder globales Passwort)

NATURAL-LANGUAGE PATTERNS (Claude erkennt sie via Memory-Regel)
  buddy <mcp> on/off                 MCP aktivieren/deaktivieren (proxmox|graylog|all)
  buddy status                       Aktive Sessions
  clear <node>                       evacuate_node (Dry-Run zuerst)
  clear <node>+confirm               sofort ausführen
  clear <node>+maintenance           danach Wartung markieren
  enable maintenance auf <node>      Wartungsmodus an
  disable maintenance auf <node>     Wartungsmodus aus
  vm <X> +5gb+fs                     Disk wachsen + FS extenden
  vm <X> +2 cores                    Cores hinzufügen
  vm <X> apt update                  apt im Gast
  vm <X> apt update +1gb+fs          kombiniert (apt + Disk-Resize)
  restore by label                   VMs zu ihren Labels migrieren

CHEATSHEET FÜR SUFFIXE
  +confirm     -> dry_run=False (sofort ausführen)
  +maintenance -> nach Evacuate Wartungsmarker setzen
  +reboot      -> bei set_ram/set_cores: Reboot wenn Hotplug fehlt
  +fs          -> bei set_disk: Linux-Partition + FS auch wachsen lassen

Tipp: 'ibf_help filter=storage' filtert nach Stichwort.
"""


@tool()
def ibf_help(filter: str = "") -> str:
    """Übersicht aller IBF-MCP-Tools mit Kurzbeschreibung und Beispielen.

    Args:
        filter: Optional -- nur Zeilen anzeigen die diesen String enthalten (case-insensitive)
    """
    if not filter:
        return _HELP_TEXT
    f = filter.lower()
    lines = []
    keep_section = False
    for line in _HELP_TEXT.splitlines():
        # Sektionen mit Großbuchstaben/== beibehalten als Anker
        if line.startswith("==") or (line.strip() and line.strip()[0].isupper() and "  " not in line.strip()):
            lines.append(line)
            continue
        if f in line.lower():
            lines.append(line)
    return "\n".join(lines) if lines else f"Keine Treffer für '{filter}'."


@tool()
def authenticate(password: str) -> str:
    """Authentifizierung für IBF-MCP-Domains.

    Globales Passwort schaltet beide Domains (proxmox + graylog) frei.
    Domain-spezifisches Passwort schaltet nur die jeweilige Domain frei.

    Args:
        password: Das IBF-MCP-Passwort (beim Nutzer erfragen)
    """
    # Alle Auth-Instanzen probieren -- global pw matched eine, domain-pw nur seine eigene
    # (Mikrotik hat eigenen MCP, daher hier nicht aufgeführt)
    results = []
    for auth in (_auth_proxmox, _auth_graylog, _auth_fortigate):
        r = auth.login(password)
        results.append(r)
        if "Falsches" not in r and "FEHLER" not in r:
            return r
    return results[0]


# ---------------------------------------------------------------------------
# DOC-LEVEL META-TOOLS (Token-Reduktion, siehe tools/claude/mcp-doc-levels.md)
# ---------------------------------------------------------------------------

@tool(description=_doc(
    full="""Setzt das Doc-Level für Tool-Beschreibungen (Token-Reduktion).

    Args:
        level: 'full' | 'compact' | 'min'.
               full=verbose (~30k tokens schemata), compact=knapp (~8k),
               min=ein-Satz (~2k, args müssen geraten werden).
        scope: 'session' (default) -- nur dieser Server-Prozess, versucht
                                       Live-Update via list_changed.
               'global' -- schreibt %TEMP%/ibf_mcp_doc_level_default,
                          wirkt bei künftigen MCP-Starts ohne ENV-Override.

    Returns Status-String mit Hinweis ob Live-Update geklappt hat.
    """,
    compact=("Doc-Level setzen: full|compact|min, scope=session|global. "
             "session=jetzt+Live-Update, global=Default-File für nächste Starts."),
    minimal="Doc-Level setzen: full|compact|min.",
    _key="ibf_set_doc_level",
))
def ibf_set_doc_level(level: str, scope: str = "session") -> str:
    global DOC_LEVEL, _DOC_LEVEL_SOURCE
    level = (level or "").strip().lower()
    if level not in _DOC_LEVELS_VALID:
        return f"[FEHLER] level={level!r}, erwartet: {' | '.join(_DOC_LEVELS_VALID)}"
    if scope not in ("session", "global"):
        return f"[FEHLER] scope={scope!r}, erwartet: session | global"

    old = DOC_LEVEL
    _logger.log_level_change("doc-level", old, level, f"set_{scope}")

    notes: list[str] = []
    if scope == "global":
        try:
            _DOC_LEVEL_FILE.write_text(level, encoding="utf-8")
            notes.append(f"Default-File geschrieben: {_DOC_LEVEL_FILE}")
        except Exception as e:
            return f"[FEHLER] konnte Default-File nicht schreiben: {e}"

    if scope == "session":
        # In-Memory umschalten + Live-Update versuchen
        DOC_LEVEL = level
        _DOC_LEVEL_SOURCE = "set_session"
        try:
            n_updated = _apply_doc_level_live(level)
            notes.append(f"Live-Update: {n_updated} Beschreibungen ersetzt")
        except Exception as e:
            notes.append(f"[WARN] In-Memory-Wechsel ok, aber Replace fehlgeschlagen: {e}")
        try:
            _send_tools_list_changed()
            notes.append("list_changed-Notification gesendet (Client sollte tools/list neu holen)")
        except Exception as e:
            notes.append(f"[WARN] list_changed-Notification fehlgeschlagen: {e} -- "
                         "Reconnect (z.B. Claude Code neu verbinden) für sicheren Übernahme.")

    return f"[OK] doc-level={level} (scope={scope})\n  " + "\n  ".join(notes)


@tool(description=_doc(
    full="Zeigt das aktuell aktive Doc-Level + Quelle (env/file/default/set_session).",
    compact="Aktuelles Doc-Level anzeigen.",
    minimal="Doc-Level anzeigen.",
    _key="ibf_get_doc_level",
))
def ibf_get_doc_level() -> str:
    return (f"doc-level={DOC_LEVEL}  source={_DOC_LEVEL_SOURCE}\n"
            f"valid: {' | '.join(_DOC_LEVELS_VALID)}\n"
            f"file:  {_DOC_LEVEL_FILE} ({'exists' if _DOC_LEVEL_FILE.exists() else 'absent'})\n"
            f"env IBF_MCP_DOC_LEVEL: {os.environ.get('IBF_MCP_DOC_LEVEL', '(unset)')!r}\n"
            f"registered tools: {len(_TOOL_DESCRIPTIONS)}")


@tool(description=_doc(
    full=("Erzwingt notifications/tools/list_changed an den Client. "
          "Diagnose-Tool: zeigt ob Client dynamische Tool-Listen unterstützt."),
    compact="list_changed erzwingen (Diagnose).",
    minimal="Tool-Liste reload.",
    _key="ibf_reload_tools",
))
def ibf_reload_tools() -> str:
    try:
        _send_tools_list_changed()
        return "[OK] list_changed gesendet. Falls Tool-Beschreibungen sich nicht ändern, hat der Client das Notification nicht honoriert."
    except Exception as e:
        return f"[FEHLER] {e}"


@tool(description=_doc(
    full=("Zeigt aktive MCP-Konfiguration: Doc-Level, Toolset, Readonly, "
          "registrierte/ausgefilterte Tools. Diagnose-Tool."),
    compact="MCP-Status: Doc-Level, Toolset, Readonly, Tool-Counts.",
    minimal="MCP-Status.",
    _key="ibf_status",
))
def ibf_status() -> str:
    tm = getattr(mcp, "_tool_manager", None)
    n_active = len(getattr(tm, "_tools", {})) if tm else 0
    n_skipped = len(_SKIPPED_TOOLS)
    skipped_by_toolset = sum(1 for _, r in _SKIPPED_TOOLS if "toolset" in r)
    skipped_by_readonly = sum(1 for _, r in _SKIPPED_TOOLS if "READONLY" in r)

    toolset_size = "alle" if ACTIVE_TOOLSET is None else f"{len(ACTIVE_TOOLSET)} whitelisted"

    lines = [
        f"=== IBF MCP Status ===",
        f"",
        f"  {'Achse':<11s}  {'Wert':<10s}  Source",
        f"  ----------- ----------  -----------------------",
        f"  doc-level    {DOC_LEVEL:<10s}  {_DOC_LEVEL_SOURCE}",
        f"  toolset      {_TOOLSET_NAME:<10s}  {_TOOLSET_SOURCE}  ({toolset_size})",
        f"  readonly     {'ON' if READONLY_MODE else 'off':<10s}  {_READONLY_SOURCE}",
        f"",
        f"  Tools:        aktiv={n_active}  skipped={n_skipped}  "
        f"(toolset:{skipped_by_toolset} readonly:{skipped_by_readonly})",
        f"",
        f"  --- Auto-Detect ---",
    ]
    if not _AUTO_DETECTED_CLIENT:
        lines.append(f"  Status: noch nicht ausgeführt (läuft beim ersten Tool-Call)")
    else:
        cn = _DETECTED_CLIENT_NAME or "(leer)"
        cv = _DETECTED_CLIENT_VERSION or "?"
        lines.append(f"  Client erkannt:  {cn!r}  v{cv}")
        lines.append(f"  Profil:          {_DETECTED_PROFILE or '?'}")
    lines.append("")
    lines.append("  Bekannte Client-Profile:")
    for pat, (d, t, ro) in _CLIENT_PROFILES.items():
        ro_s = "RO" if ro else "RW"
        lines.append(f"    {pat!r:18s} -> doc={d:<7s} toolset={t:<7s} {ro_s}")

    lines.extend([
        f"",
        f"  --- ENV-Konfiguration (lock-overrides Auto-Detect) ---",
        f"    IBF_MCP_DOC_LEVEL  = {os.environ.get('IBF_MCP_DOC_LEVEL', '(unset)')!r}",
        f"    IBF_MCP_TOOLSET    = {os.environ.get('IBF_MCP_TOOLSET', '(unset)')!r}",
        f"    IBF_MCP_READONLY   = {os.environ.get('IBF_MCP_READONLY', '(unset)')!r}",
    ])
    if _SKIPPED_TOOLS:
        lines.append("")
        lines.append(f"  Ausgefilterte Tools (zeige max 10 von {n_skipped}):")
        for name, reason in _SKIPPED_TOOLS[:10]:
            lines.append(f"    - {name}  ({reason})")
        if n_skipped > 10:
            lines.append(f"    ... +{n_skipped - 10} weitere")

    # Logger-Diagnose
    try:
        ls = _logger.get_stats()
        lines.append("")
        lines.append("  --- Logger (GELF-Pipeline) ---")
        lines.append(f"  enabled: {ls['enabled']}  args: {ls['log_args']}  target: {ls['target']}")
        lines.append(f"  session_id: {ls['session_id']}")
        lines.append(f"  sent: ok={ls['sent_ok']}  fail={ls['sent_fail']}")
        if ls.get("last_error"):
            lines.append(f"  last error: {ls['last_error']}")
        if ls.get("last_event_ts"):
            import datetime as _dt
            t = _dt.datetime.fromtimestamp(ls["last_event_ts"]).strftime("%H:%M:%S")
            lines.append(f"  last event: {t}")
    except Exception as e:
        lines.append("")
        lines.append(f"  --- Logger ---  [FEHLER] get_stats: {e}")

    return "\n".join(lines)


@tool(description=_doc(
    full="""Toolset live wechseln (welche Tools im MCP-Schema sichtbar sind).

    Args:
        name: 'min'|'compact'|'full'. min=~15 Status-Tools, compact=alle
              Read-Tools, full=alles inkl. write/destruktiv.

    Sendet `notifications/tools/list_changed` -- Client SOLL Tools-Liste
    neu fetchen. Falls Client das nicht honoriert: Reconnect nötig.
    """,
    compact="Toolset live wechseln. name=min|compact|full.",
    minimal="Toolset wechseln.",
    _key="ibf_set_toolset",
))
def ibf_set_toolset(name: str) -> str:
    global ACTIVE_TOOLSET, _TOOLSET_NAME, _TOOLSET_SOURCE
    name = (name or "").strip().lower()
    if name not in TOOLSETS:
        return f"[FEHLER] toolset={name!r}, erlaubt: {sorted(TOOLSETS)}"
    old = _TOOLSET_NAME
    ACTIVE_TOOLSET = TOOLSETS[name]
    _TOOLSET_NAME = name
    _TOOLSET_SOURCE = "set_session"
    _logger.log_level_change("toolset", old, name, "set_session")
    stats = _apply_toolset_and_readonly_live()
    notif = "list_changed gesendet"
    try:
        _send_tools_list_changed()
    except Exception as e:
        notif = f"[WARN] list_changed fehlgeschlagen: {e}"
    return (f"[OK] toolset={name}: +{stats['added']} -{stats['removed']} "
            f"(aktiv: {stats['current']}, skipped: {stats['skipped']}). {notif}")


@tool(description=_doc(
    full="""READONLY-Mode live umschalten.

    Wenn an, werden alle als `write=True` annotierten Tools (~21 destruktive
    wie proxmox_vm_delete, *_run, *_apt, fortigate_run) aus dem Schema entfernt.

    Args:
        state: 'on' | 'off' | 'toggle'

    Sendet `notifications/tools/list_changed` an den Client.
    """,
    compact="READONLY live umschalten. state=on|off|toggle.",
    minimal="Readonly umschalten.",
    _key="ibf_set_readonly",
))
def ibf_set_readonly(state: str) -> str:
    global READONLY_MODE, _READONLY_SOURCE
    old = READONLY_MODE
    s = (state or "").strip().lower()
    if s in ("on", "1", "true", "yes"):
        READONLY_MODE = True
    elif s in ("off", "0", "false", "no"):
        READONLY_MODE = False
    elif s == "toggle":
        READONLY_MODE = not READONLY_MODE
    else:
        return f"[FEHLER] state={state!r}, erlaubt: on | off | toggle"
    _READONLY_SOURCE = "set_session"
    _logger.log_level_change("readonly", "ON" if old else "off",
                              "ON" if READONLY_MODE else "off", "set_session")
    stats = _apply_toolset_and_readonly_live()
    notif = "list_changed gesendet"
    try:
        _send_tools_list_changed()
    except Exception as e:
        notif = f"[WARN] list_changed fehlgeschlagen: {e}"
    return (f"[OK] readonly={'ON' if READONLY_MODE else 'off'}: "
            f"+{stats['added']} -{stats['removed']} "
            f"(aktiv: {stats['current']}). {notif}")


@tool(description=_doc(
    full=("Sendet einen Test-Event aus dem MCP-Server an Graylog und gibt "
          "Logger-Stats zurück. Diagnose-Tool: prüft ob die GELF-Pipeline "
          "vom Server-Subprozess aus überhaupt funktioniert. Verify "
          "anschließend mit ibf_logs(minutes=2)."),
    compact="GELF-Logger-Probe + Stats.",
    minimal="Logger-Probe.",
    _key="ibf_log_probe",
))
def ibf_log_probe() -> str:
    s = _logger.probe()
    lines = [
        "=== Logger-Probe ===",
        f"  enabled:        {s['enabled']}",
        f"  target host:    {s['target']}",
        f"  resolved ip:    {s.get('resolved_ip', '?')}",
        f"  test_id base:   {s['probe_test_id']}",
        "",
        f"  UDP-Send via _send():  sent_ok={s['sent_ok']}  sent_fail={s['sent_fail']}",
        f"    udp local addr:  {s.get('udp_local_addr', '?')}",
        f"  TCP-Send-Probe:        {s.get('tcp_send', '?')}",
        f"    tcp local addr:  {s.get('tcp_local_addr', '?')}",
    ]
    if s.get("last_error"):
        lines.append(f"  last_err:       {s['last_error']}")
    lines.append("")
    lines.append(f"  Verify in 5s: ibf_logs(minutes=2) -> {s['probe_test_id']}-udp / -tcp")
    return "\n".join(lines)


@tool(description=_doc(
    full="""Eigene MCP-Logs aus Graylog (`app:ibf-mcp`) kompakt abrufen --
    Schnell-Diagnose ohne externe Tools.

    Liest direkt mit dem Graylog-Token aus Credential Manager
    (umgeht Auth-Domain-Restriktionen von `graylog_search_messages`).
    Output: eine Zeile pro Event mit Zeit, event_type, session-Suffix,
    short_message.

    Args:
        minutes:    Zeitfenster, default 5.
        event_type: Filter auf 'lifecycle' | 'auto_detect' | 'level_change'
                    | 'tool_call' | 'tool_error'. Leer = alle.
        tool_name:  Filter auf konkretes Tool (bei tool_call/tool_error).
        session_id: Filter auf Session-ID, oder 'current' für die aktuell
                    laufende Server-Session.
        limit:      Max. Anzahl angezeigter Events (default 50, max 500).
    """,
    compact=("MCP-Logs aus Graylog kompakt. minutes/event_type/"
             "tool_name/session_id (oder 'current') als Filter."),
    minimal="MCP-Logs anzeigen.",
    _key="ibf_logs",
))
def ibf_logs(minutes: int = 5, event_type: str = "", tool_name: str = "",
             session_id: str = "", limit: int = 50) -> str:
    _dashboard_import()  # path-setup für lib-Imports
    import datetime as _dt
    from lib import graylog_api as _gl

    # Graylog speichert GELF-Custom-Felder ohne den `_`-Prefix (das wird
    # nur beim Senden verlangt). In Queries also ohne Underscore.
    parts = ["app:ibf-mcp"]
    if event_type:
        parts.append(f"event_type:{event_type}")
    if tool_name:
        parts.append(f"tool_name:{tool_name}")
    if session_id:
        sid = _logger.SESSION_ID if session_id.lower() == "current" else session_id
        # Field renamed `session_id` → `mcp_session` (2026-05-06): OpenSearch
        # had auto-mapped the original field to type=date and rejected our
        # extended ID format -- see mcp-self-observability.md changelog.
        parts.append(f'mcp_session:"{sid}"')
    query = " AND ".join(parts)

    end = _dt.datetime.now()
    start = end - _dt.timedelta(minutes=minutes)
    limit = max(1, min(limit, 500))

    try:
        msgs = _gl.messages(query, since=start, until=end, limit=limit,
                            fields=("timestamp,event_type,mcp_session,tool_name,"
                                    "client_name,client_version,axis,old_value,"
                                    "new_value,message"))
    except Exception as e:
        return f"[FEHLER] Graylog-Abfrage fehlgeschlagen: {e}"

    if not msgs:
        return (f"# Keine Events: {query}\n"
                f"# Zeitfenster: {start.strftime('%H:%M:%S')}--{end.strftime('%H:%M:%S')}")

    head = f"# {len(msgs)} Events  query={query}  window={minutes}min"
    lines = [head]
    # älteste zuerst
    for m in reversed(msgs):
        ts = (m.get('timestamp') or '')[11:19]
        et = (m.get('event_type') or '?')[:13]
        sid_full = m.get('mcp_session') or ''
        sid = sid_full.split('-pid')[-1] if '-pid' in sid_full else sid_full[-12:]
        msg = (m.get('message') or '')[:80]
        lines.append(f"  [{ts}] {et:13s}  ..{sid:14s}  {msg}")
    if len(msgs) >= limit:
        lines.append(f"# (limit {limit} erreicht -- es könnte mehr geben)")
    return "\n".join(lines)


def _apply_doc_level_live(level: str) -> int:
    """Ersetzt die Beschreibungen aller registrierten Tools im laufenden
    FastMCP-Server. Returns Anzahl tatsächlich aktualisierter Tools.

    Internas-Zugriff -- FastMCP-API für dynamisches Re-Description ist nicht
    public-stabil; Best-Effort. Bei API-Inkompatibilität wirft Exception
    → Caller fängt + zeigt Warnung.
    """
    n = 0
    # FastMCP speichert Tools im _tool_manager._tools dict
    tm = getattr(mcp, "_tool_manager", None)
    if tm is None:
        raise RuntimeError("FastMCP._tool_manager nicht gefunden")
    tools_dict = getattr(tm, "_tools", None)
    if tools_dict is None:
        raise RuntimeError("FastMCP._tool_manager._tools nicht gefunden")

    for name, descs in _TOOL_DESCRIPTIONS.items():
        if name == "__instructions__":
            continue
        new_desc = descs.get(level) or descs.get("compact") or descs.get("full") or ""
        tool_obj = tools_dict.get(name)
        if tool_obj is None:
            continue
        if hasattr(tool_obj, "description"):
            tool_obj.description = new_desc
            n += 1
    return n


def _send_tools_list_changed() -> None:
    """Best-Effort tools/list_changed-Notification an Client senden.

    Im MCP-SDK liegt die API als async-Methode auf der ServerSession:
    `session.send_tool_list_changed()`. Bei stdio gibt es zur Laufzeit eine
    aktive Session pro Verbindung -- wir holen sie via `request_context`,
    falls wir grade in einem Tool-Handler aufgerufen werden.

    Bei API-Wechsel oder wenn keine aktive Session existiert: RuntimeError.
    """
    import asyncio
    from mcp import types as _mcp_types

    inner = getattr(mcp, "_mcp_server", None)
    if inner is None:
        raise RuntimeError("FastMCP._mcp_server nicht gefunden")

    # Aktive Session aus dem Request-Context holen (nur möglich wenn wir
    # gerade in einem Tool-Aufruf laufen -- was beim ibf_set_doc_level
    # immer der Fall ist).
    try:
        rc = inner.request_context
        sess = rc.session
    except (LookupError, AttributeError) as e:
        raise RuntimeError(
            f"Keine aktive ServerSession (außerhalb Tool-Aufruf?): {e}")

    coro = sess.send_notification(
        _mcp_types.ServerNotification(_mcp_types.ToolListChangedNotification())
    )
    # Im laufenden Async-Kontext: schedule als Task. Sonst (sync-only):
    # event-loop holen und blockierend ausführen.
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        asyncio.run(coro)


# ---------------------------------------------------------------------------
# PROXMOX TOOLS
# ---------------------------------------------------------------------------

@tool(description=_doc(
    full="""Proxmox: Cluster-Health, Quorum, Ressourcenübersicht aller Nodes.

    Args:
        force_refresh: True umgeht den 30s-Cache und holt frische Daten
    """,
    compact="Proxmox-Cluster-Status: Quorum, Nodes, RAM/CPU. force_refresh: skipt Cache.",
    minimal="Proxmox-Cluster-Status.",
    _key="proxmox_cluster_status",
))
def proxmox_cluster_status(force_refresh: bool = False) -> str:
    guard = _require_proxmox()
    if guard: return guard

    if force_refresh:
        _cache_clear()
    cs = px("/cluster/status")
    res = px("/cluster/resources")
    cluster = next((c for c in cs if c.get("type") == "cluster"), {})
    status_nodes = {c["name"]: c for c in cs if c.get("type") == "node"}
    res_nodes    = {r["node"]: r for r in res if r.get("type") == "node"}
    vm_counts    = {}
    for r in res:
        if r.get("type") in ("qemu", "lxc") and r.get("status") == "running":
            vm_counts[r.get("node", "")] = vm_counts.get(r.get("node", ""), 0) + 1

    lines = [
        f"Cluster: {cluster.get('name','?')}  quorate: {'yes' if cluster.get('quorate') else 'NO!'}  "
        f"nodes: {cluster.get('nodes','?')}  version: {cluster.get('version','?')}",
        "",
    ]
    for name in sorted(status_nodes):
        sn = status_nodes[name]
        rn = res_nodes.get(name, {})
        mem_u = rn.get("mem", 0)
        mem_t = rn.get("maxmem", 1)
        lines.append(
            f"{name}: {'online' if sn.get('online') else 'OFFLINE'}  "
            f"CPU {rn.get('cpu',0)*100:.1f}%  "
            f"RAM {_fmt_bytes(mem_u)}/{_fmt_bytes(mem_t)} ({mem_u/mem_t*100:.0f}%)  "
            f"VMs running: {vm_counts.get(name,0)}  "
            f"uptime: {_fmt_uptime(rn.get('uptime'))}"
        )
    return "\n".join(lines)


@tool(description=_doc(
    full="""Proxmox: Liste aller VMs und LXC-Container.

    Args:
        node: Node-Filter (k1-low, k2, k5) oder leer für alle
        status: 'running' oder 'stopped' oder leer für alle
        vmtype: 'qemu', 'lxc' oder leer für alle
        name: Substring-Filter auf VM-Name (case-insensitive), z.B. 'pwac' findet 'pw-workhorse192'
        force_refresh: True umgeht den 30s-Cache
    """,
    compact=("Proxmox VMs/LXCs auflisten. node, status=running|stopped, "
             "vmtype=qemu|lxc, name=substring."),
    minimal="Proxmox-VMs auflisten.",
    _key="proxmox_list_vms",
))
def proxmox_list_vms(
    node: str = "",
    status: str = "",
    vmtype: str = "",
    name: str = "",
    force_refresh: bool = False,
) -> str:
    guard = _require_proxmox()
    if guard: return guard

    if force_refresh:
        _cache_clear()
    res = px("/cluster/resources")
    vms = [r for r in res if r.get("type") in ("qemu", "lxc")]
    if node:
        vms = [v for v in vms if v.get("node") == node]
    if status:
        vms = [v for v in vms if v.get("status") == status]
    if vmtype:
        vms = [v for v in vms if v.get("type") == vmtype]
    if name:
        n_lower = name.lower()
        vms = [v for v in vms if n_lower in (v.get("name") or "").lower()]
    vms.sort(key=lambda x: (x.get("node",""), x.get("vmid", 0)))

    lines = [f"{'VMID':<6}  {'Name':<30}  {'Type':<4}  {'Node':<8}  {'Status':<9}  {'RAM':<10}  {'Disk':<10}  CPU%"]
    lines.append("-" * 90)
    for v in vms:
        is_run = v.get("status") == "running"
        cpu = f"{v.get('cpu',0)*100:.1f}%" if is_run else "--"
        lines.append(
            f"{v.get('vmid','?'):<6}  {(v.get('name') or '')[:30]:<30}  "
            f"{'lxc' if v.get('type')=='lxc' else 'vm':<4}  "
            f"{v.get('node','?'):<8}  {v.get('status','?'):<9}  "
            f"{_fmt_bytes(v.get('maxmem',0)):<10}  "
            f"{_fmt_bytes(v.get('maxdisk',0)):<10}  {cpu}"
        )
    lines.append(f"\n{len(vms)} VMs total")
    return "\n".join(lines)


@tool(description=_doc(
    full="""Proxmox: Letzte Tasks im Cluster oder auf einem Node.

    Args:
        node: Node-Filter oder leer für alle
        limit: Max. Anzahl Tasks (default 30)
        vmid: Nur Tasks dieser VMID
        type_filter: Substring auf Task-Type (z.B. 'migrate', 'backup', 'snapshot')
        status_filter: Substring auf Task-Status (z.B. 'OK', 'error', 'aborted')
        only_failed: True → nur Tasks mit Status != 'OK' und != leer
    """,
    compact=("Proxmox-Tasks. node, limit=30, vmid, type_filter "
             "(migrate|backup|snapshot|...), status_filter, only_failed=True "
             "für nur-Fehler."),
    minimal="Proxmox-Tasks auflisten.",
    _key="proxmox_list_tasks",
))
def proxmox_list_tasks(
    node: str = "",
    limit: int = 30,
    vmid: str = "",
    type_filter: str = "",
    status_filter: str = "",
    only_failed: bool = False,
) -> str:
    guard = _require_proxmox()
    if guard: return guard

    # Mehr Tasks pro Node holen wenn Filter aktiv -- damit limit nach Filterung erreicht wird
    fetch_limit = limit * 5 if (vmid or type_filter or status_filter or only_failed) else limit

    nodes = [node] if node else KNOWN_NODES
    tasks = []
    for n in nodes:
        try:
            for t in (px(f"/nodes/{n}/tasks", {"limit": fetch_limit}) or []):
                t.setdefault("_node", n)
                tasks.append(t)
        except Exception:
            pass
    tasks.sort(key=lambda x: x.get("starttime", 0), reverse=True)

    # Filter anwenden
    if vmid:
        tasks = [t for t in tasks if str(t.get("id", "")) == str(vmid)]
    if type_filter:
        tf = type_filter.lower()
        tasks = [t for t in tasks if tf in (t.get("type") or "").lower()]
    if status_filter:
        sf = status_filter.lower()
        tasks = [t for t in tasks if sf in (t.get("status") or "").lower()]
    if only_failed:
        tasks = [t for t in tasks if t.get("status") and t.get("status") != "OK"]

    tasks = tasks[:limit]

    lines = [f"{'Node':<8}  {'Started':<20}  {'Dur':>6}  {'Status':<12}  {'Type':<14}  VMID"]
    lines.append("-" * 80)
    for t in tasks:
        start = t.get("starttime", 0)
        end   = t.get("endtime")
        dur   = f"{int(end)-int(start)}s" if end else "running"
        lines.append(
            f"{(t.get('node') or t.get('_node','?'))[:8]:<8}  "
            f"{_fmt_ts(start):<20}  {dur:>6}  "
            f"{(t.get('status') or 'running')[:12]:<12}  "
            f"{(t.get('type') or '?')[:14]:<14}  "
            f"{t.get('id','')}"
        )
    return "\n".join(lines)


@tool()
def proxmox_get_task_log(node: str, upid: str) -> str:
    """Proxmox: Detailliertes Log eines Tasks.

    Args:
        node: Node auf dem der Task lief
        upid: Task-UPID
    """
    guard = _require_proxmox()
    if guard: return guard

    encoded = urllib.parse.quote(upid, safe="")
    log = px(f"/nodes/{node}/tasks/{encoded}/log", {"limit": 200})
    if not isinstance(log, list):
        return str(log)
    return "\n".join(entry.get("t", "") for entry in log)


@tool()
def proxmox_list_storage(node: str = "") -> str:
    """Proxmox: Storage-Pools mit Belegung.

    Args:
        node: Node-Filter oder leer für alle
    """
    guard = _require_proxmox()
    if guard: return guard

    res = px("/cluster/resources")
    storages = [r for r in res if r.get("type") == "storage"]
    if node:
        storages = [s for s in storages if s.get("node") == node]
    storages.sort(key=lambda x: (x.get("node",""), x.get("storage","")))

    lines = [f"{'Storage':<22}  {'Node':<8}  {'Type':<8}  {'Used':<10}  {'Total':<10}  %Used"]
    lines.append("-" * 72)
    for s in storages:
        total = s.get("maxdisk", 0)
        used  = s.get("disk", 0)
        pct   = f"{used/total*100:.1f}%" if total else "--"
        warn  = " !" if total and used/total > 0.85 else ""
        lines.append(
            f"{(s.get('storage') or '?')[:22]:<22}  {(s.get('node') or '?')[:8]:<8}  "
            f"{(s.get('plugintype') or '?')[:8]:<8}  "
            f"{_fmt_bytes(used):<10}  {_fmt_bytes(total):<10}  {pct}{warn}"
        )
    return "\n".join(lines)


@tool()
def proxmox_list_snapshots(vmid: str = "", node: str = "") -> str:
    """Proxmox: Snapshots einer VM oder aller VMs.

    Args:
        vmid: VMID filtern oder leer für alle
        node: Node-Filter oder leer für alle
    """
    guard = _require_proxmox()
    if guard: return guard

    res = px("/cluster/resources")
    vms = [r for r in res if r.get("type") in ("qemu", "lxc")]
    if vmid:
        vms = [v for v in vms if str(v.get("vmid")) == str(vmid)]
    if node:
        vms = [v for v in vms if v.get("node") == node]

    snaps = []
    for v in vms:
        try:
            for s in (px(f"/nodes/{v['node']}/{v['type']}/{v['vmid']}/snapshot") or []):
                if s.get("name") == "current":
                    continue
                s["_vmid"] = v["vmid"]
                s["_name"] = v.get("name", "?")
                s["_node"] = v["node"]
                snaps.append(s)
        except Exception:
            pass
    snaps.sort(key=lambda x: x.get("snaptime", 0), reverse=True)

    if not snaps:
        return "Keine Snapshots gefunden."
    lines = [f"{'VMID':<6}  {'VM-Name':<28}  {'Node':<8}  {'Snapshot':<20}  Created"]
    lines.append("-" * 80)
    for s in snaps:
        lines.append(
            f"{s['_vmid']:<6}  {s['_name'][:28]:<28}  {s['_node'][:8]:<8}  "
            f"{s.get('name','?')[:20]:<20}  {_fmt_ts(s.get('snaptime'))}"
        )
    return "\n".join(lines)


@tool()
def proxmox_get_vm_config(vmid: str) -> str:
    """Proxmox: Aktuelle Konfiguration einer VM oder eines Containers.

    Args:
        vmid: VMID
    """
    guard = _require_proxmox()
    if guard: return guard

    vmid, node, vtype, name, status = _resolve_vm(vmid)
    cfg = px(f"/nodes/{node}/{vtype}/{vmid}/config")
    return f"{name} (VMID {vmid}, {vtype}, {node}, {status})\n\n" + json.dumps(cfg, indent=2)


@tool(write=True)
def proxmox_vm_power(vmid: str, op: str, wait: bool = True) -> str:
    """Proxmox: VM/LXC starten, stoppen, neustarten.

    Args:
        vmid: VMID
        op: start | stop | shutdown | reboot | suspend | resume
        wait: Auf Task-Abschluss warten
    """
    guard = _require_proxmox()
    if guard: return guard

    if op not in ("start", "stop", "shutdown", "reboot", "suspend", "resume"):
        return f"Ungültige Operation '{op}'."
    vmid, node, vtype, name, status = _resolve_vm(vmid)
    upid = px(f"/nodes/{node}/{vtype}/{vmid}/status/{op}", method="POST", body={})
    if not wait or not isinstance(upid, str):
        return f"{name} (VMID {vmid}): {op} gestartet."
    return f"{name} (VMID {vmid}): {op} -> {_wait_task(node, upid)}"


@tool(write=True)
def proxmox_vm_delete(
    vmid_or_name: str,
    force_stop: bool = False,
    purge_unreferenced: bool = True,
    skip_lock: bool = False,
    wait: bool = True,
) -> str:
    """Proxmox: VM oder LXC dauerhaft löschen.

    DESTRUKTIV: Disks und Konfiguration werden entfernt. Bei laufenden VMs
    schlägt der Aufruf standardmäßig fehl -- mit force_stop=True wird zuerst
    gestoppt. Vor dem Aufruf immer den Status zeigen und dem Nutzer das
    Vorhaben beschreiben.

    Args:
        vmid_or_name: VMID oder Name (Substring, case-insensitive)
        force_stop: True = laufende VM erst stoppen, dann löschen
        purge_unreferenced: True = orphaned disks auch entfernen (default)
        skip_lock: True = Lock ignorieren (nur in Notfällen)
        wait: Auf Task-Abschluss warten
    """
    guard = _require_proxmox()
    if guard: return guard

    vmid, node, vtype, name, status = _resolve_vm(vmid_or_name)
    base = f"/nodes/{node}/{vtype}/{vmid}"

    msgs = [f"Lösche {name} (VMID {vmid}, {vtype}, {node}, Status: {status})"]

    if status == "running":
        if not force_stop:
            return (f"[FEHLER] {name} (VMID {vmid}) läuft. "
                    f"Mit force_stop=True erst stoppen und dann löschen.")
        try:
            upid = px(f"{base}/status/stop", method="POST", body={})
            stop_result = _wait_task(node, upid) if isinstance(upid, str) else "OK"
            msgs.append(f"  Stop: {stop_result}")
        except RuntimeError as e:
            return f"[FEHLER] Stop fehlgeschlagen: {e}"

    # purge/skiplock müssen als Query-Parameter, NICHT im Body (Proxmox API-Quirk bei DELETE)
    params = {}
    if purge_unreferenced:
        params["purge"] = 1
    if skip_lock:
        params["skiplock"] = 1

    # kurze Wartezeit nach Stop, damit der Status-Lock vergeht
    if status == "running":
        import time
        time.sleep(2)

    try:
        upid = px(base, method="DELETE", params=params if params else None)
    except RuntimeError as e:
        return f"[FEHLER] Delete fehlgeschlagen: {e}"

    if not wait or not isinstance(upid, str):
        msgs.append(f"  Delete-Task: {upid}")
        return "\n".join(msgs)

    result = _wait_task(node, upid, timeout=300)
    msgs.append(f"  Delete: {result}")
    return "\n".join(msgs)


@tool(write=True)
def proxmox_vm_run(vmid_or_name: str, cmd: str, timeout: int = 120) -> str:
    """Proxmox: Beliebigen Shell-Befehl IN einer QEMU-VM ausführen via Guest Agent.

    Voraussetzungen: agent=1 in der VM-Config UND qemu-guest-agent läuft im Gast.
    Sicherheit: User vor Ausführung explizit fragen ('Darf ich auf VM X ausführen: Y?').

    Args:
        vmid_or_name: VMID oder Name (Substring, case-insensitive)
        cmd: Bash-Befehlszeile, z.B. 'df -h /' oder 'uptime'
        timeout: Sekunden bis Abbruch (default 120)
    """
    guard = _require_proxmox()
    if guard: return guard

    vmid, node, vtype, name, status = _resolve_vm(vmid_or_name)
    if vtype != "qemu":
        return f"{name} ist ein LXC -- proxmox_vm_run ist nur für QEMU. Für LXC: pct exec via SSH."
    if status != "running":
        return f"{name} (VMID {vmid}) ist nicht running (Status: {status}) -- Guest Agent nicht erreichbar."

    cfg = px(f"/nodes/{node}/qemu/{vmid}/config")
    if cfg.get("agent") not in (1, "1", True, "true"):
        return f"{name}: Guest Agent nicht aktiviert (config: agent=1 fehlt)."

    import base64 as _b64
    encoded = _b64.b64encode(cmd.encode()).decode()
    rc, out, err = _qemu_guest_exec(
        node, vmid,
        ["bash", "-c", f"echo {encoded} | base64 -d | bash"],
        timeout=timeout,
    )
    lines = [f"{name} (VMID {vmid}): rc={rc}"]
    if out.strip():
        lines.append("--- stdout ---")
        lines.append(out.rstrip())
    if err.strip():
        lines.append("--- stderr ---")
        lines.append(err.rstrip())
    return "\n".join(lines)


@tool(write=True)
def proxmox_vm_apt(
    vmid_or_name: str,
    action: str = "upgrade",
    packages: str = "",
    auto_start_stop: bool = False,
    timeout: int = 600,
) -> str:
    """Proxmox: apt-Operation IN einer Debian/Ubuntu-VM via Guest Agent.

    Sicherheit: vor Ausführung explizit beim Nutzer bestätigen.

    Args:
        vmid_or_name: VMID oder Name
        action: Eine oder mehrere Aktionen, komma-getrennt. Erlaubt:
                'update' | 'upgrade' | 'dist-upgrade' | 'full-upgrade' |
                'install' | 'autoremove' | 'full' (=update+upgrade+autoremove)
                Beispiel: 'update,upgrade,autoremove'
        packages: Bei action='install' die zu installierenden Pakete (Leerzeichen-getrennt)
        auto_start_stop: Bei gestoppter VM erst starten, apt ausführen, dann wieder stoppen
        timeout: Sekunden (default 600)
    """
    guard = _require_proxmox()
    if guard: return guard

    # Action-Liste parsen
    actions = [a.strip().lower() for a in action.split(",") if a.strip()]
    if not actions:
        return "[FEHLER] Keine action angegeben"

    parts = []
    for a in actions:
        if a in ("update", "refresh"):
            parts.append("apt-get update -q")
        elif a == "upgrade":
            parts.append("apt-get upgrade -y")
        elif a in ("dist-upgrade", "dist", "full-upgrade"):
            parts.append("apt-get dist-upgrade -y")
        elif a == "install":
            if not packages:
                return "[FEHLER] action 'install' braucht packages='paket1 paket2'"
            parts.append(f"apt-get install -y {packages}")
        elif a == "autoremove":
            parts.append("apt-get autoremove -y")
        elif a == "full":
            parts.extend([
                "apt-get update -q",
                "apt-get upgrade -y",
                "apt-get autoremove -y",
            ])
        else:
            return (f"[FEHLER] Unbekannte action '{a}'. "
                    f"Erlaubt: update, upgrade, dist-upgrade, install, autoremove, full")

    cmd = "DEBIAN_FRONTEND=noninteractive " + " && DEBIAN_FRONTEND=noninteractive ".join(parts)
    cmd = f"({cmd}) 2>&1 | tail -20"

    # auto_start_stop: VM starten falls stopped
    vmid, node, vtype, name, status = _resolve_vm(vmid_or_name)
    if vtype != "qemu":
        return f"{name} ist ein LXC -- proxmox_vm_apt ist nur für QEMU."

    msgs = [f"{name} (VMID {vmid}): apt {','.join(actions)}"]
    needs_restore = False

    if status != "running":
        if not auto_start_stop:
            return (f"[FEHLER] {name} ist {status}. Mit auto_start_stop=True wird die VM "
                    f"gestartet, apt ausgeführt und danach wieder gestoppt.")
        msgs.append(f"  VM ist {status} -- starte für apt-Lauf")
        try:
            upid = px(f"/nodes/{node}/qemu/{vmid}/status/start", method="POST", body={})
            r = _wait_task(node, upid) if isinstance(upid, str) else "OK"
            msgs.append(f"  Start: {r}")
        except RuntimeError as e:
            return "\n".join(msgs + [f"  [FEHLER] Start: {e}"])
        # Cache invalidieren -- sonst sieht _resolve_vm in vm_run noch "stopped"
        _cache_clear()
        msgs.append(f"  Warte bis Guest Agent erreichbar ...")
        if not _qemu_guest_wait_agent(node, vmid, timeout=120):
            msgs.append(f"  [FEHLER] Guest Agent nicht erreichbar nach 120s")
            return "\n".join(msgs)
        msgs.append(f"  Guest Agent ready")
        needs_restore = True

    # apt ausführen
    result = proxmox_vm_run(vmid_or_name, cmd, timeout=timeout)
    msgs.append("--- apt-Output ---")
    msgs.append(result)

    # VM wieder stoppen falls vorher down
    if needs_restore:
        msgs.append(f"\n  Fahre VM wieder runter (war vorher {status})")
        try:
            upid = px(f"/nodes/{node}/qemu/{vmid}/status/shutdown", method="POST", body={})
            r = _wait_task(node, upid, timeout=120) if isinstance(upid, str) else "OK"
            msgs.append(f"  Shutdown: {r}")
        except RuntimeError as e:
            msgs.append(f"  [FEHLER] Shutdown: {e}")

    return "\n".join(msgs)


# Installations-Rezepte (Bash-Scripte) -- werden via Guest Agent ausgeführt.
# Quelle für 'docker': https://docs.docker.com/engine/install/debian/
_RECIPES = {
    "docker": r"""
set -e
echo "=== Docker CE Installation (Debian Standard) ==="

# 1. Konflikt-Pakete entfernen
for pkg in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
  DEBIAN_FRONTEND=noninteractive apt-get -y remove "$pkg" 2>/dev/null || true
done

# 2. Vorbedingungen
DEBIAN_FRONTEND=noninteractive apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg

# 3. Docker GPG-Key
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

# 4. Repository hinzufügen
ARCH=$(dpkg --print-architecture)
CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $CODENAME stable" \
  | tee /etc/apt/sources.list.d/docker.list > /dev/null

# 5. Docker Engine + Plugins installieren
DEBIAN_FRONTEND=noninteractive apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 6. Service starten + verifizieren
systemctl enable --now docker
docker --version
docker compose version
echo "=== Docker Installation OK ==="
""",
}


@tool(write=True)
def proxmox_vm_install_recipe(
    vmid_or_name: str,
    recipe: str,
    auto_start_stop: bool = False,
    timeout: int = 1200,
) -> str:
    """Vordefiniertes Installations-Rezept auf einer VM ausführen.

    Verfügbare Rezepte:
      docker  -- Docker CE nach offizieller Debian-Anleitung (docs.docker.com)

    Sicherheit: vor Ausführung explizit beim Nutzer bestätigen.

    Args:
        vmid_or_name: VMID oder Name (Substring)
        recipe: Name des Rezepts, z.B. 'docker'
        auto_start_stop: Bei stopped VM erst starten, ausführen, dann wieder stoppen
        timeout: Sekunden (default 1200 = 20min)
    """
    guard = _require_proxmox()
    if guard: return guard

    r = recipe.strip().lower()
    if r not in _RECIPES:
        return (f"[FEHLER] Unbekanntes Rezept '{recipe}'. "
                f"Verfügbar: {', '.join(sorted(_RECIPES))}")
    script = _RECIPES[r]

    vmid, node, vtype, name, status = _resolve_vm(vmid_or_name)
    if vtype != "qemu":
        return f"{name} ist ein LXC -- proxmox_vm_install_recipe ist nur für QEMU."

    msgs = [f"{name} (VMID {vmid}): Rezept '{r}'"]
    needs_restore = False

    if status != "running":
        if not auto_start_stop:
            return (f"[FEHLER] {name} ist {status}. "
                    f"Mit auto_start_stop=True erst starten, ausführen, dann wieder stoppen.")
        msgs.append(f"  VM ist {status} -- starte für Rezept-Lauf")
        try:
            upid = px(f"/nodes/{node}/qemu/{vmid}/status/start", method="POST", body={})
            r_start = _wait_task(node, upid) if isinstance(upid, str) else "OK"
            msgs.append(f"  Start: {r_start}")
        except RuntimeError as e:
            return "\n".join(msgs + [f"  [FEHLER] Start: {e}"])
        _cache_clear()
        msgs.append(f"  Warte bis Guest Agent erreichbar ...")
        if not _qemu_guest_wait_agent(node, vmid, timeout=120):
            msgs.append(f"  [FEHLER] Guest Agent nicht erreichbar nach 120s")
            return "\n".join(msgs)
        msgs.append(f"  Guest Agent ready")
        needs_restore = True

    # Rezept ausführen
    msgs.append(f"--- Rezept-Output ---")
    out = proxmox_vm_run(vmid_or_name, script, timeout=timeout)
    msgs.append(out)

    if needs_restore:
        msgs.append(f"\n  Fahre VM wieder runter (war vorher {status})")
        try:
            upid = px(f"/nodes/{node}/qemu/{vmid}/status/shutdown", method="POST", body={})
            r_stop = _wait_task(node, upid, timeout=120) if isinstance(upid, str) else "OK"
            msgs.append(f"  Shutdown: {r_stop}")
        except RuntimeError as e:
            msgs.append(f"  [FEHLER] Shutdown: {e}")

    return "\n".join(msgs)


@tool(write=True)
def proxmox_vm_change_id(
    vmid_or_name: str,
    new_vmid: str,
    dry_run: bool = True,
    delete_original: bool = False,
    target_node: str = "",
    full: bool = True,
    timeout: int = 1800,
) -> str:
    """Proxmox: VMID einer VM/LXC ändern (via Clone + optionalem Delete des Originals).

    Proxmox kann VMIDs nicht direkt umbenennen. Dieses Tool erledigt das über Klonen
    auf die neue ID und optionalem Löschen des Originals.

    Wichtig: VM sollte STOPPED sein für sauberen Voll-Clone. Snapshots gehen verloren!

    Suffixe in new_vmid:
      +confirm  -> dry_run=False (ausführen)
      +delete   -> delete_original=True (echter Replace)
      Beispiel: '200+confirm+delete' = ausführen UND Original entfernen

    Args:
        vmid_or_name: Source-VMID oder Name (Substring)
        new_vmid: Neue VMID (numerisch). Suffixe '+confirm' und '+delete' möglich
        dry_run: True (default) = nur Plan zeigen
        delete_original: True = Original nach Clone löschen (DESTRUKTIV)
        target_node: Optional, default = gleicher Node
        full: True (default) = Voll-Clone, False = Linked-Clone (braucht Snapshot)
        timeout: Sekunden für Clone-Task (default 1800)
    """
    guard = _require_proxmox()
    if guard: return guard

    nv = str(new_vmid).strip()
    nv, found_confirm = _extract_flag(nv, "confirm")
    if found_confirm:
        dry_run = False
    nv, found_delete = _extract_flag(nv, "delete")
    if found_delete:
        delete_original = True

    if not nv.isdigit():
        return f"[FEHLER] Neue VMID muss numerisch sein, war: '{nv}'"

    vmid, node, vtype, name, status = _resolve_vm(vmid_or_name)

    # Prüfen ob neue VMID schon belegt ist
    res = px("/cluster/resources")
    if any(str(r.get("vmid")) == nv for r in res if r.get("type") in ("qemu", "lxc")):
        return f"[FEHLER] VMID {nv} ist bereits vergeben."

    msgs = [f"=== VMID-Wechsel: {name} ({vmid}) -> {nv} ==="]
    msgs.append(f"  Source-Status: {status}")
    msgs.append(f"  Source-Type:   {vtype}")
    msgs.append(f"  Aktion:        Clone + " +
                ("Original LÖSCHEN" if delete_original else "Original behalten"))
    if status == "running":
        msgs.append(f"  ⚠ VM läuft -- für sauberen Voll-Clone besser stoppen.")
        msgs.append(f"     Wenn online geklont wird, werden Disk-Inhalte zur Klon-Zeit eingefroren.")
    if delete_original:
        msgs.append(f"  ⚠ Snapshots des Originals gehen verloren (Klon hat keine).")

    if dry_run:
        msgs.append(f"\n[DRY-RUN] Keine Aktion. Suffix '+confirm' oder dry_run=False zum Ausführen.")
        return "\n".join(msgs)

    # Phase 1: Clone
    msgs.append(f"\nPhase 1: Clone {vmid} -> {nv}")
    body = {"newid": nv, "full": 1 if full else 0}
    body["name" if vtype == "qemu" else "hostname"] = name
    if target_node:
        body["target"] = target_node

    try:
        upid = px(f"/nodes/{node}/{vtype}/{vmid}/clone", method="POST", body=body)
    except RuntimeError as e:
        return "\n".join(msgs + [f"  [FEHLER] Clone: {e}"])

    if isinstance(upid, str):
        result = _wait_task(node, upid, timeout=timeout)
        msgs.append(f"  Clone-Task: {result}")
        if result != "OK":
            msgs.append(f"  [ABBRUCH] Clone-Task lieferte '{result}', Original {vmid} unverändert.")
            # Prüfen ob ein Partial-State bei der neuen VMID hängengeblieben ist
            _cache_clear()
            try:
                res2 = px("/cluster/resources")
                partial = next((r for r in res2
                                if str(r.get("vmid")) == nv
                                and r.get("type") in ("qemu", "lxc")), None)
                if partial:
                    msgs.append(f"  ⚠ Aber: VMID {nv} existiert dennoch (möglicherweise unvollständig/locked).")
                    msgs.append(f"     Aufräumen: proxmox_vm_delete('{nv}', force_stop=True, skip_lock=True)")
            except Exception:
                pass
            return "\n".join(msgs)
    else:
        msgs.append(f"  Clone-Task: gestartet (kein UPID)")

    # Phase 2: Optional delete
    if delete_original:
        msgs.append(f"\nPhase 2: Original {vmid} entfernen")
        if status == "running":
            try:
                stop_upid = px(f"/nodes/{node}/{vtype}/{vmid}/status/stop", method="POST", body={})
                if isinstance(stop_upid, str):
                    _wait_task(node, stop_upid)
                msgs.append(f"  Stop {vmid}: OK")
                import time
                time.sleep(2)
            except RuntimeError as e:
                msgs.append(f"  [FEHLER] Stop: {e} -- Original NICHT gelöscht.")
                return "\n".join(msgs)

        try:
            del_upid = px(f"/nodes/{node}/{vtype}/{vmid}",
                          method="DELETE", params={"purge": 1})
            del_result = _wait_task(node, del_upid, timeout=300) if isinstance(del_upid, str) else "OK"
            msgs.append(f"  Delete {vmid}: {del_result}")
        except RuntimeError as e:
            msgs.append(f"  [FEHLER] Delete: {e} -- Klon {nv} existiert, Original {vmid} bleibt.")
            return "\n".join(msgs)
        msgs.append(f"\n✓ VMID-Wechsel abgeschlossen: {vmid} -> {nv}")
    else:
        msgs.append(f"\n✓ Klon erstellt: {nv}. Original {vmid} bleibt vorhanden.")
        msgs.append(f"   Zum Entfernen später: proxmox_vm_delete({vmid})")

    return "\n".join(msgs)


@tool(write=True)
def proxmox_vm_rename(
    vmid_or_name: str,
    new_name: str,
    update_hostname: bool = False,
    auto_start_stop: bool = False,
    timeout: int = 300,
) -> str:
    """Proxmox: VM/LXC umbenennen.

    Standard: ändert nur das Proxmox-Label (config: name= bzw. hostname=).
    Mit '+hostname' Suffix oder update_hostname=True: setzt zusätzlich den OS-Hostnamen
    im Gast (für QEMU via Guest Agent, für LXC via pct).

    Tipp im Chat: 'rename 100 testmaschine +hostname' setzt automatisch update_hostname=True.

    Args:
        vmid_or_name: VMID oder Name (Substring)
        new_name: Neuer Name. Suffix '+hostname' setzt update_hostname=True
        update_hostname: True = OS-Hostname im Gast setzen
        auto_start_stop: Bei stopped VM erst starten (für hostname-Update), dann wieder stoppen
        timeout: Sekunden für Hostname-Update
    """
    guard = _require_proxmox()
    if guard: return guard

    new_name, found_hostname = _extract_flag(new_name.strip(), "hostname")
    if found_hostname:
        update_hostname = True

    if not new_name:
        return "[FEHLER] new_name fehlt (oder nur '+hostname' angegeben)"

    vmid, node, vtype, name, status = _resolve_vm(vmid_or_name)
    msgs = [f"{name} (VMID {vmid}) -> '{new_name}'"]

    # 1. Proxmox-Label ändern
    cfg_key = "name" if vtype == "qemu" else "hostname"
    try:
        px(f"/nodes/{node}/{vtype}/{vmid}/config", method="PUT",
           body={cfg_key: new_name})
        msgs.append(f"  Proxmox-Label ({cfg_key}={new_name}) gesetzt")
    except RuntimeError as e:
        return "\n".join(msgs + [f"  [FEHLER] Config-Update: {e}"])

    if not update_hostname:
        return "\n".join(msgs)

    # 2. OS-Hostname setzen
    if vtype == "lxc":
        # LXC: hostname-Setting ist gleich der OS-Hostname (auf next-boot oder via pct exec)
        msgs.append(f"  LXC: OS-Hostname identisch zu config-hostname (greift bei nächstem Reboot)")
        return "\n".join(msgs)

    # QEMU: Guest Agent verwenden
    needs_restore = False
    if status != "running":
        if not auto_start_stop:
            return "\n".join(msgs + [
                f"  [HINWEIS] update_hostname übersprungen: VM ist {status}.",
                f"  Mit auto_start_stop=True wird die VM gestartet, hostname gesetzt, dann wieder gestoppt."
            ])
        msgs.append(f"  VM ist {status} -- starte für hostname-Update")
        try:
            upid = px(f"/nodes/{node}/qemu/{vmid}/status/start", method="POST", body={})
            r = _wait_task(node, upid) if isinstance(upid, str) else "OK"
            msgs.append(f"  Start: {r}")
        except RuntimeError as e:
            return "\n".join(msgs + [f"  [FEHLER] Start: {e}"])
        _cache_clear()
        if not _qemu_guest_wait_agent(node, vmid, timeout=120):
            msgs.append(f"  [FEHLER] Guest Agent nicht erreichbar")
            return "\n".join(msgs)
        msgs.append(f"  Guest Agent ready")
        needs_restore = True

    # Hostname im Gast setzen (Bash-Script via Guest Agent)
    cfg = px(f"/nodes/{node}/qemu/{vmid}/config")
    if cfg.get("agent") not in (1, "1", True, "true"):
        msgs.append(f"  [FEHLER] Guest Agent nicht aktiviert (agent=1 fehlt)")
        return "\n".join(msgs)

    hostname_script = f"""
set -e
OLD=$(hostname)
NEW={new_name!s}
hostnamectl set-hostname "$NEW" 2>/dev/null || echo "$NEW" > /etc/hostname
# /etc/hosts mitziehen
sed -i "s/\\b$OLD\\b/$NEW/g" /etc/hosts 2>/dev/null || true
echo "Hostname: $OLD -> $(hostname)"
"""
    out = proxmox_vm_run(vmid_or_name, hostname_script, timeout=timeout)
    msgs.append(f"--- hostname-update ---")
    msgs.append(out)

    if needs_restore:
        msgs.append(f"\n  Fahre VM wieder runter (war vorher {status})")
        try:
            upid = px(f"/nodes/{node}/qemu/{vmid}/status/shutdown", method="POST", body={})
            r = _wait_task(node, upid, timeout=120) if isinstance(upid, str) else "OK"
            msgs.append(f"  Shutdown: {r}")
        except RuntimeError as e:
            msgs.append(f"  [FEHLER] Shutdown: {e}")

    return "\n".join(msgs)


@tool(write=True)
def proxmox_vm_clone(
    vmid_or_name: str,
    new_vmid: str = "",
    new_name: str = "",
    target_node: str = "",
    full: bool = True,
    snapshot: str = "",
    storage: str = "",
    start: bool = False,
    wait: bool = True,
) -> str:
    """Proxmox: VM oder LXC klonen.

    Voll-Clone (default) erstellt eine unabhängige Kopie. Linked Clone ist nur für
    bestimmte Storage-Typen möglich und braucht meist einen Snapshot der Source.

    Args:
        vmid_or_name: Source-VMID oder Name (Substring, case-insensitive)
        new_vmid: Neue VMID. Leer = nächste freie automatisch
        new_name: Name/Hostname für den Klon. Leer = '<source-name>-clone'
        target_node: Ziel-Node (default: gleich wie Source)
        full: True = Voll-Clone (unabhängig), False = Linked Clone
        snapshot: Source-Snapshot-Name (optional, nötig für Linked Clone bei laufender VM)
        storage: Ziel-Storage-Pool (optional, default: gleicher wie Source)
        start: Klon nach Erstellung automatisch starten
        wait: Auf Clone-Task warten
    """
    guard = _require_proxmox()
    if guard: return guard

    vmid, node, vtype, name, status = _resolve_vm(vmid_or_name)

    # neue VMID besorgen
    if not new_vmid:
        try:
            new_vmid = str(px("/cluster/nextid"))
        except Exception as e:
            return f"[FEHLER] Konnte keine neue VMID besorgen: {e}"

    body = {"newid": new_vmid, "full": 1 if full else 0}
    if new_name:
        # Proxmox: 'name' für QEMU, 'hostname' für LXC
        body["name" if vtype == "qemu" else "hostname"] = new_name
    else:
        body["name" if vtype == "qemu" else "hostname"] = f"{name}-clone"
    if target_node:
        body["target"] = target_node
    if snapshot:
        body["snapname"] = snapshot
    if storage:
        body["storage"] = storage

    try:
        upid = px(f"/nodes/{node}/{vtype}/{vmid}/clone", method="POST", body=body)
    except RuntimeError as e:
        return f"[FEHLER] Clone-Aufruf fehlgeschlagen: {e}"

    msgs = [f"Clone {name} (VMID {vmid}) -> neuer VMID {new_vmid}"]
    if not wait or not isinstance(upid, str):
        msgs.append(f"  Task: {upid}")
        return "\n".join(msgs)

    result = _wait_task(node, upid, timeout=900)
    msgs.append(f"  Clone-Task: {result}")

    if result == "OK" and start:
        # Clone liegt auf target_node falls angegeben, sonst auf Source-Node
        clone_node = target_node or node
        try:
            start_upid = px(f"/nodes/{clone_node}/{vtype}/{new_vmid}/status/start",
                            method="POST", body={})
            if isinstance(start_upid, str):
                start_result = _wait_task(clone_node, start_upid)
                msgs.append(f"  Start: {start_result}")
            else:
                msgs.append(f"  Start: gestartet")
        except RuntimeError as e:
            msgs.append(f"  Start FEHLER: {e}")

    return "\n".join(msgs)


@tool(write=True)
def proxmox_vm_migrate(vmid: str, target: str, wait: bool = True) -> str:
    """Proxmox: VM/LXC auf einen anderen Node migrieren.

    Args:
        vmid: VMID
        target: Ziel-Node
        wait: Auf Abschluss warten
    """
    guard = _require_proxmox()
    if guard: return guard

    vmid, node, vtype, name, status = _resolve_vm(vmid)
    if node == target:
        return f"{name} läuft bereits auf {target}."
    if vtype == "qemu":
        body = {"target": target, "with-local-disks": 1}
    else:
        body = {"target": target}
        if status == "running":
            body["restart"] = 1
    upid = px(f"/nodes/{node}/{vtype}/{vmid}/migrate", method="POST", body=body)
    if not wait or not isinstance(upid, str):
        return f"{name}: Migration {node} -> {target} gestartet."
    return f"{name}: Migration {node} -> {target} -> {_wait_task(node, upid, timeout=600)}"


@tool(write=True)
def proxmox_evacuate_node(
    node: str,
    exclude_vmids: str = "",
    targets: str = "",
    dry_run: bool = True,
    wait: bool = True,
) -> str:
    """Proxmox: Alle VMs/LXC eines Nodes auf andere Nodes verteilen (greedy by free RAM).

    Hardware-pinnte VMs (hostpci, USB-Passthrough) werden automatisch ausgeschlossen.
    Default ist dry_run=True -- zeigt nur den Plan, keine Aktion. Erst mit dry_run=False
    werden Migrationen wirklich ausgeführt.

    Tipps für den node-Parameter:
      '+confirm'     -> dry_run=False (sofort ausführen)
      '+maintenance' -> nach Evacuate auch Wartungsmodus aktivieren
      Beispiel: 'pm16+confirm+maintenance' macht beides

    Args:
        node: Source-Node. Erlaubt Suffixe '+confirm' und/oder '+maintenance'
        exclude_vmids: Komma-getrennte VMIDs die auf dem Source-Node bleiben sollen
        targets: Komma-getrennte erlaubte Ziel-Nodes (default: alle anderen Online-Nodes)
        dry_run: True = nur Plan zeigen (default). False = Migrationen ausführen
        wait: Auf jede Migration warten (sequentielle Abarbeitung)
    """
    guard = _require_proxmox()
    if guard: return guard

    node, found_confirm = _extract_flag(str(node).strip(), "confirm")
    if found_confirm:
        dry_run = False
    node, found_maintenance = _extract_flag(node, "maintenance")

    res = px("/cluster/resources")
    nodes = [r for r in res if r.get("type") == "node"]
    src = next((n for n in nodes if n.get("node") == node), None)
    if not src:
        return f"[FEHLER] Node '{node}' nicht gefunden."

    online_others = [n for n in nodes if n.get("node") != node and n.get("status") == "online"]
    if targets:
        allowed = {t.strip() for t in targets.split(",")}
        online_others = [n for n in online_others if n.get("node") in allowed]

    # Wartungs-Nodes als Ziel ausschließen
    maintenance = _read_maintenance()
    excluded_maintenance = [n for n in online_others if n.get("node") in maintenance]
    online_others = [n for n in online_others if n.get("node") not in maintenance]

    if not online_others:
        return f"[FEHLER] Keine Ziel-Nodes verfügbar (alle in Wartung oder offline)."

    excl = {str(v.strip()) for v in exclude_vmids.split(",") if v.strip()} if exclude_vmids else set()

    src_vms = [r for r in res
               if r.get("type") in ("qemu", "lxc")
               and r.get("node") == node
               and str(r.get("vmid")) not in excl]

    # Hardware-Pin Erkennung: Configs der VMs holen
    pinned, mobile = [], []
    for v in src_vms:
        try:
            cfg = px(f"/nodes/{node}/{v['type']}/{v['vmid']}/config")
            hw_keys = [k for k in cfg if k.startswith(("hostpci", "usb")) and not k.startswith("usbN")]
            if hw_keys:
                v["_pin_reason"] = ", ".join(hw_keys)
                pinned.append(v)
            else:
                v["_cfg"] = cfg
                mobile.append(v)
        except Exception as e:
            v["_pin_reason"] = f"config-read-error: {e}"
            pinned.append(v)

    # Free RAM pro Ziel-Node (mit 10% Headroom für OS)
    HEADROOM = 0.10
    free_per_target = {}
    for t in online_others:
        running_on_t = sum(r.get("maxmem", 0) for r in res
                           if r.get("type") in ("qemu", "lxc")
                           and r.get("node") == t.get("node")
                           and r.get("status") == "running")
        free = int(t.get("maxmem", 0) * (1 - HEADROOM)) - running_on_t
        free_per_target[t.get("node")] = max(0, free)

    # Greedy: größte VM zuerst, auf Node mit meistem freien RAM
    mobile.sort(key=lambda v: -(v.get("maxmem", 0)))
    plan, unplaceable = [], []
    for v in mobile:
        ram = v.get("maxmem", 0)
        candidates = [t for t, free in free_per_target.items() if free >= ram]
        if not candidates:
            unplaceable.append(v)
            continue
        target = max(candidates, key=lambda t: free_per_target[t])
        plan.append((v, target))
        free_per_target[target] -= ram

    # Output
    lines = [f"=== Evacuate Plan: {node} -> {', '.join(t['node'] for t in online_others)} ==="]
    if excluded_maintenance:
        lines.append(f"Übersprungen (in Wartung): {', '.join(n['node'] for n in excluded_maintenance)}")
    if pinned:
        lines.append(f"\nHardware-pinnt ({len(pinned)}) -- bleibt auf {node}:")
        for v in pinned:
            lines.append(f"  {v.get('vmid')}  {v.get('name','?')[:30]:<30}  Grund: {v.get('_pin_reason')}")
    if not plan and not unplaceable:
        lines.append(f"\nKeine mobilen VMs zu migrieren.")
        return "\n".join(lines)

    lines.append(f"\nMigrationsplan ({len(plan)} VMs):")
    by_target = {}
    for v, t in plan:
        by_target.setdefault(t, []).append(v)
    for t in sorted(by_target):
        ram_sum = sum(v.get("maxmem", 0) for v in by_target[t])
        lines.append(f"\n  -> {t}  ({_fmt_bytes(ram_sum)} insgesamt):")
        for v in by_target[t]:
            lines.append(f"      {v.get('vmid'):>5}  {v.get('name','?')[:30]:<30}  "
                         f"{v.get('type'):<4}  RAM={_fmt_bytes(v.get('maxmem',0))}  "
                         f"status={v.get('status')}")

    if unplaceable:
        lines.append(f"\n⚠ Nicht platzierbar (kein Node hat genug RAM-Headroom):")
        for v in unplaceable:
            lines.append(f"  {v.get('vmid')}  {v.get('name','?')[:30]:<30}  RAM={_fmt_bytes(v.get('maxmem',0))}")

    if dry_run:
        lines.append(f"\n[DRY-RUN] Keine Aktion ausgeführt.")
        lines.append(f"Mit dry_run=False (oder node='{node}+confirm') ausführen.")
        return "\n".join(lines)

    # Ausführen
    lines.append(f"\n=== Migration startet ===")
    for i, (v, target) in enumerate(plan, 1):
        vid = v["vmid"]
        vtype = v["type"]
        vname = v.get("name", "?")
        if vtype == "qemu":
            body = {"target": target, "with-local-disks": 1}
        else:
            body = {"target": target}
            if v.get("status") == "running":
                body["restart"] = 1
        try:
            upid = px(f"/nodes/{node}/{vtype}/{vid}/migrate", method="POST", body=body)
            if wait and isinstance(upid, str):
                result = _wait_task(node, upid, timeout=900)
                lines.append(f"  [{i}/{len(plan)}] {vname} ({vid}) -> {target}: {result}")
            else:
                lines.append(f"  [{i}/{len(plan)}] {vname} ({vid}) -> {target}: gestartet")
        except RuntimeError as e:
            lines.append(f"  [{i}/{len(plan)}] {vname} ({vid}) -> {target}: FEHLER {e}")

    if found_maintenance:
        if dry_run:
            lines.append(f"\n[Plan] Danach Wartungsmodus aktivieren für '{node}'")
        else:
            from datetime import datetime
            state = _read_maintenance()
            state[node] = {
                "since": datetime.now().isoformat(timespec="seconds"),
                "reason": "via evacuate+maintenance",
            }
            _write_maintenance(state)
            lines.append(f"\n✓ Wartungsmodus aktiviert für '{node}'")

    return "\n".join(lines)


@tool(write=True)
def proxmox_restore_by_label(dry_run: bool = True, wait: bool = True) -> str:
    """Proxmox: VMs zurück auf den Node migrieren, der ihrem Label/Tag entspricht.

    Sucht VMs mit einem Tag, der einem realen Node-Namen entspricht (z.B. Tag 'pm16'
    auf VM bedeutet: gehört nach Node pm16). Migriert misplaced VMs iterativ -- macht
    zuerst Moves, die Platz freischaufeln, bevor er Zielnodes füllt. Vermeidet so Deadlocks
    bei Cross-Migrations (pm16 voll mit tag-pm17 und umgekehrt).

    Hardware-pinnte VMs werden übersprungen. Tipp: 'dry_run=False' für echte Migration.

    Args:
        dry_run: True = nur Plan zeigen (default). False = ausführen
        wait: Auf jede Migration warten
    """
    guard = _require_proxmox()
    if guard: return guard

    res = px("/cluster/resources")
    nodes = {r["node"]: r for r in res if r.get("type") == "node"}
    valid_nodes_lower = {n.lower(): n for n in nodes}
    all_vms = [r for r in res if r.get("type") in ("qemu", "lxc")]

    def find_node_label(tags_str: str):
        if not tags_str:
            return None
        for t in str(tags_str).split(";"):
            t = t.strip().lower()
            if t in valid_nodes_lower:
                return valid_nodes_lower[t]  # original case
        return None

    # Misplaced VMs sammeln
    to_move = []
    skipped_pinned = []
    for v in all_vms:
        target = find_node_label(v.get("tags", ""))
        if not target or target == v.get("node"):
            continue
        # Hardware-Pin Check
        try:
            cfg = px(f"/nodes/{v['node']}/{v['type']}/{v['vmid']}/config")
            if any(k.startswith(("hostpci", "usb")) for k in cfg):
                skipped_pinned.append((v["vmid"], v.get("name", "?"), v["node"], target))
                continue
        except Exception:
            continue
        to_move.append({
            "vmid":    v["vmid"],
            "name":    v.get("name", "?"),
            "type":    v["type"],
            "current": v["node"],
            "target":  target,
            "ram":     v.get("maxmem", 0),
            "status":  v.get("status"),
        })

    if not to_move and not skipped_pinned:
        return "Alle VMs sind bereits auf den durch Labels markierten Nodes (oder es gibt keine Labels)."

    # Free RAM pro Node berechnen (mit 10% Headroom)
    HEADROOM = 0.10
    free = {}
    for nname, n in nodes.items():
        used = sum(r.get("maxmem", 0) for r in all_vms
                   if r.get("node") == nname and r.get("status") == "running")
        free[nname] = int(n.get("maxmem", 0) * (1 - HEADROOM)) - used

    # Iterativer Greedy: solange ein Move möglich ist, mache ihn
    plan = []
    remaining = list(to_move)
    while remaining:
        progress = False
        # Erste passende VM nehmen (Reihenfolge ist egal -- Hauptsache irgendein Fortschritt)
        for v in list(remaining):
            if free[v["target"]] >= v["ram"]:
                plan.append(v)
                free[v["current"]] += v["ram"]
                free[v["target"]]  -= v["ram"]
                remaining.remove(v)
                progress = True
                break
        if not progress:
            break

    stuck = remaining

    # Output zusammenstellen
    lines = ["=== Restore-by-Label Plan ==="]
    if skipped_pinned:
        lines.append(f"\nÜbersprungen (Hardware-Pin, {len(skipped_pinned)}):")
        for vmid, name, src, tgt in skipped_pinned:
            lines.append(f"  {vmid}  {name[:30]:<30}  {src} -> {tgt}  (HW-Pin)")

    if plan:
        lines.append(f"\nGeplante Migrationen ({len(plan)}):")
        for v in plan:
            lines.append(f"  {v['vmid']:>5}  {v['name'][:30]:<30}  "
                         f"{v['current']:<8} -> {v['target']:<8}  RAM={_fmt_bytes(v['ram'])}")

    if stuck:
        lines.append(f"\n⚠ Festgefahren ({len(stuck)} VMs) -- Ziel-Node hat keinen Headroom:")
        for v in stuck:
            lines.append(f"  {v['vmid']:>5}  {v['name'][:30]:<30}  "
                         f"{v['current']} -> {v['target']}  "
                         f"benötigt {_fmt_bytes(v['ram'])}, frei nur {_fmt_bytes(max(0,free[v['target']]))}")
        lines.append("\nMögliche Lösung: VM manuell auf dritten Node zwischenparken oder "
                     "RAM-Headroom auf Zielnode schaffen.")

    if not plan and not stuck:
        return "\n".join(lines + ["\nNichts zu tun."])

    if dry_run:
        lines.append(f"\n[DRY-RUN] Keine Aktion. Mit dry_run=False ausführen.")
        return "\n".join(lines)

    if not plan:
        return "\n".join(lines + ["\nKeine ausführbaren Moves -- alle festgefahren."])

    # Ausführen
    lines.append(f"\n=== Migration startet ===")
    for i, v in enumerate(plan, 1):
        if v["type"] == "qemu":
            body = {"target": v["target"], "with-local-disks": 1}
        else:
            body = {"target": v["target"]}
            if v.get("status") == "running":
                body["restart"] = 1
        try:
            upid = px(f"/nodes/{v['current']}/{v['type']}/{v['vmid']}/migrate",
                      method="POST", body=body)
            if wait and isinstance(upid, str):
                result = _wait_task(v["current"], upid, timeout=900)
                lines.append(f"  [{i}/{len(plan)}] {v['name']} ({v['vmid']}) "
                             f"{v['current']}->{v['target']}: {result}")
            else:
                lines.append(f"  [{i}/{len(plan)}] {v['name']} ({v['vmid']}): gestartet")
        except RuntimeError as e:
            lines.append(f"  [{i}/{len(plan)}] {v['name']} ({v['vmid']}): FEHLER {e}")

    return "\n".join(lines)


@tool(write=True)
def proxmox_maintenance(action: str, node: str = "", reason: str = "") -> str:
    """Wartungsmodus für Nodes verwalten.

    Markiert Nodes als 'in Wartung' damit `proxmox_evacuate_node` sie nicht mehr
    als Ziel verwendet. Der Marker wird in %USERPROFILE%\\.ibf_mcp_maintenance.json
    gespeichert (überlebt Restart).

    Args:
        action: 'enable' (oder 'aktivieren'), 'disable' (oder 'deaktivieren'),
                'disable_all', 'status' (oder leer für status)
        node: Node-Name. Bei action=enable/disable Pflicht. Sonst leer.
        reason: Optionaler Grund (Freitext) bei 'enable', wird gespeichert
    """
    guard = _require_proxmox()
    if guard: return guard

    a = (action or "status").strip().lower()
    aliases = {
        "aktivieren": "enable", "aktiviere": "enable", "on": "enable",
        "deaktivieren": "disable", "deaktiviere": "disable", "off": "disable",
        "alle": "disable_all", "all_off": "disable_all", "clear": "disable_all",
    }
    a = aliases.get(a, a)

    state = _read_maintenance()

    if a == "status":
        if not state:
            return "Kein Node im Wartungsmodus."
        lines = ["Nodes im Wartungsmodus:"]
        for n in sorted(state):
            info = state[n]
            lines.append(f"  {n}  seit {info.get('since','?')}  "
                         f"{('-- ' + info['reason']) if info.get('reason') else ''}")
        return "\n".join(lines)

    if a == "disable_all":
        if not state:
            return "Es ist kein Node im Wartungsmodus."
        names = sorted(state.keys())
        _write_maintenance({})
        return f"✓ Wartungsmodus deaktiviert für: {', '.join(names)}"

    if not node:
        return f"[FEHLER] node-Parameter fehlt für action='{a}'"

    if a == "enable":
        from datetime import datetime
        state[node] = {
            "since": datetime.now().isoformat(timespec="seconds"),
            "reason": reason or "manuell aktiviert",
        }
        _write_maintenance(state)
        return (f"✓ Wartungsmodus aktiv für '{node}'.\n"
                f"  evacuate_node nutzt diesen Node nicht mehr als Ziel.\n"
                f"  Nächster Schritt: VMs verschieben mit "
                f"`proxmox_evacuate_node('{node}')` (dry-run zeigt Plan).")

    if a == "disable":
        if node not in state:
            return f"'{node}' ist nicht im Wartungsmodus."
        del state[node]
        _write_maintenance(state)
        return f"✓ Wartungsmodus deaktiviert für '{node}'."

    return (f"[FEHLER] Unbekannte action '{action}'. "
            f"Erlaubt: enable, disable, disable_all, status")


@tool(write=True)
def proxmox_vm_set_config(vmid: str, config: str) -> str:
    """Proxmox: VM/LXC-Konfiguration ändern.

    Args:
        vmid: VMID
        config: Komma-getrennte KEY=VALUE Paare, z.B. 'memory=2048,cores=2'
    """
    guard = _require_proxmox()
    if guard: return guard

    vmid, node, vtype, name, _ = _resolve_vm(vmid)
    body = {}
    for pair in config.split(","):
        pair = pair.strip()
        if "=" not in pair:
            return f"Ungültiges Format: '{pair}'"
        k, v = pair.split("=", 1)
        body[k.strip()] = v.strip()
    px(f"/nodes/{node}/{vtype}/{vmid}/config", method="PUT", body=body)
    return f"{name} (VMID {vmid}): {body} gesetzt."


@tool(write=True)
def proxmox_vm_set_ram(vmid_or_name: str, gb: str, reboot_if_needed: bool = False) -> str:
    """Proxmox: RAM einer VM/LXC ändern (in GB statt MB).

    Konvention: 1 GB = 1000 MB (passt zur Proxmox-Web-UI). Erkennt Hotplug-Status
    und meldet ob ein Reboot nötig ist. Prüft den Balloon-Wert mit.

    Args:
        vmid_or_name: VMID oder Name (Substring, case-insensitive) z.B. 'localai'
        gb: Neuer RAM-Wert in GB. Absolut: '22'. Relativ: '+2' oder '-1.5'.
            Optional Suffix '+reboot' setzt reboot_if_needed=True, z.B. '+2+reboot'
        reboot_if_needed: Bei laufender QEMU-VM ohne Memory-Hotplug automatisch rebooten
    """
    guard = _require_proxmox()
    if guard: return guard

    gb, found_reboot = _extract_flag(str(gb).strip() if gb else "", "reboot")
    if found_reboot:
        reboot_if_needed = True

    vmid, node, vtype, name, status = _resolve_vm(vmid_or_name)

    cfg = px(f"/nodes/{node}/{vtype}/{vmid}/config")
    cur_mb = int(cfg.get("memory", 0))
    cur_gb = cur_mb / 1000.0

    try:
        new_gb = _parse_relative(gb, cur_gb)
    except ValueError:
        return (f"[FEHLER] Ungültige gb-Angabe: '{gb}'. "
                f"Erlaubt: Zahl in GB ohne Einheit, z.B. '22', '+2', '-1.5'")
    if new_gb <= 0:
        return f"[FEHLER] Neuer RAM-Wert <= 0 GB ({new_gb}). Aktuell: {cur_gb} GB"
    new_mb = int(round(new_gb * 1000))

    balloon = int(cfg.get("balloon", 0)) if vtype == "qemu" else 0
    hotplug = (cfg.get("hotplug") or "") if vtype == "qemu" else ""
    can_hotplug = "memory" in hotplug

    px(f"/nodes/{node}/{vtype}/{vmid}/config", method="PUT", body={"memory": new_mb})

    delta_str = f" (Δ {new_gb - cur_gb:+.1f} GB)" if str(gb).strip()[:1] in "+-" else ""
    msgs = [f"{name} (VMID {vmid}): RAM {cur_mb} → {new_mb} MB ({cur_gb:g} → {new_gb:g} GB){delta_str}"]

    if vtype == "qemu" and balloon and balloon > new_mb:
        msgs.append(f"⚠ Balloon ({balloon} MB) > neuer Memory ({new_mb} MB) — sollte auch reduziert werden")
    elif vtype == "qemu" and balloon:
        msgs.append(f"Balloon: {balloon} MB (passt zu neuem Limit)")

    needs_reboot = status == "running" and vtype == "qemu" and not can_hotplug
    if needs_reboot:
        if reboot_if_needed:
            upid = px(f"/nodes/{node}/qemu/{vmid}/status/reboot", method="POST", body={})
            result = _wait_task(node, upid) if isinstance(upid, str) else "OK"
            msgs.append(f"Memory-Hotplug nicht aktiviert → Reboot ausgeführt: {result}")
        else:
            msgs.append("Memory-Hotplug NICHT aktiviert → Reboot von Hand nötig damit Änderung greift")
    elif status == "running" and vtype == "qemu" and can_hotplug:
        msgs.append("Memory-Hotplug aktiviert → Änderung sofort wirksam")
    elif status == "running" and vtype == "lxc":
        msgs.append("LXC: Änderung sofort wirksam (kein Reboot nötig)")

    return "\n".join(msgs)


@tool(write=True)
def proxmox_vm_set_cores(vmid_or_name: str, cores: str, reboot_if_needed: bool = False) -> str:
    """Proxmox: CPU-Cores einer VM/LXC ändern.

    Args:
        vmid_or_name: VMID oder Name (Substring, case-insensitive)
        cores: Neue Core-Anzahl. Absolut: '8'. Relativ zu aktuell: '+2' oder '-1'.
            Optional Suffix '+reboot' setzt reboot_if_needed=True, z.B. '+2+reboot'
        reboot_if_needed: Bei laufender QEMU-VM ohne CPU-Hotplug automatisch rebooten
    """
    guard = _require_proxmox()
    if guard: return guard

    cores, found_reboot = _extract_flag(str(cores).strip() if cores else "", "reboot")
    if found_reboot:
        reboot_if_needed = True

    vmid, node, vtype, name, status = _resolve_vm(vmid_or_name)

    cfg = px(f"/nodes/{node}/{vtype}/{vmid}/config")
    cur_cores = int(cfg.get("cores", 1))

    try:
        new_cores = int(round(_parse_relative(cores, cur_cores)))
    except ValueError:
        return (f"[FEHLER] Ungültige cores-Angabe: '{cores}'. "
                f"Erlaubt: Zahl, z.B. '8', '+2', '-1'")
    if new_cores < 1:
        return f"[FEHLER] Cores < 1 ({new_cores}). Aktuell: {cur_cores}"

    hotplug = (cfg.get("hotplug") or "") if vtype == "qemu" else ""
    can_hotplug = "cpu" in hotplug

    px(f"/nodes/{node}/{vtype}/{vmid}/config", method="PUT", body={"cores": new_cores})

    delta_str = f" (Δ {new_cores - cur_cores:+d})" if str(cores).strip()[:1] in "+-" else ""
    msgs = [f"{name} (VMID {vmid}): Cores {cur_cores} → {new_cores}{delta_str}"]

    needs_reboot = status == "running" and vtype == "qemu" and not can_hotplug
    if needs_reboot:
        if reboot_if_needed:
            upid = px(f"/nodes/{node}/qemu/{vmid}/status/reboot", method="POST", body={})
            result = _wait_task(node, upid) if isinstance(upid, str) else "OK"
            msgs.append(f"CPU-Hotplug nicht aktiviert → Reboot ausgeführt: {result}")
        else:
            msgs.append("CPU-Hotplug NICHT aktiviert → Reboot nötig damit Änderung greift")
    elif status == "running" and vtype == "qemu" and can_hotplug:
        msgs.append("CPU-Hotplug aktiviert → Änderung sofort wirksam")
    elif status == "running" and vtype == "lxc":
        msgs.append("LXC: Änderung sofort wirksam (kein Reboot nötig)")

    return "\n".join(msgs)


@tool(write=True)
def proxmox_vm_set_disk(vmid_or_name: str, gb: str = "", disk: str = "",
                        extend_fs: bool = False) -> str:
    """Proxmox: Festplatte einer VM/LXC vergrößern.

    Hinweis: Proxmox-API unterstützt nur VERGRÖSSERN. Verkleinern ist nicht möglich.
    Wenn nur eine Disk vorhanden ist, wird sie automatisch gewählt. Sonst Liste
    zurückgeben damit der Nutzer sich entscheidet.

    Args:
        vmid_or_name: VMID oder Name (Substring, case-insensitive)
        gb: Neue Größe in GB. Absolut: '64'. Relativ: '+10' (vergrößern).
            Optional Suffix '+fs' setzt extend_fs=True, z.B. '+10+fs' oder '64 +fs'
        disk: Disk-Schlüssel z.B. 'scsi0', 'rootfs', 'sata0'. Leer = automatisch oder Liste
        extend_fs: True = nach erfolgreichem Resize auch Linux-Partition + FS vergrößern
                   (QEMU: Standard-Debian-Layouts via Guest Agent; LXC: passiert automatisch)
    """
    guard = _require_proxmox()
    if guard: return guard

    # +fs Shortcut im gb-Parameter
    gb, found_fs = _extract_flag(str(gb).strip() if gb else "", "fs")
    if found_fs:
        extend_fs = True

    vmid, node, vtype, name, status = _resolve_vm(vmid_or_name)
    cfg = px(f"/nodes/{node}/{vtype}/{vmid}/config")
    disks = _parse_vm_disks(cfg)

    if not disks:
        return f"{name} (VMID {vmid}): keine Disks in der Config gefunden"

    # Nicht-resize-bare Disks ausfiltern (efidisk, tpmstate, unused)
    resizable = {k: v for k, v in disks.items()
                 if not k.startswith(("efidisk", "tpmstate", "unused"))}

    # Disk wählen
    if not disk:
        if len(resizable) == 1:
            disk = list(resizable.keys())[0]
        else:
            lines = [f"{name} (VMID {vmid}) hat mehrere Disks — welche soll geändert werden?"]
            for k in sorted(disks):
                marker = "" if k in resizable else "  (nicht resize-bar)"
                lines.append(f"  {k:<12}  {disks[k]['size_gb']:>6.1f} GB  storage={disks[k]['storage']}{marker}")
            lines.append("")
            lines.append("Erneut aufrufen mit z.B. disk='scsi0', gb='+10' (vergrößern um 10 GB)")
            return "\n".join(lines)

    if disk not in disks:
        return f"{name}: Disk '{disk}' nicht gefunden. Verfügbar: {', '.join(sorted(disks))}"

    cur_gb = disks[disk]["size_gb"]

    # gb leer + extend_fs=False => nur Status zeigen
    if not gb and not extend_fs:
        return f"{name}.{disk}: aktuell {cur_gb:.1f} GB (storage={disks[disk]['storage']}). gb-Parameter fehlt."

    # Default: keine Größenänderung wenn gb leer (nur extend_fs)
    new_gb = cur_gb
    if gb:
        try:
            new_gb = _parse_relative(gb, cur_gb)
        except ValueError:
            return f"[FEHLER] Ungültige gb-Angabe: '{gb}'"

    if new_gb < cur_gb:
        return (f"⚠ Verkleinern wird von Proxmox-API nicht unterstützt "
                f"({cur_gb:.1f} → {new_gb:.1f} GB).\n"
                f"Falls wirklich nötig: VM stoppen und manuell auf der Storage-Ebene shrinken.")

    resize_needed = abs(new_gb - cur_gb) >= 0.01
    if not resize_needed and not extend_fs:
        return f"{name}.{disk}: schon {cur_gb:.1f} GB, keine Änderung nötig"

    if resize_needed:
        new_size_str = f"{int(round(new_gb))}G"
        px(f"/nodes/{node}/{vtype}/{vmid}/resize", method="PUT",
           body={"disk": disk, "size": new_size_str})
        delta_str = f" (Δ {new_gb - cur_gb:+.1f} GB)" if str(gb).strip()[:1] in "+-" else ""
        msgs = [f"{name} (VMID {vmid}).{disk}: "
                f"{cur_gb:.1f} → {new_gb:.1f} GB{delta_str}  storage={disks[disk]['storage']}"]
    else:
        msgs = [f"{name} (VMID {vmid}).{disk}: "
                f"bleibt bei {cur_gb:.1f} GB (kein Disk-Resize) -- nur FS-Extension"]

    # Filesystem-Extension
    if extend_fs:
        if vtype == "lxc":
            msgs.append("LXC: Filesystem wurde von Proxmox automatisch mitvergrößert.")
        elif vtype == "qemu":
            if status != "running":
                msgs.append("⚠ extend_fs übersprungen: VM nicht running. Erst starten, dann extend_fs nochmal aufrufen.")
            elif cfg.get("agent") not in (1, "1", True, "true", "enabled=1"):
                msgs.append("⚠ extend_fs übersprungen: QEMU Guest Agent nicht aktiviert (config: agent=1 fehlt).")
            else:
                import base64 as _b64
                encoded = _b64.b64encode(_FS_EXTEND_SCRIPT.encode()).decode()
                rc, out, err = _qemu_guest_exec(
                    node, vmid,
                    ["bash", "-c", f"echo {encoded} | base64 -d | bash"],
                )
                if rc == 0:
                    last_line = (out.strip().splitlines() or [""])[-1]
                    msgs.append(f"FS extend OK -- {last_line}")
                elif rc == 2:
                    msgs.append("FS extend: Layout nicht erkannt. Manuell im Gast:")
                    msgs.append(f"  Output: {out.strip()}")
                    msgs.append("  Standard-Befehle: growpart /dev/sdX N && resize2fs /dev/sdXN  (oder LVM-Variante)")
                else:
                    msgs.append(f"FS extend FEHLER (rc={rc}):")
                    if out: msgs.append(f"  stdout: {out.strip()[:300]}")
                    if err: msgs.append(f"  stderr: {err.strip()[:300]}")

    return "\n".join(msgs)


@tool(write=True)
def proxmox_vm_snapshot(vmid: str, snapname: str, description: str = "", wait: bool = True) -> str:
    """Proxmox: Snapshot einer VM/LXC erstellen.

    Args:
        vmid: VMID
        snapname: Name des Snapshots
        description: Optionale Beschreibung
        wait: Auf Abschluss warten
    """
    guard = _require_proxmox()
    if guard: return guard

    vmid, node, vtype, name, _ = _resolve_vm(vmid)
    body = {"snapname": snapname}
    if description:
        body["description"] = description
    upid = px(f"/nodes/{node}/{vtype}/{vmid}/snapshot", method="POST", body=body)
    if not wait or not isinstance(upid, str):
        return f"Snapshot '{snapname}' für {name} gestartet."
    return f"Snapshot '{snapname}' für {name}: {_wait_task(node, upid)}"


@tool(write=True)
def proxmox_vm_delete_snapshot(vmid: str, snapname: str, wait: bool = True) -> str:
    """Proxmox: Snapshot löschen.

    Args:
        vmid: VMID
        snapname: Snapshot-Name oder 'all'
        wait: Auf Abschluss warten
    """
    guard = _require_proxmox()
    if guard: return guard

    vmid, node, vtype, name, _ = _resolve_vm(vmid)
    base = f"/nodes/{node}/{vtype}/{vmid}"
    if snapname == "all":
        snaps = [s["name"] for s in (px(f"{base}/snapshot") or []) if s.get("name") != "current"]
        if not snaps:
            return f"Keine Snapshots bei {name}."
        results = []
        for sn in snaps:
            upid = px(f"{base}/snapshot/{sn}", method="DELETE", body={})
            r = _wait_task(node, upid) if wait and isinstance(upid, str) else "gestartet"
            results.append(f"  '{sn}': {r}")
        return f"{name}: Snapshots gelöscht:\n" + "\n".join(results)
    upid = px(f"{base}/snapshot/{snapname}", method="DELETE", body={})
    if not wait or not isinstance(upid, str):
        return f"Snapshot '{snapname}' von {name}: Löschung gestartet."
    return f"Snapshot '{snapname}' von {name}: {_wait_task(node, upid)}"


@tool(write=True)
def proxmox_ssh_run(node: str, cmd: str, timeout: int = 60) -> str:
    """Proxmox: Shell-Befehl auf einem Proxmox-Node ausführen.

    Args:
        node: Ziel-Node (k1-low, k2, k5)
        cmd: Shell-Befehl
        timeout: Timeout in Sekunden (default 60)
    """
    guard = _require_proxmox()
    if guard: return guard

    ip = NODE_IPS.get(node)
    if not ip:
        return f"Unbekannter Node: {node}. Verfügbar: {', '.join(NODE_IPS)}"
    try:
        return _ssh_run_node(ip, cmd, timeout=timeout)
    except Exception as e:
        return f"[FEHLER] {e}"


@tool(write=True)
def proxmox_ssh_apt_upgrade(node: str = "", dist_upgrade: bool = False) -> str:
    """Proxmox: apt update + upgrade auf einem oder allen Nodes.

    Args:
        node: Ziel-Node oder leer für alle Nodes
        dist_upgrade: True für dist-upgrade (Kernel)
    """
    guard = _require_proxmox()
    if guard: return guard

    cmd = (
        "DEBIAN_FRONTEND=noninteractive apt-get update -q 2>&1 && "
        f"DEBIAN_FRONTEND=noninteractive apt-get {'dist-upgrade' if dist_upgrade else 'upgrade'} -y 2>&1"
    )
    nodes = [node] if node else list(NODE_IPS.keys())
    results = []
    for n in nodes:
        ip = NODE_IPS.get(n)
        if not ip:
            results.append(f"[{n}] Unbekannter Node")
            continue
        results.append(f"\n=== {n} ===")
        try:
            out = _ssh_run_node(ip, cmd, timeout=300)
            for line in out.splitlines():
                if any(kw in line for kw in ("upgraded,", "kept back", "up to date", "FEHLER", "Error")):
                    results.append(f"  {line.strip()}")
        except Exception as e:
            results.append(f"  [FEHLER] {e}")
    return "\n".join(results)


# ---------------------------------------------------------------------------
# GRAYLOG TOOLS
# ---------------------------------------------------------------------------

@tool()
def graylog_system_status() -> str:
    """Graylog: Systemstatus, Indexer-Health, aktive Notifications."""
    guard = _require_graylog()
    if guard: return guard

    info  = gl("/system")
    notif = gl("/system/notifications")
    idx   = gl("/system/indexer/cluster/health")

    lines = [
        f"Graylog {info.get('version','?')}  Cluster-ID: {info.get('cluster_id','?')[:8]}...",
        f"Timezone: {info.get('timezone','?')}  Hostname: {info.get('hostname','?')}",
        "",
        f"Indexer: {idx.get('status','?').upper()}  Shards: "
        f"active={idx.get('active_shards','?')} unassigned={idx.get('unassigned_shards','?')}",
        "",
    ]

    total_notif = notif.get("total", 0)
    if total_notif:
        lines.append(f"Notifications ({total_notif}):")
        for n in (notif.get("notifications") or [])[:5]:
            lines.append(f"  [{n.get('severity','?').upper()}] {n.get('type','?')}: {n.get('description','')[:100]}")
    else:
        lines.append("Notifications: keine")

    try:
        nodes = gl("/system/cluster/nodes")
        node_list = nodes.get("nodes") or {}
        if isinstance(node_list, list):
            lines.append(f"\nCluster-Nodes ({len(node_list)}):")
            for nd in node_list[:5]:
                lines.append(f"  {nd.get('short_node_id','?')}  {nd.get('transport_address','?')}  "
                             f"{'Leader' if nd.get('is_leader') else ''}")
        elif isinstance(node_list, dict):
            lines.append(f"\nCluster-Nodes ({len(node_list)}):")
            for nid, nd in list(node_list.items())[:5]:
                lines.append(f"  {nd.get('short_node_id','?')}  {nd.get('transport_address','?')}  "
                             f"{'Leader' if nd.get('is_leader') else ''}")
    except Exception as e:
        lines.append(f"\nCluster-Nodes: nicht verfügbar ({e})")

    return "\n".join(lines)


@tool()
def graylog_list_streams() -> str:
    """Graylog: Alle konfigurierten Streams."""
    guard = _require_graylog()
    if guard: return guard

    data = gl("/streams")
    streams = data.get("streams", [])
    lines = [f"{'ID':<36}  {'Titel':<40}  Regeln"]
    lines.append("-" * 85)
    for s in sorted(streams, key=lambda x: x.get("title", "")):
        lines.append(
            f"{s.get('id','?'):<36}  {s.get('title','?')[:40]:<40}  "
            f"{s.get('rules',[]) and len(s['rules'])} Regeln"
        )
    return "\n".join(lines)


@tool(description=_doc(
    full="""Graylog: Log-Nachrichten suchen.

    Args:
        query: Graylog-Query-Syntax z.B. 'srcip:10.10.40.1 AND action:deny'
        last: Zeitfenster z.B. '15m', '2h', '7d' (default 1h)
        limit: Max. Treffer (default 20, max 200)
        source: Host-Filter z.B. 'gw'
        stream_id: Stream-ID einschränken
        fields: Komma-getrennte Felder z.B. 'timestamp,source,message'
    """,
    compact=("Graylog Log-Suche. query=Lucene, last='15m'/'7d', limit≤200, "
             "source/stream_id/fields optional."),
    minimal="Graylog suchen.",
    _key="graylog_search_messages",
))
def graylog_search_messages(
    query: str = "*",
    last: str = "1h",
    limit: int = 20,
    source: str = "",
    stream_id: str = "",
    fields: str = "",
) -> str:
    guard = _require_graylog()
    if guard: return guard

    if source:
        query = f"source:{source} AND ({query})"

    range_secs = _parse_range(last)
    limit = min(limit, 200)

    params = {
        "query":  query,
        "range":  range_secs,
        "limit":  limit,
        "sort":   "timestamp:desc",
    }
    if stream_id:
        params["filter"] = f"streams:{stream_id}"

    data = gl("/search/universal/relative", params=params)
    messages = data.get("messages", [])
    total    = data.get("total_results", 0)

    if not messages:
        return f"Keine Treffer für '{query}' (letzte {last})."

    show_fields = [f.strip() for f in fields.split(",")] if fields else []

    lines = [f"{total} Treffer für '{query}' (letzte {last}) -- zeige {len(messages)}:\n"]
    for m in messages:
        msg = m.get("message", {})
        ts_iso = msg.get("timestamp")
        ts = "--"
        if ts_iso:
            try:
                ts = dt.datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts = ts_iso[:19]
        src = msg.get("source", "?")

        if show_fields:
            vals = "  ".join(f"{f}={str(msg.get(f,''))[:80]}" for f in show_fields)
            lines.append(f"[{ts}] {src}  {vals}")
        else:
            text = str(msg.get("message") or msg.get("full_message") or "")[:200]
            lines.append(f"[{ts}] {src}  {text}")

    return "\n".join(lines)


@tool(description=_doc(
    full="""Graylog: Trefferzahl zählen (schnell, kein Content).

    Args:
        query: Graylog-Query-Syntax
        last: Zeitfenster (default 1h)
        source: Host-Filter
    """,
    compact="Graylog Trefferzahl zählen. query, last, source optional.",
    minimal="Graylog count.",
    _key="graylog_count_messages",
))
def graylog_count_messages(query: str = "*", last: str = "1h", source: str = "") -> str:
    guard = _require_graylog()
    if guard: return guard

    if source:
        query = f"source:{source} AND ({query})"
    data = gl("/search/universal/relative", params={
        "query": query, "range": _parse_range(last), "limit": 0,
    })
    total = data.get("total_results", 0)
    return f"{total:,} Treffer für '{query}' in den letzten {last}."


@tool(description=_doc(
    full="""Graylog: Top-N Werte eines Feldes (Aggregation).

    Args:
        field: Feldname z.B. 'srcip', 'source', 'action'
        query: Filter-Query
        last: Zeitfenster (default 24h)
        size: Anzahl Top-Werte (default 15)
        source: Host-Filter
    """,
    compact="Graylog Top-N Aggregation. field=srcip|user|action|... last/size optional.",
    minimal="Graylog Top-N.",
    _key="graylog_top_values",
))
def graylog_top_values(
    field: str,
    query: str = "*",
    last: str = "24h",
    size: int = 15,
    source: str = "",
) -> str:
    guard = _require_graylog()
    if guard: return guard

    if source:
        query = f"source:{source} AND ({query})"

    terms_data = gl("/search/universal/relative/terms", params={
        "query":  query,
        "range":  _parse_range(last),
        "field":  field,
        "size":   size,
    })
    terms = terms_data.get("terms", {})
    if not terms:
        return f"Keine Werte für Feld '{field}' gefunden."

    total = sum(terms.values())
    lines = [f"Top {len(terms)} Werte von '{field}' (letzte {last}, Query: {query[:60]}):\n"]
    for i, (val, cnt) in enumerate(sorted(terms.items(), key=lambda x: -x[1])[:size], 1):
        pct = cnt / total * 100 if total else 0
        lines.append(f"  {i:>3}.  {str(val):<40}  {cnt:>8,}  ({pct:.1f}%)")
    lines.append(f"\n  Gesamt: {total:,}")
    return "\n".join(lines)


@tool()
def graylog_indexer_health() -> str:
    """Graylog: OpenSearch/Elasticsearch Backend-Health."""
    guard = _require_graylog()
    if guard: return guard

    health  = gl("/system/indexer/cluster/health")
    indices = gl("/system/indexer/indices/open")

    lines = [
        f"Status: {health.get('status','?').upper()}",
        f"Shards: active={health.get('active_shards','?')}  "
        f"primary={health.get('active_primary_shards','?')}  "
        f"unassigned={health.get('unassigned_shards','?')}",
        "",
        f"Offene Indices ({len(indices.get('indices', {}))}):",
    ]
    for name, idx in list((indices.get("indices") or {}).items())[:10]:
        size = idx.get("primary_size_bytes", 0)
        size_gb = size / 1024**3
        docs = idx.get("documents", {})
        lines.append(
            f"  {name:<35}  {size_gb:.1f} GB  docs={docs.get('count',0):,}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FORTIGATE TOOLS
# ---------------------------------------------------------------------------

@tool(description=_doc(
    full="FortiGate: System-Status (Modell, Firmware, Hostname, Uptime, Lizenz).",
    compact="FortiGate-System-Status.",
    minimal="FortiGate-Status.",
    _key="fortigate_status",
))
def fortigate_status() -> str:
    guard = _require_fortigate()
    if guard: return guard
    ok, out = _forti_run("get system status")
    return out if ok else f"[FEHLER] {out}"


@tool(write=True)
def fortigate_run(cmd: str, timeout: int = 30) -> str:
    """FortiGate: Beliebigen CLI-Befehl ausführen (audit-User, read-only).

    Sicherheit: vor Ausführung explizit beim Nutzer bestätigen.

    Args:
        cmd: FortiGate-Befehl, z.B. 'get system interface', 'show firewall policy'
        timeout: Sekunden bis Abbruch (default 30)
    """
    guard = _require_fortigate()
    if guard: return guard
    ok, out = _forti_run(cmd, timeout=timeout)
    if not ok:
        return f"[FEHLER] {out}"
    return out


@tool()
def fortigate_list_interfaces() -> str:
    """FortiGate: Network-Interfaces (Status, IP, Modus)."""
    guard = _require_fortigate()
    if guard: return guard
    ok, out = _forti_run("get system interface")
    return out if ok else f"[FEHLER] {out}"


@tool()
def fortigate_list_policies() -> str:
    """FortiGate: Firewall-Policies (alle aktiven Regeln, gekürzte Liste)."""
    guard = _require_fortigate()
    if guard: return guard
    ok, out = _forti_run("show firewall policy")
    return out if ok else f"[FEHLER] {out}"


@tool()
def fortigate_list_sessions(filter_src: str = "", filter_dst: str = "", limit: int = 30) -> str:
    """FortiGate: Aktive Sessions (mit optionalem IP-Filter).

    Args:
        filter_src: Quell-IP filtern, z.B. '10.10.40.225'
        filter_dst: Ziel-IP filtern
        limit: Max. Anzahl Sessions im Output (default 30)
    """
    guard = _require_fortigate()
    if guard: return guard

    cmds = ["config global", "diagnose sys session filter clear"]
    if filter_src:
        cmds.append(f"diagnose sys session filter src {filter_src}")
    if filter_dst:
        cmds.append(f"diagnose sys session filter dst {filter_dst}")
    cmds.append("diagnose sys session list")

    ok, out = _forti_run_shell(cmds, timeout=90)
    if not ok:
        return f"[FEHLER] {out}"

    lines = out.splitlines()
    if len(lines) > limit * 20:
        out = "\n".join(lines[: limit * 20]) + f"\n... ({len(lines)} Zeilen, gekürzt auf {limit*20})"
    return out


# Mikrotik-Tools: ausgelagert in mikrotik-mcp.py

# ---------------------------------------------------------------------------
# FORTIGATE TOOLS (Fortsetzung)
# ---------------------------------------------------------------------------

_FORTI_CAT_ALIAS = {
    "attack":      "utm-ips",
    "virus":       "utm-virus",
    "webfilter":   "utm-webfilter",
    "voip":        "utm-voip",
    "app-ctrl":    "utm-app-ctrl",
    "anomaly":     "utm-anomaly",
    "dns":         "utm-dns",
    "dlp":         "utm-dlp",
    "waf":         "utm-waf",
    "ssh":         "utm-ssh",
    "ssl":         "utm-ssl",
    "emailfilter": "utm-emailfilter",
    "icap":        "utm-icap",
    "sctp-filter": "utm-sctp-filter",
    "file-filter": "utm-file-filter",
}

_FORTI_LEVELS = ["emergency", "alert", "critical", "error",
                 "warning", "notice", "information", "debug"]


@tool(description=_doc(
    full="""FortiGate: Log-Einträge mit Zeit-, Severity- und LogID-Filter.

    Args:
        category: 'traffic' | 'event' | UTM-Kürzel: 'attack' (=utm-ips),
                  'virus' (=utm-virus), 'webfilter', 'voip', 'app-ctrl', 'anomaly',
                  'dns', 'dlp', 'waf', 'ssh', 'ssl', 'emailfilter', 'icap',
                  'sctp-filter', 'file-filter'. Direkte 'utm-*'-Namen ebenfalls OK.
        count: max. Anzahl angezeigter Einträge (default 50). Bei Multi-Day-Filter
               wird intern mehr geholt und client-seitig getrimmt.
        since: Anfang des Zeitfensters. 'today', 'yesterday', '1h', '30m', '2d',
               'YYYY-MM-DD', 'YYYY-MM-DD HH:MM'. Leer = ohne Untergrenze.
        until: Ende des Zeitfensters. 'today', 'now', 'YYYY-MM-DD',
               'YYYY-MM-DD HH:MM'. Leer = jetzt (wenn since gesetzt).
        min_level: minimale Severity. Werte: emergency, alert, critical, error,
                   warning, notice, information, debug. Wirkt inklusiv (höhere
                   Severities werden mitgenommen). Leer = keine Filterung.
        logid: konkrete LogID (z.B. '0100032002' für Admin-Login-Failed).

    Beispiele:
        fortigate_show_log(category='event', since='today', min_level='alert')
        fortigate_show_log(category='event', since='yesterday', until='today')
        fortigate_show_log(category='event', since='1h', logid='0100032002')
    """,
    compact=("FG-Logs filtern. category=traffic|event|attack|virus|webfilter|... "
             "since/until='today'/'1h'/'YYYY-MM-DD'. min_level=alert|error|warning."
             " logid für gezielte ID. count default 50."),
    minimal="FortiGate-Logs filtern.",
    _key="fortigate_show_log",
))
def fortigate_show_log(
    category: str = "traffic",
    count: int = 50,
    since: str = "",
    until: str = "",
    min_level: str = "",
    logid: str = "",
) -> str:
    guard = _require_fortigate()
    if guard: return guard

    server_cat = _FORTI_CAT_ALIAS.get(category.lower(), category)

    try:
        t_since = _parse_when(since, end=False)
        t_until = _parse_when(until, end=True) if until else (
            dt.datetime.now() if t_since else None
        )
    except ValueError as e:
        return f"[FEHLER] {e}"

    if t_since and t_until and t_since > t_until:
        return f"[FEHLER] since ({t_since}) liegt nach until ({t_until})."

    levels_csv = ""
    if min_level:
        ml = min_level.lower().strip()
        if ml not in _FORTI_LEVELS:
            return (f"[FEHLER] Unbekanntes min_level={min_level!r}. "
                    f"Erlaubt: {', '.join(_FORTI_LEVELS)}")
        levels_csv = ",".join(_FORTI_LEVELS[: _FORTI_LEVELS.index(ml) + 1])

    # FortiOS v7.2: nur `field date YYYY-MM-DD` als Server-Time-Filter verfügbar
    # (kein `start-time`/`end-time`, kein `reset`). Intra-Day- bzw. Multi-Day-
    # Ranges werden client-seitig nachgefiltert. Der Shell-Channel ist pro Call
    # frisch (`invoke_shell()`), darum ist auch ohne reset kein Filter-Carry-over.
    server_date = None
    client_filter = False
    if t_since and t_until and t_since.date() == t_until.date():
        full_day = (t_since.time() == dt.time(0, 0, 0)
                    and t_until.time() >= dt.time(23, 59, 0))
        if full_day:
            server_date = t_since.strftime("%Y-%m-%d")
        else:
            server_date = t_since.strftime("%Y-%m-%d")
            client_filter = True
    elif t_since or t_until:
        client_filter = True

    view_lines = count if not client_filter else max(count * 20, 1000)
    view_lines = min(view_lines, 9999)

    cmds = [f"execute log filter category {server_cat}"]
    if server_date:
        cmds.append(f"execute log filter field date {server_date}")
    if levels_csv:
        cmds.append(f"execute log filter field level {levels_csv}")
    if logid:
        cmds.append(f"execute log filter field logid {logid}")
    cmds.append(f"execute log filter view-lines {view_lines}")
    cmds.append("execute log display")

    ok, raw = _forti_run_shell(cmds, timeout=120)
    if not ok:
        return f"[FEHLER] {raw}"

    if client_filter:
        return _forti_filter_log_by_time(raw, t_since, t_until, count)
    return raw


# ---------------------------------------------------------------------------
# DASHBOARD (projekte/dashboard/morning.py)
# ---------------------------------------------------------------------------
#
# Dünne MCP-Wrapper über das Morning-Dashboard. Kein eigener Auth-Guard --
# die Lib-Funktionen prüfen Keyring-Tokens selbst, fehlerhafte Sektionen
# werden im Render als "[error]" ausgewiesen, ohne den ganzen Run zu killen.
#
# Constraint: MCP-Tool-Calls haben ~60 s Timeout. `dashboard_morning(all)`
# kann je nach Graylog-Latenz 25-40 s brauchen; `as_completed(timeout=50)`
# fungiert als harte Bremse, unfertige Sektionen werden mit [TIMEOUT]
# gekennzeichnet.

_DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "projekte" / "dashboard"


def _dashboard_import():
    """Lädt morning.py + lib lazily. Cached über Modul-Globals."""
    if "_dashboard_loaded" in globals():
        return globals()["_dashboard_loaded"]
    if str(_DASHBOARD_DIR) not in sys.path:
        sys.path.insert(0, str(_DASHBOARD_DIR))
    import morning as _morning
    from lib import render as _render
    from lib import snapshot as _snapshot
    globals()["_dashboard_loaded"] = (_morning, _render, _snapshot)
    return _morning, _render, _snapshot


_DASHBOARD_PRESETS = {
    "all":      ["security", "infra", "backups", "network", "cloud", "logs"],
    "critical": ["security", "logs", "network"],
    "fast":     ["security", "cloud", "logs"],   # ohne Proxmox/FG-Probe
}


@tool(description=_doc(
    full="""IBF Morning Dashboard -- Status-Übersicht für die schnelle Triage.

    Sektionen liefern Status (OK/WARN/ALERT) plus Trend (heute / gestern bis
    jetzt / 7d-Schnitt). Output ist der gleiche ASCII-Block wie aus
    `python projekte/dashboard/morning.py --no-color`.

    Args:
        sections: 'all' (alle 6 Sektionen, default), 'critical'
                  (security+logs+network, schnell), 'fast' (ohne Proxmox/FG-Probe),
                  oder Komma-Liste z.B. 'security,logs'.
        trend:    aktuell informativ -- die Collectors holen Trend-Werte immer
                  mit, Flag ist Stub für künftige `--no-trend`-Optimierung.
        timeout_s: harter Cut für Sektion-Sammeln. Default 50 (knapp unterm
                   60 s MCP-Limit). Sektionen die nicht fertig werden,
                   erscheinen als '[TIMEOUT]'.

    Tipp: Bei Timeout-Risiko `sections='critical'` oder einzelne Sektionen
    via `dashboard_section()` aufrufen.
    """,
    compact=("Status-Triage Security/Infra/Backups/Network/Cloud/Logs. "
             "sections=all|critical|fast|<csv>. timeout_s=50."),
    minimal="Status-Übersicht.",
    _key="dashboard_morning",
))
def dashboard_morning(sections: str = "all", trend: bool = True,
                      timeout_s: int = 50) -> str:
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
    morning, render, _ = _dashboard_import()

    if sections in _DASHBOARD_PRESETS:
        labels = list(_DASHBOARD_PRESETS[sections])
    else:
        labels = [s.strip() for s in sections.split(",") if s.strip()]
        unknown = [l for l in labels if l not in morning.COLLECTORS]
        if unknown:
            return (f"[FEHLER] Unbekannte sections: {unknown}. "
                    f"Verfügbar: {sorted(morning.COLLECTORS.keys())} "
                    f"oder Presets: {sorted(_DASHBOARD_PRESETS.keys())}")

    out_sections: dict = {}
    with ThreadPoolExecutor(max_workers=max(len(labels), 1)) as ex:
        futures = {ex.submit(morning._run_collector, l): l for l in labels}
        try:
            for f in as_completed(futures, timeout=timeout_s):
                label, obj, render_fn, _, err = f.result()
                if obj is None:
                    err_msg = f"{type(err).__name__}: {err}" if err else "no data"
                    out_sections[label] = (None, err_msg)
                else:
                    out_sections[label] = (obj, render_fn)
        except TimeoutError:
            done_labels = set(out_sections.keys())
            for fut, lbl in futures.items():
                if lbl in done_labels:
                    continue
                fut.cancel()
                out_sections[lbl] = (None, f"[TIMEOUT] Sektion >{timeout_s}s nicht fertig")

    # Reihenfolge der Original-Labels beibehalten + TIMEOUT-Marker für fehlende
    ordered: dict = {}
    for l in labels:
        if l in out_sections:
            ordered[l] = out_sections[l]
        else:
            ordered[l] = (None, "[NOT-RUN] Sektion gar nicht gestartet")
    return render.render_ascii(sections=ordered, color=False)


@tool(description=_doc(
    full=("Eine einzelne Dashboard-Sektion (gezielte Inspektion). "
          "name: security|infra|backups|network|cloud|logs. timeout_s: 50s default."),
    compact="Eine Dashboard-Sektion. name=security|infra|backups|network|cloud|logs.",
    minimal="Dashboard-Sektion abrufen.",
    _key="dashboard_section",
))
def dashboard_section(name: str, timeout_s: int = 50) -> str:
    return dashboard_morning(sections=name, timeout_s=timeout_s)


@tool(description=_doc(
    full="""Historische Snapshot-Werte aus Graylog (Variante D).

    Liest die GELF-Snapshots, die von `morning.py` nach jedem Run unter
    `app:ibf-dashboard` abgelegt werden.

    Args:
        metric:  z.B. 'admin_login_failed_today', 'failed_tasks_today',
                 'msg_count_today'. Format: '<metric>_<rangetype>'.
        days:    Anzahl Tage rückwärts (default 7).
        section: optional auf Sektion filtern ('security', 'logs', ...).
    """,
    compact=("Snapshot-Historie aus Graylog (app:ibf-dashboard). "
             "metric z.B. 'admin_login_failed_today'. days/section optional."),
    minimal="Snapshot-Historie aus Graylog.",
    _key="dashboard_history",
))
def dashboard_history(metric: str, days: int = 7, section: str = "") -> str:
    _dashboard_import()  # path-setup für lib-Imports
    import datetime as _dt
    from lib import graylog_api as _gl

    parts = [f'_app:ibf-dashboard', f'_metric_name:{metric}']
    if section:
        parts.append(f'_metric_section:{section}')
    query = " AND ".join(parts)

    until = _dt.datetime.now()
    since = until - _dt.timedelta(days=days)
    try:
        msgs = _gl.messages(
            query, since=since, until=until,
            limit=200,
            fields="timestamp,metric_value,dashboard_run_id,metric_section,host",
        )
    except RuntimeError as e:
        return f"[FEHLER] Graylog-Abfrage fehlgeschlagen: {e}"

    if not msgs:
        return (f"[INFO] Keine Snapshots für metric={metric!r} "
                f"in den letzten {days} Tagen. (Snapshot-Push läuft erst seit "
                f"dem ersten morning.py-Run vom 2026-05-05.)")

    lines = [f"# {len(msgs)} Snapshot-Werte für '{metric}' "
             f"in den letzten {days}d:",
             f"  {'Run-ID':21s}  {'Wert':>12s}  Section  Host"]
    for m in msgs:
        rid = (m.get("dashboard_run_id") or "?")[:19]
        val = m.get("metric_value", "?")
        sec = m.get("metric_section") or "?"
        host = m.get("host") or "?"
        lines.append(f"  {rid:21s}  {str(val):>12s}  {sec:8s}  {host}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        script = str(Path(__file__).resolve())

        if cmd == "--install":
            import subprocess
            # alte einzelne MCPs entfernen, dann ibf als user installieren
            for old in ("proxmox", "graylog", "ibf"):
                for scope in ("local", "user"):
                    subprocess.run(
                        ["claude", "mcp", "remove", old, "-s", scope],
                        capture_output=True, text=True,
                    )
            result = subprocess.run(
                ["claude", "mcp", "add", "--scope", "user", "ibf", "--", "python", script],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                print(f"[OK] Combined MCP 'ibf' registriert (user scope):\n  {result.stdout.strip()}")
                print(f"\nAlte separate MCPs (proxmox, graylog) entfernt.")
                print(f"Prüfen mit:  claude mcp list")
            else:
                print(f"[FEHLER] {result.stderr.strip()}")
                sys.exit(1)

        elif cmd == "--uninstall":
            import subprocess
            for scope in ("local", "user"):
                subprocess.run(
                    ["claude", "mcp", "remove", "ibf", "-s", scope],
                    capture_output=True, text=True,
                )
            print("[OK] Combined MCP entfernt.")

        elif cmd == "--http":
            # HTTP-Transport (streamable-http). Für Container/Remote-Deployments.
            host = os.environ.get("IBF_MCP_HTTP_HOST", "0.0.0.0")
            port = int(os.environ.get("IBF_MCP_HTTP_PORT", "8080"))
            extra = sys.argv[2:]
            i = 0
            while i < len(extra):
                if extra[i] == "--host" and i + 1 < len(extra):
                    host = extra[i + 1]; i += 2
                elif extra[i] == "--port" and i + 1 < len(extra):
                    port = int(extra[i + 1]); i += 2
                else:
                    print(f"[FEHLER] unbekanntes Argument: {extra[i]}", file=sys.stderr)
                    sys.exit(1)
            mcp.settings.host = host
            mcp.settings.port = port
            # Default-DNS-Rebinding-Whitelist erlaubt nur 127.0.0.1/localhost.
            # Bei Bind auf 0.0.0.0 (interner LAN-Server) auflockern -- der Schutz
            # ist für Browser-basierte Clients gedacht, nicht für interne MCP-Setups.
            try:
                mcp.settings.transport_security.enable_dns_rebinding_protection = False
            except Exception:
                pass
            print(f"[ibf-mcp] HTTP transport on http://{host}:{port}"
                  f"{mcp.settings.streamable_http_path}", file=sys.stderr)
            mcp.run(transport="streamable-http")

        elif cmd == "--test":
            print("Teste Proxmox + Graylog ...\n")
            print("--- PROXMOX ---")
            try:
                _auth_proxmox._write_token(_auth_proxmox._token_file)
                print(proxmox_cluster_status())
            except Exception as e:
                print(f"[FEHLER] {e}")
            print("\n--- GRAYLOG ---")
            try:
                _auth_graylog._write_token(_auth_graylog._token_file)
                print(graylog_system_status())
            except Exception as e:
                print(f"[FEHLER] {e}")

        else:
            print(f"Verwendung:")
            print(f"  python {Path(script).name} --install     # Combined MCP registrieren")
            print(f"  python {Path(script).name} --uninstall   # Entfernen")
            print(f"  python {Path(script).name} --test        # Beide Verbindungen testen")
            print(f"  python {Path(script).name} --http [--host H --port P]  # HTTP-Transport")
            print(f"  python {Path(script).name}               # MCP-Server starten (stdio)")
            print(f"")
            print(f"Passwörter (gelten für beide Domains -- Auth bleibt aber getrennt):")
            print(f"  python ibf_mcp_auth.py --set-global-password    # ein PW für alles")
            print(f"  python ibf_mcp_auth.py --set-password proxmox   # nur proxmox")
            print(f"  python ibf_mcp_auth.py --set-password graylog   # nur graylog")
            sys.exit(1)
    else:
        mcp.run()
