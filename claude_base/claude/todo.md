# Todo

## T — Aufgaben

| #   | Status | Beschreibung |
|-----|--------|-------------|
| T1  | open   | VM-MSCHWEIGER (10.10.44.32): Azure DevOps RST-Loop-Ursache identifizieren (welcher Prozess) |
| T2  | open   | DNS-Zentralisierung: DHCP-Scopes auf 10.10.10.32 umstellen, `config system dns-server` bereinigen |
| T3  | open   | Graylog: eigenen Stream für Mail-Logs anlegen (source:web12-hz OR source:itl15-gdata-smtp) |
| T4  | open   | FortiGate Policy 908 (Block Printer): Graylog-Traffic analysieren, danach `logtraffic disable` wiederherstellen |
| T5  | open   | VPN IBF-Pflach-gw: Löschung ~2026-06-01, bis dahin keine Remediation |
| T6  | open   | RDP-Alert (69f8c8f1): grace_period-Tuning nach 24h Beobachtung prüfen |
| T7  | done   | graylog-query.py: sichere Token-Verwaltung implementiert — Tier 1 Env-Var, 2 Bitwarden CLI, 3 keyring, 4 .env. Alle Tiers getestet (2026-05-04). |
| T7a | drop   | Bitwarden als Token-Quelle — CLI gibt "Not found", Desktop-IPC braucht Browser-Extension. Aufgegeben. Keyring (Tier 3) ist die aktive Lösung. |
| T8  | done   | graylog-query.py: `--from heute`/`--from gestern` als Kurzform; `--to` ohne Angabe = jetzt (now) |

---

## W — Regelmäßige Prüfungen/Tests

| #   | Intervall | Beschreibung |
|-----|-----------|-------------|
| W1  | ad-hoc    | graylog-query.py: Testsuite ausführen und Fehler analysieren (`python tools/test-graylog-query.py`) |
| W2  | bei Python-Update | graylog-query.py: Script auf neue Python-Features prüfen (stdlib-Änderungen, neue Syntax) und ggf. anpassen |
