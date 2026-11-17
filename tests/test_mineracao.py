"""Mineração de processos (Fase 7) sobre event logs sintéticos — sem pm4py e sem o log real."""

from __future__ import annotations

import json
import random
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agrosmart.mineracao import minerar
from agrosmart.mineracao.analise import (
    analisar,
    ciclos_sem_novidade,
    comparar_versoes,
    gargalos,
    incidentes,
    republicacoes,
    retentativas,
)
from agrosmart.mineracao.descoberta import (
    conformidade_por_arestas,
    dependencias_heuristicas,
    descobrir,
    dfg_desempenho,
    dfg_frequencia,
    percentil,
    variantes,
)
from agrosmart.mineracao.eventlog import (
    ATIVIDADE_COMANDO,
    FIM,
    INICIO,
    exportar_xes_manual,
    extrair_event_log,
    linhas_intervalo,
    linhas_lifecycle,
    selecionar_exemplo,
)
from agrosmart.mineracao.visual import COR_FALHA, montar_dfg, svg

T0 = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)
DURACOES = {
    "ingerir": 2.0,
    "silver": 30.0,
    "gold": 8.0,
    "catalogo": 3.5,
    "decidir": 0.2,
    "atuar": 1.0,
}
IRRIGAR = [("TLH-02", {"acao": "ligar", "minutos": 170})]


def _ev(quando: datetime, etapa: str, acao: str, ctx: dict[str, Any], **extra: Any) -> dict:
    return {
        "ocorrido_em": quando.isoformat(timespec="milliseconds"),
        "etapa": etapa,
        "acao": acao,
        "fonte": extra.pop("fonte", None),
        "camada": None,
        "batch_id": None,
        "status": extra.pop("status", "ok"),
        "mensagem": extra.pop("mensagem", ""),
        "metricas": extra.pop("metricas", {}),
        **ctx,
    }


def caso_eventos(
    caso_id: str,
    inicio: datetime,
    *,
    versao: str = "v1",
    origem: str = "worker",
    tipo: str = "ciclo",
    falha: str | None = None,
    gravados: int = 1,
    comandos: list[tuple[str, dict]] | None = None,
    duracoes: dict[str, float] | None = None,
    folga: float = 0.05,
) -> tuple[list[dict], datetime]:
    """Eventos de um caso como o orquestrador grava; devolve (eventos, instante final)."""
    ctx = {"execucao_id": caso_id, "origem": origem, "versao_processo": versao}
    if tipo:
        ctx["tipo_caso"] = tipo
    dur = {**DURACOES, **(duracoes or {})}
    comandos = IRRIGAR if comandos is None else comandos
    t = inicio
    eventos = [_ev(t, "run", "iniciada", ctx), _ev(t, "simular", "etapa_pulada", ctx)]
    etapas = ["ingerir", "silver", "gold", "catalogo", "decidir", "atuar"]
    incremental_sem_lote = versao == "v2" and gravados == 0
    falhou = False
    for etapa in etapas:
        if incremental_sem_lote and etapa in ("silver", "gold", "catalogo"):
            eventos.append(_ev(t, etapa, "etapa_pulada", ctx, mensagem="incremental"))
            continue
        if etapa == "decidir":
            eventos.append(_ev(t, "copiloto", "etapa_pulada", ctx))
        t += timedelta(seconds=folga)
        eventos.append(_ev(t, etapa, "etapa_iniciada", ctx))
        t += timedelta(seconds=dur[etapa])
        if etapa == "atuar" and falha != "atuar":
            for i, (talhao, payload) in enumerate(comandos):
                momento = t - timedelta(seconds=dur[etapa] / 2) + timedelta(milliseconds=i)
                eventos.append(
                    _ev(
                        momento,
                        "automacao",
                        "comando_publicado",
                        ctx,
                        fonte=talhao,
                        mensagem=f"ACT-{i + 1:04d} agrosmart/atuadores/{talhao}/irrigacao "
                        + json.dumps(payload),
                    )
                )
        if etapa == falha:
            eventos.append(
                _ev(
                    t,
                    etapa,
                    "etapa_falhou",
                    ctx,
                    status="erro",
                    mensagem=f"ConnectionError: {etapa} indisponível",
                    metricas={"duracao_s": dur[etapa]},
                )
            )
            falhou = True
            break
        metricas: dict[str, Any] = {"duracao_s": dur[etapa]}
        if etapa == "ingerir":
            metricas |= {
                "lotes": 3,
                "gravados": gravados,
                "atualizados": 0,
                "pulados": 3 - gravados,
            }
        if etapa == "atuar":
            metricas |= {"publicadas": len(comandos), "falhas": 0}
        eventos.append(_ev(t, etapa, "etapa_concluida", ctx, metricas=metricas))
    t += timedelta(seconds=folga)
    eventos.append(
        _ev(t, "run", "falhou" if falhou else "concluida", ctx, status="erro" if falhou else "ok")
    )
    return eventos, t


def gerar_log(
    n_v1: int = 12,
    n_v2: int = 12,
    *,
    falhas: dict[int, str] | None = None,
    intervalo_s: float = 60.0,
    semente: int = 7,
) -> list[dict]:
    """Log sintético: ciclos v1 e depois v2, com falhas injetadas por índice de caso."""
    rnd = random.Random(semente)
    falhas = falhas or {}
    eventos: list[dict] = [_ev(T0 - timedelta(days=1), "ingestao", "concluida", {})]
    inicio = T0
    for i in range(n_v1 + n_v2):
        versao = "v1" if i < n_v1 else "v2"
        gravados = 1 if i % 3 == 0 else 0  # 1 em cada 3 ciclos traz lote novo
        jitter = {e: d * rnd.uniform(0.8, 1.25) for e, d in DURACOES.items()}
        evs, _ = caso_eventos(
            f"ciclo-{i:04d}",
            inicio,
            versao=versao,
            falha=falhas.get(i),
            gravados=gravados,
            duracoes=jitter,
        )
        eventos += evs
        inicio += timedelta(seconds=intervalo_s)
    return eventos


def _log(eventos: list[dict], **kw: Any):
    return extrair_event_log(eventos, **kw)


# ----------------------------------------------------------------------------- extração


def test_extracao_lifecycle_e_intervalos() -> None:
    eventos, _ = caso_eventos("ciclo-a", T0)
    log = _log(eventos)
    (caso,) = log.casos
    assert caso.status == "ok"
    assert caso.variante == (
        "ingerir",
        "silver",
        "gold",
        "catalogo",
        "decidir",
        "atuar",
        ATIVIDADE_COMANDO,
    )
    assert caso.puladas == ["simular", "copiloto"]  # puladas viram atributo, não atividade
    silver = next(a for a in caso.atividades if a.atividade == "silver")
    assert silver.duracao_s == pytest.approx(30.0)

    linhas = linhas_lifecycle(log)
    assert len(linhas) == 2 * len(caso.atividades)
    assert {ln["lifecycle:transition"] for ln in linhas} == {"start", "complete"}
    assert linhas[0]["concept:name"] == "ingerir" and linhas[0]["case:concept:name"] == "ciclo-a"
    intervalos = linhas_intervalo(log)
    ingerir = intervalos[0]
    assert ingerir["activity"] == "ingerir" and ingerir["gravados"] == 1
    assert ingerir["resource"] == "worker" and ingerir["versao_processo"] == "v1"
    comando = next(i for i in intervalos if i["activity"] == ATIVIDADE_COMANDO)
    assert comando["tipo_atividade"] == "dominio" and comando["comandos"] == 1


def test_eventos_sem_caso_e_cli_ficam_fora() -> None:
    worker, _ = caso_eventos("ciclo-a", T0)
    cli, _ = caso_eventos("run-b", T0 + timedelta(minutes=5), origem="cli", tipo="")
    antigos = [_ev(T0, "ingestao", "concluida", {}) for _ in range(3)]
    log = _log(antigos + worker + cli)
    assert [c.id for c in log.casos] == ["ciclo-a"]
    assert log.eventos_sem_caso == 3
    assert log.casos_fora_do_escopo == 1 and log.eventos_fora_do_escopo == len(cli)
    com_cli = _log(antigos + worker + cli, apenas_worker=False)
    assert [c.tipo_caso for c in com_cli.casos] == ["ciclo", "run"]


def test_falha_vira_atividade_e_caso_falha() -> None:
    eventos, _ = caso_eventos("ciclo-x", T0, falha="silver")
    (caso,) = _log(eventos).casos
    assert caso.status == "falhou"
    assert caso.variante == ("ingerir", "silver (falha)")
    assert caso.atividades[-1].mensagem.startswith("ConnectionError")


def test_etapa_sem_conclusao_vira_falha() -> None:
    eventos, _ = caso_eventos("ciclo-x", T0)
    corte = next(
        i for i, e in enumerate(eventos) if e["acao"] == "etapa_concluida" and e["etapa"] == "gold"
    )
    (caso,) = _log(eventos[:corte]).casos
    assert caso.variante[-1] == "gold (falha)"
    assert caso.status == "falhou"


# ---------------------------------------------------------------------------- descoberta


def test_variantes_e_dfg() -> None:
    eventos = gerar_log(3, 0, falhas={1: "ingerir"})
    log = _log(eventos)
    lista = variantes(log.casos)
    assert [v["casos"] for v in lista] == [2, 1]
    assert lista[0]["pct_casos"] == pytest.approx(66.7)
    assert lista[1]["com_falha"] and lista[1]["atividades"] == ["ingerir (falha)"]

    dfg = dfg_frequencia(log.casos)["_arestas"]
    assert dfg[(INICIO, "ingerir")] == 2
    assert dfg[(INICIO, "ingerir (falha)")] == 1
    assert dfg[("silver", "gold")] == 2
    assert dfg[(ATIVIDADE_COMANDO, FIM)] == 2
    desempenho = dfg_desempenho(log.casos)
    assert desempenho["_esperas"][("ingerir", "silver")]["media"] == pytest.approx(0.05, abs=1e-3)
    assert desempenho["atividades"]["silver"]["n"] == 2


def test_heuristics_e_conformidade() -> None:
    log = _log(gerar_log(4, 0, falhas={2: "atuar"}))
    deps = {(d["de"], d["para"]): d["dependencia"] for d in dependencias_heuristicas(log.casos)}
    assert deps[("ingerir", "silver")] == pytest.approx(4 / 5)
    ok = [c for c in log.casos if not c.falhou]
    conf = conformidade_por_arestas(log.casos, ok)
    assert conf["pct_casos_conformes"] == pytest.approx(75.0)
    assert "decidir -> atuar (falha)" in conf["desvios"]
    assert 0 < conf["fitness"] < 1


def test_descobrir_sem_pm4py() -> None:
    log = _log(gerar_log(3, 3))
    d = descobrir(log.casos, usar_pm4py=False)
    assert d["pm4py"] is None
    assert d["arvore_variantes"].startswith("->( 'ingerir'")
    assert "X(" in d["arvore_variantes"]  # v1 completo × v2 atalho


def test_percentil() -> None:
    assert percentil([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert percentil([10], 95) == 10
    assert percentil([], 95) == 0


# ------------------------------------------------------------------------------- análise


def test_gargalo_e_sem_novidade() -> None:
    log = _log(gerar_log(6, 0))
    g = gargalos(log.casos)
    assert g[0]["atividade"] == "silver"
    assert g[0]["pct_lead_time"] > 50
    sn = ciclos_sem_novidade(log.casos)
    assert sn["sem_lote_novo"] == sn["reprocessaram_derivadas"] > 0
    assert sn["tempo_reprocessado_s"] > 0


def test_retrabalho_retentativa_e_republicacao() -> None:
    a, t = caso_eventos("ciclo-1", T0)
    b, t = caso_eventos("ciclo-2", t + timedelta(seconds=30), falha="atuar")
    c, t = caso_eventos("ciclo-3", t + timedelta(seconds=30))
    d, _ = caso_eventos(
        "ciclo-4",
        t + timedelta(seconds=30),
        comandos=[("TLH-02", {"acao": "ligar", "minutos": 90})],
    )
    log = _log(a + b + c + d)
    ret = retentativas(log.casos)
    assert ret == [
        {
            "etapa": "atuar",
            "caso_com_falha": "ciclo-2",
            "caso_retentativa": "ciclo-3",
            "sucesso": True,
            "intervalo_s": pytest.approx(ret[0]["intervalo_s"]),
        }
    ]
    rep = republicacoes(log.casos)
    assert rep["comandos"] == 3
    assert rep["republicacoes"] == 1  # ciclo-3 repete o comando do ciclo-1 (payload igual)
    assert rep["consecutivas"] == 0  # entre eles houve o ciclo-2, que falhou antes de publicar
    assert rep["minutos_irrigacao_repetidos"] == 170
    assert rep["por_talhao"] == {"TLH-02": 1}


def test_incidente_e_mttr() -> None:
    a, t = caso_eventos("ciclo-1", T0, falha="ingerir")
    b, t = caso_eventos("ciclo-2", t + timedelta(seconds=60), falha="silver")
    c, fim_ok = caso_eventos("ciclo-3", t + timedelta(seconds=60))
    log = _log(a + b + c)
    inc = incidentes(log.casos)
    assert inc["quantidade"] == 1 and inc["em_aberto"] is None
    (unico,) = inc["incidentes"]
    assert unico["casos_afetados"] == ["ciclo-1", "ciclo-2"]
    assert unico["caso_recuperacao"] == "ciclo-3"
    primeira_falha = log.casos[0].atividades[0].fim
    assert inc["mttr_s"] == pytest.approx((fim_ok - primeira_falha).total_seconds())

    aberto = incidentes(
        _log(c + caso_eventos("ciclo-9", fim_ok + timedelta(1), falha="atuar")[0]).casos
    )
    assert aberto["quantidade"] == 1 and aberto["em_aberto"] and aberto["mttr_s"] is None


def test_comparacao_v1_v2() -> None:
    log = _log(gerar_log(10, 10))
    comp = comparar_versoes(log.casos)
    v1, v2 = comp["versoes"]["v1"], comp["versoes"]["v2"]
    assert v1["pct_casos_com_silver_gold"] == 100.0
    assert v2["pct_casos_com_silver_gold"] < 100.0
    assert comp["comparacao"]["delta_lead_time_mediano_pct"] < 0
    assert comp["comparacao"]["delta_etapas_por_caso"] < 0
    so_v1 = comparar_versoes([c for c in log.casos if c.versao_processo == "v1"])
    assert so_v1["comparacao"] is None


def test_carga_inicial_fica_fora_do_regime() -> None:
    carga, t = caso_eventos("carga-1", T0, tipo="carga_inicial", duracoes={"silver": 600})
    ciclo, _ = caso_eventos("ciclo-1", t + timedelta(seconds=10))
    a = analisar(_log(carga + ciclo))
    assert a["casos_em_regime"] == 1
    assert a["gargalos"][0]["max"] < 100


# -------------------------------------------------------------------- exportação e visual


def test_xes_valido(tmp_path: Path) -> None:
    log = _log(gerar_log(2, 1, falhas={1: "silver"}))
    destino = exportar_xes_manual(log, tmp_path / "log.xes")
    raiz = ET.parse(destino).getroot()
    ns = {"x": "http://www.xes-standard.org/"}
    assert raiz.tag == "{http://www.xes-standard.org/}log"
    assert raiz.get("xes.version") == "1.0"
    traces = raiz.findall("x:trace", ns)
    assert len(traces) == 3
    eventos = traces[1].findall("x:event", ns)
    nomes = [e.find("x:string[@key='concept:name']", ns).get("value") for e in eventos]
    assert nomes == ["ingerir", "ingerir", "silver (falha)", "silver (falha)"]
    datas = [e.find("x:date[@key='time:timestamp']", ns).get("value") for e in eventos]
    assert all(datetime.fromisoformat(d) for d in datas)


def test_svg_dfg_tem_falha_em_vermelho() -> None:
    log = _log(gerar_log(4, 4, falhas={2: "silver", 6: "atuar"}))
    d = descobrir(log.casos, usar_pm4py=False)
    for modo in ("frequencia", "desempenho"):
        texto = svg(montar_dfg(d["dfg_frequencia"], d["dfg_desempenho"], modo=modo, titulo="t"))
        raiz = ET.fromstring(texto)
        assert raiz.tag.endswith("svg")
        assert "silver (falha)" in texto and "atuar (falha)" in texto
        assert COR_FALHA in texto
        assert texto.count("<polyline") == len(d["dfg_frequencia"]["_arestas"])


def test_exemplo_inclui_caso_com_falha() -> None:
    log = _log(gerar_log(4, 2, falhas={3: "ingerir"}))
    escolhidos = selecionar_exemplo(log, 3)
    assert len(escolhidos) == 3
    assert any(c.falhou for c in escolhidos)
    assert {c.versao_processo for c in escolhidos} == {"v1", "v2"}


# --------------------------------------------------------------------------- ponta a ponta


def _gravar(eventos: list[dict], caminho: Path) -> Path:
    caminho.write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in eventos) + "{quebrada\n",
        encoding="utf-8",
    )
    return caminho


def test_minerar_ponta_a_ponta_deterministico(tmp_path: Path) -> None:
    eventos = gerar_log(8, 8, falhas={3: "silver", 4: "ingerir", 12: "atuar"})
    fonte = _gravar(eventos, tmp_path / "eventos.jsonl")
    m1 = minerar(
        fonte, tmp_path / "a", usar_pm4py=False, graficos=False, exemplo=tmp_path / "ex.csv"
    )
    minerar(fonte, tmp_path / "b", usar_pm4py=False, graficos=False, exemplo=tmp_path / "ex.csv")
    assert m1["escopo"]["casos"] == 16
    assert m1["escopo"]["linhas_invalidas"] == 1
    assert m1["escopo"]["eventos_sem_caso"] == 1
    for nome in (
        "event_log.csv",
        "event_log_intervalos.csv",
        "event_log.xes",
        "dfg_frequencia.svg",
        "dfg_desempenho.svg",
        "metricas.json",
        "relatorio.md",
    ):
        assert (tmp_path / "a" / nome).read_bytes() == (tmp_path / "b" / nome).read_bytes(), nome
    metricas = json.loads((tmp_path / "a" / "metricas.json").read_text(encoding="utf-8"))
    assert metricas["configuracao"]["xes_gerado_por"] == "manual"
    temas = {i["tema"] for i in metricas["insights"]}
    assert {"Gargalo", "Recuperação (MTTR)", "Antes × depois (v1 → v2)"} <= temas
    assert any(t.startswith("Falha em") for t in temas)
    assert "Retrabalho na atuação (idempotência)" in temas
    relatorio = (tmp_path / "a" / "relatorio.md").read_text(encoding="utf-8")
    assert "## 10. Insights e oportunidades" in relatorio
    assert "silver (falha)" in relatorio
    cabecalho = (tmp_path / "ex.csv").read_text(encoding="utf-8").splitlines()[0]
    assert cabecalho.startswith(
        "case:concept:name,concept:name,lifecycle:transition,time:timestamp"
    )


def test_minerar_log_vazio(tmp_path: Path) -> None:
    fonte = _gravar([_ev(T0, "ingestao", "concluida", {})], tmp_path / "eventos.jsonl")
    m = minerar(fonte, tmp_path / "saida", usar_pm4py=False, graficos=False)
    assert m["escopo"]["casos"] == 0
    assert m["insights"][0]["tema"] == "Dados"
    assert (tmp_path / "saida" / "dfg_frequencia.svg").exists()


def test_minerar_com_graficos(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    fonte = _gravar(gerar_log(3, 3, falhas={1: "silver"}), tmp_path / "eventos.jsonl")
    m = minerar(fonte, tmp_path / "saida", usar_pm4py=False)
    for chave in ("dfg_frequencia_png", "duracoes_png", "lead_time_png", "variantes_png"):
        png = tmp_path / "saida" / m["arquivos"][chave]
        assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_descobrir_com_pm4py(tmp_path: Path) -> None:
    pytest.importorskip("pm4py")
    log = _log(gerar_log(4, 4, falhas={2: "silver"}))
    d = descobrir(log.casos, usar_pm4py=True)
    pm = d["pm4py"]
    assert "erro" not in pm, pm
    assert "'ingerir'" in pm["arvore_inductive"]
    assert 0 < pm["token_replay_caminho_feliz"]["fitness"] <= 1
    assert pm["token_replay_modelo_descoberto"]["fitness"] == pytest.approx(1.0)
