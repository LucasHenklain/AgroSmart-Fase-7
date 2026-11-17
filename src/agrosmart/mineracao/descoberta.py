"""Descoberta de processo: DFG, variantes, Heuristics/Inductive Miner e conformidade.

O fluxo de controle de cada caso é a sequência das atividades ordenadas pelo **início**
(forma intervalo). DFG, variantes, dependências do Heuristics Miner e a conformidade por
arestas são calculados em Python puro — o resultado não depende de pm4py estar
instalado. Quando pm4py é importável, ele contribui com o que não vale reimplementar:
Inductive Miner (árvore de processo, Petri e BPMN) e o *token-based replay* clássico.
"""

from __future__ import annotations

import contextlib
import io
import statistics
import warnings
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

from agrosmart.mineracao.eventlog import FIM, INICIO, Caso

Aresta = tuple[str, str]


def percentil(valores: Sequence[float], p: float) -> float:
    """Percentil com interpolação linear (igual a ``numpy.percentile`` padrão)."""
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    if len(ordenados) == 1:
        return float(ordenados[0])
    posicao = (len(ordenados) - 1) * p / 100
    baixo = int(posicao)
    alto = min(baixo + 1, len(ordenados) - 1)
    return float(ordenados[baixo] + (ordenados[alto] - ordenados[baixo]) * (posicao - baixo))


def resumo_estatistico(valores: Sequence[float]) -> dict[str, float]:
    if not valores:
        return {"n": 0, "media": 0.0, "mediana": 0.0, "p95": 0.0, "max": 0.0, "desvio": 0.0}
    return {
        "n": len(valores),
        "media": round(statistics.fmean(valores), 3),
        "mediana": round(statistics.median(valores), 3),
        "p95": round(percentil(valores, 95), 3),
        "max": round(max(valores), 3),
        "min": round(min(valores), 3),
        "desvio": round(statistics.pstdev(valores), 3) if len(valores) > 1 else 0.0,
    }


def tracos(casos: Iterable[Caso]) -> list[tuple[str, ...]]:
    return [c.variante for c in casos]


# ------------------------------------------------------------------------------- DFG


def dfg_frequencia(casos: Iterable[Caso]) -> dict[str, Any]:
    """DFG com frequência: arestas a→b (com nós artificiais de início/fim) e nós."""
    arestas: Counter[Aresta] = Counter()
    nos: Counter[str] = Counter()
    casos_por_no: Counter[str] = Counter()
    for traco in tracos(casos):
        nos.update(traco)
        casos_por_no.update(set(traco))
        caminho = (INICIO, *traco, FIM)
        arestas.update(zip(caminho, caminho[1:], strict=False))
    return {
        "nos": dict(sorted(nos.items())),
        "casos_por_no": dict(sorted(casos_por_no.items())),
        "arestas": {f"{a} -> {b}": n for (a, b), n in sorted(arestas.items())},
        "_arestas": dict(arestas),
    }


def dfg_desempenho(casos: Iterable[Caso]) -> dict[str, Any]:
    """Duração das atividades e tempo de espera em cada aresta (início(b) − fim(a), ≥ 0)."""
    duracoes: dict[str, list[float]] = {}
    esperas: dict[Aresta, list[float]] = {}
    for caso in casos:
        for ativ in caso.atividades:
            duracoes.setdefault(ativ.atividade, []).append(ativ.duracao_s)
        anterior_fim, anterior_nome = caso.inicio, INICIO
        for ativ in caso.atividades:
            espera = max(0.0, (ativ.inicio - anterior_fim).total_seconds())
            esperas.setdefault((anterior_nome, ativ.atividade), []).append(espera)
            anterior_fim = max(anterior_fim, ativ.fim)  # atividade aninhada não "volta" o relógio
            anterior_nome = ativ.atividade
        espera_fim = max(0.0, (caso.fim - anterior_fim).total_seconds())
        esperas.setdefault((anterior_nome, FIM), []).append(espera_fim)
    return {
        "atividades": {k: resumo_estatistico(v) for k, v in sorted(duracoes.items())},
        "esperas": {f"{a} -> {b}": resumo_estatistico(v) for (a, b), v in sorted(esperas.items())},
        "_esperas": {k: resumo_estatistico(v) for k, v in esperas.items()},
    }


# ------------------------------------------------------------------------- variantes


def variantes(casos: Sequence[Caso]) -> list[dict[str, Any]]:
    """Variantes (sequência de atividades), frequência, % de casos e lead time."""
    grupos: dict[tuple[str, ...], list[Caso]] = {}
    for caso in casos:
        grupos.setdefault(caso.variante, []).append(caso)
    total = len(casos) or 1
    saida = []
    for traco, membros in grupos.items():
        leads = [c.lead_time_s for c in membros]
        saida.append(
            {
                "variante": " → ".join(traco) if traco else "(vazia)",
                "atividades": list(traco),
                "casos": len(membros),
                "pct_casos": round(100 * len(membros) / total, 1),
                "lead_time": resumo_estatistico(leads),
                "versoes": dict(sorted(Counter(c.versao_processo for c in membros).items())),
                "com_falha": any(c.falhou for c in membros),
                "exemplos": [c.id for c in membros[:3]],
            }
        )
    saida.sort(key=lambda v: (-v["casos"], v["variante"]))
    for posicao, variante in enumerate(saida, start=1):
        variante["id"] = f"V{posicao}"
    return saida


# --------------------------------------------------------------- Heuristics (dependências)


def dependencias_heuristicas(casos: Iterable[Caso], limiar: float = 0.5) -> list[dict[str, Any]]:
    """Medida de dependência do Heuristics Miner: (|a>b| − |b>a|) / (|a>b| + |b>a| + 1).

    Para laços curtos (a>a): |a>a| / (|a>a| + 1). Ficam as relações ≥ ``limiar``.
    """
    df = dfg_frequencia(casos)["_arestas"]
    saida = []
    for (a, b), ab in df.items():
        if a == INICIO or b == FIM:
            continue
        ba = df.get((b, a), 0)
        dep = ab / (ab + 1) if a == b else (ab - ba) / (ab + ba + 1)
        if dep >= limiar:
            saida.append({"de": a, "para": b, "frequencia": ab, "dependencia": round(dep, 3)})
    saida.sort(key=lambda d: (-d["dependencia"], -d["frequencia"], d["de"], d["para"]))
    return saida


# --------------------------------------------------- Inductive Miner (fallback por variantes)


def arvore_por_variantes(casos: Sequence[Caso]) -> str:
    """Aproximação de árvore de processo sem pm4py: prefixo e sufixo comuns em sequência e
    o miolo das variantes numa escolha exclusiva (``X``). Notação igual à do pm4py."""
    unicos = sorted({c.variante for c in casos})
    if not unicos:
        return "tau"
    prefixo: list[str] = []
    for passos in zip(*unicos, strict=False):
        if len(set(passos)) != 1:
            break
        prefixo.append(passos[0])
    restos = [t[len(prefixo) :] for t in unicos]
    sufixo: list[str] = []
    for passos in zip(*(r[::-1] for r in restos), strict=False):
        if len(set(passos)) != 1:
            break
        sufixo.insert(0, passos[0])
    miolos = sorted({r[: len(r) - len(sufixo)] for r in restos})

    def seq(itens: Sequence[str]) -> str:
        rotulos = [f"'{i}'" for i in itens]
        if not rotulos:
            return "tau"
        return rotulos[0] if len(rotulos) == 1 else f"->( {', '.join(rotulos)} )"

    partes = [f"'{p}'" for p in prefixo]
    if miolos != [()]:
        ramos = [seq(m) for m in miolos]
        partes.append(ramos[0] if len(ramos) == 1 else f"X( {', '.join(ramos)} )")
    partes.extend(f"'{s}'" for s in sufixo)
    return partes[0] if len(partes) == 1 else f"->( {', '.join(partes)} )"


# ----------------------------------------------------------------------- conformidade


def conformidade_por_arestas(casos: Sequence[Caso], referencia: Sequence[Caso]) -> dict[str, Any]:
    """Fallback do token replay: o modelo de referência é o DFG dos casos de ``referencia``
    (caminhos felizes); um caso é conforme se todas as suas transições existem no modelo.
    fitness = transições permitidas / transições totais."""
    permitidas = set(dfg_frequencia(referencia)["_arestas"])
    total = ok = conformes = 0
    desvios: Counter[str] = Counter()
    for caso in casos:
        caminho = (INICIO, *caso.variante, FIM)
        pares = list(zip(caminho, caminho[1:], strict=False))
        bons = sum(1 for p in pares if p in permitidas)
        for par in pares:
            if par not in permitidas:
                desvios[f"{par[0]} -> {par[1]}"] += 1
        total += len(pares)
        ok += bons
        conformes += bons == len(pares)
    return {
        "metodo": "arestas do DFG de referência (fallback sem pm4py)",
        "fitness": round(ok / total, 4) if total else 1.0,
        "pct_casos_conformes": round(100 * conformes / len(casos), 1) if casos else 100.0,
        "desvios": dict(desvios.most_common()),
    }


# ------------------------------------------------------------------------------ pm4py


def pm4py_disponivel() -> bool:
    try:
        _importar_pm4py()
    except Exception:  # noqa: BLE001 - qualquer falha de import = indisponível
        return False
    return True


def _importar_pm4py() -> Any:
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore")
        import pm4py

    return pm4py


def dataframe_pm4py(casos: Sequence[Caso]) -> Any:
    """Uma linha por instância de atividade, ``time:timestamp`` = início (ordem do fluxo)."""
    import pandas as pd

    linhas = []
    for caso in casos:
        for ordem, ativ in enumerate(caso.atividades):
            linhas.append(
                {
                    "case:concept:name": caso.id,
                    "concept:name": ativ.atividade,
                    "time:timestamp": ativ.inicio,
                    "start_timestamp": ativ.inicio,
                    "ordem": ordem,
                }
            )
    df = pd.DataFrame(linhas)
    if not df.empty:
        df["time:timestamp"] = pd.to_datetime(df["time:timestamp"], utc=True)
        df["start_timestamp"] = pd.to_datetime(df["start_timestamp"], utc=True)
        df = df.sort_values(["case:concept:name", "time:timestamp", "ordem"], kind="stable")
    return df.reset_index(drop=True)


def _texto_petri(net: Any) -> dict[str, Any]:
    visiveis = sorted(t.label for t in net.transitions if t.label)
    silenciosas = sum(1 for t in net.transitions if not t.label)
    return {
        "lugares": len(net.places),
        "transicoes_visiveis": visiveis,
        "transicoes_silenciosas": silenciosas,
        "arcos": len(net.arcs),
    }


def _texto_bpmn(bpmn: Any) -> dict[str, Any]:
    tipos: Counter[str] = Counter(type(n).__name__ for n in bpmn.get_nodes())
    tarefas = sorted(n.get_name() for n in bpmn.get_nodes() if type(n).__name__ == "Task")
    fluxos = []
    for fluxo in bpmn.get_flows():
        origem, destino = fluxo.get_source(), fluxo.get_target()
        if type(origem).__name__ == "Task" and type(destino).__name__ == "Task":
            fluxos.append(f"{origem.get_name()} -> {destino.get_name()}")
    return {
        "elementos": dict(sorted(tipos.items())),
        "tarefas": tarefas,
        "fluxos_diretos_entre_tarefas": sorted(fluxos),
        "fluxos": len(bpmn.get_flows()),
    }


def descobrir_pm4py(casos: Sequence[Caso], referencia: Sequence[Caso]) -> dict[str, Any]:
    """Inductive Miner, Heuristics Miner e token replay com pm4py."""
    pm4py = _importar_pm4py()
    df = dataframe_pm4py(casos)
    saida: dict[str, Any] = {"versao_pm4py": getattr(pm4py, "__version__", "?")}
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore")
        arvore = pm4py.discover_process_tree_inductive(df)
        saida["arvore_inductive"] = str(arvore)
        net, im, fm = pm4py.convert_to_petri_net(arvore)
        saida["petri_inductive"] = _texto_petri(net)
        saida["bpmn_inductive"] = _texto_bpmn(pm4py.convert_to_bpmn(arvore))
        replay = pm4py.fitness_token_based_replay(df, net, im, fm)
        saida["token_replay_modelo_descoberto"] = {
            "fitness": round(float(replay.get("log_fitness", 0.0)), 4),
            "pct_casos_conformes": round(float(replay.get("perc_fit_traces", 0.0)), 1),
        }
        heu = pm4py.discover_heuristics_net(df, dependency_threshold=0.5)
        dependencias = []
        for a, destinos in sorted(heu.dependency_matrix.items()):
            for b, valor in sorted(destinos.items()):
                if valor >= 0.5:
                    dependencias.append({"de": a, "para": b, "dependencia": round(float(valor), 3)})
        saida["heuristics_pm4py"] = dependencias
        if referencia:
            df_ref = dataframe_pm4py(referencia)
            arvore_ref = pm4py.discover_process_tree_inductive(df_ref)
            net_r, im_r, fm_r = pm4py.convert_to_petri_net(arvore_ref)
            replay_r = pm4py.fitness_token_based_replay(df, net_r, im_r, fm_r)
            saida["arvore_caminho_feliz"] = str(arvore_ref)
            saida["token_replay_caminho_feliz"] = {
                "fitness": round(float(replay_r.get("log_fitness", 0.0)), 4),
                "pct_casos_conformes": round(float(replay_r.get("perc_fit_traces", 0.0)), 1),
            }
    return saida


def descobrir(casos: Sequence[Caso], *, usar_pm4py: bool | None = None) -> dict[str, Any]:
    """Tudo o que é descoberta. ``usar_pm4py=None`` usa pm4py se ele for importável."""
    referencia = [c for c in casos if not c.falhou]
    resultado: dict[str, Any] = {
        "dfg_frequencia": dfg_frequencia(casos),
        "dfg_desempenho": dfg_desempenho(casos),
        "variantes": variantes(casos),
        "heuristics": dependencias_heuristicas(casos),
        "arvore_variantes": arvore_por_variantes(casos),
        "conformidade_caminho_feliz": conformidade_por_arestas(casos, referencia),
        "pm4py": None,
    }
    if usar_pm4py is None:
        usar_pm4py = pm4py_disponivel()
    if usar_pm4py and casos:
        try:
            resultado["pm4py"] = descobrir_pm4py(casos, referencia)
        except Exception as erro:  # noqa: BLE001 - pm4py é opcional: registra e segue
            resultado["pm4py"] = {"erro": f"{type(erro).__name__}: {erro}"}
    return resultado
