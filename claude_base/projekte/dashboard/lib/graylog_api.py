"""Minimaler Graylog-REST-Client für das Dashboard.

Brauchen wir, weil das `mcp__ibf__graylog_*`-Tool nur relative Time-Ranges
unterstützt (`last="1h"`), wir fürs Dashboard aber **absolute** Ranges
(yesterday 00:00 .. yesterday 23:59) brauchen, sonst werden DST-Wechsel /
Run-Uhrzeit zur Bias-Quelle für Trend-Vergleiche.

Auth: Token aus Windows Credential Manager (`graylog`/`ibf`), Basic-Auth
mit Suffix `:token` (Graylog-Konvention).

Nur die zwei Endpoints, die das Dashboard braucht:
- count(query, since, until)         -> /search/universal/absolute (limit=0)
- top_values(query, field, ..., size) -> /search/universal/absolute/terms

Beides liest `total_results` bzw. `terms` aus der Antwort.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

import keyring

GRAYLOG_BASE = "https://gld.ibf-solutions.com/api"
USER_AGENT = "ibf-dashboard"
TIMEOUT_S = 30


def _token() -> str:
    t = keyring.get_password("graylog", "ibf")
    if not t:
        raise RuntimeError(
            "Kein Graylog-Token in Windows Credential Manager (graylog/ibf). "
            "Setzen mit: python -c \"import keyring; "
            "keyring.set_password('graylog', 'ibf', '<TOKEN>')\"")
    return t


def _to_iso_utc(d: dt.datetime) -> str:
    """Naive datetime (lokale Zeit) -> ISO-8601 in UTC mit Millisekunden + 'Z'."""
    if d.tzinfo is None:
        d = d.astimezone()  # bindet aktuelle lokale TZ
    d = d.astimezone(dt.timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def _auth_header() -> str:
    return "Basic " + base64.b64encode(f"{_token()}:token".encode()).decode()


def _request(path: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    url = f"{GRAYLOG_BASE}{path}?{qs}"
    req = urllib.request.Request(url, headers={
        "Authorization": _auth_header(),
        "X-Requested-By": USER_AGENT,
        "Accept": "application/json",
    })
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=TIMEOUT_S) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"Graylog HTTP {e.code} on {path}: {body}") from e


def _request_post(path: str, body: dict) -> dict:
    url = f"{GRAYLOG_BASE}{path}"
    req = urllib.request.Request(url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": _auth_header(),
            "X-Requested-By": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=TIMEOUT_S) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"Graylog HTTP {e.code} on {path}: {body}") from e


def count(query: str, *, since: dt.datetime, until: dt.datetime) -> int:
    """Anzahl Treffer im absoluten Zeitfenster."""
    data = _request("/search/universal/absolute", {
        "query": query or "*",
        "from": _to_iso_utc(since),
        "to": _to_iso_utc(until),
        "limit": 0,
    })
    return int(data.get("total_results", 0))


def messages(query: str, *,
             since: dt.datetime, until: dt.datetime,
             limit: int = 100,
             fields: str = "") -> list[dict]:
    """Liefert Message-Liste im absoluten Zeitfenster (sortiert nach
    timestamp:desc, jüngste zuerst).

    Returns Liste der `message`-Dicts (also schon flach, ohne den äußeren
    `{message: ...}`-Wrapper)."""
    params: dict = {
        "query": query or "*",
        "from": _to_iso_utc(since),
        "to": _to_iso_utc(until),
        "limit": limit,
        "sort": "timestamp:desc",
    }
    if fields:
        params["fields"] = fields
    data = _request("/search/universal/absolute", params)
    return [entry.get("message", {}) for entry in data.get("messages", [])]


def top_values(query: str, field: str, *,
               since: dt.datetime, until: dt.datetime,
               size: int = 10,
               fetch_cap: int = 2000) -> list[tuple[str, int]]:
    """Top-N häufigste Werte eines Feldes -- exakte Counts via Graylog
    Aggregation-API (`POST /search/aggregate`).

    Bei Fehler (z.B. Endpoint nicht verfügbar in alten Graylog-Versionen)
    Fallback auf Sample-Counter über bis zu `fetch_cap` Messages.
    """
    # Internes Limit erhöhen: bei kleinem size ist die OpenSearch-
    # term-Aggregation pro Shard unscharf (Sampling pro Shard), erst ab
    # ~50 stabilisieren sich die Counts. Client-seitig auf size clippen.
    internal_limit = max(size, 50)
    body = {
        "query": query or "*",
        "streams": [],
        "stream_categories": [],
        "timerange": {
            "type": "absolute",
            "from": _to_iso_utc(since),
            "to": _to_iso_utc(until),
        },
        "group_by": [{"field": field, "limit": internal_limit}],
        "metrics": [{"function": "count"}],
    }
    try:
        data = _request_post("/search/aggregate", body)
    except RuntimeError:
        return _top_values_sample(query, field, since=since, until=until,
                                  size=size, fetch_cap=fetch_cap)
    out: list[tuple[str, int]] = []
    for row in data.get("datarows", []):
        if len(row) < 2 or row[0] is None or row[0] == "":
            continue
        try:
            out.append((str(row[0]), int(row[1])))
        except (TypeError, ValueError):
            continue
        if len(out) >= size:
            break
    return out


def _top_values_sample(query: str, field: str, *,
                       since: dt.datetime, until: dt.datetime,
                       size: int = 10, fetch_cap: int = 2000) -> list[tuple[str, int]]:
    """Fallback: holt Sample der Messages und zählt client-seitig.
    Counts sind nivelliert wenn `total > fetch_cap`."""
    from collections import Counter
    data = _request("/search/universal/absolute", {
        "query": query or "*",
        "from": _to_iso_utc(since),
        "to": _to_iso_utc(until),
        "limit": fetch_cap,
        "fields": field,
    })
    counter: Counter = Counter()
    for entry in data.get("messages", []):
        msg = entry.get("message", {})
        v = msg.get(field)
        if v is None or v == "":
            continue
        counter[str(v)] += 1
    return counter.most_common(size)
