# Container-Deployment: ibf-mcp als HTTP-MCP

Setup für interne Hosts (z.B. `itl53` / `192.168.11.53`). Ein Container exposed
**drei** Ports parallel mit unterschiedlichem Toolset/Doc-Level.

| Port | Toolset | Doc-Level | Use-Case |
|---|---|---|---|
| 8080 | full    | compact | Standard, alle Tools |
| 8081 | compact | compact | mittlere Token-Last |
| 8082 | min     | min     | Status-Checks, Token-knapp |

(Auf itl53 sind 8080/8000 schon belegt -- Host-Mapping verwendet 8180/8181/8182.)

## Image

GitHub Actions baut bei jedem Push auf `main` (sofern Tools/Dockerfile/Entrypoint
berührt sind) und published nach **GHCR** (privat):

```
ghcr.io/ibf-solutions/ibf-mcp-tools:latest
ghcr.io/ibf-solutions/ibf-mcp-tools:<commit-sha>
```

## Konfiguration

Der Container braucht Credentials per ENV (Keyring funktioniert nicht im Container).
**`/etc/ibf-mcp.env` auf dem Host, `chmod 600`** -- gehört NICHT ins Repo.

| ENV-Var | Zweck | Beispiel |
|---|---|---|
| `PROXMOX_TOKEN` | Proxmox-API-Token | `root@pam!claude=<secret>` |
| `IBF_MCP_GLOBAL_PASSWORD` | Auth-Tool-Unlock | `<password>` |
| `GRAYLOG_TOKEN` | Graylog-API-Token | `<token>` |
| `FORTIGATE_USER` | FortiGate-User | `audit` |
| `FORTIGATE_PASSWORD` | FortiGate-Passwort | `audit` |
| `IBF_MCP_HTTP_HOST` | Bind-Host | `0.0.0.0` (Default) |

`IBF_MCP_TOOLSET` und `IBF_MCP_DOC_LEVEL` werden vom Entrypoint pro Port überschrieben
und müssen NICHT in der env-Datei stehen.

## Deploy auf itl53 (192.168.11.53)

```bash
# GHCR-Login (PAT mit read:packages-Scope)
echo "$IBF_GHCR_TOKEN" | docker login ghcr.io -u x-access-token --password-stdin

# Image ziehen
docker pull ghcr.io/ibf-solutions/ibf-mcp-tools:latest

# Bestehenden Container ggf. ersetzen
docker rm -f ibf.mcp 2>/dev/null

# Neu starten -- 3 Ports gemappt auf 8180/8181/8182 (8080/8000 sind auf itl53 belegt)
docker run -d \
  --name ibf.mcp \
  --hostname ibf.mcp \
  --restart unless-stopped \
  -p 8180:8080 \
  -p 8181:8081 \
  -p 8182:8082 \
  --env-file /etc/ibf-mcp.env \
  ghcr.io/ibf-solutions/ibf-mcp-tools:latest

# Health-Check
docker logs --tail 30 ibf.mcp
curl -sS -X POST http://127.0.0.1:8180/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

## Client-Anbindung

### Claude Code (lokal)

```powershell
# Standardvariante: full toolset
claude mcp add --scope user ibf-remote      --transport http http://192.168.11.53:8180/mcp
# Optional zusätzlich:
claude mcp add --scope user ibf-remote-min  --transport http http://192.168.11.53:8182/mcp
```

(`ibf-remote` als Name, damit der lokale stdio-`ibf` daneben weiterhin koexistieren kann.)

### claude.ai (später)

Erfordert öffentlich erreichbaren HTTPS-Endpoint mit gültigem Cert. Pfad:
1. Reverse-Proxy auf itl53 (Caddy/Traefik) mit Let's-Encrypt-Cert auf einer
   Subdomain (z.B. `mcp.ibf-solutions.com`)
2. Bearer-Token-Auth oder OAuth in `FastMCP(auth=...)` aktivieren
3. claude.ai Custom-MCP-Integration mit dieser URL + Token konfigurieren

## RAM-Verhalten

Drei Python-Prozesse im selben Container teilen Code-Pages über copy-on-write
(KSM kann zusätzlich identische Heap-Seiten dedupen). Heap, Tool-State und
Auth-Token sind pro Prozess komplett getrennt -- Crash eines Prozesses propagiert
nicht in die anderen. Aufgrund `wait -n` im Entrypoint terminiert dann aber
der Container und Docker-Restart bringt alles drei wieder hoch.

Erwarteter Mehraufwand vs. einem Einzel-Prozess: ~2× ~80-100 MB RSS extra.

## Updates

```bash
docker pull ghcr.io/ibf-solutions/ibf-mcp-tools:latest
docker rm -f ibf.mcp
docker run -d --name ibf.mcp --hostname ibf.mcp --restart unless-stopped \
  -p 8180:8080 -p 8181:8081 -p 8182:8082 --env-file /etc/ibf-mcp.env \
  ghcr.io/ibf-solutions/ibf-mcp-tools:latest
```
