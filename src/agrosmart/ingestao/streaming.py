"""Coletor **streaming**: micro-batches JSONL do gateway MQTT (sensores).

O gateway já materializa cada janela de 15 minutos em um arquivo — é o mesmo
padrão de um Kinesis Firehose ou de um consumidor MQTT gravando em S3. O
coletor não interpreta valores: só garante que cada linha é JSON válido e que
nenhuma chave desconhecida entrou no tópico. Chave faltando é tratada como
nulo e cabe ao silver reprovar pela regra ``nao_nulo``.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from agrosmart.contratos import ModoIngestao
from agrosmart.ingestao.base import Coletor, ErroSchema, Lote

_PADRAO_NOME = re.compile(r"^leituras_(?P<marca>\d{8}T\d{4})Z\.jsonl$")
INTERVALO = timedelta(minutes=15)


class ColetorStreaming(Coletor):
    modo = ModoIngestao.STREAMING
    subpasta = "sensores"

    def descobrir_lotes(self) -> list[Lote]:
        pasta = self.config.dir_landing_fonte(self.subpasta)
        lotes: list[Lote] = []
        for caminho in sorted(pasta.rglob("leituras_*.jsonl")):
            casamento = _PADRAO_NOME.match(caminho.name)
            if not casamento:
                self.logger.warning("ignorando arquivo fora do padrão: %s", caminho.name)
                continue
            inicio = datetime.strptime(casamento.group("marca"), "%Y%m%dT%H%M").replace(tzinfo=UTC)
            lotes.append(Lote(self.fonte, caminho, inicio, inicio + INTERVALO, inicio.date()))
        return lotes

    def validar_schema(self, lote: Lote) -> int:
        permitidas = set(self.contrato.campos_de_origem)
        registros = 0
        with lote.origem.open(encoding="utf-8") as arquivo:
            for numero, linha in enumerate(arquivo, start=1):
                if not linha.strip():
                    continue
                try:
                    registro = json.loads(linha)
                except json.JSONDecodeError as erro:
                    raise ErroSchema(
                        f"{lote.rotulo}: linha {numero} não é JSON válido ({erro.msg})"
                    ) from erro
                if not isinstance(registro, dict):
                    raise ErroSchema(f"{lote.rotulo}: linha {numero} não é um objeto JSON")
                desconhecidas = sorted(set(registro) - permitidas)
                if desconhecidas:
                    raise ErroSchema(
                        f"{lote.rotulo}: linha {numero} tem chaves fora do contrato "
                        f"{self.schema_version}: {desconhecidas}"
                    )
                registros += 1
        return registros
