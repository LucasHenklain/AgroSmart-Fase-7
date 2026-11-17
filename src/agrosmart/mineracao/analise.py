"""Análise do processo: gargalos, espera, variação, falhas/MTTR, retrabalho, throughput, v1×v2.

Gargalos e lead time olham o **regime** (ciclos do worker): a carga inicial simula o
histórico das seis fontes, roda uma vez e distorceria qualquer média — ela é contada à parte.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from agrosmart.mineracao.descoberta import dfg_desempenho, resumo_estatistico, variantes
from agrosmart.mineracao.eventlog import Caso, EventLog

ETAPAS_DERIVADAS = ("silver", "gold", "catalogo")


def casos_em_regime(casos: Sequence[Caso]) -> list[Caso]:
    regime = [c for c in casos if c.tipo_caso != "carga_inicial"]
    return regime or list(casos)


def _iso(instante: datetime | None) -> str | None:
    return instante.isoformat(timespec="milliseconds") if instante else None


# ---------------------------------------------------------------------------- gargalos


def gargalos(casos: Sequence[Caso]) -> list[dict[str, Any]]:
    """Participação de cada atividade (de etapa) no tempo de ciclo."""
    lead_total = sum(c.lead_time_s for c in casos)
    duracoes: dict[str, list[float]] = {}
    pct_por_caso: dict[str, list[float]] = {}
    for caso in casos:
        for ativ in caso.etapas():
            duracoes.setdefault(ativ.atividade, []).append(ativ.duracao_s)
            if caso.lead_time_s > 0:
                pct_por_caso.setdefault(ativ.atividade, []).append(
                    100 * ativ.duracao_s / caso.lead_time_s
                )
    saida = []
    for atividade, valores in duracoes.items():
        total = sum(valores)
        saida.append(
            {
                "atividade": atividade,
                **resumo_estatistico(valores),
                "total_s": round(total, 3),
                "pct_lead_time": round(100 * total / lead_total, 1) if lead_total else 0.0,
                "pct_medio_por_caso": round(statistics.fmean(pct_por_caso[atividade]), 1)
                if pct_por_caso.get(atividade)
                else 0.0,
                "cv": round(statistics.pstdev(valores) / statistics.fmean(valores), 3)
                if len(valores) > 1 and statistics.fmean(valores) > 0
                else 0.0,
            }
        )
    saida.sort(key=lambda g: (-g["pct_lead_time"], g["atividade"]))
    return saida


def ociosidade(casos: Sequence[Caso]) -> dict[str, Any]:
    """Tempo do caso fora de qualquer etapa (orquestração, imports, espera entre etapas)."""
    ociosos, pcts = [], []
    for caso in casos:
        intervalos = sorted((a.inicio, a.fim) for a in caso.etapas())
        coberto = 0.0
        fim_corrente: datetime | None = None
        for inicio, fim in intervalos:
            if fim_corrente is None or inicio > fim_corrente:
                coberto += (fim - inicio).total_seconds()
                fim_corrente = fim
            elif fim > fim_corrente:
                coberto += (fim - fim_corrente).total_seconds()
                fim_corrente = fim
        ocioso = max(0.0, caso.lead_time_s - coberto)
        ociosos.append(ocioso)
        if caso.lead_time_s > 0:
            pcts.append(100 * ocioso / caso.lead_time_s)
    total_lead = sum(c.lead_time_s for c in casos)
    return {
        **resumo_estatistico(ociosos),
        "pct_lead_time": round(100 * sum(ociosos) / total_lead, 1) if total_lead else 0.0,
    }


def esperas_relevantes(desempenho: dict[str, Any], limite: int = 10) -> list[dict[str, Any]]:
    linhas = [
        {"aresta": aresta, **estat}
        for aresta, estat in desempenho["esperas"].items()
        if estat["n"] and estat["max"] > 0
    ]
    linhas.sort(key=lambda e: (-e["media"], e["aresta"]))
    return linhas[:limite]


# ----------------------------------------------------------------------------- variação


def variacao(casos: Sequence[Caso], lista_variantes: list[dict[str, Any]]) -> dict[str, Any]:
    leads = [c.lead_time_s for c in casos]
    estat = resumo_estatistico(leads)
    media = estat["media"]
    return {
        "variantes": len(lista_variantes),
        "casos": len(casos),
        "pct_variante_principal": lista_variantes[0]["pct_casos"] if lista_variantes else 0.0,
        "lead_time": estat,
        "cv_lead_time": round(estat["desvio"] / media, 3) if media else 0.0,
    }


# ------------------------------------------------------------------------ falhas e MTTR


def falhas_por_atividade(casos: Sequence[Caso]) -> list[dict[str, Any]]:
    execucoes: Counter[str] = Counter()
    falhas: Counter[str] = Counter()
    erros: dict[str, Counter[str]] = {}
    for caso in casos:
        for ativ in caso.etapas():
            execucoes[ativ.etapa] += 1
            if ativ.falhou:
                falhas[ativ.etapa] += 1
                erros.setdefault(ativ.etapa, Counter())[
                    ativ.mensagem[:140] or "(sem mensagem)"
                ] += 1
    saida = [
        {
            "etapa": etapa,
            "execucoes": n,
            "falhas": falhas[etapa],
            "taxa_falha_pct": round(100 * falhas[etapa] / n, 1),
            "erros": [
                {"mensagem": m, "ocorrencias": q}
                for m, q in erros.get(etapa, Counter()).most_common(3)
            ],
        }
        for etapa, n in execucoes.items()
    ]
    saida.sort(key=lambda f: (-f["taxa_falha_pct"], -f["falhas"], f["etapa"]))
    return saida


def incidentes(casos: Sequence[Caso]) -> dict[str, Any]:
    """Incidente = sequência de casos com falha; termina no próximo caso concluído com sucesso.

    TTR = fim do caso que recuperou − instante da primeira falha (fim da atividade que falhou).
    """
    lista: list[dict[str, Any]] = []
    aberto: dict[str, Any] | None = None
    for caso in casos:
        if caso.falhou:
            if aberto is None:
                falha = next((a for a in caso.atividades if a.falhou), None)
                instante = falha.fim if falha else caso.fim
                aberto = {
                    "inicio": instante,
                    "caso_inicial": caso.id,
                    "atividade": falha.atividade if falha else "(caso sem etapa com falha)",
                    "mensagem": falha.mensagem[:140] if falha else "",
                    "casos_afetados": [caso.id],
                    "versao_processo": caso.versao_processo,
                }
            else:
                aberto["casos_afetados"].append(caso.id)
        elif aberto is not None:
            ttr = (caso.fim - aberto["inicio"]).total_seconds()
            lista.append(
                {**aberto, "recuperado_em": caso.fim, "caso_recuperacao": caso.id, "ttr_s": ttr}
            )
            aberto = None
    ttrs = [i["ttr_s"] for i in lista]
    serializar = [
        {**i, "inicio": _iso(i["inicio"]), "recuperado_em": _iso(i.get("recuperado_em"))}
        for i in lista
    ]
    em_aberto = None
    if aberto is not None:
        em_aberto = {**aberto, "inicio": _iso(aberto["inicio"])}
    return {
        "incidentes": serializar,
        "em_aberto": em_aberto,
        "quantidade": len(lista) + (aberto is not None),
        "mttr_s": round(statistics.fmean(ttrs), 3) if ttrs else None,
        "ttr": resumo_estatistico(ttrs),
    }


# ---------------------------------------------------------------------------- retrabalho


def retentativas(casos: Sequence[Caso]) -> list[dict[str, Any]]:
    """Etapa que falhou num caso e foi executada de novo no caso seguinte."""
    saida = []
    for atual, seguinte in zip(casos, casos[1:], strict=False):
        for falha in (a for a in atual.etapas() if a.falhou):
            nova = next((a for a in seguinte.etapas() if a.etapa == falha.etapa), None)
            if nova is None:
                continue
            saida.append(
                {
                    "etapa": falha.etapa,
                    "caso_com_falha": atual.id,
                    "caso_retentativa": seguinte.id,
                    "sucesso": not nova.falhou,
                    "intervalo_s": round((nova.inicio - falha.fim).total_seconds(), 3),
                }
            )
    return saida


def republicacoes(casos: Sequence[Caso]) -> dict[str, Any]:
    """Mesmo comando (tópico + payload) publicado de novo para o mesmo talhão.

    Compara cada comando com o último publicado para o talhão; ``consecutivo`` marca os
    que repetem o comando do caso imediatamente anterior.
    """
    ultimo: dict[str, tuple[str, int, str]] = {}
    repetidos: list[dict[str, Any]] = []
    total = 0
    por_talhao: Counter[str] = Counter()
    minutos = 0.0
    for indice, caso in enumerate(casos):
        for comando in caso.comandos:
            total += 1
            anterior = ultimo.get(comando.talhao)
            if anterior and anterior[0] == comando.assinatura:
                por_talhao[comando.talhao] += 1
                valor = comando.payload.get("minutos")
                if isinstance(valor, int | float) and comando.payload.get("acao") == "ligar":
                    minutos += float(valor)
                repetidos.append(
                    {
                        "talhao": comando.talhao,
                        "topico": comando.topico,
                        "payload": comando.payload,
                        "caso": caso.id,
                        "caso_anterior": anterior[2],
                        "consecutivo": anterior[1] >= indice - 1,
                    }
                )
            ultimo[comando.talhao] = (comando.assinatura, indice, caso.id)
    return {
        "comandos": total,
        "republicacoes": len(repetidos),
        "consecutivas": sum(1 for r in repetidos if r["consecutivo"]),
        "pct_redundante": round(100 * len(repetidos) / total, 1) if total else 0.0,
        "por_talhao": dict(sorted(por_talhao.items())),
        "minutos_irrigacao_repetidos": round(minutos, 1),
        "exemplos": repetidos[:5],
    }


def ciclos_sem_novidade(casos: Sequence[Caso]) -> dict[str, Any]:
    """Ciclos cuja ingestão não gravou nem atualizou lote — e o que foi reprocessado à toa."""
    sem_novidade = reprocessaram = 0
    tempo = 0.0
    for caso in casos:
        ingestao = next((a for a in caso.etapas() if a.etapa == "ingerir" and not a.falhou), None)
        if ingestao is None or "gravados" not in ingestao.atributos:
            continue
        novos = ingestao.atributos.get("gravados", 0) + ingestao.atributos.get("atualizados", 0)
        if novos > 0:
            continue
        sem_novidade += 1
        derivadas = [a for a in caso.etapas() if a.etapa in ETAPAS_DERIVADAS]
        if derivadas:
            reprocessaram += 1
            tempo += sum(a.duracao_s for a in derivadas)
    com_ingestao = sum(
        1 for c in casos if any(a.etapa == "ingerir" and not a.falhou for a in c.etapas())
    )
    return {
        "casos_com_ingestao_ok": com_ingestao,
        "sem_lote_novo": sem_novidade,
        "pct_sem_lote_novo": round(100 * sem_novidade / com_ingestao, 1) if com_ingestao else 0.0,
        "reprocessaram_derivadas": reprocessaram,
        "tempo_reprocessado_s": round(tempo, 3),
    }


# ---------------------------------------------------------------------------- throughput


def throughput(casos: Sequence[Caso]) -> dict[str, Any]:
    if not casos:
        return {"casos": 0, "janela_h": 0.0, "casos_por_hora": 0.0, "intervalo_entre_inicios": {}}
    inicio = min(c.inicio for c in casos)
    fim = max(c.fim for c in casos)
    horas = (fim - inicio).total_seconds() / 3600
    ordenados = sorted(casos, key=lambda c: c.inicio)
    intervalos = [
        (b.inicio - a.inicio).total_seconds()
        for a, b in zip(ordenados, ordenados[1:], strict=False)
    ]
    sucesso = sum(1 for c in casos if not c.falhou)
    if len(casos) < 2:  # um caso só não define taxa: a "janela" é o próprio lead time
        horas = 0.0
    return {
        "casos": len(casos),
        "casos_ok": sucesso,
        "inicio": _iso(inicio),
        "fim": _iso(fim),
        "janela_h": round(horas, 3),
        "casos_por_hora": round(len(casos) / horas, 2) if horas > 0 else None,
        "casos_ok_por_hora": round(sucesso / horas, 2) if horas > 0 else None,
        "intervalo_entre_inicios": resumo_estatistico(intervalos),
    }


# ----------------------------------------------------------------------------- v1 × v2


def _perfil(casos: Sequence[Caso]) -> dict[str, Any]:
    leads = [c.lead_time_s for c in casos]
    ok = [c.lead_time_s for c in casos if not c.falhou]
    derivadas = sum(1 for c in casos if any(a.etapa in ETAPAS_DERIVADAS for a in c.etapas()))
    return {
        "casos": len(casos),
        "taxa_sucesso_pct": round(100 * sum(not c.falhou for c in casos) / len(casos), 1),
        "lead_time": resumo_estatistico(leads),
        "lead_time_casos_ok": resumo_estatistico(ok),
        "etapas_por_caso": round(statistics.fmean(len(c.etapas()) for c in casos), 2),
        "pct_casos_com_silver_gold": round(100 * derivadas / len(casos), 1),
        "comandos_por_caso": round(statistics.fmean(len(c.comandos) for c in casos), 2),
        "throughput": throughput(casos),
        "gargalo": (gargalos(casos) or [{"atividade": None}])[0]["atividade"],
    }


def comparar_versoes(casos: Sequence[Caso]) -> dict[str, Any]:
    versoes: dict[str, list[Caso]] = {}
    for caso in casos:
        versoes.setdefault(caso.versao_processo, []).append(caso)
    perfis = {v: _perfil(lista) for v, lista in sorted(versoes.items())}
    saida: dict[str, Any] = {"versoes": perfis, "comparacao": None}
    if "v1" in perfis and "v2" in perfis:
        a, b = perfis["v1"], perfis["v2"]

        def delta(x: float, y: float) -> float | None:
            return round(100 * (y - x) / x, 1) if x else None

        saida["comparacao"] = {
            "delta_lead_time_mediano_pct": delta(
                a["lead_time_casos_ok"]["mediana"], b["lead_time_casos_ok"]["mediana"]
            ),
            "delta_lead_time_medio_pct": delta(
                a["lead_time_casos_ok"]["media"], b["lead_time_casos_ok"]["media"]
            ),
            "delta_p95_pct": delta(a["lead_time_casos_ok"]["p95"], b["lead_time_casos_ok"]["p95"]),
            "delta_taxa_sucesso_pp": round(b["taxa_sucesso_pct"] - a["taxa_sucesso_pct"], 1),
            "delta_etapas_por_caso": round(b["etapas_por_caso"] - a["etapas_por_caso"], 2),
        }
    return saida


# --------------------------------------------------------------------------------- tudo


def analisar(log: EventLog) -> dict[str, Any]:
    regime = casos_em_regime(log.casos)
    return {
        "casos_em_regime": len(regime),
        "gargalos": gargalos(regime),
        "ociosidade": ociosidade(regime),
        "esperas": esperas_relevantes(dfg_desempenho(regime)),
        "variacao": variacao(regime, variantes(regime)),
        "falhas": falhas_por_atividade(log.casos),
        "incidentes": incidentes(log.casos),
        "retentativas": retentativas(log.casos),
        "republicacoes": republicacoes(log.casos),
        "sem_novidade": ciclos_sem_novidade(regime),
        "throughput": throughput(regime),
        "versoes": comparar_versoes(regime),
    }
