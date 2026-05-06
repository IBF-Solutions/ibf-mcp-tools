FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    IBF_MCP_HTTP_HOST=0.0.0.0 \
    IBF_MCP_HTTP_PORT=8080

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY claude_base/tools/ibf-mcp.py        /app/ibf-mcp.py
COPY claude_base/tools/ibf_mcp_auth.py   /app/ibf_mcp_auth.py
COPY claude_base/tools/mcp_logger.py     /app/mcp_logger.py

EXPOSE 8080

CMD ["python", "/app/ibf-mcp.py", "--http"]
