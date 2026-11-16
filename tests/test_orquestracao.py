"""Orquestração ponta a ponta e backfill por intervalo, em filesystem."""

from __future__ import annotations

import io
import json
from datetime import date, timedelta

import polars as pl
import pytest

from agrosmart.config import Camada, Config
from agrosmart.orquestracao import executar
from agrosmart.processamento.silver import ler_tabela
from agrosmart.storage import criar_armazenamento
from tests.conftest import fabricar_config

DIAS = 3


@pytest.fixture(scope="module")
def execucao(tmp_path_factory: pytest.TempPathFactory) -> tuple[Config, dict]:
    cfg = fabricar_config(tmp_path_factory.mktemp("run"), dias_historico=DIAS)
    return cfg, executar(cfg)


def test_run_completo_passa_por_todas_as_etapas(execucao: tuple[Config, dict]) -> None:
    cfg, resumo = execucao
    assert resumo["sucesso"] and resumo["modo"] == "completo"
    nomes = [e["nome"] for e in resumo["etapas"]]
    assert nomes == ["simular", "ingerir", "silver", "gold", "catalogo", "copiloto"]
    assert all(e["status"] == "ok" for e in resumo["etapas"])
    assert (cfg.dir_outputs / "run.json").exists()
    assert (cfg.dir_outputs / "dq_report.json").exists()
    assert (cfg.dir_outputs / "avaliacao_ia.json").exists()
    assert list((cfg.dir_outputs / "relatorios" / "irrigacao").glob("*.md"))


def test_resumo_das_etapas_e_informativo(execucao: tuple[Config, dict]) -> None:
    _, resumo = execucao
    por_nome = {e["nome"]: e for e in resumo["etapas"]}
    assert por_nome["ingerir"]["resumo"]["rejeitados"] == 0
    assert por_nome["silver"]["resumo"]["reconciliacao_ok"] is True
    localizados, total = por_nome["silver"]["resumo"]["defeitos_localizados"].split("/")
    assert localizados == total and int(total) > 0
    assert por_nome["gold"]["resumo"]["tabelas"] == 14
    assert por_nome["copiloto"]["resumo"]["execucoes"] == 18
    assert por_nome["copiloto"]["resumo"]["fallbacks"] == 0


def test_backfill_de_um_dia_reescreve_so_o_intervalo(execucao: tuple[Config, dict]) -> None:
    cfg, _ = execucao
    lake = criar_armazenamento(cfg)
    antes = ler_tabela(lake, Camada.SILVER, "sensores_leitura")
    inicio, fim = cfg.janela_historico
    alvo = inicio + timedelta(days=1)
    chaves_antes = {o.chave for o in lake.listar(Camada.SILVER, "sensores_leitura/")}

    resumo = executar(cfg, backfill=(alvo, alvo))

    assert resumo["sucesso"] and resumo["modo"] == "backfill"
    assert resumo["intervalo"] == [alvo.isoformat(), alvo.isoformat()]
    assert [e["status"] for e in resumo["etapas"]][0] == "pulada"  # simular não roda no backfill
    depois = ler_tabela(lake, Camada.SILVER, "sensores_leitura")
    assert depois.height == antes.height
    assert {o.chave for o in lake.listar(Camada.SILVER, "sensores_leitura/")} == chaves_antes
    # O relatório do backfill não substitui o da última execução completa.
    completo = json.loads((cfg.dir_outputs / "dq_report.json").read_text(encoding="utf-8"))
    assert completo["intervalo"] is None
    nome = f"dq_report_backfill_{alvo:%Y%m%d}_{alvo:%Y%m%d}.json"
    relatorio = json.loads((cfg.dir_outputs / nome).read_text(encoding="utf-8"))
    assert relatorio["intervalo"] == [alvo.isoformat(), alvo.isoformat()]
    # fonte de eventos: só o intervalo foi lido; fonte de versões: tudo
    assert relatorio["fontes"]["sensores"]["lotes"] == 96
    assert relatorio["fontes"]["erp_talhoes"]["lotes"] == DIAS + 1
    assert relatorio["fontes"]["sensores"]["reconciliacao_ok"]


def test_backfill_recompoe_registros_apagados_da_particao(execucao: tuple[Config, dict]) -> None:
    cfg, _ = execucao
    lake = criar_armazenamento(cfg)
    inicio, _ = cfg.janela_historico
    alvo = inicio + timedelta(days=2)
    objetos = lake.listar(Camada.SILVER, "sensores_leitura/")
    chave = next(o.chave for o in objetos if f"dia={alvo:%d}" in o.chave)
    esperado = pl.read_parquet(io.BytesIO(lake.ler_bytes(Camada.SILVER, chave))).height
    total_antes = ler_tabela(lake, Camada.SILVER, "sensores_leitura").height
    lake.remover(Camada.SILVER, chave)
    assert ler_tabela(lake, Camada.SILVER, "sensores_leitura").height == total_antes - esperado

    # Só os lotes do dia: recompõe o que veio deles. Um evento do dia entregue em lote
    # do dia seguinte (chegada atrasada) ainda falta — é o limite documentado no ADR 0010.
    executar(cfg, backfill=(alvo, alvo), ia=False)
    parcial = pl.read_parquet(io.BytesIO(lake.ler_bytes(Camada.SILVER, chave)))
    assert 0 < parcial.height <= esperado

    # Intervalo com o dia seguinte: a partição volta a ser idêntica em tamanho, e a
    # mesclagem não duplica o que a primeira passada já tinha recomposto.
    executar(cfg, backfill=(alvo, alvo + timedelta(days=1)), ia=False)
    recomposto = pl.read_parquet(io.BytesIO(lake.ler_bytes(Camada.SILVER, chave)))
    assert recomposto.height == esperado
    assert ler_tabela(lake, Camada.SILVER, "sensores_leitura").height == total_antes


def test_run_sem_simular_e_sem_ia(tmp_path) -> None:
    cfg = fabricar_config(tmp_path, dias_historico=2)
    resumo = executar(cfg, simular_fontes=False, ia=False)
    por_nome = {e["nome"]: e for e in resumo["etapas"]}
    assert por_nome["simular"]["status"] == "pulada"
    assert por_nome["copiloto"]["status"] == "pulada"
    assert por_nome["ingerir"]["status"] == "ok" and por_nome["ingerir"]["resumo"]["lotes"] == 0


def test_run_para_na_primeira_falha(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = fabricar_config(tmp_path, dias_historico=2)

    def _quebra(*_args, **_kwargs):
        raise RuntimeError("landing indisponível")

    monkeypatch.setattr("agrosmart.simuladores.simular", _quebra)
    resumo = executar(cfg)
    assert not resumo["sucesso"]
    assert resumo["etapas"][0]["status"] == "erro" and "landing" in resumo["etapas"][0]["erro"]
    assert len(resumo["etapas"]) == 1


def test_backfill_fora_da_janela_nao_le_lote_nenhum(execucao: tuple[Config, dict]) -> None:
    cfg, _ = execucao
    fora = date(2020, 1, 1)
    resumo = executar(cfg, backfill=(fora, fora), ia=False)
    assert resumo["sucesso"]
    nome = f"dq_report_backfill_{fora:%Y%m%d}_{fora:%Y%m%d}.json"
    relatorio = json.loads((cfg.dir_outputs / nome).read_text(encoding="utf-8"))
    assert relatorio["fontes"]["sensores"]["lotes"] == 0
