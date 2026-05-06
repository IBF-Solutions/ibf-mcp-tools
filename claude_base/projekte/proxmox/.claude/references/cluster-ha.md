# Proxmox Cluster & HA Reference

## This Cluster

3 nodes: **k1-low** (192.168.10.1), **k2** (192.168.10.2), **k5** (192.168.10.5)
Cluster name: **K1-Serverfarm**

Quorum: 3 nodes → needs 2 online. Can lose **1 node** before quorum is lost.

## Quorum Reference

| Nodes | Quorum needed | Max node loss |
|-------|---------------|---------------|
| 2 | 2 | 0 (use QDevice!) |
| **3** | **2** | **1** ← our setup |
| 4 | 3 | 1 |
| 5 | 3 | 2 |

## VM/LXC Decision Matrix

| Factor | Use VM (QEMU) | Use LXC |
|--------|--------------|---------|
| OS | Any (Windows, BSD) | Linux only |
| Isolation | Full kernel isolation | Shared host kernel |
| Performance | Good | Better (less overhead) |
| Startup | Slower (seconds) | Fast (<1s) |
| Snapshots | Yes (qcow2 / ZFS) | Yes (ZFS / LVM) |
| Docker inside | Clean | Needs nesting+privileged |
| GPU passthrough | Yes | No (directly) |

## Live Migration Requirements

| Storage Type | Live Migration | Offline Migration |
|-------------|----------------|-------------------|
| Shared (Ceph, NFS) | Yes | Yes |
| Local (ZFS, LVM) | No | Yes (with `--with-local-disks`) |

```bash
# Migrate with local disk (offline)
qm migrate <vmid> <target-node> --with-local-disks

# LXC migrate
pct migrate <ctid> <target-node>
```

## High Availability (HA)

HA auto-restarts a VM on another node if the host fails.

**Requirements:**
- Shared storage (Ceph, NFS, iSCSI) — we don't have Ceph
- Fencing (watchdog) configured
- VM added to HA in Datacenter → HA

**Note:** Without shared storage, HA cannot live-migrate. Only useful for restart-on-failure with local storage + offline failover.

## Corosync (Cluster Communication)

Port: **5405 UDP**. Low-latency network required.

```bash
# Check corosync
systemctl status corosync
journalctl -u corosync | tail -50

# Config location
cat /etc/pve/corosync.conf
```

## Split-Brain Recovery (Emergency)

Only do this if majority of nodes is truly lost and you're sure:

```bash
# On the authoritative node only
pvecm expected 1

# Rejoin other nodes after recovery
pvecm add <existing-healthy-node>
```

## Node Resource Targets (IBF Cluster)

| Node | vCPU | RAM | Status |
|------|------|-----|--------|
| k1-low | 4 | 15.5 GB | Chronically saturated — avoid new workloads |
| k2 | 16 | 31 GB | RAM 94% (LocalAI 24 GB allocation) |
| k5 | 8 | 39 GB | Best headroom — preferred for new containers |

Migration preference: **k1-low → k5** (k5 has most free RAM/CPU).

## Cluster Diagnostics

```bash
pvecm status                     # Quorum + node list
pvecm nodes                      # Members + vote count
systemctl status pve-cluster corosync
journalctl -u pve-cluster | tail -30
journalctl -u corosync | tail -30
ping <node-ip>                   # Network reachability
```
