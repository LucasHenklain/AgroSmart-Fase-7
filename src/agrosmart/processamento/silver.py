"""Bronze → silver: o dado vira confiável.

Para cada fonte, na ordem de dependência dos contratos:

1. **ler** todos os lotes do bronze como texto (``leitura``);
2. **normalizar** o que precisa de forma canônica antes de julgar — unidades
   do ERP para o SI (litro → m³, tonelada → kg);
3. **tipar** pelo contrato (``tipagem``), guardando o que falhou;
4. **avaliar** as regras de qualidade (``qualidade.motor``): aprovados,
   quarentena, substituídos;
5. **tratar LGPD** nos aprovados — pseudonimizar ou remover campos marcados;
6. **escrever** Parquet particionado pela data do evento no silver, a
   quarentena particionada pelo lote de origem, e os substituídos em histórico;
7. **reconciliar**: registros declarados nos manifests = lidos = aprovados +
   quarentena + substituídos. Se não fechar, é alerta no relatório.

O silver é recomputado por inteiro a cada execução — com os volumes desta
fase é barato e elimina uma classe inteira de bugs de incrementalidade. O
processamento incremental por lote entra com o ``backfill`` do Passo 5.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl

from agrosmart.config import Camada, Config, carregar_config
from agrosmart.contratos import Contrato, contratos_ativos, ordenar_por_dependencia
from agrosmart.log import Evento, obter_logger, registrar_evento
from agrosmart.processamento.leitura import ler_fonte_bronze
from agrosmart.processamento.tipagem import dtype_polars, tipar
from agrosmart.qualidade.motor import ResultadoQualidade, avaliar
from agrosmart.qualidade.verificacao import carregar_defeitos_injetados, verificar_defeitos
from agrosmart.storage import Armazenamento, criar_armazenamento

# Colunas de proveniência que seguem para o silver. As demais (janela, linha,
# versão do lote, falhas de tipagem) cumpriram seu papel na avaliação.
COLUNAS_PROVENIENCIA_SILVER = ("_batch_id", "_ingested_at", "_schema_version", "_alertas")

# Unidades canônicas do silver para o ERP: (de → (para, fator)).
CONVERSOES_UNIDADE: dict[str, tuple[str, float]] = {
    "L": ("m3", 0.001),
    "t": ("kg", 1000.0),
}


# ---------------------------------------------------------------------------
# Normalização e LGPD
# ---------------------------------------------------------------------------


def normalizar_unidades(df: pl.DataFrame, contrato: Contrato) -> pl.DataFrame:
    """Converte pares (quantidade, unidade) para as unidades canônicas do silver.

    Só se aplica a contratos que têm os dois campos. A conversão acontece
    antes da tipagem, sobre texto, para que a regra de domínio da unidade
    avalie o valor **canônico** — e ``"litros"``, que não é ``L``, continue
    fora do domínio e vá para a quarentena.
    """
    if not {"quantidade", "unidade"} <= set(df.columns) or df.is_empty():
        return df
    quantidade = pl.col("quantidade").cast(pl.Float64, strict=False)
    unidade = pl.col("unidade")
    expr_qtd = quantidade
    expr_uni = unidade
    for origem, (destino, fator) in CONVERSOES_UNIDADE.items():
        expr_qtd = pl.when(unidade == origem).then(quantidade * fator).otherwise(expr_qtd)
        expr_uni = pl.when(unidade == origem).then(pl.lit(destino)).otherwise(expr_uni)
    # Mantém texto: a tipagem oficial acontece depois, pelo contrato.
    return df.with_columns(
        pl.when(quantidade.is_null())
        .then(pl.col("quantidade"))
        .otherwise(expr_qtd.round(6).cast(pl.Utf8))
        .alias("quantidade"),
        expr_uni.alias("unidade"),
    )


def pseudonimo(valor: str | None, salt: str) -> str | None:
    """``OPE-014`` → ``OPE~5f3a9c1b2d`` — prefixo legível, sufixo irreversível sem o sal."""
    if valor is None:
        return None
    prefixo = valor.split("-", 1)[0] if "-" in valor else "ID"
    digest = hashlib.sha256(f"{salt}:{valor}".encode()).hexdigest()[:10]
    return f"{prefixo}~{digest}"


def tratar_lgpd(df: pl.DataFrame, contrato: Contrato, salt: str) -> pl.DataFrame:
    """Aplica ``tratamento`` dos campos marcados: pseudonimizar ou remover."""
    for campo in contrato.campos:
        if campo.nome not in df.columns or not campo.tratamento:
            continue
        if campo.tratamento == "remover":
            df = df.drop(campo.nome)
        elif campo.tratamento == "pseudonimizar":
            df = df.with_columns(
                pl.col(campo.nome)
                .cast(pl.Utf8)
                .map_elements(lambda v, s=salt: pseudonimo(v, s), return_dtype=pl.Utf8)
                .alias(campo.nome)
            )
    return df


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------


def _parquet(df: pl.DataFrame) -> bytes:
    buffer = io.BytesIO()
    df.write_parquet(buffer, compression="zstd")
    return buffer.getvalue()


def limpar_prefixo(armazenamento: Armazenamento, camada: Camada, prefixo: str) -> int:
    chaves = [o.chave for o in armazenamento.listar(camada, prefixo)]
    for chave in chaves:
        armazenamento.remover(camada, chave)
    return len(chaves)


def limpar_particoes(
    armazenamento: Armazenamento, camada: Camada, tabela: str, dias: list[date]
) -> int:
    """Remove só as partições diárias indicadas de uma tabela (backfill por intervalo)."""
    removidos = 0
    for dia in dias:
        prefixo = f"{tabela}/ano={dia:%Y}/mes={dia:%m}/dia={dia:%d}/"
        removidos += limpar_prefixo(armazenamento, camada, prefixo)
    return removidos


def escrever_particionado(
    armazenamento: Armazenamento,
    camada: Camada,
    tabela: str,
    df: pl.DataFrame,
    coluna_data: str | None,
    *,
    recomputar: bool = True,
    substituir_dias: list[date] | None = None,
) -> list[str]:
    """Um Parquet por dia de ``coluna_data`` em ``<tabela>/ano=/mes=/dia=/``.

    Sem ``coluna_data`` (ou com ela toda nula) grava um único arquivo. Com
    ``recomputar`` os objetos anteriores da tabela são removidos antes — o
    silver é recomputado por inteiro. Com ``substituir_dias`` (backfill) só as
    partições desses dias são removidas antes de gravar.
    """
    if recomputar:
        limpar_prefixo(armazenamento, camada, f"{tabela}/")
    elif substituir_dias:
        limpar_particoes(armazenamento, camada, tabela, substituir_dias)
    if df.is_empty():
        return []

    chaves: list[str] = []
    if (
        coluna_data
        and coluna_data in df.columns
        and df.get_column(coluna_data).null_count() < df.height
    ):
        col = pl.col(coluna_data)
        dia = col.dt.date() if df.schema[coluna_data] != pl.Date else col
        particionado = df.with_columns(dia.alias("_dia"))
        for (valor,), parte in particionado.group_by(["_dia"], maintain_order=True):
            parte = parte.drop("_dia")
            if valor is None:
                chave = f"{tabela}/sem_data/{tabela}-sem_data.parquet"
            else:
                particao = f"ano={valor:%Y}/mes={valor:%m}/dia={valor:%d}"
                chave = f"{tabela}/{particao}/{tabela}-{valor:%Y%m%d}.parquet"
            armazenamento.gravar_bytes(
                camada, chave, _parquet(parte), content_type="application/vnd.apache.parquet"
            )
            chaves.append(chave)
    else:
        chave = f"{tabela}/{tabela}.parquet"
        armazenamento.gravar_bytes(
            camada, chave, _parquet(df), content_type="application/vnd.apache.parquet"
        )
        chaves.append(chave)
    return chaves


def esquema_silver(contrato: Contrato) -> dict[str, pl.DataType]:
    """Schema Polars da tabela silver de um contrato: campos tipados + proveniência."""
    schema: dict[str, pl.DataType] = {
        c.nome: dtype_polars(c.tipo) for c in contrato.campos if c.tratamento != "remover"
    }
    schema.update(
        {
            "_batch_id": pl.Utf8,
            "_ingested_at": pl.Datetime("us", "UTC"),
            "_schema_version": pl.Utf8,
            "_alertas": pl.List(pl.Utf8),
        }
    )
    return schema


def ler_tabela(
    armazenamento: Armazenamento, camada: Camada, tabela: str, prefixo: str | None = None
) -> pl.DataFrame:
    """Concatena todos os Parquet de uma tabela (ou de um prefixo dela). Vazio se não existe."""
    partes = [
        pl.read_parquet(io.BytesIO(armazenamento.ler_bytes(camada, o.chave)))
        for o in armazenamento.listar(camada, prefixo or f"{tabela}/")
        if o.chave.endswith(".parquet")
    ]
    if not partes:
        return pl.DataFrame()
    return pl.concat(partes, how="vertical_relaxed")


def _prefixo_particao(tabela: str, dia: date) -> str:
    return f"{tabela}/ano={dia:%Y}/mes={dia:%m}/dia={dia:%d}/"


def mesclar_backfill(
    armazenamento: Armazenamento,
    contrato: Contrato,
    novos: pl.DataFrame,
    batch_ids: set[str],
    intervalo: tuple[date, date],
) -> list[str]:
    """Backfill de fonte de eventos: substitui no silver exatamente o que veio dos lotes
    reprocessados, onde quer que tenha caído.

    Um evento é gravado na partição do **dia do evento**, mas o lote que o trouxe é
    particionado pelo dia da janela — e um evento pode chegar atrasado, em lote de
    outro dia. Reescrever partições inteiras perderia esses eventos. Em vez disso,
    para cada partição afetada: removem-se as linhas cujo ``_batch_id`` está entre os
    lotes reprocessados, acrescentam-se as novas, e a partição é deduplicada pela
    chave natural (mesma ordem do motor: primeira ingestão vence). Partições
    visitadas: o intervalo com margem de um dia para cada lado, mais os dias de
    evento das novas linhas.
    """
    tabela = contrato.destino.silver_tabela
    coluna_evento = contrato.particionamento.coluna_evento
    chave = list(contrato.chave_natural)
    ini, fim = intervalo
    dias: set[date] = {ini + timedelta(days=d) for d in range(-1, (fim - ini).days + 2)}
    if not novos.is_empty() and coluna_evento in novos.columns:
        col = pl.col(coluna_evento)
        como_data = col if novos.schema[coluna_evento] == pl.Date else col.dt.date()
        novos = novos.with_columns(como_data.alias("_dia_evento"))
        dias.update(novos.get_column("_dia_evento").drop_nulls().unique().to_list())
    else:
        novos = novos.with_columns(pl.lit(None, dtype=pl.Date).alias("_dia_evento"))

    chaves: list[str] = []
    for dia in sorted(dias):
        prefixo = _prefixo_particao(tabela, dia)
        existente = ler_tabela(armazenamento, Camada.SILVER, tabela, prefixo=prefixo)
        do_dia = novos.filter(pl.col("_dia_evento") == dia).drop("_dia_evento")
        if existente.is_empty() and do_dia.is_empty():
            continue
        partes = []
        if not existente.is_empty():
            partes.append(existente.filter(~pl.col("_batch_id").is_in(list(batch_ids))))
        if not do_dia.is_empty():
            partes.append(do_dia)
        combinado = pl.concat(partes, how="vertical_relaxed")
        if not combinado.is_empty():
            combinado = (
                combinado.sort(["_ingested_at", "_batch_id"])
                .unique(subset=chave, keep="first", maintain_order=True)
                .sort(coluna_evento)
            )
        limpar_prefixo(armazenamento, Camada.SILVER, prefixo)
        if combinado.is_empty():
            continue
        chave_obj = f"{prefixo}{tabela}-{dia:%Y%m%d}.parquet"
        armazenamento.gravar_bytes(
            Camada.SILVER,
            chave_obj,
            _parquet(combinado),
            content_type="application/vnd.apache.parquet",
        )
        chaves.append(chave_obj)
    return chaves


# ---------------------------------------------------------------------------
# Construção
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ResumoFonteSilver:
    fonte: str
    tabela: str
    lotes: int
    declarados: int
    lidos: int
    aprovados: int
    quarentena: int
    substituidos: int
    duplicatas: int
    falhas_tipagem: int
    registros_com_alerta: int
    arquivos_silver: int
    arquivos_quarentena: int
    reconciliacao_ok: bool
    duracao_s: float
    qualidade: dict[str, Any] = field(default_factory=dict)


def processar_fonte(
    contrato: Contrato,
    armazenamento: Armazenamento,
    config: Config,
    referencias: dict[str, pl.DataFrame],
    intervalo: tuple[date, date] | None = None,
) -> tuple[ResumoFonteSilver, ResultadoQualidade]:
    logger = obter_logger(f"silver.{contrato.fonte}")
    inicio = time.perf_counter()

    # Backfill: fontes de eventos reprocessam só os lotes do intervalo (partição do lote
    # dentro de [inicio, fim]) e substituem no silver o que veio deles (mesclar_backfill);
    # fontes de versões precisam de todo o histórico da chave para a deduplicação e são
    # recomputadas por inteiro.
    parcial = intervalo is not None and contrato.dedup.estrategia == "evento"
    filtro_particao = None
    if parcial and intervalo is not None:
        ini, fim = intervalo
        filtro_particao = lambda m: ini <= date.fromisoformat(m["particao"]) <= fim  # noqa: E731
        logger.info("backfill %s → %s (só partições do intervalo)", ini, fim)

    bruto, manifests = ler_fonte_bronze(contrato, armazenamento, config, filtro=filtro_particao)
    declarados = sum(int(m.get("registros", 0)) for m in manifests)

    normalizado = normalizar_unidades(bruto, contrato)
    tipado = tipar(normalizado, contrato)
    resultado = avaliar(
        tipado, contrato, referencias=referencias, instante_referencia=config.instante_referencia
    )

    tratado = tratar_lgpd(resultado.aprovados, contrato, config.salt_pseudonimizacao)
    colunas_silver = [
        *(c.nome for c in contrato.campos if c.nome in tratado.columns),
        *(c for c in COLUNAS_PROVENIENCIA_SILVER if c in tratado.columns),
    ]
    silver = tratado.select(colunas_silver)

    tabela = contrato.destino.silver_tabela
    coluna_evento = contrato.particionamento.coluna_evento
    if parcial and intervalo is not None:
        chaves_silver = mesclar_backfill(
            armazenamento,
            contrato,
            silver,
            {str(m["batch_id"]) for m in manifests},
            intervalo,
        )
    else:
        chaves_silver = escrever_particionado(
            armazenamento, Camada.SILVER, tabela, silver, coluna_evento
        )
    # Quarentena particionada pela data em que o registro entrou nela (ingestão), não
    # pela data do evento: é dessa data que a retenção de 30 dias precisa contar. No
    # backfill a quarentena é trilha de auditoria: acumula, e a retenção poda.
    chaves_quarentena = escrever_particionado(
        armazenamento,
        Camada.QUARENTENA,
        contrato.fonte,
        resultado.quarentena,
        "_ingested_at",
        recomputar=not parcial,
    )
    if not parcial:
        limpar_prefixo(armazenamento, Camada.SILVER, f"_substituidos/{tabela}/")
    if not resultado.substituidos.is_empty():
        armazenamento.gravar_bytes(
            Camada.SILVER,
            f"_substituidos/{tabela}/{tabela}-substituidos.parquet",
            _parquet(resultado.substituidos),
            content_type="application/vnd.apache.parquet",
        )

    contabilizados = resultado.n_aprovados + resultado.n_quarentena + resultado.n_substituidos
    reconciliacao_ok = declarados == resultado.lidos == contabilizados
    if not reconciliacao_ok:
        logger.warning(
            "reconciliação divergente: declarados=%d lidos=%d contabilizados=%d",
            declarados,
            resultado.lidos,
            contabilizados,
        )

    # A referência para as próximas fontes é o silver já tratado (chaves conformadas).
    # No backfill parcial, a tabela inteira gravada é a referência, não só o intervalo.
    referencias[tabela] = ler_tabela(armazenamento, Camada.SILVER, tabela) if parcial else silver

    resumo = ResumoFonteSilver(
        fonte=contrato.fonte,
        tabela=tabela,
        lotes=len(manifests),
        declarados=declarados,
        lidos=resultado.lidos,
        aprovados=resultado.n_aprovados,
        quarentena=resultado.n_quarentena,
        substituidos=resultado.n_substituidos,
        duplicatas=resultado.duplicatas,
        falhas_tipagem=resultado.falhas_tipagem,
        registros_com_alerta=resultado.registros_com_alerta,
        arquivos_silver=len(chaves_silver),
        arquivos_quarentena=len(chaves_quarentena),
        reconciliacao_ok=reconciliacao_ok,
        duracao_s=round(time.perf_counter() - inicio, 2),
        qualidade=resultado.resumo(),
    )
    registrar_evento(
        Evento(
            etapa="silver",
            acao="fonte_processada",
            fonte=contrato.fonte,
            camada=Camada.SILVER.value,
            status="ok" if reconciliacao_ok else "alerta",
            mensagem=f"{tabela}: {resumo.aprovados} aprovados · {resumo.quarentena} quarentena",
            metricas={
                "lidos": resumo.lidos,
                "aprovados": resumo.aprovados,
                "quarentena": resumo.quarentena,
                "substituidos": resumo.substituidos,
                "arquivos": resumo.arquivos_silver,
                "reconciliacao_ok": reconciliacao_ok,
            },
        ),
        config,
    )
    return resumo, resultado


def construir_silver(
    config: Config | None = None,
    armazenamento: Armazenamento | None = None,
    *,
    fontes: list[str] | None = None,
    intervalo: tuple[date, date] | None = None,
) -> dict[str, Any]:
    """Processa todas as fontes (ou as pedidas) e grava ``outputs/dq_report.json``.

    Com ``intervalo`` (backfill) as fontes de eventos são reprocessadas só nas
    partições do intervalo; as de versões, por inteiro.
    """
    cfg = config or carregar_config()
    lake = armazenamento or criar_armazenamento(cfg)
    logger = obter_logger("silver")
    contratos = contratos_ativos()
    selecao = set(fontes) if fontes is not None else set(contratos)
    inicio = time.perf_counter()

    referencias: dict[str, pl.DataFrame] = {}
    resumos: list[ResumoFonteSilver] = []
    resultados: dict[str, ResultadoQualidade] = {}
    for contrato in ordenar_por_dependencia(contratos):
        if contrato.fonte not in selecao:
            # Fonte fora da seleção ainda pode ser referência: usa o silver já gravado.
            existente = ler_tabela(lake, Camada.SILVER, contrato.destino.silver_tabela)
            if not existente.is_empty():
                referencias[contrato.destino.silver_tabela] = existente
            continue
        resumo, resultado = processar_fonte(contrato, lake, cfg, referencias, intervalo=intervalo)
        resumos.append(resumo)
        resultados[contrato.fonte] = resultado

    # Verdade-terreno: os defeitos injetados foram todos capturados?
    from agrosmart.simuladores.defeitos import (
        POR_ID,
    )  # import local: simuladores é opcional em produção

    defeitos = carregar_defeitos_injetados(cfg.dir_outputs / "simulacao.json")
    defeitos = [d for d in defeitos if d["fonte"] in resultados]
    severidades = {tipo.id: tipo.severidade for tipo in POR_ID.values()}
    verificacao = verificar_defeitos(defeitos, resultados, contratos, severidades)

    relatorio = {
        "gerado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "instante_referencia": cfg.instante_referencia.isoformat(),
        "backend": lake.nome,
        "intervalo": [intervalo[0].isoformat(), intervalo[1].isoformat()] if intervalo else None,
        "fontes": {r.fonte: asdict(r) for r in resumos},
        "totais": {
            "lotes": sum(r.lotes for r in resumos),
            "declarados": sum(r.declarados for r in resumos),
            "lidos": sum(r.lidos for r in resumos),
            "aprovados": sum(r.aprovados for r in resumos),
            "quarentena": sum(r.quarentena for r in resumos),
            "substituidos": sum(r.substituidos for r in resumos),
            "duplicatas": sum(r.duplicatas for r in resumos),
            "registros_com_alerta": sum(r.registros_com_alerta for r in resumos),
            "reconciliacao_ok": all(r.reconciliacao_ok for r in resumos),
            "alertas_tabela": sum(len(r.qualidade["alertas_tabela"]) for r in resumos),
        },
        "defeitos_injetados": verificacao.para_dict(),
        "duracao_s": round(time.perf_counter() - inicio, 2),
    }
    cfg.dir_outputs.mkdir(parents=True, exist_ok=True)
    # O relatório do backfill cobre só o intervalo: vai para um arquivo próprio, para não
    # substituir o relatório da última execução completa (que o gold e o dashboard leem).
    nome_relatorio = (
        "dq_report.json"
        if intervalo is None
        else f"dq_report_backfill_{intervalo[0]:%Y%m%d}_{intervalo[1]:%Y%m%d}.json"
    )
    relatorio["arquivo"] = nome_relatorio
    (cfg.dir_outputs / nome_relatorio).write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    totais = relatorio["totais"]
    logger.info(
        "silver: %d aprovados · %d quarentena · %d substituídos · reconciliação %s · "
        "defeitos localizados %d/%d",
        totais["aprovados"],
        totais["quarentena"],
        totais["substituidos"],
        "ok" if totais["reconciliacao_ok"] else "DIVERGENTE",
        verificacao.localizados,
        verificacao.total,
    )
    return relatorio
