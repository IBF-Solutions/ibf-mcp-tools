# MikroTik — IBF-Subkontext

Dies ist der **IBF-Subkontext** (Firmennetz). Der Personal-Subkontext liegt unter
`C:\Temp\claude\personal\subprojects\mikrotik\` und teilt sich `tools/` und
`mikrotik-scripte/` per Junction-Point mit diesem Verzeichnis.

## Netzwerk

- IBF-Mikrotik-Infrastruktur in **10.10.40.0/23**
- Gateway / Hauptrouter: TBD (zu ergänzen sobald bekannt / Zugang etabliert)

## Inventory

| Identity | IP / RoMON-MAC | Board | Version | Notiz |
|---|---|---|---|---|
| (leer) | (TBD) | | | |

Bekannte IBF-Geräte aus dem Personal-Kontext (RoMON-Discovery vom 2026-05-02 — nur sichtbar
am 0816power-Heimrouter, nicht direkt aus IBF-Netz). Diese sind „off-limits, keine
Verwaltung":

- IBF-CAP2 (`48.a9.8a.ba.24.fa`, 2 hops via K1.Cap.OG)
- IBF-CAP3 (`48.a9.8a.ba.28.b2`, 3 hops)
- IBF-CAP5 (`48.a9.8a.ba.27.72`, 3 hops)

Sobald hier echter Zugang besteht, normales Inventory einpflegen.

---

## Shared Knowledge — gilt für beide Subkontexte

Diese Recipes/Procedures sind kontextneutral. Personal-Subkontext referenziert sie von hier.

### Python + Paramiko Boilerplate

```python
import paramiko
key = paramiko.Ed25519Key.from_private_key_file(r'<workdir>\<key_basename>')
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('<router_ip>', username='claude', pkey=key,
          timeout=10, look_for_keys=False, allow_agent=False)
_, out, _ = c.exec_command('/system identity print', timeout=10)
print(out.read().decode(errors='replace'))
c.close()
```

`sshpass` und `plink` sind hier üblicherweise nicht da -- Python+paramiko ist die
Standard-Methode für nicht-interaktive Aufrufe. Für interaktiv: `! ssh -i <key> claude@<ip>`
direkt im Prompt mit `!`-Prefix.

### RoMON-only Devices über Gateway erreichen

Geräte ohne IP-Route sind via RoMON (Layer-2) durch den Gateway-Router erreichbar:

```
[philipp.wacker@<gateway>] > /tool/romon/ssh address=<dotted.mac> user=claude
password:    # interaktiv
[claude@<destination>] >
```

**Drei nicht-offensichtliche Caveats** (alle hart erlebt 2026-05-02):

- **MAC muss Punkt-Notation** sein (`2c.c8.1b.d7.d3.18`), nicht Doppelpunkte. `discover`
  zeigt Doppelpunkte, aber `ssh`-Subcommand akzeptiert nur Punkte.
- **RoMON ist Layer 2 -- der SSH-Service am Ziel ist irrelevant.** `/ip/service ssh`
  disable wirkt sich nicht auf RoMON-SSH aus.
- **RoMON-SSH erzwingt Password-Auth. Niemals SSH-Keys auf RoMON-only / external Geräte
  importieren.** Wenn der Ziel-User einen Key hat, setzt RouterOS
  `password-authentication=yes-if-no-key` -- Password-Auth wird stillschweigend abgelehnt
  und die RoMON-Session droppt nach Eingabe ohne Fehlermeldung. Gleicher Effekt wenn der
  Source-User auf dem Gateway einen Key hat.
  Praktische Regel: Keys nur auf direkt-erreichbaren Routern, niemals auf external/RoMON-only.

### Discovery mit Versionen

`/tool/romon/discover` reicht nicht für `version`/`board`/`uptime`:

```routeros
:put [:tostr [/tool/romon/discover as-value duration=8s]]
```

`as-value` liefert einen langen Semikolon-getrennten String -- programmatisch parsen,
nicht per Auge.

### Firmware-Update auf FW-only / RoMON-only Peers

Verifiziertes Pattern (paramiko `invoke_shell`):

```python
ch = c.invoke_shell(term='vt100', width=80, height=24)
drain_until(ch, '> ', timeout=15)
ch.send("/tool/romon/ssh address=<dotted-mac> user=claude\r\n")
drain_until(ch, 'assword', timeout=10)
time.sleep(0.3); ch.send("<password>\r\n")
drain_until(ch, '<identity>...] > ', timeout=30)
ch.send("/system/package/update/check-for-updates channel=long-term\r\n")
drain_until(ch, '> ', timeout=30)
ch.send("/system/package/update/install\r\n")
# Reboot folgt
```

**Routerboard-Firmware** nach Package-Reboot prüfen:

```routeros
/system/routerboard/print
# Falls current-firmware != upgrade-firmware:
/system/routerboard/upgrade   # [y/n]
/system/reboot                # [y/N]
```

Beide Prompts brauchen `y`. Paramiko muss `y/n` und `y/N` separat erkennen.

### Terminal-Quirks (RouterOS 7)

- RouterOS sendet `\x1bZ` + `\x1b[6n` Identity-Queries -> Prompt verzögert ~10s wenn
  unbeantwortet. Lösung: `drain_until(ch, '> ', timeout=15)` statt fixer `time.sleep`.
- Immer `term='vt100'` in `invoke_shell()` setzen.
- RoMON-SSH-Session sauber beenden mit `quit\r\n` statt Transport-Close-Wartezeit.

### Onboarding neuer cAP (CAPsMAN)

#### New WiFi (RouterOS 7, `/interface/wifi*`)

```routeros
# Auf der neuen CAP:
/interface/wifi set [find name=wifi2] configuration.manager=capsman-or-local
/interface/wifi set [find name=wifi5] configuration.manager=capsman-or-local
/interface/wifi cap set \
    enabled=yes \
    caps-man-addresses=<controller-ip> \
    discovery-interfaces=bridge \
    slaves-static=no
```

Dann am Controller (das Provisioning-Rule erstellt das Dynamic-Interface, **pusht aber
keine `master-configuration` automatisch** -- bekanntes 7.21.4-Quirk):

```routeros
/interface/wifi set [find name=cap-wifi3] configuration=cfg-2g disabled=no
/interface/wifi set [find name=cap-wifi4] configuration=cfg-5g disabled=no
```

#### Legacy Wireless (`/interface/wireless*`)

```routeros
/interface/wireless cap set \
    enabled=yes \
    interfaces=wlan1 \
    discovery-interfaces=bridge \
    caps-man-addresses=<controller-ip> \
    bridge=bridge
```

Legacy CAPsMAN appliziert `master-configuration` automatisch.

### RouterOS 7 New WiFi — die 7 Operational Notes

1. **`configuration.manager=capsman-or-local` ist Pflicht** auf jedem Master-Interface
   der CAP, sonst tauchen die Radios nicht im Controller auf.
2. **Provisioning-Rule erstellt nur das Interface, nicht die Config** -- `set [find
   name=cap-wifiN] configuration=cfg-Xg disabled=no` ist manuell pro Radio nötig.
3. **`unset` zum Clearen von Inline-Configs** auf Master-Interfaces; `""` setzen geht
   nicht.
4. **Dynamic Interfaces lassen sich umbenennen** mit `name=<new>`; Schema:
   `<Identity>-wifi2` / `<Identity>-wifi5` via `name-format` in der Provisioning-Rule.
5. **`supported-bands` akzeptiert nur ein Token** (`2ghz-ax` oder `5ghz-ax`); je eine
   Rule pro Band.
6. **Legacy `wireless`-Paket ist auf cAP ax korrekt nicht installiert** -- niemals
   `/caps-man*` (legacy) auf neuer Hardware versuchen.
7. **Windows-Host:** wo PuTTY/plink da ist, geht das. Sonst Python+paramiko.

### Config-Export-Pattern

```bash
plink -ssh -batch -pw <pwd> -hostkey "<host-key>" claude@<ip> -m export.rsc > exports/<identity>.rsc
```

mit `export.rsc` = einzeilige Datei `/export compact hide-sensitive`. PSKs, WireGuard-Keys,
Zertifikate, DHCP-Leases werden gestrippt.

---

## Tools (Junction zur IBF-Quelle)

`tools/` und `mikrotik-scripte/` sind hier die kanonische Quelle. Personal-Subkontext
junctioniert hierher zurück. Tools müssen daher **kontext-agnostisch** sein:

- Konfig aus `Path.cwd() / "config.json"` oder ENV-Var laden, **nicht** aus `__file__`-relativ
- Per-Kontext Daten (Geräte, Credentials, Pfade) leben jeweils im Kontext-Ordner

Aktuelle Tools:
- `tools/create_backup.py` -- Config-Export-Wrapper (TODO: kontext-agnostisch refactoren)

## TODO

- [ ] **T1** [P2] IBF-Inventar aufbauen — sobald Zugang etabliert
- [ ] **T2** [P2] `tools/create_backup.py` kontext-agnostisch machen — -- Konfig aus `cwd` lesen
      damit der gleiche Code in beiden Subkontexten korrekt arbeitet
- [x] **T3** [P2] ~~**MikroTik-MCP** in `claude_base/tools/ibf-mcp.py`~~ (erledigt 2026-05-05) --
      Domain `mikrotik` mit 4 Tools (status, export, neighbors, run); kontext-Detection
      für Default-Host und SSH-Key
- [ ] **T4** [P2] RoMON-Connection effizienter machen — -- aktuell laeuft jeder RoMON-Schritt
      ueber paramiko `invoke_shell()` mit `drain_until` und manueller Passwort-Eingabe
      (siehe Firmware-Update-Procedure). Das ist langsam (10s Terminal-Identity-Quirks
      beim Prompt) und brueckig. Zu probieren:
      - **Winbox-Protokoll (TCP 8291, MikroTik proprietaer)** -- waere am flexibelsten,
        weil es genau das gleiche Tunneling-Prinzip wie RoMON nutzt aber binaer/struktiert
        statt textbasiert. Python-Libraries: `routeros-api`, `librouteros`, oder
        `mikrotik-cli` koennten Hinweise geben. Gibt es eine Python-Implementation des
        Winbox-Protokolls die auch RoMON-Tunneling unterstuetzt?
      - **RouterOS-API (8728/8729)** statt SSH -- ob ueber API auch RoMON-Tunneling geht
      - **paramiko-`exec_command`** mit `RemoteCommand`-Pattern (eine Connection,
        mehrere `/tool/romon/...`-Aufrufe parallel/sequentiell)
      - **`pexpect`-Alternative** wenn Shell-Mode unvermeidlich ist
      - Persistent-SSH-Session zum Gateway pro Tool-Aufruf-Serie statt Reconnect
      Ziel: schneller Zugriff auf RoMON-only Devices (K1.OutdoorSW, hans2, IBF-CAPs) ohne
      `drain_until`-Wartezeit.
