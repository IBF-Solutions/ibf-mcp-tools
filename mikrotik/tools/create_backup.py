#!/usr/bin/env python3
"""
create_backup.py -- MikroTik Config-Backup Tool

Exportiert die Konfiguration eines MikroTik-Geraets und speichert sie
als exports/<identity>.rsc im Git-Repo.

Ablauf (Backup):
  1. SSH-Verbindung via Ed25519-Key (claude17_ed25519)
  2. Prueft ob RouterOS-Script 'create_export' auf dem Geraet existiert
     -- falls nicht: wird automatisch aus mikrotik-scripte/create_export.rsc importiert
  3. Fuehrt 'create_export' aus (erzeugt RSC + Binary-Backup auf dem Geraet)
  4. Laedt die neue RSC-Datei per SFTP herunter
  5. Speichert als exports/<identity>.rsc (ueberschreibt bestehende Datei)
  6. Git-Commit -- kein Push ohne --push

Verwendung:
  python tools/create_backup.py --device 192.168.10.100
  python tools/create_backup.py --device 192.168.10.100 --push
  python tools/create_backup.py --deploy-script
  python tools/create_backup.py --deploy-script --device 192.168.10.50

--deploy-script:
  Zieht zuerst die aktuelle Version von GitHub (git pull), dann wird
  mikrotik-scripte/create_export.rsc auf allen bekannten Geraeten
  (oder nur --device) per Delete+Recreate aktualisiert. Kein Backup.

Bekannte Geraete:
  192.168.10.100  0816power          (Router + CAPsMAN Controller)
  192.168.10.32   K1.Cap.OG          (cAP ax, OG)
  192.168.10.50   K1.wz              (cAP ax, Wohnzimmer)
  192.168.10.14   K1.garten          (cAP ax, Garten)
  192.168.10.10   K1.LTEb            (legacy CAPsMAN)
  192.168.10.35   K1.wifi-dc-26_base (legacy CAPsMAN)
  192.168.10.19   K1.AC-Raum         (wired-only)
  192.168.10.41   K1.AC-Wall-Wifi    (wired-only)
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

import paramiko

# SSH-Key (Ed25519, importiert auf allen 8 direkt erreichbaren Home-Routern)
SSH_KEY_PATH = Path(r"C:\Temp\Test-claude\claude17_ed25519")
SSH_USER = "claude"
SSH_PORT = 22

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent  # tools/ -> mikrotik/ -> subprojects/ -> ai_mainprojekt/
EXPORTS_DIR = SCRIPT_DIR.parent / "exports"
SCRIPTE_DIR = SCRIPT_DIR.parent / "mikrotik-scripte"
CREATE_EXPORT_SOURCE = SCRIPTE_DIR / "create_export.rsc"

ALL_DEVICES = [
    "192.168.10.100",
    "192.168.10.32",
    "192.168.10.50",
    "192.168.10.14",
    "192.168.10.10",
    "192.168.10.35",
    "192.168.10.19",
    "192.168.10.41",
]


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

def connect(ip: str) -> paramiko.SSHClient:
    key = paramiko.Ed25519Key.from_private_key_file(str(SSH_KEY_PATH))
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ip, port=SSH_PORT, username=SSH_USER, pkey=key,
              timeout=15, look_for_keys=False, allow_agent=False)
    return c


def run_cmd(c: paramiko.SSHClient, cmd: str, timeout: int = 15) -> str:
    _, out, _ = c.exec_command(cmd, timeout=timeout)
    return out.read().decode(errors="replace").strip()


def get_identity(c: paramiko.SSHClient) -> str:
    return run_cmd(c, ":put [/system identity get name]")


# ---------------------------------------------------------------------------
# Script deploy
# ---------------------------------------------------------------------------

def git_pull() -> None:
    print("Git-Pull (origin/master) ...")
    result = subprocess.run(
        ["git", "pull", "origin", "master", "--rebase"],
        cwd=str(REPO_ROOT), capture_output=True, text=True
    )
    if result.returncode == 0:
        lines = result.stdout.strip().splitlines()
        print(f"  {lines[-1] if lines else 'Already up to date.'}")
    else:
        print(f"  git pull Fehler:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)


def upload_and_add_script(c: paramiko.SSHClient) -> None:
    """Laedt Script-Body per SFTP hoch, RouterOS liest Source direkt aus Datei."""
    tmp = "_ce_import.txt"
    sftp = c.open_sftp()
    sftp.put(str(CREATE_EXPORT_SOURCE), tmp)
    sftp.close()
    # RouterOS liest den Source aus der Datei -- kein String-Escaping noetig
    run_cmd(
        c,
        f'/system/script/add name=create_export '
        f'policy=ftp,reboot,read,write,policy,test,password,sniff,sensitive,romon '
        f'source=[/file/get [find name="{tmp}"] contents]',
        timeout=15,
    )
    run_cmd(c, f'/file/remove [find name="{tmp}"]', timeout=10)


def deploy_script_to_device(c: paramiko.SSHClient, identity: str) -> None:
    """Loescht create_export auf dem Geraet und legt es neu aus der Repo-Quelle an."""
    exists = run_cmd(c, ':put [:len [/system/script/find name=create_export]]')
    if exists != "0":
        run_cmd(c, '/system/script/remove [find name=create_export]', timeout=10)
        print(f"  {identity}: altes Script geloescht")
    else:
        print(f"  {identity}: Script war nicht vorhanden")

    upload_and_add_script(c)

    exists_after = run_cmd(c, ':put [:len [/system/script/find name=create_export]]')
    if exists_after == "0":
        print(f"  {identity}: FEHLER -- Script-Import fehlgeschlagen", file=sys.stderr)
    else:
        print(f"  {identity}: Script erfolgreich deployed")


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def ensure_create_export(c: paramiko.SSHClient) -> None:
    """Importiert 'create_export' falls es auf dem Geraet nicht existiert."""
    exists = run_cmd(c, ':put [:len [/system/script/find name=create_export]]')
    if exists != "0":
        print("  Script 'create_export' bereits vorhanden.")
        return

    print("  Script 'create_export' fehlt -- importiere ...")
    upload_and_add_script(c)

    exists_after = run_cmd(c, ':put [:len [/system/script/find name=create_export]]')
    if exists_after == "0":
        print("  FEHLER: Import fehlgeschlagen.", file=sys.stderr)
        sys.exit(1)
    print("  Script erfolgreich importiert.")


def list_rsc_files(c: paramiko.SSHClient) -> list[str]:
    """Alle .rsc-Dateien im Root des Geraets (nicht in flash/)."""
    raw = run_cmd(
        c,
        ':foreach f in=[/file find where name~".rsc"] do={:put [/file get $f name]}',
        timeout=20,
    )
    return [name for name in raw.splitlines() if name.strip() and "/" not in name]


def run_create_export(c: paramiko.SSHClient) -> None:
    print("  Fuehre RouterOS-Script 'create_export' aus ...")
    _, out, err = c.exec_command("/system/script/run create_export", timeout=30)
    out.read(); err.read()


def diagnose_fallback_reason(c: paramiko.SSHClient) -> str:
    """Liest RouterOS-Log und Systemzeit um Fallback-Ursache kurz zu erklaeren."""
    clock = run_cmd(c, ":put [/system clock get date]", timeout=5)
    log = run_cmd(
        c,
        '/log print terse where topics~"script" and topics~"error"',
        timeout=10,
    )
    last_error = ""
    for line in reversed(log.splitlines()):
        line = line.strip()
        if line and ("script,error" in line or "invalid" in line.lower() or "syntax" in line.lower()):
            parts = line.split(" script,error", 1)
            last_error = parts[-1].strip().lstrip(",debug").strip() if len(parts) > 1 else line
            break

    if "1970" in clock:
        return f"Systemzeit nicht gesetzt (NTP fehlt, Uhr={clock}) -- ungueltige Zeichen im Dateinamen"
    if last_error:
        return last_error
    return f"Kein Fehler im Log erkennbar (Uhr={clock}), evtl. fehlende Schreibrechte"


def find_newest_rsc(before: list[str], after: list[str]) -> str | None:
    new_files = [f for f in after if f not in before]
    return new_files[-1] if new_files else None


def export_terse_fallback(c: paramiko.SSHClient, identity: str, local_path: Path) -> None:
    """Fallback: /export terse direkt wenn create_export kein File erzeugt."""
    print("  Fallback: /export terse direkt ausfuehren ...")
    tmp_name = f"_claude_backup_{identity}"
    run_cmd(c, f'/export terse file="{tmp_name}"', timeout=30)
    time.sleep(2)
    remote_name = f"{tmp_name}.rsc"
    sftp = c.open_sftp()
    try:
        sftp.stat(remote_name)
    except FileNotFoundError:
        print("  FEHLER: Auch Fallback-Export hat keine Datei erzeugt.", file=sys.stderr)
        sftp.close()
        sys.exit(1)
    print(f"  Download: {remote_name} -> {local_path.name}")
    sftp.get(remote_name, str(local_path))
    sftp.close()
    run_cmd(c, f'/file remove "{remote_name}"', timeout=10)


def download_rsc(c: paramiko.SSHClient, remote_name: str, local_path: Path) -> None:
    sftp = c.open_sftp()
    print(f"  Download: {remote_name} -> {local_path.name}")
    sftp.get(remote_name, str(local_path))
    sftp.close()


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git_commit(identity: str, device_ip: str) -> None:
    rel_path = Path("subprojects/mikrotik/exports") / f"{identity}.rsc"
    subprocess.run(["git", "add", str(rel_path)], cwd=str(REPO_ROOT), check=True)
    msg = (
        f"mikrotik: {identity} config backup aktualisiert\n\n"
        f"Erzeugt via create_export-Script auf {device_ip}.\n\n"
        f"Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
    )
    result = subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=str(REPO_ROOT), capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"  git commit: {result.stdout.strip().splitlines()[0]}")
    else:
        print(f"  git commit: {result.stdout.strip() or result.stderr.strip()}")


def git_push() -> None:
    result = subprocess.run(
        ["git", "push", "origin", "master"],
        cwd=str(REPO_ROOT), capture_output=True, text=True
    )
    if result.returncode == 0:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT), capture_output=True, text=True
        ).stdout.strip()
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(REPO_ROOT), capture_output=True, text=True
        ).stdout.strip().removesuffix(".git")
        print(f"  git push: {remote}/commit/{sha}")
    else:
        print(f"  git push Fehler:\n{result.stderr}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MikroTik Config-Backup Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Beispiele:\n"
            "  python tools/create_backup.py --device 192.168.10.100\n"
            "  python tools/create_backup.py --device 192.168.10.100 --push\n"
            "  python tools/create_backup.py --deploy-script\n"
            "  python tools/create_backup.py --deploy-script --device 192.168.10.50\n"
            "\nBekannte Geraete:\n"
            + "\n".join(
                l for l in __doc__.splitlines() if l.strip().startswith("192.")
            )
        ),
    )
    parser.add_argument(
        "--device", metavar="IP",
        help="IP-Adresse des Zielgeraets. Bei --deploy-script optional (Default: alle Geraete)"
    )
    parser.add_argument(
        "--push", action="store_true",
        help="Nach dem Commit auch pushen und Commit-URL ausgeben (nur bei Backup)"
    )
    parser.add_argument(
        "--deploy-script", action="store_true",
        help=(
            "create_export.rsc von GitHub pullen und auf allen Geraeten "
            "(oder nur --device) per Delete+Recreate aktualisieren. Kein Backup."
        )
    )
    args = parser.parse_args()

    if not args.deploy_script and not args.device:
        parser.error("--device ist erforderlich wenn --deploy-script nicht gesetzt ist")

    # --- DEPLOY-SCRIPT Modus ---
    if args.deploy_script:
        git_pull()
        targets = [args.device] if args.device else ALL_DEVICES
        print(f"Deploye create_export auf {len(targets)} Geraet(en) ...")
        errors = []
        for ip in targets:
            try:
                c = connect(ip)
                identity = get_identity(c)
                deploy_script_to_device(c, identity)
                c.close()
            except Exception as e:
                print(f"  {ip}: FEHLER -- {e}", file=sys.stderr)
                errors.append(ip)
        if errors:
            print(f"\nFehlgeschlagen: {errors}", file=sys.stderr)
            sys.exit(1)
        print("Deploy abgeschlossen.")
        return

    # --- BACKUP Modus ---
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Verbinde mit {args.device} ...")
    c = connect(args.device)
    identity = get_identity(c)
    print(f"  Geraet: {identity}")

    ensure_create_export(c)

    files_before = list_rsc_files(c)
    run_create_export(c)
    print("  Warte 3s ...")
    time.sleep(3)
    files_after = list_rsc_files(c)

    newest = find_newest_rsc(files_before, files_after)
    local_rsc = EXPORTS_DIR / f"{identity}.rsc"

    if newest:
        print(f"  Neue Datei auf Geraet: {newest}")
        download_rsc(c, newest, local_rsc)
    else:
        reason = diagnose_fallback_reason(c)
        print(f"  Fallback: create_export hat keine Datei erzeugt ({reason})")
        export_terse_fallback(c, identity, local_rsc)

    c.close()
    print(f"  Gespeichert: exports/{identity}.rsc")

    print("Git-Commit ...")
    git_commit(identity, args.device)

    if args.push:
        print("Git-Push ...")
        git_push()
    else:
        print("  (kein Push -- mit --push aufrufen)")

    print("Fertig.")


if __name__ == "__main__":
    main()
