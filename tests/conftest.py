"""Fixtures: transporte HTTP falso servindo os arquivos reais do TSE (2022) gravados em fixtures/."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from apuracao_mcp.tse_client import BASE_URL, TSEClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# URL do TSE → arquivo em fixtures/ (todos baixados de resultados.tse.jus.br em 2026-09-28)
ROTAS = {
    f"{BASE_URL}/comum/config/ele-c.json": "ele-c-2026-09-28.json",
    f"{BASE_URL}/ele2022/544/dados-simplificados/br/br-c0001-e000544-r.json": "br-c0001-e000544-r.json",
    f"{BASE_URL}/ele2022/545/dados-simplificados/br/br-c0001-e000545-r.json": "br-c0001-e000545-r.json",
    f"{BASE_URL}/ele2022/544/dados-simplificados/sp/sp-c0001-e000544-r.json": "sp-c0001-e000544-r.json",
    f"{BASE_URL}/ele2022/546/dados-simplificados/sp/sp-c0003-e000546-r.json": "sp-c0003-e000546-r.json",
    f"{BASE_URL}/ele2022/547/dados-simplificados/sp/sp-c0003-e000547-r.json": "sp-c0003-e000547-r.json",
    f"{BASE_URL}/ele2022/546/dados-simplificados/sp/sp-c0005-e000546-r.json": "sp-c0005-e000546-r.json",
    f"{BASE_URL}/ele2022/546/config/mun-e000546-cm.json": "mun-e000546-cm.json",
    f"{BASE_URL}/ele2022/546/dados/sp/sp71072-c0003-e000546-u.json": "sp71072-c0003-e000546-u.json",
}


class FakeTSE:
    """Transporte httpx que serve as fixtures e registra as requisições (URL + headers)."""

    def __init__(self, overrides: dict[str, str] | None = None):
        self.requests: list[httpx.Request] = []
        self.rotas = dict(ROTAS)
        if overrides:
            self.rotas.update(overrides)
        self.last_modified = "Sun, 02 Oct 2022 20:00:00 GMT"
        self.force_304 = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url not in self.rotas:
            return httpx.Response(404, text="<html>404</html>")
        if self.force_304 and request.headers.get("If-Modified-Since"):
            return httpx.Response(304)
        body = (FIXTURES / self.rotas[url]).read_bytes()
        return httpx.Response(200, content=body, headers={
            "Content-Type": "application/json", "Last-Modified": self.last_modified, "ETag": '"abc"',
        })

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
def fake() -> FakeTSE:
    return FakeTSE()


@pytest.fixture
def client(fake: FakeTSE) -> TSEClient:
    return TSEClient(transport=fake.transport())


@pytest.fixture
def config_2026(tmp_path: Path) -> dict:
    """Config sintético: como o TSE deve publicar as Eleições Gerais 2026 (mesma estrutura de 2024)."""
    base = json.loads((FIXTURES / "ele-c-2026-09-28.json").read_text(encoding="utf-8"))
    pleito = {
        "cd": "9001", "dt": "04/10/2026", "dtlim": "04/09/2028",
        "e": [
            {"cd": "7001", "cdt2": "7002", "nm": "Eleição Ordinária Federal - 2026 - 04/10/2026 1º Turno", "t": "1", "tp": "1",
             "abr": [{"cd": "br", "cp": [{"cd": "1", "ds": "Presidente", "tp": "1"}]}]},
            {"cd": "7003", "cdt2": "7004", "nm": "Eleição Ordinária Estadual - 2026 - 04/10/2026 1º Turno", "t": "1", "tp": "2",
             "abr": [{"cd": "sp", "cp": [{"cd": "3", "ds": "Governador", "tp": "1"}, {"cd": "5", "ds": "Senador", "tp": "1"}]},
                     {"cd": "rj", "cp": [{"cd": "3", "ds": "Governador", "tp": "1"}]}]},
        ],
    }
    base["c"] = "ele2026"
    base["pl"] = [pleito] + base["pl"]
    return base
