# Graylog Query Tool (`tools/graylog-query.py`)

Read-only CLI gegen IBF-Graylog (`gld.ibf-solutions.com`). Token aus `.env`
(im Projekt, bis 5 Ebenen drüber, oder `C:\Temp\claude\.env`).

## Rolle

Ich bin der Abfragemanager für Graylog, wenn Philipp nicht als Admin agiert.
Philipp gibt einen Suchbegriff oder ein Thema vor — ich übersetze das in
konkrete Skript-Aufrufe und führe sie aus.

**Arbeitsweise:**
- Immer `tools/graylog-query.py` verwenden — kein direkter API-Aufruf ohne Rückfrage.
- Bei Fehlern im Skript: sofort korrigieren.
- Fehlen Parameter oder Optionen, um eine Frage effizient zu beantworten:
  Erweiterung vorschlagen oder direkt einbauen.
- Jede Änderung an Parametern oder Verhalten zieht immer eine Aktualisierung
  von `--help` und `--help-ai` nach sich — beide müssen stets aktuell sein.

## Aktionen (`--action`)

| Aktion | Zweck |
|---|---|
| `query` (Default) | Logs suchen + Summary + Sample-Output |
| `count` | Nur Trefferzahl (schnell, ohne Messages) |
| `fields` | Alle Felder der ersten N Treffer — zur Feld-Erkundung neuer Sources |
| `terms` | Top-N Werte eines Feldes (client-seitige Aggregation, max 5000 Sample) |
| `streams` | Liste aller Streams (id + Titel) |

## Zeitfenster

Eines davon (sonst Default = `--range 86400`):

- `--range <sek>` — rolling
- `--last <ausdruck>` — `'15m'`, `'2h'`, `'7d'`, `'90s'`
- `--today` — seit lokal Mitternacht heute (absolute window)
- `--yesterday` — gestern lokal 00:00..00:00
- `--from <iso> --to <iso>` — beliebiges absolutes Fenster

## Filter-Komfort

- `--source <host>` — `source:<host>` zum Query addieren (wiederholbar)
- `--stream <id|titel>` — auf Stream beschränken (id oder Titel-Substring)
- `--mail-to <wert>` — Postfix-Empfänger-Match. Drei Modi:
  - `foo@bar.com` -> exakte Phrase auf `to=<>`/`orig_to=<>`/`rcpt=<>`
  - `@bar.com` -> Domain-Match (Phrase mit Slop)
  - `foo` -> Substring (Lucene-Token-Regex `.*foo.*` + AND-Anker auf to-Token)
  Setzt automatisch beide Mail-Sources (`web12-hz` + `itl15-gdata-smtp`).
- `--mail-from <wert>` — analog für Absender (`from=<>`/`mail_from=<>`/`sender=<>`)

### Auto-Phrase-Quoting für `--query`

Ein `--query`-String **ohne** Lucene-Operatoren (`AND`/`OR`/`NOT`/`:`/`(`/`"` etc.)
**mit** Punkt oder `@` wird automatisch in Phrase-Quotes verpackt.
Beispiel: `--query slido.com` -> intern `("slido.com")`. Mit `--no-auto-quote`
deaktivierbar. Wenn Auto-Quoting greift, wird das in der Query-Echo-Zeile angezeigt.

## Rauschen ausblenden

Filter wirken **client-seitig** nach dem Search-Aufruf, reduzieren also nur die
Anzeige, nicht den Server-Aufwand.

- `--exclude PRESET|REGEX` — Messages mit Pattern weglassen (mehrfach kombinierbar).
  Bekannte Presets:
  | Preset | Was es ausblendet |
  |---|---|
  | `rbl-rejects` | Spam-Rejects über öffentliche RBLs (Spamhaus, Mailspike, Barracuda, PSBL) |
  | `greylisting` | `4.7.1 Greylisted` Verzögerungen |
  | `tls-handshake` | `SSL_accept error` / `TLS handshake failed` |
  | `postscreen-noise` | `postscreen` PASS/HANGUP-Lebenszeichen |
  | `cron-noise` | `CRON[..]:` Cron-Job-Logs |
- `--list-excludes` — alle Presets mit Pattern auflisten und exit
- `--client-filter REGEX` — Messages **behalten** die Regex matchen
  (Auffangnetz für AND-Phrase-Queries die `maxClauseCount` sprengen würden:
  eine Phrase server-side, die zweite client-seitig nachfiltern)

## Mailflow (`--action mailflow`)

End-to-end-Pipeline-Trace einer Mail. Eingaben:

- `--qid HEXID` — Postfix-Queue-ID (z.B. `07BF77E025`) → eine Mail
- `--msgid <id>` — RFC-Message-ID → eine Mail
- `--mail-to <addr>` — die N neuesten Mails an Empfänger, je Pipeline + Verdikt

Ausgabe pro Mail:
- Header: From, To, MsgID, Size
- Pipeline chronologisch: `[gdata]/[mailcow]  Zeit  Stage  Detail`
  (Stages: smtpd, cleanup, qmgr, smtp, amavis, other)
- Verdikt: `DELIVERED` / `REJECTED (Grund)` / `BLOCKED (Verdict)` /
  `BOUNCED` / `QUEUED`

Source-übergreifend (sowohl `web12-hz` als auch `itl15-gdata-smtp`).

## Output

- `--limit <n>` — max Treffer (Default 50)
- `--fields a,b,c` — nur diese Felder anzeigen (sonst Auto pro Source)
- `--all-fields` — alle Felder pro Treffer
- `--raw` — JSON-Ausgabe (zum Pipen)
- `--no-truncate` — Werte nicht bei 200 Zeichen abschneiden
- `--terms-size <n>` — bei `terms`: Top-N (Default 25)

## Beispiele

**Hat philipp.wacker heute Mails bekommen:**
```bash
python tools/graylog-query.py --mail-to philipp.wacker@ibf-solutions.com --today
```

**Substring-Match (alle Empfänger die "wacker" enthalten):**
```bash
python tools/graylog-query.py --mail-to wacker --last 24h
```

**Domain-Match (alle Mails von avdata.de):**
```bash
python tools/graylog-query.py --mail-from @avdata.de --last 7d
```

**Pipeline-Trace einer einzelnen Mail per QID:**
```bash
python tools/graylog-query.py --action mailflow --qid 07BF77E025 --last 1h
```

**Letzte 5 Mails an Johannes mit jeweils vollem Verdikt:**
```bash
python tools/graylog-query.py --action mailflow --mail-to johannes --last 7d --limit 5
```

**Mail-Logs ohne Spam-/RBL-Rauschen:**
```bash
python tools/graylog-query.py --mail-to johannes --last 7d \
  --exclude rbl-rejects --exclude greylisting
```

**Schneller Count (ohne Messages):**
```bash
python tools/graylog-query.py --action count \
  --query 'srcip:10.10.10.33 AND dstport:8006' --last 1h
```

**Top Policies, die in den letzten 24h gedropped haben:**
```bash
python tools/graylog-query.py --action terms \
  --query 'action:deny' --terms policyid --terms-size 10
```

**Felder einer unbekannten Source erkunden:**
```bash
python tools/graylog-query.py --action fields \
  --source itl15-gdata-smtp --last 5m --limit 3
```

## Bekannte Mail-Sources (für `--source`)

| Source | Was |
|---|---|
| `web12-hz` | mailcow-postfix Container (primärer Mail-Server) — Default für `--mail-to`/`--mail-from` |
| `itl15-gdata-smtp` | GData-Mailfilter (Eingangsfilter via amavis) |

### GData / amavis Log-Format

Amavis-Verdicts auf `itl15-gdata-smtp`:
```
itl15-gdata-smtp amavis[PID]: (MSGID) VERDICT TYPE {RelayedInbound}, ...
```
Mögliche Verdicts: `Passed CLEAN`, `Passed SPAMMY`, `Passed BAD-HEADER`,
`Blocked INFECTED`, `Blocked SPAMMY`, `Blocked BANNED`

Abfrage Virusfunde:
```bash
python tools/graylog-query.py --action verdicts --from 2026-04-27T00:00:00 --to 2026-05-04T00:00:00
python tools/graylog-query.py --source itl15-gdata-smtp --query 'message:"Blocked INFECTED"' --action count --last 7d
```

**Bekannte Lücke (geprüft 2026-05-04):** GData-Virusdetektionen erscheinen
möglicherweise nicht in Graylog — Graylog zeigte 0 Virus-Blocks in KW18/2026,
obwohl laut Philipp das GData-Portal Funde auswies. Das GData Management Portal
immer als Gegencheck heranziehen, wenn Graylog keine Viren zeigt.

## Token-Verwaltung (`--set-token`)

Token wird im **Windows Credential Manager** gespeichert (Service: `graylog`, User: `ibf`).

**Token setzen:**
```bash
python tools/graylog-query.py --set-token          # sichere Eingabe ohne Anzeige
python tools/graylog-query.py --set-token <TOKEN>  # direkt als Argument
```

**Token löschen:**
```bash
python -c "import keyring; keyring.delete_password('graylog', 'ibf')"
```
Alternativ manuell: *Systemsteuerung > Anmeldeinformationsverwaltung > Windows-Anmeldeinformationen > `graylog` suchen > Entfernen*

**Token-Ladereihenfolge:**
1. Umgebungsvariable `GRAYLOG_IBF`
2. Bitwarden CLI (`BW_SESSION` muss gesetzt sein)
3. Windows Credential Manager (`keyring`)
4. `.env`-Datei (Fallback)

**Token auslesen (für direkte API-Nutzung):**
```python
import keyring
token = keyring.get_password("graylog", "ibf")
```

**Direkte API-Calls** (Base-URL: `https://gld.ibf-solutions.com/api`):
```python
import base64, urllib.request
auth = base64.b64encode(f"{token}:token".encode()).decode()
req = urllib.request.Request("https://gld.ibf-solutions.com/api/streams")
req.add_header("Authorization", f"Basic {auth}")
req.add_header("Accept", "application/json")
req.add_header("X-Requested-By", "graylog-query-tool")
```

## Hinweise

- **Default-Suche schneidet `@` und `.` raus** (Tokenizer). Email-Adressen also
  immer in Anführungszeichen suchen oder `--mail-to` nutzen.
- **`--action terms` nutzt `POST /search/aggregate`** (Graylog Scripting-API,
  T5 ✓ 2026-05-05) -- exakte Counts auf vollem Datensatz, keine Sample-
  Niveallierung mehr. Internes `limit = max(size, 50)` für stabile
  OpenSearch-Term-Counts. Bei API-Fehler Fallback auf den alten 5000-Sample-
  Counter mit `[WARN]`-Stderr-Hinweis. `--patterns`-Modus unverändert
  (separate exakte Count-Queries pro Pattern).
- **Read-only**. Das Tool macht nur GETs (und `POST` für Aggregation, ohne
  Schreib-Wirkung). Für echte Write-Operations (Alerts ändern etc.) gibt es
  separate Scripts unter `analysis/` bzw. `tools/`.

## Help

- `--help` — deutsche CLI-Hilfe
- `--help-ai` — kompakte Param-Doku für Tool-Wrapper

## TODOs

- [x] **T1** [P2] [graylog-tool] Mail-Forensik-Verbesserungen für `tools/graylog-query.py` ✓ ERLEDIGT 2026-05-05.
  Alle 6 Sub-Schritte aus [`claude/mailflow-tooling.md`](claude/mailflow-tooling.md) umgesetzt:
  - **Schritt 1 — Substring-`--mail-to`/`--mail-from`**: drei Modi (volle Adresse / `@domain` / Substring).
    Substring nutzt Lucene-Token-Regex `.*term.*` + AND-Anker auf to-/from-Token (Email-Adressen sind
    monolithische Tokens nach Standard-Analyzer, daher Phrase-Match alleine nicht ausreichend).
  - **Schritt 2 — `--action mailflow`**: end-to-end Pipeline-Trace mit Verdikt
    (`DELIVERED`/`REJECTED`/`BLOCKED`/`BOUNCED`/`QUEUED`), per `--qid`/`--msgid`/`--mail-to`.
  - **Schritt 3 — Auto-Source-Erweiterung**: `MAIL_SOURCES = ["web12-hz", "itl15-gdata-smtp"]`.
  - **Schritt 4 — `--exclude PRESET|REGEX`**: client-seitiger Filter, 5 vordefinierte Presets,
    `--list-excludes` zur Übersicht, mehrfach kombinierbar.
  - **Schritt 5 — HTTP-500-Diagnose**: `maxClauseCount`-Errors werden mit klarer Meldung +
    Hinweis auf `--client-filter` abgefangen statt als raw stack dump.
  - **Schritt 6 — Auto-Phrase-Quoting**: `--query slido.com` -> intern `("slido.com")`,
    via `--no-auto-quote` deaktivierbar. Echo-Zeile zeigt das transparent an.
  Default-Timeout für API-Calls auf 30s erhöht (Lucene-Regex über große Zeitfenster ist teuer).
- [ ] **T2** [P2] [mail-tool] Neues Tool `tools/mail-server-query.py` + MCP-Tools `mail_*` — Direkt-Recherche am IBF-Mailserver (mailcow auf `mx.ibf-solutions.com`) per SSH. Komplementär zu graylog-query.py (Ground-Truth, ~8 Tage Reichweite, kein Indexer-Bug). **Architektur: Option B** — Kernlogik in `claude_base/tools/lib/mail.py`, dünne Wrapper in CLI **und** MCP (`ibf-mcp.py`). Aktionen: `query`, `mailflow`, `recipients`, `senders`, `queue`, `typo-search`. Vollständiger Plan in [`claude/mail-server-query.md`](claude/mail-server-query.md). Stufen A → B → C → D → E → F: **A=shared lib (inkl. Refactor von graylog-query.py mailflow)** → B=CLI-Grundgerüst+query → C=mailflow/recipients/senders → D=typo-search → E=rspamd/Queue/rotated → F=MCP-Anbindung mit SSH-Agent-Auth (`__UNAVAILABLE__` ohne Agent).

- [ ] **T6** [P2] [mail-tool] Mail-Log-Reichweite > 8 Tage erschließen — Befund 2026-05-05: Postfix-Container-Log nutzt `gelf`-Driver direkt nach Graylog, **keine lokalen rotierten Logs**, `/var/log/mailcow.logs*` ist 2023er-Erbe. Mail-History > Graylog-Retention (30d) existiert aktuell nirgends. Vier Optionen mit Empfehlungs-Reihenfolge in [`claude/mail-log-archive.md`](claude/mail-log-archive.md): **A=mailcow-MySQL-Logs erkunden** (sofort, billig) → mit T4-Klärung gewichten → **B=Postfix-syslog-Tee + logrotate** (mittelfristig, robust). C (Graylog-Dump) und D (Retention erhöhen) zurückgestellt. Einbindung als zweiter Reader in `mail-server-query.py --include-rotated` (T2 Stufe E). Außerdem aufräumen: alte `/var/log/mailcow.logs*` aus 2023 (~38 MB Disk-Müll).

- [x] **T5** [P3] [graylog-tool] `do_terms()` auf Aggregation-API umgestellt ✓ ERLEDIGT 2026-05-05.
  Default-Top-N-Modus nutzt jetzt `POST /search/aggregate` (Scripting-API,
  AggregationRequestSpec mit `group_by` + `metrics: count`). Internes
  `limit = max(size, 50)` für stabile Term-Counts. Neuer Helper
  `_gl_post_safe()` (POST mit RuntimeError statt sys.exit -- braucht's
  fürs Fallback-Pattern). Bei Aggregation-Fehler Fallback auf alten
  5000-Sample-Counter mit `[WARN]`-Hinweis. `--patterns`-Modus unverändert.
  Live-Test: top-1 srcip in 24h-Brute-Force = 2190 hits (Aggregation),
  cross-checked via separater Count-Query = 2188 hits (gleiche IP, paar
  Sekunden später) -- Match auf <0.1 % genau.
  Result-Dict hat zusätzliches `method`-Feld (`"aggregation"` oder
  `"sample-counter"`) zur Diagnose. Returntype kompatibel zum bisherigen
  (`terms`, `total`, `sample_size`, `truncated`).

- [ ] **T4** [P2] [graylog-server] URGENT-Notification
  `journal_uncommitted_messages_deleted` aufklären — Graylog hat Messages
  verloren (geprüft 2026-05-05). Zusätzlich Message-Rate −34 % gegenüber
  gestern bis zur gleichen Uhrzeit. Mögliche Ursachen: Journal-Disk voll,
  Indexer-Backlog, OpenSearch-Cluster-Issue, eingebrochener Input.
  Untersuchen über
  `/api/system/notifications`, `/api/system/journal`,
  Indexer-Cluster-Health, OpenSearch-Logs auf dem Container-Host
  (`itl34-docker` o.ä.). Bei Disk-Voll: Retention/Index-Rotation prüfen.

- [ ] **T3** [P3] [graylog-tool] Unit-Tests für `tools/graylog-query.py` —
  T1-Implementation wurde nur mit Live-API-Smoke-Tests gegen Graylog
  verifiziert, keine wiederholbaren Tests im Repo. Reine Helfer-Funktionen
  sind ohne Live-API testbar:
  - `_mail_clause` — drei Modi (volle Adresse / `@domain` / Substring),
    je Soll-Output verifizieren
  - `_maybe_auto_quote` — Operator-Detection, `.`/`@`-Trigger,
    `--no-auto-quote`-Pfad
  - `_classify_pipeline_line` — Postfix-/amavis-Stage-Erkennung
    (Sample-Strings für jede Stage)
  - `_resolve_exclude_patterns` — Preset-Lookup + Regex-Compile +
    Fehlerfall (`'['` als kaputtes Regex)
  - `_filter_messages` — Exclude/Client-Filter-Logik, Counter-Korrektheit
  - `_QID_RX` — 9-12 Hex-Chars match, false-positive-resistance
  Stil wie `projekte/dashboard/tests/test_trend.py` (plain-runner, keine
  pytest-Dependency). Plus Edge-Cases: kombinierte Flags, HTTP-500-Catch
  via Mock-`urlopen`. Ziel: ~15-20 Tests, Run unter 1 s, kein Netz.
