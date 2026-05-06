# FortiGate Subprojekt

Hauptdoku: `claude_base/CLAUDE.md` (FortiGate SSH Connection Guide).

> ## ⚠ HINWEIS -- Alle Zugänge sind READONLY
>
> Aktuell ist der einzige verfügbare User `audit/audit` mit **read-only Profil** auf der
> FortiGate. Das bedeutet:
>
> - **Tool-Calls können nichts ändern** -- alle Schreib-Operationen werden device-seitig
>   zurückgewiesen.
> - **Konfiguration verändern** ist nur via Web-UI / Console manuell möglich (nicht über MCP).
> - **`fortigate_run`** ist deshalb in der Praxis nicht „destruktiv", auch wenn es unter
>   Confirm-Pflicht steht. Beobachtung: die explizite Confirm-Frage hilft trotzdem, weil sie
>   den User vor unsinnigen Befehls-Versuchen schützt.
> - Wenn jemals ein User mit Schreibrechten konfiguriert wird, **MUSS** dies hier vermerkt
>   werden -- ab dann sind Befehle wie `set system ...` tatsächlich gefährlich.

Tools:
- `tools/forti-connect.py` — SSH-Manager (Standalone-CLI)
- `tools/printer_sessions.py` — Session-Tracking (spezifisch Canon-Drucker)

Analyse-Artefakte (FortiGate Deep Analysis vom 2026-05-04):
- `analysis/PROGRESS.md`
- `analysis/collect.py`, `policy_hits.py`, `policy_hits2.py`
- `analysis/raw/` — 11 Audit-Dumps
- `analysis/reports/SECURITY_AUDIT.md`, `printer_investigation.md`

MCP-Server: ist Teil des combined `tools/ibf-mcp.py` (Domain `fortigate`).
Aktivierung: `buddy fortigate on` oder `buddy all on`.

---

## TODO

### MEDIUM PRIORITY

- [ ] **T1** [P2] Coverage-Check: alle FortiGate-Skripte im MCP abgedeckt? — Aktuell exposiert
  `ibf-mcp.py` nur 6 FortiGate-Tools (`status`, `list_interfaces`, `list_policies`,
  `list_sessions`, `show_log`, `run`). Folgende existierende Skripte sollten daraufhin
  geprüft werden ob ihre Funktion als MCP-Tool sinnvoll wäre:
  - `tools/forti-connect.py` — generisches SSH (✓ via `fortigate_run`)
  - `tools/printer_sessions.py` — Session-Filter für eine bestimmte IP
    (teilweise via `fortigate_list_sessions(filter_src=...)`, aber auf eine konkrete
     Drucker-IP zugeschnittene Convenience könnte sinnvoll sein)
  - `analysis/collect.py` — Batch-Sammler über alle wichtigen Bereiche
    (denkbar als `fortigate_collect_full_audit()` mit den Phasen aus dem Script)
  - `analysis/policy_hits.py` / `policy_hits2.py` — Hit-Counter für Firewall-Policies
    (denkbar als `fortigate_policy_hits()` mit Sortierung, Top-N, Zero-Hit-Filter)
  Aktion: Diese 5 Skripte durchsehen, sinnvolle als Tool ergänzen, Reste als „Standalone
  bleibt OK" markieren. Insbesondere `policy_hits` ist analytisch wertvoll für regelmäßige
  Reviews.

### MEDIUM PRIORITY (Forts.)

- [ ] **T4** [P2] DNS-Server-Einträge via `config system dns-database` zentral umsetzen?
  Aktuell sind DNS-Records vermutlich als einzelne Einträge / über externe DNS verteilt.
  FortiGate's `config system dns-database` erlaubt, mehrere Records (A/AAAA/PTR/CNAME)
  zentral auf der Firewall zu hinterlegen — schneller, kein zusätzlicher DNS-Server nötig,
  Firewall kann selbst als Resolver für LAN dienen.
  Klären: lohnt der zentrale Ansatz vs. dedizierter DNS (`10.0.0.69` aktuelles Forward-Target),
  oder bleibt es bei der Trennung? Was passiert bei FortiGate-Reboot mit den lokalen Records?

### LOW / INFORMATIONAL

- [ ] **T2** [P3] Hardcoded `audit/audit` Credentials in `ibf-mcp.py` und `forti-connect.py`
  read-only Account, derzeit akzeptiert. Wenn FortiGate mal mit beschränkterem User oder
  anderer Site kontaktiert werden soll, in Keyring auslagern (Service `fortigate-credentials`).

- [ ] **T3** [P3] `fortigate_show_log` im Shell-Mode getestet? — bei manchen FortiGate-Versionen
  liefert `execute log filter view-lines N` keine Wirkung. Praktisch validieren bei
  echtem Einsatz.

- [ ] **T6** [P1] [security] HTTPS-Admin-Mgmt von WAN absichern — Aktuell ist
  das Web-Mgmt der FortiGate auf allen 4 WAN-IPs (`80.120.87.250`,
  `88.116.6.118`, `185.124.145.91`, `185.124.145.79`) erreichbar. Folge:
  permanent ~100-300 k Admin-Login-Failed pro Tag von rotierenden
  Botnet-Quellen (87.251.64.0/24, 104.243.250.0/24, 206.123.144.0/24, ...).
  3-Strikes-Lockout greift, aber neue Subnets rotieren rein. Zwei Optionen:
  (a) `config system admin -> trusthost1..N` auf interne / VPN-Ranges
  einschränken, (b) Web-Mgmt komplett aufs interne Interface verschieben
  und für Remote-Admin SSL-VPN voraussetzen. Da `audit/audit` nur
  read-only ist, ist das Risiko begrenzt -- aber sobald ein Schreib-User
  konfiguriert wird (siehe T2), eskaliert das Risiko sofort.

- [ ] **T7** [P2] [network] WAN-IP `88.116.6.118` unreachable — TCP-Probe
  auf :443 schlägt fehl (siehe Dashboard-Run 2026-05-05 10:35), die anderen
  drei WAN-IPs antworten. Klären: Provider-Outage, Routing-Issue, Interface
  down? `get system interface` und Provider-Statusseite checken.

- [ ] **T8** [P2] [vpn] IPSec `pflach_peer` Konfig-Mismatch eskaliert —
  Phase-1-Errors 7d-Schnitt 640/Tag, am 2026-05-05 bereits 4593 vor 11 Uhr
  (+617 %). Reason: `peer SA proposal not match local policy`, mode
  aggressive, dir inbound, peer 80.120.76.222. Algorithmen / DH-Gruppe /
  Lifetime mit Gegenseite abgleichen. Tunnel re-keyed alle paar Sekunden,
  belastet die FortiGate-CPU + füllt Logs.

- [ ] **T9** [P3] [helper] Helper `10.102.250.2` (Port-Umschreiber)
  dokumentieren + Log-Reduce —
  (a) **Doku**: dieser Helper schreibt Ports um (DNAT von `88.116.6.118:443`
  via Port-Mapping). Funktioniert „im Moment perfekt", langfristig durch
  native FortiGate-VIP/DNAT-Lösung ersetzen.
  (b) **Log-Reduce**: persistente externe Scans (z.B. `178.113.26.242` (AT)
  knockt im Sekundentakt) erzeugen via Policy 932 deny mit
  `crscore=30 crlevel=high` jeweils ein Traffic-Log → bläst Graylog-Volumen
  unnötig auf. Optionen: deny-Policy auf `disable logtraffic`, oder
  Anti-Replay/RBL-Schicht davor, oder pro-IP Rate-Limit mit Log-Sample.

- [ ] **T10** [P2] [tool-bug] `fortigate_show_log min_level`-Filter
  funktioniert nicht — Beim Test 2026-05-05 lieferte
  `category=event since=today min_level=alert` 0 Treffer, ohne min_level
  6789 Treffer (davon ~90 % `level=alert`). Vermutung: die FortiOS-7.2.13-
  Syntax `execute log filter field level emergency,alert` greift nicht so
  wie angenommen -- vielleicht numerisch (0,1) oder Feld heißt anders.
  Verifizieren mit `fortigate_run "execute log filter field level ?"`,
  Implementation in `claude_base/tools/ibf-mcp.py::fortigate_show_log`
  korrigieren (`_FORTI_LEVELS`-Mapping → korrektes Filter-Argument).

- [ ] **T5** [P3] [architektur] Log-Filter im MCP-Tool vs. Graylog-Durchreich —
  `fortigate_show_log` wurde 2026-05-05 um since/until/min_level/logid/Category-
  Aliase erweitert. Frage: skaliert das? Bei mehr Filtern wird das Tool zur
  fragwürdigen Mini-DSL. Alternative: FortiGate logged ohnehin nach
  `gld.ibf-solutions.com:1514` (siehe Master-CLAUDE), Graylog kann Indexierung,
  Aggregation, Range-Suche nativ. Tool-Aufrufe für „Tagesübersicht" oder
  „Brute-Force-Scan-Erkennung" könnten via `graylog_search_messages` /
  `graylog_top_values` mit Source=FortiGate erfolgen.
  Klären: (a) Ist der FortiGate-Stream in Graylog komplett und mit den richtigen
  Feldern (level, logid, srcip, dstip) extrahiert? (b) Welche Use-Cases brauchen
  trotzdem den direkten CLI-Weg (Live-Sessions? Configuration-Inspection?)?
  (c) Falls Graylog-Pfad gangbar: `fortigate_show_log` auf seine Kernfunktion
  „Live-CLI-Dump" reduzieren und für Analyse auf Graylog-Tools verweisen.
