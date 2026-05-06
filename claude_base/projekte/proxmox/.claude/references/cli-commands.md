# Proxmox CLI Commands Reference

Nodes in this cluster: **k1-low**, **k2**, **k5**

## qm — VM Management

```bash
qm list                          # List all VMs
qm status <vmid>                 # VM status
qm config <vmid>                 # Show VM config
qm start <vmid>                  # Start VM
qm stop <vmid>                   # Force stop
qm shutdown <vmid>               # ACPI shutdown
qm reboot <vmid>                 # ACPI reboot
qm unlock <vmid>                 # Remove stuck lock
qm set <vmid> --memory 4096      # Change RAM (MB)
qm set <vmid> --cores 4          # Change CPU cores
qm resize <vmid> scsi0 +10G      # Extend disk
qm migrate <vmid> <target-node>  # Live migrate
qm migrate <vmid> <target-node> --with-local-disks  # Migrate with local disk copy
qm snapshot <vmid> <name>        # Create snapshot
qm listsnapshot <vmid>           # List snapshots
qm rollback <vmid> <name>        # Rollback to snapshot
qm delsnapshot <vmid> <name>     # Delete snapshot
qm showcmd <vmid>                # Show QEMU command line (debug)
```

## pct — LXC Container Management

```bash
pct list                         # List all containers
pct status <ctid>                # Container status
pct config <ctid>                # Show config
pct start <ctid>                 # Start container
pct stop <ctid>                  # Stop container
pct shutdown <ctid>              # Graceful shutdown
pct enter <ctid>                 # Enter shell
pct exec <ctid> -- <command>     # Run command in container
pct set <ctid> --memory 2048     # Change RAM (MB)
pct set <ctid> --cores 2         # Change CPU cores
pct resize <ctid> rootfs +5G     # Extend rootfs
pct unlock <ctid>                # Remove stuck lock
pct push <ctid> <src> <dst>      # Copy file into container
pct pull <ctid> <src> <dst>      # Copy file from container
pct snapshot <ctid> <name>       # Create snapshot
pct listsnapshot <ctid>          # List snapshots
pct rollback <ctid> <name>       # Rollback to snapshot
pct migrate <ctid> <target-node> # Migrate container
pct set <ctid> --delete unused0  # Delete orphaned volume
```

## pvecm — Cluster Management

```bash
pvecm status                     # Quorum and node status
pvecm nodes                      # List cluster members
pvecm expected <votes>           # Force expected votes (split-brain recovery, DANGEROUS)
pvecm delnode <node>             # Remove failed node
```

## pvesh — API Shell

```bash
pvesh get /nodes                              # List nodes
pvesh get /nodes/<node>/status               # Node resource status
pvesh get /nodes/<node>/qemu                 # VMs on node
pvesh get /nodes/<node>/lxc                  # Containers on node
pvesh get /nodes/<node>/tasks                # Task log
pvesh get /cluster/resources                 # All cluster resources
pvesh get /cluster/status                    # Cluster health
pvesh get /nodes/<node>/storage              # Storage on node
pvesh get /nodes/<node>/qemu/<vmid>/snapshot # VM snapshots
```

## vzdump — Backup

```bash
vzdump <vmid> --mode snapshot --storage k1e-storbox2 --compress zstd
vzdump <vmid> --mode stop --storage k1e-storbox2 --compress zstd
vzdump --all --compress zstd                 # Backup everything

# Restore
qmrestore <backup.vma> <vmid>
qmrestore <backup.vma> <vmid> --storage local-lvm --force
pct restore <ctid> <backup.tar>
pct restore <ctid> <backup.tar> --storage tank
```

## pvesm — Storage Management

```bash
pvesm status                     # All storage pools status
pvesm list <storage>             # List content of storage
```

## Useful Combinations

```bash
# Check resources on all nodes
for node in k1-low k2 k5; do
  echo "=== $node ==="
  pvesh get /nodes/$node/status --output-format yaml | grep -E '^(cpu|memory):'
done

# List all running VMs/containers cluster-wide
qm list | grep running
pct list | grep running

# Find which node a VMID runs on
pvesh get /cluster/resources | grep '"vmid":185'
```
