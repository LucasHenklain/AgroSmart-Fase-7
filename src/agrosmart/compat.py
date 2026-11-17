"""Compatibilidade entre versões de bibliotecas com API instável.

Achado real da Fase 7: o primeiro ciclo do worker em container falhou no gold
porque o ``pip`` resolveu o DuckDB 2.x na imagem enquanto o host tinha o 1.3 — o
resultado de ``con.execute(...)`` deixou de ter ``fetch_arrow_table()`` e passou a
ter ``to_arrow_table()``. O pipeline DevSecOps agora roda os testes **dentro da
imagem** também, e este módulo isola a diferença num ponto só.
"""

from __future__ import annotations

import warnings
from typing import Any


def para_arrow(resultado: Any) -> Any:
    """Resultado de ``con.execute(sql)`` → ``pyarrow.Table`` (schema preservado sem linhas).

    DuckDB 1.x expõe ``fetch_arrow_table()``; o 2.x, ``to_arrow_table()`` (e avisa que a
    cópia extra existe — irrelevante para tabelas do gold, de centenas de linhas).
    """
    metodo = getattr(resultado, "to_arrow_table", None) or resultado.fetch_arrow_table
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Arrow output on a DB-API result")
        return metodo()
