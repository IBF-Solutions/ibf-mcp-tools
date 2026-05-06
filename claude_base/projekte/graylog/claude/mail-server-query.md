# Plan: `tools/mail-server-query.py` — Direkt-Recherche am IBF-Mailserver

Hintergrund: Recherche „avdata-Mail an Johannes" am 2026-05-05 hat gezeigt,
dass Graylog und Mailserver beide gefragt werden müssen, und dass der direkte
Zugriff auf den Mailserver per `ssh + docker logs + grep + awk` zwar
funktioniert, aber jedes Mal fragil neu zusammengebaut wird (PowerShell-Quoting,
manuelle QID-Korrelation, Tippfehler-Varianten). Ein eigenes Script automatisiert
das.

T1 (graylog-query.py-Verbesserungen) ist seit 2026-05-05 umgesetzt — die dort
neu entstandene `mailflow`-Logik (Pipeline-Klassifikation, Verdikt-Ermittlung,
Postfix-Stage-Regex) ist die Blaupause für die hier geplante shared lib.

## Abgrenzung zu `graylog-query.py`

| Aspekt | `graylog-query.py` | `mail-server-query.py` (neu) |
|---|---|---|
| Datenquelle | Graylog-Index (~30 Tage retention) | Postfix-Container-Log + rspamd-Log + ggf. rotierte `/var/log/mailcow.logs*` |
| Latenz | wenige Sekunden, indiziert | wenige Sekunden bis ~30s je nach Log-Größe |
| Reichweite | 30 Tage stabil | Default ~8 Tage (Docker-Log), erweiterbar auf ältere `mailcow.logs.{1,2.gz,…}` |
| Vollständigkeit | Indexer-abhängig — bekannte Lücke bei GData-Virusfunden | Ground truth, raw vom Daemon |
| Auth | Token | SSH-Agent zu `mx.ibf-solutions.com` |
| Zweck | erste Anlaufstelle, schnelle Counts/Fields | Bestätigung, Tippfehler-Suche, Forensik wenn Graylog leer ist |

Die beiden Scripte sind **komplementär**, kein Ersatz füreinander.

## Architektur

**Option B — Shared Module + dünne Wrapper.** Die Kernlogik (Postfix-Log-Parser,
QID-Klassifikation, Verdikt-Ermittlung, Output-Formatter) liegt in
`claude_base/tools/lib/mail.py`. Drei Konsumenten greifen darauf zu:

```
                ┌─────────────────────────────────────┐
                │  claude_base/tools/lib/mail.py      │
                │  - Datenmodelle: Mail, Stage, Verdikt │
                │  - parse_postfix_line()             │
                │  - classify_pipeline()              │
                │  - format_mailflow()                │
                │  - typo_search() (rein analytisch)  │
                └─────────────────────────────────────┘
                       ↑           ↑           ↑
                       │           │           │
        ┌──────────────┘           │           └──────────────┐
        │                          │                          │
┌───────────────────┐  ┌───────────────────────┐  ┌────────────────────┐
│ graylog-query.py  │  │ mail-server-query.py  │  │ ibf-mcp.py         │
│ (CLI, Lucene-     │  │ (CLI, SSH→docker      │  │ (MCP, Subset der   │
│  Quelle)          │  │  logs Quelle)         │  │  Funktionen)       │
└───────────────────┘  └───────────────────────┘  └────────────────────┘
```

**Was das bedeutet:**
- Kein Subprocess-Aufruf von `mail-server-query.py` aus dem MCP — der MCP
  importiert direkt aus `lib/mail.py`. Keine SSH-Session-in-Subprocess-Kette.
- Datenquellen-Adapter (Graylog-API vs. SSH-grep) bleiben in den jeweiligen
  CLI-Tools; die lib bekommt nur Postfix-Log-**Zeilen** als Input und liefert
  strukturierte Mail-Objekte zurück.
- Der bereits in `graylog-query.py` implementierte mailflow-Code (T1, Schritt 2)
  wird in die lib gehoben, ohne sein Verhalten zu ändern.

**SSH-Pfad** für `mail-server-query.py`:

```
[lokal: mail-server-query.py]
   ↓ ssh (Agent + ControlMaster für Connection-Reuse)
[mx.ibf-solutions.com] docker logs … | grep -E … | head -N
   ↓ stdout
[lokal: lib/mail.py] parse_postfix_line, classify_pipeline, format_mailflow
   ↓
[stdout / JSON]
```

## Datenquellen (auf `mx.ibf-solutions.com`)

1. **`docker logs mailcowdockerized-postfix-mailcow-1`** — Default. Enthält
   alle Postfix-Stufen (`smtpd`, `cleanup`, `qmgr`, `smtp`, `bounce`).
2. **`docker logs mailcowdockerized-rspamd-mailcow-1`** — für Spam-Score und
   Verdict (statt nur Postfix-Sicht).
3. **`docker logs mailcowdockerized-dovecot-mailcow-1`** — relevant nur, wenn
   ein Empfänger eine Mailbox bei mailcow hat (bei IBF aktuell vermutlich keine
   produktiven Nutzer-Postfächer dort, nur Relay nach Domino).
4. **`docker exec mailcowdockerized-postfix-mailcow-1 postqueue -p`** — was
   steckt aktuell in der Queue.
5. **Rotierte Logs** (`/var/log/mailcow.logs.{1,2.gz,…}`) — für Anfragen
   außerhalb der Container-Log-Spannweite. Vorher prüfen, ob die heute noch
   geschrieben werden (Stand 2026-05-05: letzte Datei aus 2023, also vermutlich
   tot — das Script meldet das und fällt elegant zurück).

## Aktionen (Subset von `graylog-query.py` plus Spezialitäten)

| `--action` | Zweck |
|---|---|
| `query` (Default) | Roh-Treffer im Postfix-Log, gefiltert |
| `mailflow` | QID-/MsgId-basiert: chronologische Pipeline einer Mail mit Verdikt |
| `recipients` | Aggregation: alle Empfänger der Mails von Absender X (mit Anzahl) |
| `senders` | Umgekehrt: alle Absender, die an Empfänger X geschickt haben |
| `queue` | Aktuelle Postfix-Queue (`postqueue -p`) |
| `typo-search` | Empfänger-Substring → alle Local-Part-Varianten mit Häufigkeit (für „Tippfehler-Hypothese") |

## Filter-Optionen

Analog `graylog-query.py` (drei-Modi-Match wie in T1 Schritt 1):
- `--mail-from <substring|@domain|full-addr>` — wiederholbar.
- `--mail-to <substring|@domain|full-addr>` — wiederholbar.
- `--last <ausdruck>` — `'2h'`, `'7d'`, `'30d'`. Default: `7d`.
- `--from <iso> --to <iso>` — absolute Zeitfenster.
- `--limit <n>` — Treffer-Limit, Default 50.
- `--include-rotated` — auch `mailcow.logs.*` durchsuchen (langsamer).
- `--exclude PRESET|REGEX` — wie in graylog-query.py (selber Preset-Katalog
  aus der shared lib).

## Output

Default-Format: kompakte Pipeline-Sicht pro Mail (identisch zum mailflow-Output
von `graylog-query.py`, weil aus derselben lib-Funktion):

```
2026-05-05 10:34:31  qid=E79777D733  msgid=<00ec01dcdc6a$…@avdata.de>
  client mo4-p00-ob.smtp.rzone.de[81.169.146.161]
  from=<dagmar.klosa@avdata.de>  size=426590  rspamd=2.56/15 (no action)
  → to=<britta.schuhwerk@ibf-solutions.com>  via 80.120.87.250:11125  status=SENT
```

Optional `--raw` (JSON), `--no-truncate` analog graylog-query.py.

## Implementierungs-Reihenfolge

> **Wichtig:** Stufe A (shared lib) ist die **Voraussetzung** für die
> CLI- und MCP-Stufen. Sie ist nicht groß (großteils Refactor des bestehenden
> mailflow-Codes aus graylog-query.py), aber sie kommt zuerst.

### Stufe A — Shared lib `claude_base/tools/lib/mail.py` (mittel, ~2h)

- Datentypen definieren: `Mail`, `PipelineStage`, `Verdict` (Enum:
  `DELIVERED`/`REJECTED`/`BLOCKED`/`BOUNCED`/`QUEUED`).
- `parse_postfix_line(line: str) -> PipelineStage | None` — Regex-basiert,
  erkennt `postfix/(smtpd|cleanup|qmgr|smtp|smtps|bounce)` plus
  `amavis[…]: (\(MSGID\)) (Passed|Blocked) …`.
- `classify_pipeline(stages: list[PipelineStage]) -> Verdict` — die Logik
  steht heute in graylog-query.py (T1 Schritt 2). Hierher refactoren,
  Verhalten 1:1 erhalten.
- `format_mailflow(mail: Mail) -> str` — kompakte Pipeline-Sicht (siehe Output
  oben).
- `typo_search(lines: Iterable[str], substring: str) -> list[(addr, count)]`
  — extrahiert alle `to=<…>`-Adressen, die `substring` enthalten,
  sortiert nach Häufigkeit.
- `EXCLUDE_PRESETS` (dict) — der Preset-Katalog aus T1 Schritt 4 wandert
  hierher, beide CLI greifen darauf zu.
- **Refactor von `graylog-query.py`**: mailflow-Code-Pfade auf die lib
  umbiegen. Tests / manuelle Smoke-Checks: gleiche QIDs wie heute, gleicher
  Output.

### Stufe B — `mail-server-query.py` Grundgerüst + `query` (klein, ~2h)

- SSH-Verbindung mit Connection-Reuse (`ControlMaster auto` über
  `~/.ssh/config`-Snippet, oder Paramiko mit persistenter Session).
- `docker logs <postfix-container>` server-seitig per `grep -E` vorgefiltert,
  Patterns aus Python via `shlex.quote` an Bash übergeben — die
  PowerShell→ssh→bash-Quoting-Hölle ein für allemal sauber lösen.
- `--mail-from`, `--mail-to`, `--last`, `--limit`, `--exclude` umgesetzt
  (alle nutzen die lib).
- Default-Host aus `_detect_context()` (siehe `claude_base/CLAUDE.md`).

### Stufe C — `mailflow`, `recipients`, `senders` (klein, ~1h dank lib)

- `--action mailflow --qid …` / `--msgid …` / `--mail-to …` ruft
  `lib.classify_pipeline` und `lib.format_mailflow`. Wenig eigener Code.
- `--action recipients --mail-from dagmar.klosa@avdata.de --last 7d` →
  Aggregation pro Empfänger, sortiert. Hätte heute zwei Iterationen erspart.
- `--action senders --mail-to johannes.windeler-frick…` analog.

### Stufe D — `typo-search` (klein, ~30min)

- `--action typo-search --mail-to windeler` ruft `lib.typo_search`,
  Tabellen-Output mit Häufigkeit.
- Das war heute der „Aha-Moment" (johannes.winfeler-frick,
  johannes.frick@…ch) — manuell schwer zu fischen, hier ein Befehl.

### Stufe E — rspamd, Queue, rotierte Logs (mittel, ~2h)

- rspamd-Logs einmischen: zweite SSH-Pipeline auf den rspamd-Container,
  Score pro QID in Mail-Objekt mergen.
- `--action queue` → `docker exec … postqueue -p` parsen, kompakte Tabelle.
- `--include-rotated`: `cat`/`zcat` auf `/var/log/mailcow.logs*` zusätzlich
  zur Container-Log-Quelle. Bei toter Rotation (Stand heute: Dateien aus
  2023) freundlich melden und überspringen.

### Stufe F — MCP-Anbindung in `ibf-mcp.py` (klein, ~1h dank lib)

Neue Tool-Funktionen, jeweils dünner Wrapper über `lib.mail` und SSH-Adapter:

```python
@mcp.tool()
def mail_query(mail_to: str = "", mail_from: str = "", last: str = "7d",
               limit: int = 20) -> str:
    """Mailserver-Direktsuche im Postfix-Container-Log."""
    if not _ssh_to_mx_ok(): return "__UNAVAILABLE__"
    return mail_lib.format_query_output(_fetch_lines(...), ...)

@mcp.tool()
def mail_flow(qid: str = "", msgid: str = "") -> str: ...

@mcp.tool()
def mail_recipients(sender: str, last: str = "7d") -> str: ...

@mcp.tool()
def mail_senders(recipient: str, last: str = "7d") -> str: ...

@mcp.tool()
def mail_typo_search(name: str, last: str = "30d") -> str: ...

@mcp.tool()
def mail_queue() -> str: ...
```

**Auth:** `_ssh_to_mx_ok()` prüft, ob `ssh-add -l` einen Agent meldet und ein
einfacher `ssh root@mx.ibf-solutions.com true` durchgeht. Wenn nicht, liefert
das Tool `__UNAVAILABLE__` analog der bestehenden Konvention. Kein eigener
Token, kein Passwort-Prompt.

**Bestätigungspflicht:** Read-only nach Konstruktion (siehe Sicherheits-Hinweise
unten). Keine Confirmation-Loops nötig, im Gegensatz zu `proxmox_ssh_run` &
Co.

**Begleit-Aufgabe:** Hilfetext in `ibf_help` ergänzen, MCP-Instructions in
`ibf-mcp.py` anpassen (kurzer Verweis auf die neue mail-Domain).

## Trade-offs

- **Reichweite vs. Geschwindigkeit:** Container-Log ist schnell, aber kurz.
  `--include-rotated` als Opt-in, nicht Default.
- **SSH-Latenz:** Jede Anfrage öffnet eine SSH-Session. Mit `ControlMaster auto`
  oder Paramiko-Persistenz kein Problem; ohne ~1s Overhead pro Call.
- **Mailcow-spezifisch:** Container-Namen (`mailcowdockerized-…`) sind
  hardcoded. Wenn IBF mal von mailcow weg geht, muss das Script angepasst
  werden. Kein Drama bei einem Tool, das genau diesen Stack adressiert.
- **Shared-lib-Risiko:** Refactor von graylog-query.py-mailflow auf die lib
  könnte das bestehende Verhalten verschieben. Mitigations: 1:1-Behavior als
  Akzeptanzkriterium, vor/nach-Smoketests mit denselben QIDs.

## Sicherheits-Hinweise

- **Read-only.** Das Script und die MCP-Tools führen ausschließlich
  `docker logs`, `docker exec postqueue -p` und `cat`/`zcat` auf Logs aus.
  Niemals `postqueue -d`, niemals Container-Restart, niemals Mail-Body-Zugriff.
- **Kein Body-Inhalt.** Postfix-Logs enthalten Header-Metadaten, keine Bodies.
  Falls je Body gewollt: dovecot-Mailbox ist eine eigene Welt mit eigener
  Authentifizierung — bewusst nicht Teil dieses Plans.
- **SSH-Agent.** Kein Passwort im Script, kein Key im Repository. Lokaler
  Agent + erlaubter Key auf `mx.ibf-solutions.com:~/.ssh/authorized_keys`.

## Begleit-Aufgaben

- `CLAUDE.md` erweitern: neue Sektion „Mailserver-Direkt-Tool" mit Verweis
  auf `tools/mail-server-query.py` und kurzer „wann nehme ich was"-Tabelle
  (Graylog vs. Mailserver).
- `--help` (deutsch) + `--help-ai` parallel pflegen.
- Default-Host (`mx.ibf-solutions.com`) und Container-Namen in Konstanten
  am Skript-Anfang, nicht in der Mitte vergraben.
- Beim Refactor in Stufe A: die `MAIL_SOURCES`-Konstante und die
  `EXCLUDE_PRESETS` aus graylog-query.py in die lib heben, dortige Imports
  anpassen.
