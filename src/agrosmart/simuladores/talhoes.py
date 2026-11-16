"""Escritor da fonte ``erp_talhoes``: snapshots diários do cadastro.

Carga completa, como um ERP exporta: um CSV por dia com todos os talhões,
mesmo que nada tenha mudado. Duas coisas mudam de propósito ao longo da
janela — a previsão de colheita de um talhão é adiada pelo produtor (mudança
legítima) e um snapshot sai com latitude e longitude trocadas (defeito).
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from agrosmart.contratos import contratos_ativos
from agrosmart.simuladores.base import Contexto, DefeitoInjetado
from agrosmart.simuladores.catalogo import Talhao
from agrosmart.simuladores.defeitos import POR_ID
from agrosmart.simuladores.motor import Trajetoria

FONTE = "erp_talhoes"
SUBPASTA = "erp"
PADRAO_ARQUIVOS = "talhoes_*.csv"

# A partir do 20º dia da janela o produtor adia a colheita do trigo em 5 dias.
_DIA_MUDANCA_COLHEITA = 20
_TALHAO_MUDANCA = "TLH-04"


def dias_de_snapshot(ctx: Contexto) -> list[date]:
    """Um snapshot por dia da janela, mais o da manhã da data de referência."""
    return [*ctx.dias, ctx.fim + timedelta(days=1)]


def _linha(talhao: Talhao, extraido_em: str, colheita: date) -> dict[str, object]:
    return {
        "talhao_id": talhao.id,
        "fazenda_id": talhao.fazenda_id,
        "nome": talhao.nome,
        "area_ha": talhao.area_ha,
        "cultura": talhao.cultura,
        "variedade": talhao.variedade,
        "data_plantio": talhao.data_plantio.isoformat(),
        "data_colheita_prevista": colheita.isoformat(),
        "latitude": talhao.latitude,
        "longitude": talhao.longitude,
        "tipo_solo": talhao.tipo_solo,
        "sistema_irrigacao": talhao.sistema_irrigacao,
        "capacidade_campo_pct": talhao.capacidade_campo_pct,
        "ponto_murcha_pct": talhao.ponto_murcha_pct,
        "extraido_em": extraido_em,
    }


def gerar_talhoes(ctx: Contexto, traj: Trajetoria) -> None:
    pasta = ctx.landing(SUBPASTA)
    cabecalho = contratos_ativos()[FONTE].campos_de_origem
    rng = ctx.rng("defeitos.erp_talhoes")

    snapshots = dias_de_snapshot(ctx)
    total_linhas = len(snapshots) * len(ctx.talhoes)
    # O defeito nunca cai no último snapshot: o cadastro mais recente fica limpo.
    posicao_geo = rng.randrange(0, max(total_linhas - len(ctx.talhoes), 1))

    indice_global = 0
    for indice_dia, dia in enumerate(snapshots):
        extraido_em = f"{dia.isoformat()}T02:30:00Z"
        arquivo = f"talhoes_{dia:%Y%m%d}.csv"
        buffer = io.StringIO()
        escritor = csv.DictWriter(buffer, fieldnames=cabecalho, lineterminator="\n")
        escritor.writeheader()

        for talhao in ctx.talhoes:
            colheita = talhao.data_colheita_prevista
            if talhao.id == _TALHAO_MUDANCA and indice_dia >= _DIA_MUDANCA_COLHEITA:
                colheita = colheita + timedelta(days=5)
            linha = _linha(talhao, extraido_em, colheita)

            if indice_global == posicao_geo:
                linha["latitude"], linha["longitude"] = linha["longitude"], linha["latitude"]
                ctx.registro.adicionar(
                    DefeitoInjetado(
                        "TLH-GEO",
                        FONTE,
                        "TLH-04",
                        {"talhao_id": talhao.id, "extraido_em": extraido_em},
                        arquivo,
                        POR_ID["TLH-GEO"].descricao,
                    )
                )
            escritor.writerow(linha)
            indice_global += 1

        ctx.gravar_texto(FONTE, pasta / arquivo, buffer.getvalue(), registros=len(ctx.talhoes))
