"""Manutenção do lake: compactação de arquivos pequenos e retenção por contrato.

**Compactação.** O silver é escrito com um Parquet por dia de evento — o que
uma execução diária produziria. Em fontes de alta frequência isso vira o
problema clássico de *small files*: 30 arquivos por mês para uma tabela que
caberia em um. ``compactar`` reescreve cada partição mensal em um único
arquivo, preservando exatamente os registros, e remove os diários.

**Retenção.** Cada contrato declara quantos dias o dado fica em cada camada
(``retencao``). ``aplicar_retencao`` percorre as partições Hive
(``ano=/mes=/dia=``), calcula a idade em relação à data de referência do
pipeline e lista — ou remove, com ``aplicar=True`` — o que passou do prazo.
No MinIO isso convive com as regras de ciclo de vida do bucket; aqui a regra
sai do contrato, então muda quando o contrato muda.
"""

from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import polars as pl

from agrosmart.config import Camada, Config, carregar_config
from agrosmart.contratos import Contrato, contratos_ativos
from agrosmart.log import Evento, obter_logger, registrar_evento
from agrosmart.storage import Armazenamento, criar_armazenamento

_PARTICAO_DIA = re.compile(r"ano=(\d{4})/mes=(\d{2})/dia=(\d{2})")
_PARTICAO_MES = re.compile(r"^(?P<tabela>[^/]+)/ano=(?P<ano>\d{4})/mes=(?P<mes>\d{2})/")

# A quarentena não tem contrato próprio: retenção fixa, igual à do bucket.
RETENCAO_QUARENTENA_DIAS = 30
TIPO_PARQUET = "application/vnd.apache.parquet"


# ---------------------------------------------------------------------------
# Compactação
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ResultadoCompactacao:
    tabela: str
    particao: str
    arquivos_antes: int
    arquivos_depois: int
    registros: int
    bytes_antes: int
    bytes_depois: int


def compactar(
    armazenamento: Armazenamento,
    *,
    camada: Camada = Camada.SILVER,
    tabelas: list[str] | None = None,
) -> list[ResultadoCompactacao]:
    """Um Parquet por partição mensal. Devolve só as partições que mudaram."""
    logger = obter_logger("manutencao.compactacao")
    alvos = tabelas or [c.destino.silver_tabela for c in contratos_ativos().values()]
    resultados: list[ResultadoCompactacao] = []

    for tabela in alvos:
        objetos = [
            o
            for o in armazenamento.listar(camada, f"{tabela}/")
            if o.chave.endswith(".parquet") and "/dia=" in o.chave
        ]
        por_mes: dict[str, list] = {}
        for objeto in objetos:
            casamento = _PARTICAO_MES.match(objeto.chave)
            if casamento:
                por_mes.setdefault(casamento.group(0), []).append(objeto)

        for prefixo, grupo in sorted(por_mes.items()):
            if len(grupo) < 2:
                continue
            partes = [
                pl.read_parquet(io.BytesIO(armazenamento.ler_bytes(camada, o.chave))) for o in grupo
            ]
            unido = pl.concat(partes, how="vertical_relaxed")
            casamento = _PARTICAO_MES.match(prefixo)
            if casamento is None:  # verificação explícita (não some com -O)
                raise ValueError(f"prefixo fora do padrão de partição mensal: {prefixo!r}")
            ano_mes = f"{casamento.group('ano')}{casamento.group('mes')}"
            destino = f"{prefixo}{tabela}-{ano_mes}-compactado.parquet"
            buffer = io.BytesIO()
            unido.write_parquet(buffer, compression="zstd")
            gravado = armazenamento.gravar_bytes(
                camada, destino, buffer.getvalue(), content_type=TIPO_PARQUET
            )
            for objeto in grupo:
                armazenamento.remover(camada, objeto.chave)
            resultado = ResultadoCompactacao(
                tabela=tabela,
                particao=prefixo.rstrip("/"),
                arquivos_antes=len(grupo),
                arquivos_depois=1,
                registros=unido.height,
                bytes_antes=sum(o.tamanho_bytes for o in grupo),
                bytes_depois=gravado.tamanho_bytes,
            )
            resultados.append(resultado)
            logger.info(
                "%s: %d arquivos → 1 · %d registros · %.0f KB → %.0f KB",
                resultado.particao,
                resultado.arquivos_antes,
                resultado.registros,
                resultado.bytes_antes / 1024,
                resultado.bytes_depois / 1024,
            )
    return resultados


# ---------------------------------------------------------------------------
# Retenção
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ItemRetencao:
    camada: str
    chave: str
    particao: str
    idade_dias: int
    limite_dias: int
    fonte: str


@dataclass(slots=True)
class ResultadoRetencao:
    aplicado: bool
    data_referencia: str
    avaliados: int = 0
    expirados: list[ItemRetencao] = field(default_factory=list)
    removidos: int = 0
    por_camada: dict[str, int] = field(default_factory=dict)

    def para_dict(self) -> dict[str, Any]:
        return {
            "aplicado": self.aplicado,
            "data_referencia": self.data_referencia,
            "avaliados": self.avaliados,
            "expirados": len(self.expirados),
            "removidos": self.removidos,
            "por_camada": self.por_camada,
            "amostra": [asdict(i) for i in self.expirados[:20]],
        }


def _particao_de(chave: str) -> date | None:
    casamento = _PARTICAO_DIA.search(chave)
    if not casamento:
        return None
    ano, mes, dia = (int(g) for g in casamento.groups())
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def _alvos_retencao(contrato: Contrato) -> list[tuple[Camada, str, int | None]]:
    """(camada, prefixo, limite em dias) para cada lugar onde a fonte mora."""
    return [
        (Camada.BRONZE, f"{contrato.fonte}/", contrato.retencao.bronze_dias),
        (Camada.BRONZE, f"_manifests/{contrato.fonte}/", contrato.retencao.bronze_dias),
        (Camada.SILVER, f"{contrato.destino.silver_tabela}/", contrato.retencao.silver_dias),
        (Camada.QUARENTENA, f"{contrato.fonte}/", RETENCAO_QUARENTENA_DIAS),
    ]


def aplicar_retencao(
    armazenamento: Armazenamento,
    config: Config | None = None,
    *,
    aplicar: bool = False,
    contratos: dict[str, Contrato] | None = None,
) -> ResultadoRetencao:
    """Lista (e opcionalmente remove) partições mais velhas que a retenção do contrato."""
    cfg = config or carregar_config()
    logger = obter_logger("manutencao.retencao")
    todos = contratos or contratos_ativos()
    resultado = ResultadoRetencao(aplicado=aplicar, data_referencia=cfg.data_referencia.isoformat())

    for contrato in todos.values():
        for camada, prefixo, limite in _alvos_retencao(contrato):
            if limite is None:
                continue  # retenção permanente
            for objeto in armazenamento.listar(camada, prefixo):
                particao = _particao_de(objeto.chave)
                if particao is None:
                    continue
                resultado.avaliados += 1
                idade = (cfg.data_referencia - particao).days
                if idade > limite:
                    resultado.expirados.append(
                        ItemRetencao(
                            camada.value,
                            objeto.chave,
                            particao.isoformat(),
                            idade,
                            limite,
                            contrato.fonte,
                        )
                    )
                    resultado.por_camada[camada.value] = (
                        resultado.por_camada.get(camada.value, 0) + 1
                    )

    if aplicar:
        for item in resultado.expirados:
            armazenamento.remover(Camada(item.camada), item.chave)
        resultado.removidos = len(resultado.expirados)

    logger.info(
        "retenção: %d objetos avaliados · %d expirados · %s",
        resultado.avaliados,
        len(resultado.expirados),
        f"{resultado.removidos} removidos" if aplicar else "simulação (nada removido)",
    )
    registrar_evento(
        Evento(
            etapa="manutencao",
            acao="retencao_aplicada" if aplicar else "retencao_simulada",
            status="ok",
            mensagem=f"{len(resultado.expirados)} objetos além da retenção",
            metricas={
                "avaliados": resultado.avaliados,
                "expirados": len(resultado.expirados),
                "removidos": resultado.removidos,
            },
        ),
        cfg,
    )
    return resultado


def manter(
    config: Config | None = None,
    armazenamento: Armazenamento | None = None,
    *,
    compactar_silver: bool = True,
    retencao: bool = True,
    aplicar_retencao_: bool = False,
) -> dict[str, Any]:
    cfg = config or carregar_config()
    lake = armazenamento or criar_armazenamento(cfg)
    saida: dict[str, Any] = {}
    if compactar_silver:
        compactados = compactar(lake)
        saida["compactacao"] = [asdict(c) for c in compactados]
        registrar_evento(
            Evento(
                etapa="manutencao",
                acao="compactacao",
                camada=Camada.SILVER.value,
                mensagem=f"{len(compactados)} partições compactadas",
                metricas={
                    "particoes": len(compactados),
                    "arquivos_removidos": sum(c.arquivos_antes - 1 for c in compactados),
                },
            ),
            cfg,
        )
    if retencao:
        saida["retencao"] = aplicar_retencao(lake, cfg, aplicar=aplicar_retencao_).para_dict()
    return saida
