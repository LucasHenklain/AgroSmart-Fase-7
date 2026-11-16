"""Cliente fino para a API Claude, com saída estruturada via ferramenta.

Em vez de pedir "responda em JSON" e torcer, declara-se uma ferramenta cujo
``input_schema`` é o JSON Schema de :class:`SaidaCopiloto` e força-se o modelo a
chamá-la (``tool_choice``). O que volta é um objeto já no formato certo — o que
sobra para os guardrails é o conteúdo, não a sintaxe.

O cliente nunca é obrigatório: sem o pacote ``anthropic`` ou sem chave, o
copiloto usa o motor de regras e diz isso na saída.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

from agrosmart.config import Config


class ErroCliente(Exception):
    """Falha ao obter uma resposta estruturada do modelo."""


class GeradorEstruturado(Protocol):
    """O que o copiloto precisa de um motor generativo — substituível por um dublê nos testes."""

    nome: str

    def disponivel(self) -> bool: ...

    def gerar_estruturado(
        self, *, sistema: str, usuario: str, ferramenta: str, descricao: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...


@dataclass
class ClienteClaude:
    modelo: str
    api_key: str | None
    max_tokens: int = 2500
    timeout_s: float = 90.0
    nome: str = "claude"

    @classmethod
    def da_config(cls, config: Config) -> ClienteClaude:
        return cls(
            modelo=config.ia_modelo,
            api_key=config.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"),
        )

    def disponivel(self) -> bool:
        if not self.api_key:
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def gerar_estruturado(
        self, *, sistema: str, usuario: str, ferramenta: str, descricao: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.disponivel():
            raise ErroCliente("cliente indisponível: sem chave de API ou sem o pacote anthropic")
        import anthropic

        cliente = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout_s, max_retries=2)
        inicio = time.perf_counter()
        try:
            resposta = cliente.messages.create(
                model=self.modelo,
                max_tokens=self.max_tokens,
                system=sistema,
                messages=[{"role": "user", "content": usuario}],
                tools=[{"name": ferramenta, "description": descricao, "input_schema": schema}],
                tool_choice={"type": "tool", "name": ferramenta},
            )
        except anthropic.APIError as erro:  # inclui timeout, autenticação, limite de taxa
            raise ErroCliente(f"API Claude: {erro}") from erro

        bloco = next((b for b in resposta.content if getattr(b, "type", "") == "tool_use"), None)
        if bloco is None:
            raise ErroCliente("modelo não chamou a ferramenta de saída estruturada")
        uso = {
            "modelo": resposta.model,
            "input_tokens": resposta.usage.input_tokens,
            "output_tokens": resposta.usage.output_tokens,
            "latencia_s": round(time.perf_counter() - inicio, 2),
            "stop_reason": resposta.stop_reason,
        }
        return dict(bloco.input), uso
