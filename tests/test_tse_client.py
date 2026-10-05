from __future__ import annotations

import json
import time

import httpx
import pytest

from apuracao_mcp import tse_client as tc
from apuracao_mcp.tse_client import Eleicao, TSEClient, TSEError, normalizar_cargo, normalizar_uf


# ------------------------------------------------------------------ helpers puros
def test_normalizar_cargo_aliases():
    assert normalizar_cargo("Governador") == 3
    assert normalizar_cargo("gov") == 3
    assert normalizar_cargo("deputado federal") == 6
    assert normalizar_cargo("Deputado_Federal") == 6
    assert normalizar_cargo("1") == 1
    with pytest.raises(TSEError):
        normalizar_cargo("rei")


def test_normalizar_uf():
    assert normalizar_uf("SP") == "sp"
    assert normalizar_uf("Brasil") == "br"
    assert normalizar_uf("") == "br"
    with pytest.raises(TSEError):
        normalizar_uf("XX")


def test_parse_nome_arquivo():
    assert tc.parse_nome_arquivo("sp-c0003-e000546-r.json") == {
        "uf": "sp", "municipio": None, "cargo": 3, "eleicao": 546, "tipo": "r"}
    assert tc.parse_nome_arquivo("sp71072-c0003-e000546-u.json")["municipio"] == "71072"
    assert tc.parse_nome_arquivo("foo.json") is None


def test_conversores_numericos():
    assert tc._pct("48,43") == 48.43
    assert tc._pct("100,00") == 100.0
    assert tc._int("57259504") == 57259504
    assert tc._partido("REPUBLICANOS - REPUBLICANOS / PL / PSD") == "REPUBLICANOS"


# ------------------------------------------------------------------ config real (fixture 2026-09-28)
def test_config_e_eleicoes_ordinarias(client: TSEClient):
    cfg = client.config()
    assert cfg["c"] == "ele2024"
    assert len(cfg["pl"]) == 35
    ords = client.eleicoes_ordinarias()
    assert {e["codigo"] for e in ords} == {"619", "620"}  # municipais 2024, 1º e 2º turno
    e619 = next(e for e in ords if e["codigo"] == "619")
    assert e619["ano"] == 2024 and e619["turno"] == 1 and e619["ciclo"] == "ele2024"
    assert e619["codigo_2o_turno"] == "620"
    assert [c["codigo"] for c in e619["abrangencias"][0]["cargos"]] == [11, 13]
    assert "º" in e619["nome"]  # &#186; decodificado


def test_resolver_2024_municipal_pelo_config(client: TSEClient):
    el = client.resolver_eleicao(11, "sp", turno=1, ano=2024)
    assert (el.ciclo, el.codigo, el.data) == ("ele2024", "619", "06/10/2024")


def test_resolver_sem_2026_explica(client: TSEClient):
    with pytest.raises(TSEError) as exc:
        client.resolver_eleicao(3, "sp", turno=1)
    msg = str(exc.value)
    assert "ele2024" in msg and "2026" in msg and "ano=2022" in msg


def test_resolver_2022_fallback_conhecido(client: TSEClient):
    assert client.resolver_eleicao(1, "br", 1, 2022).codigo == "544"
    assert client.resolver_eleicao(1, "br", 2, 2022).codigo == "545"
    assert client.resolver_eleicao(3, "sp", 1, 2022).codigo == "546"
    assert client.resolver_eleicao(5, "sp", 2, 2022).codigo == "547"


def test_resolver_2026_dinamico(fake, config_2026, tmp_path):
    # serve o config sintético (estrutura de 2024 com um pleito de 2026) no lugar do real
    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url) == tc.CONFIG_URL:
            return httpx.Response(200, json=config_2026)
        return fake.handler(req)
    c = TSEClient(transport=httpx.MockTransport(handler))
    el = c.resolver_eleicao(3, "sp", turno=1)  # sem ano → escolhe a ordinária mais próxima de hoje
    assert (el.ciclo, el.codigo, el.ano, el.abrangencia) == ("ele2026", "7003", 2026, "sp")
    el_pres = c.resolver_eleicao(1, "mg", turno=1)
    assert (el_pres.codigo, el_pres.abrangencia) == ("7001", "br")
    assert c.url_resultado(el_pres, 1, "mg") == f"{tc.BASE_URL}/ele2026/7001/dados-simplificados/mg/mg-c0001-e007001-r.json"
    with pytest.raises(TSEError):
        c.resolver_eleicao(3, "ba", turno=1, ano=2026)  # BA não está no config sintético


# ------------------------------------------------------------------ URLs
def test_urls():
    el = Eleicao(2022, 1, "ele2022", "546", "02/10/2022", "x", "sp")
    c = TSEClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    assert c.url_resultado(el, 3, "SP") == f"{tc.BASE_URL}/ele2022/546/dados-simplificados/sp/sp-c0003-e000546-r.json"
    assert c.url_resultado_completo(el, 3, "SP") == f"{tc.BASE_URL}/ele2022/546/dados/sp/sp-c0003-e000546-u.json"
    assert c.url_municipio(el, 3, "sp", "71072") == f"{tc.BASE_URL}/ele2022/546/dados/sp/sp71072-c0003-e000546-u.json"
    assert c.url_municipios_config(el) == f"{tc.BASE_URL}/ele2022/546/config/mun-e000546-cm.json"


# ------------------------------------------------------------------ resultados (fixtures reais 2022)
def test_resultado_governador_sp_2022(client: TSEClient):
    r = client.resultado("SP", "governador", turno=1, ano=2022)
    assert r["fonte"] == tc.FONTE
    assert r["cargo"] == "Governador" and r["abrangencia"] == "SP"
    assert r["apurado_pct"] == 100.0 and r["matematicamente_definido"] is True
    assert r["atualizado_em"] == "10/04/2023 14:29:47"
    assert r["eleicao"] == {"ano": 2022, "turno": 1, "codigo": "546", "ciclo": "ele2022",
                            "data": "02/10/2022", "nome": "Eleições Gerais 2022 – 1º turno"}
    top = r["candidatos"][0]
    assert top["nome"] == "TARCÍSIO" and top["numero"] == "10" and top["partido"] == "REPUBLICANOS"
    assert top["pct"] == 42.32 and top["votos"] == 9881995 and top["situacao"] == "2º turno"
    assert top["vice"] == "FELICIO RAMUTH" and top["eleito"] is False
    assert [c["posicao"] for c in r["candidatos"]] == list(range(1, len(r["candidatos"]) + 1))
    assert r["totais"]["votos_validos"] == 23352549 and r["totais"]["brancos"] == 1645522
    assert r["url_tse"].endswith("sp-c0003-e000546-r.json")


def test_resumo_brasil_2022_2o_turno(client: TSEClient):
    r = client.resumo_brasil(turno=2, ano=2022)
    assert r["cargo"] == "Presidente" and r["abrangencia"] == "BR"
    assert r["candidatos"][0]["nome"] == "LULA" and r["candidatos"][0]["pct"] == 50.9
    assert r["candidatos"][0]["eleito"] is True and r["candidatos"][1]["eleito"] is False
    assert r["total_candidatos"] == 2


def test_resultado_ordena_por_votos_e_limita(client: TSEClient):
    r = client.resultado("sp", "senador", ano=2022, limite=2)
    assert r["candidatos"][0]["nome"] == "ASTRONAUTA MARCOS PONTES"
    assert len(r["candidatos"]) == 2 and r["total_candidatos"] > 2
    votos = [c["votos"] for c in client.resultado("sp", "senador", ano=2022, limite=50)["candidatos"]]
    assert votos == sorted(votos, reverse=True)


def test_presidente_por_uf(client: TSEClient):
    r = client.resultado("SP", "presidente", ano=2022)
    assert r["abrangencia"] == "SP" and r["eleicao"]["codigo"] == "544"


def test_governador_br_invalido(client: TSEClient):
    with pytest.raises(TSEError):
        client.resultado("br", "governador", ano=2022)


def test_arquivo_nao_publicado(client: TSEClient):
    with pytest.raises(tc.NaoEncontrado):
        client.resultado("RJ", "governador", ano=2022)  # rota RJ não está nas fixtures → 404


def test_municipio_sao_paulo_governador_2022(client: TSEClient):
    r = client.municipio("SP", "3550308", "governador", ano=2022)
    assert r["municipio"] == {"uf": "SP", "codigo_tse": "71072", "codigo_ibge": "3550308",
                              "nome": "SÃO PAULO", "capital": True}
    assert r["apurado_pct"] == 100.0 and r["atualizado_em"] == "18/09/2026 19:44:39"
    assert r["totais"]["votos_validos"] == 6321002 and r["totais"]["eleitorado"] == 9301013
    assert r["candidatos"][0]["votos"] >= r["candidatos"][1]["votos"]
    assert all(c["partido"] for c in r["candidatos"])
    # por nome também
    r2 = client.municipio("sp", "São Paulo", "gov", ano=2022)
    assert r2["municipio"]["codigo_tse"] == "71072"
    with pytest.raises(TSEError):
        client.municipio("sp", "Atlântida", "governador", ano=2022)


# ------------------------------------------------------------------ Eleições Gerais 2026 (fixtures reais, formato completo)
def test_resultado_governador_sp_2026_formato_completo(client_2026: TSEClient, fake_2026):
    """1º turno 04/10/2026: o agregado UF só existe em `dados/sp/sp-c0003-e006259-u.json`."""
    r = client_2026.resultado("SP", "governador")  # sem `ano`: resolve 6259 pelo config real
    assert r["eleicao"]["codigo"] == "6259" and r["eleicao"]["ciclo"] == "ele2026" and r["eleicao"]["ano"] == 2026
    assert r["cargo"] == "Governador" and r["abrangencia"] == "SP"
    assert r["url_tse"].endswith("/ele2026/6259/dados/sp/sp-c0003-e006259-u.json")
    assert r["apurado_pct"] == 100.0 and r["atualizado_em"] == "04/10/2026 22:59:12"
    assert r["secoes_totalizadas"] == 103656 and r["secoes_total"] == 103656
    assert r["totais"]["eleitorado"] == 34081699 and r["totais"]["votos_validos"] == 23130513
    assert r["totais"]["brancos"] == 1348262 and r["totais"]["nulos"] == 1915481
    top = r["candidatos"][0]
    assert top["nome"] == "TARCÍSIO" and top["nome_completo"] == "TARCISIO GOMES DE FREITAS"
    assert top["numero"] == "10" and top["partido"] == "REPUBLICANOS"
    assert top["votos"] == 14491874 and top["pct"] == 62.65 and top["situacao"] == "Eleito" and top["eleito"] is True
    assert top["vice"] == "FELICIO RAMUTH"
    assert r["candidatos"][1]["nome"] == "FERNANDO HADDAD" and r["candidatos"][1]["eleito"] is False
    assert r["candidatos"][1]["vice"] == "MÁRCIO FRANÇA"  # nome de urna do vice, não o civil
    assert r["total_candidatos"] == 5
    # formato completo foi o 1º tentado (ano >= 2024): nenhum 404 no caminho
    urls = [str(q.url) for q in fake_2026.requests]
    assert not any("dados-simplificados" in u for u in urls)
    assert "municipio" not in r


def test_resumo_brasil_2026_presidente(client_2026: TSEClient):
    r = client_2026.resumo_brasil()
    assert r["eleicao"]["codigo"] == "6257" and r["abrangencia"] == "BR" and r["cargo"] == "Presidente"
    assert r["atualizado_em"] == "05/10/2026 00:11:37" and r["apurado_pct"] == 99.99
    assert r["matematicamente_definido"] is True  # `md: "s"` (minúsculo em 2026)
    assert r["total_candidatos"] == 12 and len(r["candidatos"]) == 12
    assert r["candidatos"][0]["nome"] == "FLAVIO BOLSONARO" and r["candidatos"][0]["pct"] == 47.03
    assert r["candidatos"][1]["nome"] == "LULA" and r["candidatos"][1]["pct"] == 45.16
    assert all(c["eleito"] is False for c in r["candidatos"])  # ninguém >50%: ninguém eleito no 1º turno
    votos = [c["votos"] for c in r["candidatos"]]
    assert votos == sorted(votos, reverse=True)


def test_resultado_2026_memoriza_formato(client_2026: TSEClient, fake_2026, monkeypatch):
    client_2026.resultado("sp", "governador")
    n = len(fake_2026.requests)
    real = time.monotonic()
    monkeypatch.setattr(tc.time, "monotonic", lambda: real + tc.TTL_DADOS_S + 1)
    client_2026.resultado("sp", "governador")
    assert len(fake_2026.requests) == n + 1  # só o refresh do `-u.json`; nenhum 404 extra
    assert fake_2026.requests[-1].url.path.endswith("sp-c0003-e006259-u.json")


def test_resultado_2022_legado_simplificado_sem_404_extra(client: TSEClient, fake):
    client.resultado("SP", "governador", ano=2022)
    urls = [str(q.url) for q in fake.requests]
    assert urls[-1].endswith("/ele2022/546/dados-simplificados/sp/sp-c0003-e000546-r.json")
    assert not any(u.endswith("sp-c0003-e000546-u.json") for u in urls)  # 2022: simplificado primeiro


def test_fallback_entre_formatos(fake):
    """Se o formato esperado der 404, tenta o outro antes de desistir (e memoriza)."""
    # 2026 só com o simplificado disponível (cenário hipotético de o TSE voltar atrás)
    fake.rotas = {
        f"{tc.BASE_URL}/comum/config/ele-c.json": "ele-c-2026-10-05.json",
        f"{tc.BASE_URL}/ele2026/6259/dados-simplificados/sp/sp-c0003-e006259-r.json": "sp-c0003-e000546-r.json",
    }
    c = TSEClient(transport=fake.transport())
    r = c.resultado("sp", "governador")
    assert r["url_tse"].endswith("-r.json") and r["candidatos"][0]["nome"] == "TARCÍSIO"
    assert c._formato_resultado[("ele2026", "6259", 3, "sp")] == "r"
    # nenhum dos dois: NaoEncontrado lista as duas URLs tentadas
    with pytest.raises(tc.NaoEncontrado) as exc:
        c.resultado("rj", "governador")
    assert "rj-c0003-e006259-u.json" in str(exc.value) and "rj-c0003-e006259-r.json" in str(exc.value)


# ------------------------------------------------------------------ cache / etiqueta HTTP
def test_user_agent_identifica_projeto(client: TSEClient, fake):
    client.config()
    ua = fake.requests[-1].headers["User-Agent"]
    assert ua.startswith("apuracao-2026-mcp/") and "github.com/daniel-filius" in ua


def test_cache_ttl_e_if_modified_since(client: TSEClient, fake, monkeypatch):
    client.resultado("SP", "governador", ano=2022)
    n = len(fake.requests)
    client.resultado("SP", "governador", ano=2022)
    assert len(fake.requests) == n  # dentro do TTL: zero requisições novas
    # avança o relógio além do TTL de dados → revalida com If-Modified-Since, TSE responde 304
    real = time.monotonic()
    monkeypatch.setattr(tc.time, "monotonic", lambda: real + tc.TTL_DADOS_S + 1)
    fake.force_304 = True
    r = client.resultado("SP", "governador", ano=2022)
    assert len(fake.requests) == n + 1
    assert fake.requests[-1].headers["If-Modified-Since"] == fake.last_modified
    assert fake.requests[-1].headers["If-None-Match"] == '"abc"'
    assert r["candidatos"][0]["nome"] == "TARCÍSIO"  # dado do cache preservado após 304


def test_ttl_minimo_30s():
    assert tc.TTL_DADOS_S >= 30
