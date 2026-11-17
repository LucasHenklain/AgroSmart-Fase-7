"""Silver → gold: o modelo dimensional e os indicadores agronômicos.

As tabelas do silver são registradas como *views* em um DuckDB em memória e
cada tabela do gold é uma consulta SQL declarada em :data:`TABELAS` — com
grão, descrição e dicionário de colunas, porque o catálogo gera a
documentação a partir daqui. O resultado volta como Parquet para o lake pelo
mesmo ``Armazenamento`` que grava as outras camadas, então o gold funciona
igual em MinIO e em filesystem, sem extensão ``httpfs``.

Por que SQL e não Polars aqui: o gold é a camada que analistas leem. Uma
consulta SQL com CTEs nomeadas é documentação executável do indicador — o
balanço hídrico está escrito exatamente como se explica em sala.
"""

from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import duckdb
import polars as pl

from agrosmart.agronomia import (
    FRACAO_ESTRESSE,
    FRACAO_IRRIGACAO,
    K_ANONIMATO,
    NUVEM_CONFIANCA_BAIXA_PCT,
    NUVEM_CONFIANCA_MEDIA_PCT,
    PROFUNDIDADE_RAIZ_MM,
    tabela_fenologia,
)
from agrosmart.compat import para_arrow
from agrosmart.config import Camada, Config, carregar_config
from agrosmart.contratos import contratos_ativos
from agrosmart.log import Evento, obter_logger, registrar_evento
from agrosmart.processamento.silver import esquema_silver, ler_tabela, limpar_prefixo
from agrosmart.storage import Armazenamento, criar_armazenamento

TIPO_PARQUET = "application/vnd.apache.parquet"


@dataclass(frozen=True, slots=True)
class TabelaGold:
    nome: str
    grao: str
    descricao: str
    fontes: tuple[str, ...]
    colunas: dict[str, str]
    sql: str


# ---------------------------------------------------------------------------
# Definições
# ---------------------------------------------------------------------------

TABELAS: dict[str, TabelaGold] = {}


def _registrar(tabela: TabelaGold) -> None:
    TABELAS[tabela.nome] = tabela


_registrar(
    TabelaGold(
        nome="dim_fenologia",
        grao="um estádio por cultura (ou um mês, para perenes)",
        descricao="Estádios fenológicos com coeficiente de cultura Kc (FAO-56). Vem do "
        "conhecimento agronômico compartilhado, não de uma fonte ingerida.",
        fontes=(),
        colunas={
            "cultura": "cultura no domínio do contrato erp_talhoes",
            "estadio": "nome do estádio fenológico",
            "dia_inicio": "dias após o plantio em que o estádio começa (nulo para perenes)",
            "dia_fim": "último dia após o plantio do estádio (9999 = até o fim do ciclo)",
            "mes": "mês do ano que define o estádio (só perenes)",
            "kc": "coeficiente de cultura para ETc = Kc × ETo",
            "perene": "verdadeiro quando o estádio é definido pelo calendário",
        },
        sql="SELECT * FROM dim_fenologia_base ORDER BY cultura, perene, dia_inicio, mes",
    )
)

_registrar(
    TabelaGold(
        nome="dim_talhao",
        grao="um talhão",
        descricao="Cadastro vigente dos talhões: a versão mais recente e válida do ERP.",
        fontes=("erp_talhoes",),
        colunas={
            "talhao_id": "chave conformada usada em todo o lake",
            "fazenda_id": "fazenda a que pertence",
            "nome": "nome usado pelo produtor (identificador — não sai em agregados)",
            "area_ha": "área produtiva em hectares",
            "cultura": "cultura da safra corrente",
            "variedade": "cultivar ou híbrido",
            "data_plantio": "data de semeadura",
            "data_colheita_prevista": "previsão do produtor",
            "latitude": "centroide WGS-84 (localização — não sai em agregados)",
            "longitude": "centroide WGS-84 (localização — não sai em agregados)",
            "tipo_solo": "classe textural",
            "sistema_irrigacao": "pivô, aspersão, gotejamento, sulco ou sequeiro",
            "capacidade_campo_pct": "umidade na capacidade de campo",
            "ponto_murcha_pct": "umidade no ponto de murcha permanente",
            "irrigado": "verdadeiro quando o talhão tem sistema de irrigação",
            "cadastro_atualizado_em": "extração do ERP que originou a versão vigente",
        },
        sql="""
            SELECT talhao_id, fazenda_id, nome, area_ha, cultura, variedade, data_plantio,
                   data_colheita_prevista, latitude, longitude, tipo_solo, sistema_irrigacao,
                   capacidade_campo_pct, ponto_murcha_pct,
                   sistema_irrigacao <> 'sequeiro' AS irrigado,
                   extraido_em AS cadastro_atualizado_em
            FROM erp_talhoes
            ORDER BY talhao_id
        """,
    )
)

_registrar(
    TabelaGold(
        nome="dim_fazenda",
        grao="uma fazenda",
        descricao="Fazendas com sua estação meteorológica de referência e totais de área.",
        fontes=("erp_talhoes", "clima_diario"),
        colunas={
            "fazenda_id": "identificador da fazenda",
            "estacao_id": "estação meteorológica que a atende",
            "qtd_talhoes": "talhões cadastrados",
            "area_total_ha": "soma das áreas dos talhões",
            "culturas": "culturas presentes, separadas por vírgula",
        },
        sql="""
            WITH estacoes AS (
                SELECT fazenda_id, any_value(estacao_id) AS estacao_id FROM clima_diario GROUP BY 1
            )
            SELECT t.fazenda_id, e.estacao_id, count(*) AS qtd_talhoes,
                   round(sum(t.area_ha), 1) AS area_total_ha,
                   string_agg(DISTINCT t.cultura, ', ' ORDER BY t.cultura) AS culturas
            FROM dim_talhao t LEFT JOIN estacoes e USING (fazenda_id)
            GROUP BY 1, 2 ORDER BY 1
        """,
    )
)

_registrar(
    TabelaGold(
        nome="dim_estacao",
        grao="uma estação meteorológica",
        descricao="Estações observadas e a cobertura temporal de cada uma.",
        fontes=("clima_diario",),
        colunas={
            "estacao_id": "identificador da estação",
            "fazenda_id": "fazenda atendida",
            "dias_observados": "dias com observação no silver",
            "primeiro_dia": "primeira observação",
            "ultimo_dia": "última observação",
            "chuva_total_mm": "precipitação acumulada no período",
        },
        sql="""
            SELECT estacao_id, any_value(fazenda_id) AS fazenda_id, count(*) AS dias_observados,
                   min(data) AS primeiro_dia, max(data) AS ultimo_dia,
                   round(sum(precipitacao_mm), 1) AS chuva_total_mm
            FROM clima_diario GROUP BY 1 ORDER BY 1
        """,
    )
)

_registrar(
    TabelaGold(
        nome="dim_sensor",
        grao="um sensor de campo",
        descricao="Inventário dos sensores a partir do que publicaram: cobertura, bateria e sinal.",
        fontes=("sensores_leitura",),
        colunas={
            "sensor_id": "identificador do dispositivo",
            "talhao_id": "talhão onde está instalado",
            "primeira_leitura": "primeira leitura aprovada",
            "ultima_leitura": "última leitura aprovada",
            "leituras": "leituras aprovadas no período",
            "dias_com_leitura": "dias distintos com pelo menos uma leitura",
            "bateria_ultima_pct": "bateria na última leitura",
            "bateria_min_pct": "menor bateria observada",
            "rssi_medio_dbm": "intensidade média do sinal",
            "leituras_com_alerta": "leituras que acionaram alguma regra de alerta",
            "leituras_bateria_baixa": "leituras com bateria abaixo do limiar (SEN-10)",
            "leituras_sinal_fraco": "leituras com sinal abaixo do limiar (SEN-11)",
        },
        sql="""
            SELECT sensor_id, any_value(talhao_id) AS talhao_id,
                   min(medido_em) AS primeira_leitura, max(medido_em) AS ultima_leitura,
                   count(*) AS leituras,
                   count(DISTINCT CAST(medido_em AS DATE)) AS dias_com_leitura,
                   arg_max(bateria_pct, medido_em) AS bateria_ultima_pct,
                   min(bateria_pct) AS bateria_min_pct,
                   round(avg(rssi_dbm), 1) AS rssi_medio_dbm,
                   sum(CASE WHEN len(_alertas) > 0 THEN 1 ELSE 0 END) AS leituras_com_alerta,
                   sum(CASE WHEN list_contains(_alertas, 'SEN-10') THEN 1 ELSE 0 END)
                       AS leituras_bateria_baixa,
                   sum(CASE WHEN list_contains(_alertas, 'SEN-11') THEN 1 ELSE 0 END)
                       AS leituras_sinal_fraco
            FROM sensores_leitura GROUP BY 1 ORDER BY 1
        """,
    )
)

_registrar(
    TabelaGold(
        nome="fato_clima_diario",
        grao="uma estação em um dia",
        descricao="Observação meteorológica diária, pronta para junção por fazenda.",
        fontes=("clima_diario",),
        colunas={
            "estacao_id": "estação",
            "fazenda_id": "fazenda atendida",
            "data": "dia observado",
            "temp_min_c": "temperatura mínima",
            "temp_max_c": "temperatura máxima",
            "temp_media_c": "temperatura média",
            "umidade_relativa_pct": "umidade relativa média",
            "precipitacao_mm": "chuva acumulada",
            "velocidade_vento_ms": "vento médio",
            "radiacao_mj_m2": "radiação solar global",
            "eto_mm": "evapotranspiração de referência (FAO-56)",
            "dia_chuvoso": "verdadeiro quando houve precipitação",
            "origem_api": "provedor da observação",
        },
        sql="""
            SELECT estacao_id, fazenda_id, data, temp_min_c, temp_max_c, temp_media_c,
                   umidade_relativa_pct, precipitacao_mm, velocidade_vento_ms, radiacao_mj_m2,
                   eto_mm, precipitacao_mm > 0 AS dia_chuvoso, origem_api
            FROM clima_diario ORDER BY estacao_id, data
        """,
    )
)

_registrar(
    TabelaGold(
        nome="fato_leitura_diaria_talhao",
        grao="um talhão em um dia",
        descricao="Agregação diária das leituras de sensores por talhão, com cobertura e alertas.",
        fontes=("sensores_leitura",),
        colunas={
            "talhao_id": "talhão",
            "dia": "dia das leituras",
            "n_leituras": "leituras aprovadas no dia",
            "n_sensores_ativos": "sensores que publicaram no dia",
            "umidade_media_pct": "umidade volumétrica média do solo",
            "umidade_min_pct": "mínima do dia",
            "umidade_max_pct": "máxima do dia",
            "temp_solo_media_c": "temperatura média do solo",
            "temp_ar_min_c": "temperatura mínima do ar",
            "temp_ar_max_c": "temperatura máxima do ar",
            "umidade_ar_media_pct": "umidade relativa média do ar",
            "condutividade_media_ds_m": "condutividade elétrica média",
            "ph_medio": "pH médio",
            "bateria_min_pct": "menor bateria entre os sensores do talhão",
            "rssi_medio_dbm": "sinal médio",
            "leituras_esperadas": "sensores instalados × 96 leituras/dia",
            "cobertura_pct": "n_leituras / leituras_esperadas",
            "leituras_com_alerta": "leituras que acionaram regra de alerta",
        },
        sql="""
            WITH instalados AS (
                SELECT talhao_id, count(DISTINCT sensor_id) AS sensores
                FROM sensores_leitura GROUP BY 1
            )
            SELECT s.talhao_id, CAST(s.medido_em AS DATE) AS dia,
                   count(*) AS n_leituras, count(DISTINCT s.sensor_id) AS n_sensores_ativos,
                   round(avg(umidade_solo_pct), 2) AS umidade_media_pct,
                   round(min(umidade_solo_pct), 2) AS umidade_min_pct,
                   round(max(umidade_solo_pct), 2) AS umidade_max_pct,
                   round(avg(temperatura_solo_c), 2) AS temp_solo_media_c,
                   round(min(temperatura_ar_c), 2) AS temp_ar_min_c,
                   round(max(temperatura_ar_c), 2) AS temp_ar_max_c,
                   round(avg(umidade_ar_pct), 2) AS umidade_ar_media_pct,
                   round(avg(condutividade_ds_m), 3) AS condutividade_media_ds_m,
                   round(avg(ph), 2) AS ph_medio,
                   round(min(bateria_pct), 1) AS bateria_min_pct,
                   round(avg(rssi_dbm), 1) AS rssi_medio_dbm,
                   i.sensores * 96 AS leituras_esperadas,
                   round(count(*) * 100.0 / (i.sensores * 96), 1) AS cobertura_pct,
                   sum(CASE WHEN len(s._alertas) > 0 THEN 1 ELSE 0 END) AS leituras_com_alerta
            FROM sensores_leitura s JOIN instalados i USING (talhao_id)
            GROUP BY 1, 2, i.sensores ORDER BY 1, 2
        """,
    )
)

_registrar(
    TabelaGold(
        nome="fato_balanco_hidrico",
        grao="um talhão em um dia",
        descricao="Balanço hídrico diário: clima da fazenda, Kc do estádio, irrigação lançada e "
        "umidade medida, com fração de água disponível, déficit e sinal de irrigação. É a "
        "tabela que o copiloto lê para recomendar.",
        fontes=("erp_talhoes", "clima_diario", "erp_operacoes", "sensores_leitura"),
        colunas={
            "talhao_id": "talhão",
            "dia": "dia do balanço",
            "dap": "dias após o plantio",
            "estadio": "estádio fenológico (dim_fenologia)",
            "kc": "coeficiente de cultura",
            "precipitacao_mm": "chuva do dia na estação da fazenda",
            "eto_mm": "evapotranspiração de referência",
            "etc_mm": "evapotranspiração da cultura = Kc × ETo",
            "irrigacao_mm": "lâmina lançada no ERP para o dia",
            "umidade_media_pct": "umidade média medida pelos sensores",
            "fracao_agua_disponivel": "(θ − PMP) / (CC − PMP), 0 a 1",
            "deficit_mm": "lâmina para voltar à capacidade de campo na camada de 0–30 cm",
            "estresse_hidrico": f"fração de água disponível < {FRACAO_ESTRESSE}",
            "recomenda_irrigar": f"fração < {FRACAO_IRRIGACAO} em talhão irrigado "
            "(trava: sequeiro nunca recomenda)",
            "dias_desde_irrigacao": "dias desde a última lâmina lançada (nulo se nunca irrigou)",
            "irrigado": "talhão possui sistema de irrigação",
            "clima_disponivel": "falso quando o dia da estação está ausente ou em quarentena "
            "(chuva, ETo e ETc ficam nulos; a umidade medida continua válida)",
        },
        sql=f"""
            WITH leit AS (
                SELECT talhao_id, CAST(medido_em AS DATE) AS dia,
                       avg(umidade_solo_pct) AS umidade_media_pct
                FROM sensores_leitura GROUP BY 1, 2
            ), irr AS (
                SELECT talhao_id, data AS dia, sum(quantidade) AS irrigacao_mm
                FROM erp_operacoes
                WHERE tipo_operacao = 'irrigacao' AND unidade = 'mm' GROUP BY 1, 2
            ), dias AS (
                -- calendário observado: todo dia em que alguma fonte falou do campo
                SELECT DISTINCT data AS dia FROM clima_diario
                UNION
                SELECT DISTINCT CAST(medido_em AS DATE) FROM sensores_leitura
            ), base AS (
                SELECT t.talhao_id, d.dia, t.cultura, t.irrigado,
                       datediff('day', t.data_plantio, d.dia) AS dap,
                       c.precipitacao_mm, c.eto_mm, c.data IS NOT NULL AS clima_disponivel,
                       t.capacidade_campo_pct, t.ponto_murcha_pct
                FROM dim_talhao t
                CROSS JOIN dias d
                LEFT JOIN clima_diario c ON c.fazenda_id = t.fazenda_id AND c.data = d.dia
            ), com_kc AS (
                SELECT b.*, f.estadio, f.kc
                FROM base b LEFT JOIN dim_fenologia f
                  ON f.cultura = b.cultura
                 AND ((f.perene AND f.mes = month(b.dia))
                      OR (NOT f.perene AND b.dap BETWEEN f.dia_inicio AND f.dia_fim))
            ), enriquecido AS (
                SELECT k.*, round(k.kc * k.eto_mm, 2) AS etc_mm,
                       coalesce(i.irrigacao_mm, 0) AS irrigacao_mm,
                       round(l.umidade_media_pct, 2) AS umidade_media_pct
                FROM com_kc k
                LEFT JOIN irr i ON i.talhao_id = k.talhao_id AND i.dia = k.dia
                LEFT JOIN leit l ON l.talhao_id = k.talhao_id AND l.dia = k.dia
            ), indicadores AS (
                SELECT *,
                       round(greatest(0, least(1, (umidade_media_pct - ponto_murcha_pct)
                             / (capacidade_campo_pct - ponto_murcha_pct))), 3)
                             AS fracao_agua_disponivel,
                       round(greatest(0, (capacidade_campo_pct - umidade_media_pct) / 100.0
                             * {PROFUNDIDADE_RAIZ_MM}), 1) AS deficit_mm,
                       datediff('day',
                           max(CASE WHEN irrigacao_mm > 0 THEN dia END)
                               OVER (PARTITION BY talhao_id ORDER BY dia
                                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
                           dia) AS dias_desde_irrigacao
                FROM enriquecido
            )
            SELECT talhao_id, dia, dap, estadio, kc, precipitacao_mm, eto_mm, etc_mm, irrigacao_mm,
                   umidade_media_pct, fracao_agua_disponivel, deficit_mm,
                   fracao_agua_disponivel < {FRACAO_ESTRESSE} AS estresse_hidrico,
                   irrigado AND fracao_agua_disponivel < {FRACAO_IRRIGACAO} AS recomenda_irrigar,
                   dias_desde_irrigacao, irrigado, clima_disponivel
            FROM indicadores ORDER BY talhao_id, dia
        """,  # nosec B608 - só constantes numéricas do módulo; nenhuma entrada externa
    )
)

_registrar(
    TabelaGold(
        nome="fato_ndvi_talhao",
        grao="uma cena (drone ou satélite) de um talhão",
        descricao="Índice de vegetação por cena, com confiança pela cobertura de nuvem e "
        "variação em relação à cena anterior da mesma plataforma.",
        fontes=("imagens_ndvi_meta",),
        colunas={
            "imagem_id": "identificador da cena",
            "talhao_id": "talhão",
            "dia": "dia da captura",
            "capturada_em": "instante da captura",
            "plataforma": "drone ou satélite",
            "ndvi_medio": "NDVI médio da área útil",
            "ndvi_min": "menor NDVI",
            "ndvi_max": "maior NDVI",
            "ndvi_desvio": "desvio padrão (desuniformidade)",
            "nuvem_pct": "cobertura de nuvem",
            "confianca": f"alta (< {NUVEM_CONFIANCA_MEDIA_PCT:g} % nuvem), "
            f"media (< {NUVEM_CONFIANCA_BAIXA_PCT:g} %) ou baixa",
            "variacao_vs_anterior": "NDVI médio menos o da cena anterior da mesma plataforma",
            "area_analisada_ha": "área efetivamente analisada",
            "arquivo_bruto": "chave do binário no bronze",
            "com_alerta_dq": "a cena acionou alguma regra de alerta (ex.: nuvem alta)",
        },
        sql=f"""
            SELECT imagem_id, talhao_id, CAST(capturada_em AS DATE) AS dia, capturada_em,
                   plataforma, ndvi_medio, ndvi_min, ndvi_max, ndvi_desvio, nuvem_pct,
                   CASE WHEN nuvem_pct < {NUVEM_CONFIANCA_MEDIA_PCT} THEN 'alta'
                        WHEN nuvem_pct < {NUVEM_CONFIANCA_BAIXA_PCT} THEN 'media'
                        ELSE 'baixa' END AS confianca,
                   round(ndvi_medio - lag(ndvi_medio)
                         OVER (PARTITION BY talhao_id, plataforma ORDER BY capturada_em), 3)
                         AS variacao_vs_anterior,
                   area_analisada_ha, arquivo_bruto, len(_alertas) > 0 AS com_alerta_dq
            FROM imagens_ndvi_meta ORDER BY talhao_id, capturada_em
        """,  # nosec B608 - só constantes numéricas do módulo; nenhuma entrada externa
    )
)

_registrar(
    TabelaGold(
        nome="fato_operacao_custo",
        grao="uma operação de campo",
        descricao="Operações vigentes (última versão do ERP) com custo por hectare. O "
        "responsável já está pseudonimizado desde o silver.",
        fontes=("erp_operacoes", "erp_talhoes"),
        colunas={
            "operacao_id": "lançamento do ERP",
            "talhao_id": "talhão",
            "data": "dia da execução",
            "tipo_operacao": "natureza da operação",
            "insumo": "insumo aplicado",
            "quantidade": "quantidade na unidade canônica",
            "unidade": "unidade canônica (mm, m3, kg, ha, h, un)",
            "custo_brl": "custo total",
            "custo_brl_ha": "custo dividido pela área do talhão",
            "responsavel_id": "operador (pseudônimo)",
            "observacao": "texto livre do campo",
        },
        sql="""
            SELECT o.operacao_id, o.talhao_id, o.data, o.tipo_operacao, o.insumo, o.quantidade,
                   o.unidade, o.custo_brl, round(o.custo_brl / t.area_ha, 2) AS custo_brl_ha,
                   o.responsavel_id, o.observacao
            FROM erp_operacoes o JOIN dim_talhao t USING (talhao_id)
            ORDER BY o.data, o.talhao_id, o.operacao_id
        """,
    )
)

_registrar(
    TabelaGold(
        nome="fato_custo_diario_talhao",
        grao="um talhão, um dia, um tipo de operação",
        descricao="Custo agregado por dia e tipo, para o relatório executivo e o rateio por "
        "hectare.",
        fontes=("erp_operacoes", "erp_talhoes"),
        colunas={
            "talhao_id": "talhão",
            "dia": "dia",
            "tipo_operacao": "tipo",
            "operacoes": "quantidade de lançamentos",
            "custo_brl": "custo total",
            "custo_brl_ha": "custo por hectare",
        },
        sql="""
            SELECT talhao_id, data AS dia, tipo_operacao, count(*) AS operacoes,
                   round(sum(custo_brl), 2) AS custo_brl,
                   round(sum(custo_brl_ha), 2) AS custo_brl_ha
            FROM fato_operacao_custo GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
        """,
    )
)

_registrar(
    TabelaGold(
        nome="fato_fertilidade_talhao",
        grao="um laudo de solo",
        descricao="Resultados analíticos com interpretação: classe de saturação por bases e "
        "necessidade de calagem (método da saturação por bases, PRNT 100 %).",
        fontes=("laudos_solo", "erp_talhoes"),
        colunas={
            "laudo_id": "laudo",
            "talhao_id": "talhão",
            "coletado_em": "data da coleta",
            "emitido_em": "data de emissão",
            "profundidade_cm": "faixa amostrada",
            "ph_cacl2": "pH em CaCl2",
            "materia_organica_g_dm3": "matéria orgânica",
            "fosforo_mg_dm3": "P resina",
            "potassio_mmolc_dm3": "K trocável",
            "calcio_mmolc_dm3": "Ca trocável",
            "magnesio_mmolc_dm3": "Mg trocável",
            "ctc_mmolc_dm3": "CTC a pH 7",
            "saturacao_bases_pct": "V %",
            "classe_saturacao": "baixa (< 50), media (< 70) ou alta",
            "necessidade_calagem_t_ha": "(70 − V) × CTC / 1000 quando V < 70; senão 0",
            "classe_fosforo": "baixo (< 12), medio (< 25) ou alto",
            "arquivo_bruto": "chave do PDF no bronze",
        },
        sql="""
            SELECT l.laudo_id, l.talhao_id, l.coletado_em, l.emitido_em, l.profundidade_cm,
                   l.ph_cacl2, l.materia_organica_g_dm3, l.fosforo_mg_dm3, l.potassio_mmolc_dm3,
                   l.calcio_mmolc_dm3, l.magnesio_mmolc_dm3, l.ctc_mmolc_dm3,
                   l.saturacao_bases_pct,
                   CASE WHEN l.saturacao_bases_pct < 50 THEN 'baixa'
                        WHEN l.saturacao_bases_pct < 70 THEN 'media'
                        ELSE 'alta' END AS classe_saturacao,
                   round(CASE WHEN l.saturacao_bases_pct < 70
                              THEN (70 - l.saturacao_bases_pct) * l.ctc_mmolc_dm3 / 1000.0
                              ELSE 0 END, 2) AS necessidade_calagem_t_ha,
                   CASE WHEN l.fosforo_mg_dm3 < 12 THEN 'baixo'
                        WHEN l.fosforo_mg_dm3 < 25 THEN 'medio' ELSE 'alto' END AS classe_fosforo,
                   l.arquivo_bruto
            FROM laudos_solo l JOIN dim_talhao t USING (talhao_id)
            ORDER BY l.talhao_id, l.coletado_em, l.profundidade_cm
        """,
    )
)

_registrar(
    TabelaGold(
        nome="benchmark_regional",
        grao="uma cultura",
        descricao="Agregado por cultura para o produto de dados externo (degrau 3 do modelo de "
        "negócio). Não contém identificador de talhão, fazenda, pessoa nem coordenada; só é "
        f"publicável quando o grupo tem pelo menos {K_ANONIMATO} talhões.",
        fontes=("erp_talhoes", "fato_balanco_hidrico", "fato_ndvi_talhao", "fato_operacao_custo"),
        colunas={
            "cultura": "cultura",
            "n_talhoes": "talhões no grupo",
            "area_total_ha": "área agregada",
            "umidade_media_pct": "umidade média do solo no período",
            "fad_media": "fração de água disponível média",
            "dias_estresse_pct": "percentual de dias-talhão em estresse hídrico",
            "irrigacao_mm_media_talhao": "lâmina total média por talhão",
            "chuva_mm_media": "chuva média acumulada nas fazendas do grupo",
            "ndvi_medio": "NDVI médio das cenas com confiança alta ou média",
            "custo_brl_ha": "custo total do período por hectare",
            "publicavel": f"n_talhoes ≥ {K_ANONIMATO} (k-anonimato)",
        },
        sql=f"""
            WITH talhoes AS (
                SELECT cultura, count(*) AS n_talhoes, sum(area_ha) AS area_total_ha
                FROM dim_talhao GROUP BY 1
            ), balanco AS (
                SELECT t.cultura,
                       avg(b.umidade_media_pct) AS umidade_media_pct,
                       avg(b.fracao_agua_disponivel) AS fad_media,
                       100.0 * avg(CASE WHEN b.estresse_hidrico THEN 1 ELSE 0 END)
                             AS dias_estresse_pct,
                       sum(b.irrigacao_mm) / count(DISTINCT t.talhao_id)
                             AS irrigacao_mm_media_talhao,
                       sum(b.precipitacao_mm) / count(DISTINCT t.talhao_id) AS chuva_mm_media
                FROM fato_balanco_hidrico b JOIN dim_talhao t USING (talhao_id) GROUP BY 1
            ), ndvi AS (
                SELECT t.cultura, avg(n.ndvi_medio) AS ndvi_medio
                FROM fato_ndvi_talhao n JOIN dim_talhao t USING (talhao_id)
                WHERE n.confianca <> 'baixa' GROUP BY 1
            ), custo AS (
                SELECT t.cultura, sum(o.custo_brl) AS custo_brl
                FROM fato_operacao_custo o JOIN dim_talhao t USING (talhao_id) GROUP BY 1
            )
            SELECT tl.cultura, tl.n_talhoes, round(tl.area_total_ha, 1) AS area_total_ha,
                   round(b.umidade_media_pct, 2) AS umidade_media_pct,
                   round(b.fad_media, 3) AS fad_media,
                   round(b.dias_estresse_pct, 1) AS dias_estresse_pct,
                   round(b.irrigacao_mm_media_talhao, 1) AS irrigacao_mm_media_talhao,
                   round(b.chuva_mm_media, 1) AS chuva_mm_media,
                   round(n.ndvi_medio, 3) AS ndvi_medio,
                   round(coalesce(c.custo_brl, 0) / tl.area_total_ha, 2) AS custo_brl_ha,
                   tl.n_talhoes >= {K_ANONIMATO} AS publicavel
            FROM talhoes tl
            LEFT JOIN balanco b USING (cultura)
            LEFT JOIN ndvi n USING (cultura)
            LEFT JOIN custo c USING (cultura)
            ORDER BY tl.cultura
        """,  # nosec B608 - só constantes numéricas do módulo; nenhuma entrada externa
    )
)

_registrar(
    TabelaGold(
        nome="fato_alerta_qualidade",
        grao="um alerta de qualidade (por regra ou por entidade)",
        descricao="Alertas do último relatório de qualidade, prontos para o dashboard e para o "
        "Context Pack do copiloto: regras de alerta acionadas por fonte e alertas de frescor "
        "por entidade (sensor atrasado, lacuna na série).",
        fontes=(),
        colunas={
            "fonte": "fonte de dados",
            "regra": "id da regra do contrato",
            "tipo": "registro (regra de alerta acionada) ou tabela (frescor)",
            "subtipo": "para frescor: atrasado, lacuna ou sem_dados",
            "entidade": "sensor, estação ou talhão afetado (nulo para o total da fonte)",
            "quantidade": "registros afetados ou ocorrências",
            "detalhe": "JSON com os campos específicos do alerta",
        },
        sql="SELECT * FROM fato_alerta_qualidade_base ORDER BY fonte, regra, entidade",
    )
)


# ---------------------------------------------------------------------------
# Construção
# ---------------------------------------------------------------------------


def _alertas_do_relatorio(relatorio: dict[str, Any] | None) -> pl.DataFrame:
    schema = {
        "fonte": pl.Utf8,
        "regra": pl.Utf8,
        "tipo": pl.Utf8,
        "subtipo": pl.Utf8,
        "entidade": pl.Utf8,
        "quantidade": pl.Int64,
        "detalhe": pl.Utf8,
    }
    if not relatorio:
        return pl.DataFrame(schema=schema)
    linhas: list[dict[str, Any]] = []
    for fonte, dados in relatorio.get("fontes", {}).items():
        qualidade = dados.get("qualidade", {})
        for regra in qualidade.get("regras", []):
            if (
                regra["severidade"] == "alerta"
                and regra["regra"] != "freshness"
                and regra["violacoes"]
            ):
                linhas.append(
                    {
                        "fonte": fonte,
                        "regra": regra["id"],
                        "tipo": "registro",
                        "subtipo": regra["regra"],
                        "entidade": None,
                        "quantidade": int(regra["violacoes"]),
                        "detalhe": json.dumps(regra["parametros"], ensure_ascii=False),
                    }
                )
        for alerta in qualidade.get("alertas_tabela", []):
            detalhe = {k: v for k, v in alerta.items() if k not in {"regra", "tipo", "entidade"}}
            linhas.append(
                {
                    "fonte": fonte,
                    "regra": alerta["regra"],
                    "tipo": "tabela",
                    "subtipo": alerta["tipo"],
                    "entidade": alerta.get("entidade"),
                    "quantidade": int(alerta.get("lacunas", 1)),
                    "detalhe": json.dumps(detalhe, ensure_ascii=False, default=str),
                }
            )
    return pl.from_dicts(linhas, schema=schema) if linhas else pl.DataFrame(schema=schema)


def _parquet(df: pl.DataFrame) -> bytes:
    buffer = io.BytesIO()
    df.write_parquet(buffer, compression="zstd")
    return buffer.getvalue()


def abrir_conexao(
    armazenamento: Armazenamento,
    *,
    silver: bool = True,
    gold: bool = False,
    relatorio_qualidade: dict[str, Any] | None = None,
) -> duckdb.DuckDBPyConnection:
    """DuckDB em memória com as tabelas do lake registradas como views.

    Lê os Parquet pelo ``Armazenamento`` (funciona em MinIO e filesystem) e
    registra cada tabela pelo nome. ``dim_fenologia_base`` e
    ``fato_alerta_qualidade_base`` vêm do conhecimento agronômico e do último
    relatório de qualidade, não do lake.
    """
    con = duckdb.connect()
    registradas: list[str] = []
    if silver:
        for contrato in contratos_ativos().values():
            tabela = contrato.destino.silver_tabela
            df = ler_tabela(armazenamento, Camada.SILVER, tabela)
            if df.is_empty():
                # Tabela ainda sem registros (dia 1 de uma implantação, tudo em
                # quarentena): registra vazia com o schema do contrato para que o
                # gold materialize vazio em vez de falhar.
                df = pl.DataFrame(schema=esquema_silver(contrato))
            con.register(tabela, df.to_arrow())
            registradas.append(tabela)
            # A quarentena da fonte também fica consultável (`quarentena_<fonte>`): é o
            # que o runbook INC-04 usa para agrupar reprovações por motivo.
            reprovados = ler_tabela(armazenamento, Camada.QUARENTENA, contrato.fonte)
            if not reprovados.is_empty():
                con.register(f"quarentena_{contrato.fonte}", reprovados.to_arrow())
                registradas.append(f"quarentena_{contrato.fonte}")
    if gold:
        for nome in TABELAS:
            df = ler_tabela(armazenamento, Camada.GOLD, nome)
            # Uma tabela gold materializada vazia (ex.: nenhuma operação aprovada na
            # janela) tem schema e precisa existir na conexão: o Context Pack e o
            # dashboard consultam todas as tabelas, e "zero linhas" é uma resposta
            # válida — "tabela inexistente" não é.
            if df.width > 0:
                con.register(nome, df.to_arrow())
                registradas.append(nome)
    fenologia = pl.from_dicts(
        tabela_fenologia(),
        schema={
            "cultura": pl.Utf8,
            "estadio": pl.Utf8,
            "dia_inicio": pl.Int64,
            "dia_fim": pl.Int64,
            "mes": pl.Int64,
            "kc": pl.Float64,
            "perene": pl.Boolean,
        },
    )
    con.register("dim_fenologia_base", fenologia.to_arrow())
    con.register(
        "fato_alerta_qualidade_base", _alertas_do_relatorio(relatorio_qualidade).to_arrow()
    )
    con.execute("SET TimeZone = 'UTC'")
    return con


def construir_gold(
    config: Config | None = None, armazenamento: Armazenamento | None = None
) -> dict[str, Any]:
    """Materializa todas as tabelas do gold, na ordem de :data:`TABELAS`."""
    cfg = config or carregar_config()
    lake = armazenamento or criar_armazenamento(cfg)
    logger = obter_logger("gold")
    inicio = time.perf_counter()

    relatorio_path = cfg.dir_outputs / "dq_report.json"
    relatorio = (
        json.loads(relatorio_path.read_text(encoding="utf-8")) if relatorio_path.exists() else None
    )
    con = abrir_conexao(lake, silver=True, gold=False, relatorio_qualidade=relatorio)

    tabelas_silver = {c.destino.silver_tabela for c in contratos_ativos().values()}
    disponiveis = {
        t
        for t in tabelas_silver
        if con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [t]
        ).fetchone()[0]
        > 0
    }

    resumo: dict[str, Any] = {"tabelas": {}, "puladas": []}
    for nome, tabela in TABELAS.items():
        faltando = [f for f in tabela.fontes if f in tabelas_silver and f not in disponiveis]
        if faltando:
            resumo["puladas"].append({"tabela": nome, "motivo": f"silver sem {faltando}"})
            logger.warning("gold %s pulada: silver sem %s", nome, faltando)
            limpar_prefixo(lake, Camada.GOLD, f"{nome}/")
            continue
        # to_arrow_table preserva o schema mesmo sem linhas — .arrow() devolve um
        # leitor de lotes que, vazio, não tem schema para o Polars reconstruir.
        df = pl.from_arrow(para_arrow(con.execute(tabela.sql)))
        if not isinstance(df, pl.DataFrame):  # verificação explícita (não some com -O)
            raise TypeError(f"gold.{nome}: esperado DataFrame, veio {type(df).__name__}")
        con.register(nome, df.to_arrow())  # tabelas seguintes podem consumir esta
        limpar_prefixo(lake, Camada.GOLD, f"{nome}/")
        chave = f"{nome}/{nome}.parquet"
        objeto = lake.gravar_bytes(Camada.GOLD, chave, _parquet(df), content_type=TIPO_PARQUET)
        resumo["tabelas"][nome] = {
            "registros": df.height,
            "colunas": df.width,
            "bytes": objeto.tamanho_bytes,
            "chave": chave,
            "grao": tabela.grao,
        }
        logger.info(
            "%-28s %6d linhas · %2d colunas · %6.1f KB",
            nome,
            df.height,
            df.width,
            objeto.tamanho_bytes / 1024,
        )

    con.close()
    resumo["gerado_em"] = datetime.now(UTC).isoformat(timespec="seconds")
    resumo["duracao_s"] = round(time.perf_counter() - inicio, 2)
    cfg.dir_outputs.mkdir(parents=True, exist_ok=True)
    (cfg.dir_outputs / "gold.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    registrar_evento(
        Evento(
            etapa="gold",
            acao="concluida",
            camada=Camada.GOLD.value,
            status="alerta" if resumo["puladas"] else "ok",
            mensagem=f"{len(resumo['tabelas'])} tabelas materializadas",
            metricas={
                "tabelas": len(resumo["tabelas"]),
                "puladas": len(resumo["puladas"]),
                "duracao_s": resumo["duracao_s"],
            },
        ),
        cfg,
    )
    return resumo
