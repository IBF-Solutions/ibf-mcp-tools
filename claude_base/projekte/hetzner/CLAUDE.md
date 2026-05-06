# Hetzner Cloud Subprojekt

MCP-Server für Hetzner Cloud (Server, Volumes, Firewalls, SSH-Keys, Images, etc.).

## Herkunft

Code adaptiert von **[dkruyt/mcp-hetzner](https://github.com/dkruyt/mcp-hetzner)** (MIT-Lizenz).
Originaler Code im `mcp_hetzner/server.py` des Repos. Unsere Änderungen:

- **Auth-Gate** über `ibf_mcp_auth.Auth("hetzner")` -- "buddy hetzner on" zum Aktivieren
- **Lazy-Token-Loading** aus `HCLOUD_TOKEN`-env-var → Keyring (`hetzner/ibf`) → `.env`-Fallback
- **Silent-unavailable**: ohne Token verhalten sich alle Tools wie nicht existent (`__UNAVAILABLE__`)
- **Standalone MCP**: eigener Server `hetzner` (analog `mikrotik`), kein Bestandteil von `ibf-mcp.py`
- **CLI-Wrapper**: `--install`/`--uninstall`/`--set-token`/`--set-password`/`--test`

## Setup

1. **Hetzner-Token besorgen**: Hetzner Cloud Console → Project → Security → API Tokens → New Token (Read+Write)
2. **Token speichern**:
   ```bash
   python tools/hetzner-mcp.py --set-token
   ```
   speichert ihn in Windows Credential Manager unter `hetzner/ibf`.
3. **MCP registrieren**:
   ```bash
   python tools/hetzner-mcp.py --install
   ```
4. **Optional: separates Hetzner-Passwort** für die Auth-Domain `hetzner` (sonst greift das globale IBF-Passwort):
   ```bash
   python tools/hetzner-mcp.py --set-password
   ```
5. **Testen**:
   ```bash
   python tools/hetzner-mcp.py --test
   ```

## Tools (~30)

| Bereich | Tools |
|---|---|
| **Server** | `list_servers`, `get_server`, `create_server`, `delete_server`, `power_on`, `power_off`, `reboot` |
| **Volume** | `list_volumes`, `get_volume`, `create_volume`, `delete_volume`, `attach_volume`, `detach_volume`, `resize_volume` |
| **Firewall** | `list_firewalls`, `get_firewall`, `create_firewall`, `update_firewall`, `delete_firewall`, `set_firewall_rules`, `apply_firewall_to_resources`, `remove_firewall_from_resources` |
| **SSH-Key** | `list_ssh_keys`, `get_ssh_key`, `create_ssh_key`, `update_ssh_key`, `delete_ssh_key` |
| **Info** | `list_images`, `list_server_types`, `list_locations` |

Aufruf in Claude Code: `mcp__hetzner__list_servers`, `mcp__hetzner__create_server`, etc.
Aktivierung: `buddy hetzner on` (Domain `hetzner`).

## Sicherheit

- **Destruktive Tools** (`delete_*`, `power_off`, `set_firewall_rules`) erfordern explizite
  Bestätigung im Chat (Memory-Regel `feedback_destructive_confirm.md`).
- Token bleibt in Keyring, niemals im Source-Code.
- API-Operationen sind **read+write** wenn Token Schreibrechte hat -- in Hetzner Console
  notfalls einen Read-only-Token anlegen.

## TODO

- [ ] **T1** [P2] Read-only-Modus -- Flag oder env-var um destruktive Tools komplett zu deaktivieren,
      z.B. `HCLOUD_READONLY=1` oder Tool-Whitelist.
- [ ] **T2** [P3] Upstream-Sync -- regelmäßig prüfen ob `dkruyt/mcp-hetzner` neue Tools hat
      (`compute_floating_ip`, `Networks`, `Load Balancers`, `Certificates`?), Diff übernehmen.
- [ ] **T3** [P3] Hetzner-StorageBox separat -- die Storage-Boxen (`k1e-storbox*` aus
      proxmox-Cluster) sind eine ANDERE API (`hetzner-storage-box-api`), nicht in `hcloud`-SDK
      enthalten. Falls Management gewünscht: separate Tools dazu.

- [ ] **T4** [P3] [server-status] Server `SystemMonitor-Agent.only` ist
      `off` — gefunden im Dashboard-Run 2026-05-05 10:35 (10/11 Hetzner-
      Server running). Klären: bewusst gestoppt (z.B. nicht mehr genutzt,
      sollte gelöscht werden) oder versehentlich? Wenn obsolet:
      `mcp__hetzner__delete_server` nach Bestätigung. Wenn produktiv:
      `power_on`.
