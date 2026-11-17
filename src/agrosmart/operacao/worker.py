"""O worker: a plataforma rodando sozinha, ciclo após ciclo.

Cada ciclo é um **caso** do processo (``execucao_id = ciclo-…``) e passa pelas
mesmas etapas da orquestração, sem simular origens — os dados de sensores chegam
pela landing que o gateway MQTT (outro container) escreve continuamente:

    ingerir → silver → gold → catálogo → decidir → atuar (MQTT)

Falha em um ciclo não derruba o serviço: a etapa é registrada com erro, o ciclo
termina como ``falhou`` e o próximo tenta de novo (o *retrabalho* que a mineração
de processos mede). Na primeira subida, se o gold estiver vazio, uma **carga
inicial** simula o histórico das seis fontes (rotulada ``carga_inicial`` no event log).
"""

from __future__ import annotations

import os
import signal
import threading
import time
from typing import Any

from agrosmart.config import Camada, Config, carregar_config
from agrosmart.log import abrir_execucao, fechar_execucao, obter_logger
from agrosmart.operacao.metricas import EstadoOperacao, gravar_status, medir_landing
from agrosmart.operacao.servidor import iniciar_servidor
from agrosmart.orquestracao import executar, novo_execucao_id


def _gold_vazio(cfg: Config) -> bool:
    from agrosmart.storage import criar_armazenamento

    lake = criar_armazenamento(cfg)
    lake.preparar()
    return lake.contar(Camada.GOLD) == 0


def operar(
    config: Config | None = None,
    *,
    intervalo_s: float = 60.0,
    ciclos: int | None = None,
    publicar: bool = True,
    broker: str = "localhost",
    porta: int = 1883,
    porta_metricas: int | None = 8000,
    incremental: bool = False,
    carga_inicial: str = "auto",  # auto | sempre | nunca
    parar: threading.Event | None = None,
) -> EstadoOperacao:
    """Roda ciclos até ``ciclos`` (ou até SIGTERM/SIGINT) e devolve o estado final."""
    cfg = config or carregar_config()
    logger = obter_logger("operacao")
    versao_processo = "v2" if incremental else "v1"
    estado = EstadoOperacao(
        intervalo_s=intervalo_s,
        modo="incremental" if incremental else "completo",
        versao_processo=versao_processo,
        publicar=publicar,
    )
    parar = parar or threading.Event()

    def _sinal(signum: int, _frame: Any) -> None:
        logger.warning("sinal %s recebido: encerrando após o ciclo atual", signum)
        parar.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _sinal)
        signal.signal(signal.SIGINT, _sinal)

    servidor = None
    if porta_metricas:
        host = os.getenv("AGROSMART_METRICAS_HOST", "127.0.0.1")
        servidor = iniciar_servidor(estado, porta_metricas, host=host)
        logger.info("métricas em http://%s:%d/metrics · saúde em /health", host, porta_metricas)

    pasta_sensores = cfg.dir_landing_fonte("sensores")

    def _caso(prefixo: str, **kwargs: Any) -> dict[str, Any]:
        execucao_id = novo_execucao_id(prefixo)
        abrir_execucao(
            execucao_id,
            origem="worker",
            versao_processo=versao_processo,
            tipo_caso="carga_inicial" if prefixo == "carga" else "ciclo",
        )
        try:
            resumo = executar(
                cfg,
                ia=False,
                automacao=True,
                publicar=publicar,
                broker=broker,
                porta=porta,
                **kwargs,
            )
        except Exception as erro:  # noqa: BLE001 - o serviço não pode morrer por um ciclo
            logger.exception("ciclo %s abortado", execucao_id)
            resumo = {
                "execucao_id": execucao_id,
                "sucesso": False,
                "duracao_s": 0.0,
                "etapas": [
                    {
                        "nome": "orquestracao",
                        "status": "erro",
                        "duracao_s": 0.0,
                        "erro": f"{type(erro).__name__}: {erro}",
                        "resumo": {},
                    }
                ],
            }
        finally:
            fechar_execucao()
        estado.registrar_ciclo(resumo, medir_landing(pasta_sensores))
        gravar_status(estado, cfg.dir_outputs)
        etapas = " ".join(f"{e['nome']}={e['status']}" for e in resumo["etapas"])
        logger.info(
            "%s %s em %.1fs · %s",
            resumo["execucao_id"],
            "ok" if resumo["sucesso"] else "FALHOU",
            resumo["duracao_s"],
            etapas,
        )
        return resumo

    try:
        precisa_carga = carga_inicial == "sempre"
        if carga_inicial == "auto":
            try:
                precisa_carga = _gold_vazio(cfg)
            except Exception as erro:  # noqa: BLE001 - lake fora do ar: o ciclo vai registrar
                logger.warning("não consegui verificar o gold (%s); sigo sem carga inicial", erro)
        if precisa_carga:
            estado.mudar_fase("carga_inicial")
            logger.info("gold vazio: carga inicial com o histórico simulado das seis fontes")
            _caso("carga", simular_fontes=True)

        executados = 0
        while not parar.is_set() and (ciclos is None or executados < ciclos):
            estado.mudar_fase("ciclo")
            inicio = time.monotonic()
            _caso("ciclo", simular_fontes=False, incremental=incremental)
            executados += 1
            if ciclos is not None and executados >= ciclos:
                break
            estado.mudar_fase("aguardando")
            gravar_status(estado, cfg.dir_outputs)
            espera = max(0.0, intervalo_s - (time.monotonic() - inicio))
            parar.wait(espera)
    finally:
        estado.mudar_fase("parado")
        gravar_status(estado, cfg.dir_outputs)
        if servidor is not None:
            servidor.shutdown()
    return estado
