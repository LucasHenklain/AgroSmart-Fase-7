"""Relatório da mineração de processos: ``metricas.json`` e ``relatorio.md``.

Nenhum número é escrito à mão: tudo sai das métricas calculadas sobre o event log. A seção
"Insights e oportunidades" aplica regras simples sobre essas métricas (maior fatia do lead
time = gargalo; variante com falha → causa; comando repetido = retrabalho; etc.).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agrosmart.mineracao.visual import formatar_duracao as fd

SUGESTOES_GARGALO = {
    "silver": "reprocessar só as partições dos lotes novos e pular silver/gold/catálogo quando a "
    "ingestão não grava lote novo (ciclo incremental, v2)",
    "gold": "materializar o gold de forma incremental "
    "(só as tabelas afetadas pelas partições novas)",
    "catalogo": "regenerar o catálogo só quando o schema ou as contagens mudarem",
    "ingerir": "listar só objetos novos na landing (watermark) e paralelizar os uploads",
    "decidir": "reaproveitar a conexão de leitura do gold entre decisão e atuação",
    "atuar": "manter a conexão MQTT aberta entre ciclos e usar timeout curto no broker",
    "simular": "a simulação é só da carga inicial; no regime ela não deveria rodar",
}
SUGESTOES_FALHA = {
    "ingerir": "health-check do MinIO antes do ciclo e retentativa com backoff dentro da etapa",
    "silver": "health-check do lake antes do ciclo e retentativa com backoff dentro da etapa",
    "gold": "retentativa com backoff e escrita atômica das tabelas",
    "atuar": "fila local de comandos com reenvio quando o broker voltar (QoS 1 já garante "
    "a entrega uma vez conectado)",
    "decidir": "validar o gold antes da decisão e degradar para o último plano válido",
}


def _pct(valor: float | None) -> str:
    return "—" if valor is None else f"{_dec(valor, 1)}%".replace(".", ",")


def _dec(valor: float | None, casas: int = 1) -> str:
    return "—" if valor is None else f"{valor:.{casas}f}".replace(".", ",")


def _limpar(obj: Any) -> Any:
    """Remove chaves internas (``_…``, com tuplas) e deixa o resto serializável."""
    if isinstance(obj, dict):
        return {str(k): _limpar(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list | tuple):
        return [_limpar(v) for v in obj]
    return obj


def gerar_insights(m: dict[str, Any]) -> list[dict[str, str]]:
    """Regras sobre as métricas → lista de {tema, evidencia, oportunidade}."""
    a = m["analise"]
    insights: list[dict[str, str]] = []

    def add(tema: str, evidencia: str, oportunidade: str) -> None:
        insights.append({"tema": tema, "evidencia": evidencia, "oportunidade": oportunidade})

    n = a["casos_em_regime"]
    if n == 0:
        add(
            "Dados",
            "o event log não tem casos do worker com `execucao_id`.",
            "rodar o worker (`agrosmart worker`) para gerar ciclos e repetir a mineração.",
        )
        return insights
    if n < 5:
        add(
            "Amostra",
            f"só {n} caso(s) em regime no log: os números são indicativos.",
            "acumular mais ciclos (inclusive com falhas injetadas) antes de concluir.",
        )

    g = a["gargalos"]
    if g:
        topo = g[0]
        add(
            "Gargalo",
            f"`{topo['atividade']}` concentra {_pct(topo['pct_lead_time'])} do lead time "
            f"(mediana {fd(topo['mediana'])}, p95 {fd(topo['p95'])}, máx. {fd(topo['max'])}).",
            SUGESTOES_GARGALO.get(topo["atividade"], "investigar a atividade com maior fatia."),
        )
        if len(g) > 1:
            dois = g[0]["pct_lead_time"] + g[1]["pct_lead_time"]
            add(
                "Concentração",
                f"`{g[0]['atividade']}` + `{g[1]['atividade']}` somam {_pct(dois)} do lead time.",
                SUGESTOES_GARGALO.get(
                    g[1]["atividade"], "atacar a segunda maior fatia em seguida."
                ),
            )
    ocio = a["ociosidade"]
    if ocio["pct_lead_time"] >= 10:
        add(
            "Espera / overhead",
            f"{_pct(ocio['pct_lead_time'])} do lead time fica fora das etapas "
            f"(média {fd(ocio['media'])} por caso): imports, abertura de clientes, espera.",
            "manter clientes S3/DuckDB/MQTT quentes no worker e medir a orquestração.",
        )
    sn = a["sem_novidade"]
    if sn["sem_lote_novo"]:
        if sn["reprocessaram_derivadas"]:
            add(
                "Retrabalho de processamento",
                f"{_pct(sn['pct_sem_lote_novo'])} dos ciclos com ingestão OK "
                "não gravaram lote novo, "
                f"e {sn['reprocessaram_derivadas']} deles reprocessaram silver/gold/catálogo "
                f"mesmo assim ({fd(sn['tempo_reprocessado_s'])} no total).",
                SUGESTOES_GARGALO["silver"],
            )
        else:
            add(
                "Ciclo incremental",
                f"{sn['sem_lote_novo']} ciclo(s) sem lote novo pularam silver/gold/catálogo.",
                "manter o modo incremental como padrão do worker.",
            )

    var = a["variacao"]
    falhas_var = [v for v in m["descoberta"]["variantes"] if v["com_falha"]]
    texto_var = (
        f"{var['variantes']} variante(s) em {var['casos']} caso(s); a principal cobre "
        f"{_pct(var['pct_variante_principal'])}; lead time com CV {_dec(var['cv_lead_time'], 2)} "
        f"(desvio {fd(var['lead_time']['desvio'])})."
    )
    if falhas_var:
        texto_var += f" {len(falhas_var)} variante(s) contêm falha."
    add("Variação", texto_var, "padronizar o caminho feliz e tratar as variantes de falha abaixo.")

    falhas = [f for f in a["falhas"] if f["falhas"]]
    for f in falhas[:3]:
        causa = f["erros"][0]["mensagem"] if f["erros"] else "(sem mensagem)"
        add(
            f"Falha em `{f['etapa']}`",
            f"{f['falhas']} de {f['execucoes']} execuções ({_pct(f['taxa_falha_pct'])}); "
            f'causa mais frequente: "{causa}".',
            SUGESTOES_FALHA.get(
                f["etapa"], "retentativa com backoff e alerta na métrica de falha."
            ),
        )
    inc = a["incidentes"]
    if inc["quantidade"]:
        cadencia = a["throughput"]["intervalo_entre_inicios"].get("mediana")
        texto = f"{inc['quantidade']} incidente(s)"
        if inc["mttr_s"] is not None:
            texto += f"; MTTR {fd(inc['mttr_s'])} (da falha até o próximo caso OK)"
        if inc["em_aberto"]:
            texto += "; há um incidente ainda sem recuperação no fim do log"
        if cadencia:
            texto += f"; intervalo mediano entre ciclos {fd(cadencia)}"
        add(
            "Recuperação (MTTR)",
            texto + ".",
            "a recuperação depende do próximo ciclo: retentativa dentro do ciclo e alerta "
            "(Prometheus) na primeira falha encurtam o MTTR.",
        )
    ret = a["retentativas"]
    if ret:
        ok = sum(1 for r in ret if r["sucesso"])
        add(
            "Retentativas",
            f"{len(ret)} etapa(s) com falha foram reexecutadas no caso seguinte; {ok} com sucesso.",
            "tornar as etapas idempotentes (já são no bronze) e registrar a retentativa como tal.",
        )
    rep = a["republicacoes"]
    if rep["republicacoes"]:
        add(
            "Retrabalho na atuação (idempotência)",
            f"{rep['republicacoes']} de {rep['comandos']} comandos ({_pct(rep['pct_redundante'])}) "
            f"repetem o último comando do mesmo talhão sem mudar o payload "
            f"({rep['consecutivas']} em ciclos consecutivos; "
            f"{_dec(rep['minutos_irrigacao_repetidos'], 0)} min de irrigação reenviados).",
            "chave de idempotência por talhão (não republicar comando igual ao vigente) ou "
            "estado retido no broker (retain) com o comando atual.",
        )
    conf = m["descoberta"]["conformidade_caminho_feliz"]
    if conf["fitness"] < 1:
        add(
            "Conformidade",
            f"fitness {_dec(conf['fitness'], 3)} contra o caminho feliz; "
            f"{_pct(conf['pct_casos_conformes'])} dos casos conformes.",
            "os desvios são exatamente os caminhos de falha: atacar as causas acima.",
        )
    tp = a["throughput"]
    if tp.get("casos_por_hora"):
        add(
            "Throughput",
            f"{_dec(tp['casos_por_hora'], 1)} casos/hora "
            f"({_dec(tp['casos_ok_por_hora'], 1)} OK/hora) em "
            f"{_dec(tp['janela_h'], 2)} h de operação.",
            "com o ciclo mais curto (v2), dá para reduzir o intervalo do worker e decidir mais "
            "perto do tempo real.",
        )
    comp = a["versoes"]["comparacao"]
    if comp:
        v1, v2 = a["versoes"]["versoes"]["v1"], a["versoes"]["versoes"]["v2"]
        add(
            "Antes × depois (v1 → v2)",
            f"lead time mediano dos casos OK {fd(v1['lead_time_casos_ok']['mediana'])} → "
            f"{fd(v2['lead_time_casos_ok']['mediana'])} "
            f"({_pct(comp['delta_lead_time_mediano_pct'])}); "
            f"p95 {_pct(comp['delta_p95_pct'])}; sucesso {_pct(v1['taxa_sucesso_pct'])} → "
            f"{_pct(v2['taxa_sucesso_pct'])}; etapas por caso {_dec(v1['etapas_por_caso'], 2)} → "
            f"{_dec(v2['etapas_por_caso'], 2)}.",
            "a melhoria se paga onde não há lote novo; o próximo gargalo é o que sobra na v2 "
            f"(`{v2['gargalo']}`).",
        )
    elif len(a["versoes"]["versoes"]) == 1:
        unica = next(iter(a["versoes"]["versoes"]))
        add(
            "Antes × depois",
            f"só há casos da versão {unica} no log.",
            "rodar o worker em modo incremental (v2) para medir o ganho.",
        )
    return insights


def _tabela(cabecalho: list[str], linhas: list[list[Any]]) -> str:
    if not linhas:
        return "_(sem dados)_\n"
    saida = ["| " + " | ".join(cabecalho) + " |", "|" + "---|" * len(cabecalho)]
    for linha in linhas:
        saida.append("| " + " | ".join(str(c).replace("|", "\\|") for c in linha) + " |")
    return "\n".join(saida) + "\n"


def gerar_markdown(m: dict[str, Any]) -> str:
    esc, d, a = m["escopo"], m["descoberta"], m["analise"]
    arq = m["arquivos"]
    s: list[str] = ["# Mineração de processos — AgroSmart (Fase 7)", ""]
    s.append(
        f"Fonte: `{esc['fonte']}` · dados de {esc['periodo']['inicio'] or '—'} a "
        f"{esc['periodo']['fim'] or '—'} (UTC). Relatório gerado por "
        "`scripts/minerar_processos.py` — todos os números vêm do event log."
    )
    s += ["", "## 1. Escopo do event log", ""]
    s.append(
        _tabela(
            ["item", "valor"],
            [
                ["eventos lidos", esc["eventos"]],
                [
                    "eventos sem `execucao_id` (anteriores à Fase 7, ignorados)",
                    esc["eventos_sem_caso"],
                ],
                [
                    "eventos de casos fora do escopo (origem ≠ worker)",
                    esc["eventos_fora_do_escopo"],
                ],
                ["linhas inválidas", esc["linhas_invalidas"]],
                ["casos analisados", esc["casos"]],
                [
                    "casos em regime (ciclos) / carga inicial",
                    f"{a['casos_em_regime']} / {esc['por_tipo'].get('carga_inicial', 0)}",
                ],
                [
                    "casos por versão",
                    ", ".join(f"{k}: {v}" for k, v in esc["por_versao"].items()) or "—",
                ],
                [
                    "casos por status",
                    ", ".join(f"{k}: {v}" for k, v in esc["por_status"].items()) or "—",
                ],
                ["instâncias de atividade", esc["instancias_atividade"]],
            ],
        )
    )
    s += ["", "## 2. Modelagem", ""]
    s += [
        "- **Caso** = `execucao_id` (um ciclo do worker, uma carga inicial ou um run da CLI).",
        "- **Atividade** = etapa do pipeline; etapa que falhou vira a atividade `<etapa> (falha)` "
        "(desvio explícito no DFG e nas variantes).",
        "- **Etapas puladas** ficam fora do fluxo (duração zero; no worker `simular`/`copiloto` "
        "são puladas sempre) e viram atributo do caso — o atalho do ciclo incremental aparece "
        "como a aresta `ingerir → decidir`.",
        "- **Lifecycle** `start`/`complete` (`etapa_iniciada` / `etapa_concluida|etapa_falhou`); "
        "a forma intervalo (`event_log_intervalos.csv`) junta o par numa linha.",
        "- **Atividade de domínio** `publicar comando`: os `comando_publicado` do caso viram uma "
        "instância (início = 1º comando, fim = último).",
        "- **Recurso** = `origem`; atributos de caso: `versao_processo`, `tipo_caso`, `status`.",
        "- Gargalos, variantes e DFG usam os casos em regime (a carga inicial roda uma vez e "
        "distorceria as médias); o event log exportado tem todos os casos.",
    ]
    s += ["", "Arquivos: " + ", ".join(f"`{Path(p).name}`" for p in arq.values() if p), ""]
    s += ["## 3. Representação do processo (DFG)", ""]
    for chave, legenda in (
        ("dfg_frequencia_svg", "DFG — frequência"),
        ("dfg_desempenho_svg", "DFG — desempenho"),
    ):
        if arq.get(chave):
            s.append(f"![{legenda}]({Path(arq[chave]).name})")
            s.append("")
    arestas = d["dfg_frequencia"]["arestas"]
    esperas = d["dfg_desempenho"]["esperas"]
    s.append(
        _tabela(
            ["transição", "frequência", "espera média", "espera p95"],
            [
                [k, n, fd(esperas.get(k, {}).get("media")), fd(esperas.get(k, {}).get("p95"))]
                for k, n in sorted(arestas.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        )
    )
    s += ["", "## 4. Descoberta e conformidade", ""]
    pm = d.get("pm4py")
    if pm and not pm.get("erro"):
        s += [
            f"Algoritmos via pm4py {pm['versao_pm4py']}.",
            "",
            "**Inductive Miner (árvore de processo):**",
            "",
            "```",
            pm["arvore_inductive"],
            "```",
            "",
        ]
        petri = pm["petri_inductive"]
        s.append(
            f"Rede de Petri: {petri['lugares']} lugares, "
            f"{len(petri['transicoes_visiveis'])} transições visíveis, "
            f"{petri['transicoes_silenciosas']} silenciosas, {petri['arcos']} arcos. "
            f"BPMN: {', '.join(f'{k}={v}' for k, v in pm['bpmn_inductive']['elementos'].items())}."
        )
        s.append("")
        s.append(
            _tabela(
                ["token replay", "fitness", "% casos conformes"],
                [
                    [
                        "modelo descoberto (IM)",
                        _dec(pm["token_replay_modelo_descoberto"]["fitness"], 3),
                        _pct(pm["token_replay_modelo_descoberto"]["pct_casos_conformes"]),
                    ],
                    *(
                        [
                            [
                                "caminho feliz (IM sobre casos OK)",
                                _dec(pm["token_replay_caminho_feliz"]["fitness"], 3),
                                _pct(pm["token_replay_caminho_feliz"]["pct_casos_conformes"]),
                            ]
                        ]
                        if pm.get("token_replay_caminho_feliz")
                        else []
                    ),
                ],
            )
        )
    else:
        motivo = pm["erro"] if pm else "pm4py não instalado"
        s += [
            f"pm4py indisponível ({motivo}): árvore aproximada pelas variantes.",
            "",
            "```",
            d["arvore_variantes"],
            "```",
            "",
        ]
    conf = d["conformidade_caminho_feliz"]
    s.append(
        f"Conformidade por arestas contra o caminho feliz (casos sem falha): fitness "
        f"**{_dec(conf['fitness'], 3)}**, {_pct(conf['pct_casos_conformes'])} dos casos conformes."
    )
    if conf["desvios"]:
        s.append("Desvios: " + "; ".join(f"`{k}` ({v})" for k, v in conf["desvios"].items()) + ".")
    s += ["", "**Heuristics Miner — dependências (≥ 0,5):**", ""]
    s.append(
        _tabela(
            ["de", "para", "frequência", "dependência"],
            [
                [h["de"], h["para"], h["frequencia"], f"{_dec(h['dependencia'], 3)}"]
                for h in d["heuristics"]
            ],
        )
    )
    s += ["", "## 5. Variantes", ""]
    if arq.get("variantes_png"):
        s += [f"![Variantes]({Path(arq['variantes_png']).name})", ""]
    s.append(
        _tabela(
            ["id", "variante", "casos", "% casos", "lead time médio", "desvio", "versões"],
            [
                [
                    v["id"],
                    v["variante"],
                    v["casos"],
                    _pct(v["pct_casos"]),
                    fd(v["lead_time"]["media"]),
                    fd(v["lead_time"]["desvio"]),
                    ", ".join(f"{k}:{q}" for k, q in v["versoes"].items()),
                ]
                for v in d["variantes"]
            ],
        )
    )
    s += ["", "## 6. Gargalos (participação no lead time)", ""]
    if arq.get("duracoes_png"):
        s += [f"![Duração por atividade]({Path(arq['duracoes_png']).name})", ""]
    s.append(
        _tabela(
            ["atividade", "n", "média", "mediana", "p95", "máx.", "CV", "% lead time"],
            [
                [
                    g["atividade"],
                    g["n"],
                    fd(g["media"]),
                    fd(g["mediana"]),
                    fd(g["p95"]),
                    fd(g["max"]),
                    f"{_dec(g['cv'], 2)}",
                    _pct(g["pct_lead_time"]),
                ]
                for g in a["gargalos"]
            ],
        )
    )
    o = a["ociosidade"]
    s.append(
        f"\nTempo fora das etapas (orquestração/espera): {_pct(o['pct_lead_time'])} do lead "
        f"time (média {fd(o['media'])} por caso)."
    )
    s += ["", "**Maiores esperas entre atividades:**", ""]
    s.append(
        _tabela(
            ["transição", "n", "média", "mediana", "p95"],
            [
                [e["aresta"], e["n"], fd(e["media"]), fd(e["mediana"]), fd(e["p95"])]
                for e in a["esperas"]
            ],
        )
    )
    var = a["variacao"]
    s += ["", "## 7. Variação e lead time", ""]
    if arq.get("lead_time_png"):
        s += [f"![Lead time por caso]({Path(arq['lead_time_png']).name})", ""]
    lt = var["lead_time"]
    s.append(
        f"Lead time: média {fd(lt['media'])}, mediana {fd(lt['mediana'])}, p95 {fd(lt['p95'])}, "
        f"máx. {fd(lt['max'])}, desvio {fd(lt['desvio'])} (CV {_dec(var['cv_lead_time'], 2)})."
    )
    tp = a["throughput"]
    s.append(
        f"\nThroughput: {_dec(tp.get('casos_por_hora'), 1)} casos/hora em "
        f"{_dec(tp.get('janela_h'), 2)} h; "
        f"intervalo mediano entre inícios "
        f"{fd(tp.get('intervalo_entre_inicios', {}).get('mediana'))}."
    )
    s += ["", "## 8. Falhas, MTTR e retrabalho", ""]
    s.append(
        _tabela(
            ["etapa", "execuções", "falhas", "taxa", "erro mais frequente"],
            [
                [
                    f["etapa"],
                    f["execucoes"],
                    f["falhas"],
                    _pct(f["taxa_falha_pct"]),
                    f["erros"][0]["mensagem"][:90] if f["erros"] else "",
                ]
                for f in a["falhas"]
            ],
        )
    )
    inc = a["incidentes"]
    s.append(f"\nIncidentes: {inc['quantidade']} · MTTR: {fd(inc['mttr_s'])}.")
    if inc["incidentes"]:
        s.append("")
        s.append(
            _tabela(
                ["início", "atividade", "casos afetados", "recuperado por", "TTR"],
                [
                    [
                        i["inicio"],
                        i["atividade"],
                        len(i["casos_afetados"]),
                        i["caso_recuperacao"],
                        fd(i["ttr_s"]),
                    ]
                    for i in inc["incidentes"]
                ],
            )
        )
    ret = a["retentativas"]
    s.append(
        f"\nRetentativas (etapa que falhou e foi reexecutada no caso seguinte): {len(ret)}, "
        f"{sum(1 for r in ret if r['sucesso'])} com sucesso."
    )
    rep = a["republicacoes"]
    s.append(
        f"\nComandos de irrigação republicados sem mudança de payload: {rep['republicacoes']} "
        f"de {rep['comandos']} ({_pct(rep['pct_redundante'])}); por talhão: "
        f"{rep['por_talhao'] or '—'}."
    )
    sn = a["sem_novidade"]
    s.append(
        f"\nCiclos sem lote novo: {sn['sem_lote_novo']} de {sn['casos_com_ingestao_ok']} "
        f"({_pct(sn['pct_sem_lote_novo'])}); reprocessaram silver/gold/catálogo: "
        f"{sn['reprocessaram_derivadas']} ({fd(sn['tempo_reprocessado_s'])})."
    )
    s += ["", "## 9. Antes × depois (v1 × v2)", ""]
    vers = a["versoes"]["versoes"]
    s.append(
        _tabela(
            [
                "versão",
                "casos",
                "sucesso",
                "lead time mediano (OK)",
                "p95 (OK)",
                "etapas/caso",
                "% com silver/gold",
                "gargalo",
            ],
            [
                [
                    k,
                    v["casos"],
                    _pct(v["taxa_sucesso_pct"]),
                    fd(v["lead_time_casos_ok"]["mediana"]),
                    fd(v["lead_time_casos_ok"]["p95"]),
                    v["etapas_por_caso"],
                    _pct(v["pct_casos_com_silver_gold"]),
                    v["gargalo"],
                ]
                for k, v in vers.items()
            ],
        )
    )
    comp = a["versoes"]["comparacao"]
    if comp:
        s.append(
            f"\nΔ lead time mediano: {_pct(comp['delta_lead_time_mediano_pct'])} · "
            f"Δ p95: {_pct(comp['delta_p95_pct'])} · Δ sucesso: "
            f"{comp['delta_taxa_sucesso_pp']} p.p."
        )
    s += ["", "## 10. Insights e oportunidades", ""]
    for i, ins in enumerate(m["insights"], start=1):
        s.append(
            f"{i}. **{ins['tema']}** — {ins['evidencia']} → *Oportunidade:* {ins['oportunidade']}"
        )
    return "\n".join(s) + "\n"


def salvar(m: dict[str, Any], pasta: Path) -> tuple[Path, Path]:
    pasta.mkdir(parents=True, exist_ok=True)
    caminho_json = pasta / "metricas.json"
    caminho_md = pasta / "relatorio.md"
    caminho_json.write_text(
        json.dumps(_limpar(m), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    caminho_md.write_text(gerar_markdown(m), encoding="utf-8")
    return caminho_json, caminho_md
