# Morning-Dashboard Subprojekt

Tägliche Übersicht über IBF-Infrastruktur (Security, Infra, Backups, Network,
Cloud, Logs). Aggregiert aus Graylog, Proxmox-API, Hetzner-Cloud-API und
TCP-Probes; vergleicht heute mit gestern und dem 7-Tage-Schnitt.

## Aufruf

```powershell
# normaler Run -- ASCII auf stdout
python projekte/dashboard/morning.py

# HTML in Datei
python projekte/dashboard/morning.py --html dashboard.html

# nur eine Sektion
python projekte/dashboard/morning.py --section security

# einzelne Sektionen weglassen
python projekte/dashboard/morning.py --skip cloud,logs

# kein Snapshot-Push (für Debugging)
python projekte/dashboard/morning.py --no-snapshot
```

UTF-8-Hinweis Windows: bei Encoding-Errors mit `Δ`, `✓`, `✖` etc. einmalig
`$env:PYTHONIOENCODING = "utf-8"` setzen, oder das Script reconfiguriert
stdout selbst (in `morning.py` per `sys.stdout.reconfigure`).

## Architektur

```
morning.py                       # CLI, Parallelisierung, HTML/ASCII-Switch
lib/
├── trend.py                     # absolute Date-Ranges + Δ + Status-Schwellen
├── snapshot.py                  # GELF-UDP an gld.ibf-solutions.com:12201
├── inventory.py                 # YAML-Loader für proxmox/inventory.yml
├── graylog_api.py               # count() + top_values() mit absoluten Ranges
├── proxmox_api.py               # cluster_resources, vms, nodes, tasks, snaps
├── hetzner_api.py               # servers, volumes
├── render.py                    # ASCII (ANSI) + HTML
└── collectors/
    ├── security.py              # Brute-Force, IPS, IPSec aus Graylog
    ├── infra.py                 # Proxmox-Cluster, Storage, Soll/Ist
    ├── backups.py               # vzdump 24h vs. Inventar
    ├── network.py               # WAN-TCP-Probe + IPSec-Error-Counts
    ├── cloud.py                 # Hetzner running/total
    └── logs.py                  # Graylog-Health (Indexer, Notifications)
tests/
└── test_trend.py                # 16 Range-/Delta-Tests
snapshots/                       # Fallback-Cache (aktuell ungenutzt, Variante D)
```

## Persistenz: Variante D (Graylog als Snapshot-Store)

Jeder Run schickt seine numerischen Metriken als GELF-UDP an `gld.ibf-solutions.com:12201`
(Inputs „GELF UDP" und „GELF TCP" sind aktiv, geprüft 2026-05-05).
Custom-Felder:
- `_app: "ibf-dashboard"`
- `_metric_name`, `_metric_value`, `_metric_section`
- `_dashboard_run_id` (ISO-Timestamp, alle Metriken eines Runs gleich)

Damit kann die nächste Iteration (`--use-snapshot-history`) statt einer
Live-Graylog-Aggregation auch direkt die archivierten Werte vergleichen.

## Inventar

`projekte/proxmox/inventory.yml` ist die Soll-Liste. Felder pro Eintrag:
`vmid, name, role, label, os, dmz, note`. Soll-Check-Logik:

| role | label | wirkt |
|---|---|---|
| server | production | MUSS laufen -- ALERT wenn nicht |
| client | beliebig | nur informativ (online-Quote) |
| beliebig | on-demand / debug | kein Soll-Check |
| template | beliebig | kein Soll-Check |

## Trend-Methodik (wichtig für korrektes Lesen)

| Spalte | Bedeutung |
|---|---|
| Heute | today 00:00 bis jetzt |
| Gestern bis jetzt | gestern 00:00 bis gestern zur jetzigen Uhrzeit |
| 7d-avg | letzte 7 volle Kalendertage ÷ 7 |

Vorteil: morgens um 09:00 wird ein 9h-Heute-Fenster nicht gegen einen
24h-Gestern-Block verglichen. Tests in `tests/test_trend.py` decken
DST-Wechsel, Monats- und Jahreswechsel ab.

## Bekannte WAN-IPs / Tunnel (für Network-Sektion)

Hardcoded in `lib/collectors/network.py` (aus FortiGate-Logs vom 2026-05-05
abgeleitet). Bei Änderung dort anpassen. Bessere Quelle wäre die FortiGate-
CLI -- siehe T2.

## Ausgewählte Schwellwerte (anpassen wenn Baseline anders ist)

| Metric | WARN | ALERT |
|---|---|---|
| admin_login_failed | 100 | 1000 |
| admin_login_disabled | 20 | 200 |
| admin_login_success | 20 | 50 |
| ipsec_phase1_errors | 100 | 1000 |
| failed_tasks | 1 | 10 |
| storage % | 80 % | 90 % |

## TODO

### MEDIUM PRIORITY

- [x] **T1** [P2] [security] Echte Counts via Aggregation-API ✓ ERLEDIGT 2026-05-05.
  `lib/graylog_api.py::top_values()` nutzt jetzt `POST /search/aggregate`
  (Graylog Scripting-API, AggregationRequestSpec) statt 10k-Sample-Counter.
  Wirkung am Beispiel Brute-Force-Top-IPs heute:
  - vorher: alle Top-5 nivelliert auf 144 (Sample-Artefakt)
  - nachher: echte Counts ~2170 pro IP, `admin`-User 60404 statt 3788 (16×).
  Wichtig: internes `limit = max(size, 50)` -- bei kleinem size ist die
  OpenSearch-Term-Aggregation pro Shard unscharf, ab ~50 stabilisieren
  sich die Werte. Client-seitig auf `size` clippen.
  Fallback-Pfad `_top_values_sample()` bleibt erhalten falls die
  Aggregation-API in einer alten Graylog-Version nicht verfügbar wäre.
  Folge-TODO (offen): das gleiche Pattern auch in
  `projekte/graylog/tools/graylog-query.py::do_terms()` umstellen --
  dort wird noch immer der 5000-Sample-Counter verwendet (siehe
  graylog/T3 Unit-Tests, T1 done -- ggf. neuer T-Eintrag in graylog/).

- [x] **T2** [P2] [network] FortiGate-CLI als Ground-Truth ✓ ERLEDIGT 2026-05-05.
  Network-Sektion liest jetzt direkt von der FG via SSH (`audit/audit`,
  `lib/fortigate_api.py`). Drei kombinierte Datenquellen:
  - **`get system interface physical`** -> WAN-Interfaces mit Port,
    Mode (static/pppoe), echtem Link-Status. WAN-Detection automatisch
    (Port mit IPv4 != 10/172/192-Range). Auto-Discovery der WAN-IPs
    (vorher hardcoded).
  - **`diagnose vpn ike gateway list`** -> etablierte IPSec-Tunnel mit
    IKE-/IPsec-SA-Counters, Alter (created Xs ago).
  - **Externer TCP-Probe :443** -> Reachability von außen (zusätzlich,
    weil FG-`up` ≠ extern erreichbar -- z.B. port5/88.116.6.118 zeigt
    aktuell genau diesen Fall: FG up, extern ✖).
  - **Graylog Phase-1-Errors** -> Gesamt-Count + Top-User für
    Verhandlungen die NICHT etablieren (z.B. `pflach_peer` mit
    `vpntunnel="N/A"`, daher nicht über Tunnel-Name auffindbar).
  Fallback: wenn FG nicht erreichbar (anderes Netz, kein VPN) ->
  TCP-Probe + Graylog allein, mit Hinweis im Output.
  Status-Aggregation: ALERT bei WAN-Down oder >1000 Phase-1-Errors,
  WARN bei externer Probe-Fehler oder >100 Phase-1-Errors.

- [ ] **T3** [P2] [inventory] Hetzner-Servers werden nicht ins
  `proxmox/inventory.yml` synchronisiert -- aktuell nur Live-Count aus der
  API. Sync-Skript `tools/sync_inventory.py`, das die Hetzner-Liste mit dem
  YAML mergt (manuelle Overrides für `label` bleiben).

### LOW

- [ ] **T4** [P3] [render] HTML-Output enthält keine Charts. Bei Bedarf
  Chart.js inline einbetten (Trend-Linie pro Sektion, 7-Tage-Werte aus
  Graylog-Snapshot-History).

- [ ] **T5** [P3] [snapshot] Bei großen Strings (z.B. Top-IPs als String-
  Liste) kann das GELF-UDP-Limit von ~8 KB anschlagen. Aktuell wird nur die
  Anzahl gepusht, nicht die Werte. Fallback auf TCP-GELF wenn ein Send
  größer wird.

- [x] **T6** [P3] [mcp] Wrapper im `ibf-mcp.py` ✓ ERLEDIGT 2026-05-05.
  Drei MCP-Tools eingehängt (Block vor Entry-point):
  - `dashboard_morning(sections, trend, timeout_s)` -- ASCII-Übersicht.
    Sections-Presets: `all` / `critical` (security+logs+network) /
    `fast` (ohne Proxmox/FG-Probe) / Komma-Liste. ThreadPool mit
    `as_completed(timeout=50)` als harter Cut < 60s-MCP-Limit;
    nicht-fertige Sektionen erscheinen als leere/`[TIMEOUT]`-Slots im
    Render-Output.
  - `dashboard_section(name, timeout_s)` -- gezielt eine Sektion.
  - `dashboard_history(metric, days, section)` -- liest GELF-Snapshots
    aus Graylog (`app:ibf-dashboard`).
  Kein eigener Auth-Guard -- die Lib-Funktionen prüfen Keyring/API selbst,
  fehlerhafte Sektionen werden im Render-Block sichtbar. Lazy-Import von
  `morning.py` + `lib.render` cached über Modul-Globals.
  HELP_TEXT in `_HELP_TEXT` ergänzt. Aktivierung: MCP-Server-Restart
  (Reconnect der Claude-Code-Session).

- [ ] **T7** [P3] [inventory] VM 107 (Edomi) und VM 116 (cloudflared2)
  stehen in `proxmox/CLAUDE.md` als stopped, das Inventar markiert sie als
  `production`. Klären: sollen sie laufen (-> Soll/Ist-Alarm hilft) oder
  nicht (-> Label auf `on-demand` setzen)? Bis dahin werden sie als
  „nicht-laufend" gemeldet.

- [ ] **T8** [P3] [logs] Im Run sehen wir `URGENT
  journal_uncommitted_messages_deleted` -- Graylog hat Messages verloren.
  Ursache klären (Journal-Disk voll? Indexer-Backlog?) und im Dashboard
  als eigener auffälliger Hinweis hochstufen. (Server-seitige Klärung als
  graylog/T4 abgelegt.)

- [ ] **T11** [P3] [render] Self-Measurement-Hinweis als Comment-Spalte
  in der SECURITY-Sektion -- aktuell wird `admin_login_success` einfach
  als Counter gezählt und triggert ALERT (>50). Beobachtung 2026-05-05:
  ein Großteil der Successes sind selbst-induziert durch das Mgmt-/Dashboard-
  Tool selbst (jeder `mcp__ibf__fortigate_*`-Call macht einen
  `audit/audit`-SSH-Login auf 10.10.40.1, srcip = Management-Host
  10.10.44.17). Lösung: Render-Modul um eine optionale Note-/Comment-
  Spalte erweitern. Heuristik: pro Metric ein Schlagwort wie `[self-measured]`
  oder `[noisy]` taggen, wenn ein bekanntes Filter-Kriterium passt
  (z.B. für admin_login_success: srcip == Management-Range). Im
  HEUTE-BEACHTEN-Block dann nicht hochstufen wenn nur self-measured.
  Alternativ: zweite Zeile in der Tabelle „davon self-induced: N" als
  zusätzliche Info, ohne den Hauptwert zu verfälschen.

- [ ] **T10** [P2] [context-ibf] Proxmox-Konfig auf IBF-Kontext ausweiten --
  Ergänzt T9 (Detection-Mechanik). Konkret zu tun:
  - IBF-Proxmox-Cluster-Hosts/IPs ermitteln (pm##-Naming laut
    Master-CLAUDE), zugehörige Token-Keyring-Einträge (`proxmox-ibf/ibf`).
  - `lib/proxmox_api.py` von hardcoded `192.168.10.1` auf
    `_detect_context()`-basiertes Lookup umstellen (siehe T9).
  - Eigenes Inventar `projekte/proxmox/inventory_ibf.yml` aus dem
    IBF-Cluster auto-generieren (Live-API oder manuell).
  - Hinweis Stand 2026-05-05: Personal-Proxmox (192.168.10.1) ist aus
    dem IBF-Netz inzwischen erreichbar (VPN/Routing) -- daher
    funktioniert `infra`/`backups` aktuell auch im IBF-Netz auf der
    Personal-Cluster. Für *echte* IBF-Sicht (pm##-Hosts) ist dieser
    TODO weiterhin nötig.

- [ ] **T9** [P2] [context] Kontext-Detection für Personal/IBF-Proxmox --
  `lib/proxmox_api.py` ist hardcoded auf `192.168.10.1` (Personal).
  Folge: aus dem IBF-Netz (10.10.40.x) sind `infra` und `backups` nicht
  erreichbar (Timeout nach 5 s, Sektion bleibt leer). Lösung: analog
  `_detect_context()` aus `claude_base/tools/ibf-mcp.py` einen
  Helper, der je nach lokaler Netzwerk-IP zwischen Personal-Proxmox
  (192.168.10.1, Token `proxmox-personal/ibf`) und IBF-Proxmox
  (TBD-IP, Token `proxmox-ibf/ibf`) wählt. Plus eigener
  Inventory-Pfad pro Kontext (`projekte/proxmox/inventory.yml` für
  Personal, `projekte/proxmox/inventory_ibf.yml` für IBF). Im
  Dashboard-CLAUDE.md den Kontext-Mode dokumentieren. Voraussetzung
  für IBF-seitiges Dashboard.
