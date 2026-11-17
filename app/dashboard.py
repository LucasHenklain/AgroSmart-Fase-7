"""Camada de consumo: o dashboard do produtor sobre o gold.

    streamlit run app/dashboard.py
    http://localhost:8501/?talhao=TLH-02&secao=agua      (link direto)

Lê **só o gold** (mais os relatórios em ``outputs/``) pelo mesmo ``Armazenamento``
do pipeline — funciona em MinIO e em filesystem sem mudar uma linha. Não recalcula
indicador nenhum: o que aparece aqui é exatamente o que o copiloto leu, o que
torna as duas telas comparáveis e auditáveis.

Responsável: E3 (consumo) — Passo 5. Fase 6: seção **Automação**, que lê o JSON de
ações mais recente de ``outputs/acoes/`` (gerado por ``agrosmart automacao``).
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

import altair as alt
import polars as pl
import streamlit as st

from agrosmart.agronomia import (
    BATERIA_MANUTENCAO_PCT,
    FRACAO_ESTRESSE,
    FRACAO_IRRIGACAO,
    K_ANONIMATO,
    RSSI_FRACO_DBM,
)
from agrosmart.config import Camada, carregar_config
from agrosmart.processamento.gold import TABELAS
from agrosmart.processamento.silver import ler_tabela
from agrosmart.storage import criar_armazenamento

st.set_page_config(page_title="AgroSmart · Camada de consumo", page_icon="🌱", layout="wide")

CASOS = {"irrigacao": "Irrigação", "fitossanitario": "Fitossanitário", "executivo": "Executivo"}
CORES_CONFIANCA = {"alta": "#2e7d32", "media": "#f9a825", "baixa": "#c62828"}
SECOES = {
    "talhao": "Talhão",
    "agua": "Água",
    "vegetacao": "Vegetação",
    "custos": "Custos",
    "copiloto": "Copiloto",
    "qualidade": "Qualidade",
    "sensores": "Sensores",
    "benchmark": "Benchmark",
    "automacao": "Automação",
    "operacao": "Operação",
}
ORDEM_ETAPAS = ["ingerir", "silver", "gold", "catalogo", "decidir", "atuar"]
ROTULO_RISCO = {"critico": "🔴 crítico", "atencao": "🟠 atenção", "normal": "🟢 normal"}
ORDEM_PRIORIDADE = ["alta", "media", "baixa"]
CORES_PRIORIDADE = {"alta": "#c62828", "media": "#f9a825", "baixa": "#2e7d32"}


# ---------------------------------------------------------------------------
# Carga (cacheada): gold inteiro + relatórios de outputs/
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300, show_spinner="Lendo o gold…")
def carregar_gold() -> dict[str, pl.DataFrame]:
    cfg = carregar_config()
    lake = criar_armazenamento(cfg)
    return {nome: ler_tabela(lake, Camada.GOLD, nome) for nome in TABELAS}


@st.cache_data(ttl=300)
def carregar_json(nome: str) -> dict:
    caminho = carregar_config().dir_outputs / nome
    return json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}


def relatorio_copiloto(caso: str, talhao_id: str) -> tuple[str | None, dict | None]:
    pasta = carregar_config().dir_outputs / "relatorios" / caso
    candidatos = sorted(pasta.glob(f"{talhao_id}_*.md")) if pasta.exists() else []
    if not candidatos:
        return None, None
    md = candidatos[-1]
    prov = Path(str(md)[: -len(".md")] + ".proveniencia.json")
    proveniencia = json.loads(prov.read_text(encoding="utf-8")) if prov.exists() else None
    return md.read_text(encoding="utf-8"), proveniencia


def dir_acoes() -> Path:
    """``outputs/acoes/`` por padrão; ``AGROSMART_DIR_ACOES`` aponta para outra pasta
    (usado só para capturar a tela com um JSON de exemplo fora de ``outputs/``)."""
    sobrescrita = os.getenv("AGROSMART_DIR_ACOES", "").strip()
    return Path(sobrescrita) if sobrescrita else carregar_config().dir_outputs / "acoes"


@st.cache_data(ttl=60)
def carregar_acoes(pasta: str) -> tuple[str | None, dict]:
    """O ``acoes_<data>.json`` mais recente da pasta (ordem lexicográfica = cronológica)."""
    candidatos = sorted(Path(pasta).glob("acoes_*.json")) if Path(pasta).is_dir() else []
    if not candidatos:
        return None, {}
    ultimo = candidatos[-1]
    try:
        return ultimo.name, json.loads(ultimo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ultimo.name, {}


@st.cache_data(ttl=15)
def carregar_ciclos(limite: int = 60) -> pl.DataFrame:
    """Uma linha por (caso, etapa) dos últimos ``limite`` casos do worker, da trilha JSONL.

    Lê só as linhas de ciclo de vida de etapa (``etapa_concluida|falhou|pulada``) — o
    mesmo recorte que a mineração de processos usa como event log.
    """
    caminho = carregar_config().dir_logs / "eventos.jsonl"
    if not caminho.exists():
        return pl.DataFrame()
    linhas = []
    with caminho.open(encoding="utf-8") as arquivo:
        for bruta in arquivo:
            if '"etapa_' not in bruta or '"execucao_id"' not in bruta:
                continue
            e = json.loads(bruta)
            if e.get("origem") != "worker" or e["acao"] == "etapa_iniciada":
                continue
            linhas.append(
                {
                    "caso": e["execucao_id"],
                    "versao": e.get("versao_processo", "v1"),
                    "etapa": e["etapa"],
                    "status": e["acao"].removeprefix("etapa_"),
                    "fim": e["ocorrido_em"],
                    "duracao_s": float((e.get("metricas") or {}).get("duracao_s") or 0.0),
                    "erro": e.get("mensagem") or "",
                }
            )
    if not linhas:
        return pl.DataFrame()
    df = pl.DataFrame(linhas).with_columns(
        pl.col("fim").str.to_datetime(time_zone="UTC", strict=False)
    )
    ultimos = (
        df.group_by("caso").agg(pl.col("fim").max()).sort("fim").tail(limite).get_column("caso")
    )
    return df.filter(pl.col("caso").is_in(ultimos.to_list()))


def pandas(df: pl.DataFrame):
    return df.to_pandas()


def brl(valor: float | None) -> str:
    if valor is None:
        return "—"
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def como_datetime(df: pl.DataFrame, coluna: str) -> pl.DataFrame:
    return df.with_columns(pl.col(coluna).cast(pl.Datetime))


# ---------------------------------------------------------------------------
# Estado: barra lateral + link direto (?talhao=&secao=)
# ---------------------------------------------------------------------------

cfg = carregar_config()
gold = carregar_gold()
dq = carregar_json("dq_report.json")
avaliacao = carregar_json("avaliacao_ia.json")
execucao = carregar_json("run.json")

dim_talhao = gold["dim_talhao"]
if dim_talhao.is_empty():
    st.title("AgroSmart · Camada de consumo")
    st.warning("O gold está vazio. Rode `agrosmart run --all` e recarregue.")
    st.stop()

hoje = cfg.instante_referencia.date()
talhoes = dim_talhao.get_column("talhao_id").to_list()
params = st.query_params
talhao_inicial = (
    params.get("talhao") if params.get("talhao") in talhoes else talhoes[min(1, len(talhoes) - 1)]
)
secao_inicial = params.get("secao") if params.get("secao") in SECOES else "talhao"

with st.sidebar:
    st.header("AgroSmart")
    st.caption(f"backend **{cfg.backend.value}** · referência **{hoje:%d/%m/%Y}**")
    talhao_id = st.selectbox("Talhão", talhoes, index=talhoes.index(talhao_inicial))
    secao = st.radio(
        "Seção", list(SECOES), index=list(SECOES).index(secao_inicial), format_func=SECOES.get
    )
    janela = st.radio(
        "Janela", [14, 30, 60], index=1, horizontal=True, format_func=lambda d: f"{d} dias"
    )
    if st.button("Recarregar do lake"):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    if execucao:
        etapas_ok = sum(1 for e in execucao.get("etapas", []) if e["status"] == "ok")
        st.caption(
            f"última execução: **{execucao.get('modo')}** · {etapas_ok} etapas ok · "
            f"{execucao.get('duracao_s', 0):.0f} s"
        )
    st.caption("Fonte única: camada gold do Data Lake. Nenhum indicador é recalculado aqui.")

params["talhao"] = talhao_id
params["secao"] = secao

inicio_janela = hoje - timedelta(days=janela - 1)
talhao = dim_talhao.filter(pl.col("talhao_id") == talhao_id).row(0, named=True)
balanco = gold["fato_balanco_hidrico"]
alertas = gold["fato_alerta_qualidade"]
por_motor = avaliacao.get("por_motor", {}) if avaliacao else {}
serie = balanco.filter(
    (pl.col("talhao_id") == talhao_id) & (pl.col("dia") >= inicio_janela) & (pl.col("dia") <= hoje)
).sort("dia")
ultimo = serie.filter(pl.col("umidade_media_pct").is_not_null()).tail(1)
ultimo_row = ultimo.row(0, named=True) if not ultimo.is_empty() else None

# ---------------------------------------------------------------------------
# Cabeçalho e indicadores da fazenda
# ---------------------------------------------------------------------------

st.title("AgroSmart · Camada de consumo")
st.caption(
    "O que o produtor vê é o que o copiloto leu: balanço hídrico, vegetação, custos, "
    "qualidade dos dados e as recomendações — tudo do gold."
)

hoje_balanco = balanco.filter(pl.col("dia") == hoje)
totais = dq.get("totais", {})
aprovados, quarentena, lidos = (totais.get(k, 0) for k in ("aprovados", "quarentena", "lidos"))
exec_ia = sum(m.get("execucoes", 0) for m in por_motor.values())
aprov_ia = sum(m.get("aprovados_guardrails", 0) for m in por_motor.values())
pedindo_irrigacao = (
    int(hoje_balanco.get_column("recomenda_irrigar").fill_null(False).sum())
    if not hoje_balanco.is_empty()
    else 0
)

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Talhões", dim_talhao.height, f"{dim_talhao.get_column('area_ha').sum():.1f} ha")
c2.metric(
    "Registros aprovados",
    f"{aprovados:,}".replace(",", "."),
    f"de {lidos:,} lidos".replace(",", "."),
)
c3.metric(
    "Quarentena",
    f"{(quarentena / lidos * 100) if lidos else 0:.2f} %",
    f"{quarentena} registros",
    delta_color="inverse",
)
c4.metric(
    "Talhões pedindo irrigação",
    pedindo_irrigacao,
    f"limiar FAD < {FRACAO_IRRIGACAO}",
    delta_color="off",
)
c5.metric("Alertas de qualidade", alertas.height, "regras + frescor", delta_color="off")
c6.metric("Copiloto", f"{aprov_ia}/{exec_ia}", "aprovadas nos guardrails")
st.divider()


# ---------------------------------------------------------------------------
# Seções
# ---------------------------------------------------------------------------


def secao_talhao() -> None:
    st.subheader(f"{talhao_id} · {talhao['cultura']} {talhao['variedade'] or ''}".strip())
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Área", f"{talhao['area_ha']:.1f} ha", talhao["sistema_irrigacao"], delta_color="off")
    if ultimo_row:
        dap = ultimo_row["dap"]
        k2.metric(
            "Estádio",
            ultimo_row["estadio"] or "—",
            f"{dap} dias após o plantio" if dap is not None else "",
            delta_color="off",
        )
        fad = ultimo_row["fracao_agua_disponivel"]
        if fad is None:
            estado = "sem leitura"
        elif fad < FRACAO_ESTRESSE:
            estado = "estresse"
        elif fad < FRACAO_IRRIGACAO:
            estado = "irrigar"
        else:
            estado = "ok"
        k3.metric(
            "Fração de água disponível",
            f"{fad:.3f}" if fad is not None else "—",
            estado,
            delta_color="off",
        )
        deficit = ultimo_row["deficit_mm"]
        umidade = ultimo_row["umidade_media_pct"]
        k4.metric(
            "Déficit",
            f"{deficit:.1f} mm" if deficit is not None else "—",
            f"umidade {umidade:.1f} %" if umidade is not None else "",
            delta_color="off",
        )
    ndvi_t = gold["fato_ndvi_talhao"].filter(pl.col("talhao_id") == talhao_id).sort("capturada_em")
    if not ndvi_t.is_empty():
        n = ndvi_t.row(-1, named=True)
        variacao = n["variacao_vs_anterior"]
        k5.metric(
            "NDVI",
            f"{n['ndvi_medio']:.3f}",
            f"{variacao:+.3f} vs anterior" if variacao is not None else n["confianca"],
        )

    st.markdown("**Situação de todos os talhões hoje**")
    visao = (
        dim_talhao.select("talhao_id", "cultura", "area_ha", "sistema_irrigacao")
        .join(
            hoje_balanco.select(
                "talhao_id",
                "estadio",
                "fracao_agua_disponivel",
                "deficit_mm",
                "estresse_hidrico",
                "recomenda_irrigar",
            ),
            on="talhao_id",
            how="left",
        )
        .with_columns(
            pl.when(pl.col("recomenda_irrigar"))
            .then(pl.lit("💧 irrigar"))
            .when(pl.col("estresse_hidrico") & ~pl.col("recomenda_irrigar").fill_null(False))
            .then(pl.lit("⚠ estresse (sequeiro)"))
            .otherwise(pl.lit("✓ ok"))
            .alias("situação")
        )
    )
    st.dataframe(pandas(visao), use_container_width=True, hide_index=True)


def secao_agua() -> None:
    st.subheader(f"Balanço hídrico · {talhao_id} · últimos {janela} dias")
    if serie.is_empty():
        st.info("Sem balanço no período.")
        return
    pdf = pandas(como_datetime(serie, "dia"))
    linha = (
        alt.Chart(pdf)
        .mark_line(color="#1565c0", point=True)
        .encode(
            x=alt.X("dia:T", title=None),
            y=alt.Y(
                "fracao_agua_disponivel:Q",
                title="fração de água disponível",
                scale=alt.Scale(domain=[0, 1]),
            ),
            tooltip=[
                "dia:T",
                "fracao_agua_disponivel:Q",
                "umidade_media_pct:Q",
                "deficit_mm:Q",
                "estadio:N",
            ],
        )
    )
    limiares = (
        alt.Chart(
            {
                "values": [
                    {"y": FRACAO_IRRIGACAO, "r": "irrigar"},
                    {"y": FRACAO_ESTRESSE, "r": "estresse"},
                ]
            }
        )
        .mark_rule(strokeDash=[6, 4])
        .encode(
            y="y:Q",
            color=alt.Color("r:N", title="limiar", scale=alt.Scale(range=["#c62828", "#f9a825"])),
        )
    )
    st.altair_chart((linha + limiares).properties(height=260), use_container_width=True)

    col_a, col_b = st.columns([2, 1])
    with col_a:
        lamina = pandas(
            como_datetime(
                serie.select("dia", "precipitacao_mm", "irrigacao_mm", "etc_mm").unpivot(
                    index="dia", variable_name="componente", value_name="mm"
                ),
                "dia",
            )
        )
        st.altair_chart(
            alt.Chart(lamina)
            .mark_bar()
            .encode(
                x=alt.X("dia:T", title=None),
                y=alt.Y("mm:Q", title="mm"),
                color=alt.Color(
                    "componente:N",
                    scale=alt.Scale(
                        domain=["precipitacao_mm", "irrigacao_mm", "etc_mm"],
                        range=["#42a5f5", "#26a69a", "#ef6c00"],
                    ),
                ),
                xOffset="componente:N",
                tooltip=["dia:T", "componente:N", "mm:Q"],
            )
            .properties(height=220, title="Entradas (chuva, irrigação) e saída (ETc) de água"),
            use_container_width=True,
        )
    with col_b:
        resumo = {
            "chuva (mm)": serie.get_column("precipitacao_mm").sum(),
            "irrigação (mm)": serie.get_column("irrigacao_mm").sum(),
            "ETc (mm)": serie.get_column("etc_mm").sum(),
            "dias em estresse": int(serie.get_column("estresse_hidrico").fill_null(False).sum()),
            "dias sem clima": int((~serie.get_column("clima_disponivel")).sum()),
        }
        st.dataframe(
            pandas(
                pl.DataFrame(
                    {
                        "indicador": list(resumo),
                        "valor": [round(float(v), 1) for v in resumo.values()],
                    }
                )
            ),
            hide_index=True,
            use_container_width=True,
        )
    with st.expander("Série completa"):
        st.dataframe(pandas(serie.drop("talhao_id")), use_container_width=True, hide_index=True)


def secao_vegetacao() -> None:
    st.subheader(f"Vegetação (NDVI) · {talhao_id}")
    ndvi = gold["fato_ndvi_talhao"].filter(
        (pl.col("talhao_id") == talhao_id) & (pl.col("dia") >= inicio_janela)
    )
    if ndvi.is_empty():
        st.info("Sem cenas no período.")
        return
    pdf = pandas(como_datetime(ndvi, "capturada_em"))
    pontos = (
        alt.Chart(pdf)
        .mark_point(size=90, filled=True)
        .encode(
            x=alt.X("capturada_em:T", title=None),
            y=alt.Y("ndvi_medio:Q", title="NDVI médio", scale=alt.Scale(domain=[0, 1])),
            color=alt.Color(
                "confianca:N",
                title="confiança",
                scale=alt.Scale(domain=list(CORES_CONFIANCA), range=list(CORES_CONFIANCA.values())),
            ),
            shape=alt.Shape("plataforma:N", title="plataforma"),
            tooltip=[
                "imagem_id:N",
                "plataforma:N",
                "ndvi_medio:Q",
                "nuvem_pct:Q",
                "confianca:N",
                "variacao_vs_anterior:Q",
            ],
        )
    )
    linha = (
        alt.Chart(pdf[pdf["confianca"] != "baixa"])
        .mark_line(color="#7cb342")
        .encode(x="capturada_em:T", y="ndvi_medio:Q", detail="plataforma:N")
    )
    st.altair_chart((linha + pontos).properties(height=260), use_container_width=True)
    st.caption(
        "Cenas com confiança baixa (nuvem alta) aparecem, mas não entram na linha nem nas recomendações."
    )
    st.dataframe(
        pandas(
            ndvi.select(
                "dia",
                "plataforma",
                "ndvi_medio",
                "ndvi_desvio",
                "nuvem_pct",
                "confianca",
                "variacao_vs_anterior",
                "com_alerta_dq",
            ).sort("dia", descending=True)
        ),
        hide_index=True,
        use_container_width=True,
    )


def secao_custos() -> None:
    st.subheader(f"Custos operacionais · {talhao_id}")
    custos = gold["fato_custo_diario_talhao"].filter(
        (pl.col("talhao_id") == talhao_id) & (pl.col("dia") >= inicio_janela)
    )
    if custos.is_empty():
        st.info("Sem operações no período.")
        return
    total = custos.get_column("custo_brl").sum()
    m1, m2, m3 = st.columns(3)
    m1.metric("Total no período", brl(total))
    m2.metric("Por hectare", brl(total / talhao["area_ha"]))
    m3.metric("Operações", int(custos.get_column("operacoes").sum()))
    st.altair_chart(
        alt.Chart(pandas(como_datetime(custos, "dia")))
        .mark_bar()
        .encode(
            x=alt.X("dia:T", title=None),
            y=alt.Y("custo_brl:Q", title="R$"),
            color=alt.Color("tipo_operacao:N", title="tipo"),
            tooltip=["dia:T", "tipo_operacao:N", "custo_brl:Q", "operacoes:Q"],
        )
        .properties(height=240),
        use_container_width=True,
    )
    por_tipo = (
        custos.group_by("tipo_operacao")
        .agg(pl.col("custo_brl").sum().round(2), pl.col("operacoes").sum())
        .sort("custo_brl", descending=True)
    )
    st.dataframe(pandas(por_tipo), hide_index=True, use_container_width=True)


def secao_copiloto() -> None:
    st.subheader(f"Copiloto · {talhao_id}")
    caso = st.radio("Caso", list(CASOS), format_func=CASOS.get, horizontal=True)
    md, proveniencia = relatorio_copiloto(caso, talhao_id)
    if md is None:
        st.info("Sem relatório gerado. Rode `agrosmart ia gerar`.")
    else:
        if proveniencia:
            p1, p2, p3 = st.columns(3)
            p1.metric(
                "Motor",
                proveniencia.get("motor", "—"),
                "fallback" if proveniencia.get("fallback") else "direto",
                delta_color="off",
            )
            g = proveniencia.get("guardrails", {})
            p2.metric(
                "Guardrails",
                "aprovada" if g.get("aprovado", True) else "rejeitada",
                f"{len(g.get('violacoes', []))} anotações",
                delta_color="off",
            )
            p3.metric(
                "Prompt",
                proveniencia.get("prompt_versao") or "n/a (regras)",
                proveniencia.get("modelo") or "",
                delta_color="off",
            )
        st.markdown(md)
        if proveniencia:
            with st.expander("Proveniência (o que o motor usou e como foi validado)"):
                st.json(proveniencia)
    if avaliacao:
        st.markdown("**Avaliação da última execução**")
        campos = {"execucoes", "aprovados_guardrails", "ajustes", "fallbacks", "recomendam_irrigar"}
        linhas = [
            {
                "motor": m,
                **{k: v for k, v in dados.items() if k in campos},
                "confiança": " / ".join(f"{k} {v}" for k, v in dados.get("confianca", {}).items()),
            }
            for m, dados in por_motor.items()
        ]
        st.dataframe(pandas(pl.from_dicts(linhas)), hide_index=True, use_container_width=True)
        st.caption(f"motor generativo: {avaliacao.get('modelo_generativo', 'n/a')}")


def secao_qualidade() -> None:
    st.subheader("Qualidade dos dados (última execução do silver)")
    fontes = dq.get("fontes", {})
    if not fontes:
        st.info("Sem relatório de qualidade. Rode `agrosmart processar`.")
    else:
        tabela = pl.from_dicts(
            [
                {
                    "fonte": nome,
                    "lotes": f["lotes"],
                    "lidos": f["lidos"],
                    "aprovados": f["aprovados"],
                    "quarentena": f["quarentena"],
                    "% quarentena": round(f["quarentena"] / f["lidos"] * 100, 2)
                    if f["lidos"]
                    else 0.0,
                    "substituídos": f.get("substituidos", 0),
                    "reconciliação": "ok" if f.get("reconciliacao_ok") else "FALHA",
                }
                for nome, f in fontes.items()
            ]
        )
        st.dataframe(pandas(tabela), hide_index=True, use_container_width=True)
        v = dq.get("defeitos_injetados", {})
        if v.get("total"):
            st.success(
                f"Verificação contra a verdade-terreno da simulação: {v['localizados']}/{v['total']} defeitos injetados localizados na quarentena."
            )
    st.markdown("**Alertas abertos**")
    if alertas.is_empty():
        st.caption("nenhum alerta")
    else:
        st.dataframe(pandas(alertas), hide_index=True, use_container_width=True)


def secao_sensores() -> None:
    st.subheader(f"Sensores · {talhao_id}")
    sensores = gold["dim_sensor"].filter(pl.col("talhao_id") == talhao_id)
    if sensores.is_empty():
        st.info("Sem sensores no talhão.")
    else:
        sensores = sensores.with_columns(
            pl.when(pl.col("bateria_ultima_pct") < BATERIA_MANUTENCAO_PCT)
            .then(pl.lit("🔋 trocar bateria"))
            .when(
                (pl.col("rssi_medio_dbm") < RSSI_FRACO_DBM)
                | (pl.col("leituras_sinal_fraco") / pl.col("leituras") > 0.05)
            )
            .then(pl.lit("📶 sinal fraco"))
            .otherwise(pl.lit("✓ ok"))
            .alias("situação"),
            (pl.col("ultima_leitura") < cfg.instante_referencia - timedelta(hours=1)).alias(
                "atrasado"
            ),
        )
        st.dataframe(
            pandas(
                sensores.select(
                    "sensor_id",
                    "situação",
                    "atrasado",
                    "ultima_leitura",
                    "leituras",
                    "dias_com_leitura",
                    "bateria_ultima_pct",
                    "rssi_medio_dbm",
                    "leituras_bateria_baixa",
                    "leituras_sinal_fraco",
                )
            ),
            hide_index=True,
            use_container_width=True,
        )
    leit = gold["fato_leitura_diaria_talhao"].filter(
        (pl.col("talhao_id") == talhao_id) & (pl.col("dia") >= inicio_janela)
    )
    if not leit.is_empty():
        st.altair_chart(
            alt.Chart(pandas(como_datetime(leit, "dia")))
            .mark_bar(color="#8d6e63")
            .encode(
                x=alt.X("dia:T", title=None),
                y=alt.Y(
                    "cobertura_pct:Q",
                    title="cobertura de leituras (%)",
                    scale=alt.Scale(domain=[0, 100]),
                ),
                tooltip=["dia:T", "cobertura_pct:Q", "n_leituras:Q", "n_sensores_ativos:Q"],
            )
            .properties(height=180, title="Cobertura diária de leituras (sensores × 96 por dia)"),
            use_container_width=True,
        )


def secao_benchmark() -> None:
    st.subheader("Benchmark regional (produto de dados, degrau 3)")
    bench = gold["benchmark_regional"]
    if bench.is_empty():
        st.info("Sem benchmark.")
        return
    st.dataframe(pandas(bench), hide_index=True, use_container_width=True)
    publicaveis = int(bench.get_column("publicavel").sum())
    st.caption(
        f"Só grupos com pelo menos {K_ANONIMATO} talhões são publicáveis (k-anonimato): "
        f"{publicaveis} de {bench.height} culturas nesta amostra. Sem identificador de talhão, "
        "fazenda, pessoa ou coordenada — por construção, na SQL do gold."
    )


def _contar(itens: list[dict], chave: str, ordem: list[str] | None = None) -> dict[str, int]:
    contagem: dict[str, int] = {}
    for item in itens:
        valor = str(item.get(chave) or "—")
        contagem[valor] = contagem.get(valor, 0) + 1
    if ordem:
        return {k: contagem.get(k, 0) for k in ordem} | {
            k: v for k, v in contagem.items() if k not in ordem
        }
    return dict(sorted(contagem.items()))


def secao_automacao() -> None:
    """Fase 6: o que o motor de decisão (``agrosmart automacao``) propôs e executou."""
    st.subheader("Automação inteligente · ações do motor de decisão")
    pasta = dir_acoes()
    nome_arquivo, acoes_json = carregar_acoes(str(pasta))
    if nome_arquivo is None:
        st.warning(
            f"Nenhum arquivo `acoes_<data>.json` em `{pasta.as_posix()}`. "
            "Rode `agrosmart automacao` e recarregue — a seção lê o JSON mais recente."
        )
        return
    if not acoes_json:
        st.error(f"`{nome_arquivo}` não pôde ser lido como JSON.")
        return

    acoes: list[dict] = list(acoes_json.get("acoes") or [])
    riscos: list[dict] = list(acoes_json.get("talhoes") or [])
    resumo: dict = dict(acoes_json.get("resumo") or {})
    por_tipo = dict(resumo.get("por_tipo") or _contar(acoes, "tipo"))
    por_prioridade = {
        k: int(v)
        for k, v in (
            resumo.get("por_prioridade") or _contar(acoes, "prioridade", ORDEM_PRIORIDADE)
        ).items()
    }
    publicadas = sum(1 for a in acoes if a.get("status") == "publicado")
    risco_contagem = _contar(riscos, "risco", list(ROTULO_RISCO))

    st.caption(
        f"arquivo **{nome_arquivo}** · gerado em {acoes_json.get('gerado_em', '—')} · "
        f"data de referência {acoes_json.get('data_ref', '—')} · fonte: "
        f"{acoes_json.get('fonte', '—')}"
    )
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Talhões avaliados", resumo.get("talhoes", len(riscos)))
    m2.metric(
        "Ações propostas",
        resumo.get("acoes", len(acoes)),
        f"{publicadas} publicadas no broker · {len(acoes) - publicadas} simuladas",
        delta_color="off",
    )
    m3.metric("Prioridade alta", por_prioridade.get("alta", 0), delta_color="off")
    m4.metric(
        "Talhões críticos",
        risco_contagem.get("critico", 0),
        f"{risco_contagem.get('atencao', 0)} em atenção",
        delta_color="inverse",
    )
    # ``resumo.por_tipo`` pode trazer ``nenhuma`` (talhões sem ação); o indicador conta
    # só os tipos que viraram ação de fato.
    tipos_de_acao = sorted({str(a.get("tipo")) for a in acoes if a.get("tipo")})
    m5.metric(
        "Tipos de ação", len(tipos_de_acao), " · ".join(tipos_de_acao) or "—", delta_color="off"
    )

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Ações por tipo e prioridade**")
        if acoes:
            matriz = pl.from_dicts(
                [
                    {
                        "tipo": str(a.get("tipo") or "—"),
                        "prioridade": str(a.get("prioridade") or "—"),
                    }
                    for a in acoes
                ]
            )
            st.altair_chart(
                alt.Chart(pandas(matriz))
                .mark_bar()
                .encode(
                    y=alt.Y("tipo:N", title=None, sort="-x"),
                    x=alt.X("count():Q", title="ações"),
                    color=alt.Color(
                        "prioridade:N",
                        title="prioridade",
                        scale=alt.Scale(
                            domain=ORDEM_PRIORIDADE, range=list(CORES_PRIORIDADE.values())
                        ),
                    ),
                    tooltip=["tipo:N", "prioridade:N", "count():Q"],
                )
                .properties(height=max(120, 36 * len(por_tipo) + 40)),
                use_container_width=True,
            )
        else:
            st.caption("nenhuma ação proposta nesta execução")
    with col_b:
        st.markdown("**Risco por talhão**")
        if riscos:
            linhas = []
            for t in riscos:
                indicadores = t.get("indicadores") or {}
                linhas.append(
                    {
                        "talhão": t.get("talhao_id", "—"),
                        "risco": ROTULO_RISCO.get(str(t.get("risco")), str(t.get("risco"))),
                        "ações": sum(1 for a in acoes if a.get("talhao_id") == t.get("talhao_id")),
                        **{k: v for k, v in indicadores.items() if not isinstance(v, (dict, list))},
                    }
                )
            st.dataframe(pandas(pl.from_dicts(linhas)), hide_index=True, use_container_width=True)
        else:
            st.caption("o arquivo não traz avaliação de risco por talhão")

    st.markdown("**Ações por talhão**")
    so_este = st.checkbox(f"só o talhão selecionado ({talhao_id})", value=False)
    visiveis = [a for a in acoes if not so_este or a.get("talhao_id") == talhao_id]
    if not visiveis:
        st.info("Nenhuma ação para o filtro escolhido.")
    else:
        ordem = {p: i for i, p in enumerate(ORDEM_PRIORIDADE)}
        visiveis.sort(
            key=lambda a: (ordem.get(str(a.get("prioridade")), 9), str(a.get("talhao_id")))
        )
        tabela = pl.from_dicts(
            [
                {
                    "id": str(a.get("id") or "—"),
                    "talhão": str(a.get("talhao_id") or "—"),
                    "sensor": str(a.get("sensor_id") or ""),
                    "tipo": str(a.get("tipo") or "—"),
                    "prioridade": str(a.get("prioridade") or "—"),
                    "motivo": str(a.get("motivo") or ""),
                    "parâmetros": json.dumps(a.get("parametros") or {}, ensure_ascii=False),
                    "comando MQTT": (a.get("comando_mqtt") or {}).get("topico")
                    or (a.get("comando_mqtt") or {}).get("topic")
                    or ("sim" if a.get("comando_mqtt") else "—"),
                    "status": str(a.get("status") or "—"),
                    "evidências": len(a.get("evidencias") or []),
                }
                for a in visiveis
            ]
        )
        st.dataframe(pandas(tabela), hide_index=True, use_container_width=True)
        with st.expander("Detalhe das ações (evidências, parâmetros e comando MQTT)"):
            st.json(visiveis)
    st.caption(
        "O motor de decisão lê o gold e as leituras recentes; cada ação cita as evidências que a "
        "motivaram. `publicado` = comando enviado ao broker MQTT; `simulado` = registrado sem envio. "
        "A automação propõe e executa o que foi autorizado; o produtor continua decidindo."
    )


def secao_operacao() -> None:
    """Fase 7: a plataforma rodando sozinha — saúde do worker e os últimos ciclos."""
    st.subheader("Operação contínua da plataforma")
    status = carregar_json("status/operacao.json")
    ciclos = carregar_ciclos()
    if not status and ciclos.is_empty():
        st.info(
            "O worker ainda não rodou. Suba a plataforma com "
            "`docker compose -f infra/docker-compose.yml up -d --build` (ou `agrosmart operar`)."
        )
        return
    if status:
        ultimo = status.get("ultimo_ciclo") or {}
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric(
            "Worker",
            "🟢 ok" if status.get("saudavel") else "🔴 falha",
            status.get("motivo", ""),
            delta_color="off",
        )
        ok, erro = (status.get("ciclos", {}).get(k, 0) for k in ("ok", "erro"))
        c2.metric("Ciclos (desde a subida)", ok + erro, f"{erro} com erro", delta_color="inverse")
        c3.metric(
            "Duração média do ciclo",
            f"{status.get('duracao_media_ciclo_s') or 0:.1f} s",
            f"intervalo {status.get('intervalo_s', 0):.0f} s · {status.get('modo')}",
            delta_color="off",
        )
        c4.metric("Comandos publicados", status.get("comandos_publicados", 0), "QoS 1")
        idade = (status.get("landing") or {}).get("idade_ultima_s", -1)
        c5.metric(
            "Última leitura na landing",
            f"{idade:.0f} s atrás" if idade is not None and idade >= 0 else "—",
            f"{(status.get('landing') or {}).get('arquivos', 0):.0f} arquivos",
            delta_color="off",
        )
        if ultimo:
            st.caption(
                f"último ciclo **{ultimo.get('execucao_id')}** · "
                + " → ".join(f"{e['nome']} ({e['status']})" for e in ultimo.get("etapas", []))
            )
    if ciclos.is_empty():
        return

    por_caso = (
        ciclos.group_by("caso", "versao")
        .agg(
            pl.col("fim").max(),
            pl.col("duracao_s").sum().alias("lead_time_s"),
            (pl.col("status") == "falhou").any().alias("falhou"),
            pl.col("etapa").filter(pl.col("status") == "falhou").first().alias("etapa_falha"),
        )
        .sort("fim")
        # Caso ainda em andamento (só a etapa pulada registrada) fica de fora.
        .filter((pl.col("lead_time_s") > 0) | pl.col("falhou"))
    )
    ciclos = ciclos.filter(pl.col("caso").is_in(por_caso.get_column("caso").to_list()))
    st.markdown("**Duração por etapa nos últimos ciclos** — a barra mais alta é o gargalo")
    barras = (
        ciclos.filter(pl.col("status") != "pulada")
        .join(por_caso.select("caso", pl.col("fim").alias("fim_caso")), on="caso")
        .with_columns(pl.col("fim_caso").dt.strftime("%H:%M:%S").alias("ciclo"))
    )
    grafico = (
        alt.Chart(pandas(barras))
        .mark_bar()
        .encode(
            x=alt.X("ciclo:N", sort=None, title="ciclo (fim, UTC)"),
            y=alt.Y("duracao_s:Q", stack=True, title="segundos"),
            color=alt.Color("etapa:N", sort=ORDEM_ETAPAS, title="etapa"),
            opacity=alt.condition("datum.status == 'falhou'", alt.value(0.45), alt.value(1.0)),
            tooltip=["caso", "versao", "etapa", "status", "duracao_s", "erro"],
        )
        .properties(height=320)
    )
    st.altair_chart(grafico, use_container_width=True)

    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("**Últimos ciclos**")
        tabela = por_caso.sort("fim", descending=True).select(
            pl.col("caso"),
            pl.col("versao"),
            pl.col("fim").dt.strftime("%d/%m %H:%M:%S").alias("fim (UTC)"),
            pl.col("lead_time_s").round(1).alias("lead time (s)"),
            pl.when(pl.col("falhou"))
            .then(pl.lit("❌ ") + pl.col("etapa_falha").fill_null(""))
            .otherwise(pl.lit("✅ ok"))
            .alias("resultado"),
        )
        st.dataframe(pandas(tabela), hide_index=True, use_container_width=True, height=360)
    with c2:
        st.markdown("**Participação de cada etapa no tempo de ciclo**")
        participacao = (
            ciclos.filter(pl.col("status") == "concluida")
            .group_by("etapa")
            .agg(pl.col("duracao_s").mean().alias("media_s"))
            .with_columns((pl.col("media_s") / pl.col("media_s").sum() * 100).alias("pct"))
            .sort("pct", descending=True)
        )
        st.dataframe(
            pandas(participacao.with_columns(pl.col("media_s", "pct").round(1))),
            hide_index=True,
            use_container_width=True,
        )
        processos = carregar_json("processos/metricas.json")
        if processos:
            st.caption(
                "Análise de processo completa em `outputs/processos/relatorio.md` "
                "(`agrosmart processos`)."
            )
    st.caption(
        "Fonte: `outputs/logs/eventos.jsonl` (trilha de eventos, um caso por ciclo) e "
        "`outputs/status/operacao.json` (estado do worker). Monitoramento técnico completo no "
        "Grafana: http://localhost:3000 · alertas no Prometheus: http://localhost:9090/alerts."
    )


{
    "talhao": secao_talhao,
    "agua": secao_agua,
    "vegetacao": secao_vegetacao,
    "custos": secao_custos,
    "copiloto": secao_copiloto,
    "qualidade": secao_qualidade,
    "sensores": secao_sensores,
    "benchmark": secao_benchmark,
    "automacao": secao_automacao,
    "operacao": secao_operacao,
}[secao]()
