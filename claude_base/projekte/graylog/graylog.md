# Graylog Connection Guide

This file is **excluded from the rest of the FortiGate project docs** to keep Graylog-specific knowledge isolated.

## Endpoint

- **URL**: `https://gld.ibf-solutions.com`
- **API base**: `https://gld.ibf-solutions.com/api`
- **Server version**: Graylog 6.2.7 "Noir"
- **Cluster ID**: `e04324a0-ac54-48aa-8e8a-8232805055fa`
- **Timezone**: Europe/Vienna

## Authentication

API access token is stored in the project root `.env`:

```
# C:\Temp\claude\.env
graylog_ibf=<token>
```

The token is 51 chars. Authentication uses **HTTP Basic Auth** with:
- username = the token value
- password = the literal string `token`

Equivalent header: `Authorization: Basic base64(<token>:token)`

You also need:
- `Accept: application/json`
- `X-Requested-By: <any-string>` (Graylog requires this header for CSRF protection on most write endpoints; safe to always send)

## How to call the API from this project

A working test harness is checked in at `tools/graylog_test.py`. It loads the token from `../../../.env` (3 levels up from `tools/`) and hits the `/api/system` endpoint.

Reusable Python pattern:

```python
import base64, urllib.request, json
from pathlib import Path

def gl_token():
    env = Path(__file__).resolve().parents[N] / ".env"  # adjust N
    for line in env.read_text().splitlines():
        if line.startswith("graylog_ibf="):
            return line.split("=", 1)[1].strip()

def gl_get(path, token):
    auth = base64.b64encode(f"{token}:token".encode()).decode()
    req = urllib.request.Request(f"https://gld.ibf-solutions.com/api{path}")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Accept", "application/json")
    req.add_header("X-Requested-By", "claude-code")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())
```

## Useful endpoints (for read-only audit work)

| Path | Description |
|------|-------------|
| `/api/system` | Server info (running so far) |
| `/api/system/cluster/nodes` | Cluster topology |
| `/api/system/inputs` | Configured log inputs (e.g. syslog UDP/TCP, GELF) |
| `/api/streams` | Defined log streams |
| `/api/streams/{id}/rules` | Stream routing rules |
| `/api/search/messages` | Run a search query (POST) |
| `/api/search/universal/relative` | Time-window search (GET) |
| `/api/search/universal/absolute` | Absolute time-window search |
| `/api/dashboards` | Dashboards & widgets |
| `/api/system/notifications` | Active server notifications/alerts |
| `/api/system/indexer/cluster/health` | Elasticsearch/Opensearch backend health |
| `/api/system/indexer/indices` | Index storage stats |
| `/api/cluster/{nodeId}/jvm` | JVM stats for the leader node |

## Notes & gotchas

- The `/api/users/me` endpoint returns `404 Couldn't find user me` for token-based auth — tokens are not bound to a user account in this Graylog setup. Use `/api/system` to verify auth works.
- Graylog's API self-documents at `https://gld.ibf-solutions.com/api/api-browser/` (Swagger).
- This Graylog instance receives FortiGate logs via syslog at port `1514` (configured in FortiGate `log syslogd setting`).

## Don't

- Don't write the token into committed scripts or memory files. Always read from `.env`.
- Don't send write/POST requests without the `X-Requested-By` header — Graylog will reject them.
- Don't test write endpoints with the audit token unless you know its scope.
