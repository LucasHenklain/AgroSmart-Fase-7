"""Context Pack: tudo que o copiloto pode saber sobre um talhão, e nada além disso.

O pacote é montado **só** a partir do gold — as mesmas tabelas que o dashboard
e o SQL leem — e carrega, além dos dados, os limiares agronômicos e os
parâmetros econômicos de referência (`agronomia.py`). Quantidades que o
modelo poderia querer calcular (lâmina para voltar à capacidade de campo, custo
dessa lâmina, valor da produção em risco) já vêm calculadas: o modelo não faz
aritmética, ele cita.

Cada número do pacote é um número que a recomendação **pode** citar. Um número
que não está aqui não existe para o copiloto — é isso que o guardrail de
aterramento verifica.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from agrosmart import agronomia
from agrosmart.config import Config, carregar_config
from agrosmart.processamento.gold import abrir_conexao
from agrosmart.storage import Armazenamento, criar_armazenamento

DIAS_SERIE_HIDRICA = 14
DIAS_CLIMA_RECENTE = 7
DIAS_NDVI = 30


class Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Janela(Modelo):
    inicio: date
    fim: date
    dias: int
    hoje: date


class TalhaoContexto(Modelo):
    talhao_id: str
    nome: str
    fazenda_id: str
    cultura: str
    variedade: str | None
    area_ha: float
    sistema_irrigacao: str
    irrigado: bool
    tipo_solo: str | None
    data_plantio: date
    dap_atual: int
    estadio_atual: str | None
    kc_atual: float | None
    estadio_critico_para_agua: bool
    capacidade_campo_pct: float | None
    ponto_murcha_pct: float | None


class DiaBalanco(Modelo):
    dia: date
    estadio: str | None
    precipitacao_mm: float | None
    eto_mm: float | None
    etc_mm: float | None
    irrigacao_mm: float
    umidade_media_pct: float | None
    fracao_agua_disponivel: float | None
    deficit_mm: float | None
    estresse_hidrico: bool | None
    recomenda_irrigar: bool | None
    clima_disponivel: bool


class ResumoHidrico(Modelo):
    ultimo_dia: date
    umidade_atual_pct: float | None
    fracao_agua_disponivel_atual: float | None
    deficit_atual_mm: float | None
    recomenda_irrigar_hoje: bool
    estresse_hoje: bool
    dias_em_estresse_14d: int
    dias_com_recomendacao_14d: int
    chuva_14d_mm: float
    eto_14d_mm: float
    etc_14d_mm: float
    irrigacao_14d_mm: float
    dias_desde_irrigacao: int | None
    variacao_umidade_7d_pontos: float | None
    dias_sem_clima_14d: int
    lamina_sugerida_mm: float | None = Field(
        description="85 % do déficit até a capacidade de campo, máx. 30 mm; nulo se não irrigado."
    )
    custo_lamina_sugerida_brl: float | None
    custo_irrigacao_brl_mm_ha: float | None
    previsao_chuva_72h_mm: float | None = Field(
        default=None, description="Não disponível nesta fase: a fonte de previsão é roadmap."
    )


class CenaNDVI(Modelo):
    dia: date
    plataforma: str
    ndvi_medio: float
    ndvi_desvio: float | None
    variacao_vs_anterior: float | None
    confianca: str
    nuvem_pct: float


class ResumoVegetacao(Modelo):
    ndvi_ultimo: float | None
    confianca_ultimo: str | None
    dia_ultimo: date | None
    variacao_30d: float | None
    cenas_30d: int
    cenas_baixa_confianca_30d: int
    desuniformidade_ultima: float | None


class ClimaRecente(Modelo):
    ultimo_dia: date | None
    dias_observados_7d: int
    tmax_media_7d: float | None
    tmin_media_7d: float | None
    ur_media_7d: float | None
    dias_ur_alta_7d: int
    dias_chuva_7d: int
    chuva_7d_mm: float
    temperatura_favoravel_a_fungos: bool


class Custos(Modelo):
    periodo_dias: int
    custo_total_brl: float
    custo_por_ha_brl: float
    por_tipo_brl: dict[str, float]
    operacoes: int
    ultima_operacao_dia: date | None
    ultima_operacao_tipo: str | None
    dias_desde_pulverizacao: int | None


class Fertilidade(Modelo):
    laudo_id: str
    coletado_em: date
    profundidade_cm: str
    ph_cacl2: float
    saturacao_bases_pct: float
    classe_saturacao: str
    necessidade_calagem_t_ha: float
    classe_fosforo: str
    fosforo_mg_dm3: float


class SensorContexto(Modelo):
    sensor_id: str
    ultima_leitura: datetime
    leituras: int
    bateria_ultima_pct: float
    bateria_min_pct: float
    rssi_medio_dbm: float
    leituras_com_alerta: int
    leituras_bateria_baixa: int
    leituras_sinal_fraco: int
    fracao_sinal_fraco: float
    precisa_manutencao: bool = Field(description="bateria da última leitura abaixo do limiar")
    sinal_fraco: bool = Field(
        description="RSSI médio abaixo do limiar, ou mais de 5 % das leituras com alerta SEN-11"
    )


class Sensores(Modelo):
    sensores: list[SensorContexto]
    cobertura_media_7d_pct: float | None
    menor_cobertura_7d_pct: float | None


class AlertaContexto(Modelo):
    fonte: str
    regra: str
    tipo: str
    subtipo: str | None
    entidade: str | None
    quantidade: int
    detalhe: str | None


class Economia(Modelo):
    produtividade_ref_t_ha: float
    preco_ref_brl_t: float
    valor_producao_brl: float
    perda_por_dia_estresse_pct: float
    perda_por_dia_estresse_brl: float
    custo_fungicida_brl_ha: float
    custo_fungicida_talhao_brl: float


class Limiares(Modelo):
    fracao_irrigacao: float
    fracao_estresse: float
    nuvem_confianca_media_pct: float
    nuvem_confianca_baixa_pct: float
    dias_ur_alta_risco: int
    ur_alta_pct: float
    temp_risco_fungico_c: tuple[float, float]
    intervalo_min_pulverizacao_dias: int
    bateria_manutencao_pct: float
    rssi_fraco_dbm: int


class ContextPack(Modelo):
    versao: str = "context-pack.v1"
    gerado_em: datetime
    instante_referencia: datetime
    janela: Janela
    talhao: TalhaoContexto
    hidrico: ResumoHidrico
    serie_hidrica_14d: list[DiaBalanco]
    vegetacao: ResumoVegetacao
    cenas_ndvi_30d: list[CenaNDVI]
    clima_recente: ClimaRecente
    custos: Custos
    fertilidade: Fertilidade | None
    sensores: Sensores
    alertas: list[AlertaContexto]
    economia: Economia
    limiares: Limiares
    tabelas_gold_usadas: list[str]
    avisos: list[str]

    def para_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=indent)

    def caminhos(self) -> set[str]:
        """Todos os caminhos pontilhados do pacote (para validar ``dados_usados``)."""
        saida: set[str] = set()

        def caminhar(valor: Any, prefixo: str) -> None:
            if isinstance(valor, dict):
                for chave, item in valor.items():
                    caminho = f"{prefixo}.{chave}" if prefixo else str(chave)
                    saida.add(caminho)
                    caminhar(item, caminho)
            elif isinstance(valor, list):
                for indice, item in enumerate(valor):
                    caminho = f"{prefixo}[{indice}]"
                    saida.add(caminho)
                    caminhar(item, caminho)

        caminhar(self.model_dump(mode="json"), "")
        return saida

    def textos(self) -> list[str]:
        """Literais de texto do pacote (nomes, variedades, ids), do maior para o menor.

        O guardrail de aterramento remove esses literais antes de procurar
        números: citar "AG 8088 PRO4" ou "LAU-2026-00474" não é inventar 8088.
        """
        saida: set[str] = set()

        def caminhar(valor: Any) -> None:
            if isinstance(valor, str):
                if len(valor) >= 3:
                    saida.add(valor)
            elif isinstance(valor, dict):
                for item in valor.values():
                    caminhar(item)
            elif isinstance(valor, list):
                for item in valor:
                    caminhar(item)

        caminhar(self.model_dump(mode="json", exclude={"avisos"}))
        return sorted(saida, key=len, reverse=True)

    def numeros(self) -> set[float]:
        """Todos os números do pacote, arredondados — o universo citável."""
        saida: set[float] = set()

        def caminhar(valor: Any) -> None:
            if isinstance(valor, bool):
                return
            if isinstance(valor, int | float):
                saida.add(round(float(valor), 3))
            elif isinstance(valor, dict):
                for item in valor.values():
                    caminhar(item)
            elif isinstance(valor, list):
                for item in valor:
                    caminhar(item)

        caminhar(
            self.model_dump(mode="json", exclude={"gerado_em", "instante_referencia", "versao"})
        )
        return saida


# ---------------------------------------------------------------------------
# Construção
# ---------------------------------------------------------------------------


def _df(con: duckdb.DuckDBPyConnection, sql: str, *params: Any) -> pl.DataFrame:
    return pl.from_arrow(con.execute(sql, list(params)).fetch_arrow_table())  # type: ignore[return-value]


def _linhas(df: pl.DataFrame) -> list[dict[str, Any]]:
    return df.to_dicts()


def _opcional(valor: Any) -> Any:
    return None if valor is None else valor


def construir_context_pack(
    talhao_id: str,
    *,
    con: duckdb.DuckDBPyConnection,
    config: Config,
) -> ContextPack:
    """Monta o pacote de um talhão a partir das tabelas do gold registradas em ``con``."""
    ref = config.instante_referencia
    hoje = config.data_referencia
    avisos: list[str] = []
    tabelas: list[str] = []

    # --- talhão -----------------------------------------------------------------
    talhao_df = _df(con, "SELECT * FROM dim_talhao WHERE talhao_id = ?", talhao_id)
    if talhao_df.is_empty():
        raise KeyError(f"talhão {talhao_id!r} não existe em dim_talhao")
    t = talhao_df.to_dicts()[0]
    tabelas.append("dim_talhao")

    # --- série hídrica -----------------------------------------------------------
    # 14 dias contando o "hoje" (só a madrugada): hoje-13 … hoje.
    inicio_serie = hoje - timedelta(days=DIAS_SERIE_HIDRICA - 1)
    serie_df = _df(
        con,
        """
        SELECT dia, estadio, kc, precipitacao_mm, eto_mm, etc_mm, irrigacao_mm, umidade_media_pct,
               fracao_agua_disponivel, deficit_mm, estresse_hidrico, recomenda_irrigar,
               dias_desde_irrigacao, clima_disponivel
        FROM fato_balanco_hidrico WHERE talhao_id = ? AND dia >= ? ORDER BY dia
        """,
        talhao_id,
        inicio_serie,
    )
    tabelas.append("fato_balanco_hidrico")
    serie = _linhas(serie_df)
    if not serie:
        raise ValueError(
            f"talhão {talhao_id!r} sem balanço hídrico nos últimos {DIAS_SERIE_HIDRICA} dias"
        )
    ultimo = serie[-1]
    com_umidade = [d for d in serie if d["umidade_media_pct"] is not None]
    atual = com_umidade[-1] if com_umidade else ultimo
    if atual is not ultimo:
        avisos.append(
            f"sem leitura de sensores em {ultimo['dia']}; umidade atual é de {atual['dia']}"
        )

    fad_atual = atual["fracao_agua_disponivel"]
    deficit_atual = atual["deficit_mm"]
    irrigado = bool(t["irrigado"])
    lamina = None
    custo_lamina = None
    custo_mm_ha = agronomia.CUSTO_IRRIGACAO_BRL_MM_HA.get(t["sistema_irrigacao"])
    if irrigado and deficit_atual is not None:
        lamina = max(round(min(deficit_atual * 0.85, 30.0) * 2) / 2, 0.0)
        if custo_mm_ha is not None:
            custo_lamina = round(lamina * float(t["area_ha"]) * custo_mm_ha, 2)

    sete_dias = [d for d in com_umidade if d["dia"] >= hoje - timedelta(days=7)]
    variacao_7d = (
        round(sete_dias[-1]["umidade_media_pct"] - sete_dias[0]["umidade_media_pct"], 2)
        if len(sete_dias) >= 2
        else None
    )
    estadio_atual = atual["estadio"] or ultimo["estadio"]
    hidrico = ResumoHidrico(
        ultimo_dia=atual["dia"],
        umidade_atual_pct=_opcional(atual["umidade_media_pct"]),
        fracao_agua_disponivel_atual=_opcional(fad_atual),
        deficit_atual_mm=_opcional(deficit_atual),
        recomenda_irrigar_hoje=bool(atual["recomenda_irrigar"]),
        estresse_hoje=bool(atual["estresse_hidrico"]),
        dias_em_estresse_14d=sum(1 for d in serie if d["estresse_hidrico"]),
        dias_com_recomendacao_14d=sum(1 for d in serie if d["recomenda_irrigar"]),
        chuva_14d_mm=round(sum(d["precipitacao_mm"] or 0.0 for d in serie), 1),
        eto_14d_mm=round(sum(d["eto_mm"] or 0.0 for d in serie), 1),
        etc_14d_mm=round(sum(d["etc_mm"] or 0.0 for d in serie), 1),
        irrigacao_14d_mm=round(sum(d["irrigacao_mm"] or 0.0 for d in serie), 1),
        dias_desde_irrigacao=_opcional(ultimo["dias_desde_irrigacao"]),
        variacao_umidade_7d_pontos=variacao_7d,
        # O clima do dia corrente só chega no dia seguinte: às 03h de hoje ele não
        # existe ainda, e isso não é lacuna — só dias fechados contam.
        dias_sem_clima_14d=sum(1 for d in serie if not d["clima_disponivel"] and d["dia"] < hoje),
        lamina_sugerida_mm=lamina,
        custo_lamina_sugerida_brl=custo_lamina,
        custo_irrigacao_brl_mm_ha=custo_mm_ha,
        previsao_chuva_72h_mm=None,
    )
    avisos.append(
        "previsão meteorológica não disponível nesta fase — decidir com base no observado"
    )
    if hidrico.dias_sem_clima_14d:
        avisos.append(
            f"{hidrico.dias_sem_clima_14d} dia(s) da série sem clima (quarentena ou ausência)"
        )

    serie_modelo = [
        DiaBalanco(
            dia=d["dia"],
            estadio=d["estadio"],
            precipitacao_mm=_opcional(d["precipitacao_mm"]),
            eto_mm=_opcional(d["eto_mm"]),
            etc_mm=_opcional(d["etc_mm"]),
            irrigacao_mm=float(d["irrigacao_mm"] or 0.0),
            umidade_media_pct=_opcional(d["umidade_media_pct"]),
            fracao_agua_disponivel=_opcional(d["fracao_agua_disponivel"]),
            deficit_mm=_opcional(d["deficit_mm"]),
            estresse_hidrico=_opcional(d["estresse_hidrico"]),
            recomenda_irrigar=_opcional(d["recomenda_irrigar"]),
            clima_disponivel=bool(d["clima_disponivel"]),
        )
        for d in serie
    ]

    # --- vegetação -------------------------------------------------------------------
    cenas_df = _df(
        con,
        """
        SELECT dia, plataforma, ndvi_medio, ndvi_desvio, variacao_vs_anterior, confianca, nuvem_pct
        FROM fato_ndvi_talhao WHERE talhao_id = ? AND dia >= ? ORDER BY capturada_em
        """,
        talhao_id,
        hoje - timedelta(days=DIAS_NDVI),
    )
    tabelas.append("fato_ndvi_talhao")
    cenas = _linhas(cenas_df)
    confiaveis = [c for c in cenas if c["confianca"] != "baixa"]
    ultima = cenas[-1] if cenas else None
    vegetacao = ResumoVegetacao(
        ndvi_ultimo=ultima["ndvi_medio"] if ultima else None,
        confianca_ultimo=ultima["confianca"] if ultima else None,
        dia_ultimo=ultima["dia"] if ultima else None,
        variacao_30d=(
            round(confiaveis[-1]["ndvi_medio"] - confiaveis[0]["ndvi_medio"], 3)
            if len(confiaveis) >= 2
            else None
        ),
        cenas_30d=len(cenas),
        cenas_baixa_confianca_30d=sum(1 for c in cenas if c["confianca"] == "baixa"),
        desuniformidade_ultima=ultima["ndvi_desvio"] if ultima else None,
    )
    if ultima and ultima["confianca"] == "baixa":
        avisos.append(
            f"última cena NDVI ({ultima['dia']}) com {ultima['nuvem_pct']} % de nuvem: "
            "confiança baixa"
        )

    # --- clima recente -----------------------------------------------------------------
    clima_df = _df(
        con,
        """
        SELECT data, temp_min_c, temp_max_c, temp_media_c, umidade_relativa_pct, precipitacao_mm
        FROM fato_clima_diario WHERE fazenda_id = ? AND data >= ? ORDER BY data
        """,
        t["fazenda_id"],
        hoje - timedelta(days=DIAS_CLIMA_RECENTE),
    )
    tabelas.append("fato_clima_diario")
    clima = _linhas(clima_df)
    tmed_media = sum(c["temp_media_c"] for c in clima) / len(clima) if clima else None
    faixa = agronomia.TEMP_RISCO_FUNGICO_C
    clima_recente = ClimaRecente(
        ultimo_dia=clima[-1]["data"] if clima else None,
        dias_observados_7d=len(clima),
        tmax_media_7d=round(sum(c["temp_max_c"] for c in clima) / len(clima), 1) if clima else None,
        tmin_media_7d=round(sum(c["temp_min_c"] for c in clima) / len(clima), 1) if clima else None,
        ur_media_7d=round(sum(c["umidade_relativa_pct"] for c in clima) / len(clima), 1)
        if clima
        else None,
        dias_ur_alta_7d=sum(1 for c in clima if c["umidade_relativa_pct"] > agronomia.UR_ALTA_PCT),
        dias_chuva_7d=sum(1 for c in clima if (c["precipitacao_mm"] or 0) > 0),
        chuva_7d_mm=round(sum(c["precipitacao_mm"] or 0.0 for c in clima), 1),
        temperatura_favoravel_a_fungos=bool(
            tmed_media is not None and faixa[0] <= tmed_media <= faixa[1]
        ),
    )

    # --- custos ------------------------------------------------------------------------
    custos_df = _df(
        con,
        """
        SELECT dia, tipo_operacao, custo_brl, operacoes FROM fato_custo_diario_talhao
        WHERE talhao_id = ? ORDER BY dia
        """,
        talhao_id,
    )
    tabelas.append("fato_custo_diario_talhao")
    custos = _linhas(custos_df)
    por_tipo: dict[str, float] = {}
    for c in custos:
        por_tipo[c["tipo_operacao"]] = round(
            por_tipo.get(c["tipo_operacao"], 0.0) + float(c["custo_brl"]), 2
        )
    total = round(sum(por_tipo.values()), 2)
    ultima_op = custos[-1] if custos else None
    pulverizacoes = [c for c in custos if c["tipo_operacao"] == "pulverizacao"]
    custos_modelo = Custos(
        periodo_dias=config.dias_historico,
        custo_total_brl=total,
        custo_por_ha_brl=round(total / float(t["area_ha"]), 2) if t["area_ha"] else 0.0,
        por_tipo_brl=dict(sorted(por_tipo.items())),
        operacoes=int(sum(c["operacoes"] for c in custos)),
        ultima_operacao_dia=ultima_op["dia"] if ultima_op else None,
        ultima_operacao_tipo=ultima_op["tipo_operacao"] if ultima_op else None,
        dias_desde_pulverizacao=(hoje - pulverizacoes[-1]["dia"]).days if pulverizacoes else None,
    )

    # --- fertilidade -------------------------------------------------------------------
    fert_df = _df(
        con,
        """
        SELECT laudo_id, coletado_em, profundidade_cm, ph_cacl2, saturacao_bases_pct,
               classe_saturacao, necessidade_calagem_t_ha, classe_fosforo, fosforo_mg_dm3
        FROM fato_fertilidade_talhao WHERE talhao_id = ? AND profundidade_cm = '0-20'
        ORDER BY coletado_em DESC LIMIT 1
        """,
        talhao_id,
    )
    tabelas.append("fato_fertilidade_talhao")
    fertilidade = None
    if not fert_df.is_empty():
        f = fert_df.to_dicts()[0]
        fertilidade = Fertilidade(
            laudo_id=f["laudo_id"],
            coletado_em=f["coletado_em"],
            profundidade_cm=f["profundidade_cm"],
            ph_cacl2=float(f["ph_cacl2"]),
            saturacao_bases_pct=float(f["saturacao_bases_pct"]),
            classe_saturacao=f["classe_saturacao"],
            necessidade_calagem_t_ha=float(f["necessidade_calagem_t_ha"]),
            classe_fosforo=f["classe_fosforo"],
            fosforo_mg_dm3=float(f["fosforo_mg_dm3"]),
        )
    else:
        avisos.append("sem laudo de solo 0–20 cm aprovado para o talhão")

    # --- sensores ------------------------------------------------------------------------
    sensores_df = _df(
        con,
        """
        SELECT sensor_id, ultima_leitura, leituras, bateria_ultima_pct, bateria_min_pct,
               rssi_medio_dbm, leituras_com_alerta, leituras_bateria_baixa, leituras_sinal_fraco
        FROM dim_sensor WHERE talhao_id = ? ORDER BY sensor_id
        """,
        talhao_id,
    )
    tabelas.append("dim_sensor")
    cobertura_df = _df(
        con,
        """
        SELECT avg(cobertura_pct) AS media, min(cobertura_pct) AS minimo
        FROM fato_leitura_diaria_talhao WHERE talhao_id = ? AND dia >= ? AND dia < ?
        """,
        talhao_id,
        hoje - timedelta(days=DIAS_CLIMA_RECENTE),
        hoje,
    )
    tabelas.append("fato_leitura_diaria_talhao")
    cob = (
        cobertura_df.to_dicts()[0]
        if not cobertura_df.is_empty()
        else {"media": None, "minimo": None}
    )
    sensores = Sensores(
        sensores=[
            SensorContexto(
                sensor_id=s["sensor_id"],
                ultima_leitura=s["ultima_leitura"],
                leituras=int(s["leituras"]),
                bateria_ultima_pct=float(s["bateria_ultima_pct"]),
                bateria_min_pct=float(s["bateria_min_pct"]),
                rssi_medio_dbm=float(s["rssi_medio_dbm"]),
                leituras_com_alerta=int(s["leituras_com_alerta"]),
                leituras_bateria_baixa=int(s["leituras_bateria_baixa"]),
                leituras_sinal_fraco=int(s["leituras_sinal_fraco"]),
                fracao_sinal_fraco=round(
                    int(s["leituras_sinal_fraco"]) / max(int(s["leituras"]), 1), 3
                ),
                precisa_manutencao=float(s["bateria_ultima_pct"])
                < agronomia.BATERIA_MANUTENCAO_PCT,
                sinal_fraco=(
                    float(s["rssi_medio_dbm"]) < agronomia.RSSI_FRACO_DBM
                    or int(s["leituras_sinal_fraco"]) / max(int(s["leituras"]), 1) > 0.05
                ),
            )
            for s in _linhas(sensores_df)
        ],
        cobertura_media_7d_pct=round(float(cob["media"]), 1) if cob["media"] is not None else None,
        menor_cobertura_7d_pct=round(float(cob["minimo"]), 1)
        if cob["minimo"] is not None
        else None,
    )

    # --- alertas de qualidade --------------------------------------------------------------
    ids_sensores = [s.sensor_id for s in sensores.sensores]
    alertas_df = _df(con, "SELECT * FROM fato_alerta_qualidade ORDER BY fonte, regra, entidade")
    tabelas.append("fato_alerta_qualidade")
    alertas: list[AlertaContexto] = []
    for a in _linhas(alertas_df):
        entidade = a["entidade"]
        relevante = entidade is None or entidade in ids_sensores or entidade == talhao_id
        # alertas de estação: só os da estação da fazenda
        if entidade and entidade.startswith("EST-"):
            relevante = True
        if not relevante:
            continue
        alertas.append(
            AlertaContexto(
                fonte=a["fonte"],
                regra=a["regra"],
                tipo=a["tipo"],
                subtipo=a["subtipo"],
                entidade=entidade,
                quantidade=int(a["quantidade"]),
                detalhe=a["detalhe"],
            )
        )

    # --- talhão, economia, limiares -------------------------------------------------------
    dap = (hoje - t["data_plantio"]).days
    talhao = TalhaoContexto(
        talhao_id=t["talhao_id"],
        nome=t["nome"],
        fazenda_id=t["fazenda_id"],
        cultura=t["cultura"],
        variedade=t.get("variedade"),
        area_ha=float(t["area_ha"]),
        sistema_irrigacao=t["sistema_irrigacao"],
        irrigado=irrigado,
        tipo_solo=t.get("tipo_solo"),
        data_plantio=t["data_plantio"],
        dap_atual=dap,
        estadio_atual=estadio_atual,
        kc_atual=_opcional(atual.get("kc")),
        estadio_critico_para_agua=estadio_atual in agronomia.ESTADIOS_CRITICOS_AGUA,
        capacidade_campo_pct=_opcional(t.get("capacidade_campo_pct")),
        ponto_murcha_pct=_opcional(t.get("ponto_murcha_pct")),
    )
    valor_producao = round(agronomia.valor_producao_brl(t["cultura"], float(t["area_ha"])), 2)
    perda_pct = agronomia.perda_dia_estresse_pct(estadio_atual)
    economia = Economia(
        produtividade_ref_t_ha=agronomia.PRODUTIVIDADE_REF_T_HA.get(t["cultura"], 0.0),
        preco_ref_brl_t=agronomia.PRECO_REF_BRL_T.get(t["cultura"], 0.0),
        valor_producao_brl=valor_producao,
        perda_por_dia_estresse_pct=perda_pct,
        perda_por_dia_estresse_brl=round(valor_producao * perda_pct / 100, 2),
        custo_fungicida_brl_ha=agronomia.CUSTO_FUNGICIDA_BRL_HA,
        custo_fungicida_talhao_brl=round(agronomia.CUSTO_FUNGICIDA_BRL_HA * float(t["area_ha"]), 2),
    )
    limiares = Limiares(
        fracao_irrigacao=agronomia.FRACAO_IRRIGACAO,
        fracao_estresse=agronomia.FRACAO_ESTRESSE,
        nuvem_confianca_media_pct=agronomia.NUVEM_CONFIANCA_MEDIA_PCT,
        nuvem_confianca_baixa_pct=agronomia.NUVEM_CONFIANCA_BAIXA_PCT,
        dias_ur_alta_risco=agronomia.DIAS_UR_ALTA_RISCO,
        ur_alta_pct=agronomia.UR_ALTA_PCT,
        temp_risco_fungico_c=agronomia.TEMP_RISCO_FUNGICO_C,
        intervalo_min_pulverizacao_dias=agronomia.INTERVALO_MIN_PULVERIZACAO_DIAS,
        bateria_manutencao_pct=agronomia.BATERIA_MANUTENCAO_PCT,
        rssi_fraco_dbm=agronomia.RSSI_FRACO_DBM,
    )

    return ContextPack(
        gerado_em=datetime.now(UTC).replace(microsecond=0),
        instante_referencia=ref,
        janela=Janela(inicio=serie[0]["dia"], fim=serie[-1]["dia"], dias=len(serie), hoje=hoje),
        talhao=talhao,
        hidrico=hidrico,
        serie_hidrica_14d=serie_modelo,
        vegetacao=vegetacao,
        cenas_ndvi_30d=[
            CenaNDVI(
                dia=c["dia"],
                plataforma=c["plataforma"],
                ndvi_medio=float(c["ndvi_medio"]),
                ndvi_desvio=_opcional(c["ndvi_desvio"]),
                variacao_vs_anterior=_opcional(c["variacao_vs_anterior"]),
                confianca=c["confianca"],
                nuvem_pct=float(c["nuvem_pct"]),
            )
            for c in cenas
        ],
        clima_recente=clima_recente,
        custos=custos_modelo,
        fertilidade=fertilidade,
        sensores=sensores,
        alertas=alertas,
        economia=economia,
        limiares=limiares,
        tabelas_gold_usadas=sorted(set(tabelas)),
        avisos=avisos,
    )


def talhoes_disponiveis(con: duckdb.DuckDBPyConnection) -> list[str]:
    return [
        r[0] for r in con.execute("SELECT talhao_id FROM dim_talhao ORDER BY talhao_id").fetchall()
    ]


def abrir_gold(
    config: Config | None = None, armazenamento: Armazenamento | None = None
) -> duckdb.DuckDBPyConnection:
    cfg = config or carregar_config()
    lake = armazenamento or criar_armazenamento(cfg)
    return abrir_conexao(lake, silver=False, gold=True)
