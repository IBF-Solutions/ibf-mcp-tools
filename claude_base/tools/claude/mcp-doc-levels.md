# MCP Doc-Levels: Token-Reduktion via konfigurierbare Tool-Beschreibungen

> **Living Document.** Wann immer sich etwas am Verhalten, der Konfiguration
> oder den Doc-Level-Inhalten ändert: hier aktualisieren (Verhalten in der
> betroffenen Sektion, T-Status in §9, zeitliche Geschichte in §10).
> Implementations-Quelle: `claude_base/tools/ibf-mcp.py`.

| | |
|---|---|
| **Verantwortliche Datei** | `claude_base/tools/ibf-mcp.py` |
| **Aktueller Status** | siehe §9 (T-System) |
| **Zeitliche Geschichte** | siehe §10 (Changelog) |

---

## 1. Problem & Motivation

Der combined IBF-MCP-Server exponiert ~80 Tools mit ausführlichen deutschen
Docstrings, Args-Listen und Beispielen. Schätzung: pro Tool 200-600 Tokens
an Schema, gesamt ~30-40 k Tokens **pro Modell-Anfrage**, weil der MCP-Client
die komplette Tool-Liste in jedem Request mitsendet.

Bei Modellen mit kleinem Context (32 k / 64 k) bleibt kaum Platz für die
eigentliche Konversation. Bei Premium-Modellen mit 200 k Context macht es
zwar nicht den Unterschied „funktioniert/funktioniert nicht", aber:

- Token-Kosten pro Anfrage skalieren linear mit Schema-Größe
- Rate-Limit-Verbrauch
- Modell-Latenz (mehr Input → mehr Verarbeitung)

Ziel: **dieselbe Funktionalität, drei Verbosity-Stufen, pro Client wählbar.**

## 2. Lösungsansatz

Statt unterschiedliche MCP-Server zu bauen oder Tools wegzunehmen: **eine
Codebasis, drei Beschreibungs-Profile**, gewählt zur Laufzeit.

### Doc-Levels

| Level | Pro Tool | Gesamt | Inhalt |
|---|---|---|---|
| `full` | 200-600 Tok | ~30-40 k | Aktuelle ausführliche Docstrings mit Beispielen |
| `compact` | 80-150 Tok | ~8-12 k | Kurzbeschreibung + Args + 1 Beispiel |
| `min` | 20-40 Tok | ~2-3 k | Ein-Satz-Zusammenfassung, kein Beispiel |

`min` ist drastisch -- das Modell muss Args teilweise raten. Sinnvoll
nur in eingeübten Workflows. `compact` ist der pragmatische Default für
token-knappe Sessions.

### Implementierungs-Pattern

```python
# In ibf-mcp.py beim Start
DOC_LEVEL = _resolve_doc_level()  # siehe Sektion 3

def _doc(full: str, compact: str = None, minimal: str = None) -> str:
    """Wählt die richtige Beschreibung für den aktiven Doc-Level."""
    if DOC_LEVEL == "min":
        return minimal or compact or full.split("\n")[0]
    if DOC_LEVEL == "compact":
        return compact or full
    return full
```

Pro Tool ein-für-alle-Mal:

```python
@mcp.tool(description=_doc(
    full="""FortiGate: Log-Einträge mit Zeit-, Severity- und LogID-Filter.

    Args:
        category: 'traffic' | 'event' | UTM-Kürzel: 'attack' (=utm-ips), ...
        [25 weitere Zeilen]
    """,
    compact="""FG-Logs filtern. category=traffic|event|attack|virus.
    since/until='today'/'1h'/'YYYY-MM-DD'. min_level=alert|...""",
    minimal="FG-Logs filtern.",
))
def fortigate_show_log(...):
    ...   # Funktionslogik unverändert
```

Args-Schemas und Funktionsverhalten sind über alle Levels **identisch** --
nur der Beschreibungs-String ändert sich.

## 3. Konfigurations-Quellen (Hierarchie)

Drei orthogonale ENV-Vars, jede mit eigenem Sinn:

| ENV-Var | Werte | Wirkung | Default |
|---|---|---|---|
| `IBF_MCP_DOC_LEVEL` | `full` / `compact` / `min` | Beschreibungs-Länge pro Tool | `min` (hartcoded) |
| `IBF_MCP_TOOLSET` | `full` / `compact` / `min` | welche Tools registriert werden | `full` |
| `IBF_MCP_READONLY` | `1`/`true`/`yes`/`on` | blockt alle write-Tools | unset (= aus) |

DOC_LEVEL hat zusätzlich einen Datei-Fallback:

```
DOC_LEVEL-Hierarchie:
  1. ENV  IBF_MCP_DOC_LEVEL       ←  pro Subprozess fix
  2. Datei %TEMP%/ibf_mcp_doc_level_default  ←  globaler Fallback
  3. Hartcoded "min"
```

TOOLSET und READONLY werden nur per ENV gesetzt (kein Datei-Fallback) -- das
sind Sicherheits-/Capability-Schalter, die expliziter Konfiguration bedürfen.

### Kombinations-Matrix mit gemessenen Token-Werten

Stand 2026-05-06, ~48 Tools im Server:

| Konfig (DOC, TOOLSET, RO) | Tools | ~Tokens | Δ vs Default |
|---|---:|---:|---:|
| Default (full, full, off) | 48 | 4054 | — |
| compact, full, off | 48 | 2941 | -27 % |
| min, full, off | 48 | 2707 | -33 % |
| full, compact, off | 27 | 1744 | -57 % |
| **compact, compact, off** | **27** | **632** | **-84 %** |
| min, compact, off | 27 | 397 | -90 % |
| **min, min, off** | **15** | **169** | **-96 %** |
| full, full, **on** (RO) | 27 | 1744 | -57 % |

(`compact` und `min` Toolsets enthalten **per Definition keine write-Tools** --
RO ist dort impliziert. RO macht nur in Kombination mit `full`-Toolset
zusätzlich einen Unterschied.)

### Empfohlene Kombinationen

| Use-Case | DOC | TOOLSET | RO |
|---|---|---|---|
| Power-User, Implementation | full | full | off |
| Tagesarbeit, gemischt | compact | full | off |
| AI-Triage / Status-Check | min | min | (impliziert) |
| Sicherheitsbewusste AI-Session | full | full | **on** |
| Low-Token-Modell, lese-only | min | compact | (impliziert) |

### Hartcoded `"min"` als DOC_LEVEL-Default

Wenn weder ENV `IBF_MCP_DOC_LEVEL` noch der Default-File einen gültigen
Wert liefern, fährt der Server im Token-sparsamsten Modus an.

### ENV-Var (Quelle 1)

Wird in der Client-Konfiguration gesetzt. Gilt nur für genau diesen
Subprozess.

```jsonc
// Claude Code mcp.json oder Anthropic Desktop config:
"ibf": {
  "command": "python",
  "args": ["C:/Temp/claude/ibf/claude_base/tools/ibf-mcp.py"],
  "env": { "IBF_MCP_DOC_LEVEL": "compact" }
}
```

Vorteil: pro Client unabhängig konfigurierbar. Tab 1 in Claude Code kann
`full` haben während ein paralleler Mini-Modell-Client `min` nutzt --
es sind separate Subprozesse, sie teilen kein RAM.

### Default-Datei (Quelle 2)

`%TEMP%/ibf_mcp_doc_level_default` (Path-Helper im Code).
Wird gelesen wenn keine ENV-Var gesetzt ist. Wird beschrieben durch das
`ibf_set_doc_level(scope="global")`-Tool (siehe Sektion 5).

Sinn: ein einzelner User-Wunsch („standardmäßig kompakt für alle künftigen
MCP-Starts") ohne dass der mcp.json überall manuell anpassen muss.

### Hartcoded `"min"` (Quelle 3)

Wenn weder ENV noch Datei einen gültigen Level angeben, fährt der MCP-
Server im Token-sparsamsten Modus an: Tool-Namen + 1-Satz-Beschreibung,
Args ohne Erklärung. Das ist **bewusst nicht rückwärtskompatibel** zum
Vor-Doc-Level-Verhalten -- die Annahme ist: wer ausführliche Beschreibungen
will, setzt das aktiv per ENV oder Default-File.

Konsequenz für bestehende Aufrufmuster:
- Eingeübte Workflows („zeig dashboard", „buddy x on") funktionieren
  weiter, weil Tool-Namen aussagekräftig sind und die Auto-Memory-
  Patterns bleiben.
- Neue/komplexe Tool-Calls können scheitern, weil das Modell Args raten
  muss. In dem Fall: einmal `IBF_MCP_DOC_LEVEL=full` (oder `compact`) in
  der mcp.json setzen, oder via `ibf_set_doc_level("compact", "global")`
  den Default-File ändern.

Sicherheitsnetz: die Args-Schemas selbst (Argument-Namen + Typen) sind
in allen Levels gleich -- nur die Beschreibungen schrumpfen. Mit Args wie
`vmid`, `node`, `op`, `since`, `until` rät ein vernünftiges Modell meist
korrekt.

## 4. Multi-Client-Verhalten

stdio-MCP-Server starten **pro Client-Connection einen eigenen Subprozess**.
Drei Beispiele parallel auf demselben Rechner:

| Client | mcp.json env | Prozess | aktiver Level |
|---|---|---|---|
| Claude Code Tab 1 | `IBF_MCP_DOC_LEVEL=full` | A | full |
| Claude Code Tab 2 | (nicht gesetzt) | B | aus Datei: `compact` |
| GPT-Mini-Client | `IBF_MCP_DOC_LEVEL=min` | C | min |

Alle drei sehen unterschiedliche Tool-Schemas. Die Prozesse sind isoliert,
beeinflussen sich nicht. Filesystem (Datei-Default) ist die einzige
geteilte Komponente.

## 5. Tool-Schnittstelle

Drei neue Meta-Tools im MCP, immer in allen Doc-Levels verfügbar (sie sind
selbst kompakt -- ~30 Tok Schema je):

### `ibf_set_doc_level(level, scope="session")`

```
level:  "full" | "compact" | "min"
scope:  "session" (default) | "global"
```

- `scope="session"`: ändert die Beschreibungen im **aktuell laufenden
  Server-Prozess**, sendet `notifications/tools/list_changed` an den
  Client. Andere parallele Clients werden nicht beeinflusst.
  → Live-Update im Best-Case (siehe Sektion 6).

- `scope="global"`: schreibt in den Default-File. Beeinflusst nur
  **künftige Subprozess-Starts ohne ENV-Override**. Aktive Sessions
  ignorieren das.

Returns ein Status-String mit Hinweis ob Live-Update geklappt hat oder
ob Reconnect nötig ist.

### `ibf_get_doc_level()`

Zeigt aktuellen Level + Quelle (env/file/default), damit klar ist warum
der aktive Wert so ist.

### `ibf_reload_tools()`

Erzwingt `list_changed`-Notification ohne Level-Wechsel. Diagnose-Tool
falls man wissen will, ob der Client das Notification-Handling
überhaupt unterstützt.

### Natural-Language-Aliasse (via Auto-Memory)

Damit Claude die Buddy-Sprache versteht (analog zu
`feedback_buddy_mcp_pattern.md`):

| User sagt | Tool-Call |
|---|---|
| „buddy ibf compact" | `ibf_set_doc_level("compact")` |
| „token-mode min global" | `ibf_set_doc_level("min", "global")` |
| „welches doc-level läuft?" | `ibf_get_doc_level()` |
| „zurück auf normal" | `ibf_set_doc_level("full")` |

## 6. Live-Update vs. Reconnect

MCP-Protokoll definiert `notifications/tools/list_changed` als Server-zu-
Client-Notification. Client SOLL daraufhin via `tools/list` neu fetchen.

| Mechanismus | Status |
|---|---|
| Protokoll-Spec | klar definiert |
| FastMCP-Library | unterstützt es (interne API, nicht 100 % public-API-stabil) |
| Claude Code | unterstützt dynamische Tool-Listen (siehe ToolSearch / deferred tools) -- exakter Live-Refresh ohne Reconnect: **noch zu testen** |
| Andere Clients | sehr variabel |

**Strategie**: Live-Versuch + Persistent-Fallback.

```python
def ibf_set_doc_level(level, scope="session"):
    if scope == "global":
        DEFAULT_FILE.write_text(level)
    if scope == "session":
        try:
            _rebuild_tool_descriptions(level)
            await mcp._notify_tools_list_changed()
            return f"[OK] doc-level={level} live aktiv"
        except Exception as e:
            return (f"[OK] persistiert für nächsten Start. "
                    f"Live-Update fehlgeschlagen: {e}. "
                    f"In Claude Code: '/mcp reconnect' oder neuer Tab.")
```

Caveat: **rückwirkend nichts gespart**. Tool-Schemas die in Vor-Messages
schon im Modell-Kontext-Cache liegen, bleiben dort verbucht. Nur ab dem
Moment der Umstellung neue Schemas -> neue Token-Bilanz.

## 7. Doc-Level-Inhalte (Style-Guide)

Damit Beschreibungen pro Level konsistent bleiben.

### `full` (heutiger Stand)

- Mehrzeilige Doku mit Args-Block, Beschreibung pro Arg, Beispielen
- Auf Deutsch
- Inklusive Default-Werten und Edge-Cases

### `compact`

- 1-2 Sätze Kernbeschreibung
- Args-Liste **ohne** Beschreibung pro Arg, nur Werte-Enums
- 0-1 kurzes Beispiel oder ein Pattern
- Maximal ~120 Tokens

```
"FG-Logs filtern. category=traffic|event|attack|virus.
since='today'/'1h'/'YYYY-MM-DD'. min_level=alert|error|warning."
```

### `min`

- 1 Satz, was das Tool macht
- Keine Args-Erklärung, keine Beispiele
- Maximal ~30 Tokens
- Modell muss aus Tool-Name + Args-Namen schließen

```
"FG-Logs filtern."
```

## 8. Edge Cases & FAQs

**Was wenn der Level-File korrupt ist?** → Fallback auf hartcoded "min",
Server loggt Warnung auf stderr.

**Was wenn ein User mid-session via `set_doc_level("min")` umstellt, der
Client das nicht sofort übernimmt?** → Tool-Schemas bleiben bis zum
Reconnect auf altem Stand. Funktional kein Problem -- die `_doc()`-Auswahl
greift erst beim NÄCHSTEN Server-Start.

**Können Schema-Args zwischen Levels schrumpfen (z.B. weniger optionale
Felder in `min`)?** → **Nein.** Args müssen immer identisch sein, sonst
brechen Tool-Calls die in höherem Level konstruiert wurden.

**Was ist mit `mcp.instructions` (der „WICHTIG: prüfe..."-Block)?** →
Wird genauso über `_doc()` gewählt. Voller Text in `full`, knapper in
`compact`, leer (`""`) in `min`.

**Sind die `dashboard_*`-Tools davon betroffen?** → Ja, alle Tools im
`ibf-mcp.py` werden über `_doc()` parametrisiert.

## 9. T-System TODOs

Diese werden in `claude_base/CLAUDE-inbox.md` als T-Liste verlinkt
(separates Subprojekt-CLAUDE.md für `tools/` existiert noch nicht).

**Status-Übersicht (Stand: Phase 2, 2026-05-06):**

| ID | Status | Kurz |
|---|---|---|
| D1 | ✓ done | `_doc()`-Helper + Resolver |
| D2 | ✓ done | Meta-Tools (`set/get/reload`) |
| D3 | ✓ done | Live-Update via FastMCP-Internas |
| D4 | partial 14/48 | Tool-Refactor; Long-Tail offen |
| D5 | offen | Auto-Memory Buddy-Pattern |
| D6 | offen | Multi-Client-Test (zwei Tabs parallel) |
| D7 | offen | Live-Update praktisch in Claude Code testen |
| D8 | ✓ done | Token-Verbrauch gemessen (siehe §3 Matrix) |
| D9 | offen | HTTP-MCP-Variante evaluieren |
| D10 | ✓ done | TOOLSET-Whitelist (`min`/`compact`/`full`) |
| D11 | ✓ done | READONLY-Mode (`write=True`-Annotation) |
| D12 | ✓ done | `ibf_status`-Diagnose-Tool |
| D13 | ✓ done | Live-Wechsel TOOLSET + READONLY (`ibf_set_toolset`, `ibf_set_readonly`) -- programmatisch verifiziert; Client-Refresh-Verhalten siehe D7 |
| D14 | ✓ done | Auto-Detect Client (Claude Code → compact/full, Open Code → min/min) -- Client-Profile-Mapping bei erstem Tool-Call; explizite ENV-Overrides werden respektiert |
| D7 | ✓ done | Live-Update in Claude Code praktisch getestet (2026-05-06). **Gemischtes Ergebnis:** server-seitig wirken `set_toolset`/`set_readonly` sofort (Tools werden registriert/entfernt, Tool-Calls korrekt akzeptiert/abgewiesen mit `Unknown tool`). Capability-Wechsel (z.B. RO=on blockt write-Tools live) funktioniert daher als Sicherheitsschicht. **ABER:** Claude Codes Schema-Cache (ToolSearch) wird durch `notifications/tools/list_changed` nicht aktualisiert -- alte Tool-Schemas bleiben sichtbar/findbar bis zum manuellen Reconnect. → **Token-Reduktion via mid-session-`set_toolset` greift NICHT** in Claude Code. Workaround: Reconnect oder beim Start via ENV-Var `IBF_MCP_TOOLSET` setzen. Auto-Detect (D14) wirkt korrekt weil dort der Server-Start erfolgt VOR dem ersten Schema-Pull des Clients -- nur Mid-Session-Wechsel sind betroffen. |

- [x] **D1** [P2] [mcp] Doc-Level-Mechanik implementiert ✓ 2026-05-06.
  `_doc(full=, compact=, minimal=, _key=)`-Helper + `_resolve_doc_level()`
  (ENV → Datei → "min" hartcoded). Quell-Tracking via `_DOC_LEVEL_SOURCE`
  (env/file/default/set_session). Tool-Descriptions werden in
  `_TOOL_DESCRIPTIONS`-Dict für späteren Live-Wechsel vorgehalten.

- [x] **D2** [P2] [mcp] Meta-Tools eingebaut ✓ 2026-05-06.
  `ibf_set_doc_level(level, scope='session'|'global')`,
  `ibf_get_doc_level()`, `ibf_reload_tools()`. Persistent-File-Update
  via `scope='global'`, Live-Wechsel bei `scope='session'`.

- [x] **D3** [P2] [mcp] Live-Update mit `tools/list_changed` ✓ 2026-05-06.
  `_apply_doc_level_live()` ersetzt Beschreibungen via
  `mcp._tool_manager._tools[name].description = ...`.
  `_send_tools_list_changed()` nutzt `session.send_notification(
  ServerNotification(ToolListChangedNotification()))` aus dem aktiven
  request_context. Beide Internas/Best-Effort -- bei API-Wechsel
  in MCP-SDK ggf. nachziehen.

- [ ] **D4** [P2] [mcp] Tool-Beschreibungen refactorn -- 14/47 erledigt
  (Stand 2026-05-06):
  Erledigt: `dashboard_morning`, `dashboard_section`, `dashboard_history`,
  `proxmox_cluster_status`, `proxmox_list_vms`, `proxmox_list_tasks`,
  `fortigate_status`, `fortigate_show_log`, `graylog_search_messages`,
  `graylog_count_messages`, `graylog_top_values`, `ibf_set_doc_level`,
  `ibf_get_doc_level`, `ibf_reload_tools`.
  Offen: ~33 weitere Tools (`proxmox_vm_*`, `proxmox_ssh_*`,
  `proxmox_evacuate_node`, `proxmox_maintenance`, `proxmox_restore_*`,
  `fortigate_run`, `fortigate_list_*`, `graylog_indexer_health`,
  `graylog_list_streams`, `graylog_system_status`, `authenticate`,
  `ibf_help`).
  Aufwand-Rest: ~30-45 Min. Schema: `description=_doc(full=..., compact=...,
  minimal=..., _key="<toolname>")` über dem Decorator, Funktions-Docstring
  raus oder belassen (FastMCP nimmt die Description aus dem Decorator-Arg
  vorrangig).

- [ ] **D5** [P3] [mcp] Auto-Memory-Eintrag für Buddy-Pattern-
  Erkennung („buddy ibf compact" etc.) -- analog zu
  `feedback_buddy_mcp_pattern.md`.

- [ ] **D6** [P3] [test] Integration-Test: zwei mcp.json-Einträge mit
  unterschiedlichen ENV-Var-Levels parallel laufen lassen, beide via
  Claude Code-Tabs verbinden, prüfen dass beide unabhängig sind und
  korrekt verschiedene Schemas ausliefern.

- [ ] **D7** [P3] [test] Live-Update-Test in Claude Code: `set_doc_level`
  während laufender Session, prüfen ob `list_changed` honoriert wird
  und Schemas tatsächlich live wechseln.

- [ ] **D8** [P3] [doc] Beispiel-Tabelle „Token-Verbrauch pro Level
  gemessen" -- nach D4 messen mit `tools/list`-Roundtrip + Token-Count,
  hier und im claude_base-Master kurz dokumentieren.

- [ ] **D9** [P3] [feature] HTTP-MCP-Variante prüfen -- bei stdio ist
  Multi-Client-Trennung über separate Prozesse erzwungen. HTTP-MCP
  könnte pro Connection einen Level halten (echte „session"-Granularität),
  bringt aber andere Auth-Komplexität. Nur evaluieren wenn Bedarf
  konkret wird.

## 10. Changelog

### Quick

Eine Zeile pro Änderung, ~3-5 Wörter. Schneller Überblick beim Drüberscrollen.
Neueste **unten** (chronologische Geschichte).

| Datum | Was |
|---|---|
| 2026-05-06 | Doc angelegt |
| 2026-05-06 | Default `min` |
| 2026-05-06 | Phase 1: D1-D3 ✓, D4 14/47 |
| 2026-05-06 | Phase 2: TOOLSET + READONLY |
| 2026-05-06 | Live-Wechsel + Auto-Detect-Client |
| 2026-05-06 | D7 verifiziert: Auto-Detect + Capability-Live ✓, Schema-Cache-Refresh ✗ |

### Detail (Audit-Trail)

Volle Begründungen, technische Details, Messwerte. Für Regression-Suche
und „warum war das eigentlich so" — nicht zum täglichen Lesen.

- **2026-05-06 — Doc angelegt**
  Spec für Konzept + 3 Levels + Multi-Client-Verhalten + T-Liste D1-D9.
  Noch keine Implementation.

- **2026-05-06 — Default auf `min`**
  Hartcoded Fallback (Quelle 3) von `"full"` auf `"min"` geändert.
  Begründung: Token-sparsamster Default für die Mehrheit der Sessions;
  wer mehr Detail braucht, setzt ENV oder Default-File aktiv.

- **2026-05-06 — Phase 1 implementiert**
  D1-D3 vollständig: `_doc()`-Helper, `_resolve_doc_level()`, drei
  Meta-Tools (`ibf_set_doc_level`, `_get_`, `_reload_`), Live-Update
  via FastMCP-Internals + `session.send_notification(
  ToolListChangedNotification())`. D4 partial: 14 von 47 Tools refactored
  (alle hochfrequenten). Live-Token-Test: `full`~4k / `compact`~3k /
  `min`~2.7k Tokens (limitiert durch noch nicht refactorte Tools).

- **2026-05-06 — D7 verifiziert in echtem Claude Code**
  Live-Test mit `ibf_set_toolset("min")`. Server bestätigt
  „[OK] toolset=min: +0 -33 (aktiv: 17). list_changed gesendet". Server-
  seitig sind die Tools tatsächlich entfernt (Aufruf eines nicht-min-
  Tools liefert `Unknown tool: proxmox_list_storage`). ToolSearch des
  Claude-Code-Clients zeigt aber das alte Schema weiter an, lädt es
  ohne Hinweis als verfügbar -- Cache wird durch `list_changed` nicht
  invalidiert. Konsequenz: Mid-Session-Toolset/Readonly-Wechsel wirkt
  als **Capability-Schicht** (sichere Aufrufe), aber **nicht als
  Token-Reduktion** im Prompt. Token-Reduktion bleibt auf den Server-
  Start beschränkt (ENV-Var oder Auto-Detect über `clientInfo`
  beim Initialize). Keine Code-Änderung nötig -- Verhalten ist als
  Caveat dokumentiert.

- **2026-05-06 — Live-Wechsel + Auto-Detect-Client (D13, D14)**
  Zwei neue Meta-Tools `ibf_set_toolset(name)` und `ibf_set_readonly(state)`,
  die das Toolset bzw. den RO-Modus zur Laufzeit umschalten -- via
  `_apply_toolset_and_readonly_live()` werden Tools im
  `mcp._tool_manager._tools`-Dict hinzugefügt/entfernt, anschließend
  `notifications/tools/list_changed` an Client. Programmatisch verifiziert
  (50→29→17→29→50 Tool-Übergänge).

  Plus Auto-Detect: beim ersten Tool-Call wird der MCP-Client-Name aus
  `request_context.session._client_params.clientInfo` gelesen und gegen
  `_CLIENT_PROFILES` gematcht. Default-Profile:
  - „claude code" / „claude.ai" → compact-Doc, full-Toolset, RW
  - „open code" / „opencode" → min-Doc, min-Toolset, RW (RO impliziert da
    min-Toolset keine writes enthält)
  Explizite ENV-Vars werden NIE überschrieben. Auto-Detect läuft idempotent
  einmal pro Server-Lifetime. Client-Refresh-Verhalten in echtem Claude Code
  noch zu testen (D7 als Reminder).

- **2026-05-06 — Phase 2: zweite Achse `IBF_MCP_TOOLSET` + `IBF_MCP_READONLY`**
  Zwei neue ENV-Vars orthogonal zu DOC_LEVEL. TOOLSET filtert die Anzahl
  der registrierten Tools (`min`/`compact`/`full`). READONLY blockt alle
  als `write=True` annotierten Tools (~21 destruktive). Wrapper `tool()`
  ersetzt `@mcp.tool()` als Drop-in mit zusätzlichem `write=`-Flag.
  Kombiniert mit Doc-Level ergibt das eine 3×2-Matrix von Konfigurationen.
  Plus neues `ibf_status`-Tool zur Anzeige des aktiven Modus. Use-Cases-
  Tabelle in §3.

---

## Anhang A: Verwandte Dokumente

- `claude_base/CLAUDE.md` -- Master-Prompt, kein Kontextsystem-Spec
- `claude_base/CLAUDE-rules.md` -- T-System-Konvention
- `projekte/graylog/claude/mailflow-tooling.md` -- Vorbild für dieses
  Living-Doc-Pattern
- Auto-Memory `feedback_buddy_mcp_pattern.md` -- Vorbild für „buddy"-
  Sprachmuster (vgl. T5)

## Anhang B: Token-Schätzung (zu verifizieren in D8)

Annahmen:
- 80 Tools, durchschnittlich 350 Tokens Schema in `full`
- Plus `mcp.instructions` ~500 Tokens
- Plus Help-Tools, Auth-Tools etc. ~1 k

Pro Modell-Request, jede Iteration:

| Level | Schema-Total | Δ vs full | Effekt bei 32k-Context |
|---|---|---|---|
| full | ~28 k | -- | 87 % Schema, 13 % Konversation |
| compact | ~8 k | -71 % | 25 % Schema, 75 % Konversation |
| min | ~2 k | -93 % | 6 % Schema, 94 % Konversation |
