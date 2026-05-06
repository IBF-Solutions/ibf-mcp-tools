"""Minimaler Hetzner-Cloud-API-Client fürs Dashboard.

Token aus Credential Manager (`hetzner` / `ibf`). Bearer-Auth.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

import keyring

HETZNER_BASE = "https://api.hetzner.cloud/v1"
TIMEOUT_S = 15


def _token() -> str | None:
    return keyring.get_password("hetzner", "ibf")


def is_available() -> bool:
    return bool(_token())


def _request(path: str, params: dict | None = None) -> dict:
    url = f"{HETZNER_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    tok = _token()
    if not tok:
        raise RuntimeError("Kein Hetzner-Token (Credential Manager: hetzner/ibf)")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {tok}",
        "Accept": "application/json",
    })
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=TIMEOUT_S) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:200]
        raise RuntimeError(f"Hetzner HTTP {e.code} on {path}: {body}") from e


def servers() -> list[dict]:
    return _request("/servers", {"per_page": 50}).get("servers", [])


def volumes() -> list[dict]:
    return _request("/volumes", {"per_page": 50}).get("volumes", [])
