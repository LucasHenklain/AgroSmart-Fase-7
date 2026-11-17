"""Coletores de ingestão: landing → camada bruta.

Um coletor por modo de ingestão declarado nos contratos:

    streaming     sensores (micro-batch de 15 min, tolera atraso e desordem)
    api_pull      clima (janela D-1 fechada, resposta gravada íntegra)
    watch_folder  imagens e laudos (binário + sidecar, idempotência por checksum)
    batch         ERP (carga completa ou incremental, checagem de schema na porta)

Todos compartilham o protocolo de :class:`agrosmart.ingestao.base.Coletor`:
gravam o dado **sem transformar**, anexam metadados de ingestão ao objeto e
escrevem um manifest por lote em ``_manifests/`` — que é o que a reconciliação
e a linhagem usam depois.

Responsável: E1 (Engenharia de Dados).
"""

from __future__ import annotations

import json
import re
import shutil
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agrosmart.config import Camada, Config, carregar_config
from agrosmart.contratos import Contrato, ModoIngestao, contratos_ativos, ordenar_por_dependencia
from agrosmart.ingestao.api_pull import ColetorApiPull
from agrosmart.ingestao.base import Coletor, ResultadoIngestao
from agrosmart.ingestao.batch import ColetorBatch
from agrosmart.ingestao.streaming import ColetorStreaming
from agrosmart.ingestao.watch_folder import ColetorPastaObservada
from agrosmart.log import Evento, obter_logger, registrar_evento
from agrosmart.storage import Armazenamento, criar_armazenamento

COLETORES: dict[ModoIngestao, type[Coletor]] = {
    ModoIngestao.BATCH: ColetorBatch,
    ModoIngestao.STREAMING: ColetorStreaming,
    ModoIngestao.API_PULL: ColetorApiPull,
    ModoIngestao.WATCH_FOLDER: ColetorPastaObservada,
}


def construir_coletor(contrato: Contrato, armazenamento: Armazenamento, config: Config) -> Coletor:
    """O coletor é escolhido pelo ``ingestao.modo`` do contrato — não por nome de fonte."""
    classe = COLETORES[contrato.ingestao.modo]
    return classe(contrato, armazenamento, config)


def ingerir(
    config: Config | None = None,
    *,
    fontes: Iterable[str] | None = None,
    forcar: bool = False,
    exportar_exemplos: bool = True,
) -> dict[str, Any]:
    """Executa os coletores na ordem de dependência dos contratos e devolve o resumo."""
    cfg = config or carregar_config()
    logger = obter_logger("ingestao")
    contratos = contratos_ativos()
    selecao = set(fontes) if fontes is not None else set(contratos)
    desconhecidas = selecao - set(contratos)
    if desconhecidas:
        raise ValueError(
            f"fontes desconhecidas: {sorted(desconhecidas)}; válidas: {sorted(contratos)}"
        )

    armazenamento = criar_armazenamento(cfg)
    armazenamento.preparar()
    inicio = time.perf_counter()
    resultados: list[ResultadoIngestao] = []

    for contrato in ordenar_por_dependencia(contratos):
        if contrato.fonte not in selecao:
            continue
        coletor = construir_coletor(contrato, armazenamento, cfg)
        resultados.append(coletor.executar(forcar=forcar))

    exemplos: dict[str, list[str]] = {}
    if exportar_exemplos:
        exemplos = exportar_exemplos_bronze(cfg, armazenamento, resultados)

    resumo = {
        "backend": armazenamento.nome,
        "fontes": [r.para_dict() for r in resultados],
        "totais": {
            "lotes": sum(len(r.lotes) for r in resultados),
            "gravados": sum(r.contar("gravado") for r in resultados),
            "atualizados": sum(r.contar("atualizado") for r in resultados),
            "pulados": sum(r.contar("pulado") for r in resultados),
            "rejeitados": sum(r.contar("rejeitado") for r in resultados),
            "registros": sum(r.registros for r in resultados),
            "bytes": sum(r.bytes for r in resultados),
        },
        # Fase 7: partições (dias) que receberam lote novo nesta execução — é o que o
        # ciclo incremental usa para reprocessar só o intervalo afetado no silver.
        "datas_novas": datas_com_lote_novo(resultados),
        "objetos_no_bronze": armazenamento.contar(Camada.BRONZE),
        "objetos_na_quarentena": armazenamento.contar(Camada.QUARENTENA),
        "exemplos": exemplos,
        "duracao_s": round(time.perf_counter() - inicio, 2),
    }

    cfg.dir_outputs.mkdir(parents=True, exist_ok=True)
    (cfg.dir_outputs / "ingestao.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    totais = resumo["totais"]
    registrar_evento(
        Evento(
            etapa="ingestao",
            acao="concluida",
            camada=Camada.BRONZE.value,
            status="alerta" if totais["rejeitados"] else "ok",
            mensagem=f"{len(resultados)} fontes · {totais['lotes']} lotes",
            metricas={k: v for k, v in totais.items() if k != "bytes"}
            | {"duracao_s": resumo["duracao_s"]},
        ),
        cfg,
    )
    logger.info(
        "bronze com %d objetos · quarentena com %d",
        resumo["objetos_no_bronze"],
        resumo["objetos_na_quarentena"],
    )
    return resumo


_PADRAO_PARTICAO = re.compile(r"ano=(\d{4})/mes=(\d{2})/dia=(\d{2})")


def datas_com_lote_novo(resultados: Iterable[ResultadoIngestao]) -> list[str]:
    """Dias (AAAA-MM-DD) das partições que receberam lote gravado ou atualizado."""
    dias: set[str] = set()
    for resultado in resultados:
        for lote in resultado.lotes:
            if lote.status not in {"gravado", "atualizado"} or not lote.chave:
                continue
            achado = _PADRAO_PARTICAO.search(lote.chave)
            if achado:
                dias.add("-".join(achado.groups()))
    return sorted(dias)


def exportar_exemplos_bronze(
    config: Config, armazenamento: Armazenamento, resultados: list[ResultadoIngestao]
) -> dict[str, list[str]]:
    """Copia, por fonte, um lote representativo para ``data/exemplos/bronze/``.

    É o artefato "exemplo de dados ingeridos" da Entrega 2: o arquivo bruto
    exatamente como está no lake, ao lado do manifest que o descreve.
    """
    raiz = config.dir_exemplos / "bronze"
    exportados: dict[str, list[str]] = {}
    for resultado in resultados:
        candidatos = [
            r for r in resultado.lotes if r.status != "rejeitado" and r.registros > 0 and r.chave
        ]
        if not candidatos:
            continue
        escolhido = candidatos[0]
        pasta = raiz / resultado.fonte
        if pasta.exists():
            shutil.rmtree(pasta)
        pasta.mkdir(parents=True, exist_ok=True)

        manifest_chave = _chave_manifest_de(escolhido.chave, resultado.fonte, escolhido.batch_id)
        manifest = armazenamento.ler_json(Camada.BRONZE, manifest_chave)
        arquivos: list[str] = []
        for descritor in manifest["arquivos"]:
            destino = pasta / descritor["nome"]
            destino.write_bytes(armazenamento.ler_bytes(Camada.BRONZE, descritor["chave"]))
            arquivos.append(destino.name)
        (pasta / "_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        arquivos.append("_manifest.json")
        exportados[resultado.fonte] = arquivos
    return exportados


def _chave_manifest_de(chave_objeto: str, fonte: str, batch_id: str) -> str:
    """``fonte/<partição>/arquivo`` → ``_manifests/fonte/<partição>/<batch_id>.json``."""
    particao = Path(chave_objeto).parent.as_posix().removeprefix(f"{fonte}/")
    return f"_manifests/{fonte}/{particao}/{batch_id}.json"
