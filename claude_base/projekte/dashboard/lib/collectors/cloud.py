"""Cloud-Sektion: Hetzner-Cloud-Server + Volumes."""

from __future__ import annotations

import dataclasses

from .. import hetzner_api


@dataclasses.dataclass
class CloudSection:
    available: bool
    error: str | None
    servers_total: int
    servers_running: int
    servers_other: list[tuple[str, str]]      # [(name, status)] != running
    volumes_total: int
    volumes_attached: int


def collect() -> CloudSection:
    if not hetzner_api.is_available():
        return CloudSection(available=False, error="kein Hetzner-Token",
                            servers_total=0, servers_running=0,
                            servers_other=[], volumes_total=0, volumes_attached=0)
    try:
        srv = hetzner_api.servers()
        vol = hetzner_api.volumes()
    except RuntimeError as e:
        return CloudSection(available=False, error=str(e),
                            servers_total=0, servers_running=0,
                            servers_other=[], volumes_total=0, volumes_attached=0)

    running = sum(1 for s in srv if s.get("status") == "running")
    other = [(s.get("name", "?"), s.get("status", "?"))
             for s in srv if s.get("status") != "running"]
    attached = sum(1 for v in vol if v.get("server"))
    return CloudSection(
        available=True, error=None,
        servers_total=len(srv),
        servers_running=running,
        servers_other=other,
        volumes_total=len(vol),
        volumes_attached=attached,
    )


def render_text(sec: CloudSection) -> str:
    out = ["=== CLOUD (Hetzner) ==="]
    if not sec.available:
        out.append(f"  (nicht verfügbar: {sec.error})")
        return "\n".join(out)
    out.append(f"  Servers:  {sec.servers_running}/{sec.servers_total} running")
    out.append(f"  Volumes:  {sec.volumes_attached}/{sec.volumes_total} attached")
    if sec.servers_other:
        out.append("")
        out.append("  ⚠ Nicht-running:")
        for n, st in sec.servers_other:
            out.append(f"    - {n}  status={st}")
    return "\n".join(out)


if __name__ == "__main__":
    print(render_text(collect()))
