"""Gemeinsames Auth-Modul für alle IBF-MCPs.

Passwörter im Keyring:
  ibf-mcp-global   / password  --> authentifiziert alle MCPs
  ibf-mcp-<name>   / password  --> authentifiziert nur diesen MCP

Auth-State: %TEMP%/ibf_mcp_<name>.auth  (Datei enthält Ablaufzeit als Unix-TS)
            %TEMP%/ibf_mcp_global.auth  (global)

Verwendung in einem MCP:
    from ibf_mcp_auth import Auth
    auth = Auth("proxmox")

    # In jedem Tool:
    if not auth.is_authenticated():
        return auth.UNAVAILABLE

    # authenticate-Tool:
    @mcp.tool()
    def authenticate(password: str) -> str:
        return auth.login(password)
"""

import os
import time
from pathlib import Path

_TEMP = Path(os.environ.get("TEMP", "/tmp"))
_KEYRING_PREFIX  = "ibf-mcp"
_KEYRING_GLOBAL  = "ibf-mcp-global"
_KEYRING_USER    = "password"
_SESSION_HOURS   = 8  # Auth-Token gültig für 8 Stunden


class Auth:
    UNAVAILABLE = "__UNAVAILABLE__"

    def __init__(self, mcp_name: str):
        self.name        = mcp_name
        self._token_file = _TEMP / f"ibf_mcp_{mcp_name}.auth"
        self._global_file = _TEMP / "ibf_mcp_global.auth"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_authenticated(self) -> bool:
        return self._valid(self._token_file) or self._valid(self._global_file)

    def login(self, password: str) -> str:
        """Passwort prüfen und bei Erfolg Auth-Token schreiben."""
        global_pw  = self._keyring_get(_KEYRING_GLOBAL)
        per_mcp_pw = self._keyring_get(f"{_KEYRING_PREFIX}-{self.name}")

        if not global_pw and not per_mcp_pw:
            return (
                f"[FEHLER] Kein Passwort für MCP '{self.name}' gesetzt.\n"
                f"Setzen mit:\n"
                f"  python proxmox-mcp.py --set-password          (nur {self.name})\n"
                f"  python ibf_mcp_auth.py --set-global-password  (alle MCPs)"
            )

        if global_pw and password == global_pw:
            self._write_token(self._global_file)
            return f"Global authentifiziert — alle IBF-MCPs sind jetzt für {_SESSION_HOURS}h verfügbar."

        if per_mcp_pw and password == per_mcp_pw:
            self._write_token(self._token_file)
            return f"Authentifiziert für MCP '{self.name}' ({_SESSION_HOURS}h)."

        return "Falsches Passwort."

    def logout(self):
        """Session-Token löschen."""
        for f in (self._token_file, self._global_file):
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass

    def set_password(self, password: str, scope: str = "local"):
        """Passwort im Keyring speichern. scope: 'local' oder 'global'."""
        import keyring
        service = _KEYRING_GLOBAL if scope == "global" else f"{_KEYRING_PREFIX}-{self.name}"
        keyring.set_password(service, _KEYRING_USER, password)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _valid(path: Path) -> bool:
        try:
            ts = float(path.read_text().strip())
            return time.time() < ts
        except Exception:
            return False

    @staticmethod
    def _write_token(path: Path):
        expiry = time.time() + _SESSION_HOURS * 3600
        path.write_text(str(expiry))

    @staticmethod
    def _keyring_get(service: str):
        try:
            import keyring
            return keyring.get_password(service, _KEYRING_USER)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# CLI: Passwörter verwalten
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys, getpass

    def _set_pw(service: str, label: str):
        import keyring
        pw = getpass.getpass(f"{label}: ").strip()
        if not pw:
            print("[FEHLER] Kein Passwort eingegeben.")
            sys.exit(1)
        pw2 = getpass.getpass("Wiederholen: ").strip()
        if pw != pw2:
            print("[FEHLER] Passwörter stimmen nicht überein.")
            sys.exit(1)
        keyring.set_password(service, _KEYRING_USER, pw)
        print(f"[OK] Passwort gespeichert unter: {service}/{_KEYRING_USER}")

    if len(sys.argv) < 2:
        print("Verwendung:")
        print("  python ibf_mcp_auth.py --set-global-password         Passwort für alle IBF-MCPs")
        print("  python ibf_mcp_auth.py --set-password <mcp-name>     Passwort für einen MCP")
        print("  python ibf_mcp_auth.py --clear                       Alle aktiven Sessions löschen")
        print("  python ibf_mcp_auth.py --clear-mcp <mcp-name>        Eine MCP-Session beenden")
        print("  python ibf_mcp_auth.py --status                      Auth-Status aller MCPs anzeigen")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "--set-global-password":
        _set_pw(_KEYRING_GLOBAL, "Globales IBF-MCP-Passwort")

    elif cmd == "--set-password":
        if len(sys.argv) < 3:
            print("[FEHLER] MCP-Name fehlt.  Beispiel: --set-password proxmox")
            sys.exit(1)
        name = sys.argv[2]
        _set_pw(f"{_KEYRING_PREFIX}-{name}", f"Passwort für MCP '{name}'")

    elif cmd == "--clear":
        for f in _TEMP.glob("ibf_mcp_*.auth"):
            f.unlink()
            print(f"  Gelöscht: {f.name}")
        print("[OK] Alle Sessions beendet.")

    elif cmd == "--clear-mcp":
        if len(sys.argv) < 3:
            print("[FEHLER] MCP-Name fehlt. Beispiel: --clear-mcp graylog")
            sys.exit(1)
        name = sys.argv[2]
        target = _TEMP / f"ibf_mcp_{name}.auth"
        if target.exists():
            target.unlink()
            print(f"[OK] Session '{name}' beendet.")
        else:
            print(f"  Session '{name}' war nicht aktiv.")
        # Globaler Token bleibt unangetastet -- der wirkt für alle

    elif cmd == "--status":
        global_ok = Auth._valid(_TEMP / "ibf_mcp_global.auth")
        print(f"  Global: {'✓ aktiv' if global_ok else '✗ nicht authentifiziert'}")
        for f in sorted(_TEMP.glob("ibf_mcp_*.auth")):
            if f.name == "ibf_mcp_global.auth":
                continue
            name = f.stem.replace("ibf_mcp_", "")
            ok = Auth._valid(f)
            try:
                expiry = float(f.read_text())
                remaining = max(0, int(expiry - time.time()))
                rem_str = f"{remaining // 3600}h {(remaining % 3600) // 60}m verbleibend"
            except Exception:
                rem_str = ""
            print(f"  {name}: {'✓ aktiv' if ok else '✗ abgelaufen'}  {rem_str}")
    else:
        print(f"Unbekannter Parameter: {cmd}")
        sys.exit(1)
