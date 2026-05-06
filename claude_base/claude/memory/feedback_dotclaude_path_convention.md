---
name: ".claude/<file> path convention"
description: When the user writes ".claude/X" they mean <current-project-folder>/claude/X — never the literal hidden .claude/ directory
type: feedback
originSessionId: 39f28eb6-f98d-4009-a896-ff19aa4ea509
---
When the user writes a path like `.claude/<file>` (e.g. `.claude/examples.md`,
`.claude/notes.md`), they do **not** mean the literal hidden directory `.claude/`
in the project root. They mean a `claude/` subfolder *inside the project folder
that the current task is about*.

Examples seen so far:
- Working in `projekte/graylog/` → `.claude/examples.md` means
  `projekte/graylog/claude/examples.md`
- Working in `projekte/<other>/` → would mean `projekte/<other>/claude/<file>`

**Why:** The user organizes per-project AI-targeted docs under each project's
own `claude/` subfolder, not globally. The leading dot in their shorthand is
shorthand for "the project's claude folder", not the OS hidden-folder convention.

**How to apply:**
- Before writing a file when the user says `.claude/X`, identify which project
  folder the current task is in, and write to `<that-folder>/claude/X`.
- Create `claude/` (no leading dot) inside the project folder if it doesn't
  exist yet.
- If the project folder is ambiguous, ask before writing.
- Do NOT write to a literal `.claude/` directory unless the user explicitly
  uses an absolute path or clearly references it.
