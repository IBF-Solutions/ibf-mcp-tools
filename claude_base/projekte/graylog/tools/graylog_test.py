#!/usr/bin/env python3
"""Test Graylog API connection."""
import os
import sys
import urllib.request
import urllib.error
import base64
import json
from pathlib import Path


def load_token():
    """Load graylog_ibf token from .env file 2 dirs up."""
    env_path = Path(__file__).resolve().parents[3] / ".env"
    print(f"[*] Loading token from {env_path}")
    if not env_path.exists():
        sys.exit(f"[-] .env not found at {env_path}")
    for line in env_path.read_text().splitlines():
        if line.startswith("graylog_ibf="):
            return line.split("=", 1)[1].strip()
    sys.exit("[-] graylog_ibf token not found in .env")


def graylog_request(url, token, headers=None):
    """Make a request to Graylog using token auth (Basic auth: token:token)."""
    auth = base64.b64encode(f"{token}:token".encode()).decode()
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Accept", "application/json")
    req.add_header("X-Requested-By", "claude-code-audit")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return None, str(e)


def main():
    token = load_token()
    print(f"[*] Token loaded ({len(token)} chars)")

    base = "https://gld.ibf-solutions.com"

    # Test endpoints
    tests = [
        f"{base}/api/system",
        f"{base}/api/system/cluster/node",
        f"{base}/api/users/me",
    ]

    for url in tests:
        print(f"\n[*] GET {url}")
        status, body = graylog_request(url, token)
        print(f"    -> status: {status}")
        if isinstance(body, dict):
            print(f"    -> keys: {list(body.keys())[:8]}")
            # Print first ~300 chars of pretty JSON
            print(json.dumps(body, indent=2)[:600])
        else:
            print(f"    -> {str(body)[:300]}")


if __name__ == "__main__":
    main()
