# Pläne

## T7 — graylog-query.py: sichere Token-Verwaltung

### Ziel
Den Graylog-API-Token nicht mehr in einer Klartextdatei (`.env`) speichern müssen.
Token soll nie im Klartext im Dateisystem oder in Script-Output erscheinen.

### Optionen (zur Entscheidung)

| Option | Aufwand | Sicherheit | Voraussetzung |
|--------|---------|------------|---------------|
| A — Umgebungsvariable (`GRAYLOG_IBF`) | minimal | mittel (in Prozesslisten sichtbar) | nichts |
| B — Windows Credential Manager (`keyring`) | gering | hoch (OS-verschlüsselt, per Windows-Login geschützt) | `pip install keyring` |
| C — Bitwarden CLI (`bw`) | mittel | hoch (zentraler Vault, geräteübergreifend) | `bw` CLI installiert, `BW_SESSION` gesetzt |
| D — `age` + SSH-Agent | hoch | sehr hoch (Private Key verlässt Agent nie) | `age` installiert (`winget install FiloSottile.age`), SSH-Agent läuft — **⚠ Sackgasse auf Windows+Bitwarden:** `age -d -i` erwartet private Schlüsseldatei auf Disk; Bitwarden hält Keys nur im Vault, kein Disk-Export → `age` findet den Agent-Key nicht. Auf Linux/macOS funktioniert dieser Weg via `SSH_AUTH_SOCK`. |

### Empfohlene Reihenfolge in `load_token()` (nach Entscheidung)

```
1. Env-Var GRAYLOG_IBF          → für CI/Automation
2. Bitwarden CLI (bw)           → wenn BW_SESSION gesetzt
3. Windows Credential Manager   → interaktiv, persistent (keyring)
4. age-verschlüsselte Datei     → via SSH-Agent-Signing
5. .env-Datei                   → Fallback/Legacy
```

Jede Quelle wird still übersprungen wenn nicht verfügbar (kein Fehler, kein Zwang).

### Wie age+SSH-Agent funktioniert (Option D)

1. SSH-Agent signiert einen fixen Nonce → deterministisch (Ed25519: gleicher Key + gleiche Nachricht = immer gleiche Signatur)
2. Signatur-Bytes → SHA-256 → AES-256-Key
3. Token damit verschlüsseln → verschlüsselter Blob in Datei (z.B. `.graylog_token.enc`)
4. Entschlüsseln: Agent signiert denselben Nonce → gleicher Key → Token

Alternativ direkt mit `age`:
```powershell
# Einmalig verschlüsseln
age -R ~/.ssh/id_ed25519.pub -o .graylog_token.age <<< "mein-token"

# Entschlüsseln zur Laufzeit
age -d -i ~/.ssh/id_ed25519 .graylog_token.age
```

### Nächster Schritt
Konzept A–C umsetzen (D gestrichen), dann `load_token()` in `projekte/graylog/tools/graylog-query.py` entsprechend erweitern.

---

## T7a — Bitwarden Desktop Client Integration (Named Pipe IPC)

### Ziel
Bitwarden Desktop direkt abfragen — ohne separaten `bw` CLI-Download. Der Benutzer sieht einen Bestätigungs-Dialog im Desktop-Client.

### Technischer Ablauf

```
Python                          Bitwarden Desktop
  |                                    |
  |-- RSA-2048 Handshake-JSON -------> |
  |                                    | [zeigt Bestätigungs-Dialog]
  |                      [User klickt "Approve"]
  |<-- AES-Session-Key (RSA-encrypt) --|
  |                                    |
  |-- AES(bw-credential-retrieval) --> |
  |<-- AES(password: "mein-token") ----|
```

### Named Pipe
`\\.\pipe\tmp-app.bitwarden` — exponiert wenn "Allow DuckDuckGo browser integration" in Bitwarden Desktop Preferences aktiviert ist.

### Protokoll
- Transport: 4-Byte LE Länge + JSON (LengthDelimitedCodec)
- Handshake: RSA-2048 (OAEP/SHA-1), Python generiert Keypair on-the-fly
- Session: AES-256-CBC, Key wird per RSA vom Desktop verschlüsselt zurückgegeben
- Bestätigung: einmalig pro Session (nicht pro Abfrage)

### Verfügbare Commands
| Command | Funktion |
|---------|---------|
| `bw-status` | Accounts auflisten |
| `bw-credential-retrieval` | Passwort per URI abrufen |
| `bw-credential-create` | Item anlegen |
| `bw-generate-password` | Passwort generieren |

### Einschränkungen
- `bw-credential-retrieval` sucht per **URI**, nicht per Item-Name → Item muss eine passende URL/URI haben (z.B. `https://gld.ibf-solutions.com`)
- DuckDuckGo-Integration muss aktiviert sein (Bitwarden Desktop > Preferences)
- Offiziell nur macOS dokumentiert — Windows funktioniert laut Source-Code, aber nicht offiziell supported
- Kein fertiges Python-Lib → ~150 Zeilen selbst implementieren (`cryptography` + `pywin32`)
- Desktop muss laufen und entsperrt sein

### Referenz-Implementierungen (TypeScript)
- `github.com/bitwarden/clients` → `apps/desktop/native-messaging-test-runner/src/`
- `github.com/jeanregisser/bitwarden-cli-bio`

### Python-Dependencies
```
pip install cryptography pywin32
```

### Nächster Schritt
Prüfen ob "Allow DuckDuckGo browser integration" in Bitwarden Desktop verfügbar/aktivierbar ist, dann Python-IPC-Client implementieren.
