# Plan: Mail-Forensik-Verbesserungen für `tools/graylog-query.py`

> **Status: ✓ UMGESETZT am 2026-05-05.** Alle 6 Schritte sind im Skript live,
> Substring-Match nutzt entgegen der ursprünglichen Skizze einen
> **Lucene-Token-Regex** (`message:/.*term.*/ AND message:to`) statt Wildcard,
> weil OpenSearch leading-wildcards (`*term*`) standardmäßig deaktiviert hat
> (HTTP 500) und der Standard-Analyzer Email-Adressen als monolithisches
> Token indiziert (Phrase-Match `"to philipp"` matcht `to=<philipp.wacker@…>`
> nicht). Token-Regex matcht jeden Token-Substring -- löst beides.
> Default-Timeout auf 30 s erhöht (Token-Regex über große Zeitfenster ist teuer).
> Status pro Schritt jeweils unten am Schritt-Header.

Hintergrund: Recherche „wurde Mail von avdata.de an Britta/Johannes blockiert?"
am 2026-05-05 hat fünf konkrete Schwachstellen im aktuellen Skript offengelegt.
Reihenfolge unten = empfohlene Umsetzungsreihenfolge (höchster Hebel zuerst).

## Schritt 1 — `--mail-to` / `--mail-from` als Substring-Match  ✓

**Status quo:** `--mail-to johannes` matched nichts, weil intern exakt
`to=<johannes>, orig_to=<johannes>, rcpt=<johannes>` gesucht wird. Die ganze
E-Mail-Adresse muss bekannt sein.

**Änderung:**
- Wenn der Wert kein `@` enthält → als Substring interpretieren und über
  Wildcards einbauen: `to=<*johannes*>` bzw. via Lucene-Wildcard-Query
  (`message:to=<*johannes*>`).
- Wenn der Wert mit `@` beginnt → Domain-Match: `to=<*@avdata.de>`.
- Wenn der Wert eine vollständige Adresse ist → Verhalten wie bisher.

**Trade-off:** Wildcard-Queries sind in Graylog/OpenSearch teurer. Für unsere
Volumina (Mail-Logs, ~10⁵/Tag) unkritisch. Bei hochfrequenten Substrings
(z.B. `--mail-to a`) explizit warnen statt blockieren.

**Akzeptanz:**
- `--mail-to johannes` findet alle Mails an `johannes.windeler-frick@…`
- `--mail-to @avdata.de` ersetzt den heutigen Workaround mit `--mail-from "@avdata.de"`
- Bestehende Aufrufe mit voller Adresse funktionieren unverändert
- `--help` und `--help-ai` aktualisiert

## Schritt 2 — `--action mailflow` (Trace einer einzelnen Mail)  ✓

**Status quo:** Eine Mail durchläuft 4–6 Postfix-Stufen
(`smtpd → cleanup → milter/amavis → qmgr → smtp → removed`), verteilt auf
zwei Sources (`web12-hz` + `itl15-gdata-smtp`). Zur Bewertung muss man nach
QID oder Message-ID erneut suchen, Ergebnisse manuell sortieren und den
Endstatus selbst zusammenpuzzeln.

**Neue Aktion `--action mailflow`:**
- Eingabe: einer von
  - `--qid <id>` (z.B. `07BF77E025`)
  - `--msgid <id>` (z.B. `02a901dcdbbe$f8161c20$e8425460$@avdata.de`)
  - `--mail-to <addr> --last <window>` → listet die Mails einzeln, wählt
    die jüngsten N und zeigt jeweils einen Mini-Flow
- Ausgabe pro Mail:
  - **Header-Zeile:** Zeit, From, To, Subject (falls extrahierbar), Größe
  - **Pipeline-Zeilen** chronologisch:
    `[itl15] 12:08:07 amavis Passed CLEAN`
    `[web12] 14:08:05 postfix/smtpd client=mo4-p00-ob.smtp.rzone.de`
    `[web12] 14:08:06 postfix/smtp → 80.120.87.250 dsn=2.0.0 status=sent`
  - **Verdikt-Zeile:** `DELIVERED` / `REJECTED (RBL: zen.spamhaus.org)` /
    `BOUNCED (5.7.1)` / `QUEUED` / `BLOCKED (amavis: INFECTED)`

**Implementierungsskizze:**
1. Eine Server-Query mit `qid` oder `msgid` als Phrase, beide Sources, ranges
   ±1h um die Mail.
2. Client-seitig nach Pipeline-Stufen klassifizieren (Regex auf
   `postfix/(smtpd|cleanup|qmgr|smtp|smtps|smtpd)`, `amavis[…]: (\(MSGID\)) (Passed|Blocked) (\w+)`).
3. Endstatus aus `dsn=` + `status=` der letzten `postfix/smtp`-Zeile ableiten,
   oder aus dem ersten `Blocked`/`milter-reject`/`NOQUEUE: reject`.

**Trade-off:** Erfordert source-übergreifendes Sammeln und etwas
Postfix-Kenntnis im Code. Im Gegenzug eliminiert es 80% der Folgeabfragen
bei Mail-Forensik.

**Akzeptanz:**
- `--action mailflow --qid 07BF77E025` zeigt die ganze Pipeline in <2s
- `--action mailflow --mail-to johannes.windeler-frick --last 7d --limit 5`
  listet die letzten 5 Mails an Johannes je mit Verdikt
- `--help` enthält Beispiele

## Schritt 3 — Auto-Source erweitert auf Mail-Pipeline  ✓

**Status quo:** `--mail-to`/`--mail-from` setzt nur `source:web12-hz`.
amavis-Verdicts auf `itl15-gdata-smtp` fallen damit raus.

**Änderung:** Default-Sources bei `--mail-to`/`--mail-from` sind
`(web12-hz OR itl15-gdata-smtp)`. Mit `--source` explizit überschreibbar.

**Trade-off:** Etwas mehr Treffer pro Anfrage, aber inhaltlich vollständiger.
Da `itl15-gdata-smtp` ähnliches Volumen wie `web12-hz` hat, verdoppelt sich
die Trefferzahl im Worst Case — sollte mit Default-Limit 50 trotzdem passen.

**Akzeptanz:**
- `--mail-to philipp.wacker@…` zeigt sowohl mailcow- als auch GData-Logs
- amavis-Verdict (`Passed CLEAN`/`Blocked …`) ist im Output zu sehen, ohne
  dass man `--source` extra setzen muss

## Schritt 4 — Generisches `--exclude <preset|regex>`  ✓

**Status quo:** 64 Rejects an Johannes in 14d sind alle externer Spam, durch
RBLs (Spamhaus/Mailspike/Barracuda) blockiert. Diese rauschen die Sicht zu.

**Änderung:** Ein einziger Flag `--exclude <wert>` (wiederholbar). Der Wert
wird in dieser Reihenfolge interpretiert:

1. **Bekanntes Preset** (Lookup in einer Tabelle im Skript) → das hinterlegte
   Regex-Pattern wird verwendet.
2. **Sonst:** Wert wird direkt als Regex auf das `message`-Feld angewendet.

**Initiale Preset-Tabelle:**

| Preset | Pattern (Auszug) | Was es ausblendet |
|---|---|---|
| `rbl-rejects` | `blocked using (zen\.spamhaus\.org\|bl\.mailspike\.net\|b\.barracudacentral\.org\|psbl\.surriel\.com)` | Spam-Rejects über öffentliche RBLs |
| `greylisting` | `4\.7\.1 Greylisted` | temporär verzögerte Mails |
| `tls-handshake` | `SSL_accept error\|TLS handshake failed` | Probleme im TLS-Setup |
| `postscreen-noise` | `postfix/postscreen.*(PASS NEW\|PASS OLD\|HANGUP)` | normale postscreen-Lebenszeichen |
| `cron-noise` | `CRON\[\d+\]:` | Cron-Job-Logs, falls sie reinrutschen |

(Die Liste lebt im Skript und kann pro Preset noch anders heißen — wichtig
ist die Mechanik.)

**Verhalten:**
- `--exclude rbl-rejects` → Preset
- `--exclude 'spamhaus|mailspike'` → Regex
- `--exclude rbl-rejects --exclude greylisting` → mehrere Presets kombiniert
- `--exclude rbl-rejects --exclude 'foo.*bar'` → Preset + Regex gemischt
- Optional: `--list-excludes` zeigt alle definierten Presets mit Pattern.

**Kollisionsfall:** Wenn jemand zufällig ein Regex schreibt, das zugleich der
Name eines Presets ist (`rbl-rejects` als Regex wäre nutzlos, kommt also
praktisch nicht vor), gewinnt das Preset. Dokumentieren, dass für „garantiert
Regex" einfach ein Sonderzeichen reicht (`--exclude '^rbl-rejects'`).

**Trade-off:** Client-seitiger Filter verkleinert nur das Display, nicht den
Server-Aufwand. Für die Tagesarbeit aber genau das, was man will (alles
holen, Standard-Rauschen ausblenden). Presets machen die häufigen Fälle
selbstdokumentierend, ohne den Flag-Raum aufzublasen.

**Akzeptanz:**
- `--mail-to johannes --last 7d --exclude rbl-rejects` zeigt nur „echte"
  Reject-Events
- `--exclude 'spamhaus|mailspike'` als reine Regex-Variante funktioniert
- `--exclude rbl-rejects --exclude greylisting` lässt sich kombinieren
- `--list-excludes` listet alle Presets
- `--help` und `--help-ai` führen die Presets auf

## Schritt 5 — Phrase-Query AND-Limit-Workaround  ✓ (pragmatisch)

> Implementiert als HTTP-500-Catch mit klarer Fehlermeldung und Hinweis auf
> `--client-filter`. Auto-Splitting nicht implementiert -- der User
> entscheidet explizit per `--client-filter <regex>`, welche Phrase
> client-seitig statt server-seitig gefiltert wird.



**Status quo:** `"johannes.windeler" AND "avdata.de"` über 30d sprengt das
OpenSearch-Limit `maxClauseCount=1024` und gibt HTTP 500.

**Änderung:** Im Skript bei 2 Phrasen, die mit AND verknüpft werden:
1. Die seltenere Phrase (heuristisch: längere oder seltenere Domain) als
   Server-Phrase-Query.
2. Die zweite Phrase nach Empfang client-seitig im `message`-Feld matchen.
3. Wenn das Server-Result schon >Limit (z.B. 5000) ist, in einem zweiten
   Pass präziser oder mit kleinerem Zeitfenster nachladen.

**Trade-off:** Heuristik kann mal schief liegen (seltene Domain vs. seltenes
Wort), aber besser als HTTP 500. Alternativ kann ein neuer Flag
`--client-filter <pattern>` explizit gesetzt werden.

**Akzeptanz:**
- Kombinierte Phrase-Queries kippen nicht mehr in HTTP 500.
- Bei großem Result eine `WARN: client-side filter applied to N hits`-Zeile.

**Hinweis:** Nach Schritt 2 ist Schritt 5 für den Hauptanwendungsfall
(Mail-Forensik) weniger dringend, weil `--action mailflow` und der
Substring-`--mail-to` die Phrase-AND-Kombination meist überflüssig machen.
Trotzdem als Auffangnetz für freie Queries nützlich.

## Schritt 6 — Auto-Phrase-Quoting für `--query`  ✓

**Status quo:** Graylogs Tokenizer trennt an `.` und `@`. Daher muss der
Aufrufer Werte wie `slido.com` oder `philipp.wacker@…` selbst doppelt quoten:
`--query '"slido.com"'` (äußere Shell-Quotes + innere Lucene-Phrase-Quotes).
Vergessene innere Quotes liefern stillschweigend 0 oder falsche Treffer.

**Änderung:** Vor dem Absetzen des Requests prüft das Skript den Query-String
auf Lucene-Operator-Zeichen. Wenn **keine** Operatoren vorkommen
(`AND`, `OR`, `NOT`, `:`, `(`, `)`, `*`, `?`, `~`, `^`, `"`) **und** der
String mindestens eines von `.`, `@` enthält, wird er automatisch in
Phrase-Quotes verpackt: `slido.com` → `"slido.com"`.

Bei expliziten Lucene-Queries (`source:web12-hz AND level:3`) bleibt das
Verhalten unverändert, weil `:` als Operator erkannt wird.

**Trade-off:** Zwei seltene Edge-Cases:
- Jemand will wirklich `slido AND com` als zwei Tokens suchen → muss dann
  explizit quoten oder `--no-auto-quote` setzen.
- Felder mit Punkt-Notation in Field-Suchen (`host.name:foo`) — werden durch
  das `:` korrekt als Operator-Query erkannt, also kein Konflikt.

**Akzeptanz:**
- `--query slido.com` und `--query '"slido.com"'` liefern dasselbe Ergebnis
- `--query 'level:3 AND source:web12-hz'` bleibt unangetastet
- Wenn Auto-Quoting greift, wird in der Query-Echo-Zeile das gequotete
  Resultat angezeigt (Transparenz)
- Optional: `--no-auto-quote` als Escape-Hatch
- `--help` und `--help-ai` aktualisiert

## Reihenfolge & Aufwandsschätzung

| Schritt | Aufwand | Hebel |
|---|---|---|
| 1 — Substring `--mail-to` | klein (1h) | hoch |
| 2 — `--action mailflow` | mittel (3–5h) | sehr hoch |
| 3 — Auto-Source erweitern | klein (30min) | mittel |
| 4 — `--exclude` Filter | klein (1h) | mittel |
| 5 — Phrase-AND-Workaround | mittel (2–3h) | gering (wenn 1+2 da sind) |
| 6 — Auto-Phrase-Quoting | klein (30min) | mittel (jeder Aufruf) |

Empfehlung: **1 → 6 → 3 → 2 → 4 → 5**. Schritte 1, 6, 3 sind alle klein und
verbessern jeden weiteren Aufruf — daher zuerst. Danach 2 als großer Wurf,
dann 4 (Output-Hygiene), dann 5 als Auffangnetz.

## Begleit-Aufgaben (immer)

- `--help` (deutsch) und `--help-ai` (kompakt für AI-Wrapper) bei jeder
  Änderung mitziehen — siehe Arbeitsweise in `CLAUDE.md`.
- `CLAUDE.md` (Sektion „Filter-Komfort", „Aktionen") aktualisieren, sobald
  ein Schritt gemerged ist.
- Bestehende Aufrufbeispiele in `CLAUDE.md` müssen weiter funktionieren.
