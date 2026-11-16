"""Motor de qualidade: executa as regras do contrato sobre um DataFrame tipado.

Entrada: o DataFrame que saiu de ``processamento.tipagem`` — colunas do
contrato já tipadas, mais os metadados do lote (``_batch_id``, ``_ingested_at``,
``_janela_fim``…) e a lista ``_falha_tipagem``.

Saída: três DataFrames e um relatório.

* **aprovados** — vão para o silver, com a coluna ``_alertas`` (ids das regras
  de alerta que o registro acionou);
* **quarentena** — reprovados por regra crítica, duplicatas e falhas de
  tipagem, com ``_regras_violadas`` e ``_motivo`` legível;
* **substituídos** — versões anteriores de registros reenviados (só em fontes
  com ``dedup.estrategia = versao``); não são defeito, mas são contados para a
  reconciliação fechar.

Ordem das etapas, e por que ela importa:

1. regras de valor, registro a registro, sobre **todos** os registros — um
   snapshot antigo com defeito é reprovado mesmo que exista snapshot mais novo;
2. deduplicação só entre os aprovados — se a versão mais recente foi reprovada,
   a última versão *válida* é a que fica no silver;
3. frescor, em nível de tabela e de entidade, sobre o que sobrou.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import polars as pl

from agrosmart.contratos import Contrato, RegraQualidade, Severidade
from agrosmart.log import obter_logger
from agrosmart.processamento.tipagem import REGRA_TIPAGEM

REGRA_DUPLICATA_PADRAO = "DUP-01"

_OPERADORES = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
}


# ---------------------------------------------------------------------------
# Estruturas de resultado
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ResultadoRegra:
    id: str
    regra: str
    severidade: str
    campos: list[str]
    avaliados: int
    violacoes: int
    parametros: dict[str, Any] = field(default_factory=dict)
    observacao: str | None = None

    def para_dict(self) -> dict[str, Any]:
        saida: dict[str, Any] = {
            "id": self.id,
            "regra": self.regra,
            "severidade": self.severidade,
            "campos": self.campos,
            "avaliados": self.avaliados,
            "violacoes": self.violacoes,
            "parametros": self.parametros,
        }
        if self.observacao:
            saida["observacao"] = self.observacao
        return saida


@dataclass(slots=True)
class AlertaTabela:
    regra: str
    tipo: str  # atrasado | lacuna
    entidade: str | None
    detalhe: dict[str, Any]

    def para_dict(self) -> dict[str, Any]:
        return {"regra": self.regra, "tipo": self.tipo, "entidade": self.entidade, **self.detalhe}


@dataclass(slots=True)
class ResultadoQualidade:
    fonte: str
    tabela: str
    lidos: int
    aprovados: pl.DataFrame
    quarentena: pl.DataFrame
    substituidos: pl.DataFrame
    regras: list[ResultadoRegra]
    alertas_tabela: list[AlertaTabela]
    falhas_tipagem: int
    duplicatas: int

    @property
    def n_aprovados(self) -> int:
        return self.aprovados.height

    @property
    def n_quarentena(self) -> int:
        return self.quarentena.height

    @property
    def n_substituidos(self) -> int:
        return self.substituidos.height

    @property
    def registros_com_alerta(self) -> int:
        if self.aprovados.is_empty() or "_alertas" not in self.aprovados.columns:
            return 0
        return int(self.aprovados.select(pl.col("_alertas").list.len().gt(0).sum()).item())

    def quarentena_por_regra(self) -> dict[str, int]:
        if self.quarentena.is_empty():
            return {}
        contagem = (
            self.quarentena.select(
                pl.col("_regras_violadas").explode(empty_as_null=False).alias("regra")
            )
            .group_by("regra")
            .len()
            .sort("regra")
        )
        return {linha["regra"]: int(linha["len"]) for linha in contagem.iter_rows(named=True)}

    def resumo(self) -> dict[str, Any]:
        return {
            "fonte": self.fonte,
            "tabela": self.tabela,
            "lidos": self.lidos,
            "aprovados": self.n_aprovados,
            "quarentena": self.n_quarentena,
            "substituidos": self.n_substituidos,
            "duplicatas": self.duplicatas,
            "falhas_tipagem": self.falhas_tipagem,
            "registros_com_alerta": self.registros_com_alerta,
            "quarentena_por_regra": self.quarentena_por_regra(),
            "regras": [r.para_dict() for r in self.regras],
            "alertas_tabela": [a.para_dict() for a in self.alertas_tabela],
        }


# ---------------------------------------------------------------------------
# Expressões por tipo de regra
# ---------------------------------------------------------------------------


def _limite_futuro(regra: RegraQualidade, instante_referencia: datetime) -> pl.Expr:
    tolerancia = timedelta(minutes=regra.tolerancia_min or 0)
    if regra.referencia == "lote":
        return pl.col("_janela_fim") + pl.duration(minutes=regra.tolerancia_min or 0)
    return pl.lit(instante_referencia + tolerancia).cast(pl.Datetime("us", "UTC"))


def _expressao_violacao(
    regra: RegraQualidade,
    df: pl.DataFrame,
    referencias: dict[str, pl.DataFrame],
    instante_referencia: datetime,
) -> tuple[pl.Expr | None, str | None]:
    """Expressão booleana (True = violou) ou ``None`` quando não é avaliável."""
    if regra.regra == "nao_nulo":
        return pl.any_horizontal([pl.col(c).is_null() for c in regra.campos]), None

    if regra.regra == "intervalo":
        partes: list[pl.Expr] = []
        for campo in regra.campos:
            coluna = pl.col(campo)
            condicao = pl.lit(False)
            if regra.min is not None:
                condicao = condicao | (coluna < regra.min)
            if regra.max is not None:
                condicao = condicao | (coluna > regra.max)
            partes.append(coluna.is_not_null() & condicao)
        return pl.any_horizontal(partes), None

    if regra.regra == "dominio":
        valores = list(regra.valores or [])
        return (
            pl.any_horizontal(
                [
                    pl.col(c).is_not_null() & ~pl.col(c).cast(pl.Utf8).is_in(valores)
                    for c in regra.campos
                ]
            ),
            None,
        )

    if regra.regra == "regex":
        padrao = regra.padrao or ""
        return (
            pl.any_horizontal(
                [
                    pl.col(c).is_not_null() & ~pl.col(c).cast(pl.Utf8).str.contains(padrao)
                    for c in regra.campos
                ]
            ),
            None,
        )

    if regra.regra == "nao_futuro":
        limite = _limite_futuro(regra, instante_referencia)
        partes = []
        for campo in regra.campos:
            coluna = pl.col(campo)
            if df.schema[campo] == pl.Date:
                partes.append(coluna.is_not_null() & (coluna > limite.dt.date()))
            else:
                partes.append(coluna.is_not_null() & (coluna > limite))
        return pl.any_horizontal(partes), None

    if regra.regra == "referencia":
        tabela = referencias.get(regra.tabela_referencia or "")
        if tabela is None or regra.campo_referencia not in tabela.columns:
            return (
                None,
                f"tabela de referência {regra.tabela_referencia!r} indisponível nesta execução",
            )
        validos = tabela.get_column(regra.campo_referencia).drop_nulls().unique().to_list()
        return (
            pl.any_horizontal(
                [pl.col(c).is_not_null() & ~pl.col(c).is_in(validos) for c in regra.campos]
            ),
            None,
        )

    if regra.regra == "comparacao":
        a, b = pl.col(regra.campo_a or ""), pl.col(regra.campo_b or "")
        comparar = _OPERADORES[regra.operador or "<="]
        return a.is_not_null() & b.is_not_null() & ~comparar(a, b), None

    # unicidade e freshness têm tratamento próprio
    return None, None


def _descrever(regra: RegraQualidade) -> str:
    alvo = ",".join(regra.campos) if regra.campos else ""
    if regra.regra == "comparacao":
        alvo = f"{regra.campo_a} {regra.operador} {regra.campo_b}"
    parametros = " ".join(
        f"{k}={v}"
        for k, v in regra.parametros.items()
        if k not in {"campo_a", "campo_b", "operador"}
    )
    return " ".join(p for p in (regra.id, regra.regra, alvo, parametros) if p)


# ---------------------------------------------------------------------------
# Deduplicação
# ---------------------------------------------------------------------------


def _deduplicar(
    aprovados: pl.DataFrame, contrato: Contrato
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Devolve ``(mantidos, duplicatas, substituidos)`` conforme a estratégia do contrato."""
    if aprovados.is_empty():
        return aprovados, aprovados.clear(), aprovados.clear()

    chave = list(contrato.chave_natural)
    if contrato.dedup.estrategia == "evento":
        ordenado = aprovados.sort(["_ingested_at", "_batch_id", "_linha"])
    else:
        ordenado = aprovados.sort(
            [
                contrato.dedup.campo_ordem or "_ingested_at",
                "_ingested_at",
                "_versao_lote",
                "_linha",
            ],
            descending=[True, True, True, True],
        )

    marcado = ordenado.with_columns(
        pl.int_range(0, pl.len()).over(chave).alias("_posicao"),
        pl.col("_batch_id").first().over(chave).alias("_batch_mantido"),
    )
    if contrato.dedup.estrategia == "evento":
        e_duplicata = pl.col("_posicao") > 0
        e_substituido = pl.lit(False)
    else:
        e_duplicata = (pl.col("_posicao") > 0) & (pl.col("_batch_id") == pl.col("_batch_mantido"))
        e_substituido = (pl.col("_posicao") > 0) & (pl.col("_batch_id") != pl.col("_batch_mantido"))

    marcado = marcado.with_columns(e_duplicata.alias("_dup"), e_substituido.alias("_sub"))
    auxiliares = ["_posicao", "_batch_mantido", "_dup", "_sub"]
    mantidos = marcado.filter(~pl.col("_dup") & ~pl.col("_sub")).drop(auxiliares)
    duplicatas = marcado.filter(pl.col("_dup")).drop(auxiliares)
    substituidos = marcado.filter(pl.col("_sub")).drop(auxiliares)
    return mantidos, duplicatas, substituidos


# ---------------------------------------------------------------------------
# Frescor
# ---------------------------------------------------------------------------


def _avaliar_frescor(
    aprovados: pl.DataFrame,
    contrato: Contrato,
    regra: RegraQualidade,
    instante_referencia: datetime,
) -> list[AlertaTabela]:
    if aprovados.is_empty():
        return [AlertaTabela(regra.id, "sem_dados", None, {"mensagem": "nenhum registro aprovado"})]

    evento = contrato.particionamento.coluna_evento
    limite = timedelta(minutes=regra.max_atraso_min or 0)
    # Entidade só faz sentido com chave composta (sensor + instante, estação + dia).
    # Chave única (operacao_id) é um evento por linha: frescor da tabela inteira.
    entidade = (
        next((c for c in contrato.chave_natural if c != evento), None)
        if len(contrato.chave_natural) > 1
        else None
    )
    # Série diária: o dia D só está completo em D+1 00:00, e o espaçamento normal
    # entre observações é de um dia. Em séries com instante, o passo é zero.
    passo = timedelta(days=1) if aprovados.schema[evento] == pl.Date else timedelta(0)

    coluna_evento = pl.col(evento)
    if aprovados.schema[evento] == pl.Date:
        coluna_evento = coluna_evento.cast(pl.Datetime("us")).dt.replace_time_zone("UTC")

    base = aprovados.select(
        [*(c for c in [entidade] if c), coluna_evento.alias("_evento")]
    ).drop_nulls()
    if base.is_empty():
        return []
    grupo = [entidade] if entidade else []

    alertas: list[AlertaTabela] = []

    # 1. Atraso: última observação de cada entidade em relação ao "agora".
    ultimos = (
        base.group_by(grupo).agg(pl.col("_evento").max().alias("_ultimo"))
        if grupo
        else base.select(pl.col("_evento").max().alias("_ultimo"))
    )
    for linha in ultimos.iter_rows(named=True):
        ultimo: datetime = linha["_ultimo"]
        atraso = instante_referencia - (ultimo + passo)
        if atraso > limite:
            alertas.append(
                AlertaTabela(
                    regra.id,
                    "atrasado",
                    linha.get(entidade) if entidade else None,
                    {
                        "ultimo_evento": ultimo.isoformat(),
                        "atraso_min": round(atraso.total_seconds() / 60, 1),
                        "limite_min": regra.max_atraso_min,
                    },
                )
            )

    # 2. Lacunas: só em séries periódicas (opt-in no contrato). O intervalo entre
    #    observações consecutivas, descontado o passo normal, não pode passar do limite.
    if not regra.detectar_lacunas:
        return alertas
    ordenado = base.sort([*grupo, "_evento"])
    diferenca = pl.col("_evento").diff().over(grupo) if grupo else pl.col("_evento").diff()
    diffs = ordenado.with_columns((diferenca - passo).alias("_lacuna"))
    lacunas = diffs.filter(pl.col("_lacuna") > limite)
    if not lacunas.is_empty():
        resumo = (
            lacunas.group_by(grupo).agg(
                pl.len().alias("lacunas"),
                pl.col("_lacuna").max().alias("maior"),
                pl.col("_evento")
                .filter(pl.col("_lacuna") == pl.col("_lacuna").max())
                .first()
                .alias("fim_maior"),
            )
            if grupo
            else lacunas.select(
                pl.len().alias("lacunas"),
                pl.col("_lacuna").max().alias("maior"),
                pl.col("_evento")
                .filter(pl.col("_lacuna") == pl.col("_lacuna").max())
                .first()
                .alias("fim_maior"),
            )
        )
        for linha in resumo.iter_rows(named=True):
            maior: timedelta = linha["maior"]
            fim: datetime = linha["fim_maior"]
            alertas.append(
                AlertaTabela(
                    regra.id,
                    "lacuna",
                    linha.get(entidade) if entidade else None,
                    {
                        "lacunas": int(linha["lacunas"]),
                        "maior_lacuna_min": round(maior.total_seconds() / 60, 1),
                        "maior_lacuna_inicio": (fim - maior - passo).isoformat(),
                        "maior_lacuna_fim": fim.isoformat(),
                        "limite_min": regra.max_atraso_min,
                    },
                )
            )
    return alertas


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------


def _resultado_vazio(df: pl.DataFrame, contrato: Contrato) -> ResultadoQualidade:
    """Fonte sem registros: relatório com todas as regras zeradas, nenhuma exceção."""
    vazio = df.clear()
    regras = [ResultadoRegra(REGRA_TIPAGEM, "tipagem", "critica", [], 0, 0)]
    regras += [
        ResultadoRegra(r.id, r.regra, r.severidade.value, r.campos, 0, 0, r.parametros)
        for r in contrato.qualidade
    ]
    return ResultadoQualidade(
        fonte=contrato.fonte,
        tabela=contrato.destino.silver_tabela,
        lidos=0,
        aprovados=vazio.with_columns(pl.lit([], dtype=pl.List(pl.Utf8)).alias("_alertas")),
        quarentena=vazio.with_columns(
            pl.lit([], dtype=pl.List(pl.Utf8)).alias("_regras_violadas"),
            pl.lit(None, dtype=pl.Utf8).alias("_motivo"),
        ),
        substituidos=vazio,
        regras=regras,
        alertas_tabela=[],
        falhas_tipagem=0,
        duplicatas=0,
    )


def avaliar(
    df: pl.DataFrame,
    contrato: Contrato,
    *,
    referencias: dict[str, pl.DataFrame] | None = None,
    instante_referencia: datetime,
) -> ResultadoQualidade:
    """Aplica todas as regras do contrato e separa aprovados, quarentena e substituídos."""
    logger = obter_logger(f"qualidade.{contrato.fonte}")
    referencias = referencias or {}
    total = df.height
    if total == 0:
        return _resultado_vazio(df, contrato)
    resultados: list[ResultadoRegra] = []

    if "_falha_tipagem" not in df.columns:
        df = df.with_columns(pl.lit([], dtype=pl.List(pl.Utf8)).alias("_falha_tipagem"))

    # --- 1. regras de valor ---------------------------------------------------
    criticas: list[tuple[str, pl.Expr]] = [(REGRA_TIPAGEM, pl.col("_falha_tipagem").list.len() > 0)]
    alertas: list[tuple[str, pl.Expr]] = []
    descricoes: dict[str, str] = {
        REGRA_TIPAGEM: f"{REGRA_TIPAGEM} tipagem: valor incompatível com o tipo do contrato"
    }

    for regra in contrato.qualidade:
        if regra.regra in {"unicidade", "freshness"}:
            continue
        expressao, motivo = _expressao_violacao(regra, df, referencias, instante_referencia)
        descricoes[regra.id] = _descrever(regra)
        if expressao is None:
            resultados.append(
                ResultadoRegra(
                    regra.id,
                    regra.regra,
                    regra.severidade.value,
                    regra.campos,
                    0,
                    0,
                    regra.parametros,
                    motivo,
                )
            )
            logger.warning("regra %s não avaliada: %s", regra.id, motivo)
            continue
        if regra.severidade is Severidade.CRITICA:
            criticas.append((regra.id, expressao))
        else:
            alertas.append((regra.id, expressao))

    colunas_v = {f"_v_{rid}": expr.fill_null(False) for rid, expr in [*criticas, *alertas]}
    avaliado = (
        df.with_columns([expr.alias(nome) for nome, expr in colunas_v.items()]) if total else df
    )

    for rid, _ in [*criticas, *alertas]:
        regra = next((r for r in contrato.qualidade if r.id == rid), None)
        violacoes = int(avaliado.get_column(f"_v_{rid}").sum()) if total else 0
        if regra is None:  # regra de sistema (tipagem)
            resultados.append(ResultadoRegra(rid, "tipagem", "critica", [], total, violacoes))
        else:
            resultados.append(
                ResultadoRegra(
                    rid,
                    regra.regra,
                    regra.severidade.value,
                    regra.campos,
                    total,
                    violacoes,
                    regra.parametros,
                )
            )

    def _lista(ids: list[str]) -> pl.Expr:
        if not ids:
            return pl.lit([], dtype=pl.List(pl.Utf8))
        return pl.concat_list(
            [pl.when(pl.col(f"_v_{rid}")).then(pl.lit(rid)).otherwise(None) for rid in ids]
        ).list.drop_nulls()

    avaliado = avaliado.with_columns(
        _lista([rid for rid, _ in criticas]).alias("_regras_violadas"),
        _lista([rid for rid, _ in alertas]).alias("_alertas"),
    ).drop(list(colunas_v))

    reprovados = avaliado.filter(pl.col("_regras_violadas").list.len() > 0)
    candidatos = avaliado.filter(pl.col("_regras_violadas").list.len() == 0)

    # --- 2. duplicatas e versões ------------------------------------------------
    mantidos, duplicatas, substituidos = _deduplicar(candidatos, contrato)
    regra_unicidade = contrato.regra_unicidade
    id_dup = regra_unicidade.id if regra_unicidade else REGRA_DUPLICATA_PADRAO
    descricoes[id_dup] = (
        _descrever(regra_unicidade)
        if regra_unicidade
        else f"{id_dup} unicidade {','.join(contrato.chave_natural)}"
    )
    if not duplicatas.is_empty():
        duplicatas = duplicatas.with_columns(
            pl.lit([id_dup], dtype=pl.List(pl.Utf8)).alias("_regras_violadas")
        )
    resultados.append(
        ResultadoRegra(
            id_dup,
            "unicidade",
            regra_unicidade.severidade.value if regra_unicidade else "critica",
            list(contrato.chave_natural),
            candidatos.height,
            duplicatas.height,
            {"estrategia": contrato.dedup.estrategia, "substituidos": substituidos.height},
        )
    )

    quarentena = (
        pl.concat([reprovados, duplicatas], how="vertical_relaxed")
        if not duplicatas.is_empty()
        else reprovados
    )
    if not quarentena.is_empty():
        quarentena = quarentena.with_columns(
            pl.col("_regras_violadas")
            .list.eval(pl.element().replace_strict(descricoes, default=pl.element()))
            .list.join("; ")
            .alias("_motivo")
        ).drop("_alertas")

    # --- 3. frescor -----------------------------------------------------------------
    alertas_tabela: list[AlertaTabela] = []
    for regra in contrato.qualidade:
        if regra.regra != "freshness":
            continue
        encontrados = _avaliar_frescor(mantidos, contrato, regra, instante_referencia)
        alertas_tabela.extend(encontrados)
        resultados.append(
            ResultadoRegra(
                regra.id,
                "freshness",
                regra.severidade.value,
                [],
                mantidos.height,
                len(encontrados),
                regra.parametros,
            )
        )

    falhas_tipagem = (
        int(avaliado.get_column("_falha_tipagem").list.len().gt(0).sum()) if total else 0
    )
    resultado = ResultadoQualidade(
        fonte=contrato.fonte,
        tabela=contrato.destino.silver_tabela,
        lidos=total,
        aprovados=mantidos.drop("_regras_violadas"),
        quarentena=quarentena,
        substituidos=substituidos.drop(["_regras_violadas", "_alertas"])
        if not substituidos.is_empty()
        else substituidos,
        regras=resultados,
        alertas_tabela=alertas_tabela,
        falhas_tipagem=falhas_tipagem,
        duplicatas=duplicatas.height,
    )
    logger.info(
        "%d lidos → %d aprovados · %d quarentena (%d duplicatas) · %d substituídos · "
        "%d alertas de tabela",
        total,
        resultado.n_aprovados,
        resultado.n_quarentena,
        resultado.duplicatas,
        resultado.n_substituidos,
        len(alertas_tabela),
    )
    return resultado
