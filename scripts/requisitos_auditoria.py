"""Gera a lista fixada (nome==versão) das dependências de runtime do AgroSmart.

O estágio SCA do pipeline audita só o que o projeto realmente puxa — o pacote
``agrosmart`` com os extras de runtime (``app``, ``iot``, ``ia``) e todo o fecho
transitivo — e não o ambiente Python inteiro da máquina (que pode ter centenas de
pacotes alheios ao projeto). A lista sai das versões *instaladas* no interpretador que
executa este script, então reflete exatamente o ambiente testado.

Uso (no mesmo interpretador onde o projeto está instalado):

    python scripts/requisitos_auditoria.py saida.txt [--extras app,iot,ia]
    pip-audit -r saida.txt --no-deps --disable-pip
"""

from __future__ import annotations

import argparse
import re
import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

PACOTE_RAIZ = "agrosmart"
EXTRAS_PADRAO = ("app", "iot", "ia")


def _normalizar(nome: str) -> str:
    return re.sub(r"[-_.]+", "-", nome).lower()


def fecho_dependencias(raiz: str, extras: set[str]) -> dict[str, str]:
    """Percorre ``Requires-Dist`` a partir de ``raiz`` e devolve {nome: versão}."""
    vistos: dict[str, tuple[str, set[str]]] = {}
    pilha: list[tuple[str, set[str]]] = [(raiz, set(extras))]
    while pilha:
        nome, extras_pedidos = pilha.pop()
        try:
            dist = metadata.distribution(nome)
        except metadata.PackageNotFoundError:
            continue  # dependência condicional não instalada nesta plataforma
        chave = _normalizar(dist.metadata["Name"])
        if chave in vistos and extras_pedidos <= vistos[chave][1]:
            continue
        extras_ja = vistos.get(chave, ("", set()))[1]
        vistos[chave] = (dist.version, extras_ja | extras_pedidos)
        for linha in dist.requires or []:
            req = Requirement(linha)
            contextos = [""] + sorted(extras_pedidos)
            if req.marker is None or any(req.marker.evaluate({"extra": e}) for e in contextos):
                pilha.append((req.name, set(req.extras)))
    return {nome: versao for nome, (versao, _) in vistos.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("saida", type=Path, help="arquivo requirements a gerar")
    parser.add_argument("--extras", default=",".join(EXTRAS_PADRAO))
    args = parser.parse_args(argv)

    extras = {e.strip() for e in args.extras.split(",") if e.strip()}
    deps = fecho_dependencias(PACOTE_RAIZ, extras)
    if not deps:
        print(f"pacote {PACOTE_RAIZ!r} não está instalado neste interpretador", file=sys.stderr)
        return 2
    linhas = [f"{nome}=={versao}" for nome, versao in sorted(deps.items()) if nome != PACOTE_RAIZ]
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print(f"{len(linhas)} dependências de runtime ({PACOTE_RAIZ}[{','.join(sorted(extras))}])")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
