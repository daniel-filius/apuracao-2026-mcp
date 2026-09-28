FROM python:3.12-slim

# Verificação de posse exigida pelo registro MCP oficial (registryType "oci"):
# o valor DEVE ser igual ao campo "name" do server.json.
LABEL io.modelcontextprotocol.server.name="io.github.daniel-filius/apuracao-2026"
LABEL org.opencontainers.image.source="https://github.com/daniel-filius/apuracao-2026-mcp"
LABEL org.opencontainers.image.description="Servidor MCP com os resultados oficiais parciais das eleições brasileiras (TSE)"
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY apuracao_mcp ./apuracao_mcp
RUN pip install --no-cache-dir .

# Sem argumentos: servidor MCP em stdio. `--help`, `--version`, `--smoke`, `--demo UF CARGO` também funcionam.
ENTRYPOINT ["apuracao-mcp"]
