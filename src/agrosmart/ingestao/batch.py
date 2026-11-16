"""Coletor **batch**: exportações CSV do ERP (talhões e operações).

A checagem de schema acontece no cabeçalho, antes de qualquer linha ser lida
como dado. Coluna a mais ou a menos significa que o ERP mudou o layout de
exportação — e isso é um incidente (INC-02), não um registro ruim.
"""

from __future__ import annotations

import csv
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agrosmart.contratos import ModoIngestao
from agrosmart.ingestao.base import Coletor, ErroSchema, Lote

_PADRAO_NOME = re.compile(r"^(?P<prefixo>[a-z]+)_(?P<data>\d{8})\.csv$")


class ColetorBatch(Coletor):
    modo = ModoIngestao.BATCH

    #: subpasta da landing e prefixo do arquivo, por fonte
    LOCALIZACAO: dict[str, tuple[str, str, str]] = {
        # fonte: (subpasta, prefixo, hora da extração "HH:MM")
        "erp_talhoes": ("erp", "talhoes", "02:30"),
        "erp_operacoes": ("erp", "operacoes", "02:45"),
    }

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        try:
            self.subpasta, self.prefixo, hora = self.LOCALIZACAO[self.fonte]
        except KeyError as erro:
            raise ValueError(
                f"ColetorBatch não sabe onde a fonte {self.fonte!r} aterrissa"
            ) from erro
        self.hora_extracao = datetime.strptime(hora, "%H:%M").time()

    def descobrir_lotes(self) -> list[Lote]:
        pasta = self.config.dir_landing_fonte(self.subpasta)
        lotes: list[Lote] = []
        for caminho in sorted(pasta.glob(f"{self.prefixo}_*.csv")):
            casamento = _PADRAO_NOME.match(caminho.name)
            if not casamento:
                self.logger.warning("ignorando arquivo fora do padrão: %s", caminho.name)
                continue
            dia = datetime.strptime(casamento.group("data"), "%Y%m%d").date()
            extracao = datetime.combine(dia, self.hora_extracao, tzinfo=UTC)
            # Carga completa: a janela é o instante da extração. Incremental:
            # cobre o dia anterior inteiro até a extração.
            inicio = (
                extracao
                if self.fonte == "erp_talhoes"
                else datetime.combine(dia - timedelta(days=1), datetime.min.time(), tzinfo=UTC)
            )
            lotes.append(Lote(self.fonte, caminho, inicio, extracao, dia))
        return lotes

    def validar_schema(self, lote: Lote) -> int:
        esperado = set(self.contrato.campos_de_origem)
        with lote.origem.open(encoding="utf-8", newline="") as arquivo:
            leitor = csv.reader(arquivo)
            try:
                cabecalho = next(leitor)
            except StopIteration as erro:
                raise ErroSchema(f"{lote.rotulo}: arquivo vazio, sem cabeçalho") from erro
            encontrado = {c.strip() for c in cabecalho}
            faltando = sorted(esperado - encontrado)
            extras = sorted(encontrado - esperado)
            if faltando or extras:
                partes = []
                if faltando:
                    partes.append(f"colunas ausentes {faltando}")
                if extras:
                    partes.append(f"colunas inesperadas {extras}")
                raise ErroSchema(
                    f"{lote.rotulo}: cabeçalho divergente do contrato {self.schema_version} — "
                    + "; ".join(partes)
                )
            registros = 0
            for numero, linha in enumerate(leitor, start=2):
                if not linha:
                    continue
                if len(linha) != len(cabecalho):
                    raise ErroSchema(
                        f"{lote.rotulo}: linha {numero} tem {len(linha)} campos, "
                        f"cabeçalho tem {len(cabecalho)}"
                    )
                registros += 1
        return registros


def caminho_para(subpasta: str, config_dir_landing: Path) -> Path:
    return config_dir_landing / subpasta
