#!/bin/sh
# Startet drei MCP-Instanzen parallel:
#   8080: TOOLSET=full,    DOC_LEVEL=compact   (Standard)
#   8081: TOOLSET=compact, DOC_LEVEL=compact   (mittlerer Token-Verbrauch)
#   8082: TOOLSET=min,     DOC_LEVEL=min       (knapper Token-Modus)
#
# Wenn ein Prozess stirbt, beendet der Container -- restart-policy startet neu.
set -e

log_prefix() {
  prefix="$1"; shift
  "$@" 2>&1 | awk -v p="[$prefix] " '{print p $0; fflush()}'
}

# Tokens / Auth-Settings kommen aus --env-file (PROXMOX_TOKEN, IBF_MCP_GLOBAL_PASSWORD, ...)
# IBF_MCP_TOOLSET / IBF_MCP_DOC_LEVEL werden hier pro Prozess überschrieben.
unset IBF_MCP_TOOLSET IBF_MCP_DOC_LEVEL

IBF_MCP_TOOLSET=full    IBF_MCP_DOC_LEVEL=compact log_prefix full    python /app/ibf-mcp.py --http --port 8080 &
IBF_MCP_TOOLSET=compact IBF_MCP_DOC_LEVEL=compact log_prefix compact python /app/ibf-mcp.py --http --port 8081 &
IBF_MCP_TOOLSET=min     IBF_MCP_DOC_LEVEL=min     log_prefix min     python /app/ibf-mcp.py --http --port 8082 &

# Auf erstes Exit warten, exit-Code propagieren -> Docker-Restart greift
wait -n
EXIT=$?
echo "[entrypoint] One MCP process exited with $EXIT, terminating container"
kill 0 2>/dev/null || true
exit "$EXIT"
