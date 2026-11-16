from __future__ import annotations

import json

from agrosmart.catalogo import construir_catalogo, gerar_dicionario
from agrosmart.contratos import contratos_ativos
from agrosmart.processamento.gold import TABELAS


def test_catalogo_lista_toda_fonte_e_toda_tabela_gold(lake_processado: dict) -> None:
    catalogo = construir_catalogo(lake_processado["cfg"], lake_processado["lake"])
    assert set(catalogo["fontes"]) == set(contratos_ativos())
    for fonte, dados in catalogo["fontes"].items():
        assert dados["silver"]["existe"], fonte
        assert dados["bronze"]["lotes"] > 0, fonte
        assert dados["silver"]["lotes_distintos"] <= dados["bronze"]["lotes"]
    assert set(catalogo["gold"]) == set(TABELAS)
    assert all(g["existe"] for g in catalogo["gold"].values())


def test_catalogo_registra_linhagem_bronze_silver_gold(lake_processado: dict) -> None:
    catalogo = construir_catalogo(lake_processado["cfg"], lake_processado["lake"])
    sensores = catalogo["fontes"]["sensores"]
    assert sensores["linhagem"]["silver_tabela"] == "sensores_leitura"
    assert "fato_balanco_hidrico" in sensores["linhagem"]["gold_tabelas"]
    assert sensores["linhagem"]["bronze_lotes"] == sensores["silver"]["lotes_distintos"]
    por_arquivo = sensores["silver"]["linhagem_por_arquivo"]
    assert len(por_arquivo) == sensores["silver"]["arquivos"]
    assert all(info["lotes"] >= 1 for info in por_arquivo.values())


def test_catalogo_persistido_e_com_schema(lake_processado: dict) -> None:
    cfg = lake_processado["cfg"]
    construir_catalogo(cfg, lake_processado["lake"])
    persistido = json.loads((cfg.dir_outputs / "catalogo.json").read_text(encoding="utf-8"))
    schema = persistido["fontes"]["clima"]["silver"]["schema"]
    assert schema["data"] == "Date" and schema["eto_mm"] == "Float64"
    assert persistido["camadas"]["bronze"]["objetos"] > 0


def test_dicionario_e_gerado_dos_contratos_e_do_gold(lake_processado: dict) -> None:
    cfg = lake_processado["cfg"]
    catalogo = construir_catalogo(cfg, lake_processado["lake"])
    destino = gerar_dicionario(cfg, catalogo)
    texto = destino.read_text(encoding="utf-8")
    assert destino.name == "dicionario-de-dados.md"
    assert "## sensores_leitura" in texto and "## fato_balanco_hidrico" in texto
    assert "| SEN-02 | unicidade | critica |" in texto
    assert "pessoal → pseudonimizar" in texto
    assert "`_batch_id` | proveniência" in texto
    assert "registros em" in texto  # inventário do lake entrou no texto
