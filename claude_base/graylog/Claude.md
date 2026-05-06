# Graylog Connection & Operations Guide

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
    env = Path(r"C:\Temp\claude\.env")
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

For PUT/POST: add `Content-Type: application/json` and `data=json.dumps(body).encode()`.

## Useful endpoints

| Path | Description |
|------|-------------|
| `/api/system` | Server info (use this to verify auth — `/api/users/me` returns 404 for token auth) |
| `/api/system/cluster/nodes` | Cluster topology |
| `/api/system/inputs` | Configured log inputs (e.g. syslog UDP/TCP, GELF) |
| `/api/streams` | Defined log streams |
| `/api/streams/{id}` | Stream details + rules |
| `/api/streams/{id}/rules` | Stream routing rules |
| `/api/search/universal/relative` | Time-window search (GET, params: `query`, `range`, `limit`) |
| `/api/search/universal/absolute` | Absolute time-window search |
| `/api/search/messages` | Run a search query (POST) |
| `/api/events/definitions` | List/create event definitions (alerts) |
| `/api/events/definitions/{id}` | Get/PUT/DELETE specific alert |
| `/api/events/definitions/{id}/schedule` | PUT to enable |
| `/api/events/definitions/{id}/unschedule` | PUT to disable (preserves definition) |
| `/api/events/notifications` | Notification configurations (HTTP webhooks etc.) |
| `/api/events/notifications/{id}` | Get/PUT/DELETE specific notification |
| `/api/events/search` | POST: search event occurrences (`filter.event_definitions: [<id>]`, `timerange.range: <s>`) |
| `/api/dashboards` | Dashboards & widgets |
| `/api/system/notifications` | Active server notifications |
| `/api/system/indexer/cluster/health` | Elasticsearch/Opensearch backend health |
| `/api/system/indexer/indices` | Index storage stats |

The histogram endpoint `/api/search/universal/relative/histogram` does **not** exist in 6.x — to get per-minute breakdowns, query multiple cumulative windows and diff them.

## Stream/Alert architecture (current state on this instance)

FortiGate logs arrive via syslog at port `1514` (configured in FortiGate `log syslogd setting`). They land in the default index, then get routed by streams.

### Filter exclusions (FortiGate-side, NOT Graylog-side)

The FortiGate already drops these before sending to Graylog:
- IPv6 multicast (`dstip ff02::1:2`) — traffic
- `srcip 233.233.233.233` — webfilter, event, virus, attack

If you want to drop additional sources during a flood, **prefer doing it on the FortiGate** (`config log syslogd filter` → `config free-style`) rather than in Graylog — saves network bandwidth and Graylog input pipeline load.

### Known event definitions (Alerts)

Inventory as of 2026-05-04 — 26 total. Notable:

| ID | Title | Notes |
|---|---|---|
| `66797be0722f954c70f616f6` | RDP Helper Proxmox API Fortigate | **DISABLED** — replaced by debounced version below |
| `69f8c8f12a061d80ac216eb0` | RDP Helper Proxmox API Fortigate (debounced) | **ENABLED** — see "RDP Helper Alert" section |
| `667126f4722f954c70ef682e` | RDP Helper Proxmox API | (separate, not the FortiGate one) |
| `66a762bbe77cdb19c0b07df3` | RDP Helper Proxmox API → Homeassistant | |
| `6451040730f09b2a2b7f51bb` | Fortigate User Auth error | |
| `6397263e8bb17f062fbac392` | Fortigate User Auth success | |
| `654391b08036530374db5076` | SSTP Error | |

### RDP Helper Alert (operations playbook)

The "RDP Helper Proxmox API Fortigate" alert wakes Proxmox VMs when an RDP packet hits the FortiGate. Architecture:

1. **Stream** `66796cd2722f954c70f60a95` ("RDP Helper Fortigate") pre-filters with rules: `source=gw AND dstip=10.102.250.1 AND dstport in [4000..5600]`.
2. **Event definition** runs on that stream and triggers the notification.
3. **Notification** `66712e5f722f954c70ef6cd3` ("RDP Helper Notify VM start") POSTs to `http://10.10.10.33:18181/start_vm` — the wake daemon on `itl33-dockeri01`, which in turn calls Proxmox API on `10.10.20.16:8006` to start the VM.

**The original alert was un-debounced**, firing the webhook for every individual RDP retry packet. Result: the wake daemon hammered the Proxmox API hundreds of times per minute during normal RDP activity, contributing substantially to Graylog log volume.

**Debounced replacement** (`69f8c8f12a061d80ac216eb0`) created 2026-05-04:
- `query: *` (stream already filters)
- `group_by: ["dstport"]` — one logical event per VM
- `series: count` + `conditions: count > 0` (required when `group_by` is set)
- `search_within_ms: 30000`, `execute_every_ms: 10000`
- `field_spec: { dstport: template-v1 ${source.dstport}, require_values=true }`
- `key_spec: ["dstport"]` — **critical**: enables per-port grace period
- `notification_settings.grace_period_ms: 120000` — 2 min cooldown per VM

Effect measured immediately after rollout: ~5–7× reduction in Proxmox API calls (from ~20 msg/min to ~4/min for `srcip:10.10.10.33 AND dstip:10.10.20.16 AND dstport:8006`), with all active VMs still waking on first RDP attempt within 10s.

**Backup of original** is at `analysis/graylog-rdp-alert-backup-*.json`. To roll back:

```bash
# re-enable old:
PUT /api/events/definitions/66797be0722f954c70f616f6/schedule
# disable new:
PUT /api/events/definitions/69f8c8f12a061d80ac216eb0/unschedule
```

The swap script that did this is `tools/graylog_rdp_alert_swap.py`.

### Notes on `aggregation-v1` event definitions

A few quirks discovered while building the debounced alert:

- `key_spec` entries **must** match a key in `field_spec`. Setting `key_spec: ["dstport"]` without a corresponding `field_spec.dstport` returns HTTP 400 `"Event Definition key_spec can only contain fields defined in field_spec."`
- Without a `key_spec`, `grace_period_ms` debounces **globally per definition** — so a multi-key alert (e.g. per VM) where keys aren't declared will only deliver one webhook per cooldown across all keys, silently dropping wakes for other VMs.
- PUT to `/events/definitions/{id}` requires the `id` field in the body — without it returns HTTP 400 `"Event definition IDs don't match"`.
- POST to create new with immediate scheduling: `/events/definitions?schedule=true`.
- New definitions cannot have a non-empty `key_spec` unless `field_spec` is populated first — when cloning, populate `field_spec` and `key_spec` in the same PUT.

### `field_spec` template syntax

To extract a field from the matched message into the event key:

```json
"field_spec": {
  "dstport": {
    "data_type": "string",
    "providers": [
      {"type": "template-v1", "template": "${source.dstport}", "require_values": true}
    ]
  }
}
```

`${source.<field>}` references a field from the matched log message.

## Local tooling

| Path | Purpose |
|---|---|
| `tools/graylog_test.py` | Smoke test: hits `/api/system` |
| `tools/graylog_search.py` | CLI search wrapper: `graylog_search.py <query> [range_s] [limit]` |
| `tools/graylog_rdp_alert_swap.py` | Backup + clone-with-debounce + disable for the RDP alert (already executed; kept as reference for similar swaps) |

## Don't

- Don't write the token into committed scripts or memory files. Always read from `.env`.
- Don't send write/POST requests without the `X-Requested-By` header — Graylog will reject them.
- Don't test write endpoints with the audit token unless you know its scope.
- Don't filter source devices on the Graylog side if you can do it on the FortiGate — drop on the source, not in transit.
- Don't `DELETE` event definitions when changing them — `PUT /unschedule` keeps the JSON for rollback.

## Gotchas

- The `/api/users/me` endpoint returns `404 Couldn't find user me` for token-based auth — tokens aren't bound to a user account here. Use `/api/system` to verify auth works.
- Graylog's Swagger is at `https://gld.ibf-solutions.com/api/api-browser/` if you need to check schemas.
- The RDP wake daemon (`http://10.10.10.33:18181/start_vm`) is the *receiver* of the webhook, not part of Graylog. If wakes stop working, check both: alert fired (Graylog Events page) AND daemon reachable.
