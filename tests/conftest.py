"""Fixtures compartilhadas.

Os testes nunca tocam o lake real: cada um recebe uma ``Config`` apontando
para um diretório temporário com backend ``filesystem``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from agrosmart.config import RAIZ_PROJETO, Backend, Config


def fabricar_config(raiz: Path, **sobrescritas: object) -> Config:
    """Config de teste: backend filesystem, janela curta, semente fixa."""
    base: dict[str, object] = {
        "backend": Backend.FILESYSTEM,
        "minio_endpoint": "http://localhost:9000",
        "minio_access_key": "x",
        "minio_secret_key": "y",
        "minio_region": "us-east-1",
        "prefixo_bucket": "teste",
        "raiz": raiz,
        "seed": 1,
        "dias_historico": 3,
        "qtd_talhoes": 6,
        "data_referencia": date(2026, 9, 1),
        "paralelismo": 4,
        "salt_pseudonimizacao": "sal-de-teste",
        "ia_modelo": "claude-sonnet-5",
        "anthropic_api_key": None,
        "log_level": "WARNING",
    }
    base.update(sobrescritas)
    return Config(**base)  # type: ignore[arg-type]


@pytest.fixture()
def config_temporaria(tmp_path: Path) -> Config:
    return fabricar_config(tmp_path)


@pytest.fixture(scope="session")
def lake_processado(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Pipeline completo (simular → ingerir → silver → gold) em filesystem, 6 dias.

    Compartilhado entre os testes de integração que só **leem** o lake. Testes
    que alteram o lake (compactação, retenção) montam o próprio.
    """
    from agrosmart.ingestao import ingerir
    from agrosmart.processamento.gold import construir_gold
    from agrosmart.processamento.silver import construir_silver
    from agrosmart.simuladores import simular
    from agrosmart.storage import criar_armazenamento

    cfg = fabricar_config(tmp_path_factory.mktemp("lake"), dias_historico=6)
    simulacao = simular(cfg)
    ingestao = ingerir(cfg, exportar_exemplos=False)
    lake = criar_armazenamento(cfg)
    dq = construir_silver(cfg, lake)
    gold = construir_gold(cfg, lake)
    return {
        "cfg": cfg,
        "lake": lake,
        "simulacao": simulacao,
        "ingestao": ingestao,
        "dq": dq,
        "gold": gold,
        "dias": 6,
    }


@pytest.fixture(scope="session")
def dir_contratos_projeto() -> Path:
    return RAIZ_PROJETO / "contratos"
