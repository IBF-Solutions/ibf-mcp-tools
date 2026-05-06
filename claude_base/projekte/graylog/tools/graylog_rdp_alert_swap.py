#!/usr/bin/env python3
"""
Backup, clone-with-debounce, and disable the
'RDP Helper Proxmox API Fortigate' Graylog event definition.

Steps:
  1. Fetch current definition (id 66797be0722f954c70f616f6) -> save to analysis/
  2. POST a new definition with debounced config (same notification + stream)
  3. Disable the old definition (PUT /unschedule, fallback to state=DISABLED)
"""
import base64
import datetime as dt
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

OLD_ID = "66797be0722f954c70f616f6"
STREAM_ID = "66796cd2722f954c70f60a95"
NOTIFICATION_ID = "66712e5f722f954c70ef6cd3"
BASE = "https://gld.ibf-solutions.com/api"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = PROJECT_ROOT / "analysis"


def load_token():
    env = Path(r"C:\Temp\claude\.env")
    for line in env.read_text().splitlines():
        if line.startswith("graylog_ibf="):
            return line.split("=", 1)[1].strip()
    sys.exit("token not found")


def gl(method, path, token, body=None):
    auth = base64.b64encode(f"{token}:token".encode()).decode()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Accept", "application/json")
    req.add_header("X-Requested-By", "claude-code")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main():
    token = load_token()

    # ---- 1. Backup -------------------------------------------------------
    print(f"[1] GET /events/definitions/{OLD_ID}")
    status, current = gl("GET", f"/events/definitions/{OLD_ID}", token)
    if status != 200:
        sys.exit(f"  backup fetch failed: {status} {current}")
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUP_DIR / f"graylog-rdp-alert-backup-{ts}.json"
    backup_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    print(f"  -> backup written to {backup_path}")
    print(f"  -> current state: {current.get('state')}, "
          f"title: {current.get('title')}")

    # ---- 2. Build new (debounced) definition -----------------------------
    new_def = {
        "title": "RDP Helper Proxmox API Fortigate (debounced)",
        "description": (
            "Überwacht RDP Verbindungsanfragen von Fortigate und startet "
            "zugehörige Proxmox VM. Debounced: 1 Webhook pro dstport / 2min."
        ),
        "priority": current.get("priority", 2),
        "alert": current.get("alert", True),
        "config": {
            "type": "aggregation-v1",
            "query": "*",
            "query_parameters": [],
            "filters": [],
            "streams": [STREAM_ID],
            "stream_categories": [],
            "group_by": ["dstport"],
            "series": [
                {"id": "count", "type": "count", "field": None}
            ],
            "conditions": {
                "expression": {
                    "expr": ">",
                    "left":  {"expr": "number-ref", "ref": "count"},
                    "right": {"expr": "number", "value": 0},
                }
            },
            "search_within_ms": 30000,
            "execute_every_ms": 10000,
            "use_cron_scheduling": False,
            "cron_expression": None,
            "cron_timezone": None,
            "event_limit": 0,
        },
        "field_spec": {},
        "key_spec": [],
        "notification_settings": {
            "grace_period_ms": 120000,
            "backlog_size": 1,
        },
        "notifications": [
            {"notification_id": NOTIFICATION_ID, "notification_parameters": None}
        ],
        "storage": [
            {"type": "persist-to-streams-v1",
             "streams": ["000000000000000000000002"]}
        ],
        "state": "ENABLED",
    }
    payload_path = BACKUP_DIR / f"graylog-rdp-alert-new-{ts}.json"
    payload_path.write_text(json.dumps(new_def, indent=2), encoding="utf-8")
    print(f"  -> planned new definition saved to {payload_path}")

    print("[2] POST /events/definitions  (creating new debounced alert)")
    status, created = gl("POST", "/events/definitions?schedule=true",
                         token, body=new_def)
    if status not in (200, 201):
        sys.exit(f"  create failed: {status} {created}")
    new_id = created.get("id") if isinstance(created, dict) else None
    print(f"  -> new id: {new_id}, state: "
          f"{created.get('state') if isinstance(created, dict) else '?'}")

    # ---- 3. Disable old --------------------------------------------------
    print(f"[3] PUT /events/definitions/{OLD_ID}/unschedule  (disable old)")
    status, body = gl("PUT", f"/events/definitions/{OLD_ID}/unschedule", token)
    print(f"  -> status: {status}")
    if status not in (200, 204):
        # fallback: PUT full definition with state=DISABLED
        print("  -> unschedule endpoint failed, fallback to PUT full def")
        disabled = {k: v for k, v in current.items()
                    if k not in ("_scope", "id", "updated_at",
                                 "matched_at", "scheduler")}
        disabled["state"] = "DISABLED"
        status2, body2 = gl("PUT", f"/events/definitions/{OLD_ID}",
                            token, body=disabled)
        print(f"  -> fallback status: {status2}")
        if status2 not in (200, 204):
            print(f"  -> fallback body: {body2}")

    # ---- verify ----------------------------------------------------------
    print("[4] verify")
    status, old_now = gl("GET", f"/events/definitions/{OLD_ID}", token)
    print(f"  old state: {old_now.get('state') if isinstance(old_now, dict) else old_now}")
    status, new_now = gl("GET", f"/events/definitions/{new_id}", token)
    print(f"  new state: {new_now.get('state') if isinstance(new_now, dict) else new_now}")
    print(f"  new id  : {new_id}")
    print("\nDONE.")


if __name__ == "__main__":
    main()
