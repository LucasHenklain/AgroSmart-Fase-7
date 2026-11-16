"""Escritor da fonte ``erp_operacoes``: lançamentos diários com custo.

Um CSV por dia de lançamento, contendo o que foi lançado naquele dia — em
geral operações executadas na véspera, mais as correções retroativas que o
motor gerou (mesmo ``operacao_id``, custo corrigido, três dias depois). Dias
sem lançamento produzem arquivo só com cabeçalho: é o que o ERP faria, e o
coletor precisa lidar com lote vazio.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from agrosmart.contratos import contratos_ativos
from agrosmart.simuladores.base import Contexto, DefeitoInjetado
from agrosmart.simuladores.defeitos import POR_ID, distribuir_posicoes
from agrosmart.simuladores.motor import Operacao, Trajetoria

FONTE = "erp_operacoes"
SUBPASTA = "erp"
PADRAO_ARQUIVOS = "operacoes_*.csv"


def dias_de_lancamento(ctx: Contexto) -> list[date]:
    """Do segundo dia da janela até a manhã da data de referência."""
    return [ctx.inicio + timedelta(days=i) for i in range(1, len(ctx.dias) + 1)]


def _linha(op: Operacao, extraido_em: str) -> dict[str, object]:
    return {
        "operacao_id": op.operacao_id,
        "talhao_id": op.talhao_id,
        "data": op.data.isoformat(),
        "tipo_operacao": op.tipo_operacao,
        "insumo": op.insumo or "",
        "quantidade": op.quantidade,
        "unidade": op.unidade,
        "custo_brl": op.custo_brl,
        "responsavel_id": op.responsavel_id,
        "observacao": op.observacao or "",
        "extraido_em": extraido_em,
    }


def gerar_operacoes(ctx: Contexto, traj: Trajetoria) -> None:
    pasta = ctx.landing(SUBPASTA)
    cabecalho = contratos_ativos()[FONTE].campos_de_origem
    rng = ctx.rng("defeitos.erp_operacoes")

    dias = dias_de_lancamento(ctx)
    por_dia: dict[date, list[Operacao]] = {dia: [] for dia in dias}
    for op in sorted(traj.operacoes, key=lambda o: (o.lancado_em, o.operacao_id, o.correcao)):
        if op.lancado_em in por_dia:
            por_dia[op.lancado_em].append(op)

    # Defeitos em posições globais distintas, só sobre lançamentos originais.
    originais = [(dia, i) for dia in dias for i, op in enumerate(por_dia[dia]) if not op.correcao]
    alvo: dict[tuple[date, int], str] = {}
    tipos = [POR_ID[t] for t in ("OPR-CEN", "OPR-UNI", "OPR-TLH", "OPR-FUT")]
    # "Centavos" (×100) só é detectável pelo teto de OPR-06 quando o custo original
    # já é razoável; em R$ 40 o erro passaria — limitação declarada no contrato.
    elegiveis = {
        "OPR-CEN": {
            i for i, (dia, idx) in enumerate(originais) if por_dia[dia][idx].custo_brl >= 300
        }
    }
    for tipo_id, escolhidas in distribuir_posicoes(rng, len(originais), tipos, elegiveis).items():
        for posicao in escolhidas:
            alvo[originais[posicao]] = tipo_id

    for dia in dias:
        extraido_em = f"{dia.isoformat()}T02:45:00Z"
        arquivo = f"operacoes_{dia:%Y%m%d}.csv"
        buffer = io.StringIO()
        escritor = csv.DictWriter(buffer, fieldnames=cabecalho, lineterminator="\n")
        escritor.writeheader()

        for indice, op in enumerate(por_dia[dia]):
            linha = _linha(op, extraido_em)
            tipo_id = alvo.get((dia, indice))
            if tipo_id == "OPR-CEN":
                linha["custo_brl"] = round(op.custo_brl * 100, 2)
            elif tipo_id == "OPR-UNI":
                linha["unidade"] = "litros"
            elif tipo_id == "OPR-TLH":
                linha["talhao_id"] = "TLH-99"
            elif tipo_id == "OPR-FUT":
                linha["data"] = (ctx.config.data_referencia + timedelta(days=10)).isoformat()
            if tipo_id:
                tipo = POR_ID[tipo_id]
                ctx.registro.adicionar(
                    DefeitoInjetado(
                        tipo_id,
                        FONTE,
                        tipo.regra,
                        {"operacao_id": op.operacao_id, "lancado_em": dia.isoformat()},
                        arquivo,
                        tipo.descricao,
                    )
                )
            escritor.writerow(linha)

        ctx.gravar_texto(FONTE, pasta / arquivo, buffer.getvalue(), registros=len(por_dia[dia]))
