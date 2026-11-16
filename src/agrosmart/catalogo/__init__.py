"""Catálogo de dados, dicionário e linhagem — gerados, nunca escritos à mão.

``construir_catalogo`` percorre o lake e produz ``outputs/catalogo.json``: o
que existe em cada camada, com schema, contagens, partições, lotes de origem
e linhagem (bronze → silver → gold). ``gerar_dicionario`` transforma os
contratos e as definições do gold em ``docs/dicionario-de-dados.md``.

Se o dicionário divergir do dado, o bug está em quem gerou — não há uma
segunda fonte da verdade para ficar defasada.

Responsável: E2 (Qualidade e Analytics).
"""

from __future__ import annotations

import io
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from agrosmart.config import Camada, Config, carregar_config
from agrosmart.contratos import Contrato, contratos_ativos, ordenar_por_dependencia
from agrosmart.log import obter_logger
from agrosmart.processamento.gold import TABELAS, TabelaGold
from agrosmart.processamento.silver import COLUNAS_PROVENIENCIA_SILVER
from agrosmart.storage import Armazenamento, criar_armazenamento

_DESCRICAO_PROVENIENCIA = {
    "_batch_id": "lote do bronze que originou o registro",
    "_ingested_at": "instante da ingestão no bronze",
    "_schema_version": "contrato (fonte.versão) que validou o registro",
    "_alertas": "ids das regras de alerta acionadas pelo registro",
}


def _particoes(chaves: list[str]) -> list[str]:
    encontradas: set[str] = set()
    for chave in chaves:
        hive = [p for p in chave.split("/") if "=" in p]
        if hive:
            encontradas.add("/".join(hive))
    return sorted(encontradas)


def _inventario_tabela(
    armazenamento: Armazenamento, camada: Camada, tabela: str, coluna_evento: str | None
) -> dict[str, Any]:
    objetos = [
        o for o in armazenamento.listar(camada, f"{tabela}/") if o.chave.endswith(".parquet")
    ]
    if not objetos:
        return {"existe": False}

    partes: list[pl.DataFrame] = []
    linhagem: dict[str, dict[str, Any]] = {}
    for objeto in objetos:
        df = pl.read_parquet(io.BytesIO(armazenamento.ler_bytes(camada, objeto.chave)))
        partes.append(df)
        if "_batch_id" in df.columns:
            lotes = df.get_column("_batch_id").unique().sort().to_list()
            linhagem[objeto.chave] = {
                "registros": df.height,
                "lotes": len(lotes),
                "amostra_lotes": lotes[:3],
            }
    completo = pl.concat(partes, how="vertical_relaxed")

    inventario: dict[str, Any] = {
        "existe": True,
        "registros": completo.height,
        "colunas": completo.width,
        "arquivos": len(objetos),
        "bytes": sum(o.tamanho_bytes for o in objetos),
        "particoes": _particoes([o.chave for o in objetos]),
        "schema": {nome: str(dtype) for nome, dtype in completo.schema.items()},
    }
    if "_batch_id" in completo.columns:
        inventario["lotes_distintos"] = completo.get_column("_batch_id").n_unique()
        inventario["linhagem_por_arquivo"] = linhagem
    if coluna_evento and coluna_evento in completo.columns:
        serie = completo.get_column(coluna_evento).drop_nulls()
        if not serie.is_empty():
            inventario["primeiro_evento"] = str(serie.min())
            inventario["ultimo_evento"] = str(serie.max())
    return inventario


def _inventario_bronze(armazenamento: Armazenamento, fonte: str) -> dict[str, Any]:
    dados = list(armazenamento.listar(Camada.BRONZE, f"{fonte}/"))
    manifests = list(armazenamento.listar(Camada.BRONZE, f"_manifests/{fonte}/"))
    return {
        "objetos": len(dados),
        "bytes": sum(o.tamanho_bytes for o in dados),
        "lotes": len(manifests),
        "particoes": _particoes([o.chave for o in dados]),
    }


def _gold_que_consome(tabela_silver: str) -> list[str]:
    return [nome for nome, t in TABELAS.items() if tabela_silver in t.fontes]


def construir_catalogo(
    config: Config | None = None, armazenamento: Armazenamento | None = None
) -> dict[str, Any]:
    cfg = config or carregar_config()
    lake = armazenamento or criar_armazenamento(cfg)
    logger = obter_logger("catalogo")
    inicio = time.perf_counter()
    contratos = contratos_ativos()

    fontes: dict[str, Any] = {}
    for contrato in ordenar_por_dependencia(contratos):
        tabela = contrato.destino.silver_tabela
        silver = _inventario_tabela(
            lake, Camada.SILVER, tabela, contrato.particionamento.coluna_evento
        )
        quarentena = _inventario_tabela(lake, Camada.QUARENTENA, contrato.fonte, None)
        fontes[contrato.fonte] = {
            "contrato": contrato.nome_completo,
            "titulo": contrato.titulo,
            "tipo_dado": contrato.tipo_dado.value,
            "classificacao_lgpd": contrato.classificacao_lgpd.value,
            "campos_lgpd": [c.nome for c in contrato.campos_lgpd],
            "bronze": _inventario_bronze(lake, contrato.fonte),
            "silver": {"tabela": tabela, **silver},
            "quarentena": quarentena,
            "linhagem": {
                "bronze_lotes": silver.get("lotes_distintos", 0),
                "silver_tabela": tabela,
                "gold_tabelas": _gold_que_consome(tabela),
            },
            "retencao_dias": contrato.retencao.model_dump(),
        }

    gold: dict[str, Any] = {}
    for nome, definicao in TABELAS.items():
        inventario = _inventario_tabela(lake, Camada.GOLD, nome, None)
        gold[nome] = {
            "grao": definicao.grao,
            "descricao": definicao.descricao,
            "fontes_silver": list(definicao.fontes),
            **inventario,
        }

    catalogo = {
        "gerado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "backend": lake.nome,
        "camadas": {
            camada.value: {"proposito": camada.proposito, "objetos": lake.contar(camada)}
            for camada in Camada
        },
        "fontes": fontes,
        "gold": gold,
        "duracao_s": round(time.perf_counter() - inicio, 2),
    }
    cfg.dir_outputs.mkdir(parents=True, exist_ok=True)
    (cfg.dir_outputs / "catalogo.json").write_text(
        json.dumps(catalogo, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    logger.info(
        "catálogo: %d fontes · %d tabelas gold · %ss",
        len(fontes),
        sum(1 for g in gold.values() if g.get("existe")),
        catalogo["duracao_s"],
    )
    return catalogo


# ---------------------------------------------------------------------------
# Dicionário de dados
# ---------------------------------------------------------------------------


def _md_contrato(contrato: Contrato, inventario: dict[str, Any] | None) -> list[str]:
    linhas = [
        f"## {contrato.destino.silver_tabela} — {contrato.titulo}",
        "",
        f"Contrato `{contrato.nome_completo}` · tipo **{contrato.tipo_dado.value}** · "
        f"LGPD **{contrato.classificacao_lgpd.value}** · ingestão `{contrato.ingestao.modo.value}` "
        f"({contrato.ingestao.janela}) · SLA {contrato.ingestao.sla_freshness_min} min · "
        f"dedup `{contrato.dedup.estrategia}` · chave `{', '.join(contrato.chave_natural)}` · "
        f"partição por `{contrato.particionamento.coluna_evento}`",
        "",
        contrato.descricao.strip(),
        "",
    ]
    if inventario and inventario.get("existe"):
        periodo = (
            f", eventos de {inventario['primeiro_evento']} a {inventario['ultimo_evento']}"
            if "primeiro_evento" in inventario
            else ""
        )
        linhas += [
            f"No lake: **{inventario['registros']:,}** registros em {inventario['arquivos']} "
            f"arquivo(s), {inventario.get('lotes_distintos', 0)} lotes do bronze{periodo}.",
            "",
        ]
    linhas += [
        "| Campo | Tipo | Unidade | Obrig. | LGPD | Descrição |",
        "|---|---|---|---|---|---|",
    ]
    for campo in contrato.campos:
        lgpd = campo.lgpd or ""
        if campo.tratamento:
            lgpd = f"{lgpd} → {campo.tratamento}"
        linhas.append(
            f"| `{campo.nome}` | {campo.tipo.value} | {campo.unidade or ''} | "
            f"{'●' if campo.obrigatorio else ''} | {lgpd} | {campo.descricao} |"
        )
    for nome in COLUNAS_PROVENIENCIA_SILVER:
        linhas.append(f"| `{nome}` | proveniência | | | | {_DESCRICAO_PROVENIENCIA[nome]} |")
    linhas += [
        "",
        "**Regras de qualidade**",
        "",
        "| Id | Regra | Severidade | Campos / parâmetros | Justificativa |",
        "|---|---|---|---|---|",
    ]
    for regra in contrato.qualidade:
        alvo = ", ".join(regra.campos) if regra.campos else ""
        if regra.regra == "comparacao":
            alvo = f"{regra.campo_a} {regra.operador} {regra.campo_b}"
        params = ", ".join(
            f"{k}={v}"
            for k, v in regra.parametros.items()
            if k not in {"campo_a", "campo_b", "operador"}
        )
        detalhe = " · ".join(p for p in (alvo, params) if p)
        justificativa = " ".join(regra.justificativa.split())
        linhas.append(
            f"| {regra.id} | {regra.regra} | {regra.severidade.value} | {detalhe} | "
            f"{justificativa} |"
        )
    linhas.append("")
    return linhas


def _md_gold(definicao: TabelaGold, inventario: dict[str, Any] | None) -> list[str]:
    linhas = [
        f"## {definicao.nome}",
        "",
        f"**Grão:** {definicao.grao}",
        "",
        definicao.descricao,
        "",
    ]
    if definicao.fontes:
        linhas += [f"Fontes silver: {', '.join(f'`{f}`' for f in definicao.fontes)}", ""]
    if inventario and inventario.get("existe"):
        linhas += [
            f"No lake: **{inventario['registros']:,}** registros, {inventario['colunas']} colunas.",
            "",
        ]
    linhas += ["| Coluna | Tipo | Descrição |", "|---|---|---|"]
    schema = (inventario or {}).get("schema", {})
    for coluna, descricao in definicao.colunas.items():
        linhas.append(f"| `{coluna}` | {schema.get(coluna, '')} | {descricao} |")
    linhas.append("")
    return linhas


def gerar_dicionario(config: Config | None = None, catalogo: dict[str, Any] | None = None) -> Path:
    """Escreve ``docs/dicionario-de-dados.md`` a partir dos contratos, do gold e do catálogo."""
    cfg = config or carregar_config()
    contratos = contratos_ativos()
    inventarios_silver = {f: d["silver"] for f, d in (catalogo or {}).get("fontes", {}).items()}
    inventarios_gold = (catalogo or {}).get("gold", {})

    linhas = [
        "# Dicionário de dados",
        "",
        "> **Arquivo gerado** por `agrosmart catalogo` a partir de `contratos/*.yaml` e das",
        "> definições do gold em `src/agrosmart/processamento/gold.py`. Não edite à mão:",
        "> altere o contrato ou a definição e regenere.",
        "",
        f"Gerado em {datetime.now(UTC).isoformat(timespec='seconds')}"
        + (f" a partir do catálogo de {catalogo['gerado_em']}." if catalogo else "."),
        "",
        "## Camadas",
        "",
        "| Camada | Propósito |",
        "|---|---|",
    ]
    for camada in Camada:
        linhas.append(f"| **{camada.value}** ({camada.rotulo}) | {camada.proposito} |")
    linhas += ["", "---", "", "# Silver — uma tabela por fonte", ""]
    for contrato in ordenar_por_dependencia(contratos):
        linhas += _md_contrato(contrato, inventarios_silver.get(contrato.fonte))
    linhas += ["---", "", "# Gold — modelo dimensional e indicadores", ""]
    for nome, definicao in TABELAS.items():
        linhas += _md_gold(definicao, inventarios_gold.get(nome))

    destino = cfg.dir_docs / "dicionario-de-dados.md"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join(linhas), encoding="utf-8")
    return destino
