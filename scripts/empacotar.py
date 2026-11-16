"""Atalho de linha de comando para o empacotamento da entrega.

    python scripts/empacotar.py "Nome Completo" RM12345 1TDSA        # Fase 6 (padrão)
    python scripts/empacotar.py "Nome Completo" RM12345 1TDSA 5      # Fase 5

Equivale a ``agrosmart empacotar --nome ... --rm ... --turma ... [--fase N]``.
"""

from __future__ import annotations

import sys

from agrosmart.empacotar import FASE_PADRAO, ErroEmpacotamento, empacotar


def main(argv: list[str]) -> int:
    if len(argv) not in {3, 4}:
        print(__doc__)
        return 2
    nome, rm, turma = argv[:3]
    try:
        fase = int(argv[3]) if len(argv) == 4 else FASE_PADRAO
    except ValueError:
        print(f"erro: fase inválida: {argv[3]!r}")
        return 2
    try:
        resultado = empacotar(nome, rm, turma, fase=fase)
    except ErroEmpacotamento as erro:
        print(f"erro: {erro}")
        return 1
    tamanho_kb = resultado.bytes_zip / 1024
    print(f"{resultado.destino} · {resultado.arquivos} arquivos · {tamanho_kb:,.0f} KB")
    for aviso in resultado.avisos:
        print(f"  aviso: {aviso}")
    return 0 if resultado.integridade_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
