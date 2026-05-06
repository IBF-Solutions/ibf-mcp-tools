"""Infra-Sektion: Proxmox-Cluster, Storage, Failed Tasks, Soll/Ist-Vergleich
gegen das Inventar."""

from __future__ import annotations

import dataclasses
import datetime as dt
import time

from .. import inventory, proxmox_api, trend


@dataclasses.dataclass
class InfraRow:
    name: str
    today: float
    yesterday: float
    week_avg: float
    status: str
    delta_yesterday_pct: float | None
    delta_week_pct: float | None
    note: str = ""


@dataclasses.dataclass
class StorageRow:
    name: str
    node: str
    pct_used: float
    used_gb: float
    total_gb: float
    status: str


@dataclasses.dataclass
class InfraSection:
    rows: list[InfraRow]
    storage: list[StorageRow]
    cluster_health: str             # OK / DEGRADED / SPLIT
    cluster_note: str
    must_run_missing: list[str]     # Namen der VMs die laufen sollten, aber nicht
    surprise_running: list[str]     # die laufen, aber laut Inventar nicht "production"
    stale_snapshots: list[str]      # snapshots > 7 Tage alt


def _cluster_health() -> tuple[str, str]:
    try:
        cs = proxmox_api.cluster_status()
    except RuntimeError as e:
        return "ERROR", f"API nicht erreichbar: {e}"
    cluster_info = next((c for c in cs if c.get("type") == "cluster"), {})
    quorum = cluster_info.get("quorate")
    nodes_in = [c for c in cs if c.get("type") == "node"]
    online = [n for n in nodes_in if n.get("online")]
    note = (f"{len(online)}/{len(nodes_in)} Nodes online, "
            f"quorate={quorum}")
    if quorum == 1 and len(online) == len(nodes_in):
        return "OK", note
    if quorum == 1:
        return "DEGRADED", note
    return "SPLIT", note


def _storage(now_dt: dt.datetime) -> list[StorageRow]:
    rows: list[StorageRow] = []
    for s in proxmox_api.storage_resources():
        total = s.get("maxdisk") or 0
        used = s.get("disk") or 0
        if not total:
            continue
        pct = used / total * 100.0
        if pct >= 90:
            status = "ALERT"
        elif pct >= 80:
            status = "WARN"
        else:
            status = "OK"
        rows.append(StorageRow(
            name=s.get("storage", "?"),
            node=s.get("node", "?"),
            pct_used=round(pct, 1),
            used_gb=round(used / 1024**3, 1),
            total_gb=round(total / 1024**3, 1),
            status=status,
        ))
    rows.sort(key=lambda r: -r.pct_used)
    return rows


def _soll_ist(entries: list[inventory.Entry], live_vms: list[dict]) -> tuple[list[str], list[str]]:
    by_vmid: dict[int, dict] = {v.get("vmid"): v for v in live_vms if v.get("vmid")}
    must_missing: list[str] = []
    surprise_running: list[str] = []

    inv_vmids: set[int] = set()
    for e in entries:
        if e.source != "proxmox" or e.vmid is None:
            continue
        inv_vmids.add(e.vmid)
        live = by_vmid.get(e.vmid)
        live_status = (live or {}).get("status", "missing")
        if e.must_run and live_status != "running":
            must_missing.append(f"vmid={e.vmid} {e.name} ({live_status})")

    # VMs die laufen, im Inventar aber nicht als production gelistet
    for vmid, live in by_vmid.items():
        if live.get("status") != "running":
            continue
        if vmid not in inv_vmids:
            surprise_running.append(f"vmid={vmid} {live.get('name','?')} (nicht im Inventar)")
    return must_missing, surprise_running


def _stale_snapshots(live_vms: list[dict], days: int = 7) -> list[str]:
    cutoff = time.time() - days * 86400
    out: list[str] = []
    for v in live_vms:
        vmid = v.get("vmid")
        node = v.get("node")
        if not vmid or not node:
            continue
        snaps = proxmox_api.snapshots(vmid, node, v.get("type", "qemu"))
        for s in snaps:
            if s.get("name") == "current":
                continue
            ts = s.get("snaptime") or 0
            if ts and ts < cutoff:
                age_d = int((time.time() - ts) / 86400)
                out.append(f"vmid={vmid} '{s.get('name')}' age={age_d}d")
    return out


def _failed_tasks_count(since_dt: dt.datetime, until_dt: dt.datetime) -> int:
    since_u = int(since_dt.timestamp())
    until_u = int(until_dt.timestamp())
    tasks = proxmox_api.all_failed_tasks_since(since_u)
    return sum(1 for t in tasks if (t.get("starttime") or 0) <= until_u)


def collect() -> InfraSection:
    now_dt = dt.datetime.now()
    health, note = _cluster_health()

    s, u = trend.range_today(now_dt)
    today_failed = _failed_tasks_count(s, u)
    s, u = trend.range_yesterday_until_now_time(now_dt)
    yest_failed = _failed_tasks_count(s, u)
    s, u = trend.range_last_7d(now_dt)
    week_total = _failed_tasks_count(s, u)
    week_avg = week_total / 7.0

    failed_row = InfraRow(
        name="failed_tasks",
        today=today_failed,
        yesterday=yest_failed,
        week_avg=round(week_avg, 1),
        status=trend.status_for(today_failed, warn=1, alert=10),
        delta_yesterday_pct=trend.delta_pct(today_failed, yest_failed),
        delta_week_pct=trend.delta_pct(today_failed, week_avg),
        note="Proxmox-Tasks mit errors=1",
    )

    entries = inventory.load()
    live_vms = proxmox_api.vms()
    must_missing, surprise_running = _soll_ist(entries, live_vms)
    stale = _stale_snapshots(live_vms)

    rows = [failed_row]

    return InfraSection(
        rows=rows,
        storage=_storage(now_dt),
        cluster_health=health,
        cluster_note=note,
        must_run_missing=must_missing,
        surprise_running=surprise_running,
        stale_snapshots=stale,
    )


def render_text(sec: InfraSection) -> str:
    out = ["=== INFRA ==="]
    out.append(f"  Cluster: {sec.cluster_health}  ({sec.cluster_note})")
    out.append("")
    out.append(f"  {'Metric':30s}  {'Today':>10s}  {'Yest.':>10s}  {'7d-avg':>10s}  Status")
    for r in sec.rows:
        out.append(f"  {r.name:30s}  {r.today:>10.0f}  {r.yesterday:>10.0f}  "
                   f"{r.week_avg:>10.1f}  {r.status}")
    out.append("")
    out.append("  Storage (sortiert):")
    out.append(f"    {'Pool':25s}  {'Node':10s}  Used      Status")
    for s in sec.storage:
        out.append(f"    {s.name:25s}  {s.node:10s}  "
                   f"{s.pct_used:>5.1f}% ({s.used_gb:.0f}/{s.total_gb:.0f} GB)  {s.status}")
    if sec.must_run_missing:
        out.append("")
        out.append("  ⚠ Production-VMs nicht laufend:")
        for m in sec.must_run_missing:
            out.append(f"    - {m}")
    if sec.surprise_running:
        out.append("")
        out.append("  ℹ Laufend, aber nicht im Inventar als production:")
        for s in sec.surprise_running:
            out.append(f"    - {s}")
    if sec.stale_snapshots:
        out.append("")
        out.append(f"  ℹ Stale Snapshots (>7d, {len(sec.stale_snapshots)}):")
        for s in sec.stale_snapshots[:5]:
            out.append(f"    - {s}")
        if len(sec.stale_snapshots) > 5:
            out.append(f"    (+{len(sec.stale_snapshots)-5} weitere)")
    return "\n".join(out)


if __name__ == "__main__":
    print(render_text(collect()))
