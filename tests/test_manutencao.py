"""Compactação e retenção sobre um lake próprio (o teste altera o lake)."""

from __future__ import annotations

import pytest

from agrosmart.config import Camada, Config
from agrosmart.ingestao import ingerir
from agrosmart.processamento.gold import construir_gold
from agrosmart.processamento.manutencao import aplicar_retencao, compactar, manter
from agrosmart.processamento.silver import construir_silver, ler_tabela
from agrosmart.simuladores import simular
from agrosmart.storage import Armazenamento, criar_armazenamento
from tests.conftest import fabricar_config

DIAS = 3


@pytest.fixture(scope="module")
def lake_mutavel(tmp_path_factory: pytest.TempPathFactory) -> tuple[Config, Armazenamento]:
    cfg = fabricar_config(tmp_path_factory.mktemp("manut"), dias_historico=DIAS)
    simular(cfg)
    ingerir(cfg, exportar_exemplos=False)
    lake = criar_armazenamento(cfg)
    construir_silver(cfg, lake)
    return cfg, lake


def test_compactacao_reduz_a_um_arquivo_por_mes_sem_perder_registros(
    lake_mutavel: tuple[Config, Armazenamento],
) -> None:
    cfg, lake = lake_mutavel
    antes = ler_tabela(lake, Camada.SILVER, "sensores_leitura")
    arquivos_antes = lake.contar(Camada.SILVER, "sensores_leitura/")
    assert arquivos_antes == DIAS + 1  # dias de agosto + a madrugada de 1º de setembro

    resultados = compactar(lake, tabelas=["sensores_leitura"])

    # Só a partição de agosto tinha mais de um arquivo; setembro fica como está.
    assert len(resultados) == 1
    assert resultados[0].arquivos_antes == DIAS and resultados[0].arquivos_depois == 1
    assert lake.contar(Camada.SILVER, "sensores_leitura/") == 2
    depois = ler_tabela(lake, Camada.SILVER, "sensores_leitura")
    assert depois.height == antes.height
    assert set(depois.columns) == set(antes.columns)
    chaves = [o.chave for o in lake.listar(Camada.SILVER, "sensores_leitura/")]
    assert any(c.endswith("-202608-compactado.parquet") and "/mes=08/" in c for c in chaves)


def test_compactar_de_novo_nao_faz_nada(lake_mutavel: tuple[Config, Armazenamento]) -> None:
    _, lake = lake_mutavel
    assert compactar(lake, tabelas=["sensores_leitura"]) == []


def test_gold_continua_funcionando_apos_compactacao(
    lake_mutavel: tuple[Config, Armazenamento],
) -> None:
    cfg, lake = lake_mutavel
    resumo = construir_gold(cfg, lake)
    assert resumo["puladas"] == []
    assert resumo["tabelas"]["fato_leitura_diaria_talhao"]["registros"] == 6 * (DIAS + 1)


def test_retencao_em_simulacao_nao_remove_nada_dentro_do_prazo(
    lake_mutavel: tuple[Config, Armazenamento],
) -> None:
    cfg, lake = lake_mutavel
    resultado = aplicar_retencao(lake, cfg, aplicar=False)
    assert resultado.avaliados > 0
    assert resultado.expirados == []
    assert resultado.removidos == 0


def test_retencao_identifica_e_remove_particoes_vencidas(
    lake_mutavel: tuple[Config, Armazenamento],
) -> None:
    cfg, lake = lake_mutavel
    # bronze de sensores retém 90 dias; quarentena, 30. Plantamos objetos antigos.
    vencidos = [
        (Camada.BRONZE, "sensores/ano=2025/mes=01/dia=01/leituras_antigas.jsonl"),
        (Camada.BRONZE, "_manifests/sensores/ano=2025/mes=01/dia=01/abc123.json"),
        (Camada.QUARENTENA, "sensores/ano=2026/mes=06/dia=01/sensores-20260601.parquet"),
    ]
    for camada, chave in vencidos:
        lake.gravar_bytes(camada, chave, b"x")
    lake.gravar_bytes(Camada.BRONZE, "laudos_solo/ano=2020/mes=01/dia=01/antigo.pdf", b"x")

    simulado = aplicar_retencao(lake, cfg, aplicar=False)
    chaves_expiradas = {i.chave for i in simulado.expirados}
    assert chaves_expiradas == {c for _, c in vencidos}  # laudos: retenção permanente
    assert simulado.por_camada == {"bronze": 2, "quarentena": 1}
    for camada, chave in vencidos:
        assert lake.existe(camada, chave)

    aplicado = aplicar_retencao(lake, cfg, aplicar=True)
    assert aplicado.removidos == 3
    for camada, chave in vencidos:
        assert not lake.existe(camada, chave)
    assert lake.existe(Camada.BRONZE, "laudos_solo/ano=2020/mes=01/dia=01/antigo.pdf")


def test_manter_consolida_compactacao_e_retencao(
    lake_mutavel: tuple[Config, Armazenamento],
) -> None:
    cfg, lake = lake_mutavel
    saida = manter(cfg, lake, aplicar_retencao_=False)
    assert "compactacao" in saida and "retencao" in saida
    assert saida["retencao"]["aplicado"] is False
