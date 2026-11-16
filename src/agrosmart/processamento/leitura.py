"""Leitura da camada bruta: dos objetos do bronze a um DataFrame *todo texto*.

Regra do módulo: **nada é interpretado aqui**. Todo valor entra como string
(ou nulo), com as colunas do contrato mais os metadados do lote vindos do
manifest. Quem transforma texto em tipo é ``tipagem``; quem julga valores é
``qualidade``. Separar as três coisas é o que permite dizer, para cada
registro reprovado, *em qual porta* ele caiu.

Formatos suportados, escolhidos pelo modo de ingestão do contrato:

* ``batch``        CSV com cabeçalho — uma linha por registro
* ``streaming``    JSONL — um objeto por linha
* ``api_pull``     envelope da API de clima — as séries ``daily`` viram linhas
                   e os nomes da API são traduzidos para os campos do contrato
* ``watch_folder`` sidecar JSON — um registro por lote, com os campos derivados
                   (ponteiro para o binário, checksum, tamanho) vindos do manifest
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from typing import Any

import polars as pl

from agrosmart.config import Camada, Config
from agrosmart.contratos import Contrato, ModoIngestao
from agrosmart.log import obter_logger
from agrosmart.storage import Armazenamento

# Tradução das séries da API meteorológica para os campos do contrato ``clima``.
MAPA_API_CLIMA: dict[str, str] = {
    "data": "time",
    "temp_max_c": "temperature_2m_max",
    "temp_min_c": "temperature_2m_min",
    "temp_media_c": "temperature_2m_mean",
    "umidade_relativa_pct": "relative_humidity_2m_mean",
    "precipitacao_mm": "precipitation_sum",
    "velocidade_vento_ms": "wind_speed_10m_max",
    "radiacao_mj_m2": "shortwave_radiation_sum",
    "eto_mm": "et0_fao_evapotranspiration",
}

#: Colunas de proveniência anexadas a cada registro a partir do manifest do lote.
COLUNAS_METADADOS: dict[str, pl.DataType] = {
    "_batch_id": pl.Utf8,
    "_ingested_at": pl.Datetime("us", "UTC"),
    "_janela_fim": pl.Datetime("us", "UTC"),
    "_particao": pl.Date,
    "_schema_version": pl.Utf8,
    "_versao_lote": pl.Int64,
    "_linha": pl.Int64,
}


def _texto(valor: Any) -> str | None:
    """Normaliza qualquer valor JSON para texto — o tipo vem depois, do contrato."""
    if valor is None:
        return None
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, str):
        return valor if valor != "" else None
    return str(valor)


def _instante(texto: str) -> datetime:
    instante = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    return instante if instante.tzinfo else instante.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Parsers por formato
# ---------------------------------------------------------------------------


def _linhas_csv(dados: bytes) -> list[dict[str, str | None]]:
    leitor = csv.DictReader(io.StringIO(dados.decode("utf-8")))
    return [{chave: _texto(valor) for chave, valor in linha.items()} for linha in leitor]


def _linhas_jsonl(dados: bytes) -> list[dict[str, str | None]]:
    linhas: list[dict[str, str | None]] = []
    for bruta in dados.decode("utf-8").splitlines():
        if not bruta.strip():
            continue
        registro = json.loads(bruta)
        linhas.append({chave: _texto(valor) for chave, valor in registro.items()})
    return linhas


def _linhas_clima(dados: bytes) -> list[dict[str, str | None]]:
    envelope = json.loads(dados.decode("utf-8"))
    daily: dict[str, list[Any]] = envelope["daily"]
    linhas: list[dict[str, str | None]] = []
    for indice in range(len(daily["time"])):
        linha: dict[str, str | None] = {
            "estacao_id": _texto(envelope.get("estacao_id")),
            "fazenda_id": _texto(envelope.get("fazenda_id")),
            "origem_api": _texto(envelope.get("provedor")),
        }
        for campo, serie in MAPA_API_CLIMA.items():
            valores = daily.get(serie)
            linha[campo] = (
                _texto(valores[indice]) if valores is not None and indice < len(valores) else None
            )
        linhas.append(linha)
    return linhas


def _linha_sidecar(dados: bytes, manifest: dict[str, Any]) -> list[dict[str, str | None]]:
    registro = {chave: _texto(valor) for chave, valor in json.loads(dados.decode("utf-8")).items()}
    anexo = next((a for a in manifest["arquivos"] if a["papel"] == "anexo"), None)
    if anexo is not None:
        registro["arquivo_bruto"] = anexo["chave"]
        registro["checksum_sha256"] = anexo["checksum_sha256"]
        registro["tamanho_bytes"] = str(anexo["bytes"])
    return [registro]


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def listar_manifests(
    armazenamento: Armazenamento, fonte: str, paralelismo: int = 8
) -> list[dict[str, Any]]:
    """Todos os manifests de uma fonte, em ordem de partição e janela."""
    chaves = [o.chave for o in armazenamento.listar(Camada.BRONZE, f"_manifests/{fonte}/")]
    with ThreadPoolExecutor(max_workers=max(1, paralelismo)) as executor:
        manifests = list(executor.map(lambda c: armazenamento.ler_json(Camada.BRONZE, c), chaves))
    manifests.sort(key=lambda m: (m["particao"], m["janela"]["inicio"], m["batch_id"]))
    return manifests


def ler_lote(
    contrato: Contrato, armazenamento: Armazenamento, manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    """Registros de um lote como texto, com as colunas de proveniência."""
    principal = next(a for a in manifest["arquivos"] if a["papel"] == "principal")
    dados = armazenamento.ler_bytes(Camada.BRONZE, principal["chave"])
    modo = contrato.ingestao.modo

    if modo is ModoIngestao.BATCH:
        registros = _linhas_csv(dados)
    elif modo is ModoIngestao.STREAMING:
        registros = _linhas_jsonl(dados)
    elif modo is ModoIngestao.API_PULL:
        registros = _linhas_clima(dados)
    else:
        registros = _linha_sidecar(dados, manifest)

    metadados = {
        "_batch_id": manifest["batch_id"],
        "_ingested_at": _instante(manifest["ingerido_em"]),
        "_janela_fim": _instante(manifest["janela"]["fim"]),
        "_particao": date.fromisoformat(manifest["particao"]),
        "_schema_version": manifest["schema_version"],
        "_versao_lote": int(manifest.get("versao", 1)),
    }
    campos = set(c.nome for c in contrato.campos)
    saida: list[dict[str, Any]] = []
    for numero, registro in enumerate(registros, start=1):
        # Só o que o contrato conhece; o coletor já rejeitou chaves estranhas.
        linha: dict[str, Any] = {campo: registro.get(campo) for campo in campos}
        linha.update(metadados)
        linha["_linha"] = numero
        saida.append(linha)
    return saida


def ler_fonte_bronze(
    contrato: Contrato,
    armazenamento: Armazenamento,
    config: Config,
    filtro: Callable[[dict[str, Any]], bool] | None = None,
) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    """DataFrame todo texto com todos os lotes de uma fonte, mais os manifests lidos.

    Colunas: os campos do contrato (``Utf8``) e as de :data:`COLUNAS_METADADOS`.
    Uma fonte sem lotes devolve um DataFrame vazio com o schema certo. ``filtro``
    seleciona manifests (backfill por intervalo de partições).
    """
    logger = obter_logger(f"leitura.{contrato.fonte}")
    manifests = listar_manifests(armazenamento, contrato.fonte, config.paralelismo)
    if filtro is not None:
        manifests = [m for m in manifests if filtro(m)]

    with ThreadPoolExecutor(max_workers=max(1, config.paralelismo)) as executor:
        lotes = list(executor.map(lambda m: ler_lote(contrato, armazenamento, m), manifests))

    registros = [linha for lote in lotes for linha in lote]
    schema: dict[str, pl.DataType] = {c.nome: pl.Utf8 for c in contrato.campos}
    schema.update(COLUNAS_METADADOS)

    if registros:
        df = pl.from_dicts(registros, schema=schema)
    else:
        df = pl.DataFrame(schema=schema)

    logger.info("%d lotes · %d registros lidos do bronze", len(manifests), df.height)
    return df, manifests
