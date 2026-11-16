"""Simuladores de fontes de dados.

Geram, de forma determinística (semente fixa em ``AGROSMART_SEED``), o
histórico das seis fontes descritas em ``contratos/`` a partir de **uma única
trajetória agronômica** (ver ``motor``): o clima que a estação registra é o
que molha o solo que o sensor mede, a irrigação que o ERP lança responde à
umidade baixa, o NDVI que o drone vê cai depois de dias de estresse.

Os simuladores escrevem na pasta ``landing/`` **como se fossem os sistemas de
origem** — gateway MQTT, API de clima, exportação do ERP, drone, laboratório.
Os coletores (``agrosmart.ingestao``) leem daí; nenhum simulador toca o lake.

Também injetam, de propósito, uma fração pequena de registros defeituosos,
cada um mapeado à regra do contrato que deve capturá-lo (``defeitos``). O
registro completo vai para ``outputs/simulacao.json`` e é a verdade-terreno
que o motor de qualidade precisa acertar.

Responsável: E1 (Engenharia de Dados).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from typing import Any

from agrosmart.config import Config, carregar_config
from agrosmart.log import Evento, obter_logger, registrar_evento
from agrosmart.simuladores import clima, imagens, laudos, operacoes, sensores, talhoes
from agrosmart.simuladores.base import Contexto
from agrosmart.simuladores.motor import Trajetoria, simular_trajetoria

Gerador = Callable[[Contexto, Trajetoria], None]

# fonte -> (subpasta da landing, padrão de arquivos para limpeza, gerador)
FONTES: dict[str, tuple[str, str, Gerador]] = {
    "erp_talhoes": (talhoes.SUBPASTA, talhoes.PADRAO_ARQUIVOS, talhoes.gerar_talhoes),
    "clima": (clima.SUBPASTA, "*.json", clima.gerar_clima),
    "erp_operacoes": (operacoes.SUBPASTA, operacoes.PADRAO_ARQUIVOS, operacoes.gerar_operacoes),
    "sensores": (sensores.SUBPASTA, "*", sensores.gerar_sensores),
    "imagens_ndvi": (imagens.SUBPASTA, "*", imagens.gerar_imagens),
    "laudos_solo": (laudos.SUBPASTA, "*", laudos.gerar_laudos),
}

ORDEM = tuple(FONTES)


def simular(
    config: Config | None = None,
    *,
    fontes: Iterable[str] | None = None,
    limpar: bool = True,
) -> dict[str, Any]:
    """Gera a landing das fontes pedidas (todas, por padrão) e devolve o resumo.

    A trajetória é sempre simulada por inteiro, mesmo quando só uma fonte é
    escrita: é barato, e garante que ``simular --fonte sensores`` produza
    exatamente os mesmos sensores que ``simular`` sem filtro.
    """
    cfg = config or carregar_config()
    logger = obter_logger("simuladores")
    selecionadas = list(ORDEM) if fontes is None else [f for f in ORDEM if f in set(fontes)]
    desconhecidas = set(fontes or ()) - set(FONTES)
    if desconhecidas:
        raise ValueError(f"fontes desconhecidas: {sorted(desconhecidas)}; válidas: {list(ORDEM)}")

    inicio = time.perf_counter()
    ctx = Contexto(cfg)
    logger.info(
        "janela %s → %s · %d dias · %d talhões · %d sensores · seed %d",
        ctx.inicio,
        ctx.fim,
        len(ctx.dias),
        len(ctx.talhoes),
        len(ctx.sensores),
        cfg.seed,
    )
    traj = simular_trajetoria(ctx)

    for fonte in selecionadas:
        subpasta, padrao, gerador = FONTES[fonte]
        if limpar:
            removidos = ctx.limpar_landing(subpasta, padrao)
            if removidos:
                logger.debug(
                    "landing/%s: %d itens de execução anterior removidos", subpasta, removidos
                )
        t0 = time.perf_counter()
        gerador(ctx, traj)
        contagem = ctx.contagens.get(fonte)
        logger.info(
            "%-14s %6d arquivos · %7d registros · %8.1f KB · %.1fs",
            fonte,
            contagem.arquivos if contagem else 0,
            contagem.registros if contagem else 0,
            (contagem.bytes if contagem else 0) / 1024,
            time.perf_counter() - t0,
        )

    resumo = ctx.resumo()
    resumo["fontes_selecionadas"] = selecionadas
    resumo["trajetoria"] = traj.estatisticas()
    resumo["metricas_extras"] = ctx.metricas_extras
    resumo["duracao_s"] = round(time.perf_counter() - inicio, 2)

    cfg.dir_outputs.mkdir(parents=True, exist_ok=True)
    destino = cfg.dir_outputs / "simulacao.json"
    destino.write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")

    registrar_evento(
        Evento(
            etapa="simulacao",
            acao="concluida",
            mensagem=f"{len(selecionadas)} fontes geradas em landing/",
            metricas={
                "arquivos": sum(c.arquivos for c in ctx.contagens.values()),
                "registros": sum(c.registros for c in ctx.contagens.values()),
                "defeitos": len(ctx.registro.itens),
                "duracao_s": resumo["duracao_s"],
            },
        ),
        cfg,
    )
    return resumo
