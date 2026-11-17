"""Mineração de processos sobre a trilha de eventos (Fase 7).

Uso::

    python scripts/minerar_processos.py                       # só casos do worker
    python scripts/minerar_processos.py --incluir-cli          # inclui runs da CLI
    python scripts/minerar_processos.py --eventos x.jsonl --saida pasta/ --sem-exemplo

Gera em ``--saida`` (padrão ``outputs/processos``): event_log.csv/.xes, DFGs em SVG/PNG,
gráficos, metricas.json e relatorio.md; e o recorte ``data/exemplos/fase7/event_log_exemplo.csv``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agrosmart.config import RAIZ_PROJETO
from agrosmart.mineracao import minerar


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--eventos", type=Path, default=RAIZ_PROJETO / "outputs" / "logs" / "eventos.jsonl"
    )
    parser.add_argument("--saida", type=Path, default=RAIZ_PROJETO / "outputs" / "processos")
    parser.add_argument(
        "--incluir-cli", action="store_true", help="inclui casos de runs da CLI (origem=cli)"
    )
    parser.add_argument(
        "--exemplo",
        type=Path,
        default=RAIZ_PROJETO / "data" / "exemplos" / "fase7" / "event_log_exemplo.csv",
        help="onde gravar o recorte de ≈3 casos",
    )
    parser.add_argument("--sem-exemplo", action="store_true", help="não grava o recorte")
    parser.add_argument("--sem-pm4py", action="store_true", help="força os fallbacks sem pm4py")
    parser.add_argument("--sem-graficos", action="store_true", help="não gera PNGs (matplotlib)")
    args = parser.parse_args(argv)

    metricas = minerar(
        args.eventos,
        args.saida,
        apenas_worker=not args.incluir_cli,
        usar_pm4py=False if args.sem_pm4py else None,
        graficos=not args.sem_graficos,
        exemplo=None if args.sem_exemplo else args.exemplo,
    )
    esc, analise = metricas["escopo"], metricas["analise"]
    print(
        f"{esc['casos']} caso(s) · {esc['instancias_atividade']} atividades · "
        f"{esc['eventos_sem_caso']} eventos sem execucao_id ignorados · "
        f"pm4py={metricas['configuracao']['pm4py']}"
    )
    if analise["gargalos"]:
        g = analise["gargalos"][0]
        print(f"gargalo: {g['atividade']} ({g['pct_lead_time']}% do lead time)")
    print(f"{len(metricas['insights'])} insight(s) · saída em {args.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
