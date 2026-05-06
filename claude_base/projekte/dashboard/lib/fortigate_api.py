"""FortiGate-CLI-Client fürs Dashboard (Read-only via SSH).

Liefert Ground-Truth-Daten von der FortiGate, die durch Graylog-Aggregation
nicht oder nur indirekt erreichbar sind:

- Interface-Status (Link up/down, IP, Mode -- echte Hardware-Sicht)
- IPSec-IKE-Gateway-Status (welche Tunnel sind aktuell etabliert,
  IKE/IPsec-SA-Counters, last-established-Alter)

Auth: hardcoded `audit/audit` (read-only Account, FortiGate-spezifisch,
kein Geheimnis -- analog zu `claude_base/tools/ibf-mcp.py`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import paramiko

FORTI_HOST = "10.10.40.1"
FORTI_PORT = 10022
FORTI_USER = "audit"
FORTI_PASS = "audit"
TIMEOUT_S = 5


def _connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(FORTI_HOST, port=FORTI_PORT, username=FORTI_USER, password=FORTI_PASS,
              timeout=TIMEOUT_S, banner_timeout=TIMEOUT_S,
              look_for_keys=False, allow_agent=False)
    return c


def is_available() -> bool:
    """Schnelle Reachability/Auth-Probe -- True wenn FG erreichbar UND Login klappt."""
    try:
        c = _connect()
        c.close()
        return True
    except Exception:
        return False


def _run(cmd: str, timeout: int = 15) -> str:
    c = _connect()
    try:
        _, stdout, _ = c.exec_command(cmd, timeout=timeout)
        return stdout.read().decode("utf-8", errors="ignore")
    finally:
        try:
            c.close()
        except Exception:
            pass


@dataclass
class Interface:
    name: str
    status: str = "?"        # 'up' / 'down' / '?'
    ip: str = ""
    mode: str = ""           # 'static' / 'dhcp' / 'pppoe' / ...
    speed: str = ""

    @property
    def is_up(self) -> bool:
        return self.status.lower() == "up"

    @property
    def has_ipv4(self) -> bool:
        return bool(self.ip) and not self.ip.startswith("0.0.0.0")


@dataclass
class IkeGateway:
    name: str
    interface: str = ""
    addr_local: str = ""
    addr_peer: str = ""
    created_ago_s: int | None = None
    ike_established: tuple[int, int] | None = None       # (current, total)
    ipsec_established: tuple[int, int] | None = None
    last_status: str = ""                                 # z.B. 'established 580s ago'

    @property
    def is_up(self) -> bool:
        if self.ike_established and self.ike_established[0] > 0:
            return bool(self.ipsec_established and self.ipsec_established[0] > 0)
        return False


# ---- Parser --------------------------------------------------------------

_IF_HEADER = re.compile(r'^\s*==\[([^\]]+)\]')
_IF_KEY    = re.compile(r'^\s+(\w+):\s+(.+?)\s*$')
_AGO_RX    = re.compile(r'(\d+)s\s+ago')


def interface_physical(raw: str | None = None) -> list[Interface]:
    """Parst `get system interface physical`. Wenn raw=None: liest live von FG."""
    if raw is None:
        raw = _run("get system interface physical")
    out: list[Interface] = []
    cur: Interface | None = None
    for line in raw.splitlines():
        m = _IF_HEADER.match(line)
        if m:
            if cur is not None:
                out.append(cur)
            cur = Interface(name=m.group(1))
            continue
        if cur is None:
            continue
        m = _IF_KEY.match(line)
        if not m:
            continue
        k, v = m.group(1).lower(), m.group(2)
        if k == "status":
            cur.status = v.lower().strip()
        elif k == "ip":
            cur.ip = v.split()[0] if v else ""
        elif k == "mode":
            cur.mode = v.strip()
        elif k == "speed":
            cur.speed = v.strip()
    if cur is not None:
        out.append(cur)
    return out


def vpn_ike_gateways(raw: str | None = None) -> list[IkeGateway]:
    """Parst `diagnose vpn ike gateway list`."""
    if raw is None:
        raw = _run("diagnose vpn ike gateway list")
    out: list[IkeGateway] = []
    cur: IkeGateway | None = None
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("name:"):
            if cur is not None:
                out.append(cur)
            cur = IkeGateway(name=s.split(":", 1)[1].strip())
            continue
        if cur is None:
            continue
        if s.startswith("interface:"):
            cur.interface = s.split(":", 1)[1].strip()
        elif s.startswith("addr:"):
            # 'addr: 80.120.87.250:500 -> 116.203.6.112:500'
            parts = s.split(":", 1)[1].strip().split("->")
            if len(parts) == 2:
                cur.addr_local = parts[0].strip()
                cur.addr_peer = parts[1].strip()
        elif s.startswith("created:"):
            m = _AGO_RX.search(s)
            if m:
                cur.created_ago_s = int(m.group(1))
        elif s.startswith("IKE SA:"):
            m = re.search(r'established\s+(\d+)/(\d+)', s)
            if m:
                cur.ike_established = (int(m.group(1)), int(m.group(2)))
        elif s.startswith("IPsec SA:"):
            m = re.search(r'established\s+(\d+)/(\d+)', s)
            if m:
                cur.ipsec_established = (int(m.group(1)), int(m.group(2)))
        elif s.startswith("status:") and not cur.last_status:
            cur.last_status = s.split(":", 1)[1].strip()
    if cur is not None:
        out.append(cur)
    return out
