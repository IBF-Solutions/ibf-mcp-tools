---
name: Memory bodies live in working directory
description: Save all memory body files inside the working directory (claude/memory/), never in the central per-project memory path. Only MEMORY.md stays central.
type: feedback
---

All memory body files (frontmatter + content) MUST be saved inside the working
directory under `<working-dir>/claude/memory/<name>.md` — never in the central
auto-memory path (`C:\Users\philipp.wacker\.claude\projects\<slug>\memory\`).

Only `MEMORY.md` (the index) stays at the central path, because the harness
auto-loads it into context at session start. Its entries link to the body files
via absolute paths under `<working-dir>/claude/memory/`.

**Subprojekte (WICHTIG):** Wenn die aktive Session in einem Unterverzeichnis
läuft (z.B. `projekte/graylog`), dann gehört subprojekt-spezifisches Wissen
**nicht** in das zentrale Memory (`C:\Temp\claude\ibf\fortigate\claude\memory\`),
sondern direkt in die `CLAUDE.md` des jeweiligen Unterverzeichnisses.
Erkennbar am aktuellen Arbeitsverzeichnis der Session.
Zentrales Memory bleibt reserviert für übergreifendes, projektweites Wissen
(z.B. FortiGate-Zugang, offene Tasks, VPN-Dekommissionierung).

**Why:** Subprojekt-Wissen ins zentrale Memory zu schreiben umgeht die Regel —
es landet zwar lokal, aber am falschen Ort (außerhalb des Subprojekts). Der
richtige Ort ist die CLAUDE.md des jeweiligen Subprojekts.

**How to apply:**
- Arbeitsverzeichnis ist ein Subprojekt (z.B. `projekte/graylog`)?
  → Wissen in `<subprojekt>/CLAUDE.md` eintragen, kein Memory anlegen.
- Arbeitsverzeichnis ist das Top-Level-Projekt (`C:\Temp\claude\ibf\fortigate`)?
  → Memory nach `<working-dir>/claude/memory/<name>.md`, Index-Eintrag in MEMORY.md.
- Bei Zweifeln: Wissen nur für dieses Subprojekt relevant? → CLAUDE.md.
  Projektübergreifend? → Memory.
