from __future__ import annotations

import pytest

from agrosmart.config import Camada, Config
from agrosmart.storage import (
    ArmazenamentoFilesystem,
    ErroArmazenamento,
    ObjetoNaoEncontrado,
    checksum_sha256,
    criar_armazenamento,
    normalizar_chave,
)


@pytest.fixture()
def lake(config_temporaria: Config) -> ArmazenamentoFilesystem:
    armazenamento = ArmazenamentoFilesystem(config_temporaria)
    armazenamento.preparar()
    return armazenamento


def test_fabrica_respeita_o_backend(config_temporaria: Config) -> None:
    assert isinstance(criar_armazenamento(config_temporaria), ArmazenamentoFilesystem)


def test_roundtrip_bytes_com_checksum_e_metadados(lake: ArmazenamentoFilesystem) -> None:
    dados = b'{"sensor_id": "SNS-0001"}'
    objeto = lake.gravar_bytes(
        Camada.BRONZE,
        "sensores/ano=2026/mes=08/dia=28/lote.jsonl",
        dados,
        metadados={"batch_id": "abc", "_source": "mqtt"},
    )
    assert objeto.tamanho_bytes == len(dados)
    assert objeto.checksum_sha256 == checksum_sha256(dados)
    # chaves de metadados normalizadas ao estilo S3
    assert objeto.metadados["batch-id"] == "abc"
    assert lake.ler_bytes(Camada.BRONZE, objeto.chave) == dados


def test_roundtrip_json(lake: ArmazenamentoFilesystem) -> None:
    conteudo = {"registros": 3, "fonte": "clima", "acentuação": "ok"}
    lake.gravar_json(Camada.BRONZE, "clima/_manifest.json", conteudo)
    assert lake.ler_json(Camada.BRONZE, "clima/_manifest.json") == conteudo


def test_listar_por_prefixo_e_contar(lake: ArmazenamentoFilesystem) -> None:
    lake.gravar_texto(Camada.SILVER, "sensores/parte-0.parquet", "a")
    lake.gravar_texto(Camada.SILVER, "sensores/parte-1.parquet", "b")
    lake.gravar_texto(Camada.SILVER, "clima/parte-0.parquet", "c")

    chaves = [o.chave for o in lake.listar(Camada.SILVER, "sensores/")]
    assert chaves == ["sensores/parte-0.parquet", "sensores/parte-1.parquet"]
    assert lake.contar(Camada.SILVER) == 3
    assert lake.contar(Camada.GOLD) == 0


def test_camadas_sao_isoladas(lake: ArmazenamentoFilesystem) -> None:
    lake.gravar_texto(Camada.BRONZE, "x.txt", "bronze")
    assert lake.existe(Camada.BRONZE, "x.txt")
    assert not lake.existe(Camada.SILVER, "x.txt")


def test_ler_chave_inexistente_levanta_erro_especifico(lake: ArmazenamentoFilesystem) -> None:
    with pytest.raises(ObjetoNaoEncontrado):
        lake.ler_bytes(Camada.GOLD, "nao/existe.parquet")


def test_remover_apaga_objeto_e_metadados(lake: ArmazenamentoFilesystem) -> None:
    lake.gravar_texto(Camada.QUARENTENA, "q/1.json", "{}")
    lake.remover(Camada.QUARENTENA, "q/1.json")
    assert not lake.existe(Camada.QUARENTENA, "q/1.json")
    assert lake.contar(Camada.QUARENTENA) == 0


def test_escrita_nao_deixa_temporarios(lake: ArmazenamentoFilesystem) -> None:
    lake.gravar_texto(Camada.BRONZE, "a/b.txt", "conteúdo")
    assert [o.chave for o in lake.listar(Camada.BRONZE)] == ["a/b.txt"]


def test_uri_da_camada_nao_exige_chave(lake: ArmazenamentoFilesystem) -> None:
    uri = lake.uri_camada(Camada.BRONZE)
    assert uri.startswith("file:///")
    assert uri.endswith("/datalake/bronze")


def test_diagnostico_reporta_camadas(lake: ArmazenamentoFilesystem) -> None:
    lake.gravar_texto(Camada.BRONZE, "a.txt", "1")
    diag = lake.diagnostico()
    assert diag["backend"] == "filesystem"
    assert diag["camadas"]["bronze"]["objetos"] == 1
    assert all(info["existe"] for info in diag["camadas"].values())


@pytest.mark.parametrize(
    "chave",
    ["", "   ", "../fora/do/lake", "a/../../b"],
)
def test_chaves_perigosas_sao_rejeitadas(chave: str) -> None:
    with pytest.raises(ErroArmazenamento):
        normalizar_chave(chave)


def test_normalizacao_de_chave() -> None:
    assert normalizar_chave("\\sensores\\ano=2026\\x.json") == "sensores/ano=2026/x.json"
    assert normalizar_chave("/a/b") == "a/b"
