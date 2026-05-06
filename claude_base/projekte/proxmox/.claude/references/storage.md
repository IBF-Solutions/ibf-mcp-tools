# Proxmox Storage Reference

## Storage Types in This Cluster

| Name | Type | Node | Content | Notes |
|------|------|------|---------|-------|
| `tank` | ZFS pool | all | images, rootdir | Primary VM/LXC storage |
| `local-lvm` | LVM-thin | all | images, rootdir | Fast block storage |
| `local` / `qcow2` | dir | all | backup, iso, images | Same underlying FS |
| `qcow2-sdb` | dir (ext4) | k5 only | rootdir, images, iso | Added 2026-04-28, 110 GB |
| `k1e-storbox` | CIFS | all | backup, iso | Hetzner StorageBox 100 GB |
| `k1e-storbox2` | CIFS | all | backup, iso | Hetzner StorageBox 100 GB |

Active backup targets: **k1e-storbox** and **k1e-storbox2** (CIFS mounts to Hetzner).

## Storage Type Properties

| Type | Snapshots | Live Migration | Thin Provisioning | Performance |
|------|-----------|---------------|-------------------|-------------|
| ZFS | Yes | No (local) | Yes | High + compression |
| LVM-thin | Yes | No (local) | Yes | Fast |
| dir | No | No (local) | No (full alloc) | Moderate |
| CIFS | No | No | No | Network-bound |

> Local storage (ZFS, LVM, dir) requires `--with-local-disks` for migration.

## Disk Formats

| Format | Use Case | Notes |
|--------|----------|-------|
| `raw` | Production VMs | Fastest, full allocation |
| `qcow2` | Dev / snapshots needed | Slightly slower, supports snapshots on dir storage |

ZFS and LVM-thin use their own snapshot mechanism — format less relevant.

## Disk Cache Modes

| Mode | Safety | Speed | When to Use |
|------|--------|-------|-------------|
| `none` | Safe | Good | **Default — recommended** |
| `writeback` | Unsafe | Best | Non-critical, battery-backed |
| `writethrough` | Safe | Moderate | Compatibility |
| `directsync` | Safest | Slow | Critical data |

## Performance Options

```bash
# Extend VM disk
qm resize <vmid> scsi0 +10G

# Enable discard/TRIM (SSD + LVM-thin)
# In VM config: scsi0: local-lvm:vm-100-disk-0,discard=on

# Dedicated I/O thread
# In VM config: scsi0: local-lvm:vm-100-disk-0,iothread=1
```

## Content Types

| Content | Description | Extension |
|---------|-------------|-----------|
| `images` | VM disk images | .raw, .qcow2 |
| `rootdir` | LXC root filesystem | directory |
| `backup` | vzdump backups | .vma, .tar |
| `iso` | Installation ISOs | .iso |
| `vztmpl` | LXC templates | .tar.gz |
| `snippets` | Cloud-init / hooks | .yaml |

## Diagnostics

```bash
pvesm status                     # All storage pools + usage
zpool status                     # ZFS health
zpool list                       # ZFS space
df -h                            # Filesystem-level usage
mount | grep cifs                # CIFS mounts (Hetzner)
du -sh /var/lib/vz/* 2>/dev/null | sort -h   # Content sizes
```

## Backup Retention (Current Config)

Both active jobs use `keep-last: 1` — single backup generation.
TODO: increase to `keep-last: 2` for VM 185 + LXC 117 (see CLAUDE.md).

## ZFS Tips

```bash
# ZFS pool status and health
zpool status tank

# Space usage including snapshots
zfs list -o name,used,avail,refer,mountpoint

# List all snapshots
zfs list -t snapshot

# Destroy orphaned snapshot
zfs destroy tank/<dataset>@<snapname>

# Check compression savings
zfs get compressratio tank
```

## CIFS / Hetzner StorageBox

Mounts at:
- `/mnt/pve/k1e-storbox/`
- `/mnt/pve/k1e-storbox2/`

```bash
# Check if mounted
mount | grep cifs

# Manual remount (if unavailable)
pvesm scan cifs k1e-storbox
```
