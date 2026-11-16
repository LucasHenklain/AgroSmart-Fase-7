"""Verificação da quarentena contra a verdade-terreno dos defeitos injetados.

Os simuladores registram cada defeito que injetaram (``outputs/simulacao.json
→ defeitos[]``) com a regra que deve capturá-lo e a chave do registro. Este
módulo confere, depois do silver, se cada um deles de fato:

* apareceu na **quarentena** (defeitos de regra crítica), ou
* seguiu para o silver **com o alerta marcado** (defeitos de regra de alerta).

Um defeito não localizado é bug do motor de qualidade — não do dado. O
resultado entra no ``dq_report.json`` como prova de que o mecanismo funciona,
e é testado automaticamente.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from agrosmart.contratos import Contrato, TipoCampo
from agrosmart.qualidade.motor import ResultadoQualidade


@dataclass(slots=True)
class VerificacaoDefeitos:
    total: int = 0
    localizados: int = 0
    por_tipo: dict[str, dict[str, int]] = field(default_factory=dict)
    faltantes: list[dict[str, Any]] = field(default_factory=list)
    fontes_sem_resultado: list[str] = field(default_factory=list)

    @property
    def cobertura(self) -> float:
        return round(self.localizados / self.total, 4) if self.total else 1.0

    def para_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "localizados": self.localizados,
            "cobertura": self.cobertura,
            "por_tipo": self.por_tipo,
            "faltantes": self.faltantes[:50],
            "faltantes_total": len(self.faltantes),
            "fontes_sem_resultado": self.fontes_sem_resultado,
        }


def _valor_tipado(bruto: Any, tipo: TipoCampo) -> Any:
    if bruto is None:
        return None
    if tipo is TipoCampo.TIMESTAMP:
        instante = datetime.fromisoformat(str(bruto).replace("Z", "+00:00"))
        return instante if instante.tzinfo else instante.replace(tzinfo=UTC)
    if tipo is TipoCampo.DATE:
        return date.fromisoformat(str(bruto))
    if tipo is TipoCampo.INT:
        return int(bruto)
    if tipo is TipoCampo.FLOAT:
        return float(bruto)
    return str(bruto)


def _filtro_chave(chave: dict[str, Any], contrato: Contrato) -> pl.Expr | None:
    condicoes: list[pl.Expr] = []
    tipos = contrato.mapa_tipos()
    for campo, valor in chave.items():
        if campo not in tipos:
            continue  # ex.: lancado_em, que descreve o arquivo e não é coluna
        condicoes.append(pl.col(campo) == pl.lit(_valor_tipado(valor, tipos[campo])))
    if not condicoes:
        return None
    return pl.all_horizontal(condicoes)


def verificar_defeitos(
    defeitos: list[dict[str, Any]],
    resultados: dict[str, ResultadoQualidade],
    contratos: dict[str, Contrato],
    severidades: dict[str, str],
) -> VerificacaoDefeitos:
    """``severidades`` mapeia o tipo do defeito (ex.: ``IMG-TRU``) à severidade da regra alvo."""
    verificacao = VerificacaoDefeitos(total=len(defeitos))
    sem_resultado: set[str] = set()

    for defeito in defeitos:
        fonte, tipo = defeito["fonte"], defeito["tipo"]
        contagem = verificacao.por_tipo.setdefault(tipo, {"injetados": 0, "localizados": 0})
        contagem["injetados"] += 1

        resultado = resultados.get(fonte)
        if resultado is None:
            sem_resultado.add(fonte)
            verificacao.faltantes.append({**defeito, "motivo": "fonte não processada"})
            continue

        filtro = _filtro_chave(defeito["chave"], contratos[fonte])
        if filtro is None:
            verificacao.faltantes.append({**defeito, "motivo": "chave sem colunas verificáveis"})
            continue

        if severidades.get(tipo) == "alerta":
            base = resultado.aprovados
            achou = (
                not base.is_empty()
                and base.filter(filtro & pl.col("_alertas").list.contains(defeito["regra"])).height
                > 0
            )
        else:
            base = resultado.quarentena
            achou = not base.is_empty() and base.filter(filtro).height > 0

        if achou:
            contagem["localizados"] += 1
            verificacao.localizados += 1
        else:
            verificacao.faltantes.append({**defeito, "motivo": "não localizado"})

    verificacao.fontes_sem_resultado = sorted(sem_resultado)
    return verificacao


def carregar_defeitos_injetados(caminho: Path) -> list[dict[str, Any]]:
    if not caminho.exists():
        return []
    conteudo = json.loads(caminho.read_text(encoding="utf-8"))
    return list(conteudo.get("defeitos", []))
