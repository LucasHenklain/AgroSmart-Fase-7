"""Interface de linha de comando do AgroSmart.

    agrosmart doctor              # o ambiente está pronto?
    agrosmart init                # cria pastas e buckets das camadas
    agrosmart contratos           # lista as fontes e suas regras
    agrosmart contratos sensores  # detalha uma fonte
    agrosmart run --all           # pipeline completo (etapas entram nos próximos passos)

Cada comando é fino: valida argumentos, chama um módulo e apresenta o
resultado. A lógica mora nos pacotes ``simuladores``, ``ingestao``,
``qualidade``, ``processamento`` e ``ia``.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from agrosmart import __version__
from agrosmart.config import Backend, Camada, carregar_config, garantir_diretorios
from agrosmart.contratos import ErroContrato, carregar_contratos, ordenar_por_dependencia
from agrosmart.log import configurar_logging, console, obter_logger
from agrosmart.storage import criar_armazenamento

app = typer.Typer(
    name="agrosmart",
    help="AgroSmart Fase 5 — Data Lake, ingestão, qualidade e IA generativa para o campo.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)

_OK = "[green]✔[/green]"
_FALHA = "[red]✘[/red]"
_AVISO = "[yellow]![/yellow]"


@app.callback()
def _principal(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Log em nível DEBUG.")] = False,
) -> None:
    configurar_logging()
    if verbose:
        import logging

        logging.getLogger("agrosmart").setLevel(logging.DEBUG)


def _pendente(passo: int, titulo: str, entrega: str) -> None:
    """Etapas ainda não implementadas dizem exatamente em que passo entram."""
    console.print(
        Panel(
            f"[bold]{titulo}[/bold] entra no [bold]Passo {passo}[/bold] do plano ({entrega}).\n"
            "A interface já está fixada para que a documentação e os scripts não mudem depois.",
            title="etapa pendente",
            border_style="yellow",
        )
    )
    raise typer.Exit(code=2)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@app.command()
def doctor() -> None:
    """Verifica configuração, dependências, contratos e o backend do lake."""
    falhas = 0
    tabela = Table(box=box.SIMPLE_HEAD, show_lines=False, pad_edge=False)
    tabela.add_column("", width=2)
    tabela.add_column("verificação", style="bold")
    tabela.add_column("detalhe")

    # 1. configuração
    try:
        cfg = carregar_config()
        tabela.add_row(
            _OK,
            "configuração",
            f"backend={cfg.backend.value} · seed={cfg.seed} · talhões={cfg.qtd_talhoes}",
        )
    except Exception as erro:  # noqa: BLE001
        tabela.add_row(_FALHA, "configuração", str(erro))
        console.print(tabela)
        raise typer.Exit(code=1) from erro

    # 2. dependências
    for modulo, papel in (
        ("polars", "processamento"),
        ("duckdb", "consultas SQL sobre Parquet"),
        ("pyarrow", "escrita Parquet"),
        ("boto3", "cliente S3/MinIO"),
        ("PIL", "imagens"),
    ):
        try:
            mod = importlib.import_module(modulo)
            versao = getattr(mod, "__version__", "")
            tabela.add_row(_OK, f"dependência {modulo}", f"{versao} · {papel}")
        except ImportError:
            falhas += 1
            tabela.add_row(_FALHA, f"dependência {modulo}", f"ausente — necessária para {papel}")

    # 3. contratos
    try:
        contratos = carregar_contratos(cfg.dir_contratos)
        ordem = " → ".join(c.fonte for c in ordenar_por_dependencia(contratos))
        total_regras = sum(len(c.qualidade) for c in contratos.values())
        tabela.add_row(
            _OK, "contratos", f"{len(contratos)} fontes · {total_regras} regras · ordem: {ordem}"
        )
    except ErroContrato as erro:
        falhas += 1
        tabela.add_row(_FALHA, "contratos", str(erro))

    # 4. backend do lake
    try:
        armazenamento = criar_armazenamento(cfg)
        diag = armazenamento.diagnostico()
        if cfg.backend is Backend.MINIO and not diag.get("alcancavel"):
            falhas += 1
            tabela.add_row(
                _FALHA,
                "lake (minio)",
                f"{cfg.minio_endpoint} inalcançável — {diag.get('erro')}\n"
                "  suba com: docker compose -f infra/docker-compose.yml up -d\n"
                "  ou use o plano B: AGROSMART_BACKEND=filesystem",
            )
        else:
            camadas = diag.get("camadas", {})
            faltando = [c for c, info in camadas.items() if not info.get("existe")]
            resumo = " · ".join(f"{c}={info.get('objetos', 0)}" for c, info in camadas.items())
            if faltando:
                tabela.add_row(
                    _AVISO,
                    f"lake ({diag['backend']})",
                    f"camadas ausentes: {faltando} — rode `agrosmart init`",
                )
            else:
                extra = f" · {diag['latencia_ms']} ms" if "latencia_ms" in diag else ""
                tabela.add_row(
                    _OK, f"lake ({diag['backend']})", f"objetos por camada: {resumo}{extra}"
                )
    except Exception as erro:  # noqa: BLE001
        falhas += 1
        tabela.add_row(_FALHA, "lake", str(erro))

    # 5. IA
    if cfg.ia_habilitada:
        tabela.add_row(_OK, "ia generativa", f"chave presente · modelo {cfg.ia_modelo}")
    else:
        tabela.add_row(
            _AVISO, "ia generativa", "sem ANTHROPIC_API_KEY — copiloto rodará em modo regras/mock"
        )

    console.print()
    console.print(
        Panel(
            tabela,
            title=f"agrosmart doctor · v{__version__}",
            border_style="green" if not falhas else "red",
        )
    )
    if falhas:
        console.print(f"[red]{falhas} verificação(ões) crítica(s) falharam.[/red]")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init() -> None:
    """Cria os diretórios de trabalho e as camadas do lake (buckets ou pastas)."""
    logger = obter_logger("init")
    cfg = carregar_config()
    criados = garantir_diretorios(cfg)
    logger.info("%d diretórios de trabalho garantidos", len(criados))

    armazenamento = criar_armazenamento(cfg)
    armazenamento.preparar()
    for camada in Camada:
        logger.info("camada %-10s → %s", camada.value, armazenamento.uri_camada(camada))
    console.print(f"\n{_OK} lake pronto no backend [bold]{armazenamento.nome}[/bold]")


# ---------------------------------------------------------------------------
# contratos
# ---------------------------------------------------------------------------


@app.command()
def contratos(
    fonte: Annotated[str | None, typer.Argument(help="Detalha uma fonte específica.")] = None,
) -> None:
    """Lista as fontes de dados e suas regras de qualidade."""
    cfg = carregar_config()
    try:
        todos = carregar_contratos(cfg.dir_contratos)
    except ErroContrato as erro:
        console.print(f"{_FALHA} {erro}")
        raise typer.Exit(code=1) from erro

    if fonte is None:
        tabela = Table(title="Fontes de dados", box=box.SIMPLE_HEAD)
        tabela.add_column("fonte", style="bold")
        tabela.add_column("tipo")
        tabela.add_column("lgpd")
        tabela.add_column("ingestão")
        tabela.add_column("sla", justify="right")
        tabela.add_column("campos", justify="right")
        tabela.add_column("regras", justify="right")
        tabela.add_column("→ silver")
        for c in ordenar_por_dependencia(todos):
            criticas = len(c.regras_criticas)
            alertas = len(c.regras_alerta)
            tabela.add_row(
                c.nome_completo,
                c.tipo_dado.value,
                c.classificacao_lgpd.value,
                c.ingestao.modo.value,
                f"{c.ingestao.sla_freshness_min} min",
                str(len(c.campos)),
                f"{criticas} crít · {alertas} alerta",
                c.destino.silver_tabela,
            )
        console.print(tabela)
        return

    contrato = todos.get(fonte)
    if contrato is None:
        console.print(
            f"{_FALHA} fonte {fonte!r} não existe. Disponíveis: {', '.join(sorted(todos))}"
        )
        raise typer.Exit(code=1)

    console.print(
        Panel(
            f"[bold]{contrato.titulo}[/bold]\n\n{contrato.descricao.strip()}",
            title=contrato.nome_completo,
        )
    )

    campos = Table(title="Schema", box=box.SIMPLE_HEAD)
    campos.add_column("campo", style="bold")
    campos.add_column("tipo")
    campos.add_column("unidade")
    campos.add_column("obrig.", justify="center")
    campos.add_column("descrição")
    for campo in contrato.campos:
        campos.add_row(
            campo.nome,
            campo.tipo.value,
            campo.unidade or "",
            "●" if campo.obrigatorio else "",
            campo.descricao,
        )
    console.print(campos)

    regras = Table(title="Regras de qualidade", box=box.SIMPLE_HEAD)
    regras.add_column("id", style="bold")
    regras.add_column("regra")
    regras.add_column("severidade")
    regras.add_column("campos / parâmetros")
    for regra in contrato.qualidade:
        cor = "red" if regra.severidade.value == "critica" else "yellow"
        params = ", ".join(f"{k}={v}" for k, v in regra.parametros.items())
        alvo = ", ".join(regra.campos) if regra.campos else ""
        regras.add_row(
            regra.id,
            regra.regra,
            f"[{cor}]{regra.severidade.value}[/{cor}]",
            " · ".join(p for p in (alvo, params) if p),
        )
    console.print(regras)


# ---------------------------------------------------------------------------
# etapas do pipeline — interface fixada, implementação por passo
# ---------------------------------------------------------------------------


@app.command()
def simular(
    fonte: Annotated[str | None, typer.Option(help="Só uma fonte (padrão: todas).")] = None,
    dias: Annotated[int | None, typer.Option(help="Dias de histórico (padrão: .env).")] = None,
    manter: Annotated[
        bool, typer.Option("--manter", help="Não apagar a landing anterior.")
    ] = False,
) -> None:
    """Gera dados sintéticos das fontes na pasta de landing."""
    from dataclasses import replace

    from agrosmart.simuladores import simular as executar_simulacao

    cfg = carregar_config()
    if dias is not None:
        cfg = replace(cfg, dias_historico=dias)
    resumo = executar_simulacao(cfg, fontes=[fonte] if fonte else None, limpar=not manter)

    tabela = Table(title="Landing gerada", box=box.SIMPLE_HEAD)
    tabela.add_column("fonte", style="bold")
    tabela.add_column("arquivos", justify="right")
    tabela.add_column("registros", justify="right")
    tabela.add_column("KB", justify="right")
    for nome, contagem in resumo["fontes"].items():
        tabela.add_row(
            nome,
            str(contagem["arquivos"]),
            str(contagem["registros"]),
            f"{contagem['bytes'] / 1024:,.0f}",
        )
    console.print(tabela)

    defeitos = Table(
        title="Defeitos injetados (verdade-terreno para a qualidade)", box=box.SIMPLE_HEAD
    )
    defeitos.add_column("tipo", style="bold")
    defeitos.add_column("regra alvo")
    defeitos.add_column("ocorrências", justify="right")
    from agrosmart.simuladores.defeitos import POR_ID

    for tipo, quantidade in resumo["defeitos_por_tipo"].items():
        defeitos.add_row(tipo, POR_ID[tipo].regra, str(quantidade))
    console.print(defeitos)

    traj = resumo["trajetoria"]
    console.print(
        f"trajetória: {traj['operacoes']} operações ({traj['irrigacoes']} irrigações, "
        f"{traj['correcoes_retroativas']} correções retroativas) · {traj['laudos']} laudos · "
        f"chuva {traj['chuva_total_mm_por_estacao']} mm · {resumo['duracao_s']}s"
    )
    console.print(f"{_OK} resumo em [bold]outputs/simulacao.json[/bold]")


@app.command()
def ingerir(
    fonte: Annotated[str | None, typer.Option(help="Só uma fonte (padrão: todas).")] = None,
    forcar: Annotated[bool, typer.Option("--forcar", help="Regravar lotes já ingeridos.")] = False,
    sem_exemplos: Annotated[
        bool, typer.Option("--sem-exemplos", help="Não exportar data/exemplos.")
    ] = False,
) -> None:
    """Coleta os dados da landing e grava na camada bruta com manifest."""
    from agrosmart.ingestao import ingerir as executar_ingestao

    cfg = carregar_config()
    resumo = executar_ingestao(
        cfg, fontes=[fonte] if fonte else None, forcar=forcar, exportar_exemplos=not sem_exemplos
    )

    tabela = Table(title=f"Ingestão → bronze ({resumo['backend']})", box=box.SIMPLE_HEAD)
    for coluna, alinhamento in (
        ("fonte", "left"),
        ("modo", "left"),
        ("lotes", "right"),
        ("gravados", "right"),
        ("atualizados", "right"),
        ("pulados", "right"),
        ("rejeitados", "right"),
        ("registros", "right"),
        ("KB", "right"),
        ("s", "right"),
    ):
        tabela.add_column(coluna, justify=alinhamento, style="bold" if coluna == "fonte" else None)
    for r in resumo["fontes"]:
        rejeitados = f"[red]{r['rejeitados']}[/red]" if r["rejeitados"] else "0"
        tabela.add_row(
            r["fonte"],
            r["modo"],
            str(r["lotes"]),
            str(r["gravados"]),
            str(r["atualizados"]),
            str(r["pulados"]),
            rejeitados,
            str(r["registros"]),
            f"{r['bytes'] / 1024:,.0f}",
            f"{r['duracao_s']:.1f}",
        )
    console.print(tabela)

    for r in resumo["fontes"]:
        for rejeicao in r["rejeicoes"]:
            console.print(f"  {_AVISO} {r['fonte']} · {rejeicao['lote']}: {rejeicao['motivo']}")

    exemplos = ", ".join(resumo["exemplos"]) or "nenhum"
    console.print(
        f"bronze: {resumo['objetos_no_bronze']} objetos · "
        f"quarentena: {resumo['objetos_na_quarentena']} · "
        f"exemplos exportados: {exemplos} · {resumo['duracao_s']}s"
    )
    console.print(f"{_OK} resumo em [bold]outputs/ingestao.json[/bold]")


def _imprimir_relatorio_qualidade(relatorio: dict, fonte: str | None = None) -> None:
    totais = relatorio["totais"]
    tabela = Table(title="Qualidade por fonte", box=box.SIMPLE_HEAD)
    for coluna, alinhamento in (
        ("fonte", "left"),
        ("lidos", "right"),
        ("aprovados", "right"),
        ("quarentena", "right"),
        ("duplicatas", "right"),
        ("substituídos", "right"),
        ("c/ alerta", "right"),
        ("alertas tabela", "right"),
        ("reconciliação", "left"),
    ):
        tabela.add_column(coluna, justify=alinhamento, style="bold" if coluna == "fonte" else None)
    for nome, dados in relatorio["fontes"].items():
        if fonte and nome != fonte:
            continue
        quarentena = f"[red]{dados['quarentena']}[/red]" if dados["quarentena"] else "0"
        tabela.add_row(
            nome,
            f"{dados['lidos']:,}",
            f"{dados['aprovados']:,}",
            quarentena,
            str(dados["duplicatas"]),
            str(dados["substituidos"]),
            str(dados["registros_com_alerta"]),
            str(len(dados["qualidade"]["alertas_tabela"])),
            f"{_OK} fecha" if dados["reconciliacao_ok"] else f"{_FALHA} divergente",
        )
    console.print(tabela)

    regras = Table(title="Regras acionadas (violações > 0)", box=box.SIMPLE_HEAD)
    for coluna in ("fonte", "regra", "tipo", "severidade", "avaliados", "violações"):
        regras.add_column(
            coluna, justify="right" if coluna in {"avaliados", "violações"} else "left"
        )
    for nome, dados in relatorio["fontes"].items():
        if fonte and nome != fonte:
            continue
        for regra in dados["qualidade"]["regras"]:
            if regra["violacoes"]:
                cor = "red" if regra["severidade"] == "critica" else "yellow"
                regras.add_row(
                    nome,
                    regra["id"],
                    regra["regra"],
                    f"[{cor}]{regra['severidade']}[/{cor}]",
                    f"{regra['avaliados']:,}",
                    f"{regra['violacoes']:,}",
                )
    console.print(regras)

    alertas = [
        (nome, a)
        for nome, dados in relatorio["fontes"].items()
        if not fonte or nome == fonte
        for a in dados["qualidade"]["alertas_tabela"]
    ]
    if alertas:
        frescor = Table(title="Alertas de frescor (por entidade)", box=box.SIMPLE_HEAD)
        for coluna in ("fonte", "regra", "tipo", "entidade", "detalhe"):
            frescor.add_column(coluna)
        for nome, alerta in alertas[:30]:
            detalhe = {k: v for k, v in alerta.items() if k not in {"regra", "tipo", "entidade"}}
            frescor.add_row(
                nome, alerta["regra"], alerta["tipo"], str(alerta.get("entidade")), str(detalhe)
            )
        console.print(frescor)

    verificacao = relatorio.get("defeitos_injetados", {})
    if verificacao.get("total"):
        estado = _OK if verificacao["faltantes_total"] == 0 else _FALHA
        console.print(
            f"{estado} defeitos injetados localizados: {verificacao['localizados']}/"
            f"{verificacao['total']} (cobertura {verificacao['cobertura']:.1%})"
        )
    console.print(
        f"totais: {totais['lidos']:,} lidos · {totais['aprovados']:,} aprovados · "
        f"{totais['quarentena']:,} quarentena · {totais['substituidos']:,} substituídos · "
        f"reconciliação {'ok' if totais['reconciliacao_ok'] else 'DIVERGENTE'}"
    )


@app.command()
def qualidade(
    fonte: Annotated[str | None, typer.Option(help="Só uma fonte (padrão: todas).")] = None,
    reexecutar: Annotated[
        bool, typer.Option("--reexecutar", help="Reprocessar o silver antes de mostrar.")
    ] = False,
) -> None:
    """Mostra o relatório de qualidade (dq_report.json) — ou o recalcula com --reexecutar."""
    import json

    cfg = carregar_config()
    caminho = cfg.dir_outputs / "dq_report.json"
    if reexecutar or not caminho.exists():
        from agrosmart.processamento.silver import construir_silver

        relatorio = construir_silver(cfg, fontes=[fonte] if fonte else None)
    else:
        relatorio = json.loads(caminho.read_text(encoding="utf-8"))
    _imprimir_relatorio_qualidade(relatorio, fonte)
    console.print(f"{_OK} relatório em [bold]outputs/dq_report.json[/bold]")


@app.command()
def processar(
    camada: Annotated[str, typer.Option(help="silver | gold | all")] = "all",
    fonte: Annotated[str | None, typer.Option(help="Só uma fonte no silver.")] = None,
) -> None:
    """Transforma bronze → silver (qualidade + LGPD) → gold (modelo dimensional) → catálogo."""
    if camada not in {"silver", "gold", "all"}:
        console.print(f"{_FALHA} camada inválida: {camada!r} (use silver, gold ou all)")
        raise typer.Exit(code=2)

    cfg = carregar_config()
    if camada in {"silver", "all"}:
        from agrosmart.processamento.silver import construir_silver

        relatorio = construir_silver(cfg, fontes=[fonte] if fonte else None)
        _imprimir_relatorio_qualidade(relatorio, fonte)

    if camada in {"gold", "all"}:
        from agrosmart.processamento.gold import construir_gold

        resumo = construir_gold(cfg)
        tabela = Table(title="Gold materializado", box=box.SIMPLE_HEAD)
        tabela.add_column("tabela", style="bold")
        tabela.add_column("grão")
        tabela.add_column("linhas", justify="right")
        tabela.add_column("colunas", justify="right")
        tabela.add_column("KB", justify="right")
        for nome, info in resumo["tabelas"].items():
            tabela.add_row(
                nome,
                info["grao"],
                f"{info['registros']:,}",
                str(info["colunas"]),
                f"{info['bytes'] / 1024:,.1f}",
            )
        console.print(tabela)
        for pulada in resumo["puladas"]:
            console.print(f"  {_AVISO} {pulada['tabela']}: {pulada['motivo']}")

    if camada == "all":
        from agrosmart.catalogo import construir_catalogo, gerar_dicionario

        catalogo_ = construir_catalogo(cfg)
        destino = gerar_dicionario(cfg, catalogo_)
        console.print(
            f"{_OK} catálogo em [bold]outputs/catalogo.json[/bold] · dicionário em "
            f"[bold]{destino.relative_to(cfg.raiz).as_posix()}[/bold]"
        )


@app.command()
def catalogo() -> None:
    """Inventaria o lake (schema, contagens, partições, linhagem) e regenera o dicionário."""
    from agrosmart.catalogo import construir_catalogo, gerar_dicionario

    cfg = carregar_config()
    resultado = construir_catalogo(cfg)
    destino = gerar_dicionario(cfg, resultado)

    tabela = Table(title="Catálogo · silver", box=box.SIMPLE_HEAD)
    for coluna in (
        "fonte",
        "tabela",
        "registros",
        "arquivos",
        "lotes",
        "quarentena",
        "gold que consome",
    ):
        tabela.add_column(
            coluna,
            justify="right"
            if coluna in {"registros", "arquivos", "lotes", "quarentena"}
            else "left",
        )
    for fonte, dados in resultado["fontes"].items():
        silver, quarentena = dados["silver"], dados["quarentena"]
        tabela.add_row(
            fonte,
            silver["tabela"],
            f"{silver.get('registros', 0):,}",
            str(silver.get("arquivos", 0)),
            str(silver.get("lotes_distintos", 0)),
            f"{quarentena.get('registros', 0):,}",
            ", ".join(dados["linhagem"]["gold_tabelas"]) or "—",
        )
    console.print(tabela)

    gold = Table(title="Catálogo · gold", box=box.SIMPLE_HEAD)
    for coluna in ("tabela", "grão", "registros", "colunas"):
        gold.add_column(coluna, justify="right" if coluna in {"registros", "colunas"} else "left")
    for nome, dados in resultado["gold"].items():
        gold.add_row(
            nome, dados["grao"], f"{dados.get('registros', 0):,}", str(dados.get("colunas", 0))
        )
    console.print(gold)
    console.print(
        f"{_OK} catálogo em [bold]outputs/catalogo.json[/bold] · dicionário em "
        f"[bold]{destino.relative_to(cfg.raiz).as_posix()}[/bold]"
    )


@app.command()
def manter(
    compactar: Annotated[bool, typer.Option("--compactar/--sem-compactar")] = True,
    retencao: Annotated[bool, typer.Option("--retencao/--sem-retencao")] = True,
    aplicar: Annotated[
        bool, typer.Option("--aplicar", help="Remover de fato o que passou da retenção.")
    ] = False,
) -> None:
    """Compacta arquivos pequenos do silver e aplica (ou simula) a retenção dos contratos."""
    from agrosmart.processamento.manutencao import manter as executar_manutencao

    saida = executar_manutencao(
        carregar_config(), compactar_silver=compactar, retencao=retencao, aplicar_retencao_=aplicar
    )
    if "compactacao" in saida:
        tabela = Table(title="Compactação do silver", box=box.SIMPLE_HEAD)
        for coluna in (
            "partição",
            "arquivos antes",
            "depois",
            "registros",
            "KB antes",
            "KB depois",
        ):
            tabela.add_column(coluna, justify="left" if coluna == "partição" else "right")
        for item in saida["compactacao"]:
            tabela.add_row(
                item["particao"],
                str(item["arquivos_antes"]),
                str(item["arquivos_depois"]),
                f"{item['registros']:,}",
                f"{item['bytes_antes'] / 1024:,.0f}",
                f"{item['bytes_depois'] / 1024:,.0f}",
            )
        console.print(tabela)
        if not saida["compactacao"]:
            console.print("  nada a compactar — cada partição mensal já tem um único arquivo")
    if "retencao" in saida:
        ret = saida["retencao"]
        modo = "aplicada" if ret["aplicado"] else "simulada"
        console.print(
            f"retenção {modo} em {ret['data_referencia']}: {ret['avaliados']} objetos avaliados · "
            f"{ret['expirados']} além da retenção · {ret['removidos']} removidos"
        )
        for item in ret["amostra"][:10]:
            console.print(
                f"  {_AVISO} {item['camada']}/{item['chave']} · {item['idade_dias']} d "
                f"(limite {item['limite_dias']} d)"
            )


@app.command()
def sql(
    consulta: Annotated[str, typer.Argument(help="Consulta SQL sobre silver e gold.")],
    limite: Annotated[int, typer.Option(help="Máximo de linhas exibidas.")] = 40,
) -> None:
    """Consulta ad hoc (DuckDB) sobre as tabelas do silver e do gold."""
    from agrosmart.processamento.gold import abrir_conexao
    from agrosmart.storage import criar_armazenamento

    cfg = carregar_config()
    con = abrir_conexao(criar_armazenamento(cfg), silver=True, gold=True)
    try:
        resultado = con.execute(consulta)
        colunas = [d[0] for d in resultado.description or []]
        linhas = resultado.fetchmany(limite + 1)
    except Exception as erro:  # noqa: BLE001 - erro de SQL do usuário vai para a tela
        console.print(f"{_FALHA} {erro}")
        raise typer.Exit(code=1) from None
    finally:
        con.close()

    tabela = Table(box=box.SIMPLE_HEAD)
    for coluna in colunas:
        tabela.add_column(str(coluna))
    for linha in linhas[:limite]:
        tabela.add_row(*(str(v) for v in linha))
    console.print(tabela)
    sufixo = f" (mostrando {limite}; há mais)" if len(linhas) > limite else ""
    console.print(f"{len(linhas[:limite])} linha(s){sufixo}")


ia_app = typer.Typer(
    name="ia",
    help="Copiloto de IA generativa: contexto, recomendações e avaliação a partir do gold.",
    no_args_is_help=True,
)
app.add_typer(ia_app, name="ia")


def _casos(caso: str | None) -> list:
    from agrosmart.ia.esquema import Caso

    if caso is None:
        return list(Caso)
    try:
        return [Caso(caso)]
    except ValueError:
        console.print(
            f"{_FALHA} caso inválido: {caso!r} (use irrigacao, fitossanitario ou executivo)"
        )
        raise typer.Exit(code=2) from None


@ia_app.command("contexto")
def ia_contexto(
    talhao: Annotated[str, typer.Argument(help="Talhão, ex.: TLH-03")],
    salvar: Annotated[
        bool, typer.Option("--salvar", help="Gravar em outputs/relatorios/contexto/.")
    ] = False,
) -> None:
    """Mostra o Context Pack de um talhão — tudo que o copiloto pode saber sobre ele."""
    from agrosmart.ia.contexto import abrir_gold, construir_context_pack

    cfg = carregar_config()
    con = abrir_gold(cfg)
    try:
        pack = construir_context_pack(talhao, con=con, config=cfg)
    finally:
        con.close()
    texto = pack.para_json()
    console.print(texto if len(texto) < 12000 else texto[:12000] + "\n… (truncado; use --salvar)")
    console.print(
        f"\n{len(pack.numeros())} números citáveis · {len(pack.caminhos())} caminhos · "
        f"tabelas: {', '.join(pack.tabelas_gold_usadas)}"
    )
    if salvar:
        pasta = cfg.dir_outputs / "relatorios" / "contexto"
        pasta.mkdir(parents=True, exist_ok=True)
        destino = pasta / f"{talhao}_{pack.janela.hoje.isoformat()}.contexto.json"
        destino.write_text(texto, encoding="utf-8")
        console.print(f"{_OK} salvo em [bold]{destino.relative_to(cfg.raiz).as_posix()}[/bold]")


@ia_app.command("gerar")
def ia_gerar(
    talhao: Annotated[str | None, typer.Option(help="Só um talhão (padrão: todos).")] = None,
    caso: Annotated[
        str | None, typer.Option(help="irrigacao | fitossanitario | executivo (padrão: os três).")
    ] = None,
    motor: Annotated[str, typer.Option(help="auto | claude | regras")] = "auto",
    pdf: Annotated[bool, typer.Option("--pdf/--sem-pdf")] = True,
) -> None:
    """Gera recomendações (Markdown, PDF e proveniência) em outputs/relatorios/<caso>/."""
    from agrosmart.ia.agente import Copiloto
    from agrosmart.ia.contexto import abrir_gold, construir_context_pack, talhoes_disponiveis
    from agrosmart.ia.render import salvar

    if motor not in {"auto", "claude", "regras"}:
        console.print(f"{_FALHA} motor inválido: {motor!r}")
        raise typer.Exit(code=2)
    cfg = carregar_config()
    copiloto = Copiloto(cfg, motor_preferido=motor)  # type: ignore[arg-type]
    con = abrir_gold(cfg)
    try:
        alvos = [talhao] if talhao else talhoes_disponiveis(con)
        packs = {t: construir_context_pack(t, con=con, config=cfg) for t in alvos}
    finally:
        con.close()

    tabela = Table(
        title=f"Copiloto · motor {'claude' if copiloto.usa_modelo else 'regras'}",
        box=box.SIMPLE_HEAD,
    )
    for coluna in (
        "talhão",
        "caso",
        "motor",
        "confiança",
        "prioridade",
        "recomendações",
        "guardrails",
        "arquivo",
    ):
        tabela.add_column(coluna)
    for t, pack in packs.items():
        for c in _casos(caso):
            resposta = copiloto.responder(pack, c)
            caminhos = salvar(resposta, cfg, pdf=pdf)
            estado = (_OK if resposta.guardrails.aprovado else _FALHA) + (
                f" {len(resposta.guardrails.ajustes)} ajuste(s)"
                if resposta.guardrails.ajustes
                else ""
            )
            motor_txt = resposta.motor + (
                " [yellow](fallback)[/yellow]" if resposta.fallback else ""
            )
            tabela.add_row(
                t,
                c.value,
                motor_txt,
                resposta.saida.confianca,
                resposta.saida.prioridade_maxima,
                str(len(resposta.saida.recomendacoes)),
                estado,
                caminhos[0].relative_to(cfg.raiz).as_posix(),
            )
    console.print(tabela)


@ia_app.command("avaliar")
def ia_avaliar(
    talhao: Annotated[str | None, typer.Option(help="Só um talhão (padrão: todos).")] = None,
    sem_modelo: Annotated[
        bool, typer.Option("--sem-modelo", help="Avaliar só o motor de regras.")
    ] = False,
) -> None:
    """Roda casos × talhões × motores e mede guardrails, fallbacks e concordância."""
    from agrosmart.ia.avaliacao import avaliar

    cfg = carregar_config()
    relatorio = avaliar(cfg, talhoes=[talhao] if talhao else None, incluir_modelo=not sem_modelo)
    tabela = Table(title="Avaliação por motor", box=box.SIMPLE_HEAD)
    for coluna in (
        "motor",
        "execuções",
        "aprovadas",
        "ajustes",
        "fallbacks",
        "latência média",
        "alta/média/baixa",
        "irrigar",
    ):
        tabela.add_column(coluna, justify="right" if coluna != "motor" else "left")
    for motor, m in relatorio["por_motor"].items():
        if not m.get("execucoes"):
            continue
        conf = m["confianca"]
        tabela.add_row(
            motor,
            str(m["execucoes"]),
            str(m["aprovados_guardrails"]),
            str(m["com_ajustes"]),
            str(m["fallbacks"]),
            f"{m['latencia_media_s']} s",
            f"{conf['alta']}/{conf['media']}/{conf['baixa']}",
            str(m["recomenda_irrigar"]),
        )
    console.print(tabela)
    conc = relatorio.get("concordancia_regras_vs_modelo")
    if conc:
        console.print(
            f"concordância regras × modelo em {conc['pares']} pares: "
            f"irrigar {conc['irrigar_igual_pct']} % · "
            f"prioridade {conc['prioridade_igual_pct']} % · "
            f"confiança {conc['confianca_igual_pct']} % · "
            f"tipos (Jaccard) {conc['tipos_jaccard_medio']}"
        )
    elif not relatorio["motor_generativo_ativo"]:
        console.print(
            f"{_AVISO} motor generativo inativo (sem ANTHROPIC_API_KEY): "
            "só o motor de regras foi avaliado"
        )
    console.print(
        f"{_OK} relatório em [bold]outputs/avaliacao_ia.json[/bold] e "
        f"[bold]outputs/relatorios/avaliacao_ia.md[/bold] · {relatorio['duracao_s']}s"
    )


@app.command()
def run(
    tudo: Annotated[
        bool, typer.Option("--all", help="Simular, ingerir, validar, processar e gerar IA.")
    ] = False,
    inicio: Annotated[
        str | None, typer.Option("--from", help="Backfill: data inicial AAAA-MM-DD.")
    ] = None,
    fim: Annotated[
        str | None, typer.Option("--to", help="Backfill: data final AAAA-MM-DD.")
    ] = None,
    sem_simular: Annotated[
        bool, typer.Option("--sem-simular", help="Não gerar landing (origens reais).")
    ] = False,
    sem_ia: Annotated[bool, typer.Option("--sem-ia", help="Não gerar recomendações.")] = False,
    forcar: Annotated[
        bool, typer.Option("--forcar", help="Reingerir lotes já presentes no bronze.")
    ] = False,
) -> None:
    """Orquestra o pipeline completo, com suporte a backfill por intervalo."""
    from datetime import date

    from agrosmart.orquestracao import executar

    if not tudo and not (inicio or fim):
        console.print("Use [bold]--all[/bold] ou um intervalo [bold]--from/--to[/bold].")
        raise typer.Exit(code=2)
    backfill = None
    if inicio or fim:
        if not (inicio and fim):
            console.print(f"{_FALHA} backfill exige --from e --to")
            raise typer.Exit(code=2)
        try:
            backfill = (date.fromisoformat(inicio), date.fromisoformat(fim))
        except ValueError as erro:
            console.print(f"{_FALHA} datas inválidas: {erro}")
            raise typer.Exit(code=2) from None
        if backfill[0] > backfill[1]:
            console.print(f"{_FALHA} --from precisa ser anterior ou igual a --to")
            raise typer.Exit(code=2)

    resumo = executar(
        carregar_config(),
        simular_fontes=not sem_simular,
        ia=not sem_ia,
        backfill=backfill,
        forcar_ingestao=forcar,
    )
    tabela = Table(title=f"agrosmart run · {resumo['modo']}", box=box.SIMPLE_HEAD)
    tabela.add_column("etapa", style="bold")
    tabela.add_column("status")
    tabela.add_column("s", justify="right")
    tabela.add_column("resumo")
    for etapa in resumo["etapas"]:
        cor = {"ok": "green", "erro": "red", "pulada": "yellow"}[etapa["status"]]
        detalhe = etapa["erro"] or " · ".join(f"{k}={v}" for k, v in etapa["resumo"].items())
        tabela.add_row(
            etapa["nome"], f"[{cor}]{etapa['status']}[/{cor}]", f"{etapa['duracao_s']:.1f}", detalhe
        )
    console.print(tabela)
    estado = _OK if resumo["sucesso"] else _FALHA
    console.print(
        f"{estado} {resumo['modo']} em {resumo['duracao_s']}s · resumo em "
        "[bold]outputs/run.json[/bold]"
    )
    if not resumo["sucesso"]:
        raise typer.Exit(code=1)


@app.command()
def empacotar(
    nome: Annotated[str, typer.Option(help="Nome completo do integrante (será normalizado).")],
    rm: Annotated[str, typer.Option(help="RM do integrante.")],
    turma: Annotated[str, typer.Option(help="Turma.")],
    fase: Annotated[
        int, typer.Option(help="Fase da entrega: 7 (padrão), 6 ou 5.")
    ] = 7,
    simular: Annotated[
        bool, typer.Option("--simular", help="Só listar o que entraria, sem gravar.")
    ] = False,
) -> None:
    """Monta o zip da entrega no padrão nomecompleto_rm_turma_fase<N>_atividade.zip e o testa."""
    from agrosmart.empacotar import ErroEmpacotamento
    from agrosmart.empacotar import empacotar as executar_empacotamento

    try:
        resultado = executar_empacotamento(nome, rm, turma, fase=fase, simular=simular)
    except ErroEmpacotamento as erro:
        console.print(f"{_FALHA} {erro}")
        raise typer.Exit(code=1) from None

    console.print(f"[bold]{resultado.destino.name}[/bold] · fase {resultado.fase}")
    comprimido = f" → {resultado.bytes_zip / 1024:,.0f} KB comprimidos" if not simular else ""
    console.print(
        f"{resultado.arquivos} arquivos · {resultado.bytes_originais / 1024:,.0f} KB originais"
        + (comprimido or " (simulação)")
    )
    if simular:
        for item in resultado.inventario[:40]:
            console.print(f"  {item}")
        if len(resultado.inventario) > 40:
            console.print(f"  … e mais {len(resultado.inventario) - 40}")
    else:
        console.print(
            (_OK if resultado.integridade_ok else _FALHA)
            + " integridade do zip "
            + ("verificada" if resultado.integridade_ok else "FALHOU")
            + " · inventário em [bold]entrega/CONTEUDO_DO_ZIP.txt[/bold]"
        )
    for aviso in resultado.avisos:
        console.print(f"  {_AVISO} {aviso}")
    if not simular and not resultado.integridade_ok:
        raise typer.Exit(code=1)


# --- Fase 6: automação ---


@app.command()
def automacao(
    data: Annotated[
        str | None, typer.Option(help="Data de referência AAAA-MM-DD (padrão: última do gold).")
    ] = None,
    publicar: Annotated[
        bool, typer.Option("--publicar", help="Publicar os comandos de irrigação via MQTT (QoS 1).")
    ] = False,
    broker: Annotated[str, typer.Option(help="Host do broker MQTT.")] = "localhost",
    porta: Annotated[int, typer.Option(help="Porta do broker MQTT.")] = 1883,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Só mostrar o plano: não grava nem publica.")
    ] = False,
    motor: Annotated[
        str, typer.Option(help="Justificativa do copiloto: auto | claude | regras | nenhum.")
    ] = "auto",
) -> None:
    """Lê o gold, decide por talhão e age: plano em outputs/acoes/ (+ MQTT com --publicar)."""
    from datetime import date

    from agrosmart.automacao.atuadores import ErroPublicacao
    from agrosmart.automacao.motor import executar as executar_automacao

    if motor not in {"auto", "claude", "regras", "nenhum"}:
        console.print(f"{_FALHA} motor inválido: {motor!r}")
        raise typer.Exit(code=2)
    data_ref = None
    if data:
        try:
            data_ref = date.fromisoformat(data)
        except ValueError:
            console.print(f"{_FALHA} data inválida: {data!r} (use AAAA-MM-DD)")
            raise typer.Exit(code=2) from None

    cfg = carregar_config()
    try:
        saida = executar_automacao(
            cfg,
            data=data_ref,
            publicar=publicar,
            broker=broker,
            porta=porta,
            dry_run=dry_run,
            copiloto=None if motor == "nenhum" else motor,
        )
    except (ValueError, ErroPublicacao) as erro:
        console.print(f"{_FALHA} {erro}")
        raise typer.Exit(code=1) from None
    plano = saida["plano"]

    talhoes = Table(
        title=f"Plano de ações · {plano.data_ref}" + (" · dry-run" if dry_run else ""),
        box=box.SIMPLE_HEAD,
    )
    colunas = (
        "talhão",
        "cultura",
        "risco",
        "FAD",
        "déficit mm",
        "cobertura 7d",
        "decisão",
        "ações",
    )
    for coluna in colunas:
        talhoes.add_column(coluna, justify="right" if coluna in {"FAD", "déficit mm"} else "left")
    cor_risco = {"critico": "red", "atencao": "yellow", "normal": "green"}

    def _num(valor: object, formato: str, sufixo: str = "") -> str:
        return "n/d" if valor is None else f"{valor:{formato}}{sufixo}"

    for t in plano.talhoes:
        i = t.indicadores
        talhoes.add_row(
            t.talhao_id,
            f"{i.get('cultura') or 'n/d'} · {i.get('sistema_irrigacao') or 'n/d'}",
            f"[{cor_risco[t.risco]}]{t.risco}[/{cor_risco[t.risco]}]",
            _num(i.get("fracao_agua_disponivel"), ".3f"),
            _num(i.get("deficit_mm"), ".1f"),
            _num(i.get("cobertura_min_7d_pct"), ".0f", " %"),
            i.get("situacao_hidrica", ""),
            ", ".join(a.id for a in plano.acoes_de(t.talhao_id)) or "nenhuma",
        )
    console.print(talhoes)

    acoes = Table(title="Ações", box=box.SIMPLE_HEAD)
    for coluna in ("id", "talhão", "sensor", "tipo", "prioridade", "parâmetros", "MQTT", "status"):
        acoes.add_column(coluna)
    for a in plano.acoes:
        p = a.parametros
        if a.tipo == "irrigar":
            params = f"{p['lamina_mm']} mm · {p['minutos']} min · {p['janela']}"
        elif a.tipo == "alerta_fitossanitario":
            params = f"{p['dias_ur_alta_7d']} dia(s) UR alta · {p['temp_media_7d_c']} °C"
        elif a.tipo == "trocar_bateria":
            params = f"bateria {p['bateria_pct']} %"
        else:
            params = p.get("motivo_tecnico", "")
        comando = f"{a.comando_mqtt.topico} {a.comando_mqtt.payload}" if a.comando_mqtt else "—"
        cor = {"alta": "red", "media": "yellow", "baixa": "white"}[a.prioridade]
        acoes.add_row(
            a.id,
            a.talhao_id,
            a.sensor_id or "—",
            a.tipo,
            f"[{cor}]{a.prioridade}[/{cor}]",
            params,
            comando,
            "[green]publicado[/green]" if a.status == "publicado" else a.status,
        )
    console.print(acoes)
    if not plano.acoes:
        console.print("  nenhuma ação: todos os talhões dentro dos limiares")

    r = plano.resumo
    console.print(
        f"{r.talhoes} talhões · {r.acoes} ação(ões) · por tipo {r.por_tipo} · "
        f"por prioridade {r.por_prioridade}"
    )
    pub = saida["publicacao"]
    if pub["solicitada"]:
        estado = _OK if not pub["falhas"] else _FALHA
        console.print(
            f"{estado} MQTT {broker}:{porta}: {pub['publicadas']} comando(s) publicado(s)"
            + (f", {len(pub['falhas'])} falha(s)" if pub["falhas"] else "")
        )
        for falha in pub["falhas"]:
            console.print(f"  {_AVISO} {falha['id']} {falha['topico']}: {falha['erro']}")
    if dry_run:
        console.print(f"{_AVISO} dry-run: nada gravado, nada publicado")
    else:
        destinos = " · ".join(
            f"[bold]{c.relative_to(cfg.raiz).as_posix()}[/bold]" for c in saida["caminhos"]
        )
        console.print(f"{_OK} plano em {destinos}")
    if pub["falhas"]:
        raise typer.Exit(code=1)


# --- fim Fase 6: automação ---


# ---------------------------------------------------------------------------
# --- Fase 6: coleta IoT ---
# A origem real da fonte ``sensores``: dispositivos (ESP32 no Wokwi ou o emulador
# local) publicam por MQTT; o gateway materializa a landing que o ``ingerir`` já lê.
# ---------------------------------------------------------------------------


@app.command("coletar-mqtt")
def coletar_mqtt(
    broker: Annotated[str, typer.Option(help="Host do broker MQTT.")] = "localhost",
    porta: Annotated[int, typer.Option(help="Porta do broker.")] = 1883,
    topico: Annotated[
        str, typer.Option(help="Tópico a assinar (aceita curingas).")
    ] = "agrosmart/sensores/+/leitura",
    janela_min: Annotated[int, typer.Option(help="Minutos por arquivo JSONL (contrato: 15).")] = 15,
    duracao_s: Annotated[
        int | None, typer.Option(help="Encerrar após N segundos (padrão: até Ctrl+C).")
    ] = None,
    saida: Annotated[
        Path | None, typer.Option(help="Pasta da landing de sensores (padrão: landing/sensores).")
    ] = None,
) -> None:
    """Assina o tópico dos sensores no broker MQTT e grava micro-batches JSONL na landing."""
    from agrosmart.ingestao.mqtt_gateway import ErroGateway, GatewayMqtt

    cfg = carregar_config()
    try:
        gateway = GatewayMqtt(
            cfg, broker=broker, porta=porta, topico=topico, janela_min=janela_min, saida=saida
        )
        resumo = gateway.executar(duracao_s=duracao_s)
    except ErroGateway as erro:
        console.print(f"{_FALHA} {erro}")
        console.print(
            "  suba o broker local com: docker compose -f infra/docker-compose.yml up -d mosquitto"
        )
        raise typer.Exit(code=1) from None

    tabela = Table(title=f"Coleta MQTT · {resumo.broker} · {resumo.topico}", box=box.SIMPLE_HEAD)
    tabela.add_column("métrica", style="bold")
    tabela.add_column("valor", justify="right")
    tabela.add_row("mensagens recebidas", str(resumo.recebidas))
    tabela.add_row("leituras gravadas", str(resumo.gravadas))
    tabela.add_row("duplicatas ignoradas", str(resumo.duplicatas))
    descartadas = f"[red]{resumo.total_descartadas}[/red]" if resumo.total_descartadas else "0"
    tabela.add_row("descartadas (inválidas)", descartadas)
    for motivo, n in sorted(resumo.descartadas.items()):
        tabela.add_row(f"  · {motivo}", str(n))
    for chave, n in sorted(resumo.chaves_extras.items()):
        tabela.add_row(f"chave extra ignorada · {chave}", str(n))
    tabela.add_row("sensores distintos", str(len(resumo.por_sensor)))
    tabela.add_row("arquivos escritos", str(len(resumo.arquivos)))
    tabela.add_row("reconexões", str(resumo.reconexoes))
    console.print(tabela)
    for arquivo, linhas in resumo.arquivos.items():
        console.print(f"  {arquivo}  ({linhas} linha(s) nesta execução)")
    console.print(f"{_OK} resumo em [bold]outputs/coleta_mqtt.json[/bold]")


@app.command()
def dispositivo(
    broker: Annotated[str, typer.Option(help="Host do broker MQTT.")] = "localhost",
    porta: Annotated[int, typer.Option(help="Porta do broker.")] = 1883,
    n: Annotated[int, typer.Option(help="Quantos dispositivos do catálogo emular (1–12).")] = 12,
    intervalo_s: Annotated[float, typer.Option(help="Segundos entre publicações.")] = 5.0,
    duracao_s: Annotated[
        int | None, typer.Option(help="Encerrar após N segundos (padrão: até Ctrl+C).")
    ] = None,
    seed: Annotated[int, typer.Option(help="Semente do ruído das leituras.")] = 1,
) -> None:
    """Emula os dispositivos de campo (par local do firmware ESP32) publicando no broker."""
    from agrosmart.simuladores.dispositivo_mqtt import Frota, publicar

    try:
        frota = Frota(carregar_config(), n=n, seed=seed)
        resumo = publicar(
            frota, broker=broker, porta=porta, intervalo_s=intervalo_s, duracao_s=duracao_s
        )
    except (ValueError, ConnectionError) as erro:
        console.print(f"{_FALHA} {erro}")
        raise typer.Exit(code=1) from None

    tabela = Table(title=f"Dispositivos emulados · {broker}:{porta}", box=box.SIMPLE_HEAD)
    tabela.add_column("métrica", style="bold")
    tabela.add_column("valor", justify="right")
    tabela.add_row("dispositivos", str(resumo.dispositivos))
    tabela.add_row("ciclos", str(resumo.ciclos))
    tabela.add_row("leituras publicadas", str(resumo.publicadas))
    tabela.add_row("  · atrasadas (buffer)", str(resumo.atrasadas))
    tabela.add_row("  · duplicadas (reenvio)", str(resumo.duplicadas))
    tabela.add_row("comandos de irrigação recebidos", str(resumo.comandos))
    console.print(tabela)


# --- fim Fase 6: coleta IoT ---


# ---------------------------------------------------------------------------
# --- Fase 7: operação contínua e monitoramento ---
# ---------------------------------------------------------------------------


@app.command()
def operar(
    intervalo_s: Annotated[
        float, typer.Option(help="Segundos entre o início de dois ciclos.")
    ] = 60,
    ciclos: Annotated[
        int | None, typer.Option(help="Parar após N ciclos (padrão: até SIGTERM/Ctrl+C).")
    ] = None,
    publicar: Annotated[
        bool, typer.Option("--publicar/--sem-publicar", help="Publicar comandos MQTT ao atuador.")
    ] = True,
    broker: Annotated[str, typer.Option(help="Host do broker MQTT.")] = "localhost",
    porta: Annotated[int, typer.Option(help="Porta do broker MQTT.")] = 1883,
    porta_metricas: Annotated[
        int, typer.Option(help="Porta HTTP de /health, /metrics e /status (0 desliga).")
    ] = 8000,
    incremental: Annotated[
        bool,
        typer.Option(
            "--incremental/--completo",
            help="v2: pula silver/gold sem lote novo e reprocessa só o intervalo afetado.",
        ),
    ] = False,
    carga_inicial: Annotated[
        str, typer.Option(help="auto (se o gold estiver vazio) | sempre | nunca.")
    ] = "auto",
) -> None:
    """Worker: roda o ciclo ingestão → silver → gold → decisão → atuação em intervalo fixo."""
    from agrosmart.operacao.worker import operar as executar_worker

    if carga_inicial not in {"auto", "sempre", "nunca"}:
        console.print(f"{_FALHA} --carga-inicial inválida: {carga_inicial!r}")
        raise typer.Exit(code=2)
    estado = executar_worker(
        carregar_config(),
        intervalo_s=intervalo_s,
        ciclos=ciclos,
        publicar=publicar,
        broker=broker,
        porta=porta,
        porta_metricas=porta_metricas or None,
        incremental=incremental,
        carga_inicial=carga_inicial,
    )
    resumo = estado.para_dict()
    console.print(
        f"{_OK if resumo['saudavel'] else _FALHA} worker encerrado · ciclos {resumo['ciclos']} · "
        f"média {resumo['duracao_media_ciclo_s']}s · status em "
        "[bold]outputs/status/operacao.json[/bold]"
    )


@app.command()
def status(
    url: Annotated[
        str | None,
        typer.Option(help="Consultar o worker por HTTP (ex.: http://localhost:8000/status)."),
    ] = None,
) -> None:
    """Como está a operação: saúde, último ciclo, etapas e indicadores do worker."""
    import json
    from datetime import UTC, datetime

    cfg = carregar_config()
    if url:
        import urllib.request

        if not url.startswith(("http://", "https://")):
            console.print(f"{_FALHA} URL precisa começar com http:// ou https://")
            raise typer.Exit(code=2)
        with urllib.request.urlopen(url, timeout=5) as resposta:  # noqa: S310 - esquema validado
            dados = json.loads(resposta.read().decode("utf-8"))
    else:
        caminho = cfg.dir_outputs / "status" / "operacao.json"
        if not caminho.exists():
            console.print(f"{_AVISO} sem {caminho.relative_to(cfg.raiz)}: o worker ainda não rodou")
            raise typer.Exit(code=1)
        dados = json.loads(caminho.read_text(encoding="utf-8"))

    def _quando(epoch: float | None) -> str:
        if not epoch:
            return "—"
        return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")

    estado = _OK if dados["saudavel"] else _FALHA
    console.print(
        f"{estado} [bold]{'saudável' if dados['saudavel'] else 'degradado'}[/bold] · "
        f"{dados['motivo']} · fase {dados['fase']} · modo {dados['modo']} "
        f"({dados['versao_processo']}) · intervalo {dados['intervalo_s']:.0f}s"
    )
    console.print(
        f"ciclos {dados['ciclos']} · duração média {dados['duracao_media_ciclo_s']}s · "
        f"último sucesso {_quando(dados.get('ultimo_sucesso_em'))} · "
        f"comandos publicados {dados['comandos_publicados']}"
    )
    ultimo = dados.get("ultimo_ciclo") or {}
    if ultimo:
        tabela = Table(title=f"Último ciclo · {ultimo['execucao_id']}", box=box.SIMPLE_HEAD)
        for coluna in ("etapa", "status", "s", "erro"):
            tabela.add_column(coluna)
        cores = {"ok": "green", "erro": "red", "pulada": "yellow"}
        for e in ultimo["etapas"]:
            cor = cores.get(e["status"], "white")
            tabela.add_row(
                e["nome"],
                f"[{cor}]{e['status']}[/{cor}]",
                f"{e['duracao_s']:.1f}",
                (e.get("erro") or "")[:80],
            )
        console.print(tabela)
    console.print(
        f"lotes {dados['lotes']} · silver {dados['silver']} · plano {dados['acoes']} · "
        f"landing {dados['landing']}"
    )
    if not dados["saudavel"]:
        raise typer.Exit(code=1)


@app.command()
def processos(
    eventos: Annotated[
        Path | None,
        typer.Option(help="Trilha JSONL (padrão: outputs/plataforma/eventos.jsonl ou logs/)."),
    ] = None,
    saida: Annotated[Path | None, typer.Option(help="Pasta (padrão: outputs/processos).")] = None,
    incluir_cli: Annotated[
        bool, typer.Option("--incluir-cli", help="Incluir runs da CLI além do worker.")
    ] = False,
) -> None:
    """Mineração de processos: event log CSV/XES, DFG, variantes, gargalos e v1 × v2."""
    from agrosmart.mineracao import minerar

    cfg = carregar_config()
    if eventos is None:
        exportado = cfg.dir_outputs / "plataforma" / "eventos.jsonl"
        eventos = exportado if exportado.exists() else cfg.dir_logs / "eventos.jsonl"
    saida = saida or cfg.dir_outputs / "processos"
    if not eventos.exists():
        console.print(f"{_FALHA} trilha não encontrada: {eventos}")
        raise typer.Exit(code=1)
    metricas = minerar(eventos, saida, apenas_worker=not incluir_cli)
    esc, analise = metricas["escopo"], metricas["analise"]
    console.print(
        f"{_OK} {esc['casos']} caso(s) · {esc['instancias_atividade']} atividades · "
        f"pm4py={metricas['configuracao']['pm4py']}"
    )
    if analise["gargalos"]:
        g = analise["gargalos"][0]
        console.print(
            f"gargalo: [bold]{g['atividade']}[/bold] ({g['pct_lead_time']}% do lead time)"
        )
    for i, insight in enumerate(metricas["insights"][:5], 1):
        console.print(f"  {i}. [bold]{insight['tema']}[/bold] — {insight['evidencia']}")
    console.print(f"{_OK} relatório em [bold]{saida / 'relatorio.md'}[/bold]")


# --- fim Fase 7 ---


@app.command()
def versao() -> None:
    """Mostra a versão."""
    console.print(f"agrosmart {__version__} · python {sys.version.split()[0]}")


if __name__ == "__main__":
    app()
