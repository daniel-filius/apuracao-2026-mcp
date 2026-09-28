"""Servidor MCP (stdio) — Apuração Eleições 2026 (TSE).

Tools:
- listar_eleicoes()                       → eleições ordinárias no config oficial + histórico disponível
- resultado(uf, cargo, turno, ano)        → parcial oficial por UF (ou BR para presidente)
- resumo_brasil(turno, ano)               → presidente, total nacional
- municipio(uf, municipio, cargo, turno, ano) → parcial oficial por município

Toda resposta traz `fonte` ("TSE – divulgação oficial, resultado parcial"),
`apurado_pct` e `atualizado_em` (carimbo do TSE). Nenhuma projeção.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import __version__
from .tse_client import CARGOS, TSEClient, TSEError

INSTRUCTIONS = (
    "Consulta os resultados oficiais parciais das eleições brasileiras publicados pelo TSE. "
    "Sempre informe ao usuário que o resultado é PARCIAL, cite o percentual apurado (`apurado_pct`) "
    "e o horário do TSE (`atualizado_em`). Não faça projeções nem declare vencedor antes de "
    "`matematicamente_definido` ser verdadeiro. UF em sigla (SP, RJ, ...) ou BR para presidente. "
    f"Cargos: {', '.join(CARGOS)}. Se o TSE ainda não publicou a eleição pedida, a tool explica; "
    "dados históricos: ano=2022 (gerais) e ano=2024 (municipais)."
)

mcp = MCPServer(
    name="apuracao-2026",
    title="Apuração Eleições 2026 (TSE)",
    description="Resultados oficiais parciais das eleições brasileiras 2026 (TSE) por UF, cargo e município.",
    instructions=INSTRUCTIONS,
    website_url="https://github.com/daniel-filius/apuracao-2026-mcp",
    version=__version__,
)

_client: TSEClient | None = None


def get_client() -> TSEClient:
    global _client
    if _client is None:
        _client = TSEClient()
    return _client


def set_client(client: TSEClient) -> None:
    """Injeção para testes."""
    global _client
    _client = client


def _safe(fn, *args, **kwargs) -> dict[str, Any]:
    try:
        return fn(*args, **kwargs)
    except TSEError as exc:
        return {"erro": str(exc), "fonte": "TSE – divulgação oficial"}


@mcp.tool(
    title="Listar eleições",
    description="Lista as eleições ordinárias presentes no config oficial do TSE (ciclo, código, data, "
                "turno e cargos por abrangência) e o histórico disponível. Use antes de `resultado` "
                "se não souber qual pleito está no ar.",
)
def listar_eleicoes() -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        c = get_client()
        cfg = c.config()
        eleicoes = c.eleicoes_ordinarias()
        anos = sorted({e["ano"] for e in eleicoes if e["ano"]})
        aviso = None
        if 2026 not in anos:
            aviso = ("O config oficial do TSE ainda não lista as Eleições Gerais 2026 (1º turno 04/10/2026, "
                     "2º turno 25/10/2026). Ele costuma ser publicado perto da votação; tente de novo mais tarde.")
        return {
            "fonte": "TSE – divulgação oficial",
            "config_ciclo": cfg.get("c"),
            "config_gerado_em": f"{cfg.get('dg')} {cfg.get('hg')}",
            "eleicoes_ordinarias": eleicoes,
            "historico_disponivel": [
                {"ano": 2022, "turno": 1, "nome": "Eleições Gerais 2022 – 1º turno", "uso": "ano=2022, turno=1"},
                {"ano": 2022, "turno": 2, "nome": "Eleições Gerais 2022 – 2º turno", "uso": "ano=2022, turno=2"},
            ],
            "aviso": aviso,
        }
    return _safe(_run)


@mcp.tool(
    title="Resultado por UF",
    description="Resultado parcial oficial de um cargo em uma UF (ou BR para presidente). "
                "cargo: presidente, governador, senador, deputado_federal, deputado_estadual, "
                "deputado_distrital, prefeito, vereador. turno 1 ou 2. ano opcional (padrão: eleição "
                "ordinária mais próxima de hoje; 2022 para histórico).",
)
def resultado(uf: str, cargo: str, turno: int = 1, ano: int | None = None, limite: int = 20) -> dict[str, Any]:
    return _safe(get_client().resultado, uf, cargo, turno=turno, ano=ano, limite=limite)


@mcp.tool(
    title="Resumo Brasil (presidente)",
    description="Resultado parcial oficial para presidente, total nacional. turno 1 ou 2; ano opcional.",
)
def resumo_brasil(turno: int = 1, ano: int | None = None, limite: int = 20) -> dict[str, Any]:
    return _safe(get_client().resumo_brasil, turno=turno, ano=ano, limite=limite)


@mcp.tool(
    title="Resultado por município",
    description="Resultado parcial oficial de um cargo em um município. municipio: nome exato, código IBGE "
                "(7 dígitos) ou código TSE. Ex.: municipio('SP', 'São Paulo', 'governador').",
)
def municipio(uf: str, municipio: str, cargo: str, turno: int = 1, ano: int | None = None,
              limite: int = 20) -> dict[str, Any]:
    return _safe(get_client().municipio, uf, municipio, cargo, turno=turno, ano=ano, limite=limite)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="apuracao-mcp",
        description="Servidor MCP (stdio) com os resultados oficiais parciais do TSE. "
                    "Sem argumentos: inicia o servidor MCP em stdio.",
    )
    parser.add_argument("--version", action="version", version=f"apuracao-mcp {__version__}")
    parser.add_argument("--smoke", action="store_true",
                        help="teste de fumaça: lê o config do TSE e imprime ciclo/data (equivale a `jq .c`).")
    parser.add_argument("--demo", nargs=2, metavar=("UF", "CARGO"),
                        help="imprime o resultado normalizado (ano 2022) e sai — não inicia o MCP.")
    args = parser.parse_args(argv)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # sem "HTTP Request: GET ..." no stderr

    if args.smoke:
        cfg = get_client().config()
        print(json.dumps({"c": cfg.get("c"), "dg": cfg.get("dg"), "hg": cfg.get("hg"),
                          "pleitos": len(cfg.get("pl", []))}, ensure_ascii=False))
        return 0
    if args.demo:
        uf, cargo = args.demo
        print(json.dumps(_safe(get_client().resultado, uf, cargo, ano=2022), ensure_ascii=False, indent=2))
        return 0

    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
