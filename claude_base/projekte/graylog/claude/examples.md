# graylog-query.py — examples (AI-optimized)

Tool path (relative to project root): `projekte/graylog/tools/graylog-query.py`
Read-only. Token from `.env` (graylog_ibf=…). Endpoint: `gld.ibf-solutions.com`.

## Decision tree (start here)

| Goal | Action |
|---|---|
| "Wieviele Treffer für X?" | `--action count` |
| "Welche Felder gibt's überhaupt für source Y?" | `--action fields --source Y --limit 3` |
| "Top N Werte eines Feldes?" | `--action terms --terms <field>` (client-side, max 5000 sample) |
| "Liste aller Streams?" | `--action streams` |
| Default — search and show messages | (no `--action` needed) |

## Time window — pick exactly one

| Option | Use when |
|---|---|
| `--today` / `--yesterday` | Local-day boundary (00:00 lokal) |
| `--last 15m` / `--last 2h` / `--last 7d` / `--last 90s` | Rolling window, human-readable |
| `--range 86400` | Rolling window in seconds (legacy, default 24h) |
| `--from 2026-05-01T00:00:00 --to 2026-05-04T12:00:00` | Absolute UTC window |

## Quoting rules (critical)

Graylog tokenizes on `@`, `.`, `-`, whitespace by default. Without quotes, multi-word/email queries match individual tokens OR-ed together → false positives.

| Goal | Right | Wrong |
|---|---|---|
| Match phrase `Blocked INFECTED` | `--query '"Blocked INFECTED"'` | `--query 'Blocked INFECTED'` |
| Match email | `--query '"user@domain.com"'` or `--mail-to user@domain.com` | `--query 'user@domain.com'` |
| Match field-value with spaces | `--query 'policyname:"My Policy"'` | `--query 'policyname:My Policy'` |

Shell rule: outer single quotes preserve literal contents; inner double quotes go to Graylog as phrase markers.

## Output options

- Default: 5 auto-picked fields per source (FortiGate-aware vs generic).
- `--fields timestamp,message` — explicit field selection.
- `--all-fields` — every field per result (skips `gl2_*` and `_id`).
- `--no-truncate` — keep values longer than 200 chars (mandatory for amavis/postfix `message`).
- `--raw` — JSON output for piping/parsing.
- `--limit N` — max messages (default 50).

## Common queries — copy-paste

### Mail (postfix on web12-hz, amavis on itl15-gdata-smtp)

```bash
# "Hat <user> heute Mails bekommen?" — postfix delivery log
python projekte/graylog/tools/graylog-query.py \
  --mail-to user@ibf-solutions.com --today

# Count only, faster
python projekte/graylog/tools/graylog-query.py --action count \
  --mail-to user@ibf-solutions.com --today

# Outgoing mail from a user
python projekte/graylog/tools/graylog-query.py \
  --mail-from user@ibf-solutions.com --today
```

### amavis verdicts on itl15-gdata-smtp

amavis logs verdicts as phrases inside `message`. Use exact-phrase queries.

```bash
# Virus-Funde (echte Blocks)
python projekte/graylog/tools/graylog-query.py --action count \
  --source itl15-gdata-smtp --query '"Blocked INFECTED"' --last 30d

# Spam-Funde (Tagged + zugestellt — wird nicht hart geblockt)
python projekte/graylog/tools/graylog-query.py --action count \
  --source itl15-gdata-smtp --query '"Passed SPAMMY"' --last 30d

# Detail-Output für die Treffer
python projekte/graylog/tools/graylog-query.py \
  --source itl15-gdata-smtp --query '"Blocked INFECTED"' \
  --last 30d --limit 20 --fields timestamp,message --no-truncate
```

Verdict-Vokabular (alle als Phrase nutzen):
`"Passed CLEAN"`, `"Passed SPAMMY"`, `"Passed INFECTED"`,
`"Blocked SPAMMY"`, `"Blocked INFECTED"`, `"Blocked BANNED"`,
`"Blocked BAD-HEADER"`, `"Blocked MTA"`.

### FortiGate logs (source=gw)

```bash
# Specific src/dst flow rate
python projekte/graylog/tools/graylog-query.py --action count \
  --query 'srcip:10.10.10.33 AND dstip:10.10.20.16 AND dstport:8006' --last 1h

# Top policies that denied traffic
python projekte/graylog/tools/graylog-query.py --action terms \
  --query 'action:deny' --terms policyid --terms-size 10 --last 24h

# Logs for a specific firewall rule (UUID)
python projekte/graylog/tools/graylog-query.py \
  --query 'poluuid:01ce489e-ab34-51ef-3843-d9c5d79e8c13' --last 1h
```

### Discover unfamiliar source/stream structure

```bash
# What fields does source X have?
python projekte/graylog/tools/graylog-query.py --action fields \
  --source <hostname> --last 5m --limit 3

# What's the top-N value of field <f> in messages from source X?
python projekte/graylog/tools/graylog-query.py --action terms \
  --source <hostname> --terms <field> --terms-size 25 --last 1h

# Distinct programs running on a Linux source (from postfix-style messages)
python projekte/graylog/tools/graylog-query.py \
  --source <hostname> --last 6h --limit 200 --fields message \
  | grep -oE '[a-z][a-z0-9_/.-]+\[' | sort -u
```

### Stream-scoped search

```bash
# Restrict to a stream by id-substring of title
python projekte/graylog/tools/graylog-query.py \
  --query 'level:3' --stream 'Bitdefender' --last 24h

# List all streams
python projekte/graylog/tools/graylog-query.py --action streams
```

## Mail-source convention (current)

| Source | Role | Notes |
|---|---|---|
| `web12-hz` | mailcow-postfix container, primary delivery | Auto-applied as filter when `--mail-to`/`--mail-from` is used |
| `itl15-gdata-smtp` | amavis pre-filter with GData engine | Linux box; verdicts in `message` field |

## Pitfalls

1. **Phrase quoting** — see "Quoting rules" above. Skipping this is the #1 cause of bogus high counts.
2. **`--source` repeats are AND-joined** with the `source:"X"` filter → use `--query 'source:"X" OR source:"Y"'` to OR.
3. **`--action terms` is client-side** — sample capped at 5000 most-recent messages. If `total_msgs > sample_size`, the tool warns; counts are biased toward recent.
4. **`message` field truncates at 200 chars by default**. For amavis/postfix verdicts, the queue-id, sender, recipient, virus-name are all near the end of the line — always pass `--no-truncate`.
5. **Default output is FortiGate-centric** (auto-fields include srcip/dstip/etc.). For non-FortiGate sources, override with `--fields timestamp,message,source`.

## Help

`--help` (DE) — full CLI reference for humans.
`--help-ai` — terse param doc, machine-friendly.
