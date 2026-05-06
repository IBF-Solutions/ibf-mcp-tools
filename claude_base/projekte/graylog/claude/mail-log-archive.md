# Plan: Mail-Log-Reichweite über die ~8 Tage Container-Buffer hinaus

## Status quo (geprüft 2026-05-05 auf `mx.ibf-solutions.com`)

- **Docker-Log-Driver der mailcow-Container ist `gelf`**, Ziel
  `udp://10.10.10.42:12201` (= Graylog-Eingang). Beweis:
  ```
  docker inspect mailcowdockerized-postfix-mailcow-1 \
    --format '{{.HostConfig.LogConfig.Type}} {{.HostConfig.LogConfig.Config}}'
  → gelf map[gelf-address:udp://10.10.10.42:12201 tag:mailcow-postfix]
  ```
- **Keine lokalen Container-Log-Dateien**:
  `/var/lib/docker/containers/<id>/<id>-json.log*` existiert nicht (gelf-Driver
  speichert nicht lokal). `docker logs` zeigt nur den **In-Memory-Ringpuffer**
  der Docker-Engine (~8 Tage je nach Volumen).
- **`/etc/logrotate.d/`** hat keinerlei Mailcow-Einträge — kein logrotate
  für Mail aktiv.
- **`/var/log/mailcow.logs*`** existiert (auch `.gz`-Files), aber alle
  Dateien sind **aus September 2023** und werden nicht mehr beschrieben.
  Vermutlich Erbe einer früheren rsyslog-Konfig vor dem gelf-Umstieg.
  Heute: nur Disk-Müll, kein Forensik-Wert.

**Konsequenz:** Mail-Logs > 8 Tage existieren aktuell **ausschließlich in
Graylog** (30 Tage Retention, plus T4-Risiko mit dem journal-uncommitted-Issue).

## Zielbild

Eine zweite, vom Graylog-Indexer unabhängige Quelle für Postfix-Logs, die
mindestens die Graylog-Retention-Lücke schließt und im Idealfall länger
zurückreicht. Verfügbar für `mail-server-query.py --include-rotated` (T2 Stufe E).

## Optionen

### Option A — mailcow-MySQL-Tabelle nutzen (falls vorhanden)

mailcow speichert in der MySQL-DB ggf. eigene Postfix-Log-Daten für die
Web-UI. Erster Schritt ist daher Erkundung: welche Tabellen existieren, mit
welcher Reichweite und Struktur, ob die per `docker exec mysql-mailcow-1 mysql …`
abgefragt werden können.

**Pro:** vermutlich schon vorhanden, 30–60 Tage Reichweite typisch.
**Con:** mailcow-Eigen-Schema, anderes Format als Raw-Postfix-Log; Reichweite
durch mailcow-Cron begrenzt; Mail-Body-Inhalt kann mitgespeichert sein
(Quarantäne) — Vorsicht beim Auslesen.

### Option B — rsyslog-Tee + lokales logrotate

Auf dem Host einen rsyslog-Receiver konfigurieren, der parallel zum
gelf-Driver die Postfix-Logs lokal speichert. Per `/etc/logrotate.d/mailcow`
mit `weekly` + `rotate 26` ergibt das ~6 Monate Reichweite.

Setup-Skizze:
1. Docker-Log-Driver der mailcow-Postfix/rspamd-Container auf
   **`syslog`** umstellen — oder besser: zusätzlich zum gelf einen
   Sidecar-Forwarder. Docker erlaubt nur einen Log-Driver pro Container,
   daher Sidecar-Pattern (z.B. `fluent-bit` oder `vector` als zweiter
   Container, der die gelf-UDP-Pakete sniffed/dupliziert).
2. Alternative ohne Sidecar: rsyslog horcht auf einem zweiten UDP-Port,
   gelf-Driver schickt parallel dorthin (geht **nicht** mit Docker, denn
   gelf hat genau ein Ziel).
3. Pragmatischer: **Postfix selbst** so konfigurieren, dass es zusätzlich
   ans lokale syslog schreibt. Postfix hat
   `syslog_facility = mail` standardmäßig — wenn der Postfix-Container
   syslog auf `/dev/log` mounten und der Host einen lokalen rsyslog mit
   `mail.* /var/log/postfix.log` hat, läuft das parallel ohne Sidecar.

**Pro:** unabhängig von Graylog, beliebig konfigurierbare Retention,
einfache Standard-Tools.
**Con:** Konfigurationsaufwand, Disk-Belegung auf dem Hetzner-Server
beachten, Container-Restart nötig.

### Option C — Externer Log-Archiver

Cron-Job, der täglich aus Graylog die Mail-Logs der letzten 24 h dumpt
und als komprimierte JSON-Datei lokal ablegt. Verzeichnis-Layout
`/var/log/mailcow-archive/2026/05/05.jsonl.gz`.

**Pro:** Format unter eigener Kontrolle, beliebig lange Retention,
direkt von `mail-server-query.py` parsbar.
**Con:** Hat genau dasselbe Indexer-Risiko wie Graylog (Quelle ist
Graylog), löst T4 also nicht — nur Verlängerung der Retention.

### Option D — Graylog-Retention erhöhen

OpenSearch-Index-Retention von 30 auf z.B. 180 Tage hochsetzen, plus
ggf. größerer Storage am Indexer.

**Pro:** keine neue Komponente, bleibt im bekannten Workflow.
**Con:** Indexer-Größe wächst um Faktor 6, T4 (Indexer-Loss) wird damit
nicht adressiert. Plus: muss mit Plattform-Admin abgestimmt werden.

## Empfehlung

**Reihenfolge:**
1. **Sofort (klein, ~30 min):** Option A erkunden — schauen ob in der
   mailcow-MySQL eine nutzbare Postfix-Log-Tabelle existiert. Wenn ja,
   sofort als Quelle für `mail-server-query.py --include-rotated`
   einbinden. Diese Option ist die billigste Brücke.
2. **Parallel zu T4 (klein, ~1 h):** klären, ob T4 (Graylog-Indexer-Loss)
   strukturell oder einmalig ist. Wenn strukturell → Option B oder C
   wird zur Pflicht. Wenn einmalig → Option A reicht erstmal.
3. **Mittelfristig (mittel, ~2–3 h):** Option B (Postfix-syslog-Tee + lokales
   logrotate) implementieren. Das ist die robusteste Lösung mit eigener
   Quelle und etablierten Tools.
4. **Aufräumen (klein, ~10 min):** alte `/var/log/mailcow.logs*` aus 2023
   prüfen ob die enthalten wirklich nichts Wertvolles, dann archivieren
   oder löschen — aktuell nur Disk-Müll (~38 MB).

Option C und D bewusst zurückgestellt: C löst T4 nicht, D ist Plattform-
weit und braucht Admin-Koordination.

## Einbindung in `mail-server-query.py` (T2 Stufe E)

Die heutige Plan-Skizze `--include-rotated` (T2 Stufe E) erweitert sich
um die hier identifizierten Quellen. Konkret bedeutet das:

- Wenn Option A umgesetzt: zweiter Reader in `lib/mail.py`, der MySQL-
  Tabelle queried und in dieselbe `Mail`-Datenstruktur konvertiert.
- Wenn Option B umgesetzt: zweiter Reader, der `/var/log/postfix.log`
  und `.gz`-Files via SSH ausliest (`zcat | grep`).
- Aus Sicht des CLI/MCP bleibt `--include-rotated` ein einzelner Flag,
  der intern mehrere Quellen mergen kann.

## Sicherheits-Hinweise

- Bei Option A (MySQL-Lesen): nur die **Log-Tabelle** lesen, niemals
  Quarantäne-Bodies oder User-Tabellen.
- Bei Option B (rsyslog-Tee): Filterregel auf rsyslog setzen, dass nur
  Mail-Header, keine Bodies geloggt werden — Postfix tut das ohnehin per
  Default, aber explizit prüfen.
- Bei Aufräumen alter Logs: keine `rm`-Aktion ohne Rückfrage,
  insbesondere nicht ohne Backup. Lieber `tar.gz` und beiseitelegen.
