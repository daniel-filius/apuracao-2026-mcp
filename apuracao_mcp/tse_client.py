"""Cliente da divulgação oficial de resultados do TSE (resultados.tse.jus.br).

Fonte: arquivos JSON públicos, sem chave e sem cadastro, publicados pelo TSE em
`https://resultados.tse.jus.br/oficial/...`. Este módulo:

- lê o config canônico `comum/config/ele-c.json` e resolve dinamicamente
  ciclo / código da eleição / cargos por abrangência (os códigos mudam a cada pleito);
- monta as URLs dos resultados. Desde as Eleições Gerais 2026 o TSE publica o
  agregado UF/BR no formato *completo* `dados/<uf>/<uf>-c<cargo>-e<eleicao>-u.json`
  (mesmo esquema `carg/agr/par/cand` do arquivo por município
  `dados/<uf>/<uf><cod_mun>-c<cargo>-e<eleicao>-u.json`); o formato *simplificado*
  `dados-simplificados/<uf>/<uf>-c<cargo>-e<eleicao>-r.json` (2022) não existe mais
  no portal para 2026 (NoSuchKey) e fica como legado. O cliente tenta o formato
  esperado para o ano, cai para o outro em 404 e memoriza o que funcionou;
- faz cache por URL (>= 45 s) com `If-Modified-Since` / `If-None-Match` e um
  User-Agent que identifica o projeto, para não sobrecarregar o TSE;
- normaliza a resposta para um dicionário estável, sempre com `fonte` e
  `atualizado_em` (carimbo do próprio TSE).

Neutralidade: só números oficiais. Nenhuma projeção, nenhuma interpretação.
"""

from __future__ import annotations

import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from . import __version__

BASE_URL = os.environ.get("TSE_BASE_URL", "https://resultados.tse.jus.br/oficial").rstrip("/")
CONFIG_URL = f"{BASE_URL}/comum/config/ele-c.json"
USER_AGENT = (
    f"apuracao-2026-mcp/{__version__} "
    "(+https://github.com/daniel-filius/apuracao-2026-mcp; projeto voluntario, cache >=45s)"
)
FONTE = "TSE – divulgação oficial, resultado parcial"

TTL_DADOS_S = 45  # resultados: nunca menos que 30 s por arquivo
TTL_CONFIG_S = 300
TTL_MUNICIPIOS_S = 3600

CARGOS: dict[str, int] = {
    "presidente": 1,
    "governador": 3,
    "senador": 5,
    "deputado_federal": 6,
    "deputado_estadual": 7,
    "deputado_distrital": 8,
    "prefeito": 11,
    "vereador": 13,
}
CARGO_NOMES: dict[int, str] = {
    1: "Presidente",
    3: "Governador",
    5: "Senador",
    6: "Deputado Federal",
    7: "Deputado Estadual",
    8: "Deputado Distrital",
    11: "Prefeito",
    13: "Vereador",
}
_ALIASES_CARGO = {
    "pres": "presidente",
    "presidencia": "presidente",
    "gov": "governador",
    "governadora": "governador",
    "sen": "senador",
    "senadora": "senador",
    "depfed": "deputado_federal",
    "deputado-federal": "deputado_federal",
    "deputado federal": "deputado_federal",
    "federal": "deputado_federal",
    "depest": "deputado_estadual",
    "deputado-estadual": "deputado_estadual",
    "deputado estadual": "deputado_estadual",
    "estadual": "deputado_estadual",
    "deputado distrital": "deputado_distrital",
    "distrital": "deputado_distrital",
    "prefeita": "prefeito",
    "vereadora": "vereador",
}
UFS = {
    "ac", "al", "am", "ap", "ba", "ce", "df", "es", "go", "ma", "mg", "ms", "mt",
    "pa", "pb", "pe", "pi", "pr", "rj", "rn", "ro", "rr", "rs", "sc", "se", "sp", "to",
}

# Eleições Gerais de 2022 — os arquivos continuam publicados pelo TSE e servem
# como dados históricos/demonstração enquanto o config não lista 2026.
# cargo presidente -> eleição "federal"; demais cargos -> eleição "estadual".
ELEICOES_CONHECIDAS: dict[tuple[int, int], dict[str, Any]] = {
    (2022, 1): {"ciclo": "ele2022", "dt": "02/10/2022", "federal": "544", "estadual": "546",
                "nome": "Eleições Gerais 2022 – 1º turno"},
    (2022, 2): {"ciclo": "ele2022", "dt": "30/10/2022", "federal": "545", "estadual": "547",
                "nome": "Eleições Gerais 2022 – 2º turno"},
}


class TSEError(Exception):
    """Erro de uso/dados do cliente TSE (mensagem já em pt-BR, segura para o usuário)."""


class NaoEncontrado(TSEError):
    """Arquivo não publicado pelo TSE (HTTP 404)."""


@dataclass
class Eleicao:
    ano: int
    turno: int
    ciclo: str
    codigo: str
    data: str
    nome: str
    abrangencia: str  # "br" ou uf


@dataclass
class _Entry:
    data: Any
    fetched_at: float
    last_modified: str | None
    etag: str | None


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalizar_cargo(cargo: str) -> int:
    key = _sem_acento(str(cargo)).strip().lower().replace("_", " ")
    key = _ALIASES_CARGO.get(key, key).replace(" ", "_")
    if key.isdigit() and int(key) in CARGO_NOMES:
        return int(key)
    if key not in CARGOS:
        raise TSEError(f"cargo inválido: {cargo!r}. Use um de: {', '.join(CARGOS)}")
    return CARGOS[key]


def normalizar_uf(uf: str) -> str:
    u = str(uf).strip().lower()
    if u in ("", "brasil", "br", "nacional"):
        return "br"
    if u not in UFS:
        raise TSEError(f"UF inválida: {uf!r}. Use a sigla (SP, RJ, ...) ou BR para o total nacional.")
    return u


def _int(s: Any) -> int:
    try:
        return int(str(s).replace(".", "")) if s not in (None, "") else 0
    except ValueError:
        return 0


def _pct(s: Any) -> float:
    try:
        return float(str(s).replace(".", "").replace(",", ".")) if s not in (None, "") else 0.0
    except ValueError:
        return 0.0


def _partido(cc: str | None) -> str:
    return (cc or "").split(" - ")[0].strip()


def _parse_dt(s: str) -> date:
    return datetime.strptime(s, "%d/%m/%Y").date()


class TSEClient:
    """Cliente síncrono com cache; seguro para uso a partir de várias threads."""

    def __init__(self, transport: httpx.BaseTransport | None = None, timeout: float = 15.0):
        self._http = httpx.Client(
            transport=transport,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )
        self._cache: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        self.requests_feitas = 0  # instrumentação (testes / métricas)
        # (ciclo, codigo, cargo, uf) -> "u" (completo) | "r" (simplificado): formato que o TSE
        # realmente serve para este agregado, descoberto no 1º acesso (evita 404 a cada refresh).
        self._formato_resultado: dict[tuple[str, str, int, str], str] = {}

    # ------------------------------------------------------------------ HTTP
    def _get_json(self, url: str, ttl: float) -> Any:
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(url)
            if entry and now - entry.fetched_at < ttl:
                return entry.data
        headers: dict[str, str] = {}
        if entry:
            if entry.last_modified:
                headers["If-Modified-Since"] = entry.last_modified
            if entry.etag:
                headers["If-None-Match"] = entry.etag
        self.requests_feitas += 1
        resp = self._http.get(url, headers=headers)
        if resp.status_code == 304 and entry:
            with self._lock:
                entry.fetched_at = time.monotonic()
            return entry.data
        if resp.status_code == 404:
            raise NaoEncontrado(f"o TSE ainda não publicou este arquivo: {url}")
        resp.raise_for_status()
        data = resp.json()
        with self._lock:
            self._cache[url] = _Entry(
                data=data,
                fetched_at=time.monotonic(),
                last_modified=resp.headers.get("Last-Modified"),
                etag=resp.headers.get("ETag"),
            )
        return data

    # ---------------------------------------------------------------- config
    def config(self) -> dict[str, Any]:
        return self._get_json(CONFIG_URL, TTL_CONFIG_S)

    def eleicoes_ordinarias(self) -> list[dict[str, Any]]:
        """Eleições ordinárias (não suplementares, não consultas) presentes no config."""
        cfg = self.config()
        out: list[dict[str, Any]] = []
        for pleito in cfg.get("pl", []):
            dt = pleito.get("dt", "")
            ano = int(dt[-4:]) if len(dt) == 10 else None
            for e in pleito.get("e", []):
                nome = _sem_acento(e.get("nm", ""))
                if "ordin" not in nome.lower():
                    continue
                abrangencias = []
                for abr in e.get("abr", []):
                    abrangencias.append({
                        "abrangencia": abr.get("cd", "").lower(),
                        "cargos": [
                            {"codigo": int(cp["cd"]), "nome": cp.get("ds")}
                            for cp in abr.get("cp", []) if str(cp.get("cd", "")).isdigit()
                        ],
                    })
                out.append({
                    "ano": ano,
                    "turno": int(e.get("t") or 1),
                    "ciclo": f"ele{ano}" if ano else cfg.get("c"),
                    "codigo": str(e.get("cd")),
                    "codigo_2o_turno": str(e.get("cdt2") or "") or None,
                    "data": dt,
                    "nome": e.get("nm", "").replace("&#186;", "º"),
                    "abrangencias": abrangencias,
                })
        return out

    def resolver_eleicao(self, cargo_cd: int, uf: str, turno: int = 1, ano: int | None = None) -> Eleicao:
        """Descobre (ciclo, código da eleição) para um cargo/UF/turno.

        Ordem: config oficial do TSE (dinâmico) → tabela de 2022 (histórico).
        Sem `ano`, usa a eleição ordinária mais próxima de hoje. Se o TSE ainda
        não publicou o config do pleito pedido, levanta TSEError explicando.
        """
        uf = normalizar_uf(uf)
        hoje = date.today()
        candidatos: list[tuple[int, Eleicao]] = []
        for e in self.eleicoes_ordinarias():
            if e["turno"] != turno or (ano is not None and e["ano"] != ano):
                continue
            for abr in e["abrangencias"]:
                if cargo_cd in [c["codigo"] for c in abr["cargos"]] and abr["abrangencia"] in ("br", uf):
                    dist = abs((_parse_dt(e["data"]) - hoje).days)
                    candidatos.append((dist, Eleicao(
                        ano=e["ano"], turno=turno, ciclo=e["ciclo"], codigo=e["codigo"],
                        data=e["data"], nome=e["nome"], abrangencia=abr["abrangencia"],
                    )))
        if candidatos:
            candidatos.sort(key=lambda x: x[0])
            return candidatos[0][1]

        conhecida = ELEICOES_CONHECIDAS.get((ano or 0, turno))
        if conhecida:
            codigo = conhecida["federal"] if cargo_cd == 1 else conhecida["estadual"]
            return Eleicao(ano=ano, turno=turno, ciclo=conhecida["ciclo"], codigo=codigo,
                           data=conhecida["dt"], nome=conhecida["nome"],
                           abrangencia="br" if cargo_cd == 1 else uf)

        cfg = self.config()
        nome_cargo = CARGO_NOMES.get(cargo_cd, str(cargo_cd))
        alvo = f"de {ano}" if ano else "atual (Eleições Gerais 2026)"
        raise TSEError(
            f"O TSE ainda não publicou no config oficial (ciclo '{cfg.get('c')}', gerado em "
            f"{cfg.get('dg')} {cfg.get('hg')}) uma eleição ordinária {alvo} com o cargo {nome_cargo} "
            f"para '{uf.upper()}' no {turno}º turno. A configuração costuma aparecer perto do dia "
            "da votação. Para dados históricos use ano=2022 (Eleições Gerais) ou ano=2024 (municipais)."
        )

    # ------------------------------------------------------------- resultados
    def url_resultado(self, el: Eleicao, cargo_cd: int, uf: str) -> str:
        """Agregado UF/BR no formato *simplificado* (`-r.json`, usado até 2022)."""
        uf = normalizar_uf(uf)
        return (f"{BASE_URL}/{el.ciclo}/{el.codigo}/dados-simplificados/{uf}/"
                f"{uf}-c{cargo_cd:04d}-e{int(el.codigo):06d}-r.json")

    def url_resultado_completo(self, el: Eleicao, cargo_cd: int, uf: str) -> str:
        """Agregado UF/BR no formato *completo* (`-u.json`, o que o TSE serve em 2026)."""
        uf = normalizar_uf(uf)
        return (f"{BASE_URL}/{el.ciclo}/{el.codigo}/dados/{uf}/"
                f"{uf}-c{cargo_cd:04d}-e{int(el.codigo):06d}-u.json")

    def _ordem_formatos(self, el: Eleicao, cargo_cd: int, uf: str) -> list[str]:
        memorizado = self._formato_resultado.get((el.ciclo, el.codigo, cargo_cd, uf))
        if memorizado:
            return [memorizado]
        # 2024 em diante o portal só publica o completo; a tabela histórica de 2022 era simplificado.
        return ["u", "r"] if (el.ano or 0) >= 2024 else ["r", "u"]

    def _buscar_resultado(self, el: Eleicao, cargo_cd: int, uf: str, limite: int) -> dict[str, Any]:
        tentadas: list[str] = []
        for fmt in self._ordem_formatos(el, cargo_cd, uf):
            url = (self.url_resultado_completo if fmt == "u" else self.url_resultado)(el, cargo_cd, uf)
            try:
                raw = self._get_json(url, TTL_DADOS_S)
            except NaoEncontrado:
                tentadas.append(url)
                continue
            self._formato_resultado[(el.ciclo, el.codigo, cargo_cd, uf)] = fmt
            if fmt == "u":
                return normalizar_completo(raw, el, cargo_cd, uf.upper(), url, limite)
            return normalizar_simplificado(raw, el, cargo_cd, uf, url, limite)
        raise NaoEncontrado("o TSE ainda não publicou este arquivo: " + " | ".join(tentadas))

    def url_municipio(self, el: Eleicao, cargo_cd: int, uf: str, cod_tse: str) -> str:
        uf = normalizar_uf(uf)
        return (f"{BASE_URL}/{el.ciclo}/{el.codigo}/dados/{uf}/"
                f"{uf}{cod_tse}-c{cargo_cd:04d}-e{int(el.codigo):06d}-u.json")

    def url_municipios_config(self, el: Eleicao) -> str:
        return f"{BASE_URL}/{el.ciclo}/{el.codigo}/config/mun-e{int(el.codigo):06d}-cm.json"

    def resultado(self, uf: str, cargo: str, turno: int = 1, ano: int | None = None,
                  limite: int = 20) -> dict[str, Any]:
        cargo_cd = normalizar_cargo(cargo)
        uf = normalizar_uf(uf)
        if cargo_cd != 1 and uf == "br":
            raise TSEError("Só o cargo 'presidente' tem total nacional (BR); para os demais informe a UF.")
        el = self.resolver_eleicao(cargo_cd, uf, turno, ano)
        return self._buscar_resultado(el, cargo_cd, uf, limite)

    def resumo_brasil(self, turno: int = 1, ano: int | None = None, limite: int = 20) -> dict[str, Any]:
        return self.resultado("br", "presidente", turno=turno, ano=ano, limite=limite)

    def buscar_municipio(self, el: Eleicao, uf: str, municipio: str) -> dict[str, Any]:
        """Localiza município por código IBGE (7 dígitos), código TSE (5) ou nome."""
        uf = normalizar_uf(uf)
        cfg = self._get_json(self.url_municipios_config(el), TTL_MUNICIPIOS_S)
        chave = _sem_acento(str(municipio)).strip().lower()
        for abr in cfg.get("abr", []):
            if abr.get("cd", "").lower() != uf:
                continue
            for mu in abr.get("mu", []):
                if chave in (mu.get("cdi", "").lower(), mu.get("cd", "").lower()) or \
                        _sem_acento(mu.get("nm", "")).lower() == chave:
                    return {"uf": uf.upper(), "codigo_tse": mu["cd"], "codigo_ibge": mu.get("cdi"),
                            "nome": mu.get("nm"), "capital": mu.get("c") == "S"}
        raise TSEError(f"município {municipio!r} não encontrado em {uf.upper()} "
                       f"(informe nome exato, código IBGE de 7 dígitos ou código TSE).")

    def municipio(self, uf: str, municipio: str, cargo: str, turno: int = 1,
                  ano: int | None = None, limite: int = 20) -> dict[str, Any]:
        cargo_cd = normalizar_cargo(cargo)
        uf = normalizar_uf(uf)
        if uf == "br":
            raise TSEError("informe a UF do município.")
        el = self.resolver_eleicao(cargo_cd, uf, turno, ano)
        mun = self.buscar_municipio(el, uf, municipio)
        url = self.url_municipio(el, cargo_cd, uf, mun["codigo_tse"])
        raw = self._get_json(url, TTL_DADOS_S)
        return normalizar_municipio(raw, el, cargo_cd, mun, url, limite)


# ------------------------------------------------------------------ normalização
def _carimbo(raw: dict[str, Any]) -> str:
    return f"{raw.get('dg', '')} {raw.get('hg', '')}".strip()


def _candidato(c: dict[str, Any], pos: int) -> dict[str, Any]:
    return {
        "posicao": pos,
        # Formato completo (2026) traz `nm` = nome civil e `nmu` = nome de urna; o simplificado
        # (2022) só `nm` já com o nome de urna. Exibimos sempre o nome de urna, como o TSE.
        "nome": c.get("nmu") or c.get("nm"),
        "nome_completo": c.get("nm") if c.get("nmu") else None,
        "numero": c.get("n"),
        "partido": _partido(c.get("cc")) or c.get("sgp") or None,
        "coligacao": c.get("cc") or None,
        "vice": c.get("nv") or None,
        "votos": _int(c.get("vap")),
        "pct": _pct(c.get("pvap")),
        "situacao": c.get("st") or None,
        # `st` é a situação oficial ("Eleito", "Eleito por QP", "2º turno", "Não eleito"...).
        # O flag `e` do TSE também marca quem vai ao 2º turno, por isso não serve como "eleito".
        "eleito": _sem_acento(c.get("st") or "").lower().startswith("eleit"),
    }


def _cabecalho(el: Eleicao, cargo_cd: int, abrangencia: str, raw: dict[str, Any], url: str) -> dict[str, Any]:
    return {
        "fonte": FONTE,
        "aviso": "Resultado parcial da apuração oficial. Percentuais e horário são os publicados pelo TSE "
                 "(horário de Brasília). Sem projeção.",
        "atualizado_em": _carimbo(raw),
        "consultado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
        "eleicao": {"ano": el.ano, "turno": el.turno, "codigo": el.codigo, "ciclo": el.ciclo,
                    "data": el.data, "nome": el.nome},
        "cargo": CARGO_NOMES.get(cargo_cd, str(cargo_cd)),
        "abrangencia": abrangencia,
        "url_tse": url,
    }


def normalizar_simplificado(raw: dict[str, Any], el: Eleicao, cargo_cd: int, uf: str,
                            url: str, limite: int = 20) -> dict[str, Any]:
    cands = sorted(raw.get("cand", []), key=lambda c: -_int(c.get("vap")))
    out = _cabecalho(el, cargo_cd, uf.upper(), raw, url)
    out.update({
        "apurado_pct": _pct(raw.get("pst")),
        "secoes_totalizadas": _int(raw.get("st")),
        "secoes_total": _int(raw.get("s")),
        "matematicamente_definido": _md(raw),
        "totais": {
            "eleitorado": _int(raw.get("e")),
            "comparecimento": _int(raw.get("c")),
            "comparecimento_pct": _pct(raw.get("pc")),
            "abstencao_pct": _pct(raw.get("pa")),
            "votos_validos": _int(raw.get("vv")),
            "brancos": _int(raw.get("vb")),
            "brancos_pct": _pct(raw.get("pvb")),
            "nulos": _int(raw.get("tvn")),
            "nulos_pct": _pct(raw.get("ptvn")),
        },
        "total_candidatos": len(cands),
        "candidatos": [_candidato(c, i + 1) for i, c in enumerate(cands[:limite])],
    })
    return out


def _md(raw: dict[str, Any]) -> bool:
    # `md` = matematicamente definido; o TSE já publicou "S" (2022) e "s" (2026).
    return str(raw.get("md") or "").strip().lower() == "s"


def normalizar_completo(raw: dict[str, Any], el: Eleicao, cargo_cd: int, abrangencia: str,
                        url: str, limite: int = 20, mun: dict[str, Any] | None = None) -> dict[str, Any]:
    """Formato completo do TSE (`-u.json`, blocos `carg/agr/par/cand` + `s`/`e`/`v`).

    Em 2026 é o único formato publicado, tanto para o agregado UF/BR quanto por município.
    """
    cands: list[dict[str, Any]] = []
    for carg in raw.get("carg", []):
        if str(carg.get("cd")) != str(cargo_cd):
            continue
        for agr in carg.get("agr", []):
            for par in agr.get("par", []):
                for c in par.get("cand", []):
                    c = dict(c)
                    c.setdefault("sgp", par.get("sg"))
                    c["cc"] = c.get("cc") or (f"{par.get('sg')} - {agr.get('com')}" if agr.get("com") else par.get("sg"))
                    vices = [v.get("nmu") or v.get("nm") for v in c.get("vs", []) if v.get("tp") == "v"]
                    c["nv"] = vices[0] if vices else None
                    cands.append(c)
    cands.sort(key=lambda c: -_int(c.get("vap")))
    s, e, v = raw.get("s") or {}, raw.get("e") or {}, raw.get("v") or {}
    out = _cabecalho(el, cargo_cd, abrangencia, raw, url)
    if mun is not None:
        out["municipio"] = mun
    out.update({
        "apurado_pct": _pct(s.get("pst")),
        "secoes_totalizadas": _int(s.get("st")),
        "secoes_total": _int(s.get("ts")),
        "matematicamente_definido": _md(raw),
        "totais": {
            "eleitorado": _int(e.get("te")),
            "comparecimento": _int(e.get("c")),
            "comparecimento_pct": _pct(e.get("pc")),
            "abstencao_pct": _pct(e.get("pa")),
            "votos_validos": _int(v.get("vv")),
            "brancos": _int(v.get("vb")),
            "brancos_pct": _pct(v.get("pvb")),
            "nulos": _int(v.get("tvn")),
            "nulos_pct": _pct(v.get("ptvn")),
        },
        "total_candidatos": len(cands),
        "candidatos": [_candidato(c, i + 1) for i, c in enumerate(cands[:limite])],
    })
    return out


def normalizar_municipio(raw: dict[str, Any], el: Eleicao, cargo_cd: int, mun: dict[str, Any],
                         url: str, limite: int = 20) -> dict[str, Any]:
    return normalizar_completo(raw, el, cargo_cd, f"{mun['uf']} / {mun['nome']}", url, limite, mun=mun)


_FILE_RE = re.compile(r"^(?P<uf>[a-z]{2})(?P<mun>\d{5})?-c(?P<cargo>\d{4})-e(?P<ele>\d{6})-(?P<tipo>[ru])\.json$")


def parse_nome_arquivo(nome: str) -> dict[str, Any] | None:
    """Decodifica nomes como `sp-c0003-e000546-r.json` (útil em debug/testes)."""
    m = _FILE_RE.match(nome)
    if not m:
        return None
    d = m.groupdict()
    return {"uf": d["uf"], "municipio": d["mun"], "cargo": int(d["cargo"]), "eleicao": int(d["ele"]),
            "tipo": d["tipo"]}
