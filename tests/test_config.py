from __future__ import annotations

from pathlib import Path

from agrosmart.config import Backend, Camada, Config, garantir_diretorios


def test_nome_dos_buckets_segue_o_prefixo(config_temporaria: Config) -> None:
    assert config_temporaria.bucket(Camada.BRONZE) == "teste-bronze"
    assert config_temporaria.bucket(Camada.QUARENTENA) == "teste-quarentena"


def test_caminho_local_por_camada(config_temporaria: Config, tmp_path: Path) -> None:
    assert config_temporaria.caminho_local(Camada.GOLD) == tmp_path / "datalake" / "gold"


def test_garantir_diretorios_cria_camadas_no_filesystem(config_temporaria: Config) -> None:
    criados = garantir_diretorios(config_temporaria)
    for camada in Camada:
        assert config_temporaria.caminho_local(camada).is_dir()
    assert config_temporaria.dir_logs in criados
    # idempotente
    garantir_diretorios(config_temporaria)


def test_resumo_nao_vaza_segredos(config_temporaria: Config) -> None:
    resumo = config_temporaria.resumo()
    assert "y" not in resumo.values()
    assert "minio_secret_key" not in resumo
    assert resumo["backend"] == Backend.FILESYSTEM.value


def test_ia_desabilitada_sem_chave(config_temporaria: Config) -> None:
    assert config_temporaria.ia_habilitada is False


def test_toda_camada_tem_proposito_documentado() -> None:
    for camada in Camada:
        assert len(camada.proposito) > 40
        assert camada.rotulo
