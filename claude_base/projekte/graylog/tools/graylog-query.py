#!/usr/bin/env python3
"""Graylog Query Tool — IBF Graylog (gld.ibf-solutions.com).

Read-only CLI for ad-hoc log searches. See --help for usage.
"""
import argparse
import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


GRAYLOG_BASE = "https://gld.ibf-solutions.com/api"

# Mail-Pipeline-Sources: postfix (web12-hz) + amavis-/GData-Filter (itl15-gdata-smtp).
# Ein --mail-to/--mail-from setzt automatisch beide, weil amavis-Verdicts und
# postfix-Logs auf zwei verschiedenen Hosts landen (Plan-Schritt 3).
MAIL_SOURCES = ["web12-hz", "itl15-gdata-smtp"]
AMAVIS_DEFAULT_SOURCE = "itl15-gdata-smtp"
AMAVIS_VERDICTS = [
    ("Blocked INFECTED",   "block"),
    ("Blocked SPAMMY",     "block"),
    ("Blocked BANNED",     "block"),
    ("Blocked BAD-HEADER", "block"),
    ("Blocked MTA",        "block"),
    ("Passed INFECTED",    "pass"),
    ("Passed SPAMMY",      "pass"),
    ("Passed CLEAN",       "pass"),
]

# Lucene-Operator-Detection für Auto-Phrase-Quoting (Plan-Schritt 6).
# Wenn KEINER dieser Operatoren in --query vorkommt UND der String '.' oder '@'
# enthält, wird er automatisch in Phrase-Quotes verpackt.
_LUCENE_OPS_RX = re.compile(r'\b(?:AND|OR|NOT|TO)\b|[:()"*?~^\\]')

# Exclude-Presets für Plan-Schritt 4 (RBL-Rauschen, Greylisting etc.).
# Wert ist ein Python-Regex, der client-seitig auf das `message`-Feld jedes
# Messages angewandt wird. Mehrere Presets/Regex sind über --exclude (mehrfach)
# kombinierbar.
EXCLUDE_PRESETS = {
    "rbl-rejects":      r"blocked using (?:zen\.spamhaus\.org|bl\.mailspike\.net|b\.barracudacentral\.org|psbl\.surriel\.com)",
    "greylisting":      r"4\.7\.1 Greylisted",
    "tls-handshake":    r"SSL_accept error|TLS handshake failed",
    "postscreen-noise": r"postfix/postscreen.*(?:PASS NEW|PASS OLD|HANGUP)",
    "cron-noise":       r"CRON\[\d+\]:",
}


HELP_AI = """graylog-query.py - read-only Graylog CLI

ACTIONS (--action, default: query)
  query     - search messages, print summary + samples
  count     - return only the total count (fast)
  fields    - dump ALL fields of the most recent N messages (field discovery)
  terms     - top-N values of a field (aggregation)
  streams   - list streams (id, title)
  verdicts  - amavis verdict summary table (default source: itl15-gdata-smtp)
  mailflow  - end-to-end pipeline trace per mail (qid/msgid/recipient)

QUERY (--query, Graylog query syntax. Default: '*')
  Examples:
    --query 'srcip:10.10.40.7'
    --query 'message:"to=<wacker@ibf-solutions.com>"'
    --query 'NOT source:gw'
  Auto-Phrase-Quoting: a query without Lucene operators that contains '.'
  or '@' is automatically wrapped in phrase quotes (e.g. `slido.com` ->
  `"slido.com"`). Disable with --no-auto-quote.

TIME WINDOW (one of)
  --range <seconds>    rolling window in seconds (default: 86400)
  --last <expr>        '15m' '2h' '7d' '90s' (rolling)
  --today              since local midnight today
  --yesterday          local yesterday 00:00..00:00
  --from <ausdruck> [--to <ausdruck>]
                       absolute window; --to optional (default: jetzt/now)
                       Keywords:  heute/today, gestern/yesterday, jetzt/now
                       ISO:       2026-05-04, 2026-05-04T08:00:00, ...Z, ...+02:00
                       Freitext:  'letzte Woche', 'vor 3 Tagen' (benötigt: pip install dateparser)

FILTER SUGAR
  --source <host>          add 'source:<host>' to query (repeatable)
  --stream <id|title>      restrict to a stream (id or substring of title)
  --mail-to <addr|substr>  recipient match. Three modes:
                             foo@bar.com  -> exact phrase
                             @bar.com     -> domain match
                             foo          -> substring (token-regex + to-anchor)
                           Auto-adds sources: web12-hz + itl15-gdata-smtp.
  --mail-from <...>        analog for sender

NOISE FILTERS (client-side, applied after fetch)
  --exclude PRESET|REGEX   strip messages matching pattern (repeatable).
                           Presets: rbl-rejects, greylisting, tls-handshake,
                           postscreen-noise, cron-noise.
  --list-excludes          print all known presets with their regex
  --client-filter REGEX    keep only messages matching this regex
                           (use to split AND-heavy queries into single-phrase
                           server-query + client-side narrowing)

MAILFLOW (--action mailflow)
  --qid HEXID              postfix queue-id (e.g. 07BF77E025) - one mail
  --msgid <id>             RFC message-id - one mail
  --mail-to <addr>         find N most-recent mails to recipient, render each
                           pipeline (smtpd -> cleanup -> amavis -> qmgr -> smtp).
                           Verdict: DELIVERED / REJECTED / BLOCKED / BOUNCED / QUEUED

OUTPUT
  --limit <n>              max messages (default 50; ignored for count/streams)
  --fields a,b,c           fields to show (default: smart per-source)
  --all-fields             show every field of every result
  --raw                    output raw JSON (for piping)
  --no-truncate            don't truncate field values to 200 chars
  --terms-size <n>         for terms: how many top values (default 25)
  --patterns PAT ...       for terms: count messages matching each pattern
  --no-auto-quote          disable auto phrase-quoting

EXAMPLES
  Did philipp.wacker get emails today:
    graylog-query.py --mail-to philipp.wacker@ibf-solutions.com --today

  Substring match (any recipient containing 'wacker'):
    graylog-query.py --mail-to wacker --last 24h

  Domain match (any sender from avdata.de):
    graylog-query.py --mail-from @avdata.de --last 7d

  Pipeline trace per queue-id:
    graylog-query.py --action mailflow --qid 07BF77E025 --last 1h

  5 most-recent mails to johannes (each with full pipeline + verdict):
    graylog-query.py --action mailflow --mail-to johannes --last 7d --limit 5

  Hide RBL-reject noise + greylisting:
    graylog-query.py --mail-to johannes --last 7d --exclude rbl-rejects --exclude greylisting

  Discover what fields a source has:
    graylog-query.py --action fields --source web12-hz --last 5m --limit 3
"""


HELP_TXT = """Graylog Query Tool — Hilfe

Liest aus IBF-Graylog (gld.ibf-solutions.com) per REST-API. Reine Lese-Schnittstelle.

Aktionen (--action):
  query     Logs suchen + Summary (Default)
  count     Nur Trefferzahl
  fields    Alle Felder der ersten N Treffer (Feld-Erkundung)
  terms     Top-N Werte eines Feldes (Aggregation)
  streams   Liste aller Streams
  verdicts  amavis-Verdict-Tabelle (Default-Source: itl15-gdata-smtp)
  mailflow  End-to-end-Pipeline-Trace einer Mail (per --qid / --msgid /
            --mail-to). Output: smtpd -> cleanup -> amavis -> qmgr -> smtp +
            Verdikt (DELIVERED / REJECTED / BLOCKED / BOUNCED / QUEUED)

Suche (--query):
  Graylog-Query-Syntax. Beispiele:
    --query 'srcip:10.10.40.7'
    --query 'message:"to=<wacker@ibf-solutions.com>"'
  Auto-Phrase-Quoting: Query ohne Lucene-Operatoren die '.' oder '@' enthält
  wird automatisch in Phrase-Quotes verpackt (slido.com -> "slido.com").
  Mit --no-auto-quote deaktivierbar.

Zeitfenster (eines davon):
  --range <sek>            Default 86400 (24h)
  --last <ausdruck>        '15m' '2h' '7d' '90s'
  --today                  seit lokal Mitternacht heute
  --yesterday              gestern lokal 00:00 .. 00:00
  --from <ausdruck> [--to <ausdruck>]
                           absolutes Fenster; --to optional (Default: jetzt)
                           Keywords: heute, gestern, jetzt (auch: today, yesterday, now)
                           ISO:      2026-05-04, 2026-05-04T08:00:00, ...Z, ...+02:00
                           Freitext: 'letzte Woche', 'vor 3 Tagen' (pip install dateparser)

Filter-Komfort:
  --source <host>          'source:<host>' zum Query addieren (wiederholbar)
  --stream <id|titel>      auf Stream beschränken (id oder Titel-Substring)
  --mail-to <addr|teil>    Postfix-Empfänger-Match. Drei Modi:
                             foo@bar.com  -> exakte Phrase
                             @bar.com     -> Domain-Match
                             foo          -> Substring (Token-Regex)
                           Setzt automatisch beide Mail-Sources
                           (web12-hz + itl15-gdata-smtp).
  --mail-from <...>        analog für Absender

Rauschen ausblenden (client-seitig nach Empfang):
  --exclude PRESET|REGEX   Messages mit Pattern weglassen (mehrfach möglich).
                           Presets: rbl-rejects, greylisting, tls-handshake,
                           postscreen-noise, cron-noise.
  --list-excludes          alle Presets mit Regex auflisten und exit
  --client-filter REGEX    nur Messages behalten die Regex matchen
                           (Auffangnetz für komplexe AND-Phrase-Queries)

Mailflow (--action mailflow):
  --qid HEXID              Postfix-Queue-ID (z.B. 07BF77E025) -- eine Mail
  --msgid <id>             RFC-Message-ID -- eine Mail
  --mail-to <addr>         N neueste Mails an Empfänger, je Pipeline + Verdikt

Output:
  --limit <n>          Max. Treffer (Default 50)
  --fields a,b,c       Felder die angezeigt werden (sonst Auto-Auswahl)
  --all-fields         Alle Felder pro Treffer
  --raw                JSON-Ausgabe (zum Pipen)
  --no-truncate        Werte nicht abschneiden
  --terms-size <n>     Bei terms: wieviele Top-Werte (Default 25)
  --patterns PAT ...   Bei terms: Zählt pro Pattern wieviele Messages matchen (wiederholbar, Regex ok)

Token (Ladereihenfolge):
  1. Umgebungsvariable GRAYLOG_IBF
  2. Bitwarden CLI (BW_SESSION muss gesetzt sein)
  3. Windows Credential Manager (empfohlen):
       python graylog-query.py --set-token        (sichere Eingabe)
       python graylog-query.py --set-token <TOKEN> (direkt)
  4. .env-Datei: graylog_ibf=<TOKEN> (im Projekt oder bis 5 Ebenen drüber)

Beispiele:
  graylog-query.py --mail-to philipp.wacker@ibf-solutions.com --today
  graylog-query.py --mail-to wacker --last 24h          # Substring
  graylog-query.py --mail-from @avdata.de --last 7d     # Domain
  graylog-query.py --action mailflow --qid 07BF77E025 --last 1h
  graylog-query.py --action mailflow --mail-to johannes --last 7d --limit 5
  graylog-query.py --mail-to johannes --last 7d --exclude rbl-rejects --exclude greylisting
  graylog-query.py --action count --query 'srcip:10.10.10.33 AND dstport:8006' --last 1h
  graylog-query.py --action terms --query 'action:deny' --terms policyid --yesterday
  graylog-query.py --action streams
  graylog-query.py --action fields --source web12-hz --last 5m --limit 3
"""


# ----- token loading ---------------------------------------------------------

def load_token():
    """Load Graylog API token from the first available source:
    1. Environment variable GRAYLOG_IBF
    2. Bitwarden CLI (bw) — requires BW_SESSION to be set
    3. Windows Credential Manager (keyring library)
    4. .env file (legacy fallback)
    """
    # 1 — Umgebungsvariable
    token = os.environ.get("GRAYLOG_IBF", "").strip()
    if token:
        return token

    # 2 — Bitwarden CLI
    token = _token_from_bitwarden()
    if token:
        return token

    # 3 — Windows Credential Manager (keyring)
    token = _token_from_keyring()
    if token:
        return token

    # 4 — .env-Datei
    token = _token_from_env_file()
    if token:
        return token

    sys.exit(
        "[ERROR] Kein graylog_ibf-Token gefunden.\n\n"
        "Einen der folgenden Wege einrichten:\n"
        "  1. Umgebungsvariable:         $env:GRAYLOG_IBF = '<TOKEN>'\n"
        "  2. Bitwarden CLI:             bw create item ... (Name: graylog-ibf, Passwort: <TOKEN>)\n"
        "                                dann: $env:BW_SESSION = (bw unlock --raw)\n"
        "  3. Windows Credential Manager: python -c \"import keyring; "
        "keyring.set_password('graylog', 'ibf', '<TOKEN>')\"\n"
        "  4. .env-Datei:                graylog_ibf=<TOKEN>  (neben diesem Script oder bis 5 Ebenen drüber)\n\n"
        "Token in Graylog: System > Authentication > Tokens"
    )


def _token_from_bitwarden():
    """Try Bitwarden CLI (bw). Looks in PATH and next to this script.
    Silently returns None if bw is not available or vault is locked."""
    if not os.environ.get("BW_SESSION"):
        return None
    bw_candidates = ["bw", str(Path(__file__).parent / "bw.exe"), str(Path(__file__).parent / "bw")]
    for bw in bw_candidates:
        try:
            result = subprocess.run(
                [bw, "get", "password", "graylog-ibf", "--session", os.environ["BW_SESSION"]],
                capture_output=True, text=True, timeout=10
            )
            token = result.stdout.strip()
            if result.returncode == 0 and token:
                return token
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _token_from_keyring():
    """Try Windows Credential Manager via keyring. Silently returns None if not installed."""
    try:
        import keyring
        token = keyring.get_password("graylog", "ibf")
        return token.strip() if token else None
    except Exception:
        return None


def _token_from_env_file():
    """Try .env files — script directory up to 5 levels, plus C:\\Temp\\claude\\.env."""
    candidates = [Path(__file__).resolve().parents[n] / ".env" for n in range(6)]
    candidates.append(Path(r"C:\Temp\claude\.env"))
    for env in candidates:
        if not env.exists():
            continue
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("graylog_ibf="):
                return line.split("=", 1)[1].strip()
    return None


# ----- HTTP helper -----------------------------------------------------------

def gl(method, path, token, body=None, params=None):
    url = GRAYLOG_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    auth = base64.b64encode(f"{token}:token".encode()).decode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Accept", "application/json")
    req.add_header("X-Requested-By", "graylog-query-tool")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ct = r.headers.get("Content-Type", "")
            txt = r.read().decode()
            if "text/html" in ct or "login.ibf-solutions.com" in txt:
                sys.exit(
                    "\n"
                    "  +-------------------------------------------------------+\n"
                    "  |                                                       |\n"
                    "  |   !!!   ACHTUNG: IBF-LOGIN ABGELAUFEN   !!!          |\n"
                    "  |                                                       |\n"
                    "  |   Oeffne im Webbrowser:                              |\n"
                    "  |   https://login.ibf-solutions.com                    |\n"
                    "  |                                                       |\n"
                    "  |   Gib dort den Zugriff auf den Server frei           |\n"
                    "  |   und fuehre das Script danach erneut aus.           |\n"
                    "  |                                                       |\n"
                    "  +-------------------------------------------------------+\n"
                )
            return json.loads(txt) if txt else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:600]
        # Plan-Schritt 5: bessere Diagnose bei OpenSearch-Limit (kombinierte
        # Phrase-Queries mit AND über große Zeitfenster sprengen maxClauseCount).
        if e.code == 500 and ("maxClauseCount" in body or "too_many_clauses" in body):
            sys.exit(
                f"[HTTP 500] OpenSearch-Limit erreicht (maxClauseCount).\n"
                f"  URL: {url}\n  {body[:200]}\n\n"
                "Mögliche Lösungen:\n"
                "  - Zeitfenster verkleinern (--last 24h statt 30d)\n"
                "  - Kombinierte Phrasen aufteilen: nur EINE Phrase im --query, "
                "die zweite via --client-filter '<regex>' nachfiltern.\n"
                "  - Bei Mail-Forensik: --action mailflow nutzt single-Phrase-Pattern.\n")
        sys.exit(f"[HTTP {e.code}] {url}\n{body}")
    except urllib.error.URLError as e:
        sys.exit(f"[FEHLER] Graylog nicht erreichbar ({e.reason})\n  URL: {url}")
    except TimeoutError:
        sys.exit(f"[FEHLER] Timeout nach 30s — Graylog überlastet oder nicht erreichbar\n"
                 f"  URL: {url}\n"
                 "  Hinweis: Lucene-Regex (.*term.*) über große Zeitfenster ist teuer.\n"
                 "  Versuche: kleineres --last, oder bei Mail-Forensik die volle Adresse "
                 "/ --mail-to @domain (Phrase-Match) statt Substring.")


# ----- time window resolver --------------------------------------------------

def parse_duration(s):
    m = re.match(r"^(\d+)\s*([smhd]?)$", s.strip())
    if not m:
        sys.exit(f"[ERROR] Ungültiges Zeitformat: {s} (erwartet z.B. '15m', '2h', '7d', '90s')")
    n, unit = int(m.group(1)), m.group(2) or "s"
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _parse_ts(s):
    """Zeitstempel oder natürlichsprachiger Ausdruck → UTC-String für Graylog-API.

    Reihenfolge:
    1. Explizite Keywords (heute/gestern/jetzt + EN-Aliases) — definiertes Verhalten
    2. dateparser (optional, pip install dateparser) — natürliche Sprache
    3. ISO-Fallback (fromisoformat)
    """
    now = dt.datetime.now().astimezone()
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    kw = s.strip().lower()

    if kw in ("heute", "today"):
        return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(dt.timezone.utc).strftime(fmt)
    if kw in ("gestern", "yesterday"):
        return (now.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=1)).astimezone(dt.timezone.utc).strftime(fmt)
    if kw in ("jetzt", "now"):
        return now.astimezone(dt.timezone.utc).strftime(fmt)

    try:
        import dateparser as _dp
        r = _dp.parse(s, languages=["de", "en"],
                      settings={"RETURN_AS_TIMEZONE_AWARE": True, "TIMEZONE": "Europe/Vienna"})
        if r is not None:
            return r.astimezone(dt.timezone.utc).strftime(fmt)
    except ImportError:
        pass

    s2 = s.replace("Z", "+00:00")  # Python < 3.11 fromisoformat kennt kein Z
    d = dt.datetime.fromisoformat(s2)
    if d.tzinfo is None:
        d = d.astimezone()
    return d.astimezone(dt.timezone.utc).strftime(fmt)


def resolve_window(args):
    """Return (mode, params) where mode is 'relative' or 'absolute'."""
    if args.to and not args.from_:
        sys.exit("[ERROR] --to ohne --from ist nicht erlaubt.")
    n_set = sum(bool(x) for x in [
        args.range != 86400, args.last, args.today, args.yesterday, args.from_])
    if n_set > 1:
        sys.exit("[ERROR] Nur eine Zeit-Option auf einmal (oder --from [--to]).")

    if args.from_:
        to_str = args.to if args.to else "jetzt"
        return ("absolute", {"from": _parse_ts(args.from_), "to": _parse_ts(to_str)})
    if args.today:
        now = dt.datetime.now().astimezone()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return ("absolute", {
            "from": midnight.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "to":   now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        })
    if args.yesterday:
        now = dt.datetime.now().astimezone()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        y_start = midnight - dt.timedelta(days=1)
        return ("absolute", {
            "from": y_start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "to":   midnight.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        })
    if args.last:
        return ("relative", {"range": parse_duration(args.last)})
    return ("relative", {"range": args.range})


# ----- query builder ---------------------------------------------------------

def _mail_clause(kind, addr):
    """Lucene-Klausel für --mail-to / --mail-from (Plan-Schritt 1).

    kind: 'to' | 'from'
    Drei Modi je nach Form von `addr`:
      'foo@bar.com'  -> exakte Phrase 'to=<foo@bar.com>' (Verhalten wie früher)
      '@bar.com'     -> Domain-Match: 'to'-Token gefolgt (mit Slop) von Domain-Tokens
      'foo'          -> Substring: 'to'-Token direkt gefolgt vom Substring-Token

    Match-Strategie: Lucene's `match_phrase` mit Slop. Der Standard-Analyzer
    von Graylog/OpenSearch entfernt Punctuation (`=`, `<`, `>`, `@`) und
    splittet an `.`/`-` -- die Phrase `to philipp` matcht daher direkt das
    Token-Pattern, das `to=<philipp...>` erzeugt. Slop=0 ist directional
    (verhindert dass `from=<philipp@...> to=<other@...>` als Treffer für
    `--mail-to philipp` durchgeht), Slop>0 erlaubt Lücken (für längere User-
    Teile vor der Domain bei Domain-Mode).

    Felder die wir matchen (Postfix-Konvention):
      to=<>, orig_to=<>, rcpt=<>      -- für kind='to'
      from=<>, mail_from=<>, sender=<>  -- für kind='from'
    """
    if kind == "to":
        prefixes = ["to", "orig_to", "rcpt"]
    else:
        prefixes = ["from", "mail_from", "sender"]

    if "@" in addr and not addr.startswith("@"):
        # Volle Adresse: exakte Phrase (Lucene tokenisiert beides gleich)
        ors = [f'message:"{p}=<{addr}>"' for p in prefixes]
        return "(" + " OR ".join(ors) + ")"

    if addr.startswith("@"):
        # Domain: '@avdata.de' -> Phrase 'to avdata.de' mit Slop 8.
        # Slop deckt User-Teil + ggf. Subdomain ab. `re.split` nicht nötig --
        # der Lucene-Phrase-Analyzer tokenisiert die Phrase selbst gleich.
        domain = addr[1:]
        ors = [f'message:"{p} {domain}"~8' for p in prefixes]
        return "(" + " OR ".join(ors) + ")"

    # Substring: 'wacker' / 'philipp' -- der Standard-Analyzer indiziert
    # E-Mail-Adressen als EIN Token (`philipp.wacker@ibf-solutions.com`),
    # daher matcht `to philipp` als Phrase nichts wenn die Adresse länger ist.
    # Lösung: Lucene-Regex `.*term.*` matcht JEDEN Token der term als
    # Substring enthält (Token-Level-Regex), + AND-Anker auf einen
    # to-/from-Token im selben Doc.
    # Leading-Wildcard `*term*` ist im Graylog-OpenSearch deaktiviert (HTTP 500).
    safe = re.sub(r'([\\./\[\]^$+?{}|()])', r'\\\1', addr)
    prefix_or = " OR ".join(f"message:{p}" for p in prefixes)
    return f"(message:/.*{safe}.*/ AND ({prefix_or}))"


def _maybe_auto_quote(q, no_auto_quote=False):
    """Auto-Phrase-Quote für --query (Plan-Schritt 6).

    Wenn der Query-String keine Lucene-Operatoren enthält UND einen Punkt
    oder ein @ enthält, in Phrase-Quotes verpacken (verhindert die stille
    Tokenizer-Aufspaltung an '.' und '@'). Schon gequotete Strings,
    Felder-Suchen ('field:value') und Operator-Queries bleiben unangetastet.
    Returns (quoted_string, was_auto_quoted_bool).
    """
    if no_auto_quote or not q or q.strip() == "*":
        return q, False
    if _LUCENE_OPS_RX.search(q):
        return q, False
    if "." in q or "@" in q:
        return f'"{q}"', True
    return q, False


def build_query(args):
    parts = []
    auto_quoted = False
    if args.query:
        q, was_quoted = _maybe_auto_quote(args.query,
                                          no_auto_quote=getattr(args, "no_auto_quote", False))
        auto_quoted = was_quoted
        parts.append(f"({q})")
    for s in args.source or []:
        parts.append(f'source:"{s}"')
    if args.mail_to:
        parts.append(_mail_clause("to", args.mail_to))
        if not args.source and MAIL_SOURCES:
            mail_or = " OR ".join(f'source:"{s}"' for s in MAIL_SOURCES)
            parts.append(f"({mail_or})")
    if args.mail_from:
        parts.append(_mail_clause("from", args.mail_from))
        if not args.source and MAIL_SOURCES:
            mail_or = " OR ".join(f'source:"{s}"' for s in MAIL_SOURCES)
            parts.append(f"({mail_or})")
    final = " AND ".join(parts) if parts else "*"
    # Side-channel für die Echo-Zeile (transparent machen, dass auto-quote griff)
    build_query._last_auto_quoted = auto_quoted   # type: ignore[attr-defined]
    return final


build_query._last_auto_quoted = False  # type: ignore[attr-defined]


# ----- stream resolver -------------------------------------------------------

def resolve_stream(token, name_or_id):
    if not name_or_id:
        return None
    if re.match(r"^[a-f0-9]{24}$", name_or_id):
        return name_or_id
    streams = gl("GET", "/streams", token).get("streams", [])
    matches = [s for s in streams if name_or_id.lower() in s.get("title", "").lower()]
    if not matches:
        sys.exit(f"[ERROR] Kein Stream-Titel matcht: {name_or_id}")
    if len(matches) > 1:
        opts = ", ".join(f"{s['id']}={s['title']!r}" for s in matches)
        sys.exit(f"[ERROR] Mehrdeutig: {opts}")
    return matches[0]["id"]


# ----- output helpers --------------------------------------------------------

DEFAULT_FIELDS_BY_SOURCE_HINT = {
    # match heuristic on source field; first match wins
    "gw":               ["timestamp", "srcip", "srcport", "dstip", "dstport", "action", "service", "policyid", "policyname", "app"],
    "web12-hz":         ["timestamp", "source", "message"],
    "itl34-docker":     ["timestamp", "source", "container_name", "message"],
    "_default":         ["timestamp", "source", "message"],
}


def pick_default_fields(messages):
    if not messages:
        return DEFAULT_FIELDS_BY_SOURCE_HINT["_default"]
    first_src = (messages[0].get("message", {}).get("source") or "").lower()
    for hint, flds in DEFAULT_FIELDS_BY_SOURCE_HINT.items():
        if hint != "_default" and hint in first_src:
            return flds
    return DEFAULT_FIELDS_BY_SOURCE_HINT["_default"]


def fmt_value(v, truncate):
    s = str(v)
    if truncate and len(s) > 200:
        s = s[:200] + "..."
    return s


def print_messages(messages, fields_arg, all_fields, truncate):
    fields = (fields_arg.split(",") if fields_arg else None) or pick_default_fields(messages)
    for i, mm in enumerate(messages, 1):
        m = mm.get("message", {})
        print(f"\n#{i}  {m.get('timestamp','?')}  src={m.get('source','?')}")
        if all_fields:
            for k in sorted(m.keys()):
                if k.startswith("gl2_") or k == "_id":
                    continue
                print(f"  {k}: {fmt_value(m[k], truncate)}")
        else:
            for k in fields:
                if k in m:
                    print(f"  {k}: {fmt_value(m[k], truncate)}")


# ----- search dispatch -------------------------------------------------------

def do_search(token, query, mode, time_params, limit, stream_id):
    params = {"query": query, "limit": limit, "sort": "timestamp:desc"}
    params.update(time_params)
    if stream_id:
        params["filter"] = f"streams:{stream_id}"
    path = "/search/universal/" + ("absolute" if mode == "absolute" else "relative")
    return gl("GET", path, token, params=params)


def _pattern_clause(field, p):
    """Build a Graylog query clause for one pattern.
    Uses regex syntax (/pattern/) when the pattern contains regex metacharacters,
    phrase quoting otherwise."""
    if _is_regex(p):
        escaped = p.replace("/", "\\/")
        return f"{field}:/{escaped}/"
    return f'{field}:"{p}"'


def _is_regex(s):
    """True when the string contains regex metacharacters (. excluded — too common in IPs/domains)."""
    return bool(re.search(r"[\\^$*+?{}\[\]|(]", s))


def _gl_post_safe(path, token, body, timeout=30):
    """POST mit JSON-Body, **ohne** sys.exit bei Fehler -- für Code-Pfade
    die einen Fallback haben wollen. Wirft RuntimeError bei HTTP-/Netz-Fehler."""
    auth = base64.b64encode(f"{token}:token".encode()).decode()
    req = urllib.request.Request(
        GRAYLOG_BASE + path,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Authorization": f"Basic {auth}",
                 "X-Requested-By": "graylog-query-tool",
                 "Accept": "application/json",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"HTTP {e.code} on {path}: {body_txt}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"Network error on {path}: {e}") from e


def _aggregate_top_values(token, query, mode, time_params, field, size, stream_id):
    """Echte Aggregation via `POST /search/aggregate` (Graylog Scripting-API).

    Liefert exakte Counts statt Sample-Niveallierung. Internes
    `limit = max(size, 50)` weil OpenSearch-Term-Aggregation pro Shard
    bei kleinem size unscharf ist (siehe T1 im Dashboard-Subprojekt).

    Wirft RuntimeError bei API-Fehler -- Caller fällt auf Sample-Methode zurück.
    """
    internal_limit = max(size, 50)
    if mode == "absolute":
        timerange = {"type": "absolute",
                     "from": time_params["from"], "to": time_params["to"]}
    else:
        timerange = {"type": "relative", "range": time_params["range"]}
    body = {
        "query": query or "*",
        "streams": [stream_id] if stream_id else [],
        "stream_categories": [],
        "timerange": timerange,
        "group_by": [{"field": field, "limit": internal_limit}],
        "metrics": [{"function": "count"}],
    }
    data = _gl_post_safe("/search/aggregate", token, body)
    terms: dict[str, int] = {}
    for row in data.get("datarows", []):
        if len(row) < 2 or row[0] is None or row[0] == "":
            continue
        try:
            terms[str(row[0])] = int(row[1])
        except (TypeError, ValueError):
            continue
        if len(terms) >= size:
            break
    # Total separat holen (für die Anzeige der Total-Treffer)
    total_res = do_search(token, query, mode, time_params, 1, stream_id)
    total = total_res.get("total_results", 0)
    return {
        "terms":       terms,
        "total":       total,
        "sample_size": total,
        "missing":     "n/a",   # Aggregation rechnet auf vollem Datensatz
        "truncated":   False,
        "method":      "aggregation",
    }


def do_terms(token, query, mode, time_params, field, size, stream_id, patterns=None, fetch_cap=5000):
    """Top-N häufigste Werte eines Feldes.

    Drei Modi:
      `--patterns`: parallele Count-Queries pro Pattern (exakt, unverändert).
      sonst:        echte Aggregation via `/search/aggregate` (T5, exakte Counts).
                    Bei API-Fehler Fallback auf alten Sample-Counter.
    """
    if patterns:
        import concurrent.futures

        def _count(q_full):
            return do_search(token, q_full, mode, time_params, 1, stream_id).get("total_results", 0)

        base = f"({query})" if query and query != "*" else ""

        def _pq(p):
            clause = _pattern_clause(field, p)
            return f"{base} AND {clause}" if base else clause

        any_clause = " OR ".join(_pattern_clause(field, p) for p in patterns)
        any_q = f"{base} AND ({any_clause})" if base else f"({any_clause})"

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(patterns) + 2, 12)) as ex:
            pat_futs  = {ex.submit(_count, _pq(p)): p for p in patterns}
            any_fut   = ex.submit(_count, any_q)
            total_fut = ex.submit(_count, query or "*")
            counts    = {pat_futs[f]: f.result() for f in concurrent.futures.as_completed(pat_futs)}
            matched_any = any_fut.result()
            base_total  = total_fut.result()

        return {
            "patterns":    {p: counts[p] for p in patterns},
            "no_match":    base_total - matched_any,
            "total":       base_total,
            "sample_size": base_total,
            "truncated":   False,
        }

    # Default-Pfad: erst echte Aggregation, sonst Fallback auf Sample-Counter
    try:
        return _aggregate_top_values(token, query, mode, time_params, field, size, stream_id)
    except RuntimeError as e:
        print(f"[WARN] /search/aggregate fehlgeschlagen ({e}); falle zurück auf "
              f"Sample-Counter mit fetch_cap={fetch_cap}", file=sys.stderr)

    # Fallback: Sample-basierter Counter (alte Methode)
    from collections import Counter
    res = do_search(token, query, mode, time_params, fetch_cap, stream_id)
    total = res.get("total_results", 0)
    msgs  = res.get("messages", [])
    counter = Counter()
    missing = 0
    for mm in msgs:
        v = mm.get("message", {}).get(field)
        if v is None:
            missing += 1
        else:
            counter[str(v)] += 1
    return {
        "terms":       dict(counter.most_common(size)),
        "total":       total,
        "sample_size": len(msgs),
        "missing":     missing,
        "truncated":   total > len(msgs),
        "method":      "sample-counter",
    }


# ----- exclude / client filter (Plan-Schritt 4 + 5) --------------------------

def _resolve_exclude_patterns(exclude_args):
    """Liste der --exclude-Args -> Liste kompilierter Regex-Patterns.
    Preset-Namen werden über `EXCLUDE_PRESETS` aufgelöst, alles andere als
    Regex auf `message`-Feld kompiliert."""
    patterns = []
    for raw in exclude_args or []:
        if raw in EXCLUDE_PRESETS:
            patterns.append(re.compile(EXCLUDE_PRESETS[raw]))
        else:
            try:
                patterns.append(re.compile(raw))
            except re.error as e:
                sys.exit(f"[ERROR] --exclude {raw!r}: ungültiges Regex: {e}")
    return patterns


def _resolve_client_filter(pattern):
    if not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error as e:
        sys.exit(f"[ERROR] --client-filter {pattern!r}: ungültiges Regex: {e}")


def _filter_messages(messages, exclude_pats, client_filter):
    """Client-seitiger Filter über das `message`-Feld jedes Logs.
    Returns (filtered_messages, n_excluded, n_filtered_out)."""
    if not exclude_pats and not client_filter:
        return messages, 0, 0
    out = []
    n_excl = 0
    n_filt = 0
    for mm in messages:
        text = (mm.get("message", {}).get("message") or "")
        if any(p.search(text) for p in exclude_pats):
            n_excl += 1
            continue
        if client_filter and not client_filter.search(text):
            n_filt += 1
            continue
        out.append(mm)
    return out, n_excl, n_filt


# ----- raw output helper -----------------------------------------------------

def clean_raw_messages(messages, fields_arg, all_fields):
    """Strip internal Graylog fields and apply --fields filtering for raw output."""
    field_list = fields_arg.split(",") if fields_arg else None
    out = []
    for mm in messages:
        m = mm.get("message", {})
        if all_fields or not field_list:
            row = {k: v for k, v in m.items() if not k.startswith("gl2_") and k != "_id"}
        else:
            row = {k: m[k] for k in field_list if k in m}
        out.append(row)
    return out


# ----- mailflow (Plan-Schritt 2) --------------------------------------------

# Postfix Queue-IDs sind Hex-Strings, meist 10-12 Zeichen lang.
# (8-14 als Toleranz; im Fließtext seltener als die Konkurrenz.)
_QID_RX = re.compile(r'\b[A-F0-9]{8,14}\b')


def _classify_pipeline_line(text):
    """(stage, status, detail) -- klassifiziert eine Postfix/Amavis-Log-Zeile.
    `status` triggert die Verdict-Berechnung in `_render_one_flow`."""
    if not text:
        return ("other", "?", "")

    # amavis verdicts
    m = re.search(r'amavis\[\d+\]:\s+\([^)]+\)\s+(Passed|Blocked)\s+([A-Z\-]+)', text)
    if m:
        verdict, kind = m.group(1), m.group(2)
        return ("amavis",
                "amavis_pass" if verdict == "Passed" else "amavis_block",
                f"amavis {verdict} {kind}")

    # postfix smtpd reject (NOQUEUE)
    if "NOQUEUE:" in text and "reject:" in text:
        m = re.search(r'reject:\s+(.+?)(?:;|\s+from=)', text)
        return ("smtpd", "reject", f"reject: {m.group(1).strip() if m else '?'}")

    # postfix smtpd connect (mit QID)
    m = re.search(r'postfix/smtpd\[\d+\]:\s+(\w+):\s+client=([^\s,]+)', text)
    if m and m.group(1) != "NOQUEUE":
        return ("smtpd", "client", f"client={m.group(2)}")

    # cleanup -> message-id
    m = re.search(r'postfix/cleanup\[\d+\]:\s+\w+:\s+message-id=<([^>]+)>', text)
    if m:
        return ("cleanup", "msgid", f"msgid={m.group(1)}")

    # qmgr enqueue
    m = re.search(r'postfix/qmgr\[\d+\]:\s+\w+:\s+from=<([^>]*)>,\s+size=(\d+)', text)
    if m:
        return ("qmgr", "queued", f"from={m.group(1)} size={m.group(2)}")

    # qmgr removed
    if re.search(r'postfix/qmgr\[\d+\]:\s+\w+:\s+removed', text):
        return ("qmgr", "removed", "removed")

    # smtp/lmtp delivery
    m = re.search(r'postfix/(?:smtp|smtps|lmtp)\[\d+\]:\s+\w+:\s+to=<([^>]*)>.*?status=(\w+)', text)
    if m:
        return ("smtp", m.group(2), f"to={m.group(1)} status={m.group(2)}")

    # postscreen / milter / generic fallback (kurz)
    short = text[:120]
    return ("other", "?", short)


def _render_one_flow(token, ident_query, label, mode, time_params, stream_id, raw=False):
    source_filter = " OR ".join(f'source:"{s}"' for s in MAIL_SOURCES)
    full_q = f"({ident_query}) AND ({source_filter})"
    res = do_search(token, full_q, mode, time_params, 200, stream_id)
    msgs = res.get("messages", [])
    if not msgs:
        print(f"[INFO] {label}: keine Logs im Zeitfenster gefunden\n")
        return

    msgs.sort(key=lambda mm: mm.get("message", {}).get("timestamp", ""))

    header = {"from": "?", "to": "?", "size": None, "msgid": "?"}
    pipeline = []
    verdict = "QUEUED"
    verdict_locked = False

    for mm in msgs:
        m = mm.get("message", {})
        text = m.get("message", "")
        ts = (m.get("timestamp") or "")[:19].replace("T", " ")
        src = m.get("source", "?")
        stage, status, detail = _classify_pipeline_line(text)

        # Header-Felder mitnehmen (das erste sichtbare gewinnt)
        if header["from"] == "?":
            mt = re.search(r'from=<([^>]+)>', text)
            if mt:
                header["from"] = mt.group(1)
        if header["to"] == "?":
            mt = re.search(r'(?:^|[\s,])to=<([^>]+)>', text)
            if mt:
                header["to"] = mt.group(1)
        if header["size"] is None:
            mt = re.search(r'size=(\d+)', text)
            if mt:
                header["size"] = int(mt.group(1))
        if header["msgid"] == "?":
            mt = re.search(r'message-id=<([^>]+)>', text)
            if mt:
                header["msgid"] = mt.group(1)

        # Verdict-Logik: terminale Stati locken
        if not verdict_locked:
            if status == "reject":
                verdict = f"REJECTED ({detail.replace('reject: ', '')})"
                verdict_locked = True
            elif status == "amavis_block":
                verdict = f"BLOCKED ({detail})"
                verdict_locked = True
            elif status == "sent":
                verdict = "DELIVERED"
            elif status == "bounced":
                verdict = "BOUNCED"

        pipeline.append((src, ts, stage, detail, status))

    if raw:
        print(json.dumps({
            "label": label,
            "header": header,
            "pipeline": [{"source": s, "ts": t, "stage": st, "detail": d}
                         for s, t, st, d, _ in pipeline],
            "verdict": verdict,
        }, indent=2, default=str))
        return

    size_str = f"{header['size']} bytes" if header['size'] is not None else "?"
    print(f"\n=== {label} ===")
    print(f"  From:    {header['from']}")
    print(f"  To:      {header['to']}")
    if header["msgid"] != "?":
        print(f"  MsgID:   {header['msgid']}")
    print(f"  Size:    {size_str}")
    print(f"\n  Pipeline:")
    for src, ts, stage, detail, _ in pipeline:
        if "itl15" in src:
            sshort = "[gdata]"
        elif "web12" in src:
            sshort = "[mailcow]"
        else:
            sshort = f"[{src[:8]}]"
        print(f"    {sshort:10s} {ts}  {stage:9s}  {detail}")
    print(f"\n  Verdict: {verdict}\n")


def do_mailflow(token, args, mode, time_params, stream_id):
    """Pipeline-Trace per --qid / --msgid / --mail-to."""
    if args.qid:
        _render_one_flow(token, f'message:"{args.qid}"',
                         f"qid {args.qid}",
                         mode, time_params, stream_id, raw=args.raw)
        return
    if args.msgid:
        _render_one_flow(token, f'message:"{args.msgid}"',
                         f"msgid {args.msgid}",
                         mode, time_params, stream_id, raw=args.raw)
        return
    if args.mail_to or args.mail_from:
        # 1) Finde Mails mit dem Empfänger / Absender
        mail_q = (_mail_clause("to", args.mail_to) if args.mail_to
                  else _mail_clause("from", args.mail_from))
        source_filter = " OR ".join(f'source:"{s}"' for s in MAIL_SOURCES)
        full_q = f"{mail_q} AND ({source_filter})"
        max_mails = args.limit if args.limit and args.limit < 50 else 5
        res = do_search(token, full_q, mode, time_params,
                        max_mails * 20, stream_id)  # mehr holen, um QIDs zu finden
        msgs = res.get("messages", [])
        # 2) QIDs extrahieren (eine pro Log-Line, dedupen, neueste zuerst)
        msgs.sort(key=lambda mm: mm.get("message", {}).get("timestamp", ""), reverse=True)
        qids: list[str] = []
        seen: set = set()
        # Bekannte 1-2 Hex-Words die KEINE QIDs sind (PIDs, Microsoft IDs etc.) -- heuristisch
        for mm in msgs:
            text = mm.get("message", {}).get("message", "")
            for cand in _QID_RX.findall(text):
                if 9 <= len(cand) <= 12 and cand not in seen:
                    seen.add(cand)
                    qids.append(cand)
                    break  # eine pro log
            if len(qids) >= max_mails:
                break
        if not qids:
            kind = "Empfänger" if args.mail_to else "Absender"
            target = args.mail_to or args.mail_from
            print(f"[INFO] Keine QIDs in den letzten {len(msgs)} Mails für {kind} {target!r} gefunden")
            return
        kind = "Empfänger" if args.mail_to else "Absender"
        target = args.mail_to or args.mail_from
        print(f"# {len(qids)} Mail(s) für {kind} {target!r}, neueste zuerst:")
        for qid in qids:
            _render_one_flow(token, f'message:"{qid}"', f"qid {qid}",
                             mode, time_params, stream_id, raw=args.raw)
        return
    sys.exit("[ERROR] mailflow benötigt eines von: --qid, --msgid, --mail-to, --mail-from")


# ----- verdicts --------------------------------------------------------------

def do_verdicts(token, args, mode, time_params, stream_id):
    """Run a count query per amavis verdict phrase and return a list of
    (verdict, category, count) tuples."""
    sources = args.source or [AMAVIS_DEFAULT_SOURCE]
    source_filter = " OR ".join(f'source:"{s}"' for s in sources)
    base_parts = []
    if args.query:
        base_parts.append(f"({args.query})")
    if len(sources) == 1:
        base_parts.append(source_filter)
    else:
        base_parts.append(f"({source_filter})")
    base_q = " AND ".join(base_parts)

    rows = []
    for verdict, category in AMAVIS_VERDICTS:
        phrase = f'message:"{verdict}"'
        q = f"{base_q} AND {phrase}" if base_q else phrase
        res = do_search(token, q, mode, time_params, 1, stream_id)
        rows.append((verdict, category, res.get("total_results", 0)))
    return rows, sources


# ----- main ------------------------------------------------------------------

def main():
    if "--help-ai" in sys.argv:
        print(HELP_AI); return
    if "--help" in sys.argv or "-h" in sys.argv:
        print(HELP_TXT); return

    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--action", default="query",
                   choices=["query", "count", "fields", "terms", "streams",
                            "verdicts", "mailflow"])
    p.add_argument("--query", default="")
    p.add_argument("--no-auto-quote", action="store_true",
                   help="Auto-Phrase-Quoting für --query deaktivieren")
    # time
    p.add_argument("--range", type=int, default=86400)
    p.add_argument("--last")
    p.add_argument("--today", action="store_true")
    p.add_argument("--yesterday", action="store_true")
    p.add_argument("--from", dest="from_", help="ISO-8601 start (UTC)")
    p.add_argument("--to", help="ISO-8601 end (UTC)")
    # filter sugar
    p.add_argument("--source", action="append")
    p.add_argument("--stream")
    p.add_argument("--mail-to")
    p.add_argument("--mail-from")
    # mailflow (Plan-Schritt 2)
    p.add_argument("--qid", help="Postfix Queue-ID für --action mailflow (z.B. 07BF77E025)")
    p.add_argument("--msgid", help="Message-ID für --action mailflow")
    # exclude (Plan-Schritt 4)
    p.add_argument("--exclude", action="append", metavar="PRESET|REGEX",
                   help="client-seitig auszublendende Patterns (mehrfach möglich); "
                        "Preset-Namen: " + ", ".join(EXCLUDE_PRESETS.keys()))
    p.add_argument("--list-excludes", action="store_true",
                   help="Liste alle bekannten --exclude-Presets mit Pattern und exit")
    p.add_argument("--client-filter", metavar="REGEX",
                   help="zusätzlicher Client-Filter -- nur Messages behalten, "
                        "die diesem Regex matchen (Auffangnetz für komplexe Queries)")
    # output
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--fields")
    p.add_argument("--all-fields", action="store_true")
    p.add_argument("--raw", "--json", action="store_true", dest="raw")
    p.add_argument("--no-truncate", action="store_true")
    # terms
    p.add_argument("--terms")
    p.add_argument("--terms-size", type=int, default=25)
    p.add_argument("--patterns", action="append", metavar="PATTERN",
                   help="regex/substring patterns to count (repeatable); use with --action terms")
    p.add_argument("--set-token", nargs="?", const="__prompt__", metavar="TOKEN",
                   help="Token im Windows Credential Manager speichern (ohne Wert: sichere Eingabe)")
    args = p.parse_args()

    if args.list_excludes:
        print("# bekannte --exclude-Presets:")
        for name, pat in EXCLUDE_PRESETS.items():
            print(f"  {name:20s}  {pat}")
        return

    if args.set_token is not None:
        import getpass
        token_value = args.set_token
        if token_value == "__prompt__":
            token_value = getpass.getpass("Graylog Token: ").strip()
            if not token_value:
                sys.exit("[ERROR] Kein Token eingegeben.")
        try:
            import keyring
            keyring.set_password("graylog", "ibf", token_value)
            print("[OK] Token im Windows Credential Manager gespeichert.")
        except Exception as e:
            sys.exit(f"[ERROR] keyring nicht verfügbar: {e}\n"
                     f"Installieren mit: pip install keyring")
        return

    token = load_token()
    truncate = not args.no_truncate

    # streams listing — short circuit
    if args.action == "streams":
        streams = gl("GET", "/streams", token).get("streams", [])
        if args.raw:
            print(json.dumps(streams, indent=2)); return
        print(f"# {len(streams)} streams")
        for s in sorted(streams, key=lambda x: x.get("title", "")):
            disabled = " [DISABLED]" if s.get("disabled") else ""
            print(f"  {s.get('id')}  {s.get('title')}{disabled}")
        return

    mode, time_params = resolve_window(args)
    stream_id = resolve_stream(token, args.stream)

    if args.action == "mailflow":
        do_mailflow(token, args, mode, time_params, stream_id)
        return

    if args.action == "verdicts":
        rows, sources = do_verdicts(token, args, mode, time_params, stream_id)
        if args.raw:
            print(json.dumps([{"verdict": v, "category": c, "count": n} for v, c, n in rows], indent=2))
            return
        print(f"amavis verdicts — source: {', '.join(sources)}")
        print(f"window: {mode} {time_params}\n")
        col = max(len(v) for v, _, _ in rows) + 2
        print(f"  {'Verdict':<{col}}  Count")
        print(f"  {'-' * (col + 9)}")
        last_cat = None
        for verdict, category, count in rows:
            if last_cat and category != last_cat:
                print(f"  {'-' * (col + 9)}")
            marker = " !" if (category == "block" and count > 0) else ""
            print(f"  {verdict:<{col}}  {count:>5}{marker}")
            last_cat = category
        total_block = sum(n for _, c, n in rows if c == "block")
        total_pass = sum(n for _, c, n in rows if c == "pass")
        print(f"  {'=' * (col + 9)}")
        print(f"  {'Total Blocked':<{col}}  {total_block:>5}")
        print(f"  {'Total Passed':<{col}}  {total_pass:>5}")
        return

    query = build_query(args)

    if args.action == "count":
        res = do_search(token, query, mode, time_params, 1, stream_id)
        if args.raw:
            print(json.dumps({"query": query, "total_results": res.get("total_results")}, indent=2))
        else:
            print(f"query: {query}")
            print(f"window: {mode} {time_params}")
            print(f"total_results: {res.get('total_results')}")
        return

    if args.action == "terms":
        if not args.terms:
            sys.exit("[ERROR] --terms <field> erforderlich für action=terms")
        res = do_terms(token, query, mode, time_params, args.terms, args.terms_size,
                       stream_id, patterns=args.patterns)
        if args.raw:
            print(json.dumps(res, indent=2)); return
        sample_info = (f"sample: {res.get('sample_size')}/{res.get('total')}"
                       if res.get("truncated") else f"total: {res.get('total')}")
        print(f"query: {query}")
        print(f"window: {mode} {time_params}")
        if res.get("truncated"):
            print(f"  WARN: counts based on most-recent {res.get('sample_size')} of {res.get('total')} msgs")
        if args.patterns:
            patterns_res = res.get("patterns", {})
            col = max((len(p) for p in patterns_res), default=10) + 2
            print(f"\nfield: {args.terms}  {sample_info}")
            print(f"  {'Pattern':<{col}}  Count")
            print(f"  {'-' * (col + 9)}")
            for p, count in patterns_res.items():
                print(f"  {p:<{col}}  {count:>5}")
            print(f"  {'-' * (col + 9)}")
            print(f"  {'(no match)':<{col}}  {res.get('no_match', 0):>5}")
        else:
            terms = res.get("terms", {})
            print(f"field: {args.terms}  {sample_info}  missing-field: {res.get('missing')}")
            print(f"top {min(args.terms_size, len(terms))} values:")
            for k, v in sorted(terms.items(), key=lambda x: -x[1]):
                print(f"  {v:>8}  {k}")
        return

    # query / fields — both go through do_search
    res = do_search(token, query, mode, time_params, args.limit, stream_id)
    messages = res.get("messages", [])
    total = res.get("total_results", 0)

    # Plan-Schritt 4 + 5: Client-seitiges Excluding / Filtering
    exclude_pats = _resolve_exclude_patterns(args.exclude)
    client_filter = _resolve_client_filter(args.client_filter)
    messages, n_excl, n_filt = _filter_messages(messages, exclude_pats, client_filter)

    if args.raw:
        cleaned = clean_raw_messages(
            messages,
            fields_arg=args.fields if args.action != "fields" else None,
            all_fields=args.all_fields or args.action == "fields",
        )
        print(json.dumps({
            "query": query,
            "total_results": total,
            "showing": len(cleaned),
            "messages": cleaned,
        }, indent=2))
        return

    print(f"query: {query}")
    if getattr(build_query, "_last_auto_quoted", False):
        print(f"  (auto-quoted: --query {args.query!r} -> Phrase)")
    print(f"window: {mode} {time_params}")
    suffix = ""
    if n_excl:
        suffix += f"   excluded: {n_excl}"
    if n_filt:
        suffix += f"   filtered: {n_filt}"
    print(f"total_results: {total}   showing: {len(messages)}{suffix}")

    if args.action == "fields":
        # force all-fields mode for discovery
        print_messages(messages, fields_arg=None, all_fields=True, truncate=truncate)
    else:
        print_messages(messages, fields_arg=args.fields,
                       all_fields=args.all_fields, truncate=truncate)

    if total > len(messages):
        print(f"\n... ({total - len(messages)} weitere Treffer, --limit erhöhen)")


if __name__ == "__main__":
    main()
