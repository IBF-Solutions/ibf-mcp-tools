"""Backups-Sektion: vzdump-Tasks der letzten 24h, abgeglichen mit Inventar.

Logik:
- Liste aller vzdump-Tasks der letzten 24h (Proxmox-API).
- Pro must-run-VM aus dem Inventar prüfen, ob ein erfolgreicher Backup
  in den letzten 24h vorliegt.
- VMs ohne Backup -> WARN/ALERT je nach Anzahl.
- VM 101 (Frigate) ist explizit von der Soll-Backup-Liste ausgeschlossen
  (siehe proxmox/CLAUDE.md: „intentionally has no backup").
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re

from .. import inventory, proxmox_api, trend


# VMs, für die KEIN Backup erwartet wird (siehe proxmox/CLAUDE.md)
NO_BACKUP_EXPECTED: set[int] = {101}  # Frigate


_BACKUP_RX = re.compile(r"vzdump.*?(\d{2,5})")


@dataclasses.dataclass
class BackupSection:
    backed_up_24h: list[int]                  # vmids mit erfolgreichem Backup
    failed_24h: list[tuple[int, str]]         # (vmid, status_text)
    missing: list[tuple[int, str]]            # must-run-VMs ohne Backup-Lauf
    summary: str                              # Kurztext für Status-Zeile


def _extract_vmid_from_task(task: dict) -> int | None:
    s = task.get("id") or task.get("upid") or ""
    m = _BACKUP_RX.search(s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    # alternativer Spot: in 'id' steht direkt die VMID bei manchen Versionen
    raw = str(task.get("id", ""))
    if raw.isdigit():
        return int(raw)
    return None


def collect() -> BackupSection:
    cutoff_unix = int((dt.datetime.now() - dt.timedelta(hours=24)).timestamp())
    # Bewusst KEIN try/except hier -- bei Proxmox-API-Fehler (401, Timeout etc.)
    # soll der RuntimeError zum Collector-Runner propagieren, der ihn als
    # `(None, err_msg)` an den Renderer durchreicht. Sonst würden wir
    # fälschlich "0 Backups gesehen = alle VMs ohne Backup" alarmieren,
    # obwohl wir in Wahrheit nichts wissen.
    tasks = proxmox_api.backup_tasks_since(cutoff_unix)

    backed_up: dict[int, str] = {}
    failed: list[tuple[int, str]] = []
    for t in tasks:
        vmid = _extract_vmid_from_task(t)
        if vmid is None:
            continue
        status = t.get("status") or t.get("exitstatus") or ""
        if status == "OK":
            backed_up[vmid] = status
        else:
            failed.append((vmid, status or "running/unknown"))

    entries = inventory.load()
    missing: list[tuple[int, str]] = []
    for e in entries:
        if e.source != "proxmox" or not e.must_run or e.vmid is None:
            continue
        if e.vmid in NO_BACKUP_EXPECTED:
            continue
        if e.vmid not in backed_up:
            missing.append((e.vmid, e.name))

    if not entries:
        summary = "kein Inventar"
    elif missing or failed:
        summary = (f"{len(backed_up)} OK, {len(missing)} ohne Backup, "
                   f"{len(failed)} fehlgeschlagen")
    else:
        summary = f"{len(backed_up)} VMs gesichert, alle Soll-VMs abgedeckt"

    return BackupSection(
        backed_up_24h=sorted(backed_up.keys()),
        failed_24h=failed,
        missing=missing,
        summary=summary,
    )


def render_text(sec: BackupSection) -> str:
    out = ["=== BACKUPS (letzte 24h) ==="]
    out.append(f"  {sec.summary}")
    if sec.failed_24h:
        out.append("")
        out.append("  ⚠ Fehlgeschlagen:")
        for vmid, st in sec.failed_24h:
            out.append(f"    - vmid={vmid}  status={st}")
    if sec.missing:
        out.append("")
        out.append("  ⚠ Production-VMs OHNE Backup-Lauf:")
        for vmid, name in sec.missing:
            out.append(f"    - vmid={vmid}  {name}")
    return "\n".join(out)


if __name__ == "__main__":
    print(render_text(collect()))
