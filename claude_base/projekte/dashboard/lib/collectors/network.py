"""Network-Sektion: WAN-Status + IPSec-Tunnel-Status.

Zwei Datenquellen kombiniert (T2-Implementation 2026-05-05):

1. **FortiGate-CLI (Ground-Truth)**: `get system interface physical` für
   Interface-Link-Status, `diagnose vpn ike gateway list` für etablierte
   VPN-Tunnel mit IKE-/IPsec-SA-Counters. Liefert die echte
   Hardware-/Daemon-Sicht.

2. **Externer TCP-Probe**: TCP-Connect auf Port 443 jeder bekannten
   WAN-IP -- ergänzend, weil FG-Interface-`up` nicht garantiert
   externe Reachability (NAT/Routing-Probleme).

3. **Graylog-Phase-1-Errors**: zeigt Tunnel-Verhandlungen die GAR NICHT
   etabliert werden (z.B. `pflach_peer` mit Konfig-Mismatch -- erscheint
   nicht in `vpn ike gateway list`, nur in den Error-Logs).

Wenn die FortiGate aus dem aktuellen Netz nicht erreichbar ist (z.B. aus
dem Personal-Netz ohne VPN), fällt die Sektion sauber auf Graylog +
TCP-Probe zurück.
"""

from __future__ import annotations

import dataclasses
import socket
from typing import Iterable

from .. import fortigate_api as fg
from .. import graylog_api as gl
from .. import trend


# Bekannte Tunnel-Namen aus FG-Logs der letzten Sessions -- dient als
# Referenz für Phase-1-Error-Aggregation aus Graylog (auch für Tunnel die
# in `vpn ike gateway list` nicht auftauchen weil sie nie etablieren).
KNOWN_TUNNELS: list[str] = [
    "web1-hz-gw-gre",
    "web1-hz-gw4-gre",
    "web1-hz-gw5-gre",
]


@dataclasses.dataclass
class WanInterface:
    port: str               # 'port1', 'port17', ...
    ip: str
    fg_status: str          # 'up' / 'down' / 'unknown' (FG meldet)
    external_ok: bool       # TCP-Probe :443 von hier
    mode: str = ""          # 'static' / 'pppoe'

    @property
    def status_label(self) -> str:
        if self.fg_status == "up" and self.external_ok:
            return "OK"
        if self.fg_status == "up" and not self.external_ok:
            return "WARN"   # Interface up, von außen nicht erreichbar
        if self.fg_status == "down":
            return "ALERT"
        return "?"


@dataclasses.dataclass
class TunnelStatus:
    name: str
    established: bool
    last_status: str = ""
    age_s: int | None = None
    phase1_errors_today: int = 0


@dataclasses.dataclass
class NetworkSection:
    fg_available: bool
    wan: list[WanInterface]
    tunnels: list[TunnelStatus]
    extra_tunnel_errors: dict[str, int]    # Tunnel die in den Logs sind, aber nicht in der FG-Liste
    total_phase1_errors_today: int = 0     # Summe über alle Tunnel/User
    top_phase1_users: list[tuple[str, int]] = dataclasses.field(default_factory=list)
    overall_status: str = "OK"
    note: str = ""


def _tcp_probe(ip: str, port: int = 443, timeout: float = 2.0) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _wan_from_fg_interfaces(ifaces) -> list[WanInterface]:
    """Filter: WAN = Interface das eine echte (nicht 0.0.0.0) IP hat UND
    Mode 'pppoe' oder 'static' -- und Name beginnt mit 'port' (kein VLAN/loopback)."""
    wan: list[WanInterface] = []
    for i in ifaces:
        if not i.has_ipv4:
            continue
        if not i.name.startswith("port"):
            continue
        # Ausschluss privater/spezial Ranges (RFC1918 + link-local + loopback)
        if _is_private_or_special_ipv4(i.ip):
            continue
        wan.append(WanInterface(
            port=i.name, ip=i.ip, fg_status=i.status,
            external_ok=False, mode=i.mode,
        ))
    return wan


def _is_private_or_special_ipv4(ip: str) -> bool:
    """RFC1918 + link-local + loopback. Frühere Logik filterte alle 172.x.x.x
    raus -- inkl. öffentlicher 172.0/172.32+. Diese Variante prüft korrekt."""
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if a == 10:                              # 10.0.0.0/8
        return True
    if a == 172 and 16 <= b <= 31:           # 172.16.0.0/12
        return True
    if a == 192 and b == 168:                # 192.168.0.0/16
        return True
    if a == 169 and b == 254:                # link-local
        return True
    if a == 127:                             # loopback
        return True
    return False


def _tunnel_phase1_errors(name: str, since, until) -> int:
    try:
        return gl.count(f'logid:0101037124 AND vpntunnel:"{name}"',
                        since=since, until=until)
    except RuntimeError:
        return -1


def _all_tunnels_with_errors(since, until, top_n: int = 5) -> dict[str, int]:
    """Findet Tunnel-Namen die heute in den Phase-1-Error-Logs auftauchen.
    Hilft bei nicht-etablierten Verhandlungen (`pflach_peer` etc.)."""
    try:
        top = gl.top_values(
            query='logid:0101037124',
            field="vpntunnel", since=since, until=until,
            size=top_n, fetch_cap=1000,
        )
    except RuntimeError:
        return {}
    return dict(top)


def collect() -> NetworkSection:
    s_today, u_today = trend.range_today()

    fg_avail = False
    ifaces: list = []
    gws: list = []
    note = ""
    try:
        if fg.is_available():
            fg_avail = True
            ifaces = fg.interface_physical()
            gws = fg.vpn_ike_gateways()
        else:
            note = "FortiGate nicht erreichbar (anderes Netz/VPN inaktiv) -- Fallback auf TCP-Probe + Graylog"
    except Exception as e:
        note = f"FortiGate-Fehler: {e} -- Fallback auf TCP-Probe + Graylog"

    # WAN-Interfaces: aus FG-Liste, mit External-Probe ergänzt
    if fg_avail:
        wan = _wan_from_fg_interfaces(ifaces)
        for w in wan:
            w.external_ok = _tcp_probe(w.ip)
    else:
        # Fallback: feste WAN-IP-Liste (historisch), nur externe Sicht
        wan = [WanInterface(port="?", ip=ip, fg_status="unknown",
                            external_ok=_tcp_probe(ip))
               for ip in ("80.120.87.250", "88.116.6.118",
                          "185.124.145.91", "185.124.145.79")]

    # Tunnel-Status: etablierte aus FG + Errors aus Graylog
    fg_tunnel_names: set[str] = set()
    tunnels: list[TunnelStatus] = []
    for gw in gws:
        fg_tunnel_names.add(gw.name)
        errs = _tunnel_phase1_errors(gw.name, s_today, u_today)
        tunnels.append(TunnelStatus(
            name=gw.name,
            established=gw.is_up,
            last_status=gw.last_status,
            age_s=gw.created_ago_s,
            phase1_errors_today=max(errs, 0),
        ))
    # Auch known-tunnel die nicht in FG-Liste sind (dann definitiv NICHT etabliert)
    for tn in KNOWN_TUNNELS:
        if tn not in fg_tunnel_names:
            errs = _tunnel_phase1_errors(tn, s_today, u_today)
            tunnels.append(TunnelStatus(
                name=tn, established=False,
                phase1_errors_today=max(errs, 0),
                last_status="not in IKE-Gateway-List",
            ))

    # Tunnels die nur über Logs sichtbar sind (Verhandlung schlägt fehl,
    # `pflach_peer` etc.)
    tunnel_errs = _all_tunnels_with_errors(s_today, u_today)
    extra = {n: c for n, c in tunnel_errs.items()
             if n and n != "N/A" and n not in fg_tunnel_names
             and n not in KNOWN_TUNNELS}

    # Total Phase-1-Errors heute (sieht auch nicht-zuordenbare Verhandlungen
    # wie `pflach_peer` mit vpntunnel="N/A") und Top-User dahinter
    try:
        total_phase1 = gl.count('logid:0101037124', since=s_today, until=u_today)
    except RuntimeError:
        total_phase1 = -1
    top_users: list[tuple[str, int]] = []
    if total_phase1 > 100:
        try:
            top_users = gl.top_values(
                query='logid:0101037124', field="user",
                since=s_today, until=u_today, size=5, fetch_cap=2000,
            )
        except RuntimeError:
            pass

    # Status-Aggregation
    wan_alert = any(w.status_label == "ALERT" for w in wan)
    wan_warn = any(w.status_label == "WARN" for w in wan)
    tunnel_down = any(not t.established for t in tunnels)
    high_err = (total_phase1 > 1000
                or any(e > 1000 for e in extra.values())
                or any(t.phase1_errors_today > 1000 for t in tunnels))
    if wan_alert or high_err:
        status = "ALERT"
    elif wan_warn or tunnel_down or total_phase1 > 100:
        status = "WARN"
    else:
        status = "OK"

    return NetworkSection(
        fg_available=fg_avail,
        wan=wan,
        tunnels=tunnels,
        extra_tunnel_errors=extra,
        total_phase1_errors_today=max(total_phase1, 0),
        top_phase1_users=top_users,
        overall_status=status,
        note=note,
    )


def render_text(sec: NetworkSection) -> str:
    out = ["=== NETWORK ==="]
    out.append(f"  Status: {sec.overall_status}"
               + ("  (FG-CLI verfügbar)" if sec.fg_available else "  (Fallback-Modus)"))
    if sec.note:
        out.append(f"  {sec.note}")
    out.append("")
    out.append("  WAN-Interfaces:")
    out.append(f"    {'Port':6s}  {'IP':18s}  {'Mode':8s}  FG-Link  Probe :443  Status")
    for w in sec.wan:
        probe = "✓" if w.external_ok else "✖"
        out.append(f"    {w.port:6s}  {w.ip:18s}  {w.mode:8s}  "
                   f"{w.fg_status:7s}  {probe:^10s}  {w.status_label}")
    out.append("")
    if sec.tunnels:
        out.append("  IPSec-Tunnel (etabliert + bekannt):")
        out.append(f"    {'Name':25s}  {'Up?':4s}  {'Alter':>10s}  Phase-1-Errs heute")
        for t in sec.tunnels:
            up = "✓" if t.established else "✖"
            age = f"{t.age_s}s" if t.age_s else "-"
            errs = f"{t.phase1_errors_today}" if t.phase1_errors_today >= 0 else "?"
            flag = " ⚠" if t.phase1_errors_today > 1000 else ""
            out.append(f"    {t.name:25s}  {up:4s}  {age:>10s}  {errs}{flag}")
    if sec.extra_tunnel_errors:
        out.append("")
        out.append("  Verhandlungen die NICHT etablieren (nur Errors in Logs):")
        for name, n in sec.extra_tunnel_errors.items():
            out.append(f"    {name:25s}  {n} Phase-1-Errors heute")
    if sec.total_phase1_errors_today > 100:
        out.append("")
        out.append(f"  IPSec Phase-1-Errors heute (gesamt): "
                   f"{sec.total_phase1_errors_today}")
        if sec.top_phase1_users:
            out.append("    Top-User der gescheiterten Verhandlungen:")
            for u, n in sec.top_phase1_users[:5]:
                out.append(f"      {u:25s}  {n}")
    return "\n".join(out)


if __name__ == "__main__":
    print(render_text(collect()))
