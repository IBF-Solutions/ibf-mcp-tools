# Proxmox Cluster

**Alias:** `proxmox` — Wenn der Nutzer „proxmox" schreibt, ist dieses Subprojekt gemeint.

## Connection

| Property | Value |
|---|---|
| Web UI | https://192.168.10.1:8006/ |
| API Base | https://192.168.10.1:8006/api2/json |
| Token ID | `root@pam!claude` |
| Token Secret | Windows Credential Manager: `proxmox-personal` / `ibf` |

## Authentication

All API calls use the Proxmox API token header (no session cookie needed):

```bash
# Token aus Credential Manager laden (Python):
# import keyring; token = keyring.get_password("proxmox-personal", "ibf")

curl -sk \
  -H "Authorization: PVEAPIToken=root@pam!claude=<secret-aus-credential-manager>" \
  https://192.168.10.1:8006/api2/json/<endpoint>
```

The `-sk` flags skip SSL certificate verification (self-signed cert).
Privilege Separation is **disabled** — token inherits full root permissions.

## Cluster Nodes

| Node | vCPU | RAM | Uptime | Notes |
|---|---|---|---|---|
| `k2` | 16 | 31 GB | — | LocalAI host; low CPU, high RAM usage |
| `k5` | 8 | 39 GB | — | Frigate + HA-DB; moderate load |
| `k1-low` | 4 | 15.5 GB | — | Most containers; chronically CPU/RAM saturated |

## VM & Container Inventory

| VMID | Name | Type | Node | Status | Notes |
|---|---|---|---|---|---|
| 100 | debian13-basic | qemu | k5 | stopped | Template/spare |
| 101 | 183p-d-frigate | lxc | k5 | running | NVR / camera system |
| 102 | k81-webserver-lms-docker | lxc | k1-low | running | Web + LMS (Docker) |
| 103 | k84-mqtt | lxc | k1-low | running | MQTT broker |
| 104 | 119-rustdesk | lxc | k1-low | running | RustDesk relay |
| 105 | 156-vaultwarden2 | lxc | k1-low | running | Vaultwarden password manager |
| 106 | 68-debian13-localai | qemu | k2 | running | Local AI inference (GPU likely) |
| 107 | 118-Edomi | lxc | k5 | stopped | Smart home (Edomi) |
| 110 | tools-93-mikrotik-ros-chr | qemu | k5 | stopped | MikroTik CHR test VM |
| 114 | debianMini12 | qemu | k1-low | stopped | Spare/test |
| 115 | vm-windows11 | qemu | k5 | stopped | Windows 11 VM |
| 116 | cloudflared2 | lxc | k5 | stopped | Cloudflare tunnel |
| 117 | 86-homeass-db | lxc | **k2** | running | Home Assistant database (MariaDB) — migriert k5→k2 am 2026-04-28 |
| 122 | 65-paperless-v4 | qemu | k1-low | running | Paperless-ngx DMS |
| **130** | **claude-workhorse** | lxc | k5 | running | **Tool-LXC für Cam/Network-Management** — siehe unten |
| 109 | pw-workhorse192 | lxc | k5 | running | Debian 13, SSH-only, IP 192.168.10.79, angelegt 2026-05-04 via proxmox-query.py |
| 185 | 85-homeassistantV2 | qemu | k1-low | running | Home Assistant main instance |

## Storage

| Name | Type | Capacity | Used | Content |
|---|---|---|---|---|
| `tank` | ZFS pool | ~450–3600 GB / node | varies | images, rootdir |
| `local-lvm` | LVM-thin | 141 GB | — | images, rootdir |
| `local` | dir | 68 GB | ~11–27 GB | backup, iso, images |
| `qcow2` | dir | 68 GB | ~11–27 GB | images, iso |
| `k1e-storbox` | CIFS (Hetzner StorageBox) | 100 GB | ~81 GB | backup, iso |
| `k1e-storbox2` | CIFS (Hetzner StorageBox) | 100 GB | ~69 GB | backup, iso |
| `qcow2-sdb` (k5 only) | dir on /dev/sdb1 (ext4) | 110 GB | leer | rootdir, images, iso |

## Common API Endpoints

| Endpoint | Description |
|---|---|
| `GET /nodes` | List all nodes and their status |
| `GET /nodes/<node>/qemu` | List VMs on a node |
| `GET /nodes/<node>/lxc` | List LXC containers on a node |
| `GET /nodes/<node>/status` | Node resource usage (CPU, RAM, storage) |
| `GET /nodes/<node>/storage` | Storage pools on a node |
| `GET /nodes/<node>/tasks` | Recent task log for a node |
| `POST /nodes/<node>/qemu/<vmid>/status/start` | Start a VM |
| `POST /nodes/<node>/qemu/<vmid>/status/stop` | Stop a VM |
| `POST /nodes/<node>/qemu/<vmid>/status/shutdown` | Graceful shutdown |
| `GET /cluster/resources` | All cluster resources (VMs, nodes, storage) |
| `GET /cluster/status` | Cluster health |

## Notes

- SSL certificate is self-signed — always use `-k` / `--insecure` with curl
- Network: 192.168.10.0/23 (same LAN as MikroTik router at 192.168.10.100)
- k1-low is chronically resource-constrained — avoid adding more workloads there
- `85-homeassistantV2` and `86-homeass-db` together form the HA stack (VM 185 + LXC 117)
- SSH key at `~/.ssh/proxmox_claude` — root access to all three nodes
- **claude-workhorse (LXC 130 auf k5)** — Tool-/Jumphost-Container, ausführliche Doku in `../../CLAUDE_workhorse.md` (Repo-Root).
- k1e-storbox and k1e-storbox2 are Hetzner StorageBoxes (CIFS)
- VM 101 (Frigate) intentionally has no backup — camera recordings are transient
- k5 NIC (`eno1`, e1000e): EEE deaktiviert (2026-04-28) — war Absturzursache (Hardware Unit Hang)
- k1-low NIC (`enp3s0`, r8169): EEE deaktiviert (2026-04-28) — beheben von 223 Link-Down Events / 90 Tage
- LXC 101 (Frigate) Coral USB: Whole-Bus-Passthrough statt einzelner Devices — robust gegen Re-Enumeration (1a6e:089a ↔ 18d1:9302)

---

