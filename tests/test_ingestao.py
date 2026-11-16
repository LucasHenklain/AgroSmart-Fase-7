from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agrosmart.config import Camada, Config
from agrosmart.contratos import contratos_ativos
from agrosmart.ingestao import construir_coletor, ingerir
from agrosmart.ingestao.base import Lote
from agrosmart.ingestao.batch import ColetorBatch
from agrosmart.ingestao.watch_folder import ColetorPastaObservada
from agrosmart.simuladores import simular
from agrosmart.storage import ArmazenamentoFilesystem, criar_armazenamento
from tests.conftest import fabricar_config

DIAS_TESTE = 3


@pytest.fixture(scope="module")
def ambiente(tmp_path_factory: pytest.TempPathFactory) -> tuple[Config, dict, dict]:
    cfg = fabricar_config(tmp_path_factory.mktemp("ing"), dias_historico=DIAS_TESTE)
    simulacao = simular(cfg)
    ingestao = ingerir(cfg)
    return cfg, simulacao, ingestao


def _lake(cfg: Config) -> ArmazenamentoFilesystem:
    lake = criar_armazenamento(cfg)
    assert isinstance(lake, ArmazenamentoFilesystem)
    return lake


# ---------------------------------------------------------------------------
# Ingestão completa
# ---------------------------------------------------------------------------


def test_todas_as_fontes_ingeridas_sem_rejeicao(ambiente: tuple[Config, dict, dict]) -> None:
    _, _, ingestao = ambiente
    assert {f["fonte"] for f in ingestao["fontes"]} == set(contratos_ativos())
    for fonte in ingestao["fontes"]:
        assert fonte["rejeitados"] == 0, fonte["rejeicoes"]
        assert fonte["gravados"] == fonte["lotes"]


def test_fonte_mestra_e_ingerida_primeiro(ambiente: tuple[Config, dict, dict]) -> None:
    _, _, ingestao = ambiente
    assert ingestao["fontes"][0]["fonte"] == "erp_talhoes"


def test_registros_ingeridos_batem_com_os_gerados(ambiente: tuple[Config, dict, dict]) -> None:
    _, simulacao, ingestao = ambiente
    for fonte in ingestao["fontes"]:
        assert fonte["registros"] == simulacao["fontes"][fonte["fonte"]]["registros"], fonte[
            "fonte"
        ]


def test_bronze_tem_um_manifest_por_lote(ambiente: tuple[Config, dict, dict]) -> None:
    cfg, _, ingestao = ambiente
    lake = _lake(cfg)
    manifests = lake.contar(Camada.BRONZE, "_manifests/")
    assert manifests == ingestao["totais"]["lotes"]
    assert lake.contar(Camada.QUARENTENA) == 0


def test_chaves_do_bronze_sao_particionadas_por_data(ambiente: tuple[Config, dict, dict]) -> None:
    cfg, _, _ = ambiente
    lake = _lake(cfg)
    chaves = [o.chave for o in lake.listar(Camada.BRONZE, "sensores/")]
    assert chaves
    for chave in chaves:
        partes = chave.split("/")
        assert partes[0] == "sensores"
        assert (
            partes[1].startswith("ano=")
            and partes[2].startswith("mes=")
            and partes[3].startswith("dia=")
        )


def test_manifest_descreve_o_lote_fielmente(ambiente: tuple[Config, dict, dict]) -> None:
    cfg, _, _ = ambiente
    lake = _lake(cfg)
    manifest_chave = next(o.chave for o in lake.listar(Camada.BRONZE, "_manifests/sensores/"))
    manifest = lake.ler_json(Camada.BRONZE, manifest_chave)
    dados = lake.ler_bytes(Camada.BRONZE, manifest["arquivos"][0]["chave"])
    linhas = [linha for linha in dados.decode("utf-8").splitlines() if linha.strip()]
    assert manifest["registros"] == len(linhas)
    assert manifest["bytes"] == len(dados)
    assert manifest["versao"] == 1
    assert manifest["schema_version"] == "sensores.v1"
    assert manifest["modo"] == "streaming"


def test_objeto_bruto_e_identico_ao_da_landing(ambiente: tuple[Config, dict, dict]) -> None:
    """Bronze sem transformação: byte a byte igual à origem."""
    cfg, _, _ = ambiente
    lake = _lake(cfg)
    manifest_chave = next(o.chave for o in lake.listar(Camada.BRONZE, "_manifests/clima/"))
    manifest = lake.ler_json(Camada.BRONZE, manifest_chave)
    origem = cfg.dir_landing / manifest["origem"]
    assert lake.ler_bytes(Camada.BRONZE, manifest["arquivos"][0]["chave"]) == origem.read_bytes()


def test_binario_e_sidecar_ficam_na_mesma_particao(ambiente: tuple[Config, dict, dict]) -> None:
    cfg, _, _ = ambiente
    lake = _lake(cfg)
    manifest_chave = next(o.chave for o in lake.listar(Camada.BRONZE, "_manifests/imagens_ndvi/"))
    manifest = lake.ler_json(Camada.BRONZE, manifest_chave)
    papeis = {a["papel"] for a in manifest["arquivos"]}
    assert papeis == {"principal", "anexo"}
    pastas = {a["chave"].rsplit("/", 1)[0] for a in manifest["arquivos"]}
    assert len(pastas) == 1


def test_metadados_de_ingestao_no_objeto(ambiente: tuple[Config, dict, dict]) -> None:
    cfg, _, _ = ambiente
    lake = _lake(cfg)
    objeto = next(o for o in lake.listar(Camada.BRONZE, "erp_talhoes/"))
    assert objeto.metadados["schema-version"] == "erp_talhoes.v1"
    assert len(objeto.metadados["batch-id"]) == 16
    assert objeto.metadados["sha256"] == objeto.checksum_sha256


def test_exemplos_exportados_para_a_entrega(ambiente: tuple[Config, dict, dict]) -> None:
    cfg, _, ingestao = ambiente
    assert set(ingestao["exemplos"]) == set(contratos_ativos())
    for fonte, arquivos in ingestao["exemplos"].items():
        pasta = cfg.dir_exemplos / "bronze" / fonte
        assert (pasta / "_manifest.json").exists()
        for nome in arquivos:
            assert (pasta / nome).exists(), f"{fonte}/{nome}"


# ---------------------------------------------------------------------------
# Idempotência e versionamento
# ---------------------------------------------------------------------------


def test_reingerir_nao_duplica_nada(ambiente: tuple[Config, dict, dict]) -> None:
    cfg, _, primeira = ambiente
    lake = _lake(cfg)
    antes = lake.contar(Camada.BRONZE)
    segunda = ingerir(cfg, exportar_exemplos=False)
    assert segunda["totais"]["pulados"] == primeira["totais"]["lotes"]
    assert segunda["totais"]["gravados"] == 0
    assert lake.contar(Camada.BRONZE) == antes


def test_origem_alterada_gera_nova_versao(tmp_path: Path) -> None:
    cfg = fabricar_config(tmp_path, dias_historico=2)
    simular(cfg, fontes=["erp_talhoes"])
    ingerir(cfg, fontes=["erp_talhoes"], exportar_exemplos=False)

    arquivo = sorted((cfg.dir_landing / "erp").glob("talhoes_*.csv"))[0]
    arquivo.write_text(
        arquivo.read_text(encoding="utf-8").replace("Alto da Sede", "Alto da Sede II"),
        encoding="utf-8",
    )

    resumo = ingerir(cfg, fontes=["erp_talhoes"], exportar_exemplos=False)
    fonte = resumo["fontes"][0]
    assert fonte["atualizados"] == 1
    assert fonte["pulados"] == fonte["lotes"] - 1

    lake = _lake(cfg)
    manifests = [
        lake.ler_json(Camada.BRONZE, o.chave)
        for o in lake.listar(Camada.BRONZE, "_manifests/erp_talhoes/")
    ]
    versoes = sorted(m["versao"] for m in manifests)
    assert versoes[-1] == 2 and versoes[:-1] == [1] * (len(versoes) - 1)
    atualizado = next(m for m in manifests if m["versao"] == 2)
    assert atualizado["versao_anterior_checksum"] is not None


def test_batch_id_e_deterministico() -> None:
    lote = Lote(
        "x",
        Path("a.csv"),
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 8, 2, tzinfo=UTC),
        datetime(2026, 8, 1).date(),
    )
    outro = Lote(
        "x",
        Path("a.csv"),
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 8, 2, tzinfo=UTC),
        datetime(2026, 8, 1).date(),
    )
    assert lote.batch_id == outro.batch_id
    assert len(lote.batch_id) == 16
    assert int(lote.batch_id, 16) >= 0


# ---------------------------------------------------------------------------
# Schema na porta
# ---------------------------------------------------------------------------


def test_csv_com_coluna_inesperada_e_rejeitado_para_a_quarentena(tmp_path: Path) -> None:
    cfg = fabricar_config(tmp_path, dias_historico=2)
    simular(cfg, fontes=["erp_talhoes"])
    arquivo = sorted((cfg.dir_landing / "erp").glob("talhoes_*.csv"))[0]
    linhas = arquivo.read_text(encoding="utf-8").splitlines()
    linhas[0] += ",coluna_nova"
    linhas[1:] = [linha + ",x" for linha in linhas[1:]]
    arquivo.write_text("\n".join(linhas) + "\n", encoding="utf-8")

    resumo = ingerir(cfg, fontes=["erp_talhoes"], exportar_exemplos=False)
    fonte = resumo["fontes"][0]
    assert fonte["rejeitados"] == 1
    assert "coluna_nova" in fonte["rejeicoes"][0]["motivo"]

    lake = _lake(cfg)
    rejeitados = [o.chave for o in lake.listar(Camada.QUARENTENA, "_rejeitados/erp_talhoes/")]
    assert any(c.endswith(arquivo.name) for c in rejeitados)
    assert any(c.endswith("_motivo.json") for c in rejeitados)
    assert not any(
        o.chave.endswith(arquivo.name) for o in lake.listar(Camada.BRONZE, "erp_talhoes/")
    )


def test_csv_com_coluna_faltando_e_rejeitado(tmp_path: Path) -> None:
    cfg = fabricar_config(tmp_path, dias_historico=2)
    simular(cfg, fontes=["erp_operacoes"])
    arquivo = sorted((cfg.dir_landing / "erp").glob("operacoes_*.csv"))[0]
    linhas = arquivo.read_text(encoding="utf-8").splitlines()
    cabecalho = linhas[0].split(",")
    indice = cabecalho.index("custo_brl")
    novas = []
    for linha in linhas:
        campos = linha.split(",")
        del campos[indice]
        novas.append(",".join(campos))
    arquivo.write_text("\n".join(novas) + "\n", encoding="utf-8")

    resumo = ingerir(cfg, fontes=["erp_operacoes"], exportar_exemplos=False)
    assert resumo["fontes"][0]["rejeitados"] == 1
    assert "custo_brl" in resumo["fontes"][0]["rejeicoes"][0]["motivo"]


def test_jsonl_com_chave_desconhecida_e_rejeitado(tmp_path: Path) -> None:
    cfg = fabricar_config(tmp_path, dias_historico=1)
    simular(cfg, fontes=["sensores"])
    arquivo = sorted((cfg.dir_landing / "sensores").rglob("*.jsonl"))[0]
    registro = json.loads(arquivo.read_text(encoding="utf-8").splitlines()[0])
    registro["campo_pirata"] = 1
    arquivo.write_text(json.dumps(registro) + "\n", encoding="utf-8")

    resumo = ingerir(cfg, fontes=["sensores"], exportar_exemplos=False)
    fonte = resumo["fontes"][0]
    assert fonte["rejeitados"] == 1
    assert fonte["gravados"] == fonte["lotes"] - 1


def test_sidecar_sem_binario_e_rejeitado(tmp_path: Path) -> None:
    cfg = fabricar_config(tmp_path, dias_historico=2)
    simular(cfg, fontes=["laudos_solo"])
    pdf = sorted((cfg.dir_landing / "laudos").glob("*.pdf"))[0]
    pdf.unlink()

    resumo = ingerir(cfg, fontes=["laudos_solo"], exportar_exemplos=False)
    fonte = resumo["fontes"][0]
    assert fonte["rejeitados"] == 1
    assert "não encontrado" in fonte["rejeicoes"][0]["motivo"]


def test_envelope_da_api_sem_serie_obrigatoria_e_rejeitado(tmp_path: Path) -> None:
    cfg = fabricar_config(tmp_path, dias_historico=1)
    simular(cfg, fontes=["clima"])
    arquivo = sorted((cfg.dir_landing / "clima").glob("*.json"))[0]
    envelope = json.loads(arquivo.read_text(encoding="utf-8"))
    del envelope["daily"]["precipitation_sum"]
    arquivo.write_text(json.dumps(envelope), encoding="utf-8")

    resumo = ingerir(cfg, fontes=["clima"], exportar_exemplos=False)
    assert resumo["fontes"][0]["rejeitados"] == 1
    assert "precipitation_sum" in resumo["fontes"][0]["rejeicoes"][0]["motivo"]


def test_coletor_e_escolhido_pelo_modo_do_contrato(config_temporaria: Config) -> None:
    lake = criar_armazenamento(config_temporaria)
    contratos = contratos_ativos()
    assert isinstance(
        construir_coletor(contratos["erp_talhoes"], lake, config_temporaria), ColetorBatch
    )
    assert isinstance(
        construir_coletor(contratos["laudos_solo"], lake, config_temporaria), ColetorPastaObservada
    )
