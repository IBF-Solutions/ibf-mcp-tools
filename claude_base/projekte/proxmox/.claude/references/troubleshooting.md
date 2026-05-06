# Proxmox Troubleshooting Reference

## Quick Lookup

| Problem | First Commands |
|---------|----------------|
| VM won't start | `qm unlock <vmid>`, `qm config <vmid>`, check storage |
| Container won't start | `pct unlock <ctid>`, `pct config <ctid>` |
| Cluster quorum lost | `pvecm status`, `pvecm expected <n>` |
| Storage unavailable | `pvesm status`, `mount \| grep nfs` |
| High CPU | `top`, `ps aux --sort=-%cpu \| head -10` |
| High memory / OOM | `free -h`, `journalctl -k \| grep -i oom` |
| Migration failed | Check shared storage, target resources, network |
| Disk full | `df -h`, `du -sh /* 2>/dev/null \| sort -h` |
| Network issues | `brctl show`, `bridge vlan show`, `ip route` |
| Backup failed | `pvesm status`, check space, task log |

## Diagnostic Commands

### Cluster Health

```bash
pvecm status                     # Quorum + online nodes
pvecm nodes                      # List members
systemctl status pve-cluster     # Cluster daemon
systemctl status corosync        # Corosync
```

### Node Health

```bash
pveversion -v                    # Proxmox version
free -h                          # Memory
df -h                            # Disk space
uptime                           # Load average
top -bn1 | head -20              # Process overview
dmesg | tail -50                 # Kernel messages
journalctl -k | grep -iE 'error|oom|hang|warn' | tail -30
```

### VM / Container

```bash
qm status <vmid>                 # VM state
qm config <vmid>                 # VM config
qm showcmd <vmid>                # QEMU command (debug)
qm unlock <vmid>                 # Clear lock
journalctl | grep <vmid>         # VM-related log entries

pct status <ctid>                # Container state
pct config <ctid>                # Container config
pct unlock <ctid>                # Clear lock
```

### Storage

```bash
pvesm status                     # All pools
zpool status                     # ZFS pool health (tank)
zpool list                       # ZFS space
df -h                            # Filesystem usage
mount | grep -E 'cifs|nfs'       # CIFS/NFS mounts (Hetzner StorageBoxes)
```

### Network

```bash
brctl show                       # Bridges + attached interfaces
bridge vlan show                 # VLAN config
ip link                          # Interface status
ip addr                          # IP addresses
ip route                         # Routing table
```

### Log Locations

| What | Where |
|------|-------|
| System | `journalctl`, `/var/log/syslog` |
| Kernel / OOM | `dmesg`, `journalctl -k` |
| Proxmox tasks | `/var/log/pve/tasks/`, `journalctl -u pve*` |
| Cluster | `journalctl -u pve-cluster` |
| Firewall | `journalctl -u pve-firewall` |
| Web UI | `journalctl -u pveproxy` |
| Auth | `/var/log/auth.log` |

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| VM locked | Interrupted operation | `qm unlock <vmid>` |
| TASK ERROR: storage not available | Mount failed | `pvesm status`, remount |
| migration error: ... no shared storage | No shared storage to target | Use `--with-local-disks` |
| OOM kill | Container/VM RAM too low | Increase memory limit |
| quorum loss | Node(s) down | `pvecm status`, `pvecm expected` |
| API 403 | Token permission issue | Check token privileges in Datacenter > API Tokens |
| CIFS mount fails | Hetzner StorageBox unreachable | Check network, DNS |

## Workflows

### VM Won't Start

1. `qm unlock <vmid>` — remove lock
2. `pvesm status` — verify storage accessible
3. `free -h` + `df -h` — check resources
4. `qm config <vmid>` — review config
5. `journalctl | grep <vmid>` — read error
6. `qm start <vmid> --debug` — verbose start

### OOM / Memory Issue

```bash
# Find OOM events
journalctl -k | grep -i 'out of memory'
journalctl -k | grep -i 'oom'

# Current memory per container/VM
pvesh get /cluster/resources | python3 -c "
import json, sys
r = json.load(sys.stdin)
for i in r['data']:
    if i.get('type') in ('qemu','lxc') and i.get('status') == 'running':
        print(f\"{i['vmid']:4} {i.get('name','?'):<30} {i.get('mem',0)/1024/1024/1024:.1f}GB / {i.get('maxmem',0)/1024/1024/1024:.1f}GB\")
"

# Increase LXC RAM (example: LXC 117 → 2GB)
pct set 117 --memory 2048
```

### Cluster Quorum Lost (3-node cluster)

Our cluster: 3 nodes → quorum = 2. Can lose 1 node.

```bash
pvecm status
# If majority unreachable:
pvecm expected 1   # DANGEROUS — only on authoritative node
```

### Remove Orphaned Snapshot / Volume

```bash
# Delete unused volume from LXC config
pct set <ctid> --delete unused0

# Delete ZFS snapshot manually
zfs list -t snapshot
zfs destroy <pool>/<dataset>@<snapname>
```

### Force Stop Locked VM

```bash
qm unlock <vmid>
# If still stuck:
ps aux | grep <vmid>
kill <qemu-pid>
qm stop <vmid> --skiplock
```
