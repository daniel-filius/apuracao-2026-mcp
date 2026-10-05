from __future__ import annotations

import json

import pytest
from mcp.client import Client

from apuracao_mcp import __version__
from apuracao_mcp import server as srv
from apuracao_mcp.tse_client import TSEClient


@pytest.fixture(autouse=True)
def _inject(client: TSEClient):
    srv.set_client(client)
    yield
    srv.set_client(None)  # type: ignore[arg-type]


def _payload(result) -> dict:
    if getattr(result, "structured_content", None):
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    return json.loads(result.content[0].text)


async def test_lista_tools():
    async with Client(srv.mcp) as c:
        tools = await c.list_tools()
    nomes = sorted(t.name for t in tools.tools)
    assert nomes == ["listar_eleicoes", "municipio", "resultado", "resumo_brasil"]
    res = next(t for t in tools.tools if t.name == "resultado")
    assert set(res.input_schema["properties"]) >= {"uf", "cargo", "turno", "ano"}
    assert res.input_schema["required"] == ["uf", "cargo"]


async def test_tool_resultado():
    async with Client(srv.mcp) as c:
        r = await c.call_tool("resultado", {"uf": "SP", "cargo": "governador", "ano": 2022})
    assert not r.is_error
    d = _payload(r)
    assert d["fonte"].startswith("TSE") and "parcial" in d["fonte"]
    assert d["apurado_pct"] == 100.0 and d["atualizado_em"]
    assert d["candidatos"][0]["nome"] == "TARCÍSIO"


async def test_tool_resumo_brasil_e_municipio():
    async with Client(srv.mcp) as c:
        br = _payload(await c.call_tool("resumo_brasil", {"ano": 2022, "turno": 2}))
        mu = _payload(await c.call_tool("municipio", {"uf": "SP", "municipio": "São Paulo",
                                                      "cargo": "governador", "ano": 2022}))
    assert br["candidatos"][0]["nome"] == "LULA"
    assert mu["municipio"]["codigo_ibge"] == "3550308" and mu["fonte"].startswith("TSE")


async def test_tool_listar_eleicoes_avisa_sem_2026():
    async with Client(srv.mcp) as c:
        d = _payload(await c.call_tool("listar_eleicoes", {}))
    assert d["config_ciclo"] == "ele2024"
    assert {e["codigo"] for e in d["eleicoes_ordinarias"]} == {"619", "620"}
    assert "2026" in d["aviso"]


async def test_tool_erro_amigavel():
    async with Client(srv.mcp) as c:
        d = _payload(await c.call_tool("resultado", {"uf": "SP", "cargo": "governador"}))
    assert "erro" in d and "2026" in d["erro"]


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc:
        srv.main(["--version"])
    assert exc.value.code == 0
    assert f"apuracao-mcp {__version__}" in capsys.readouterr().out


def test_cli_smoke_e_demo(capsys):
    assert srv.main(["--smoke"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["c"] == "ele2024" and out["pleitos"] == 35
    assert srv.main(["--demo", "SP", "governador"]) == 0
    assert "TARCÍSIO" in capsys.readouterr().out
