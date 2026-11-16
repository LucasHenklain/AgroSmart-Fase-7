"""Schema da saída do copiloto — o mesmo para o motor de regras e para o modelo.

A saída é estruturada de propósito: um JSON com campos fixos é o que permite
validar (guardrails), comparar motores (avaliação) e renderizar (Markdown,
PDF, dashboard) sem interpretar prosa. O texto livre existe — no diagnóstico e
no detalhe de cada recomendação — mas dentro de campos, com a lista de dados
usados ao lado.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Caso(StrEnum):
    IRRIGACAO = "irrigacao"
    FITOSSANITARIO = "fitossanitario"
    EXECUTIVO = "executivo"


Prioridade = Literal["alta", "media", "baixa"]
Confianca = Literal["alta", "media", "baixa"]
TipoRecomendacao = Literal[
    "irrigacao",
    "fitossanitario",
    "nutricao",
    "manutencao",
    "monitoramento",
    "gestao",
]


class Recomendacao(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prioridade: Prioridade
    tipo: TipoRecomendacao
    acao: str = Field(min_length=5, max_length=160, description="Imperativo curto: o que fazer.")
    detalhe: str = Field(
        min_length=10,
        max_length=900,
        description="Por que fazer e como, citando só números presentes no contexto.",
    )
    janela_execucao: str | None = Field(
        default=None, max_length=80, description="Quando executar, se aplicável."
    )
    dados_usados: list[str] = Field(
        default_factory=list,
        description=(
            "Caminhos do contexto que sustentam a recomendação (ex.: hidrico.deficit_atual_mm)."
        ),
    )


class SaidaCopiloto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caso: Caso
    talhao_id: str
    diagnostico: str = Field(min_length=20, max_length=1800)
    recomendacoes: list[Recomendacao] = Field(min_length=1, max_length=6)
    custo_estimado_brl: float | None = Field(
        default=None,
        ge=0,
        description="Custo das ações recomendadas, se calculável a partir do contexto.",
    )
    economia_estimada_brl: float | None = Field(
        default=None,
        ge=0,
        description="Perda evitada ou economia, se calculável a partir do contexto.",
    )
    confianca: Confianca
    justificativa_confianca: str = Field(min_length=10, max_length=400)
    limitacoes: list[str] = Field(default_factory=list, max_length=8)
    dados_usados: list[str] = Field(
        default_factory=list, description="Caminhos do contexto usados no diagnóstico."
    )

    @property
    def tipos(self) -> set[str]:
        return {r.tipo for r in self.recomendacoes}

    @property
    def prioridade_maxima(self) -> str:
        ordem = {"alta": 0, "media": 1, "baixa": 2}
        return min((r.prioridade for r in self.recomendacoes), key=lambda p: ordem[p])

    def recomenda_irrigar(self) -> bool:
        return any(
            r.tipo == "irrigacao" and r.prioridade in {"alta", "media"} for r in self.recomendacoes
        )


def esquema_json_saida() -> dict:
    """JSON Schema da saída, para a chamada com saída estruturada (tool use)."""
    schema = SaidaCopiloto.model_json_schema()
    # Enums StrEnum são serializados como strings; garante que o modelo veja os valores.
    return schema
