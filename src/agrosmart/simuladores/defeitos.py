"""Catálogo dos defeitos injetados de propósito na simulação.

Cada defeito aponta para a regra do contrato que **deve** capturá-lo. Isso dá
ao Passo 3 uma verdade-terreno: se um defeito injetado não aparecer na
quarentena (crítico) ou no relatório (alerta), o motor de qualidade tem um bug
— não o dado.

A proporção é pequena (~1 % nos sensores, unidades nas outras fontes) porque
o objetivo é demonstrar o mecanismo, não simular uma origem podre. Alguns
comportamentos que geram alertas — bateria baixa, sinal fraco, sensor calado,
cena nublada — **não** estão aqui: emergem do modelo físico e são cenários
operacionais legítimos, não defeitos.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TipoDefeito:
    id: str
    fonte: str
    regra: str
    severidade: str
    descricao: str
    proporcao: float | None = None  # por registro (fontes de volume)
    quantidade: int | None = None  # ocorrências fixas (fontes pequenas)


CATALOGO: tuple[TipoDefeito, ...] = (
    # --- sensores: proporções por leitura --------------------------------------
    TipoDefeito(
        "SEN-DUP",
        "sensores",
        "SEN-02",
        "critica",
        "leitura reenviada pelo gateway no lote seguinte (duplicata exata)",
        proporcao=0.005,
    ),
    TipoDefeito(
        "SEN-FUT",
        "sensores",
        "SEN-08",
        "critica",
        "relógio do dispositivo adiantado: medido_em dois dias no futuro",
        proporcao=0.001,
    ),
    TipoDefeito(
        "SEN-UMI",
        "sensores",
        "SEN-04",
        "critica",
        "sensor descalibrado: umidade acima de 100 %",
        proporcao=0.002,
    ),
    TipoDefeito(
        "SEN-TLH",
        "sensores",
        "SEN-09",
        "critica",
        "dispositivo configurado com talhão inexistente (TLH-99)",
        proporcao=0.001,
    ),
    TipoDefeito(
        "SEN-ID",
        "sensores",
        "SEN-03",
        "critica",
        "dispositivo não homologado publicando no tópico (id fora do padrão)",
        proporcao=0.0005,
    ),
    TipoDefeito(
        "SEN-NUL",
        "sensores",
        "SEN-01",
        "critica",
        "falha de leitura: umidade ausente",
        proporcao=0.001,
    ),
    # --- clima: ocorrências fixas -----------------------------------------------
    TipoDefeito(
        "CLI-INV",
        "clima",
        "CLI-03",
        "critica",
        "parser trocou colunas: temp_min > temp_max",
        quantidade=2,
    ),
    TipoDefeito(
        "CLI-UR",
        "clima",
        "CLI-05",
        "critica",
        "umidade relativa acima de 100 %",
        quantidade=1,
    ),
    TipoDefeito(
        "CLI-DUP",
        "clima",
        "CLI-02",
        "critica",
        "API devolveu o dia anterior junto com o dia pedido",
        quantidade=1,
    ),
    # --- ERP operações ------------------------------------------------------------
    TipoDefeito(
        "OPR-CEN",
        "erp_operacoes",
        "OPR-06",
        "critica",
        "exportação em centavos: custo 100 vezes maior",
        quantidade=3,
    ),
    TipoDefeito(
        "OPR-UNI",
        "erp_operacoes",
        "OPR-04",
        "critica",
        'unidade fora do domínio ("litros" em vez de "L")',
        quantidade=2,
    ),
    TipoDefeito(
        "OPR-TLH",
        "erp_operacoes",
        "OPR-07",
        "critica",
        "lançamento em talhão inexistente (TLH-99)",
        quantidade=1,
    ),
    TipoDefeito(
        "OPR-FUT",
        "erp_operacoes",
        "OPR-08",
        "critica",
        "operação planejada lançada como executada (data futura)",
        quantidade=1,
    ),
    # --- ERP talhões -------------------------------------------------------------
    TipoDefeito(
        "TLH-GEO",
        "erp_talhoes",
        "TLH-04",
        "critica",
        "latitude e longitude trocadas em um snapshot",
        quantidade=1,
    ),
    # --- imagens --------------------------------------------------------------------
    TipoDefeito(
        "IMG-NDV",
        "imagens_ndvi",
        "IMG-03",
        "critica",
        "erro no cálculo do índice: ndvi_max acima de 1",
        quantidade=1,
    ),
    TipoDefeito(
        "IMG-TRU",
        "imagens_ndvi",
        "IMG-10",
        "alerta",
        "upload interrompido: arquivo com 1 KB",
        quantidade=1,
    ),
    # --- laudos --------------------------------------------------------------------
    TipoDefeito(
        "LAU-PH",
        "laudos_solo",
        "LAU-03",
        "critica",
        "extrator leu o campo errado: pH 11,2",
        quantidade=1,
    ),
    TipoDefeito(
        "LAU-DAT",
        "laudos_solo",
        "LAU-06",
        "critica",
        "datas trocadas na extração: emitido antes da coleta",
        quantidade=1,
    ),
)

POR_ID: dict[str, TipoDefeito] = {d.id: d for d in CATALOGO}


def sortear(rng: random.Random, tipo: TipoDefeito) -> bool:
    """Decide, por registro, se um defeito proporcional é aplicado."""
    if tipo.proporcao is None:
        return False
    return rng.random() < tipo.proporcao


def escolher_posicoes(rng: random.Random, total: int, tipo: TipoDefeito) -> set[int]:
    """Índices (0..total-1) que recebem um defeito de quantidade fixa."""
    if tipo.quantidade is None or total == 0:
        return set()
    return set(rng.sample(range(total), min(tipo.quantidade, total)))


def distribuir_posicoes(
    rng: random.Random,
    total: int,
    tipos: Iterable[TipoDefeito],
    elegiveis: dict[str, set[int]] | None = None,
) -> dict[str, set[int]]:
    """Posições distintas para vários defeitos de quantidade fixa.

    Sorteio sem reposição sobre um único conjunto: nenhum registro recebe dois
    defeitos, e cada tipo recebe exatamente a quantidade declarada enquanto
    houver registros disponíveis. ``elegiveis`` restringe, por tipo, as
    posições candidatas — para defeitos que só fazem sentido (ou só são
    detectáveis pela regra alvo) em certos registros.
    """
    disponiveis = list(range(total))
    resultado: dict[str, set[int]] = {}
    for tipo in tipos:
        candidatos = disponiveis
        if elegiveis and tipo.id in elegiveis:
            candidatos = [i for i in disponiveis if i in elegiveis[tipo.id]]
        quantidade = min(tipo.quantidade or 0, len(candidatos))
        escolhidas = set(rng.sample(candidatos, quantidade)) if quantidade else set()
        disponiveis = [i for i in disponiveis if i not in escolhidas]
        resultado[tipo.id] = escolhidas
    return resultado
