"""Configuração central do AgroSmart.

Um único lugar decide *onde* o Data Lake vive e *como* ele é endereçado. Todo o
resto do código conversa com camadas lógicas (``bronze``, ``silver``, ``gold``,
``quarentena``) e nunca com caminhos ou buckets literais — é isso que permite
trocar MinIO por sistema de arquivos, ou por S3 de verdade, sem tocar em uma
linha de lógica de negócio.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# src/agrosmart/config.py -> src/agrosmart -> src -> raiz do projeto
RAIZ_PROJETO = Path(__file__).resolve().parents[2]


class Camada(StrEnum):
    """As camadas do Data Lake, na ordem em que o dado as atravessa."""

    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    QUARENTENA = "quarentena"

    @property
    def rotulo(self) -> str:
        return {
            Camada.BRONZE: "Bruta",
            Camada.SILVER: "Confiável",
            Camada.GOLD: "Refinada",
            Camada.QUARENTENA: "Quarentena",
        }[self]

    @property
    def proposito(self) -> str:
        return {
            Camada.BRONZE: (
                "Preserva o dado exatamente como chegou. Imutável, append-only, "
                "particionado por fonte e data. É a única fonte da verdade para "
                "reprocessar o lake inteiro quando uma regra muda."
            ),
            Camada.SILVER: (
                "Dado validado, tipado, deduplicado e com unidades no SI. "
                "Aqui o lake deixa de ser um pântano: nada entra sem passar "
                "pelas regras do contrato da fonte."
            ),
            Camada.GOLD: (
                "Modelo dimensional e indicadores prontos para consumo. Serve "
                "dashboard, API e o Context Pack do copiloto de IA a partir do "
                "mesmo conjunto de tabelas."
            ),
            Camada.QUARENTENA: (
                "Registros reprovados na validação, guardados com o motivo da "
                "reprovação. Nada é descartado em silêncio — dado ruim é "
                "evidência de problema na origem."
            ),
        }[self]


class Backend(StrEnum):
    """Onde o lake está fisicamente."""

    MINIO = "minio"
    FILESYSTEM = "filesystem"


def _env_texto(chave: str, padrao: str) -> str:
    valor = os.getenv(chave)
    return valor.strip() if valor and valor.strip() else padrao


def _env_inteiro(chave: str, padrao: int) -> int:
    bruto = os.getenv(chave)
    if not bruto or not bruto.strip():
        return padrao
    try:
        return int(bruto.strip())
    except ValueError as erro:
        raise ValueError(f"{chave} precisa ser um número inteiro, recebi {bruto!r}") from erro


def _env_data(chave: str, padrao: date) -> date:
    bruto = os.getenv(chave)
    if not bruto or not bruto.strip():
        return padrao
    try:
        return date.fromisoformat(bruto.strip())
    except ValueError as erro:
        raise ValueError(f"{chave} precisa ser uma data AAAA-MM-DD, recebi {bruto!r}") from erro


@dataclass(frozen=True, slots=True)
class Config:
    """Configuração imutável, resolvida uma vez por execução."""

    backend: Backend
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_region: str
    prefixo_bucket: str
    raiz: Path
    seed: int
    dias_historico: int
    qtd_talhoes: int
    data_referencia: date
    paralelismo: int
    salt_pseudonimizacao: str
    ia_modelo: str
    anthropic_api_key: str | None
    log_level: str

    # --- tempo simulado -----------------------------------------------------

    @property
    def instante_referencia(self) -> datetime:
        """O "agora" do pipeline: 03h00 UTC da data de referência.

        Toda regra que compara com o presente (freshness, não-futuro) usa este
        instante, não o relógio da máquina. É o que torna a simulação e os
        prints reprodutíveis em qualquer dia em que o avaliador rodar.
        """
        return datetime(
            self.data_referencia.year,
            self.data_referencia.month,
            self.data_referencia.day,
            3,
            0,
            tzinfo=UTC,
        )

    @property
    def janela_historico(self) -> tuple[date, date]:
        """Primeiro e último dia (inclusive) cobertos pela simulação."""
        fim = self.data_referencia - timedelta(days=1)
        inicio = fim - timedelta(days=self.dias_historico - 1)
        return inicio, fim

    # --- diretórios do projeto ---------------------------------------------

    @property
    def dir_contratos(self) -> Path:
        return self.raiz / "contratos"

    @property
    def dir_dados(self) -> Path:
        return self.raiz / "data"

    @property
    def dir_exemplos(self) -> Path:
        """Amostras curadas que vão para a entrega — não é o lake."""
        return self.dir_dados / "exemplos"

    @property
    def dir_landing(self) -> Path:
        """Onde os sistemas de origem depositam o que produzem.

        É a fronteira entre "mundo externo" e lake: os simuladores escrevem
        aqui como se fossem o gateway MQTT, a API de clima, o ERP e o drone;
        os coletores leem daqui e gravam no bronze.
        """
        return self.raiz / "landing"

    def dir_landing_fonte(self, subpasta: str) -> Path:
        return self.dir_landing / subpasta

    @property
    def dir_outputs(self) -> Path:
        return self.raiz / "outputs"

    @property
    def dir_logs(self) -> Path:
        return self.dir_outputs / "logs"

    @property
    def dir_relatorios(self) -> Path:
        return self.dir_outputs / "relatorios"

    @property
    def dir_prints(self) -> Path:
        return self.dir_outputs / "prints"

    @property
    def dir_datalake(self) -> Path:
        """Raiz do lake quando o backend é ``filesystem``."""
        return self.raiz / "datalake"

    @property
    def dir_docs(self) -> Path:
        return self.raiz / "docs"

    # --- endereçamento do lake ---------------------------------------------

    def bucket(self, camada: Camada) -> str:
        """Nome do bucket S3 de uma camada: ``agrosmart-bronze``."""
        return f"{self.prefixo_bucket}-{camada.value}"

    def caminho_local(self, camada: Camada) -> Path:
        """Raiz local de uma camada quando o backend é ``filesystem``."""
        return self.dir_datalake / camada.value

    # --- capacidades opcionais ---------------------------------------------

    @property
    def ia_habilitada(self) -> bool:
        """Sem chave, o copiloto cai no motor de regras e a demo continua de pé."""
        return bool(self.anthropic_api_key)

    def resumo(self) -> dict[str, object]:
        """Visão segura para log e para o comando ``doctor`` — sem segredos."""
        return {
            "backend": self.backend.value,
            "endpoint": (
                self.minio_endpoint if self.backend is Backend.MINIO else str(self.dir_datalake)
            ),
            "prefixo_bucket": self.prefixo_bucket,
            "raiz": str(self.raiz),
            "seed": self.seed,
            "dias_historico": self.dias_historico,
            "qtd_talhoes": self.qtd_talhoes,
            "data_referencia": self.data_referencia.isoformat(),
            "paralelismo": self.paralelismo,
            "ia_modelo": self.ia_modelo,
            "ia_habilitada": self.ia_habilitada,
            "log_level": self.log_level,
        }


@lru_cache(maxsize=1)
def carregar_config() -> Config:
    """Lê ``.env`` + variáveis de ambiente e devolve a configuração da execução.

    O resultado é memoizado: a configuração é resolvida uma única vez por
    processo, de modo que dois módulos nunca discordam sobre onde o lake está.
    """
    load_dotenv(RAIZ_PROJETO / ".env", override=False)

    bruto_backend = _env_texto("AGROSMART_BACKEND", Backend.MINIO.value).lower()
    try:
        backend = Backend(bruto_backend)
    except ValueError as erro:
        validos = ", ".join(b.value for b in Backend)
        raise ValueError(
            f"AGROSMART_BACKEND={bruto_backend!r} é inválido. Use um de: {validos}."
        ) from erro

    chave_ia = os.getenv("ANTHROPIC_API_KEY", "").strip()
    # Fase 7: no container o pacote é instalado como wheel (site-packages), então a
    # raiz do projeto (contratos/, landing/, outputs/) vem de AGROSMART_RAIZ.
    raiz_env = os.getenv("AGROSMART_RAIZ", "").strip()
    raiz = Path(raiz_env).resolve() if raiz_env else RAIZ_PROJETO

    return Config(
        backend=backend,
        minio_endpoint=_env_texto("AGROSMART_MINIO_ENDPOINT", "http://localhost:9000"),
        minio_access_key=_env_texto("AGROSMART_MINIO_ACCESS_KEY", "agrosmart"),
        minio_secret_key=_env_texto("AGROSMART_MINIO_SECRET_KEY", "agrosmart123"),
        minio_region=_env_texto("AGROSMART_MINIO_REGION", "us-east-1"),
        prefixo_bucket=_env_texto("AGROSMART_PREFIXO_BUCKET", "agrosmart"),
        raiz=raiz,
        seed=_env_inteiro("AGROSMART_SEED", 20260905),
        dias_historico=_env_inteiro("AGROSMART_DIAS_HISTORICO", 30),
        qtd_talhoes=_env_inteiro("AGROSMART_QTD_TALHOES", 6),
        data_referencia=_env_data("AGROSMART_DATA_REFERENCIA", date(2026, 9, 1)),
        paralelismo=_env_inteiro("AGROSMART_PARALELISMO", 8),
        salt_pseudonimizacao=_env_texto("AGROSMART_SALT_PSEUDONIMIZACAO", "agrosmart-fase5-dev"),
        ia_modelo=_env_texto("AGROSMART_IA_MODELO", "claude-sonnet-5"),
        anthropic_api_key=chave_ia or None,
        log_level=_env_texto("AGROSMART_LOG_LEVEL", "INFO").upper(),
    )


def garantir_diretorios(config: Config | None = None) -> list[Path]:
    """Cria os diretórios de trabalho do projeto. Idempotente."""
    cfg = config or carregar_config()
    diretorios = [
        cfg.dir_exemplos,
        cfg.dir_landing / "erp",
        cfg.dir_landing / "sensores",
        cfg.dir_landing / "clima",
        cfg.dir_landing / "imagens",
        cfg.dir_landing / "laudos",
        cfg.dir_logs,
        cfg.dir_relatorios,
        cfg.dir_prints,
    ]
    if cfg.backend is Backend.FILESYSTEM:
        diretorios += [cfg.caminho_local(camada) for camada in Camada]
    for caminho in diretorios:
        caminho.mkdir(parents=True, exist_ok=True)
    return diretorios
