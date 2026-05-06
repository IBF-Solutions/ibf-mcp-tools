---
name: Proxmox Cluster Connection
description: API credentials and node inventory for the local Proxmox cluster at 192.168.10.1
type: project
originSessionId: fd52a0de-5a35-45e2-8454-a9c34a1933c2
---
Proxmox cluster at https://192.168.10.1:8006/ — API token auth, 3 nodes all online.

**Connection:**
- API Token ID: `root@pam!claude`
- API Token Secret: Windows Credential Manager — `proxmox-personal` / `ibf`
- SSL cert is self-signed — always use `curl -sk`

**Auth header:** `Authorization: PVEAPIToken=root@pam!claude=<keyring:proxmox-personal/ibf>`

**Nodes:** k2, k5, k1-low (all online as of 2026-04-28)

**Why:** This is the user's home lab Proxmox cluster managed via Claude.

**How to apply:** Use curl with the above header for all Proxmox API calls. Never use WebFetch (no custom header support) — always use Bash + curl.
