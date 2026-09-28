"""Gera o Pix "copia-e-cola" estático (BR Code / EMVCo) de DOAÇÃO, sem valor fixo,
para o README. A chave Pix é lida de control-panel/finance_config.py (nunca
hardcoded aqui). O payload resultante é público por natureza (é um QR de
recebimento) — é a única saída deste script.

Uso (na sandbox do Agentic OS):  .venv/bin/python gen_pix.py > pix.txt
"""
from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "control-panel"))


def _strip(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _tlv(id_: str, value: str) -> str:
    return f"{id_}{len(value):02d}{value}"


def _crc16(payload: str) -> str:
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def build(pix_key: str, name: str, city: str, txid: str = "APURACAO2026") -> str:
    mai = _tlv("26", _tlv("00", "br.gov.bcb.pix") + _tlv("01", pix_key))
    body = (_tlv("00", "01") + _tlv("01", "12") + mai + _tlv("52", "0000") + _tlv("53", "986")
            + _tlv("58", "BR") + _tlv("59", _strip(name).upper()[:25]) + _tlv("60", _strip(city).upper()[:15])
            + _tlv("62", _tlv("05", txid[:25])) + "6304")
    return body + _crc16(body)


if __name__ == "__main__":
    import finance_config  # noqa: E402

    key = finance_config.reveal().get("pix_key")
    if not key:
        sys.exit("sem chave Pix configurada em /finance")
    print(build(key, "Daniel Filius", "Sao Paulo"))
