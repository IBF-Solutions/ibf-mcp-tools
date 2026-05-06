# CLAUDE-Inbox

Transiente Notizen + neue Items, die noch nicht ihrem endgültigen Platz zugewiesen sind.
Die Inbox soll regelmäßig freigeräumt werden -- jeder Eintrag wandert irgendwann
woandershin (Subprojekt-CLAUDE.md, Master-CLAUDE.md, oder wird erledigt).


## Allgemeine Projekt-Regeln (sollten nach Master-CLAUDE.md migrieren)

- Lege für jedes größere Thema (fortigate, graylog, proxmox, redmine, ...) einen Unterordner
  in `./projekte/` an und schreibe dort projektspezifische Doku in `CLAUDE.md` (oder andere
  Files).
- Speichere Referenzen wo nötig in der zentralen `./CLAUDE.md`, damit Fragen wie „was
  passiert in graylog" beantwortet werden können.
- Speichere Workingfiles (Analysen etc.) im lokalen `./workdir/` oder in den Unterordnern.
  Diese sollen in der `.gitignore` ausgenommen werden.
- Tokens/IDs liegen in `./.env` -- können überschrieben werden von `./.env_personal` oder
  `.env`-Files im jeweiligen Projekte-Unterordner.
- Für SSH-Verbindungen: Python+paramiko wann immer möglich.
- Die zentrale `./CLAUDE.md` darf nur unter bestimmten Voraussetzungen geändert werden --
  extra kurz halten.


## TODO

- [ ] **T1** [P1] [redmine] Redmine Ticketsystem -- als Subprojekt unter `./projekte/redmine/`
      anlegen, Doku + Tools (CLI/MCP) für Tickets, Suchen, Updates. Token in `.env`/Keyring.
      Schemata für Add/Done/Cancel analog zum T-System.

- [ ] **T2** [P2] [meta] T-System vs IBF-Buddy klären -- IBF-Buddy `/buddy import todos`
      definiert ähnliches Schema (`- [ ] **Tnn** [Pn] [kategorie] Titel — Detail  [#XXXX]`)
      für eine ZENTRALE `claude_todos.md`, hatte aber andere Absichten als unser
      projektlokales T-System (pro Unterordner). Klären ob die beiden zusammenfließen sollen,
      ob `buddy import todos` adaptiert werden muss, oder ob beide parallel sinnvoll sind.
      Aktuell: lokales T-System pro Projektordner ist im Root-CLAUDE.md beschrieben (Tn
      ohne führende Nullen).

- [ ] **T3** [P2] [credentials] Credential-Audit + Setup-Tool -- recherchieren wo aktuell
      Credentials liegen (Keyring vs `.env` vs hardcoded) und entscheiden welche Keyring-
      Storage brauchen. Anforderungen:
      1. **Audit**: welche Tokens/Keys/Passwörter existieren wo (proxmox-ibf, proxmox-personal,
         graylog_ibf in .env, fortigate audit/audit hardcoded, mikrotik SSH-Key-Pfad,
         ibf-mcp-global, ibf-mcp-<domain>, IBF-Buddy redmine/gh, künftig gitlab+redmine+zabbix).
      2. **Vereinheitlichung**: alle sollen **per Parameter** setzbar sein (CLI:
         `--set-token`/`--set-password`/etc., konsistente Syntax über alle Tools).
      3. **One-Script-Setup**: Wizard `setup-credentials.py` (oder als Buddy-Subcommand), der
         nacheinander alle relevanten und noch nicht gesetzten Credentials abfragt, in den
         jeweils richtigen Keyring-Service speichert, und am Ende eine maskierte Übersicht
         zeigt. Bereits gesetzte überspringen; `--force` zum Neuabfragen.
      4. **Doku**: in `claude_base/CLAUDE.md` Cheatsheet welche Service-Names existieren.

- [ ] **T4** [P2] [zabbix] Zabbix-Subprojekt anlegen unter `./projekte/zabbix/` -- Doku,
      Tools, später MCP-Domain `zabbix` für Monitoring-Abfragen (Hosts, Triggers, aktive
      Probleme, Verlauf). Token/User in Keyring (`zabbix-ibf`/`zabbix-personal`). Eigene
      `CLAUDE.md` mit T-System-TODO-Liste.

- [ ] **T5** [P2] [infra] Vector Server zur Nutzung verfügbar machen.

- [ ] **T6** [P3] [docs] Tools-Bereich in Master-CLAUDE.md migrieren -- folgende Punkte
      besser formulieren und dort verankern, danach aus Inbox entfernen:
      - Tools/Scripte gehören im jeweiligen Ordner in `./scripts/*` (oder `./tools/*` ?).
        Beim Aufräumen verschieben vorschlagen.
      - Pro Tätigkeit EIN Script mit Parametern statt vieler kleiner. `--help` aktuell halten.
        Zusätzlich `--ai-help` für effiziente AI-Prompts (kein Prosa, keine Mensch-Erklärungen).
      - Tools nutzen wann immer möglich. Bestehende erweitern statt parallele zu schaffen.
      - Wenn ein Tool zu komplex wird für effiziente Nutzung -- Lösung vorschlagen.

- [ ] **T7** [P2] [mcp] MCP-Doc-Levels (Token-Reduktion via konfigurierbare
      Tool-Beschreibungen). Three Levels (`full`/`compact`/`min`) im selben
      `ibf-mcp.py`-Code, gewählt per ENV-Var oder Persistent-File. Multi-Client-
      tauglich (jeder Subprozess unabhängig). Spec mit T-Liste D1-D9 in
      [`tools/claude/mcp-doc-levels.md`](tools/claude/mcp-doc-levels.md) -- dort
      pflegen, nicht hier.
      Stand 2026-05-06 (Phase 1 implementiert): D1-D3 ✓, D4 partial 14/47.
      Wichtigste Folge-TODOs: **D4** Refactor-Rest (33 Tools), **D5** Auto-
      Memory-Pattern, **D7** Live-Update praktisch testen, **D8** Token-Messung
      verifizieren -- Details siehe Doc, dort als ✓/offen geführt.

- [ ] **T8** [P3] [mcp] MCP-Self-Observability via Graylog (`app:ibf-mcp`).
      Logger sendet Lifecycle-, Auto-Detect-, Level-Change-, Tool-Call- und
      Tool-Error-Events via GELF-UDP. Default aktuell `IBF_MCP_LOG=on` (alles
      inkl. Tool-Calls). Spec mit T-Liste L1-L6 in
      [`tools/claude/mcp-self-observability.md`](tools/claude/mcp-self-observability.md).
      Wichtigste Folge-TODOs: **L1** Default restriktiver, **L4** Dashboard-
      Sektion „MCP-Activity", **L5** Latenz-Felder.


## Referenzen zu Projekten (Quick-Pointer)

Diese Liste selbständig fortführen, wenn neue Subprojekte dazukommen.

- graylog: `./projekte/graylog/`
- proxmox: `./projekte/proxmox/`
- fortigate: `./projekte/fortigate/`
- gitlab: `./projekte/gitlab/`
- mikrotik (IBF-Subkontext): `./mikrotik/` (außerhalb von `claude_base/`)
- mikrotik (Personal-Subkontext): `C:\Temp\claude\personal\subprojects\mikrotik\`
