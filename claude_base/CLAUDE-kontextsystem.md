# Kontextsystem (ibf vs personal)

> **Wann diese Datei lesen?**
> - Wenn du Code schreibst der Credentials/Tokens lädt
> - Vor `git add`/`git commit` (wegen Daten/Code-Trennung)
> - Wenn ein Tool zwischen IBF und Personal unterscheidet (proxmox/graylog/mikrotik/...)
> - Bei Mehrdeutigkeit ob „pm16" oder „k5" gemeint ist
>
> Sonst nicht zwingend nötig -- die wichtigsten Punkte sind im Master-CLAUDE.md
> kurz erwähnt.

---

Es existieren zwei Arbeits-Kontexte mit getrennten Credentials, Naming-Konventionen und
Cluster-Topologien. Tools (Proxmox-MCP, proxmox-query.py, graylog-query.py, mikrotik-mcp.py)
erkennen den Kontext automatisch und wählen den passenden Keyring-Eintrag.

## Erkennung

Automatisch via lokaler Netzwerk-Interface-IP:

| Lokale IP | Kontext | Bedeutung |
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
