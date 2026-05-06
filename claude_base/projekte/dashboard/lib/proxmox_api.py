"""Minimaler Proxmox-VE-API-Client fürs Dashboard.

Token aus Windows Credential Manager (`proxmox-personal` / `ibf`).
Self-signed SSL -> Verifikation deaktiviert (gleich wie alle anderen
IBF-Tools, siehe `proxmox/CLAUDE.md`).

Bewusst nur die Endpoints die das Dashboard braucht -- kein generischer
Client. Für tiefergehende Aufrufe ist `tools/proxmox-query.py` da.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import keyring

PROXMOX_BASE = "https://192.168.10.1:8006/api2/json"
TIMEOUT_S = 5  # Fail-Fast: bei Personal-Proxmox-Unerreichbarkeit aus IBF-Netz


def _full_token() -> str:
    """Voller Proxmox-API-Token im Format `root@pam!<token-id>=<secret>`.

    Token-ID ist NICHT hardcoded -- Anwender wählt sie in der Proxmox-WebUI
    (z.B. `claude`, `claude_mcp`, ...) und legt den vollen String im
    Credential Manager unter `proxmox-personal`/`ibf` ab. Konsistent mit
    `proxmox-query.py` und `ibf-mcp.py`.
    """
    s = keyring.get_password("proxmox-personal", "ibf")
    if not s:
        raise RuntimeError(
            "Kein Proxmox-Token in Credential Manager (proxmox-personal/ibf)")
    if "!" not in s or "=" not in s:
        raise RuntimeError(
            f"Proxmox-Token hat unerwartetes Format -- erwartet "
            f"'root@pam!<id>=<secret>', bekommen: {s[:20]}...")
    return s


def _request(path: str, params: dict | None = None) -> Any:
    url = f"{PROXMOX_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"PVEAPIToken={_full_token()}",
        "Accept": "application/json",
    })
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=TIMEOUT_S) as r:
            data = json.loads(r.read())
        return data.get("data", data)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:200]
        raise RuntimeError(f"Proxmox HTTP {e.code} on {path}: {body}") from e


def cluster_resources() -> list[dict]:
    """Alle Cluster-Resources (nodes, VMs, LXCs, storage) in einem Call."""
    return _request("/cluster/resources") or []


def cluster_status() -> list[dict]:
    """Cluster-Health: Quorum, Node-Members."""
    return _request("/cluster/status") or []


def vms() -> list[dict]:
    return [r for r in cluster_resources() if r.get("type") in ("qemu", "lxc")]


def nodes() -> list[dict]:
    return [r for r in cluster_resources() if r.get("type") == "node"]


def storage_resources() -> list[dict]:
    return [r for r in cluster_resources() if r.get("type") == "storage"]


def failed_tasks(node: str, *, since_unix: int, limit: int = 100) -> list[dict]:
    """Fehlgeschlagene Tasks eines Nodes seit `since_unix`."""
    tasks = _request(f"/nodes/{node}/tasks", {"errors": 1, "limit": limit}) or []
    return [t for t in tasks if (t.get("starttime") or 0) >= since_unix]


def all_failed_tasks_since(since_unix: int) -> list[dict]:
    out: list[dict] = []
    for n in nodes():
        node = n.get("node")
        if not node:
            continue
        try:
            out.extend(failed_tasks(node, since_unix=since_unix))
        except RuntimeError:
            continue
    return out


def backup_tasks_since(since_unix: int) -> list[dict]:
    """vzdump-Tasks aller Nodes seit `since_unix`. Liest auch erfolgreiche."""
    out: list[dict] = []
    for n in nodes():
        node = n.get("node")
        if not node:
            continue
        try:
            tasks = _request(f"/nodes/{node}/tasks",
                             {"typefilter": "vzdump", "limit": 200}) or []
            out.extend([t for t in tasks
                        if (t.get("starttime") or 0) >= since_unix])
        except RuntimeError:
            continue
    return out


def snapshots(vmid: int, node: str, vm_type: str) -> list[dict]:
    typ = "qemu" if vm_type == "qemu" else "lxc"
    try:
        return _request(f"/nodes/{node}/{typ}/{vmid}/snapshot") or []
    except RuntimeError:
        return []
