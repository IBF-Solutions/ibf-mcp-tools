"""Inventar-Loader fürs Dashboard.

Liest `proxmox/inventory.yml` und liefert getypte Einträge mit Helfern für
die Soll-laufen-Logik:

    role=server UND label=production  ->  must_run=True
    role=client                       ->  is_client=True (informativ)
    label in {on-demand, debug}       ->  kein Soll-Check
    role=template                     ->  kein Soll-Check

Hetzner-Cloud-Server stehen unter `hetzner:` in derselben YAML; werden
beim ersten Dashboard-Run aus der Live-API populiert (TODO: separat).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Iterable

try:
    import yaml
except ImportError as e:
    raise ImportError(
        "pyyaml fehlt -- bitte installieren: pip install pyyaml") from e

DEFAULT_PATH = (Path(__file__).resolve().parent.parent.parent
                / "proxmox" / "inventory.yml")


@dataclasses.dataclass
class Entry:
    name: str
    role: str           # server | client | template | infra
    label: str          # production | on-demand | debug
    os: str             # linux | windows-server | windows-client | other
    vmid: int | None = None
    note: str = ""
    dmz: bool = False
    source: str = "proxmox"   # proxmox | hetzner

    @property
    def must_run(self) -> bool:
        return self.role == "server" and self.label == "production"

    @property
    def is_client(self) -> bool:
        return self.role == "client"

    @property
    def is_excluded_from_soll_check(self) -> bool:
        """on-demand/debug/template/client sind kein Soll-Check-Kandidat."""
        return self.label in ("on-demand", "debug") or self.role in ("template", "client")


def load(path: Path = DEFAULT_PATH) -> list[Entry]:
    if not path.exists():
        raise FileNotFoundError(f"Inventar nicht gefunden: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    out: list[Entry] = []
    for source_key in ("proxmox", "hetzner"):
        for item in data.get(source_key) or []:
            out.append(Entry(
                vmid=item.get("vmid"),
                name=item["name"],
                role=item.get("role", "server"),
                label=item.get("label", "production"),
                os=item.get("os", "linux"),
                note=item.get("note", ""),
                dmz=bool(item.get("dmz", False)),
                source=source_key,
            ))
    return out


def must_run(entries: Iterable[Entry]) -> list[Entry]:
    return [e for e in entries if e.must_run]


def clients(entries: Iterable[Entry]) -> list[Entry]:
    return [e for e in entries if e.is_client]


def by_label(entries: Iterable[Entry], label: str) -> list[Entry]:
    return [e for e in entries if e.label == label]


def summary(entries: Iterable[Entry]) -> dict:
    """Schnelle Übersicht für Sanity-Check / Dashboard-Footer."""
    es = list(entries)
    return {
        "total": len(es),
        "must_run_servers": len(must_run(es)),
        "clients": len(clients(es)),
        "on_demand": len(by_label(es, "on-demand")),
        "debug": len(by_label(es, "debug")),
        "templates": sum(1 for e in es if e.role == "template"),
    }
