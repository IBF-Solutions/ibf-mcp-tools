# Container-Deployment: ibf-mcp als HTTP-MCP

Setup für interne Hosts (z.B. `itl53` / `192.168.11.53`). Der combined IBF-MCP läuft
als langlebiger HTTP-Server (FastMCP `streamable-http` Transport), Claude Code +
claude.ai können sich verbinden.

## Image

GitHub Actions baut bei jedem Push auf `main` (sofern Tools/Dockerfile berührt sind)
und published nach GHCR:

```
ghcr.io/ibf-solutions/ibf-mcp-tools:latest
ghcr.io/ibf-solutions/ibf-mcp-tools:<commit-sha>
```

Pro Image-Build wird sowohl `latest` als auch der genaue SHA-Tag gepusht --
Pinning auf SHA für Produktion empfohlen.

## Konfiguration

Der Container braucht Credentials per ENV (Keyring funktioniert nicht im Container):

| ENV-Var | Zweck | Beispiel |
|---|---|---|
| `PROXMOX_TOKEN` | Proxmox-API-Token | `root@pam!claude=<secret>` |
| `IBF_MCP_GLOBAL_PASSWORD` | Auth-Tool-Unlock (optional) | `<password>` |
| `IBF_MCP_DOC_LEVEL` | Tool-Beschreibungs-Verbosity | `compact` |
| `IBF_MCP_TOOLSET` | Welche Tools registriert werden | `full` |
| `IBF_MCP_READONLY` | Destruktive Tools blocken | `0` (off) |
| `IBF_MCP_HTTP_HOST` | Bind-Host | `0.0.0.0` (Default) |
| `IBF_MCP_HTTP_PORT` | Bind-Port | `8080` (Default) |
| `GRAYLOG_TOKEN` | Graylog-API-Token | `<token>` |
| `FORTIGATE_PASSWORD` | FortiGate-Passwort | `audit` |

Empfohlen: `/etc/ibf-mcp.env` auf dem Host:
```
PROXMOX_TOKEN=root@pam!claude=...
IBF_MCP_DOC_LEVEL=compact
IBF_MCP_TOOLSET=full
IBF_MCP_READONLY=0
```
mit `chmod 600`.

## Deploy auf itl53 (192.168.11.53)

```bash
# Image ziehen (GHCR ist public lesbar nur wenn Repo public ist -- sonst gh login)
echo "$GHCR_TOKEN" | docker login ghcr.io -u <user> --password-stdin
docker pull ghcr.io/ibf-solutions/ibf-mcp-tools:latest

# Bestehenden Container ggf. ersetzen
docker rm -f ibf.mcp 2>/dev/null

# Neu starten
docker run -d \
  --name ibf.mcp \
  --hostname ibf.mcp \
  --restart unless-stopped \
  -p 8080:8080 \
  --env-file /etc/ibf-mcp.env \
  ghcr.io/ibf-solutions/ibf-mcp-tools:latest

# Health-Check
docker logs --tail 30 ibf.mcp
curl -sS http://127.0.0.1:8080/mcp -X POST \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

## Client-Anbindung

### Claude Code (lokal)

```powershell
claude mcp add --scope user ibf-remote --transport http http://192.168.11.53:8080/mcp
```

(`ibf-remote` als Name, damit der lokale stdio-`ibf` daneben weiterhin koexistieren kann.)

### claude.ai (später)

Erfordert öffentlich erreichbaren HTTPS-Endpoint mit gültigem Cert. Pfad:
1. Reverse-Proxy auf itl53 (Caddy/Traefik) mit Let's-Encrypt-Cert auf einer
   Subdomain (z.B. `mcp.ibf-solutions.com`)
2. Bearer-Token-Auth oder OAuth in `FastMCP(auth=...)` aktivieren
3. claude.ai Custom-MCP-Integration mit dieser URL + Token konfigurieren

## Updates

Image-Refresh nach neuem Push auf `main`:

```bash
docker pull ghcr.io/ibf-solutions/ibf-mcp-tools:latest
docker rm -f ibf.mcp
docker run -d --name ibf.mcp --hostname ibf.mcp --restart unless-stopped \
  -p 8080:8080 --env-file /etc/ibf-mcp.env \
  ghcr.io/ibf-solutions/ibf-mcp-tools:latest
```

(Sauberer Weg via watchtower oder `docker compose pull && up -d` -- bei Bedarf
später als `compose.yml` aufsetzen.)
