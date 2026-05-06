# MASTER-PROMPT FÜR CLAUDE CODE
1. Sprache: Deutsch, Name: Philipp
2. Tonfall: Sachlich, keine Lobhudeleien, keine Füllfloskeln.
3. Kontinuität: Vorhandenen Code niemals ohne explizite Rückfrage entfernen.

Ausführliche Begründung und das vollständige TODO-Format (`T#`) in
`CLAUDE-rules.md` -- die wird nur on-demand gelesen.


# TODO-System (T-System) -- Kurzfassung

Format: `- [ ] **Tn** [Pn] [kategorie] Titel — Detail  [#XXXX]`
(Tn = laufende Nummer ohne führende Null, Pn = P1/P2/P3, default P2.)

Speicherort: pro Subprojekt im jeweiligen `CLAUDE.md`, eigene Nummerierung.
Vollständige Regeln und Sonderfälle in `CLAUDE-rules.md`.


# Kontextsystem

Es existieren zwei Arbeits-Kontexte mit getrennten Credentials, Naming-Konventionen und
Cluster-Topologien. Tools (Proxmox-MCP, proxmox-query.py, graylog-query.py) erkennen den
Kontext automatisch und wählen den passenden Keyring-Eintrag.

## Erkennung

Automatisch via lokaler Netzwerk-Interface-IP:

| Lokale IP beginnt mit | Kontext | Bedeutung |
|---|---|---|
| in `10.10.40.0/21` (40.x – 47.x) | `ibf` | IBF-Firmennetz (DNS-Suffix `int.ibf-solutions.com`) |
| alles andere | `personal` | Heim-/Persönliches Netz (z.B. `k1.local` = `192.168.10.0/23`) |

Implementation: `socket.gethostbyname_ex(socket.gethostname())[2]` -- kein DNS-Call,
kein UDP-Probe (8.8.8.8 ist von außerhalb teilweise geblockt).

> **✓ Bestätigte Architektur-Entscheidung (2026-05-05)**: `_detect_context()` ist
> der Standard-Mechanismus für kontext-aware Tools. Neue Tools verwenden ihn statt
> eigene Heuristiken zu erfinden -- Konsistenz vor Kreativität. Aktuell genutzt von
> (Pfade relativ ab `C:\Temp\claude\ibf\`):
> `claude_base/tools/ibf-mcp.py`, `claude_base/projekte/proxmox/tools/proxmox-query.py`,
> `claude_base/projekte/graylog/tools/graylog-query.py`, `mikrotik/tools/mikrotik-mcp.py`.

## Was unterscheidet sich pro Kontext?

| Aspekt | `ibf` | `personal` |
|---|---|---|
| Proxmox-Host-Naming | `pm##` (z.B. pm16, pm17, pm18) | `k####` (k1-low, k2, k5) |
| Proxmox-Token-Keyring | `proxmox-ibf` / `ibf` | `proxmox-personal` / `ibf` |
| Erwartetes Projekt-Pfadfragment | `\ibf\` | `\personal\` |
| Graylog-Zugriff | `gld.ibf-solutions.com` (über VPN auch personal) | dito |
| FortiGate (10.10.40.1) | direkt erreichbar | nur über VPN |
| Mikrotik-Workspace | `C:\Temp\claude\ibf\mikrotik\` | `C:\Temp\claude\personal\subprojects\mikrotik\` |
| Mikrotik-Default-Gateway | TBD (10.10.40.0/23) | `192.168.10.100` (0816power) |
| Mikrotik-SSH-Key | TBD | `C:\Temp\Test-claude\claude17_ed25519` |

## Strikte Trennung -- aber nur für Daten, nicht für Code

Die Kontexte sind **bei Daten** hart isoliert, **bei Code/Skripten** ausdrücklich nicht.

### DATEN (hart getrennt -- nie über die Grenze)

| Datenart | IBF | Personal |
|---|---|---|
| API-Tokens / SSH-Keys / Passwörter | nur in IBF-Keyring/-Tools | nur in Personal-Keyring/-Tools |
| Geräte-Inventare (IPs, MACs, Hostnames) | IBF-Devices (pm##, IBF-CAPs, FortiGate, ...) | Heim-Devices (k####, K1.*, 0816power) |
| Konfigurations-Exports (`.rsc`, `.conf`) | IBF-Geräte | Heim-Geräte |
| Audit-Reports / Logs | IBF-Befund | Heim-Befund |
| TODOs mit identifizierbaren Details | je Kontext | je Kontext |
| `.env`-Dateien | je Kontext | je Kontext |

**Git-Regel: Personal-Daten NIE in IBF-Git, IBF-Daten NIE in Personal-Git.**

### CODE / SKRIPTE / RECIPES (ausdrücklich teilbar)

Tools, Bibliotheken, RouterOS-Snippets, Procedure-Beschreibungen sind **bewusst beidseitig
nutzbar**. Keine Code-Duplikation -- single source of truth, beide Kontexte greifen darauf zu.

Konkrete Mechanismen:
- **Junction-Points** (Windows): Personal-Mikrotik referenziert IBF-Mikrotik `tools/` und
  `mikrotik-scripte/` per `mklink /J` -- Edit hier, wirkt sofort dort
- **Gemeinsame Tool-Verzeichnisse**: `claude_base/tools/` (z.B. `ibf-mcp.py`, Auth-Modul)
  läuft kontext-agnostisch und wählt Credentials zur Laufzeit über `_detect_context()`
- **Shared-Knowledge-Sektionen** in CLAUDE.md: Recipes leben in einer Datei, der andere
  Kontext verweist mit Link drauf

**Voraussetzung an gemeinsam nutzbaren Code**: er darf **keine** kontextspezifischen Daten
hardcoded enthalten. Default-Hosts, Keys, Tokens kommen aus Config (.env, Keyring) oder
werden zur Laufzeit aus dem erkannten Kontext aufgelöst.

### Praktisch für Claude beim Arbeiten

- Vor `git add`/`git commit` prüfen: Sind in den geänderten Dateien **Daten** aus dem
  jeweils anderen Kontext? (IPs aus 192.168.10.x in einem IBF-Repo, pm##-Hostnames in
  einem Personal-Repo, Geräte-MACs, etc.)
- Code-Änderungen sind unkritisch -- die werden bewusst geteilt.
- Bei gemischten Inhalten den Nutzer warnen, nicht stillschweigend committen.
- `.gitignore` pro Kontext-Repo führen (`*.env`, `*.env_personal`, Workdir-Verzeichnisse,
  Backup-Dateien).
- Im Zweifel: nachfragen welcher Kontext gemeint ist, statt zu raten.

## Manuelle Überschreibung

Falls die Auto-Detection daneben liegt (z.B. VPN aktiv, beide Netze erreichbar):

```bash
# Per Umgebungsvariable -- höchste Priorität:
$env:PROXMOX_TOKEN = "root@pam!claude=<secret>"

# Oder Keyring direkt befüllen:
python -m keyring set proxmox-ibf ibf
```

## Path-Mismatch-Warnung

Tools warnen auf stderr, wenn der Skript-Aufrufpfad nicht zum erkannten Netz passt:

```
[WARN] Netzwerk-Kontext: 'personal', aber dieses Script liegt in einem
       'ibf'-Pfad: ...\ibf\claude_base\projekte\proxmox\tools\proxmox-query.py
       Bitte im richtigen Projektverzeichnis arbeiten.
```

Erlaubt aber die Ausführung (nicht-blockierend) -- nützlich für Test-Aufrufe.

## Kontextabhängige Tools

Pfade relativ ab `C:\Temp\claude\ibf\`:

- `claude_base/tools/ibf-mcp.py` (combined MCP) -- wählt Proxmox-Token automatisch
- `claude_base/projekte/proxmox/tools/proxmox-query.py` (CLI) -- dito
- `claude_base/projekte/graylog/tools/graylog-query.py` (CLI) -- analog
- `mikrotik/tools/mikrotik-mcp.py` -- Mikrotik-MCP, Default-Host pro Kontext
- `projekte/fortigate/tools/forti-connect.py` -- konstant 10.10.40.1, daher nur ibf

## Memory-Regel

Die Kontext-Information ist auch in der Auto-Memory dokumentiert
(`feedback_chat_patterns.md`), damit zukünftige Claude-Sessions wissen, ob „pm16" oder
„k5" gemeint ist. Bei Mehrdeutigkeit: nachfragen.


# FortiGate SSH Connection Guide

## Device Information
- **Model**: FortiGate-120G
- **Version**: v7.2.13, build 1762
- **Hostname**: gw
- **Serial Number**: FG120GTK24007805
- **IP Address**: 10.10.40.1
- **SSH Port**: 10022
- **SSH Server**: xeiDv (custom FortiGate SSH implementation)

## Credentials
- **Username**: audit
- **Password**: audit
- **Access Level**: Read-only

> **⚠ HINWEIS:** Aktuell sind ALLE Zugänge READONLY von FortiGate-Seite aus. Schreib-
> Operationen werden device-seitig abgelehnt. Konfigurations-Änderungen müssen via Web-UI
> oder Console manuell erfolgen. Sobald ein Schreib-User eingerichtet wird, ist das hier
> zu vermerken.

## ⚠️ Connection Instructions

**Always use `projekte/fortigate/tools/forti-connect.py` for any FortiGate connection.**

This script handles all SSH connection details, authentication, error handling, and connection lifecycle management. Do not attempt direct SSH or paramiko connections unless explicitly required.

### Using forti-connect.py

Script location: `projekte/fortigate/tools/forti-connect.py`

**Single command execution:**
```bash
python projekte/fortigate/tools/forti-connect.py "get system status"
python projekte/fortigate/tools/forti-connect.py "get system interface"
python projekte/fortigate/tools/forti-connect.py "get firewall policy"
```

**Interactive mode (multiple commands in one session):**
```bash
python projekte/fortigate/tools/forti-connect.py --interactive
```
Then type commands at the prompt:
```
forti> get system status
forti> get system interface
forti> exit
```

**Custom target device:**
```bash
python projekte/fortigate/tools/forti-connect.py "get system status" --host 10.10.40.1 --user audit --password audit
```

**Why use the script:**
- ✅ Persistent connection (no reconnection overhead)
- ✅ Automatic error recovery and reconnection
- ✅ Proper resource cleanup
- ✅ Simple, consistent interface
- ✅ Built-in interactive mode for exploration
- ✅ Handles FortiGate SSH quirks

---

### Alternative: Python/Paramiko (Direct usage)
```python
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

ssh.connect(
    "10.10.40.1",
    port=10022,
    username="audit",
    password="audit",
    timeout=15,
    banner_timeout=15,
    look_for_keys=False,
    allow_agent=False
)

stdin, stdout, stderr = ssh.exec_command("get system status")
output = stdout.read().decode('utf-8')
print(output)

ssh.close()
```

### SSH Command Line (Interactive)
```bash
ssh -o StrictHostKeyChecking=no -p 10022 audit@10.10.40.1
```

When prompted, enter password: `audit`

## Notes
- The SSH server requires proper authentication handshake
- Paramiko works reliably with this device
- Direct SSH command-line piping may have connection issues (requires pseudo-terminal)
- FortiGate uses custom SSH server "xeiDv" - standard SSH clients work but with limitations
- SSH key-based auth can be added later for passwordless access

## Logging Configuration

**External Logging (Graylog):**
- Server: `gld.ibf-solutions.com`
- Port: `1514`
- Status: Enabled
- Protocol: Syslog

**Exclusions (not logged to Graylog):**
- IPv6 multicast traffic (dstip ff02::1:2)
- Traffic from 233.233.233.233 (webfilter, event, virus, attack logs)

All other traffic and logs are forwarded to Graylog for centralized logging and analysis.

## Security Assessment

**Date**: May 4, 2026
**Status**: ✅ **GOOD** - No critical issues found

### Findings

**Positive:**
- ✅ Proper external logging to Graylog (gld.ibf-solutions.com:1514)
- ✅ Firewall policies are specific and well-organized
- ✅ Expired/inactive policies (e.g., 1086) are not processing traffic
- ✅ Only minimal admin accounts (audit user with restricted profile)
- ✅ Security Level set to "High"
- ✅ Firmware Signature certified
- ✅ NTP properly configured for time synchronization

**Notes:**
- Private Encryption is disabled (not relevant - device is standalone, no HA)
- INDUSTRIAL-DB, IPS-ETDB not licensed/used (by design)
- IoT-Detect not licensed/used (by design)

### Recommendations

1. **Firmware Updates** - Check if newer v7.2.x patches available
2. **Routine Database Updates** - Ensure Virus-DB, IPS-DB, APP-DB stay current

### Security Posture
Device is well-configured and maintained with no security gaps identified.

## Useful FortiGate Commands
- `show system status` - System status and version info
- `show system interface` - Network interfaces
- `diagnose system sessions list` - Active sessions
- `get log traffic` - Traffic logs
- `show firewall policy` - Firewall policies
- `show log syslogd setting` - Graylog/syslog configuration
