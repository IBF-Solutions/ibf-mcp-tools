#!/usr/bin/env python3
"""Proxmox Query Tool -- IBF Proxmox Cluster (192.168.10.1:8006).

Read-only CLI for ad-hoc cluster queries. See --help for usage.
"""
import argparse
import datetime as dt
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


PROXMOX_BASE = "https://192.168.10.1:8006/api2/json"
KNOWN_NODES   = ["k1-low", "k2", "k5"]

# context -> (keyring service, path fragment that must appear in __file__)
_CTX_CONFIG = {
    "ibf":      ("proxmox-ibf",      r"\ibf"),
    "personal": ("proxmox-personal", r"\personal"),
}


HELP_AI = """proxmox-query.py -- Proxmox cluster CLI

ACTIONS (--action, default: status)
  status    cluster health + node overview (CPU, RAM, VM count, uptime)
  nodes     detailed per-node resources incl. swap, rootfs, kernel
  vms       VM/LXC inventory with live resource usage
  tasks     recent task log (cluster-wide or per node)
  storage   storage pool usage
  snapshots list snapshots for a VM or all VMs
  exec      raw API GET call (path exploration, always --raw)
  control   VM/LXC lifecycle: create, power, migrate, snapshot, config, resize

READ FILTER
  --node <name>             restrict to node: k1-low, k2, k5
  --vmid <id>               filter by VMID
  --type vm|lxc|all         QEMU VM, LXC container, or both (default: all)
  --status running|stopped  filter by status

READ OUTPUT
  --limit <n>   max entries for tasks (default 50)
  --raw         output raw JSON (all actions)
  --fields k,k  for --raw: keep only these keys per entry

EXEC-SPECIFIC
  --path /nodes/k5/status   API path (required for exec action)

CONTROL (--action control --op <op>)

  Power / state  (require --vmid):
    start, stop, shutdown, reboot, suspend, resume, unlock

  Migration  (require --vmid --target):
    migrate       --target <node>  [--wait]
                  for QEMU: copies local disks automatically

  Snapshots  (require --vmid --snapname):
    snapshot      [--description <text>]  [--wait]
    delsnapshot   [--wait]
    rollback      [--wait]

  Config  (require --vmid):
    set           --config KEY=VALUE  (repeatable)
                  e.g. --config memory=2048 --config cores=2
    resize        --disk <disk> --size <delta_or_abs>
                  e.g. --disk scsi0 --size +10G  or  --disk rootfs --size +5G

  Create LXC  (no --vmid needed, auto-assigned):
    create        --hostname <name>          (required)
                  --node <node>              (default: k5)
                  --template <volid>         (default: debian-13 on local)
                  --storage <pool>           (default: tank)
                  --disk-size <GB>           (default: 8)
                  --memory <MB>              (default: 512)
                  --cores <n>                (default: 1)
                  --bridge <bridge>          (default: vmbr0)
                  --ip <cidr|dhcp>           (default: dhcp)
                  --ssh-key <pubkey>         (recommended; no password set)
                  --start                    start after creation
                  --wait                     wait for task

  Global control flag:
    --wait        poll task until finished (timeout 600s)

TOKEN  (context-aware -- auto-detected from local IP)
  Network 10.10.40.x  --> Kontext 'ibf'      --> Credential Manager: proxmox-ibf/ibf
  other networks      --> Kontext 'personal' --> Credential Manager: proxmox-personal/ibf
  Path mismatch (script in wrong tree) prints a warning to stderr.

  --set-token [VALUE]       save token to Windows Credential Manager (context auto-selected)
                            omit VALUE for secure prompt

EXAMPLES
  Start VM 185 and wait:
    proxmox-query.py --action control --op start --vmid 185 --wait

  Shutdown LXC 117:
    proxmox-query.py --action control --op shutdown --vmid 117

  Migrate VM 185 to k5 (local disk):
    proxmox-query.py --action control --op migrate --vmid 185 --target k5 --wait

  Snapshot VM 185:
    proxmox-query.py --action control --op snapshot --vmid 185 --snapname pre-update --wait

  Set RAM on LXC 117 to 2 GB:
    proxmox-query.py --action control --op set --vmid 117 --config memory=2048

  Resize rootfs of LXC 109:
    proxmox-query.py --action control --op resize --vmid 109 --disk rootfs --size +5G

  Create Debian 13 LXC with SSH key:
    proxmox-query.py --action control --op create \\
      --hostname myserver --node k5 --memory 512 --disk-size 8 \\
      --ssh-key "ssh-ed25519 AAAA..." --start --wait

  List all running VMs:
    proxmox-query.py --action vms --status running

  Raw API call:
    proxmox-query.py --action exec --path /nodes/k5/status
"""

HELP_TXT = """Proxmox Query Tool -- Hilfe

Liest aus und steuert IBF-Proxmox-Cluster (192.168.10.1:8006) per REST-API.

Aktionen (--action, Default: status):
  status    Cluster-Health + Node-Übersicht (CPU, RAM, VM-Anzahl, Uptime)
  nodes     Detaillierte Node-Ressourcen (Swap, RootFS, Kernel)
  vms       VM/LXC-Inventar mit Live-Ressourcenauslastung
  tasks     Aufgaben-Log -- cluster-weit oder per Node
  storage   Storage-Pools mit Belegung
  snapshots Snapshots einer VM oder aller VMs
  exec      Roher API-GET-Aufruf (Erkundung, immer JSON)
  control   VM/LXC Lifecycle: anlegen, steuern, migrieren, Snapshots, Config

Lese-Filter:
  --node <name>             Nur dieser Node (k1-low, k2, k5)
  --vmid <id>               Filter auf VMID
  --type vm|lxc|all         QEMU-VM, LXC-Container oder beide (Default: all)
  --status running|stopped  Filter auf Status

Lese-Output:
  --limit <n>   Max. Einträge bei tasks (Default 50)
  --raw         JSON-Ausgabe (zum Pipen)
  --fields k,k  Bei --raw: nur diese Keys pro Eintrag

Control (--action control --op <op>):

  Power / Status  (benötigen --vmid):
    start, stop, shutdown, reboot, suspend, resume, unlock

  Migration  (benötigt --vmid --target):
    migrate       --target <node>  [--wait]
                  QEMU: lokale Disks werden automatisch mitkopiert

  Snapshots  (benötigen --vmid --snapname):
    snapshot      [--description <text>]  [--wait]
    delsnapshot   [--wait]
    rollback      [--wait]

  Config  (benötigen --vmid):
    set           --config KEY=VALUE  (wiederholbar)
                  z.B. --config memory=2048 --config cores=2
    resize        --disk <disk> --size <delta_oder_abs>
                  z.B. --disk scsi0 --size +10G  oder  --disk rootfs --size +5G

  LXC anlegen  (kein --vmid nötig, wird automatisch vergeben):
    create        --hostname <name>           (Pflicht)
                  --node <node>               (Default: k5)
                  --template <volid>          (Default: debian-13 auf local)
                  --storage <pool>            (Default: tank)
                  --disk-size <GB>            (Default: 8)
                  --memory <MB>              (Default: 512)
                  --cores <n>                (Default: 1)
                  --bridge <bridge>          (Default: vmbr0)
                  --ip <cidr|dhcp>           (Default: dhcp)
                  --ssh-key <pubkey>         (empfohlen; kein Passwort gesetzt)
                  --start                    nach Erstellung starten
                  --wait                     auf Task warten

  Globales Flag:
    --wait        Auf Task-Abschluss warten (Timeout 600s)

Token (Ladereihenfolge):
  1. Umgebungsvariable PROXMOX_TOKEN  (Format: root@pam!claude=<secret>)
  2. Windows Credential Manager (kontext-automatisch):
       Netz 10.10.40.x  --> Kontext 'ibf'      --> proxmox-ibf/ibf
       andere Netze     --> Kontext 'personal' --> proxmox-personal/ibf
       Pfad im falschen Projektverzeichnis: Warnung auf stderr
  3. .env-Datei: proxmox_token=<TOKEN>

Token setzen:   python proxmox-query.py --set-token   (Kontext wird auto-erkannt)
Token loeschen (ibf):      python -c "import keyring; keyring.delete_password('proxmox-ibf', 'ibf')"
Token loeschen (personal): python -c "import keyring; keyring.delete_password('proxmox-personal', 'ibf')"

Beispiele:
  proxmox-query.py
  proxmox-query.py --action vms --status running
  proxmox-query.py --action tasks --limit 20
  proxmox-query.py --action storage
  proxmox-query.py --action snapshots --vmid 185
  proxmox-query.py --action exec --path /nodes/k5/status

  proxmox-query.py --action control --op start --vmid 185 --wait
  proxmox-query.py --action control --op shutdown --vmid 117
  proxmox-query.py --action control --op migrate --vmid 185 --target k5 --wait
  proxmox-query.py --action control --op snapshot --vmid 185 --snapname pre-update --wait
  proxmox-query.py --action control --op delsnapshot --vmid 185 --snapname pre-update
  proxmox-query.py --action control --op rollback --vmid 185 --snapname pre-update --wait
  proxmox-query.py --action control --op set --vmid 117 --config memory=2048
  proxmox-query.py --action control --op set --vmid 185 --config memory=8192 --config cores=4
  proxmox-query.py --action control --op resize --vmid 109 --disk rootfs --size +5G
  proxmox-query.py --action control --op unlock --vmid 185

  proxmox-query.py --action control --op create \\
    --hostname myserver --node k5 --memory 512 --disk-size 8 \\
    --ssh-key "ssh-ed25519 AAAA..." --start --wait
"""


# ----- token loading ---------------------------------------------------------

def load_token():
    token = os.environ.get("PROXMOX_TOKEN", "").strip()
    if token:
        return token

    token = _token_from_keyring()
    if token:
        return token

    token = _token_from_env_file()
    if token:
        return token

    ctx = _detect_context()
    service, _ = _CTX_CONFIG[ctx]
    sys.exit(
        f"[ERROR] Kein Proxmox-Token gefunden (Kontext: {ctx}, erwartet: {service}/ibf).\n\n"
        "Einen der folgenden Wege einrichten:\n"
        "  1. Umgebungsvariable:  $env:PROXMOX_TOKEN = 'root@pam!claude=<secret>'\n"
        "  2. Windows Credential Manager:\n"
        "       python proxmox-query.py --set-token\n"
        f"     speichert unter: {service}/ibf\n"
        "  3. .env-Datei: proxmox_token=root@pam!claude=<secret>\n\n"
        "Token anlegen in Proxmox: Datacenter > API Tokens"
    )


_IBF_NETWORKS = ("10.10.40.0/21",)  # IBF-Firmennetz, deckt 10.10.40.x .. 10.10.47.x
                                    # (DNS-Suffix int.ibf-solutions.com)

def _detect_context() -> str:
    """Return 'ibf' if any local interface is inside _IBF_NETWORKS, else 'personal'.
    Uses local interface enumeration -- no DNS/UDP calls (8.8.8.8 may be blocked).
    """
    try:
        import socket
        from ipaddress import ip_address, ip_network
        nets = [ip_network(n) for n in _IBF_NETWORKS]
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            try:
                if any(ip_address(ip) in n for n in nets):
                    return "ibf"
            except ValueError:
                continue
        return "personal"
    except Exception:
        return "personal"


def _check_path_context(ctx: str) -> None:
    """Warn if the script lives in a tree that doesn't match the network context."""
    script_path = str(Path(__file__).resolve())
    _, expected_fragment = _CTX_CONFIG.get(ctx, ("", ""))
    if expected_fragment and expected_fragment.lower() not in script_path.lower():
        other = "ibf" if ctx == "personal" else "personal"
        print(
            f"[WARN] Netzwerk-Kontext: '{ctx}', aber dieses Script liegt in einem "
            f"'{other}'-Pfad:\n"
            f"       {script_path}\n"
            f"       Bitte im richtigen Projektverzeichnis arbeiten.",
            file=sys.stderr,
        )


def _token_from_keyring():
    try:
        import keyring
        ctx = _detect_context()
        service, _ = _CTX_CONFIG[ctx]
        token = keyring.get_password(service, "ibf")
        return token.strip() if token else None
    except Exception:
        return None


def _token_from_env_file():
    candidates = [Path(__file__).resolve().parents[n] / ".env" for n in range(6)]
    candidates.append(Path(r"C:\Temp\claude\.env"))
    for env_path in candidates:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("proxmox_token="):
                return line.split("=", 1)[1].strip()
    return None


# ----- HTTP helper -----------------------------------------------------------

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_TOKEN = None  # set in main()


def px(path, params=None, method="GET", body=None):
    url = PROXMOX_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    encoded_body = urllib.parse.urlencode(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=encoded_body, method=method)
    req.add_header("Authorization", f"PVEAPIToken={_TOKEN}")
    req.add_header("Accept", "application/json")
    if encoded_body is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as r:
            txt = r.read().decode()
            if not txt:
                return None
            data = json.loads(txt)
            return data.get("data", data)
    except urllib.error.HTTPError as e:
        sys.exit(f"[HTTP {e.code}] {method} {path}\n{e.read().decode()[:400]}")
    except urllib.error.URLError as e:
        sys.exit(f"[FEHLER] Proxmox nicht erreichbar ({e.reason})\n  URL: {url}")
    except TimeoutError:
        sys.exit(f"[FEHLER] Timeout -- Proxmox nicht erreichbar\n  URL: {url}")


# ----- formatting helpers ----------------------------------------------------

def _fmt_bytes(b):
    if b is None:
        return "--"
    b = float(b)
    if b == 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def _fmt_pct(used, total):
    if not total:
        return "--"
    return f"{used / total * 100:.1f}%"


def _fmt_uptime(secs):
    if not secs:
        return "--"
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    return f"{secs // 86400}d {(secs % 86400) // 3600}h"


def _fmt_ts(ts):
    if not ts:
        return "--"
    return dt.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_dur(start, end):
    if not end or not start:
        return "running"
    return f"{int(end) - int(start)}s"


def _filter_fields(items, fields_str):
    if not fields_str:
        return items
    keys = [k.strip() for k in fields_str.split(",")]
    return [{k: item[k] for k in keys if k in item} for item in items]


# ----- actions ---------------------------------------------------------------

def do_status(args):
    cluster_status = px("/cluster/status")
    resources      = px("/cluster/resources")

    cluster_info = next((c for c in cluster_status if c.get("type") == "cluster"), {})
    status_nodes = {c["name"]: c for c in cluster_status if c.get("type") == "node"}
    resource_nodes = {r["node"]: r for r in resources if r.get("type") == "node"}

    # Count running VMs/LXC per node
    vm_counts = {}
    for r in resources:
        if r.get("type") in ("qemu", "lxc") and r.get("status") == "running":
            vm_counts[r.get("node", "")] = vm_counts.get(r.get("node", ""), 0) + 1

    if args.raw:
        out = {
            "cluster": cluster_info,
            "nodes": [
                {**status_nodes.get(n, {}), **resource_nodes.get(n, {}), "running_vms": vm_counts.get(n, 0)}
                for n in sorted(status_nodes)
            ],
        }
        print(json.dumps(out, indent=2))
        return

    quorate = "yes" if cluster_info.get("quorate") else "NO !"
    print(f"Cluster: {cluster_info.get('name', '?')}  "
          f"quorate: {quorate}  "
          f"version: {cluster_info.get('version', '?')}  "
          f"nodes: {cluster_info.get('nodes', '?')}")
    print()

    header = f"  {'Node':<10}  {'Status':<8}  {'CPU%':<7}  {'RAM':<28}  {'VMs':<5}  {'Uptime':<10}  IP"
    print(header)
    print(f"  {'-'*10}  {'-'*8}  {'-'*7}  {'-'*28}  {'-'*5}  {'-'*10}  {'-'*15}")

    for name in sorted(status_nodes):
        sn  = status_nodes[name]
        rn  = resource_nodes.get(name, {})
        online  = sn.get("online", 0)
        status  = "online" if online else "OFFLINE"
        cpu     = f"{rn.get('cpu', 0) * 100:.1f}%" if rn else "--"
        mem_u   = rn.get("mem", 0)
        mem_t   = rn.get("maxmem", 0)
        ram     = f"{_fmt_bytes(mem_u)} / {_fmt_bytes(mem_t)} ({_fmt_pct(mem_u, mem_t)})" if mem_t else "--"
        vms     = str(vm_counts.get(name, 0))
        uptime  = _fmt_uptime(rn.get("uptime"))
        ip      = sn.get("ip", "--")
        print(f"  {name:<10}  {status:<8}  {cpu:<7}  {ram:<28}  {vms:<5}  {uptime:<10}  {ip}")


def do_nodes(args):
    resources = px("/cluster/resources")
    cluster_status = px("/cluster/status")
    status_nodes = {c["name"]: c for c in cluster_status if c.get("type") == "node"}

    nodes_to_check = [args.node] if args.node else sorted(status_nodes)

    all_data = []
    for name in nodes_to_check:
        sn = status_nodes.get(name, {})
        if not sn.get("online", 0):
            all_data.append((name, None))
            continue
        try:
            nd = px(f"/nodes/{name}/status")
            all_data.append((name, nd))
        except SystemExit:
            all_data.append((name, None))

    if args.raw:
        print(json.dumps({name: data for name, data in all_data}, indent=2))
        return

    for name, nd in all_data:
        if nd is None:
            print(f"Node: {name}  [OFFLINE]\n")
            continue
        mem  = nd.get("memory", {})
        swap = nd.get("swap", {})
        root = nd.get("rootfs", {})
        cpu  = nd.get("cpu", 0) * 100
        cpus = nd.get("cpuinfo", {}).get("cpus", "?")
        print(f"Node: {name}")
        print(f"  CPU:     {cpus} vCPUs  usage: {cpu:.1f}%  "
              f"model: {nd.get('cpuinfo', {}).get('model', '--')[:50]}")
        print(f"  RAM:     {_fmt_bytes(mem.get('used'))} / {_fmt_bytes(mem.get('total'))}  "
              f"({_fmt_pct(mem.get('used', 0), mem.get('total', 1))})")
        if swap.get("total"):
            print(f"  Swap:    {_fmt_bytes(swap.get('used'))} / {_fmt_bytes(swap.get('total'))}  "
                  f"({_fmt_pct(swap.get('used', 0), swap.get('total', 1))})")
        if root.get("total"):
            print(f"  RootFS:  {_fmt_bytes(root.get('used'))} / {_fmt_bytes(root.get('total'))}  "
                  f"({_fmt_pct(root.get('used', 0), root.get('total', 1))})")
        print(f"  Uptime:  {_fmt_uptime(nd.get('uptime'))}")
        print(f"  Kernel:  {nd.get('kversion', '--')}")
        print()


def do_vms(args):
    resources = px("/cluster/resources")
    vms = [r for r in resources if r.get("type") in ("qemu", "lxc")]

    if args.type and args.type != "all":
        type_map = {"vm": "qemu", "lxc": "lxc"}
        vms = [v for v in vms if v.get("type") == type_map.get(args.type, args.type)]
    if args.node:
        vms = [v for v in vms if v.get("node") == args.node]
    if args.status:
        vms = [v for v in vms if v.get("status") == args.status]
    if args.vmid:
        vms = [v for v in vms if str(v.get("vmid")) == str(args.vmid)]

    vms.sort(key=lambda x: (x.get("node", ""), x.get("vmid", 0)))

    if args.raw:
        out = _filter_fields(vms, args.fields)
        print(json.dumps(out, indent=2))
        return

    running = sum(1 for v in vms if v.get("status") == "running")

    col_hdr = (f"  {'VMID':<6}  {'Name':<32}  {'Type':<4}  {'Status':<9}  "
               f"{'CPU%':<6}  {'RAM':<10}  {'Disk':<10}  Uptime")
    col_sep = (f"  {'-'*6}  {'-'*32}  {'-'*4}  {'-'*9}  "
               f"{'-'*6}  {'-'*10}  {'-'*10}  {'-'*10}")

    node_list = {n["node"]: n for n in px("/nodes") if "node" in n}

    current_node = None
    for v in vms:
        node = v.get("node") or "?"
        if node != current_node:
            current_node = node
            node_vms = [x for x in vms if x.get("node") == node]
            total_ram  = sum(x.get("maxmem", 0) for x in node_vms)
            total_disk = sum(x.get("maxdisk", 0) for x in node_vms)
            n = node_list.get(node, {})
            node_maxmem = n.get("maxmem", 0)
            pct = f" ({total_ram/node_maxmem*100:.0f}% alloc)" if node_maxmem else ""
            print(f"\n[ {node} ]  RAM {_fmt_bytes(total_ram)}{pct}  |  Disk {_fmt_bytes(total_disk)}")
            print(col_hdr)
            print(col_sep)

        vmid   = str(v.get("vmid", "?"))
        name   = (v.get("name") or "")[:32]
        vtype  = "lxc" if v.get("type") == "lxc" else "vm"
        status = v.get("status", "?")
        is_run = status == "running"
        cpu    = f"{v.get('cpu', 0) * 100:.1f}%" if is_run else "--"
        ram    = _fmt_bytes(v.get("maxmem", 0)) if v.get("maxmem") else "--"
        disk   = _fmt_bytes(v.get("maxdisk", 0)) if v.get("maxdisk") else "--"
        uptime = _fmt_uptime(v.get("uptime")) if is_run else "--"
        print(f"  {vmid:<6}  {name:<32}  {vtype:<4}  {status:<9}  "
              f"{cpu:<6}  {ram:<10}  {disk:<10}  {uptime}")

    print(f"\n  {len(vms)} total  |  {running} running")


def do_tasks(args):
    nodes_to_check = [args.node] if args.node else KNOWN_NODES
    limit = args.limit
    all_tasks = []

    for node in nodes_to_check:
        try:
            tasks = px(f"/nodes/{node}/tasks", {"limit": limit})
            if isinstance(tasks, list):
                for t in tasks:
                    t.setdefault("_node", node)
                all_tasks.extend(tasks)
        except SystemExit:
            pass

    all_tasks.sort(key=lambda x: x.get("starttime", 0), reverse=True)
    all_tasks = all_tasks[:limit]

    if args.raw:
        out = _filter_fields(all_tasks, args.fields)
        print(json.dumps(out, indent=2))
        return

    scope = args.node if args.node else "cluster-wide"
    print(f"Tasks -- {scope}  ({len(all_tasks)} shown)\n")
    print(f"  {'Node':<8}  {'Started':<20}  {'Dur.':<8}  {'Status':<10}  "
          f"{'User':<12}  {'Type':<12}  {'ID':<6}")
    print(f"  {'-'*8}  {'-'*20}  {'-'*8}  {'-'*10}  "
          f"{'-'*12}  {'-'*12}  {'-'*6}")

    for t in all_tasks:
        node    = (t.get("node") or t.get("_node") or "?")[:8]
        started = _fmt_ts(t.get("starttime"))
        dur     = _fmt_dur(t.get("starttime"), t.get("endtime"))
        status  = (t.get("status") or ("running" if not t.get("endtime") else "?"))[:10]
        user    = (t.get("user") or "?")[:12]
        ttype   = (t.get("type") or "?")[:12]
        tid     = str(t.get("id") or "")[:6]
        print(f"  {node:<8}  {started:<20}  {dur:<8}  {status:<10}  "
              f"{user:<12}  {ttype:<12}  {tid:<6}")


def do_storage(args):
    resources = px("/cluster/resources")
    storages = [r for r in resources if r.get("type") == "storage"]

    if args.node:
        storages = [s for s in storages if s.get("node") == args.node]

    storages.sort(key=lambda x: (x.get("node", ""), x.get("storage", "")))

    if args.raw:
        out = _filter_fields(storages, args.fields)
        print(json.dumps(out, indent=2))
        return

    print(f"  {'Storage':<22}  {'Node':<8}  {'Type':<10}  "
          f"{'Total':<12}  {'Used':<12}  {'Free':<12}  %Used")
    print(f"  {'-'*22}  {'-'*8}  {'-'*10}  "
          f"{'-'*12}  {'-'*12}  {'-'*12}  {'-'*6}")

    for s in storages:
        name   = (s.get("storage") or "?")[:22]
        node   = (s.get("node") or "?")[:8]
        stype  = (s.get("plugintype") or "?")[:10]
        total  = s.get("maxdisk", 0)
        used   = s.get("disk", 0)
        free   = total - used if total else 0
        pct    = _fmt_pct(used, total)
        avail  = s.get("status", "?")
        mark   = "  !" if total and (used / total) > 0.85 else ""
        print(f"  {name:<22}  {node:<8}  {stype:<10}  "
              f"{_fmt_bytes(total):<12}  {_fmt_bytes(used):<12}  {_fmt_bytes(free):<12}  {pct}{mark}")


def do_snapshots(args):
    resources = px("/cluster/resources")
    vms = [r for r in resources if r.get("type") in ("qemu", "lxc")]

    if args.vmid:
        vms = [v for v in vms if str(v.get("vmid")) == str(args.vmid)]
    if args.node:
        vms = [v for v in vms if v.get("node") == args.node]

    all_snaps = []
    for v in vms:
        vmid  = v.get("vmid")
        node  = v.get("node")
        vtype = "qemu" if v.get("type") == "qemu" else "lxc"
        try:
            snaps = px(f"/nodes/{node}/{vtype}/{vmid}/snapshot")
            if not isinstance(snaps, list):
                continue
            for s in snaps:
                if s.get("name") == "current":
                    continue
                s["_vmid"]   = vmid
                s["_vmname"] = v.get("name", "?")
                s["_node"]   = node
                all_snaps.append(s)
        except SystemExit:
            pass

    all_snaps.sort(key=lambda x: x.get("snaptime") or 0, reverse=True)

    if args.raw:
        out = _filter_fields(all_snaps, args.fields)
        print(json.dumps(out, indent=2))
        return

    if not all_snaps:
        print("Keine Snapshots gefunden.")
        return

    print(f"  {'VMID':<6}  {'VM Name':<28}  {'Node':<8}  "
          f"{'Snapshot':<22}  {'Created':<20}  Description")
    print(f"  {'-'*6}  {'-'*28}  {'-'*8}  "
          f"{'-'*22}  {'-'*20}  {'-'*30}")

    for s in all_snaps:
        vmid    = str(s.get("_vmid", "?"))
        vname   = (s.get("_vmname") or "")[:28]
        node    = (s.get("_node") or "?")[:8]
        sname   = (s.get("name") or "?")[:22]
        created = _fmt_ts(s.get("snaptime"))
        desc    = (s.get("description") or "")[:40]
        print(f"  {vmid:<6}  {vname:<28}  {node:<8}  "
              f"{sname:<22}  {created:<20}  {desc}")

    print(f"\n  {len(all_snaps)} Snapshots")


def do_exec(args):
    if not args.path:
        sys.exit("[ERROR] --path erforderlich für action=exec\n"
                 "Beispiel: --path /nodes/k5/status")
    data = px(args.path)
    print(json.dumps(data, indent=2))


# ----- SSH helpers -----------------------------------------------------------

_SSH_KEY = os.path.expandvars(r"%USERPROFILE%\.ssh\proxmox_claude")

_NODE_IPS = {
    "k1-low": "192.168.10.1",
    "k2":     "192.168.10.2",
    "k5":     "192.168.10.5",
}

_SSH_OPS = {
    "apt-upgrade": (
        "DEBIAN_FRONTEND=noninteractive apt-get update -q 2>&1 "
        "&& DEBIAN_FRONTEND=noninteractive apt-get upgrade -y 2>&1"
    ),
    "apt-dist-upgrade": (
        "DEBIAN_FRONTEND=noninteractive apt-get update -q 2>&1 "
        "&& DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y 2>&1"
    ),
    "reboot": "reboot",
    "uptime": "uptime && uname -r",
}


def _ssh_connect(ip, key=None):
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        ip, port=22, username="root",
        key_filename=key or _SSH_KEY,
        look_for_keys=False, allow_agent=False,
        timeout=15, banner_timeout=15,
    )
    return client


def _ssh_run(client, cmd, timeout=300):
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    return out, err


def do_ssh(args):
    if not args.op and not args.cmd:
        sys.exit(
            "[ERROR] --op <op> oder --cmd <befehl> erforderlich für action=ssh\n"
            "Bekannte Ops: " + ", ".join(_SSH_OPS)
        )

    nodes = [args.node] if args.node else list(_NODE_IPS.keys())
    cmd = args.cmd if args.cmd else _SSH_OPS.get(args.op)
    if cmd is None:
        sys.exit(f"[ERROR] Unbekannte SSH-Op: {args.op!r}\nBekannte: {', '.join(_SSH_OPS)}")

    # preflight: alle Verbindungen zuerst testen bevor irgendetwas läuft
    print(f"  Preflight -- verbinde zu {len(nodes)} Node(s) ...")
    clients = {}
    for name in nodes:
        ip = _NODE_IPS.get(name)
        if not ip:
            print(f"  [{name}] FEHLER: unbekannter Node")
            continue
        try:
            client = _ssh_connect(ip)
            clients[name] = client
            print(f"  [{name}] verbunden ({ip})")
        except Exception as e:
            print(f"  [{name}] FEHLER: {e}")

    if not clients:
        sys.exit("[ERROR] Keine Verbindung zu keinem Node möglich.")

    failed = [n for n in nodes if n not in clients]
    if failed:
        print(f"\n  [WARN] Folgende Nodes nicht erreichbar: {', '.join(failed)}")
        answer = input("  Trotzdem fortfahren? [j/N] ").strip().lower()
        if answer != "j":
            for c in clients.values():
                c.close()
            sys.exit("Abgebrochen.")

    print(f"\n  Führe aus: {cmd[:80]}{'...' if len(cmd) > 80 else ''}\n")

    for name, client in clients.items():
        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"{'='*60}")
        try:
            out, err = _ssh_run(client, cmd, timeout=args.timeout or 300)
            print(out)
            if err:
                print("[STDERR]", err[:500])
        except Exception as e:
            print(f"  [FEHLER] {e}")
        finally:
            client.close()


# ----- control helpers -------------------------------------------------------

def _resolve_vm(vmid):
    """Return (node, apitype, name, status) for a VMID or exit."""
    resources = px("/cluster/resources")
    vm = next((r for r in resources if str(r.get("vmid")) == str(vmid)), None)
    if not vm:
        sys.exit(f"[ERROR] VMID {vmid} nicht im Cluster gefunden")
    return vm["node"], ("qemu" if vm["type"] == "qemu" else "lxc"), vm.get("name", "?"), vm.get("status", "?")


def _wait_task(node, upid, timeout=600):
    import time
    encoded = urllib.parse.quote(upid, safe="")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = px(f"/nodes/{node}/tasks/{encoded}/status")
            if isinstance(s, dict) and s.get("status") == "stopped":
                return s.get("exitstatus", "OK")
        except SystemExit:
            break
        time.sleep(3)
    return "timeout"


def _handle_task(node, upid, args):
    if not isinstance(upid, str):
        print("  [OK]")
        return
    print(f"  task: {upid}")
    if args.wait:
        print("  warte...", end="", flush=True)
        result = _wait_task(node, upid)
        print(f" {result}")
    else:
        print("  (--wait zum Warten auf Abschluss)")


# ----- action: control -------------------------------------------------------

def do_control(args):
    if not args.op:
        sys.exit(
            "[ERROR] --op erforderlich für action=control\n"
            "Verfügbar: create move start stop shutdown reboot suspend resume unlock "
            "migrate snapshot delsnapshot rollback set resize"
        )

    op = args.op.lower()

    if op == "create":
        _do_create(args)
        return

    if op == "move":
        _do_move(args)
        return

    if not args.vmid:
        sys.exit("[ERROR] --vmid erforderlich für action=control")

    vmid = str(args.vmid)
    node, vtype, name, vm_status = _resolve_vm(vmid)
    base = f"/nodes/{node}/{vtype}/{vmid}"

    print(f"  {name}  (VMID {vmid}, {vtype}, {node}, {vm_status})")

    if op in ("start", "stop", "shutdown", "reboot", "suspend", "resume"):
        upid = px(f"{base}/status/{op}", method="POST", body={})
        _handle_task(node, upid, args)

    elif op == "unlock":
        px(f"{base}/config", method="PUT", body={"delete": "lock"})
        print("  [OK] lock entfernt")

    elif op == "migrate":
        if not args.target:
            sys.exit("[ERROR] --target <node> erforderlich für migrate")
        body = {"target": args.target, "with-local-disks": 1}
        upid = px(f"{base}/migrate", method="POST", body=body)
        _handle_task(node, upid, args)

    elif op == "snapshot":
        if not args.snapname:
            sys.exit("[ERROR] --snapname erforderlich für snapshot")
        body = {"snapname": args.snapname}
        if args.description:
            body["description"] = args.description
        upid = px(f"{base}/snapshot", method="POST", body=body)
        _handle_task(node, upid, args)

    elif op == "delsnapshot":
        if not args.snapname or args.snapname == "all":
            snaps = px(f"{base}/snapshot")
            names = [s["name"] for s in snaps if isinstance(snaps, list) and s.get("name") != "current"]
            if not names:
                print("Keine Snapshots vorhanden.")
            for name in names:
                print(f"  loesche Snapshot '{name}' ...")
                upid = px(f"{base}/snapshot/{name}", method="DELETE", body={})
                _handle_task(node, upid, args)
        else:
            upid = px(f"{base}/snapshot/{args.snapname}", method="DELETE", body={})
            _handle_task(node, upid, args)

    elif op == "rollback":
        if not args.snapname:
            sys.exit("[ERROR] --snapname erforderlich für rollback")
        upid = px(f"{base}/snapshot/{args.snapname}/rollback", method="POST", body={})
        _handle_task(node, upid, args)

    elif op == "set":
        if not args.config:
            sys.exit("[ERROR] --config key=value erforderlich für set (wiederholbar)\n"
                     "Beispiel: --config memory=2048 --config cores=2")
        body = {}
        for pair in args.config:
            if "=" not in pair:
                sys.exit(f"[ERROR] Ungültiges Format {pair!r} -- erwartet key=value")
            k, v = pair.split("=", 1)
            body[k.strip()] = v.strip()
        px(f"{base}/config", method="PUT", body=body)
        print(f"  [OK] {body}")

    elif op == "resize":
        if not args.disk or not args.size:
            sys.exit("[ERROR] --disk und --size erforderlich\n"
                     "Beispiel: --disk scsi0 --size +10G   oder   --disk rootfs --size +5G")
        px(f"{base}/resize", method="PUT", body={"disk": args.disk, "size": args.size})
        print(f"  [OK] {args.disk} {args.size}")

    else:
        sys.exit(
            f"[ERROR] Unbekannte Operation: {op!r}\n"
            "Verfügbar: create start stop shutdown reboot suspend resume unlock "
            "migrate snapshot delsnapshot rollback set resize"
        )


def _do_move(args):
    if not args.target:
        sys.exit("[ERROR] --target <node> erforderlich für move")

    resources = px("/cluster/resources")
    vms = [r for r in resources if r.get("type") in ("qemu", "lxc")]

    # filter by source node or explicit id list
    if args.ids:
        id_set = {str(i.strip()) for i in args.ids.split(",")}
        vms = [v for v in vms if str(v.get("vmid")) in id_set]
    elif args.node:
        vms = [v for v in vms if v.get("node") == args.node]
    else:
        sys.exit("[ERROR] --node <src> oder --ids <id,...> erforderlich für move")

    # apply exclude list
    if args.exclude:
        ex_set = {str(i.strip()) for i in args.exclude.split(",")}
        vms = [v for v in vms if str(v.get("vmid")) not in ex_set]

    # skip VMs already on target
    vms = [v for v in vms if v.get("node") != args.target]

    if not vms:
        print("Keine VMs zum Migrieren gefunden.")
        return

    vms.sort(key=lambda v: v.get("vmid", 0))
    total = len(vms)
    print(f"  Migriere {total} VM(s) nach {args.target}\n")

    for i, v in enumerate(vms, 1):
        vmid   = str(v.get("vmid"))
        name   = v.get("name", "?")
        vtype  = v.get("type", "qemu")
        src    = v.get("node", "?")
        status = v.get("status", "?")
        print(f"  [{i}/{total}] {name} (VMID {vmid}, {vtype}, {src}, {status})")
        base = f"/nodes/{src}/{vtype}/{vmid}"
        try:
            if vtype == "qemu":
                body = {"target": args.target, "with-local-disks": 1}
            else:
                # LXC: with-local-disks ist kein gueltiger Parameter
                # restart=1 stoppt laufende CTs, migriert, startet auf Ziel
                body = {"target": args.target}
                if v.get("status") == "running":
                    body["restart"] = 1
            upid = px(f"{base}/migrate", method="POST", body=body)
            _handle_task(src, upid, args)
        except SystemExit as e:
            print(f"  [FEHLER] {e}")
        print()


def _do_create(args):
    node     = args.node or "k5"
    storage  = args.storage or "tank"
    hostname = args.hostname
    if not hostname:
        sys.exit("[ERROR] --hostname erforderlich für create")

    # next free VMID unless specified
    vmid = str(args.vmid) if args.vmid else str(px("/cluster/nextid"))

    template  = args.template or "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"
    disk_size = str(args.disk_size or 8)
    memory    = int(args.memory or 512)
    cores     = int(args.cores or 1)
    bridge    = args.bridge or "vmbr0"
    ip_cfg    = args.ip or "dhcp"
    ip_str    = f"ip={ip_cfg}" if ip_cfg == "dhcp" else f"ip={ip_cfg}"

    body = {
        "vmid":        vmid,
        "ostemplate":  template,
        "hostname":    hostname,
        "rootfs":      f"{storage}:{disk_size}",
        "memory":      memory,
        "swap":        512,
        "cores":       cores,
        "net0":        f"name=eth0,bridge={bridge},{ip_str}",
        "unprivileged": 1,
        "onboot":      1,
    }
    if args.ssh_key:
        body["ssh-public-keys"] = args.ssh_key
    if args.start:
        body["start"] = 1

    print(f"  Erstelle LXC {vmid} ({hostname}) auf {node}")
    print(f"  Template:  {template}")
    print(f"  Rootfs:    {storage}:{disk_size} GB  RAM: {memory} MB  Cores: {cores}")
    print(f"  Netzwerk:  bridge={bridge}  {ip_str}")
    if args.ssh_key:
        print(f"  SSH-Key:   {args.ssh_key[:40]}...")
    print()

    upid = px(f"/nodes/{node}/lxc", method="POST", body=body)
    _handle_task(node, upid, args)

    if args.wait:
        print(f"\n  VMID {vmid} bereit. Status prüfen:")
        print(f"    proxmox-query.py --action vms --vmid {vmid}")


# ----- main ------------------------------------------------------------------

def main():
    if "--help-ai" in sys.argv:
        print(HELP_AI)
        return
    if "--help" in sys.argv or "-h" in sys.argv:
        print(HELP_TXT)
        return

    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--action", default="status",
                   choices=["status", "nodes", "vms", "tasks", "storage", "snapshots", "exec", "control", "ssh"])
    # read filters
    p.add_argument("--node")
    p.add_argument("--vmid")
    p.add_argument("--type",   choices=["vm", "lxc", "all"], default="all")
    p.add_argument("--status", choices=["running", "stopped"])
    p.add_argument("--limit",  type=int, default=50)
    p.add_argument("--raw", "--json", action="store_true", dest="raw")
    p.add_argument("--fields")
    p.add_argument("--path")
    # control
    p.add_argument("--op")
    p.add_argument("--target")
    p.add_argument("--ids",     help="Komma-getrennte VMIDs für move")
    p.add_argument("--exclude", help="Komma-getrennte VMIDs ausschliessen")
    p.add_argument("--snapname")
    p.add_argument("--description")
    p.add_argument("--disk")
    p.add_argument("--size")
    p.add_argument("--config", action="append", metavar="KEY=VALUE")
    p.add_argument("--wait", action="store_true")
    # ssh-specific
    p.add_argument("--cmd",     help="SSH: Shell-Befehl der auf dem Node ausgeführt wird")
    p.add_argument("--timeout", type=int, default=300, help="SSH: Timeout in Sekunden (default 300)")
    # create-specific
    p.add_argument("--hostname")
    p.add_argument("--template")
    p.add_argument("--storage")
    p.add_argument("--disk-size", type=int, dest="disk_size")
    p.add_argument("--memory",    type=int)
    p.add_argument("--cores",     type=int)
    p.add_argument("--bridge")
    p.add_argument("--ip")
    p.add_argument("--ssh-key",   dest="ssh_key")
    p.add_argument("--start",     action="store_true")
    # token
    p.add_argument("--set-token", nargs="?", const="__prompt__", metavar="TOKEN",
                   help="Token im Windows Credential Manager speichern")
    args = p.parse_args()

    if args.set_token is not None:
        import getpass
        ctx = _detect_context()
        service, _ = _CTX_CONFIG[ctx]
        token_value = args.set_token
        if token_value == "__prompt__":
            token_value = getpass.getpass(
                f"Proxmox Token [{ctx} -> {service}/ibf] (root@pam!<name>=<secret>): "
            ).strip()
            if not token_value:
                sys.exit("[ERROR] Kein Token eingegeben.")
        try:
            import keyring
            keyring.set_password(service, "ibf", token_value)
            print(f"[OK] Token gespeichert: Windows Credential Manager [{service}/ibf] (Kontext: {ctx})")
        except Exception as e:
            sys.exit(f"[ERROR] keyring nicht verfügbar: {e}\n"
                     "Installieren mit: pip install keyring")
        return

    dispatch = {
        "status":    do_status,
        "nodes":     do_nodes,
        "vms":       do_vms,
        "tasks":     do_tasks,
        "storage":   do_storage,
        "snapshots": do_snapshots,
        "exec":      do_exec,
        "control":   do_control,
        "ssh":       do_ssh,
    }

    # ssh action braucht kein API-Token
    if args.action != "ssh":
        global _TOKEN
        _TOKEN = load_token()
        _check_path_context(_detect_context())

    dispatch[args.action](args)


if __name__ == "__main__":
    main()
