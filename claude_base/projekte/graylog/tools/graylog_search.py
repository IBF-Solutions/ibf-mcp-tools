#!/usr/bin/env python3
"""Search Graylog for messages matching a query."""
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
import base64
import json
from pathlib import Path


def load_token():
    env_path = Path(__file__).resolve().parents[3] / ".env"
    for line in env_path.read_text().splitlines():
        if line.startswith("graylog_ibf="):
            return line.split("=", 1)[1].strip()
    sys.exit("[-] graylog_ibf token not found in .env")


def gl_get(path, token, params=None):
    url = f"https://gld.ibf-solutions.com/api{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    auth = base64.b64encode(f"{token}:token".encode()).decode()
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Accept", "application/json")
    req.add_header("X-Requested-By", "claude-code-audit")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main():
    if len(sys.argv) < 2:
        print("Usage: graylog_search.py <query> [range_seconds] [limit]")
        print("  e.g. graylog_search.py 'srcip:10.10.40.225' 86400 50")
        sys.exit(1)

    query = sys.argv[1]
    range_s = int(sys.argv[2]) if len(sys.argv) > 2 else 3600
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 50

    token = load_token()
    print(f"[*] query: {query}")
    print(f"[*] range: last {range_s}s ({range_s//3600}h)")
    print(f"[*] limit: {limit}\n")

    params = {
        "query": query,
        "range": range_s,
        "limit": limit,
    }
    fields = os.environ.get("GL_FIELDS")
    if fields:
        params["fields"] = fields
    status, body = gl_get("/search/universal/relative", token, params)

    if status != 200:
        print(f"[-] HTTP {status}: {body}")
        sys.exit(1)

    print(f"[+] Total results in range: {body.get('total_results')}")
    print(f"[+] Returned messages: {len(body.get('messages', []))}\n")

    raw = os.environ.get("GL_RAW") == "1"
    for m in body.get("messages", [])[:limit]:
        msg = m.get("message", {})
        if raw:
            print(json.dumps(msg, indent=2)[:2000])
            print("---")
            continue
        ts = msg.get("timestamp", "?")
        src = msg.get("srcip") or msg.get("src_ip") or msg.get("src") or msg.get("source", "?")
        dst = msg.get("dstip") or msg.get("dst_ip") or msg.get("dst", "?")
        dport = msg.get("dstport") or msg.get("dst_port") or "?"
        svc = msg.get("service", "?")
        action = msg.get("action", "?")
        pid = msg.get("policyid", "?")
        full_msg = (msg.get("message") or msg.get("full_message") or "")[:160]
        print(f"  {ts}  {src} -> {dst}:{dport}  svc={svc} action={action} policyid={pid}")
        if full_msg:
            print(f"    {full_msg}")


if __name__ == "__main__":
    main()
