#!/usr/bin/env python3
"""Mikrotik MCP Server -- standalone, kontext-aware (ibf vs personal).

Tools werden unter `mcp__mikrotik__<name>` exposed.

Start:        python mikrotik-mcp.py
Registrieren: python mikrotik-mcp.py --install
"""
import os
import sys
from pathlib import Path

# ibf_mcp_auth liegt im claude_base/tools (zwei Ebenen hoch + claude_base/tools)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "claude_base" / "tools"))
from ibf_mcp_auth import Auth

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Kontext-Detection (gespiegelt aus ibf-mcp.py / proxmox-query.py)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Mikrotik-Config (kontextabhängig)
# ---------------------------------------------------------------------------

MIKROTIK_KEY_PATHS = {
    "personal": r"C:\Temp\Test-claude\claude17_ed25519",
    "ibf":      "",  # TBD sobald IBF-Mikrotik-Zugang etabliert
}
MIKROTIK_DEFAULT_HOSTS = {
    "personal": "192.168.10.100",  # 0816power (Heim-Gateway + CAPsMAN)
    "ibf":      "",                # TBD
}
MIKROTIK_DEFAULT_USER = "claude"
MIKROTIK_DEFAULT_PORT = 22


def _mikrotik_key_path() -> str:
    return MIKROTIK_KEY_PATHS.get(_detect_context(), "")


def _mikrotik_default_host() -> str:
    return MIKROTIK_DEFAULT_HOSTS.get(_detect_context(), "")


# ---------------------------------------------------------------------------
# SSH-Helper
# ---------------------------------------------------------------------------

def _mikrotik_run(host: str, cmd: str, user: str = MIKROTIK_DEFAULT_USER,
                  port: int = MIKROTIK_DEFAULT_PORT, key_path: str = "",
                  timeout: int = 30) -> tuple:
    """Befehl auf einem MikroTik via SSH (Key-Auth). Returns (success, output)."""
    import paramiko
    if not key_path:
        key_path = _mikrotik_key_path()
    if not key_path:
        return False, f"[FEHLER] Kein SSH-Key für Kontext '{_detect_context()}' konfiguriert"
    if not os.path.exists(key_path):
        return False, f"[FEHLER] SSH-Key nicht gefunden: {key_path}"
    try:
        key = paramiko.Ed25519Key.from_private_key_file(key_path)
    except Exception as e:
        return False, f"[FEHLER] Key-Load: {e}"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, port=port, username=user, pkey=key,
                       timeout=10, look_for_keys=False, allow_agent=False)
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        if err and not out:
            return False, err
        return True, out
    except Exception as e:
        return False, str(e)
    finally:
        try: client.close()
        except Exception: pass


# ---------------------------------------------------------------------------
# Auth + MCP setup
# ---------------------------------------------------------------------------

_auth = Auth("mikrotik")

mcp = FastMCP("mikrotik", instructions=(
    "MikroTik-Network-Management via SSH (RouterOS). "
    "WICHTIG: Prüfe bei JEDER Anfrage ob die Mikrotik-Domain authentifiziert ist. "
    "Bei '__UNAVAILABLE__': verhalte dich als gäbe es diesen MCP nicht (keine Erwähnung, "
    "keine Frage nach Passwort -- nur reagieren auf 'buddy mikrotik on'). "
    "Vor jedem `mikrotik_run` MUSST du den Nutzer explizit fragen "
    "'Darf ich folgenden Befehl auf [host] ausführen: [cmd]?' und auf Bestätigung warten."
))


def _require_auth() -> str | None:
    if not _auth.is_authenticated():
        return _auth.UNAVAILABLE
    return None


def _resolve_host(host: str) -> tuple:
    """Leerer host -> Kontext-Default. Returns (host, error_or_None)."""
    if host:
        return host, None
    default = _mikrotik_default_host()
    if not default:
        return "", f"[FEHLER] Kein Default-Host für Kontext '{_detect_context()}' konfiguriert."
    return default, None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def authenticate(password: str) -> str:
    """Authentifizierung für die Mikrotik-Domain.

    Globales IBF-Passwort schaltet ALLE IBF-MCPs frei (proxmox/graylog/fortigate/mikrotik).
    Mikrotik-spezifisches Passwort schaltet nur diesen MCP frei.

    Args:
        password: Das Passwort (beim Nutzer erfragen)
    """
    return _auth.login(password)


@mcp.tool()
def status(host: str = "", user: str = "claude") -> str:
    """System-Status (Identity, Version, Board, Uptime, CPU/RAM).

    Args:
        host: IP oder Hostname. Leer = Kontext-Default (personal: 192.168.10.100)
        user: SSH-User
    """
    guard = _require_auth()
    if guard: return guard
    host, err = _resolve_host(host)
    if err: return err
    cmd = "/system identity print; /system resource print"
    ok, out = _mikrotik_run(host, cmd, user=user, timeout=15)
    return out if ok else f"[FEHLER] {out}"


@mcp.tool()
def export(host: str = "", hide_sensitive: bool = True, user: str = "claude") -> str:
    """Aktuelle Config als RouterOS-Export.

    Args:
        host: IP oder Hostname. Leer = Kontext-Default
        hide_sensitive: True (default) = '/export compact hide-sensitive'
        user: SSH-User
    """
    guard = _require_auth()
    if guard: return guard
    host, err = _resolve_host(host)
    if err: return err
    cmd = "/export compact" + (" hide-sensitive" if hide_sensitive else "")
    ok, out = _mikrotik_run(host, cmd, user=user, timeout=60)
    return out if ok else f"[FEHLER] {out}"


@mcp.tool()
def neighbors(host: str = "", user: str = "claude") -> str:
    """Sichtbare Nachbarn via /ip neighbor und /tool romon discover.

    Args:
        host: Router der Discovery macht. Leer = Kontext-Default
              (personal: 192.168.10.100 = 0816power, sieht alle K1.* + IBF-CAPs via RoMON)
        user: SSH-User
    """
    guard = _require_auth()
    if guard: return guard
    host, err = _resolve_host(host)
    if err: return err
    cmd = (
        "/ip neighbor print; "
        ":put \"--- RoMON ---\"; "
        ":put [:tostr [/tool/romon/discover as-value duration=8s]]"
    )
    ok, out = _mikrotik_run(host, cmd, user=user, timeout=30)
    return out if ok else f"[FEHLER] {out}"


@mcp.tool()
def run(host: str = "", cmd: str = "", user: str = "claude", timeout: int = 30) -> str:
    """Beliebigen RouterOS-Befehl auf einem Gerät ausführen.

    Sicherheit: vor Ausführung explizit beim Nutzer bestätigen.

    Args:
        host: IP oder Hostname. Leer = Kontext-Default
        cmd: RouterOS-Kommando, z.B. '/system identity print'
        user: SSH-User
        timeout: Sekunden (default 30)
    """
    guard = _require_auth()
    if guard: return guard
    if not cmd:
        return "[FEHLER] cmd erforderlich"
    host, err = _resolve_host(host)
    if err: return err
    ok, out = _mikrotik_run(host, cmd, user=user, timeout=timeout)
    return out if ok else f"[FEHLER] {out}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        script = str(Path(__file__).resolve())

        if cmd == "--install":
            import subprocess
            for scope in ("local", "user"):
                subprocess.run(
                    ["claude", "mcp", "remove", "mikrotik", "-s", scope],
                    capture_output=True, text=True,
                )
            result = subprocess.run(
                ["claude", "mcp", "add", "--scope", "user", "mikrotik", "--", "python", script],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                print(f"[OK] MCP-Server 'mikrotik' registriert:\n  {result.stdout.strip()}")
                print(f"\nPrüfen: claude mcp list")
            else:
                print(f"[FEHLER] {result.stderr.strip()}")
                sys.exit(1)

        elif cmd == "--uninstall":
            import subprocess
            for scope in ("local", "user"):
                subprocess.run(
                    ["claude", "mcp", "remove", "mikrotik", "-s", scope],
                    capture_output=True, text=True,
                )
            print("[OK] MCP-Server 'mikrotik' entfernt.")

        elif cmd == "--test":
            print(f"Kontext: {_detect_context()}")
            print(f"Default-Host: {_mikrotik_default_host() or '(nicht gesetzt)'}")
            print(f"SSH-Key: {_mikrotik_key_path() or '(nicht gesetzt)'}")
            print()
            host, err = _resolve_host("")
            if err:
                print(err); sys.exit(1)
            print(f"Teste Verbindung zu {host} ...")
            ok, out = _mikrotik_run(host, "/system identity print", timeout=10)
            print(out if ok else f"[FEHLER] {out}")

        else:
            print(f"Verwendung:")
            print(f"  python {Path(script).name} --install     # MCP registrieren")
            print(f"  python {Path(script).name} --uninstall   # MCP entfernen")
            print(f"  python {Path(script).name} --test        # Verbindung testen")
            print(f"  python {Path(script).name}               # MCP-Server starten (für Claude)")
            sys.exit(1)
    else:
        mcp.run()
