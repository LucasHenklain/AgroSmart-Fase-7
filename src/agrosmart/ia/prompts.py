"""Prompts versionados do copiloto.

Cada prompt é um arquivo Markdown em ``ia/prompts/<nome>.v<N>.md``. A versão
faz parte do nome de propósito: mudar um prompt é criar ``v2`` e apontar para
ela, e cada relatório gerado registra a versão que o produziu — sem isso,
comparar saídas de duas semanas diferentes não significa nada.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agrosmart.ia.contexto import ContextPack
from agrosmart.ia.esquema import Caso

PASTA = Path(__file__).with_name("prompts")
_PADRAO = re.compile(r"^(?P<nome>[a-z_]+)\.v(?P<versao>\d+)\.md$")


@dataclass(frozen=True, slots=True)
class Prompt:
    nome: str
    versao: int
    texto: str

    @property
    def rotulo(self) -> str:
        return f"{self.nome}.v{self.versao}"

    def renderizar(self, **valores: str) -> str:
        texto = self.texto
        for chave, valor in valores.items():
            texto = texto.replace("{{" + chave + "}}", valor)
        return texto


def listar() -> dict[str, list[Prompt]]:
    """Todos os prompts por nome, versões em ordem crescente."""
    encontrados: dict[str, list[Prompt]] = {}
    for caminho in sorted(PASTA.glob("*.md")):
        casamento = _PADRAO.match(caminho.name)
        if not casamento:
            continue
        prompt = Prompt(
            casamento.group("nome"),
            int(casamento.group("versao")),
            caminho.read_text(encoding="utf-8"),
        )
        encontrados.setdefault(prompt.nome, []).append(prompt)
    for versoes in encontrados.values():
        versoes.sort(key=lambda p: p.versao)
    return encontrados


def carregar(nome: str, versao: int | None = None) -> Prompt:
    """O prompt ``nome`` na versão pedida (ou a mais recente)."""
    versoes = listar().get(nome)
    if not versoes:
        raise FileNotFoundError(f"prompt {nome!r} não existe em {PASTA}")
    if versao is None:
        return versoes[-1]
    for prompt in versoes:
        if prompt.versao == versao:
            return prompt
    raise FileNotFoundError(f"prompt {nome!r} não tem versão v{versao}")


def montar(caso: Caso, pack: ContextPack) -> tuple[Prompt, Prompt, str, str]:
    """(prompt de sistema, prompt do caso, texto de sistema, texto do usuário)."""
    sistema = carregar("sistema")
    do_caso = carregar(caso.value)
    usuario = do_caso.renderizar(
        talhao_id=pack.talhao.talhao_id,
        hoje=pack.janela.hoje.isoformat(),
        contexto=pack.para_json(indent=None),
    )
    return sistema, do_caso, sistema.texto, usuario
