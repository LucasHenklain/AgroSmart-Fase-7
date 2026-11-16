"""Tipagem pelo contrato: texto → tipo declarado, sem perder o que falhou.

Toda coluna sai da leitura como texto. Aqui cada campo é convertido para o
tipo do contrato de forma **não estrita**: o que não converte vira nulo, e o
nome do campo entra na lista ``_falha_tipagem`` daquele registro. Um registro
com essa lista não vazia é reprovado pela regra de sistema ``TIP-01`` — o
motor de qualidade a trata como qualquer regra crítica, com a diferença de
que ela não precisa constar no contrato para existir.
"""

from __future__ import annotations

import polars as pl

from agrosmart.contratos import Contrato, TipoCampo

REGRA_TIPAGEM = "TIP-01"

_DTYPES: dict[TipoCampo, pl.DataType] = {
    TipoCampo.STRING: pl.Utf8,
    TipoCampo.INT: pl.Int64,
    TipoCampo.FLOAT: pl.Float64,
    TipoCampo.BOOL: pl.Boolean,
    TipoCampo.DATE: pl.Date,
    TipoCampo.TIMESTAMP: pl.Datetime("us", "UTC"),
}


def dtype_polars(tipo: TipoCampo) -> pl.DataType:
    return _DTYPES[tipo]


def _expressao(nome: str, tipo: TipoCampo) -> pl.Expr:
    coluna = pl.col(nome).str.strip_chars()
    if tipo is TipoCampo.STRING:
        return coluna
    if tipo is TipoCampo.INT:
        # "12.0" vindo de um CSV ainda é inteiro; "12.5" não é.
        return (
            pl.when(coluna.str.contains(r"^-?\d+(\.0+)?$"))
            .then(coluna.str.replace(r"\.0+$", "").cast(pl.Int64, strict=False))
            .otherwise(None)
        )
    if tipo is TipoCampo.FLOAT:
        return coluna.cast(pl.Float64, strict=False)
    if tipo is TipoCampo.BOOL:
        minusculo = coluna.str.to_lowercase()
        return (
            pl.when(minusculo.is_in(["true", "1", "sim", "yes"]))
            .then(True)
            .when(minusculo.is_in(["false", "0", "nao", "não", "no"]))
            .then(False)
            .otherwise(None)
        )
    if tipo is TipoCampo.DATE:
        return coluna.str.to_date("%Y-%m-%d", strict=False)
    # TIMESTAMP: aceita ``Z`` e deslocamentos com ou sem dois-pontos.
    normalizado = coluna.str.replace(r"Z$", "+0000").str.replace(
        r"([+-]\d{2}):(\d{2})$", "${1}${2}"
    )
    return normalizado.str.to_datetime("%Y-%m-%dT%H:%M:%S%z", strict=False).dt.convert_time_zone(
        "UTC"
    )


def tipar(df: pl.DataFrame, contrato: Contrato) -> pl.DataFrame:
    """Converte as colunas do contrato e anexa ``_falha_tipagem`` (lista de campos).

    Colunas que não estão no contrato (metadados ``_*``) passam intocadas.
    """
    if df.is_empty():
        return df.with_columns(pl.lit([], dtype=pl.List(pl.Utf8)).alias("_falha_tipagem"))

    convertidas: list[pl.Expr] = []
    falhas: list[pl.Expr] = []
    for campo in contrato.campos:
        if campo.nome not in df.columns:
            continue
        origem = pl.col(campo.nome)
        tipada = _expressao(campo.nome, campo.tipo)
        convertidas.append(tipada.alias(campo.nome))
        falhas.append(
            pl.when(origem.is_not_null() & tipada.is_null())
            .then(pl.lit(campo.nome))
            .otherwise(None)
        )

    return df.with_columns(
        [*convertidas, pl.concat_list(falhas).list.drop_nulls().alias("_falha_tipagem")]
    )
