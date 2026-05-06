# Verhaltens- und Format-Regeln

> **Wann diese Datei lesen?**
> - Wenn unklar ist, wie Antworten formuliert werden sollen (Sprache/Tonfall)
> - Bevor Code/Sektionen entfernt oder umstrukturiert werden
> - Beim Anlegen oder Ändern von TODOs (T-System-Format)
>
> Die drei Kern-Verhaltensregeln stehen verkürzt auch im Master `CLAUDE.md`,
> damit sie ohne Lesen dieser Datei im Auto-Kontext sind. Ausführliche
> Begründung und das vollständige T-System-Format hier.

---

## Verhaltensregeln

### 1. Sprache: Deutsch, Anrede: Philipp

Antworten grundsätzlich auf Deutsch. Englische Fachbegriffe (CLI, MCP,
Token, Container, Snapshot, ...) bleiben englisch -- keine künstliche
Eindeutschung. Code-Kommentare und Identifier in englisch ist OK, sofern
das Projekt sie ohnehin so führt.

### 2. Tonfall: Sachlich, keine Lobhudeleien, keine Füllfloskeln

- Keine Eröffnungsformeln wie „Sehr gerne!" / „Klar, das machen wir!"
- Keine Selbst-Lobpreisungen („Ich habe das jetzt sauber implementiert...")
- Keine zusammenfassenden Schluss-Absätze, die wiederholen was im Diff steht
- Direkt zum Punkt; bei Unklarheit nachfragen statt vermuten

### 3. Code-Kontinuität: Nichts ohne Rückfrage entfernen

Vorhandener Code, Dateien und Konfigurations-Einträge werden **nicht
stillschweigend** entfernt -- auch nicht, wenn sie auf den ersten Blick
unbenutzt aussehen.

Konkret:
- Vor `rm`/`del`/`Remove-Item` auf nicht-trivialen Dateien: Liste zeigen,
  Zustimmung abwarten.
- Vor dem Löschen von Funktionen, Imports, ungenutzten Variablen: kurz
  fragen, ob das gewollt ist.
- Bei Refactoring nie „nebenbei" Code wegoptimieren.
- Auskommentiert-aber-erhalten ist OK, solange ersichtlich bleibt warum.

**Begründung:** Vermeintlicher Toter-Code ist oft Work-in-Progress, ein
Workaround für ein nicht-offensichtliches Problem oder ein Anker für ein
zukünftiges Feature. Lieber einmal zuviel fragen.

---

## T-System (TODO-Format)

Format pro Eintrag:

```
- [ ] **Tn** [Pn] [kategorie] Titel — Detail  [#XXXX]
```

| Feld | Bedeutung |
|---|---|
| `Tn` | Laufende Nummer mit T-Prefix (`T1`, `T2`, `T3`, ...) -- KEINE führenden Nullen |
| `Pn` | Priorität: `P1` = hoch, `P2` = mittel, `P3` = niedrig. **`P2` wenn nicht angegeben.** |
| `[kategorie]` | Optional, Stichwort wie `[fortigate]`, `[security]`, `[mcp]` |
| `Titel — Detail` | Mit ` — ` (Em-Dash umgeben von Spaces) getrennt |
| `#XXXX` | Optional, Redmine-Ticket / Wiki-Referenz am Zeilenende |

### Speicherort & Nummerierung

- Pro Subprojekt eine eigene `CLAUDE.md` mit eigener `T#`-Nummerierung.
- `T1` in `proxmox/CLAUDE.md` ist nicht dasselbe wie `T1` in
  `fortigate/CLAUDE.md`.
- Bei langen Detail-Plänen optional `./claude/<Thema>.md` zusätzlich anlegen.
- Keine zentrale `claude_todos.md` -- die ist beim IBF-Buddy-Schema
  vorgesehen, hier wird **lokal pro Projektordner** geführt (Diskussion
  in `CLAUDE-inbox.md`).

### Anzeige in Tabellen

Wenn TODOs in Tabellenform aufgelistet werden, **immer T-ID als erste
Spalte**. Bei projektübergreifender Mischung Projekt-Prefix dazu
(`proxmox:T3`, `fortigate:T1`).

### „add last todo"

Wenn der Nutzer „add last todo" o.ä. schreibt, wird der **zuletzt
angelegte** TODO-Eintrag erweitert (zusätzliches Detail, Status, Notiz)
statt einen neuen anzulegen. Hintergrund in Auto-Memory
`feedback_add_last_todo.md`.
