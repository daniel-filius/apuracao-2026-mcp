"""Bot Telegram inline — Apuração Eleições 2026 (TSE).

DORMENTE por padrão: só sobe se a variável `APURACAO_BOT_TOKEN` estiver definida
(ambiente ou arquivo apontado por `APURACAO_ENV_FILE`). Sem token, `main()`
imprime um aviso e sai com código 0 — nada de erro, nada de retry.

Uso em grupo (modo inline, ativar no @BotFather com /setinline):
    @Apuracao2026Bot SP governador
    @Apuracao2026Bot br            (presidente, total nacional)
Comandos: /start, /br, /uf SP [cargo]

Métricas anônimas em `metrics.jsonl` (ids hasheados com SHA-256 truncado):
inline_query, chosen_inline_result, message, com `chat`/`user` hasheados.

Somente stdlib + tse_client (sem dependência de framework de bot).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .tse_client import CARGO_NOMES, CARGOS, TSEClient, TSEError, normalizar_cargo, normalizar_uf

TOKEN_VAR = "APURACAO_BOT_TOKEN"
METRICS_PATH = Path(os.environ.get("APURACAO_METRICS_PATH", "metrics.jsonl"))
PIX_LINK = "https://github.com/daniel-filius/apuracao-2026-mcp#apoie"
_EMOJI_CARGO = {1: "🇧🇷", 3: "🏛", 5: "🏛", 6: "📜", 7: "📜", 8: "📜", 11: "🏙", 13: "🏙"}


# ------------------------------------------------------------------ token
def carregar_token() -> str | None:
    """Lê APURACAO_BOT_TOKEN do ambiente ou de um .env (linha exata). Nunca imprime o valor."""
    tok = os.environ.get(TOKEN_VAR)
    if tok:
        return tok.strip()
    env_file = os.environ.get("APURACAO_ENV_FILE")
    if env_file and Path(env_file).is_file():
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{TOKEN_VAR}="):  # nome completo + '=' — sem prefix-match solto
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                return value or None
    return None


# ------------------------------------------------------------------ parsing / formatação
_UF_RE = re.compile(r"\b([a-z]{2})\b")


def parse_consulta(texto: str) -> tuple[str, str]:
    """'SP governador' | 'governador sp' | 'br' | 'presidente' → (uf, cargo). Levanta TSEError."""
    t = (texto or "").strip().lower()
    if not t:
        raise TSEError("digite UF e cargo, ex.: `SP governador` ou `br`")
    tokens = t.replace(",", " ").split()
    uf, cargo = None, None
    for tok in tokens:
        if len(tok) == 2 and tok.isalpha():
            try:
                uf = normalizar_uf(tok)
                continue
            except TSEError:
                pass
        try:
            normalizar_cargo(tok)
            cargo = tok
        except TSEError:
            pass
    if uf is None and cargo is None:
        # tenta "deputado federal" (duas palavras)
        try:
            normalizar_cargo(t)
            cargo = t
        except TSEError:
            raise TSEError("não entendi. Use `UF cargo`, ex.: `SP governador`, `RJ senador`, `br`.")
    if cargo is None:
        cargo = "presidente" if uf == "br" else "governador"
    if uf is None:
        uf = "br" if normalizar_cargo(cargo) == 1 else None
    if uf is None:
        raise TSEError("informe a UF, ex.: `SP governador`.")
    return uf, cargo


def _fmt_pct(v: float) -> str:
    return f"{v:.2f}".replace(".", ",") + "%"


def _fmt_int(v: int) -> str:
    return f"{v:,}".replace(",", ".")


def formatar_card(r: dict[str, Any], top: int = 5) -> str:
    """Card textual (HTML do Telegram) com o parcial oficial — números + carimbo do TSE, sem comentário."""
    if "erro" in r:
        return f"⚠️ {r['erro']}"
    cargo = r.get("cargo", "")
    emoji = _EMOJI_CARGO.get(CARGOS.get(cargo.lower().replace(" ", "_"), 0), "🗳")
    el = r.get("eleicao", {})
    turno = f"{el.get('turno')}º turno" if el.get("turno") else ""
    linhas = [
        f"🗳 <b>{cargo} · {r.get('abrangencia')}</b> {emoji} {el.get('ano', '')} {turno}".strip(),
        f"<b>{_fmt_pct(r.get('apurado_pct', 0.0))} apurado</b> · TSE {r.get('atualizado_em', '')}"
        + (" · ✅ matematicamente definido" if r.get("matematicamente_definido") else ""),
        "",
    ]
    for c in r.get("candidatos", [])[:top]:
        marca = " ✅" if c.get("eleito") else ""
        partido = f" ({c['partido']})" if c.get("partido") else ""
        linhas.append(f"{c['posicao']}º {c['nome']}{partido} — <b>{_fmt_pct(c['pct'])}</b> · {_fmt_int(c['votos'])} votos{marca}")
    if r.get("total_candidatos", 0) > top:
        linhas.append(f"… +{r['total_candidatos'] - top} candidatos")
    linhas += ["", "<i>Fonte: TSE – divulgação oficial, resultado parcial. Sem projeção.</i>"]
    return "\n".join(linhas)


def montar_inline_results(texto: str, client: TSEClient) -> list[dict[str, Any]]:
    try:
        uf, cargo = parse_consulta(texto)
        r = client.resultado(uf, cargo)
    except TSEError as exc:
        r = {"erro": str(exc)}
    card = formatar_card(r)
    titulo = (f"{r.get('cargo')} · {r.get('abrangencia')} · {_fmt_pct(r.get('apurado_pct', 0.0))} apurado"
              if "erro" not in r else "Apuração TSE — não foi possível")
    desc = (" · ".join(f"{c['nome']} {_fmt_pct(c['pct'])}" for c in r.get("candidatos", [])[:3])
            if "erro" not in r else r["erro"][:120])
    rid = hashlib.sha1(f"{texto}|{r.get('atualizado_em')}".encode()).hexdigest()[:32]
    return [{
        "type": "article",
        "id": rid,
        "title": titulo[:64],
        "description": desc[:120],
        "input_message_content": {"message_text": card, "parse_mode": "HTML", "disable_web_page_preview": True},
    }]


def texto_start() -> str:
    return (
        "🗳 <b>Apuração Eleições 2026 (TSE)</b>\n"
        "Resultados oficiais parciais, direto da divulgação do TSE.\n\n"
        "Em qualquer grupo digite <code>@Apuracao2026Bot SP governador</code> ou <code>@Apuracao2026Bot br</code>.\n"
        "Aqui: /br (presidente), /uf SP [cargo]\n"
        f"Cargos: {', '.join(CARGOS)}\n\n"
        f"Projeto voluntário e sem fins políticos — código aberto e apoio via Pix em {PIX_LINK}"
    )


# ------------------------------------------------------------------ métricas
def _hash(v: Any) -> str:
    return hashlib.sha256(str(v).encode()).hexdigest()[:12]


def registrar_metrica(tipo: str, update: dict[str, Any], extra: dict[str, Any] | None = None,
                      path: Path | None = None) -> dict[str, Any]:
    path = path or METRICS_PATH
    src = update.get("inline_query") or update.get("chosen_inline_result") or update.get("message") or {}
    user = (src.get("from") or {}).get("id")
    chat = (src.get("chat") or {}).get("id")
    rec = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "tipo": tipo,
           "user": _hash(user) if user else None, "chat": _hash(chat) if chat else None}
    if extra:
        rec.update(extra)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# ------------------------------------------------------------------ Telegram API (stdlib)
class Telegram:
    def __init__(self, token: str):
        self._base = f"https://api.telegram.org/bot{token}/"

    def call(self, method: str, **params: Any) -> Any:
        data = urllib.parse.urlencode({k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                                       for k, v in params.items() if v is not None}).encode()
        req = urllib.request.Request(self._base + method, data=data)
        with urllib.request.urlopen(req, timeout=40) as resp:
            body = json.loads(resp.read().decode())
        if not body.get("ok"):
            raise RuntimeError(f"telegram {method}: {body.get('description')}")
        return body.get("result")


def tratar_update(update: dict[str, Any], tg: Telegram, client: TSEClient) -> None:
    if "inline_query" in update:
        q = update["inline_query"]
        registrar_metrica("inline_query", update, {"q": (q.get("query") or "")[:40]})
        results = montar_inline_results(q.get("query", ""), client)
        tg.call("answerInlineQuery", inline_query_id=q["id"], results=results, cache_time=30, is_personal=False)
        return
    if "chosen_inline_result" in update:
        registrar_metrica("chosen_inline_result", update, {"q": (update["chosen_inline_result"].get("query") or "")[:40]})
        return
    msg = update.get("message")
    if not msg or not msg.get("text"):
        return
    registrar_metrica("message", update)
    text = msg["text"].strip()
    chat_id = msg["chat"]["id"]
    if text.startswith("/start") or text.startswith("/help"):
        tg.call("sendMessage", chat_id=chat_id, text=texto_start(), parse_mode="HTML", disable_web_page_preview=True)
    elif text.startswith("/br"):
        tg.call("sendMessage", chat_id=chat_id, text=formatar_card(_res(client, "br presidente")), parse_mode="HTML")
    elif text.startswith("/uf"):
        tg.call("sendMessage", chat_id=chat_id, text=formatar_card(_res(client, text[3:])), parse_mode="HTML")


def _res(client: TSEClient, texto: str) -> dict[str, Any]:
    try:
        uf, cargo = parse_consulta(texto)
        return client.resultado(uf, cargo)
    except TSEError as exc:
        return {"erro": str(exc)}


def loop(tg: Telegram, client: TSEClient, offset_path: Path = Path(".apuracao_bot_offset.json")) -> None:
    offset = 0
    if offset_path.is_file():
        try:
            offset = int(json.loads(offset_path.read_text())["offset"])
        except Exception:
            offset = 0
    print(f"[apuracao-bot] long polling iniciado (offset={offset})", file=sys.stderr)
    while True:
        try:
            updates = tg.call("getUpdates", offset=offset, timeout=25,
                              allowed_updates=["message", "inline_query", "chosen_inline_result"])
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            print(f"[apuracao-bot] getUpdates falhou: {exc}", file=sys.stderr)
            time.sleep(5)
            continue
        for up in updates or []:
            offset = max(offset, up["update_id"] + 1)
            try:
                tratar_update(up, tg, client)
            except Exception as exc:  # nunca derruba o loop por 1 update
                print(f"[apuracao-bot] erro no update {up.get('update_id')}: {exc}", file=sys.stderr)
        offset_path.write_text(json.dumps({"offset": offset}))


def main() -> int:
    token = carregar_token()
    if not token:
        print(f"[apuracao-bot] dormente: {TOKEN_VAR} não definido (ambiente ou APURACAO_ENV_FILE). Saindo.",
              file=sys.stderr)
        return 0
    tg = Telegram(token)
    me = tg.call("getMe")
    print(f"[apuracao-bot] conectado como @{me.get('username')}", file=sys.stderr)
    loop(tg, TSEClient())
    return 0


if __name__ == "__main__":
    sys.exit(main())
