from __future__ import annotations

import json

import pytest

from apuracao_mcp import bot
from apuracao_mcp.tse_client import TSEClient, TSEError


def test_parse_consulta():
    assert bot.parse_consulta("SP governador") == ("sp", "governador")
    assert bot.parse_consulta("governador sp") == ("sp", "governador")
    assert bot.parse_consulta("br") == ("br", "presidente")
    assert bot.parse_consulta("presidente") == ("br", "presidente")
    assert bot.parse_consulta("RJ") == ("rj", "governador")
    assert bot.parse_consulta("mg senador") == ("mg", "senador")
    with pytest.raises(TSEError):
        bot.parse_consulta("")
    with pytest.raises(TSEError):
        bot.parse_consulta("banana")


def test_formatar_card(client: TSEClient):
    r = client.resultado("SP", "governador", ano=2022)
    card = bot.formatar_card(r)
    assert "Governador · SP" in card and "100,00% apurado" in card and "TSE 10/04/2023 14:29:47" in card
    assert "1º TARCÍSIO (REPUBLICANOS) — <b>42,32%</b> · 9.881.995 votos" in card
    assert "Sem projeção" in card and "matematicamente definido" in card
    assert bot.formatar_card({"erro": "x"}).startswith("⚠️")


def test_inline_results_e_erro(client: TSEClient):
    # sem ano o config atual não tem 2026 → o card explica, sem quebrar
    res = bot.montar_inline_results("SP governador", client)
    assert len(res) == 1 and res[0]["type"] == "article"
    assert res[0]["input_message_content"]["parse_mode"] == "HTML"
    assert "não foi possível" in res[0]["title"] or "apurado" in res[0]["title"]
    assert len(res[0]["id"]) <= 64


def test_metricas_hasheadas(tmp_path):
    p = tmp_path / "m.jsonl"
    upd = {"inline_query": {"id": "1", "from": {"id": 12345}, "query": "SP governador"}}
    rec = bot.registrar_metrica("inline_query", upd, {"q": "SP governador"}, path=p)
    assert rec["user"] and "12345" not in rec["user"] and len(rec["user"]) == 12
    upd2 = {"message": {"from": {"id": 1}, "chat": {"id": -100}, "text": "/start"}}
    bot.registrar_metrica("message", upd2, path=p)
    linhas = [json.loads(l) for l in p.read_text().splitlines()]
    assert len(linhas) == 2 and linhas[1]["chat"] and linhas[1]["tipo"] == "message"


def test_token_dormente(monkeypatch, tmp_path):
    monkeypatch.delenv(bot.TOKEN_VAR, raising=False)
    monkeypatch.delenv("APURACAO_ENV_FILE", raising=False)
    assert bot.carregar_token() is None
    assert bot.main() == 0  # sem token: sai limpo, sem rede
    env = tmp_path / ".env"
    env.write_text("APURACAO_BOT_TOKEN_OUTRO=nao\nAPURACAO_BOT_TOKEN=\"123:abc\"\n")
    monkeypatch.setenv("APURACAO_ENV_FILE", str(env))
    assert bot.carregar_token() == "123:abc"  # match exato, não prefixo


def test_tratar_update_inline(client: TSEClient, tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "METRICS_PATH", tmp_path / "m.jsonl")
    chamadas = []

    class TG:
        def call(self, method, **kw):
            chamadas.append((method, kw))

    bot.tratar_update({"inline_query": {"id": "q1", "from": {"id": 7}, "query": "br"}}, TG(), client)
    assert chamadas[0][0] == "answerInlineQuery" and chamadas[0][1]["inline_query_id"] == "q1"
    bot.tratar_update({"message": {"chat": {"id": 5}, "from": {"id": 7}, "text": "/start"}}, TG(), client)
    assert chamadas[1][0] == "sendMessage" and "Apuração" in chamadas[1][1]["text"]
    linhas = (tmp_path / "m.jsonl").read_text().splitlines()
    assert len(linhas) == 2 and "7" not in json.loads(linhas[0])["user"][:0]  # métricas gravadas, ids hasheados
