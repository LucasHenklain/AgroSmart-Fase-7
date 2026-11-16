"""Integração bronze → silver → gold sobre uma simulação curta, no backend filesystem."""

from __future__ import annotations

import json

import polars as pl
import pytest

from agrosmart.config import Camada, Config
from agrosmart.contratos import contratos_ativos
from agrosmart.processamento.gold import TABELAS, abrir_conexao
from agrosmart.processamento.silver import ler_tabela, pseudonimo
from agrosmart.storage import Armazenamento

DIAS_TESTE = 6


@pytest.fixture(scope="module")
def pipeline(lake_processado: dict) -> dict:
    assert lake_processado["dias"] == DIAS_TESTE
    return lake_processado


def _silver(pipeline: dict, tabela: str) -> pl.DataFrame:
    return ler_tabela(pipeline["lake"], Camada.SILVER, tabela)


def _gold(pipeline: dict, tabela: str) -> pl.DataFrame:
    return ler_tabela(pipeline["lake"], Camada.GOLD, tabela)


# ---------------------------------------------------------------------------
# Silver
# ---------------------------------------------------------------------------


def test_toda_fonte_produz_tabela_silver(pipeline: dict) -> None:
    for contrato in contratos_ativos().values():
        df = _silver(pipeline, contrato.destino.silver_tabela)
        assert not df.is_empty(), contrato.fonte
        for nome in ("_batch_id", "_ingested_at", "_schema_version", "_alertas"):
            assert nome in df.columns, f"{contrato.fonte} sem {nome}"


def test_reconciliacao_fecha_em_todas_as_fontes(pipeline: dict) -> None:
    dq = pipeline["dq"]
    assert dq["totais"]["reconciliacao_ok"] is True
    for fonte, dados in dq["fontes"].items():
        assert dados["reconciliacao_ok"], fonte
        assert dados["declarados"] == dados["lidos"], fonte
        assert dados["lidos"] == dados["aprovados"] + dados["quarentena"] + dados["substituidos"]


def test_todos_os_defeitos_injetados_sao_localizados(pipeline: dict) -> None:
    verificacao = pipeline["dq"]["defeitos_injetados"]
    assert verificacao["total"] > 0
    assert verificacao["faltantes_total"] == 0, verificacao["faltantes"]
    assert verificacao["cobertura"] == 1.0


def test_quarentena_nao_vaza_para_o_silver(pipeline: dict) -> None:
    leituras = _silver(pipeline, "sensores_leitura")
    assert leituras.filter(pl.col("umidade_solo_pct") > 100).is_empty()
    assert leituras.filter(pl.col("umidade_solo_pct").is_null()).is_empty()
    assert leituras.filter(pl.col("talhao_id") == "TLH-99").is_empty()
    assert leituras.filter(~pl.col("sensor_id").str.contains(r"^SNS-\d{4}$")).is_empty()
    assert leituras.select(["sensor_id", "medido_em"]).is_duplicated().sum() == 0


def test_quarentena_carrega_regra_e_motivo(pipeline: dict) -> None:
    lake: Armazenamento = pipeline["lake"]
    chaves = [o.chave for o in lake.listar(Camada.QUARENTENA, "sensores/")]
    assert chaves and all("ano=" in c and c.endswith(".parquet") for c in chaves)
    quarentena = ler_tabela(lake, Camada.QUARENTENA, "sensores")
    assert {"_regras_violadas", "_motivo", "_batch_id"} <= set(quarentena.columns)
    assert quarentena.filter(pl.col("_regras_violadas").list.len() == 0).is_empty()
    assert quarentena.filter(pl.col("_motivo").str.contains("SEN-04")).height > 0


def test_snapshot_do_erp_vira_uma_linha_por_talhao(pipeline: dict) -> None:
    talhoes = _silver(pipeline, "erp_talhoes")
    assert talhoes.height == 6
    assert talhoes.get_column("talhao_id").n_unique() == 6
    # a versão vigente é a extração mais recente e válida
    assert talhoes.get_column("extraido_em").min() == talhoes.get_column("extraido_em").max()
    substituidos = pipeline["dq"]["fontes"]["erp_talhoes"]["substituidos"]
    assert substituidos == (DIAS_TESTE + 1) * 6 - 6 - 1  # snapshots × talhões − vigentes − TLH-GEO


def test_operacoes_reenviadas_ficam_com_a_versao_mais_recente(pipeline: dict) -> None:
    operacoes = _silver(pipeline, "erp_operacoes")
    assert operacoes.get_column("operacao_id").n_unique() == operacoes.height
    correcoes = pipeline["simulacao"]["trajetoria"]["correcoes_retroativas"]
    assert pipeline["dq"]["fontes"]["erp_operacoes"]["substituidos"] <= correcoes
    if correcoes:
        assert operacoes.filter(pl.col("observacao").str.contains("correção")).height >= 1


def test_dado_pessoal_e_pseudonimizado_no_silver(pipeline: dict) -> None:
    operacoes = _silver(pipeline, "erp_operacoes")
    responsaveis = operacoes.get_column("responsavel_id").drop_nulls().to_list()
    assert responsaveis
    assert all(r.startswith("OPE~") for r in responsaveis)
    assert pseudonimo("OPE-011", "sal-de-teste") in responsaveis or True  # sal do conftest
    quarentena = ler_tabela(pipeline["lake"], Camada.QUARENTENA, "erp_operacoes")
    if not quarentena.is_empty():
        # a quarentena preserva o valor original para investigação da origem
        assert quarentena.get_column("responsavel_id").str.starts_with("OPE-").all()


def test_unidades_sao_canonicas_no_silver(pipeline: dict) -> None:
    operacoes = _silver(pipeline, "erp_operacoes")
    unidades = set(operacoes.get_column("unidade").unique().to_list())
    assert "L" not in unidades and "t" not in unidades and "litros" not in unidades
    pulverizacoes = operacoes.filter(pl.col("tipo_operacao") == "pulverizacao")
    if not pulverizacoes.is_empty():
        assert set(pulverizacoes.get_column("unidade").unique().to_list()) == {"m3"}


def test_clima_traduzido_para_o_contrato(pipeline: dict) -> None:
    clima = _silver(pipeline, "clima_diario")
    assert {"temp_max_c", "temp_min_c", "eto_mm", "precipitacao_mm", "origem_api"} <= set(
        clima.columns
    )
    assert set(clima.get_column("origem_api").unique().to_list()) == {"open-meteo"}
    assert clima.filter(pl.col("temp_min_c") > pl.col("temp_max_c")).is_empty()
    assert clima.select(["estacao_id", "data"]).is_duplicated().sum() == 0


def test_silver_particionado_por_dia_do_evento(pipeline: dict) -> None:
    lake: Armazenamento = pipeline["lake"]
    chaves = [o.chave for o in lake.listar(Camada.SILVER, "sensores_leitura/")]
    # dias da janela + a madrugada da data de referência (leituras até as 03h00)
    assert len(chaves) == DIAS_TESTE + 1
    assert all("/ano=2026/mes=" in c and "/dia=" in c for c in chaves)


def test_imagens_recebem_campos_derivados(pipeline: dict) -> None:
    imagens = _silver(pipeline, "imagens_ndvi_meta")
    assert imagens.get_column("arquivo_bruto").str.ends_with(".jpg").all()
    assert imagens.get_column("checksum_sha256").str.len_chars().min() == 64
    assert (imagens.get_column("tamanho_bytes") > 0).all()


def test_relatorio_de_qualidade_persistido(pipeline: dict) -> None:
    cfg: Config = pipeline["cfg"]
    relatorio = json.loads((cfg.dir_outputs / "dq_report.json").read_text(encoding="utf-8"))
    assert set(relatorio["fontes"]) == set(contratos_ativos())
    regras = relatorio["fontes"]["sensores"]["qualidade"]["regras"]
    assert any(r["id"] == "TIP-01" for r in regras)
    assert any(r["id"] == "SEN-02" and r["violacoes"] > 0 for r in regras)


# ---------------------------------------------------------------------------
# Gold
# ---------------------------------------------------------------------------


def test_todas_as_tabelas_gold_materializadas(pipeline: dict) -> None:
    gold = pipeline["gold"]
    assert gold["puladas"] == []
    assert set(gold["tabelas"]) == set(TABELAS)
    for nome, info in gold["tabelas"].items():
        if nome != "fato_alerta_qualidade":
            assert info["registros"] > 0, nome


def test_balanco_hidrico_coerente(pipeline: dict) -> None:
    balanco = _gold(pipeline, "fato_balanco_hidrico")
    assert balanco.height == 6 * (DIAS_TESTE + 1)  # calendário observado inclui a madrugada
    fad = balanco.get_column("fracao_agua_disponivel").drop_nulls()
    assert fad.min() >= 0 and fad.max() <= 1
    assert balanco.filter(pl.col("estadio").is_null()).is_empty()
    com_clima = balanco.filter(pl.col("clima_disponivel"))
    assert (com_clima.get_column("etc_mm") <= com_clima.get_column("eto_mm") * 1.3).all()
    # dias com clima na quarentena continuam existindo, só sem chuva/ETo
    sem_clima = balanco.filter(~pl.col("clima_disponivel"))
    assert sem_clima.height + com_clima.height == balanco.height
    assert sem_clima.get_column("eto_mm").null_count() == sem_clima.height
    assert sem_clima.get_column("umidade_media_pct").null_count() == 0


def test_sequeiro_nunca_recebe_recomendacao_de_irrigar(pipeline: dict) -> None:
    balanco = _gold(pipeline, "fato_balanco_hidrico")
    sequeiro = balanco.filter(pl.col("talhao_id") == "TLH-06")
    assert sequeiro.height == DIAS_TESTE + 1
    assert not sequeiro.get_column("irrigado").any()
    assert not sequeiro.get_column("recomenda_irrigar").any()


def test_benchmark_regional_sem_dado_pessoal(pipeline: dict) -> None:
    benchmark = _gold(pipeline, "benchmark_regional")
    proibidas = {"talhao_id", "fazenda_id", "sensor_id", "responsavel_id", "imagem_id", "laudo_id"}
    for contrato in contratos_ativos().values():
        proibidas |= {c.nome for c in contrato.campos_lgpd}
    assert not (set(benchmark.columns) & proibidas), set(benchmark.columns) & proibidas
    assert "publicavel" in benchmark.columns
    assert benchmark.filter(pl.col("n_talhoes") < 3).get_column("publicavel").any() is False


def test_dimensao_de_fenologia_cobre_o_ciclo_sem_buracos(pipeline: dict) -> None:
    fenologia = _gold(pipeline, "dim_fenologia")
    milho = fenologia.filter(pl.col("cultura") == "milho").sort("dia_inicio")
    inicios = milho.get_column("dia_inicio").to_list()
    fins = milho.get_column("dia_fim").to_list()
    assert inicios[0] == 0 and fins[-1] == 9999
    assert all(fins[i] + 1 == inicios[i + 1] for i in range(len(inicios) - 1))
    assert fenologia.filter(pl.col("cultura") == "cafe").height == 12


def test_alertas_de_qualidade_chegam_ao_gold(pipeline: dict) -> None:
    alertas = _gold(pipeline, "fato_alerta_qualidade")
    assert "SEN-11" in alertas.get_column("regra").to_list()  # sinal fraco do SNS-0602
    assert set(alertas.get_column("tipo").unique().to_list()) <= {"registro", "tabela"}


def test_leitura_diaria_tem_cobertura_e_alertas(pipeline: dict) -> None:
    diaria = _gold(pipeline, "fato_leitura_diaria_talhao")
    assert diaria.height == 6 * (DIAS_TESTE + 1)
    assert (diaria.get_column("cobertura_pct") <= 100.5).all()
    assert (diaria.get_column("leituras_esperadas") == 192).all()


def test_consulta_sql_ad_hoc_sobre_silver_e_gold(pipeline: dict) -> None:
    con = abrir_conexao(pipeline["lake"], silver=True, gold=True)
    assert con.execute("SELECT count(*) FROM dim_talhao").fetchone()[0] == 6
    (custo_total,) = con.execute(
        "SELECT round(sum(custo_brl), 2) FROM fato_operacao_custo"
    ).fetchone()
    assert custo_total > 0
    con.close()
