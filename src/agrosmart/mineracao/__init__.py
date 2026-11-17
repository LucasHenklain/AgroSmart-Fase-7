"""Mineração de processos sobre a trilha de eventos do AgroSmart (Fase 7).

Entrada única: :func:`minerar` lê ``outputs/logs/eventos.jsonl``, extrai o event log
(caso = ``execucao_id``), descobre o processo (DFG, variantes, Heuristics/Inductive Miner,
conformidade), analisa gargalos, falhas, retrabalho e v1×v2, e grava em ``pasta_saida``:

* ``event_log.csv`` (formato pm4py: ``case:concept:name``, ``concept:name``,
  ``lifecycle:transition``, ``time:timestamp``) e ``event_log_intervalos.csv``;
* ``event_log.xes`` (pm4py ``write_xes`` se instalado; senão XES 1.0 escrito à mão);
* ``dfg_frequencia.svg``/``.png`` e ``dfg_desempenho.svg``/``.png``;
* ``duracao_atividades.png``, ``lead_time_casos.png``, ``variantes.png`` (matplotlib);
* ``metricas.json`` e ``relatorio.md``.

pm4py, pandas e matplotlib são opcionais: sem eles, tudo exceto Inductive Miner/token
replay (substituídos por aproximações documentadas) e os PNGs continua funcionando.
"""

from __future__ import annotations

import contextlib
import io
import warnings
from collections import Counter
from pathlib import Path
from typing import Any

from agrosmart.mineracao import visual
from agrosmart.mineracao.analise import analisar, casos_em_regime
from agrosmart.mineracao.descoberta import descobrir
from agrosmart.mineracao.eventlog import (
    EventLog,
    carregar_event_log,
    exportar_csv,
    exportar_intervalos_csv,
    exportar_xes_manual,
    linhas_lifecycle,
    selecionar_exemplo,
)
from agrosmart.mineracao.relatorio import gerar_insights, salvar

__all__ = ["minerar", "carregar_event_log", "exportar_exemplo"]


def _ordenar_extensoes(destino: Path) -> None:
    """pm4py escreve as ``<extension>`` na ordem de um ``set``: ordena para o arquivo ser
    idêntico entre execuções."""
    linhas = destino.read_text(encoding="utf-8").splitlines(keepends=True)
    posicoes = [i for i, ln in enumerate(linhas) if ln.lstrip().startswith("<extension ")]
    for i, linha in zip(posicoes, sorted(linhas[i] for i in posicoes), strict=True):
        linhas[i] = linha
    destino.write_text("".join(linhas), encoding="utf-8")


def _xes(log: EventLog, destino: Path, usar_pm4py: bool) -> str:
    """Escreve o XES; devolve quem gerou ("pm4py" ou "manual")."""
    if usar_pm4py:
        try:
            import pandas as pd

            df = pd.DataFrame(linhas_lifecycle(log))
            df["time:timestamp"] = pd.to_datetime(df["time:timestamp"], utc=True, format="ISO8601")
            df["duracao_s"] = pd.to_numeric(df["duracao_s"], errors="coerce")
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
                warnings.catch_warnings(),
            ):
                warnings.simplefilter("ignore")
                import pm4py

                pm4py.write_xes(df, str(destino), case_id_key="case:concept:name")
            _ordenar_extensoes(destino)
            return "pm4py"
        except Exception:  # noqa: BLE001 - cai para o XES manual
            pass
    exportar_xes_manual(log, destino)
    return "manual"


def exportar_exemplo(log: EventLog, destino: Path, quantidade: int = 3) -> Path:
    """Recorte pequeno do event log (≈3 casos, com falha se houver) para a entrega."""
    return exportar_csv(log, destino, selecionar_exemplo(log, quantidade))


def _rotulo(caminho: Path) -> str:
    """Caminho relativo ao diretório atual quando possível (saída igual em qualquer máquina)."""
    try:
        return caminho.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return caminho.as_posix()


def _escopo(log: EventLog, caminho: Path) -> dict[str, Any]:
    return {
        "fonte": _rotulo(caminho),
        "eventos": log.total_eventos,
        "eventos_sem_caso": log.eventos_sem_caso,
        "eventos_fora_do_escopo": log.eventos_fora_do_escopo,
        "casos_fora_do_escopo": log.casos_fora_do_escopo,
        "linhas_invalidas": log.linhas_invalidas,
        "casos": len(log.casos),
        "instancias_atividade": sum(len(c.atividades) for c in log.casos),
        "por_versao": dict(sorted(Counter(c.versao_processo for c in log.casos).items())),
        "por_tipo": dict(sorted(Counter(c.tipo_caso for c in log.casos).items())),
        "por_status": dict(sorted(Counter(c.status for c in log.casos).items())),
        "por_origem": dict(sorted(Counter(c.origem for c in log.casos).items())),
        "etapas_puladas": dict(sorted(Counter(p for c in log.casos for p in c.puladas).items())),
        "periodo": {
            "inicio": log.casos[0].inicio.isoformat(timespec="seconds") if log.casos else None,
            "fim": max(c.fim for c in log.casos).isoformat(timespec="seconds")
            if log.casos
            else None,
        },
    }


def minerar(
    caminho_eventos: Path,
    pasta_saida: Path,
    *,
    apenas_worker: bool = True,
    incluir_dominio: bool = True,
    usar_pm4py: bool | None = None,
    graficos: bool = True,
    exemplo: Path | None = None,
) -> dict[str, Any]:
    """Roda a mineração ponta a ponta e devolve as métricas (as mesmas do ``metricas.json``).

    ``apenas_worker=False`` inclui os runs da CLI; ``usar_pm4py=None`` usa pm4py se
    importável; ``exemplo`` grava também o recorte de ≈3 casos nesse caminho.
    """
    caminho_eventos, pasta_saida = Path(caminho_eventos), Path(pasta_saida)
    log = carregar_event_log(
        caminho_eventos, apenas_worker=apenas_worker, incluir_dominio=incluir_dominio
    )
    regime = casos_em_regime(log.casos)
    pasta_saida.mkdir(parents=True, exist_ok=True)

    descoberta = descobrir(regime, usar_pm4py=usar_pm4py)
    pm4py_ok = bool(descoberta["pm4py"]) and not descoberta["pm4py"].get("erro")
    arquivos: dict[str, str | None] = {
        "event_log_csv": exportar_csv(log, pasta_saida / "event_log.csv").name,
        "event_log_intervalos_csv": exportar_intervalos_csv(
            log, pasta_saida / "event_log_intervalos.csv"
        ).name,
        "event_log_xes": "event_log.xes",
    }
    xes_por = _xes(log, pasta_saida / "event_log.xes", pm4py_ok)

    for modo in ("frequencia", "desempenho"):
        titulo = (
            f"DFG — {'frequência' if modo == 'frequencia' else 'desempenho'} · "
            f"{len(regime)} caso(s) em regime"
        )
        desenho = visual.montar_dfg(
            descoberta["dfg_frequencia"], descoberta["dfg_desempenho"], modo=modo, titulo=titulo
        )
        arquivos[f"dfg_{modo}_svg"] = visual.salvar_svg(
            desenho, pasta_saida / f"dfg_{modo}.svg"
        ).name
        if graficos:
            png = visual.salvar_png_dfg(desenho, pasta_saida / f"dfg_{modo}.png")
            arquivos[f"dfg_{modo}_png"] = png.name if png else None
    if graficos:
        for chave, funcao, alvo, nome in (
            ("duracoes_png", visual.grafico_duracoes, regime, "duracao_atividades.png"),
            ("lead_time_png", visual.grafico_lead_time, log.casos, "lead_time_casos.png"),
            ("variantes_png", visual.grafico_variantes, descoberta["variantes"], "variantes.png"),
        ):
            gerado = funcao(alvo, pasta_saida / nome)
            arquivos[chave] = gerado.name if gerado else None
    if exemplo is not None:
        arquivos["exemplo_csv"] = _rotulo(exportar_exemplo(log, Path(exemplo)))

    metricas: dict[str, Any] = {
        "escopo": _escopo(log, caminho_eventos),
        "configuracao": {
            "apenas_worker": apenas_worker,
            "incluir_dominio": incluir_dominio,
            "pm4py": pm4py_ok,
            "xes_gerado_por": xes_por,
        },
        "descoberta": descoberta,
        "analise": analisar(log),
        "arquivos": {k: v for k, v in arquivos.items() if v},
    }
    metricas["insights"] = gerar_insights(metricas)
    salvar(metricas, pasta_saida)
    metricas["arquivos"]["metricas_json"] = "metricas.json"
    metricas["arquivos"]["relatorio_md"] = "relatorio.md"
    return metricas
