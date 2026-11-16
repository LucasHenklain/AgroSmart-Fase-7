"""Coletor **api_pull**: respostas diárias da API meteorológica (clima).

A resposta é gravada íntegra, com envelope e tudo. A validação é a do
*envelope*: as séries obrigatórias existem e têm o mesmo comprimento que o
eixo de tempo. A tradução dos nomes da API para os campos do contrato é
tarefa do silver — é exatamente isso que faz esta fonte ser semiestruturada.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from agrosmart.contratos import ModoIngestao
from agrosmart.ingestao.base import Coletor, ErroSchema, Lote, ler_json_arquivo

_PADRAO_NOME = re.compile(r"^(?P<estacao>EST-[A-Z]{2}-\d{3})_(?P<data>\d{8})\.json$")

SERIES_OBRIGATORIAS = (
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "relative_humidity_2m_mean",
    "precipitation_sum",
    "et0_fao_evapotranspiration",
)
CHAVES_ENVELOPE = ("estacao_id", "fazenda_id", "daily", "daily_units")


class ColetorApiPull(Coletor):
    modo = ModoIngestao.API_PULL
    subpasta = "clima"

    def descobrir_lotes(self) -> list[Lote]:
        pasta = self.config.dir_landing_fonte(self.subpasta)
        lotes: list[Lote] = []
        for caminho in sorted(pasta.glob("*.json")):
            casamento = _PADRAO_NOME.match(caminho.name)
            if not casamento:
                self.logger.warning("ignorando arquivo fora do padrão: %s", caminho.name)
                continue
            dia = datetime.strptime(casamento.group("data"), "%Y%m%d").date()
            inicio = datetime.combine(dia, datetime.min.time(), tzinfo=UTC)
            lotes.append(Lote(self.fonte, caminho, inicio, inicio + timedelta(days=1), dia))
        return lotes

    def validar_schema(self, lote: Lote) -> int:
        envelope = ler_json_arquivo(lote.origem)
        if not isinstance(envelope, dict):
            raise ErroSchema(f"{lote.rotulo}: resposta não é um objeto JSON")
        faltando = [c for c in CHAVES_ENVELOPE if c not in envelope]
        if faltando:
            raise ErroSchema(f"{lote.rotulo}: envelope sem as chaves {faltando}")
        daily = envelope["daily"]
        if not isinstance(daily, dict) or "time" not in daily:
            raise ErroSchema(f"{lote.rotulo}: 'daily' sem eixo 'time'")
        tamanho = len(daily["time"])
        for serie in SERIES_OBRIGATORIAS:
            valores = daily.get(serie)
            if valores is None:
                raise ErroSchema(f"{lote.rotulo}: série obrigatória ausente: {serie}")
            if len(valores) != tamanho:
                raise ErroSchema(
                    f"{lote.rotulo}: série {serie} tem {len(valores)} valores, "
                    f"eixo 'time' tem {tamanho}"
                )
        return tamanho
