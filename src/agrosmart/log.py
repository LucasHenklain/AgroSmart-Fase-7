"""Logging e trilha de eventos do pipeline.

Duas saídas, dois públicos:

* **console** — legível, colorido, para quem está rodando o pipeline agora;
* **``outputs/logs/eventos.jsonl``** — uma linha JSON por evento, para a
  observabilidade da Entrega 2. É a partir desse arquivo que se responde
  "quantos registros a fonte X ingeriu ontem e quantos foram reprovados",
  sem precisar reprocessar nada.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.logging import RichHandler

from agrosmart.config import Config, carregar_config

console = Console()

_ARQUIVO_EVENTOS = "eventos.jsonl"
_configurado = False
# Coletores gravam eventos de várias threads; a trilha precisa de uma linha inteira por evento.
_trava_eventos = threading.Lock()


def configurar_logging(config: Config | None = None, *, forcar: bool = False) -> None:
    """Instala o handler de console. Seguro para chamar várias vezes."""
    global _configurado
    if _configurado and not forcar:
        return

    cfg = config or carregar_config()
    raiz = logging.getLogger("agrosmart")
    raiz.handlers.clear()
    raiz.setLevel(cfg.log_level)
    raiz.propagate = False
    raiz.addHandler(
        RichHandler(
            console=console,
            rich_tracebacks=True,
            show_path=False,
            omit_repeated_times=False,
            log_time_format="%H:%M:%S",
        )
    )
    # boto3 é verborrágico no nível INFO e afoga o log do pipeline.
    for ruidoso in ("boto3", "botocore", "urllib3", "s3transfer"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)

    _configurado = True


def obter_logger(nome: str) -> logging.Logger:
    """Logger nomeado sob o namespace ``agrosmart``."""
    configurar_logging()
    sufixo = nome.removeprefix("agrosmart.")
    return logging.getLogger(f"agrosmart.{sufixo}")


@dataclass(slots=True)
class Evento:
    """Um fato estruturado sobre a execução do pipeline."""

    etapa: str
    acao: str
    fonte: str | None = None
    camada: str | None = None
    batch_id: str | None = None
    status: str = "ok"
    mensagem: str = ""
    metricas: dict[str, Any] = field(default_factory=dict)
    ocorrido_em: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )

    def para_dict(self) -> dict[str, Any]:
        return {
            "ocorrido_em": self.ocorrido_em,
            "etapa": self.etapa,
            "acao": self.acao,
            "fonte": self.fonte,
            "camada": self.camada,
            "batch_id": self.batch_id,
            "status": self.status,
            "mensagem": self.mensagem,
            "metricas": self.metricas,
        }


def registrar_evento(
    evento: Evento, config: Config | None = None, *, eco_console: bool = True
) -> Path:
    """Anexa o evento à trilha JSONL e, por padrão, ecoa um resumo no console.

    ``eco_console=False`` é para eventos de alta cardinalidade (um por lote de
    sensores, por exemplo): ficam na trilha para auditoria, mas o console
    recebe só o resumo da etapa.
    """
    cfg = config or carregar_config()
    cfg.dir_logs.mkdir(parents=True, exist_ok=True)
    destino = cfg.dir_logs / _ARQUIVO_EVENTOS

    linha = json.dumps(evento.para_dict(), ensure_ascii=False) + "\n"
    with _trava_eventos, destino.open("a", encoding="utf-8") as arquivo:
        arquivo.write(linha)

    if not eco_console:
        return destino

    logger = obter_logger(f"evento.{evento.etapa}")
    detalhes = " ".join(f"{k}={v}" for k, v in evento.metricas.items())
    linha = f"[{evento.acao}] {evento.mensagem or evento.acao}"
    if detalhes:
        linha = f"{linha} · {detalhes}"

    if evento.status == "erro":
        logger.error(linha)
    elif evento.status == "alerta":
        logger.warning(linha)
    else:
        logger.info(linha)

    return destino


def ler_eventos(config: Config | None = None) -> list[dict[str, Any]]:
    """Lê a trilha de eventos inteira. Usado pelo ``doctor`` e pelos relatórios."""
    cfg = config or carregar_config()
    destino = cfg.dir_logs / _ARQUIVO_EVENTOS
    if not destino.exists():
        return []
    with destino.open(encoding="utf-8") as arquivo:
        return [json.loads(linha) for linha in arquivo if linha.strip()]
