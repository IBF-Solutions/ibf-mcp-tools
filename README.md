# ibf-mcp-tools

Tooling rund um die IBF-Infrastruktur: Model-Context-Protocol-Server (Proxmox,
Graylog, FortiGate, Mikrotik, Hetzner) plus die CLI-Skripte und Doku, mit denen
Claude Code damit arbeitet.

## Inhalt

```
.
├── claude_base/                 # Hauptarbeitsverzeichnis (CLAUDE.md = Master-Prompt)
│   ├── CLAUDE.md                #   Regeln, Sprache, Kontextsystem (ibf/personal)
│   ├── CLAUDE-rules.md          #   T-System / TODO-Format
│   ├── CLAUDE-kontextsystem.md  #   Detail-Doku zur Auto-Erkennung
│   ├── tools/                   #   Combined IBF-MCP (proxmox+graylog+fortigate)
│   │   ├── ibf-mcp.py
│   │   ├── ibf_mcp_auth.py      #   gemeinsames Auth-Modul
│   │   └── mcp_logger.py
│   ├── projekte/
│   │   ├── proxmox/             #   proxmox-query.py CLI + Doku
│   │   ├── graylog/             #   graylog-query.py CLI + Doku
│   │   ├── fortigate/           #   forti-connect.py + Tools (analysis/ NICHT im Repo)
│   │   ├── hetzner/             #   hetzner-mcp.py
│   │   ├── dashboard/           #   morning-Briefing-Generator
│   │   └── zeiterfassung/       #   IFW-Zeiterfassung-Automatisierung
│   └── claude/                  #   plan.md, todo.md, memory/
└── mikrotik/                    # Eigenständiger Mikrotik-MCP (seit 2026-05-05)
    ├── tools/mikrotik-mcp.py
    └── mikrotik-scripte/
```

## Installation auf neuem System

Vollständige Anleitung in `claude_base/CLAUDE.md`. Kurzfassung:

```powershell
pip install mcp keyring paramiko
python -m keyring set proxmox-ibf ibf            # Proxmox-Token (oder proxmox-personal)
python claude_base/tools/ibf_mcp_auth.py --set-global-password
python claude_base/tools/ibf-mcp.py --test
python claude_base/tools/ibf-mcp.py --install    # registriert combined MCP in Claude Code
```

Mikrotik-MCP analog separat:
```powershell
python mikrotik/tools/mikrotik-mcp.py --install
```

## Kontextsystem

Die Tools erkennen automatisch ob sie im **ibf**- (10.10.40.0/21) oder
**personal**-Netz (alles andere) laufen und wählen den passenden Keyring-Eintrag.
Details: `claude_base/CLAUDE.md`, Abschnitt „Kontextsystem".

Manuelle Überschreibung per `$env:PROXMOX_TOKEN` möglich.

## Was bewusst NICHT im Repo ist

- **Private SSH-Keys** (`*id_rsa*`, `*.pem`, `key-files/`)
- **`.env` und Auth-Token** (`*.env`, `*.auth`)
- **Sub-Repo `claude_base/projekte/gitlab/`** -- gehört in die interne IBF-GitLab
  (`git.ibf-solutions.com/it/config-backup`), nicht hierher
- **`claude_base/projekte/graylog/tools/bw.exe`** -- 118 MB Bitwarden-CLI-Binary
- **`claude_base/projekte/fortigate/analysis/`** -- enthält Roh-Exports mit
  ENC-verschlüsselten PSKs/Passwörtern
- **`claude_base/projekte/zeiterfassung/auth.json`** -- Browser-Session-Cookies
- **`workdir/`** -- transiente Daten

Siehe `.gitignore` für die vollständige Liste.

## Sicherheit

FortiGate `ENC`-Werte sind **nicht** sicher -- sie sind nur mit dem Geräte-Key
obfuskiert. Niemals die `analysis/raw/`-Files oder `mainfirewall-backup.conf`
(im GitLab-Sub-Repo) in dieses öffentliche/halböffentliche Repo bringen. Für
Diff-Reviews die jeweilige `*-diff.conf` (sanitized) verwenden.
