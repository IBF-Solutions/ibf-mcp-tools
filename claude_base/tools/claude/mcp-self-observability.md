# MCP Self-Observability: GELF-Log-Pipeline für `ibf-mcp`

> **Living Document.** Bei Änderungen am Logger-Verhalten oder den
> ENV-Vars hier aktualisieren (Verhalten in betroffener Sektion,
> T-Status in §6, Geschichte in §7).
> Implementations-Quellen:
> - `claude_base/tools/mcp_logger.py`
> - Hooks in `claude_base/tools/ibf-mcp.py`

| | |
|---|---|
| **Verantwortliche Dateien** | `tools/mcp_logger.py`, `tools/ibf-mcp.py` |
| **Aktueller Status** | siehe §6 (T-System) |
| **Zeitliche Geschichte** | siehe §7 (Changelog) |

---

## 1. Zweck

Der MCP-Server schickt Events über sich selbst an Graylog (GELF-UDP).
Damit lässt sich nachträglich auswerten:

- Wann läuft der Server, in welcher Konfig?
- Welche Clients connecten sich (Auto-Detect-Befund)?
- Welche Tools werden wie oft / wann aufgerufen?
- Welche Tools werfen Errors?
- Wer wechselt mid-session DOC-LEVEL / TOOLSET / READONLY?

App-Tag in Graylog: **`app:ibf-mcp`** (zur Abgrenzung von
`app:ibf-dashboard` aus `dashboard/lib/snapshot.py`).

## 2. Event-Typen

| `event_type` | Wann | Wichtige Felder |
|---|---|---|
| `lifecycle` | Server-Start, ggf. Shutdown | `lifecycle_event`, `doc_level`, `toolset`, `readonly` (+ `_source`-Felder) |
| `auto_detect` | Beim ersten Tool-Call (D14) | `client_name`, `client_version`, `profile_match`, `changed` |
| `level_change` | `ibf_set_doc_level/_toolset/_readonly` | `axis`, `old_value`, `new_value`, `source` |
| `tool_call` | Vor jedem Tool-Aufruf | `tool_name`, `tool_args` (redacted, opt-out via `IBF_MCP_LOG_ARGS=off`) |
| `tool_error` | Exception in Tool | `tool_name`, `error_type`, `error_message` |

Gemeinsame Felder bei allen Events: `app` (= `ibf-mcp`), `mcp_session`
(ISO-Sekunde + PID + 4-Hex-Random beim Server-Start, eindeutig pro
Subprozess auch bei parallelen Starts in derselben Sekunde), `host`
(Hostname des Servers), `level` (6 = info, 3 = error).

Beispiel-Format: `2026-05-06T10:26:18-pid45264-c5a4`

> **⚠ Field-Name `mcp_session`** (nicht `session_id`): OpenSearch hatte das
> ursprünglich verwendete `session_id` aufgrund des initialen reinen-ISO-
> Formats automatisch auf `type=date` gemapped. Nach der Format-Erweiterung
> (PID + Random-Suffix) waren alle Events nicht mehr als Date parsbar →
> 12k+ Mapping-Failures, Events landeten nie im Index. Umbenannt 2026-05-06,
> siehe Changelog.

## 3. Konfiguration

| ENV-Var | Default | Wirkung |
|---|---|---|
| `IBF_MCP_LOG` | `on` | `off`/`0`/`false` schaltet das gesamte Logging aus |
| `IBF_MCP_LOG_ARGS` | `on` | `off` deaktiviert Args-Logging in `tool_call` (Tool-Name bleibt) |
| `IBF_MCP_LOG_HOST` | `gld.ibf-solutions.com` | Graylog-Host (für lokale Tests anpassbar) |
| `IBF_MCP_LOG_PORT` | `12201` | GELF-UDP-Port |

> **TODO:** Default ist aktuell **alles an** (Variante A). Das ist gut zum
> Einrichten und Verifizieren, aber langfristig sollte der Default
> restriktiver werden -- z.B. nur Lifecycle/Auto-Detect/Level-Change ohne
> Tool-Call-Logs (siehe **T1** unten).

## 4. Sicherheit & Redaction

- **`authenticate(password=...)`** loggt **nie** seine Args -- Tool ist in
  `_NEVER_LOG_ARGS_TOOLS` hartcoded.
- **Felder mit `password`/`token`/`secret`/`api_key`/`apikey`/`auth`/
  `credential` im Namen** werden auf `***` redacted (case-insensitive
  Substring-Match) wenn `IBF_MCP_LOG_ARGS=on`.
- **Lange String-Args** (>200 Zeichen) werden auf 200 Zeichen + `...[truncated]`
  gekürzt.
- **GELF-UDP-Limit** (~8 KB pro Datagram): Events werden bei
  Überschreitung redigiert (Tool-Args + `short_message` gekürzt). Kein
  TCP-Fallback aktuell.

## 5. Auswertung in Graylog

Mit `tools/graylog-query.py` oder direkt in der WebUI:

```bash
# Alles vom MCP-Server der letzten Stunde
graylog-query.py --query 'app:ibf-mcp' --last 1h

# Nur Tool-Calls
graylog-query.py --query 'app:ibf-mcp AND event_type:tool_call' --last 1h

# Auto-Detect-Befunde der letzten Woche
graylog-query.py --query 'app:ibf-mcp AND event_type:auto_detect' --last 7d

# Welche Tools wurden am häufigsten aufgerufen?
graylog-query.py --action terms --terms tool_name \
  --query 'app:ibf-mcp AND event_type:tool_call' --last 24h

# Errors der letzten Woche
graylog-query.py --query 'app:ibf-mcp AND event_type:tool_error' --last 7d

# Eine konkrete Server-Session vollständig
graylog-query.py --query 'app:ibf-mcp AND mcp_session:"2026-05-06T10:17:59-pid45264-c5a4"' --last 24h
```

> **Hinweis Feld-Mapping:** im GELF-Schema heißt das Hauptfeld
> `short_message`. Graylog speichert den Text intern unter `message` --
> beim Suchen/Anzeigen also `message` verwenden, nicht `short_message`.

## 6. T-System TODOs

| ID | Status | Kurz |
|---|---|---|
| **L8** | ✓ done | **Field-Rename `session_id` → `mcp_session`** -- 2026-05-06: 12k+ Index-Failures entdeckt. OpenSearch hatte beim ersten ISO-only-Event das Feld auf `type=date` gemapped, danach ließen sich Werte mit PID/Hex-Suffix nicht mehr parsen → silent drop. Lösung: Field-Rename ist sofort wirksam, kein Konflikt mehr. Bestehender Failure-Pool bleibt verloren (informational only); Cleanup-Option B (Field-Type-Profile in Graylog) wurde verworfen. Dateien: `tools/mcp_logger.py` (`_send` + `probe`), `tools/ibf-mcp.py` (ibf_logs Query + fields + Lookup). |
| **L1** | offen | **Default-Verhalten restriktiver machen** -- aktuell ist `IBF_MCP_LOG=on` mit allem inkl. Tool-Calls Default. Sobald die Pipeline produktiv läuft und das Volumen abschätzbar ist, auf restriktiveren Default umstellen: entweder `IBF_MCP_LOG=lifecycle` als Mittelweg (nur Lifecycle/Auto-Detect/Level-Change) oder `IBF_MCP_LOG=off` mit Opt-In via ENV. Erinnerung vom 2026-05-06: „im moment würde ich a machen, add todo als erinnerung das zu ändern". |
| L2 | offen | Log-Levels granularer machen: aktuell on/off binär. Sinnvoll wäre `IBF_MCP_LOG_LEVEL=quiet|lifecycle|info|debug` mit aufsteigender Verbosity. |
| L3 | offen | TCP-GELF-Fallback bei großen Events (>8 KB Tool-Args) statt Truncation. |
| L4 | offen | Dashboard-Sektion „MCP-Activity" -- letzte N Tool-Calls + Top-Tools + Error-Rate als eigene Sektion in `dashboard/morning.py`. |
| L7 | ✓ done | `ibf_logs`-Tool als Schnell-Diagnose -- direkter Graylog-Zugriff über `lib.graylog_api`, kompakter Output (Zeit + event_type + Session-Suffix + message-Text). Argumente: `minutes`, `event_type`, `tool_name`, `session_id` (oder `'current'`). Ins min-Toolset aufgenommen. **Bug entdeckt:** GELF-Custom-Fields werden in Graylog ohne `_`-Prefix gespeichert -- Queries müssen `app:` schreiben, nicht `_app:`. Im Tool berücksichtigt + Hinweis als Code-Kommentar. |
| L5 | offen | `tool_call`-Events mit Latenz-Feld (`duration_ms`) anreichern -- für Performance-Auswertung. |
| L6 | offen | Multi-Client-Test (D6 aus mcp-doc-levels): zwei parallele Sessions + Filter via `session_id` in Graylog -- damit isolierbar wer was tat. |

## 7. Changelog

### Quick

| Datum | Was |
|---|---|
| 2026-05-06 | Logger angelegt, Default `IBF_MCP_LOG=on` mit Tool-Calls |
| 2026-05-06 | session_id-Format: ISO + PID + Random (Kollisionen vermeiden) |
| 2026-05-06 | `ibf_logs`-Tool für Schnell-Diagnose ins min-Toolset |
| 2026-05-06 | **Field-Rename `session_id` → `mcp_session`** (Index-Mapping-Konflikt, L8) |

### Detail

- **2026-05-06 — Field-Rename `session_id` → `mcp_session`**
  Diagnose: GELF-Events vom MCP-Subprozess kamen sichtbar nicht in
  Graylog an, obwohl `socket.sendto()` ohne Fehler returnierte
  (`sent_ok` zählte hoch). Differential-Tests mit verschiedenen
  Payloads zeigten: Events ohne `_session_id` ODER mit reinem ISO-
  Wert kamen durch, alle anderen wurden gedroppt. Endpoint
  `/api/system/indexer/failures` lieferte Klartext: 12.707 Failures
  mit `mapper_parsing_exception: failed to parse field [session_id]
  of type [date]`. OpenSearch hatte das Feld beim allerersten Event
  (reines ISO) als Date gemapped; nach Format-Erweiterung (PID +
  Hex-Suffix) liessen sich neue Werte nicht mehr parsen → still
  verworfen. Lösung: Field umbenannt zu `_mcp_session` (im GELF-
  Send) bzw. `mcp_session` (in Queries). Greift sofort beim nächsten
  Server-Restart, kein Index-Reset nötig. Bestehender Failure-Pool
  bleibt verloren -- informational only.

- **2026-05-06 — Logger angelegt**
  Neue Datei `tools/mcp_logger.py` mit GELF-UDP-Sender (App-Tag
  `ibf-mcp`, App-Filter trennt von Dashboard-Snapshots). Hooks im
  `tool()`-Wrapper für `tool_call`/`tool_error`, in
  `_maybe_auto_detect_client` für `auto_detect`, in den drei
  `ibf_set_*` für `level_change`, plus Lifecycle-Event beim Modul-
  Init. Default Variante A: alles an. Live-Test mit 5 Test-Events
  in Graylog verifiziert -- alle Felder (`event_type`, `session_id`,
  `tool_name`, `axis`, `tool_args` etc.) korrekt indexiert. Args-
  Redaction: `password`/`token`/etc. → `***`; `authenticate` Args
  generell nicht geloggt. Schwergewicht-Schutz: GELF-Datagram >8 KB
  wird gekürzt. Logger-Failure killt nie den MCP-Server (try/except
  um Send).

---

## Anhang A: Verwandte Dokumente

- `tools/claude/mcp-doc-levels.md` -- Token-Reduktion + Toolset/Readonly
- `projekte/dashboard/lib/snapshot.py` -- Vorbild für GELF-Pattern
  (`app:ibf-dashboard`)
- `projekte/graylog/tools/graylog-query.py` -- Auswertungs-CLI

## Anhang B: GELF-Feld-Schema (Beispiel `tool_call`)

```json
{
  "version": "1.1",
  "host": "<server-hostname>",
  "short_message": "tool_call dashboard_morning args={...}",
  "timestamp": 1717234567.123,
  "level": 6,
  "_app": "ibf-mcp",
  "_session_id": "2026-05-06T10:17:59",
  "_event_type": "tool_call",
  "_tool_name": "dashboard_morning",
  "_tool_args": "{\"sections\": \"all\", \"timeout_s\": 50}"
}
```

Custom-Felder müssen mit `_` beginnen (GELF-Spec). Die Lib zieht
dieses Prefix automatisch.
