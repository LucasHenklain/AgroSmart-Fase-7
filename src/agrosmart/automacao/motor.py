"""Motor de automação: lê o gold, decide por talhão e produz o plano de ações.

O plano é **determinístico**: para o mesmo gold e a mesma data de referência,
sai o mesmo JSON (fora ``gerado_em``). Nenhum indicador é recalculado aqui —
fração de água disponível, déficit, estresse, cobertura, bateria e sinal vêm
prontos de ``fato_balanco_hidrico``, ``fato_leitura_diaria_talhao`` e
``dim_sensor``. O motor só compara com os limiares de ``agronomia.py`` (os
mesmos do gold e do copiloto) e transforma a comparação em ação:

==========================  ======================================  ===================
decisão                     condição                                origem do limiar
==========================  ======================================  ===================
``irrigar``                 irrigado e FAD < ``FRACAO_IRRIGACAO``   agronomia.py
``alerta_fitossanitario``   ≥ ``DIAS_UR_ALTA_RISCO`` dias com UR do  agronomia.py
                            ar > ``UR_ALTA_PCT`` em 7 e temperatura
                            média na faixa ``TEMP_RISCO_FUNGICO_C``
``trocar_bateria``          bateria < ``BATERIA_MANUTENCAO_PCT``    agronomia.py
``inspecionar_sensor``      RSSI < ``RSSI_FRACO_DBM`` ou > 5 % das  agronomia.py /
                            leituras fracas; cobertura < 80 % em    ia/contexto.py /
                            algum dia da semana; sem umidade em 14d ia/guardrails.py G4
``nenhuma``                 nada acima                              —
==========================  ======================================  ===================

A lâmina de irrigação segue a mesma regra do Context Pack (85 % do déficit até
a capacidade de campo, máximo de 30 mm, múltiplos de 0,5 mm). Os minutos vêm
da taxa de aplicação do sistema de irrigação — parâmetro do **atuador**, não
limiar agronômico, declarado em :data:`TAXA_APLICACAO_MM_H`.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict

from agrosmart import agronomia
from agrosmart.config import Camada, Config, carregar_config
from agrosmart.log import Evento, obter_logger, registrar_evento
from agrosmart.processamento.silver import ler_tabela
from agrosmart.storage import Armazenamento, criar_armazenamento

FONTE = "gold.fato_balanco_hidrico + fato_leitura_diaria_talhao + dim_sensor"
VERSAO_PLANO = "plano-acoes.v1"

# Janelas — as mesmas do Context Pack (ia/contexto.py): 14 dias de balanço, 7 de sensores.
DIAS_JANELA_HIDRICA = 14
DIAS_JANELA_SENSORES = 7
# Cobertura mínima de leituras: abaixo disso o copiloto rebaixa a confiança
# (ia/regras.py ``_confianca_por_dados`` e ia/guardrails.py G4). Aqui vira inspeção.
COBERTURA_MINIMA_PCT = 80.0
# Fração de leituras com SEN-11 acima da qual o sensor é "sinal fraco" (ia/contexto.py).
FRACAO_SINAL_FRACO_MAX = 0.05
# Faixa de "programar irrigação": entre o limiar e limiar + 0,15 o copiloto recomenda
# preparar a lâmina (ia/regras.py) e o guardrail G2 bloqueia irrigar acima dela.
MARGEM_PROGRAMAR = 0.15
# Lâmina sugerida — idêntica à do Context Pack (ia/contexto.py ``lamina_sugerida_mm``).
FRACAO_LAMINA = 0.85
LAMINA_MAX_MM = 30.0
# Taxa de aplicação por sistema (mm/h): parâmetro do atuador, valores típicos de projeto.
# Não é limiar agronômico — é o que converte lâmina em minutos de válvula aberta e deve
# ser calibrado por equipamento. Sistema desconhecido usa o padrão.
TAXA_APLICACAO_MM_H: dict[str, float] = {
    "pivo_central": 8.0,
    "aspersao": 10.0,
    "gotejamento": 4.0,
    "sulco": 15.0,
}
TAXA_APLICACAO_PADRAO_MM_H = 8.0
PASSO_MINUTOS = 5
# Janela de execução da irrigação: madrugada, menor evapotranspiração (ia/regras.py).
JANELA_IRRIGACAO = "04:00-07:00"
TOPICO_IRRIGACAO = "agrosmart/atuadores/{talhao_id}/irrigacao"

TipoAcao = Literal["irrigar", "alerta_fitossanitario", "trocar_bateria", "inspecionar_sensor"]
Prioridade = Literal["alta", "media", "baixa"]
Risco = Literal["critico", "atencao", "normal"]
Status = Literal["simulado", "publicado"]
_ORDEM_PRIORIDADE = {"alta": 0, "media": 1, "baixa": 2}
_ORDEM_TIPO = {
    "irrigar": 0,
    "alerta_fitossanitario": 1,
    "trocar_bateria": 2,
    "inspecionar_sensor": 3,
}


# ---------------------------------------------------------------------------
# Esquema do plano — o contrato com o dashboard e com o emulador do atuador
# ---------------------------------------------------------------------------


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ComandoMQTT(_Modelo):
    topico: str
    payload: dict[str, Any]


class Acao(_Modelo):
    id: str
    talhao_id: str
    sensor_id: str | None
    tipo: TipoAcao
    prioridade: Prioridade
    parametros: dict[str, Any]
    motivo: str
    evidencias: list[str]
    comando_mqtt: ComandoMQTT | None
    status: Status = "simulado"


class TalhaoPlano(_Modelo):
    talhao_id: str
    risco: Risco
    indicadores: dict[str, Any]
    # Opcional: justificativa do copiloto (ia/agente.py). Ausente do JSON quando nula.
    copiloto: dict[str, Any] | None = None


class ResumoPlano(_Modelo):
    talhoes: int
    acoes: int
    por_tipo: dict[str, int]
    por_prioridade: dict[str, int]


class Plano(_Modelo):
    gerado_em: str
    data_ref: str
    fonte: str = FONTE
    resumo: ResumoPlano
    talhoes: list[TalhaoPlano]
    acoes: list[Acao]

    def para_dict(self) -> dict[str, Any]:
        """O JSON do plano, exatamente no esquema combinado com o dashboard."""
        dados = self.model_dump(mode="json")
        for talhao in dados["talhoes"]:
            if talhao.get("copiloto") is None:
                talhao.pop("copiloto", None)
        return dados

    def para_json(self) -> str:
        return json.dumps(self.para_dict(), ensure_ascii=False, indent=2)

    def acoes_de(self, talhao_id: str) -> list[Acao]:
        return [a for a in self.acoes if a.talhao_id == talhao_id]

    def comandos_pendentes(self) -> list[Acao]:
        return [a for a in self.acoes if a.comando_mqtt is not None and a.status == "simulado"]


# ---------------------------------------------------------------------------
# Entrada: as tabelas do gold, já materializadas
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TabelasGold:
    """As tabelas que o motor lê. ``talhoes`` (dim_talhao) é opcional — só enriquece."""

    balanco: pl.DataFrame
    leituras: pl.DataFrame
    sensores: pl.DataFrame
    talhoes: pl.DataFrame = field(default_factory=pl.DataFrame)


def _sem_decimais(df: pl.DataFrame) -> pl.DataFrame:
    """DuckDB grava contagens como Decimal; Polars não faz aritmética com elas."""
    conversoes = [
        pl.col(nome).cast(pl.Int64 if dtype.scale == 0 else pl.Float64)
        for nome, dtype in df.schema.items()
        if isinstance(dtype, pl.Decimal)
    ]
    return df.with_columns(conversoes) if conversoes else df


def carregar_tabelas(armazenamento: Armazenamento) -> TabelasGold:
    """Lê as tabelas do gold pelo ``Armazenamento`` — MinIO ou filesystem, mesmo código."""
    ler = lambda nome: _sem_decimais(ler_tabela(armazenamento, Camada.GOLD, nome))  # noqa: E731
    tabelas = TabelasGold(
        balanco=ler("fato_balanco_hidrico"),
        leituras=ler("fato_leitura_diaria_talhao"),
        sensores=ler("dim_sensor"),
        talhoes=ler("dim_talhao"),
    )
    if tabelas.balanco.is_empty():
        raise ValueError(
            "gold sem fato_balanco_hidrico — rode `agrosmart processar` (ou `run --all`) antes"
        )
    return tabelas


def data_de_referencia(tabelas: TabelasGold, data: date | None = None) -> date:
    """``data`` quando informada; senão o último dia do balanço com umidade medida."""
    if data is not None:
        return data
    com_umidade = tabelas.balanco.filter(pl.col("umidade_media_pct").is_not_null())
    base = com_umidade if not com_umidade.is_empty() else tabelas.balanco
    ultimo = base.get_column("dia").max()
    if ultimo is None:
        raise ValueError("fato_balanco_hidrico vazio: não há data de referência")
    return ultimo  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Decisões
# ---------------------------------------------------------------------------


def _fmt(valor: float | None, casas: int = 1) -> str:
    if valor is None:
        return "n/d"
    return f"{valor:.{casas}f}".replace(".", ",")


def _lamina_mm(deficit_mm: float) -> float:
    """85 % do déficit até a capacidade de campo, máx. 30 mm, múltiplos de 0,5 (Context Pack)."""
    return max(round(min(deficit_mm * FRACAO_LAMINA, LAMINA_MAX_MM) * 2) / 2, 0.0)


def _minutos(lamina_mm: float, sistema_irrigacao: str | None) -> int:
    taxa = TAXA_APLICACAO_MM_H.get(sistema_irrigacao or "", TAXA_APLICACAO_PADRAO_MM_H)
    minutos = lamina_mm / taxa * 60.0
    return int(math.ceil(minutos / PASSO_MINUTOS) * PASSO_MINUTOS)


def _linhas(df: pl.DataFrame) -> list[dict[str, Any]]:
    return df.to_dicts()


def _cadastro(tabelas: TabelasGold, talhao_id: str) -> dict[str, Any]:
    if tabelas.talhoes.is_empty() or "talhao_id" not in tabelas.talhoes.columns:
        return {}
    linhas = _linhas(tabelas.talhoes.filter(pl.col("talhao_id") == talhao_id))
    return linhas[0] if linhas else {}


@dataclass(slots=True)
class _Hidrico:
    """A linha do balanço que vale para hoje: a última com umidade medida na janela."""

    dia: date | None
    umidade_pct: float | None
    fad: float | None
    deficit_mm: float | None
    estadio: str | None
    estresse: bool
    irrigado: bool
    dias_na_janela: int

    @property
    def estadio_critico(self) -> bool:
        return self.estadio in agronomia.ESTADIOS_CRITICOS_AGUA


def _hidrico(balanco: pl.DataFrame, talhao_id: str, data_ref: date) -> _Hidrico:
    inicio = data_ref - timedelta(days=DIAS_JANELA_HIDRICA - 1)
    janela = balanco.filter(
        (pl.col("talhao_id") == talhao_id) & (pl.col("dia") >= inicio) & (pl.col("dia") <= data_ref)
    ).sort("dia")
    linhas = _linhas(janela)
    irrigado = bool(linhas[-1]["irrigado"]) if linhas else False
    com_umidade = [d for d in linhas if d.get("umidade_media_pct") is not None]
    if not com_umidade:
        return _Hidrico(None, None, None, None, None, False, irrigado, len(linhas))
    atual = com_umidade[-1]
    return _Hidrico(
        dia=atual["dia"],
        umidade_pct=atual["umidade_media_pct"],
        fad=atual["fracao_agua_disponivel"],
        deficit_mm=atual["deficit_mm"],
        estadio=atual.get("estadio"),
        estresse=bool(atual.get("estresse_hidrico")),
        irrigado=bool(atual["irrigado"]),
        dias_na_janela=len(linhas),
    )


def _decidir_irrigacao(
    h: _Hidrico, talhao_id: str, cadastro: dict[str, Any]
) -> tuple[str, Acao | None]:
    """Devolve (situação hídrica, ação). A trava do sequeiro é a mesma do gold e do G2."""
    if h.fad is None or h.dia is None:
        return "sem_leitura", None
    limiar = agronomia.FRACAO_IRRIGACAO
    if not h.irrigado:
        return ("sequeiro_estresse" if h.estresse else "sequeiro"), None
    if h.fad >= limiar + MARGEM_PROGRAMAR:
        return "ok", None
    if h.fad >= limiar:
        return "programar", None

    lamina = _lamina_mm(float(h.deficit_mm or 0.0))
    if lamina <= 0:
        return "ok", None
    sistema = cadastro.get("sistema_irrigacao")
    minutos = _minutos(lamina, sistema)
    prioridade: Prioridade = "alta" if (h.estresse or h.estadio_critico) else "media"
    motivo = (
        f"Fração de água disponível em {_fmt(h.fad, 3)}, abaixo do limiar de {_fmt(limiar, 2)} "
        f"para irrigar; déficit de {_fmt(h.deficit_mm)} mm até a capacidade de campo. "
        f"Lâmina de {_fmt(lamina)} mm ({_fmt(FRACAO_LAMINA * 100, 0)} % do déficit, máx. "
        f"{_fmt(LAMINA_MAX_MM, 0)} mm) = {minutos} min de {sistema or 'irrigação'} a "
        f"{_fmt(TAXA_APLICACAO_MM_H.get(sistema or '', TAXA_APLICACAO_PADRAO_MM_H))} mm/h."
    )
    if h.estresse:
        motivo += (
            f" Talhão em estresse hídrico (fração abaixo de {_fmt(agronomia.FRACAO_ESTRESSE, 2)})."
        )
    elif h.estadio_critico:
        motivo += f" Estádio {h.estadio} é crítico para água."
    acao = Acao(
        id="",
        talhao_id=talhao_id,
        sensor_id=None,
        tipo="irrigar",
        prioridade=prioridade,
        parametros={
            "lamina_mm": lamina,
            "minutos": minutos,
            "janela": JANELA_IRRIGACAO,
            "fracao_agua_disponivel": h.fad,
            "limiar_fracao_irrigacao": limiar,
            "deficit_mm": h.deficit_mm,
            "sistema_irrigacao": sistema,
            "taxa_aplicacao_mm_h": TAXA_APLICACAO_MM_H.get(
                sistema or "", TAXA_APLICACAO_PADRAO_MM_H
            ),
        },
        motivo=motivo,
        evidencias=[f"fato_balanco_hidrico:{talhao_id}:{h.dia.isoformat()}"],
        comando_mqtt=ComandoMQTT(
            topico=TOPICO_IRRIGACAO.format(talhao_id=talhao_id),
            payload={"acao": "ligar", "minutos": minutos},
        ),
    )
    return "irrigar", acao


def _janela_sensores(leituras: pl.DataFrame, talhao_id: str, data_ref: date) -> pl.DataFrame:
    """Os 7 dias fechados antes da referência: o dia corrente só tem a madrugada."""
    if leituras.is_empty():
        return leituras
    inicio = data_ref - timedelta(days=DIAS_JANELA_SENSORES)
    return leituras.filter(
        (pl.col("talhao_id") == talhao_id) & (pl.col("dia") >= inicio) & (pl.col("dia") < data_ref)
    ).sort("dia")


def _decidir_fitossanitario(
    janela: pl.DataFrame, talhao_id: str
) -> tuple[dict[str, Any], Acao | None]:
    linhas = [
        d
        for d in _linhas(janela)
        if d.get("umidade_ar_media_pct") is not None
        and d.get("temp_ar_min_c") is not None
        and d.get("temp_ar_max_c") is not None
    ]
    if not linhas:
        return {"dias_ur_alta_7d": 0, "ur_media_7d_pct": None, "temp_media_7d_c": None}, None
    ur_alta = [d for d in linhas if d["umidade_ar_media_pct"] > agronomia.UR_ALTA_PCT]
    ur_media = round(sum(d["umidade_ar_media_pct"] for d in linhas) / len(linhas), 1)
    temp_media = round(
        sum((d["temp_ar_min_c"] + d["temp_ar_max_c"]) / 2 for d in linhas) / len(linhas), 1
    )
    faixa = agronomia.TEMP_RISCO_FUNGICO_C
    indicadores = {
        "dias_ur_alta_7d": len(ur_alta),
        "ur_media_7d_pct": ur_media,
        "temp_media_7d_c": temp_media,
    }
    if len(ur_alta) < agronomia.DIAS_UR_ALTA_RISCO or not (faixa[0] <= temp_media <= faixa[1]):
        return indicadores, None
    motivo = (
        f"{len(ur_alta)} dia(s) com umidade do ar acima de {_fmt(agronomia.UR_ALTA_PCT, 0)} % "
        f"nos últimos {DIAS_JANELA_SENSORES} dias (média de {_fmt(ur_media)} %) e temperatura "
        f"média de {_fmt(temp_media)} °C, dentro da faixa {_fmt(faixa[0], 0)}–{_fmt(faixa[1], 0)} "
        f"°C favorável a fungos foliares: inspecionar as folhas e avaliar pulverização "
        f"preventiva respeitando o intervalo mínimo de "
        f"{agronomia.INTERVALO_MIN_PULVERIZACAO_DIAS} dias."
    )
    acao = Acao(
        id="",
        talhao_id=talhao_id,
        sensor_id=None,
        tipo="alerta_fitossanitario",
        prioridade="media",
        parametros={
            **indicadores,
            "limiar_ur_pct": agronomia.UR_ALTA_PCT,
            "dias_ur_alta_risco": agronomia.DIAS_UR_ALTA_RISCO,
            "faixa_temp_c": list(faixa),
            "janela": "próximos 2 dias, primeira janela sem chuva",
        },
        motivo=motivo,
        evidencias=[
            f"fato_leitura_diaria_talhao:{talhao_id}:{d['dia'].isoformat()}" for d in ur_alta
        ],
        comando_mqtt=None,
    )
    return indicadores, acao


def _dia(valor: Any) -> str:
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    return str(valor)[:10]


def _decidir_sensores(
    sensores: pl.DataFrame, janela: pl.DataFrame, talhao_id: str, situacao_hidrica: str
) -> tuple[dict[str, Any], list[Acao]]:
    acoes: list[Acao] = []
    meus = (
        _linhas(sensores.filter(pl.col("talhao_id") == talhao_id).sort("sensor_id"))
        if not sensores.is_empty()
        else []
    )
    indicadores: dict[str, Any] = {
        "sensores": len(meus),
        "bateria_min_pct": (
            round(min(float(s["bateria_ultima_pct"]) for s in meus), 1) if meus else None
        ),
        "cobertura_min_7d_pct": None,
    }

    # Bateria: o dispositivo ainda mede, mas vai calar — troca programada (SEN-10).
    for s in meus:
        bateria = float(s["bateria_ultima_pct"])
        if bateria < agronomia.BATERIA_MANUTENCAO_PCT:
            acoes.append(
                Acao(
                    id="",
                    talhao_id=talhao_id,
                    sensor_id=s["sensor_id"],
                    tipo="trocar_bateria",
                    prioridade="media",
                    parametros={
                        "bateria_pct": bateria,
                        "limiar_pct": agronomia.BATERIA_MANUTENCAO_PCT,
                        "janela": "próxima entrada no talhão",
                    },
                    motivo=(
                        f"Bateria do sensor {s['sensor_id']} em {_fmt(bateria)} %, abaixo do "
                        f"limiar de manutenção de {_fmt(agronomia.BATERIA_MANUTENCAO_PCT, 0)} %: "
                        "programar a troca antes que o sensor cale."
                    ),
                    evidencias=[f"dim_sensor:{s['sensor_id']}:{_dia(s['ultima_leitura'])}"],
                    comando_mqtt=None,
                )
            )

    # Sinal: RSSI médio abaixo do limiar ou mais de 5 % das leituras com SEN-11.
    for s in meus:
        rssi = float(s["rssi_medio_dbm"])
        leituras = max(int(s.get("leituras") or 0), 1)
        fracas = int(s.get("leituras_sinal_fraco") or 0)
        fracao = round(fracas / leituras, 3)
        if rssi < agronomia.RSSI_FRACO_DBM or fracao > FRACAO_SINAL_FRACO_MAX:
            acoes.append(
                Acao(
                    id="",
                    talhao_id=talhao_id,
                    sensor_id=s["sensor_id"],
                    tipo="inspecionar_sensor",
                    prioridade="baixa",
                    parametros={
                        "motivo_tecnico": "sinal_fraco",
                        "rssi_medio_dbm": rssi,
                        "leituras_sinal_fraco": fracas,
                        "fracao_sinal_fraco": fracao,
                        "limiar_rssi_dbm": agronomia.RSSI_FRACO_DBM,
                    },
                    motivo=(
                        f"Sensor {s['sensor_id']} com sinal médio de {_fmt(rssi)} dBm e "
                        f"{fracas} leitura(s) abaixo de {agronomia.RSSI_FRACO_DBM} dBm "
                        f"(fração {_fmt(fracao, 3)}): reposicionar antena ou gateway para "
                        "evitar perda de leituras."
                    ),
                    evidencias=[f"dim_sensor:{s['sensor_id']}:{_dia(s['ultima_leitura'])}"],
                    comando_mqtt=None,
                )
            )

    # Cobertura: dias da semana com menos leituras que o esperado — um sensor calou.
    dias = [d for d in _linhas(janela) if d.get("cobertura_pct") is not None]
    if dias:
        minimo = min(float(d["cobertura_pct"]) for d in dias)
        indicadores["cobertura_min_7d_pct"] = round(minimo, 1)
        abaixo = [d for d in dias if float(d["cobertura_pct"]) < COBERTURA_MINIMA_PCT]
        if abaixo:
            suspeito = _sensor_suspeito(meus)
            acoes.append(
                Acao(
                    id="",
                    talhao_id=talhao_id,
                    sensor_id=suspeito,
                    tipo="inspecionar_sensor",
                    prioridade="media",
                    parametros={
                        "motivo_tecnico": "cobertura_baixa",
                        "cobertura_min_7d_pct": round(minimo, 1),
                        "dias_abaixo": len(abaixo),
                        "limiar_cobertura_pct": COBERTURA_MINIMA_PCT,
                    },
                    motivo=(
                        f"Cobertura de leituras de {_fmt(minimo)} % em {len(abaixo)} dia(s) da "
                        f"última semana, abaixo de {_fmt(COBERTURA_MINIMA_PCT, 0)} %: lacuna de "
                        "sensor que rebaixa a confiança das decisões hídricas."
                        + (f" Sensor com menos dias de leitura: {suspeito}." if suspeito else "")
                    ),
                    evidencias=[
                        f"fato_leitura_diaria_talhao:{talhao_id}:{d['dia'].isoformat()}"
                        for d in abaixo
                    ],
                    comando_mqtt=None,
                )
            )

    if situacao_hidrica == "sem_leitura":
        acoes.append(
            Acao(
                id="",
                talhao_id=talhao_id,
                sensor_id=None,
                tipo="inspecionar_sensor",
                prioridade="alta",
                parametros={"motivo_tecnico": "sem_umidade", "dias": DIAS_JANELA_HIDRICA},
                motivo=(
                    f"Nenhuma leitura de umidade do solo nos últimos {DIAS_JANELA_HIDRICA} dias: "
                    "sem dado não há decisão hídrica — verificar sensores e gateway."
                ),
                evidencias=[f"fato_balanco_hidrico:{talhao_id}:{DIAS_JANELA_HIDRICA}d"],
                comando_mqtt=None,
            )
        )
    return indicadores, acoes


def _sensor_suspeito(sensores: list[dict[str, Any]]) -> str | None:
    """Entre os sensores do talhão, o que publicou em menos dias — se houver diferença."""
    if len(sensores) < 2:
        return None
    dias = [int(s.get("dias_com_leitura") or 0) for s in sensores]
    if min(dias) == max(dias):
        return None
    return min(sensores, key=lambda s: int(s.get("dias_com_leitura") or 0))["sensor_id"]


def _classificar_risco(h: _Hidrico, situacao: str, acoes: list[Acao]) -> Risco:
    irrigar_alta = any(a.tipo == "irrigar" and a.prioridade == "alta" for a in acoes)
    if h.estresse or irrigar_alta:
        return "critico"
    if acoes or situacao in {"programar", "sem_leitura"}:
        return "atencao"
    return "normal"


# ---------------------------------------------------------------------------
# O plano
# ---------------------------------------------------------------------------


def gerar_plano(
    tabelas: TabelasGold, *, data: date | None = None, gerado_em: datetime | None = None
) -> Plano:
    """Decide por talhão e monta o plano. Determinístico para o mesmo gold e a mesma data."""
    data_ref = data_de_referencia(tabelas, data)
    ids = set(tabelas.balanco.get_column("talhao_id").unique().to_list())
    if not tabelas.sensores.is_empty():
        ids |= set(tabelas.sensores.get_column("talhao_id").unique().to_list())
    talhoes: list[TalhaoPlano] = []
    acoes: list[Acao] = []

    for talhao_id in sorted(ids):
        cadastro = _cadastro(tabelas, talhao_id)
        h = _hidrico(tabelas.balanco, talhao_id, data_ref)
        situacao, acao_irrigar = _decidir_irrigacao(h, talhao_id, cadastro)
        janela = _janela_sensores(tabelas.leituras, talhao_id, data_ref)
        ind_fito, acao_fito = _decidir_fitossanitario(janela, talhao_id)
        ind_sens, acoes_sens = _decidir_sensores(tabelas.sensores, janela, talhao_id, situacao)

        do_talhao = [a for a in (acao_irrigar, acao_fito) if a is not None] + acoes_sens
        do_talhao.sort(key=lambda a: (_ORDEM_TIPO[a.tipo], a.sensor_id or ""))
        risco = _classificar_risco(h, situacao, do_talhao)
        indicadores: dict[str, Any] = {
            "cultura": cadastro.get("cultura"),
            "sistema_irrigacao": cadastro.get("sistema_irrigacao"),
            "area_ha": cadastro.get("area_ha"),
            "irrigado": h.irrigado,
            "dia_leitura": h.dia.isoformat() if h.dia else None,
            "umidade_media_pct": h.umidade_pct,
            "fracao_agua_disponivel": h.fad,
            "deficit_mm": h.deficit_mm,
            "estadio": h.estadio,
            "estadio_critico_para_agua": h.estadio_critico,
            "estresse_hidrico": h.estresse,
            "situacao_hidrica": situacao,
            "dias_balanco_14d": h.dias_na_janela,
            **ind_fito,
            **ind_sens,
            "acoes": len(do_talhao),
        }
        talhoes.append(TalhaoPlano(talhao_id=talhao_id, risco=risco, indicadores=indicadores))
        acoes.extend(do_talhao)

    for indice, acao in enumerate(acoes, start=1):
        acao.id = f"ACT-{indice:04d}"

    por_tipo: dict[str, int] = {}
    for acao in acoes:
        por_tipo[acao.tipo] = por_tipo.get(acao.tipo, 0) + 1
    sem_acao = sum(1 for t in talhoes if t.indicadores["acoes"] == 0)
    if sem_acao:
        por_tipo["nenhuma"] = sem_acao  # talhões sem ação: a decisão "nenhuma" também conta
    por_prioridade = {p: sum(1 for a in acoes if a.prioridade == p) for p in _ORDEM_PRIORIDADE}

    instante = (gerado_em or datetime.now(UTC)).replace(microsecond=0)
    return Plano(
        gerado_em=instante.isoformat(),
        data_ref=data_ref.isoformat(),
        fonte=FONTE,
        resumo=ResumoPlano(
            talhoes=len(talhoes),
            acoes=len(acoes),
            por_tipo=por_tipo,
            por_prioridade=por_prioridade,
        ),
        talhoes=talhoes,
        acoes=acoes,
    )


# ---------------------------------------------------------------------------
# Copiloto: a explicação em linguagem natural, com os guardrails de sempre
# ---------------------------------------------------------------------------


def anexar_copiloto(
    plano: Plano,
    config: Config,
    armazenamento: Armazenamento | None = None,
    *,
    motor: str = "auto",
) -> int:
    """Pede ao copiloto (ia/agente.py) a justificativa de cada talhão e anexa ao plano.

    O plano já está decidido quando o copiloto é chamado: ele explica, não decide.
    Sem chave de API o motor de regras responde; qualquer falha é registrada e o
    plano segue sem a justificativa daquele talhão — nunca sem o plano.
    """
    from agrosmart.ia.agente import Copiloto
    from agrosmart.ia.contexto import abrir_gold, construir_context_pack
    from agrosmart.ia.esquema import Caso

    logger = obter_logger("automacao.copiloto")
    cfg = replace(config, data_referencia=date.fromisoformat(plano.data_ref))
    copiloto = Copiloto(cfg, motor_preferido=motor)  # type: ignore[arg-type]
    try:
        con = abrir_gold(cfg, armazenamento)
    except Exception as erro:  # noqa: BLE001 - sem gold completo não há Context Pack
        logger.warning("copiloto indisponível (%s): plano segue sem justificativas", erro)
        return 0
    anexadas = 0
    try:
        for talhao in plano.talhoes:
            tipos = {a.tipo for a in plano.acoes_de(talhao.talhao_id)}
            casos = [Caso.IRRIGACAO] + (
                [Caso.FITOSSANITARIO] if "alerta_fitossanitario" in tipos else []
            )
            try:
                pack = construir_context_pack(talhao.talhao_id, con=con, config=cfg)
            except Exception as erro:  # noqa: BLE001 - talhão sem contexto não derruba o plano
                logger.warning("%s: sem Context Pack (%s)", talhao.talhao_id, erro)
                continue
            respostas = [copiloto.responder(pack, caso) for caso in casos]
            talhao.copiloto = {
                "motor": respostas[0].motor,
                "fallback": any(r.fallback for r in respostas),
                "confianca": respostas[0].saida.confianca,
                "justificativa_confianca": respostas[0].saida.justificativa_confianca,
                "guardrails_aprovados": all(r.guardrails.aprovado for r in respostas),
                "diagnostico": respostas[0].saida.diagnostico,
                "recomendacoes": [
                    f"[{rec.prioridade.upper()}] {rec.acao} ({rec.tipo})"
                    for r in respostas
                    for rec in r.saida.recomendacoes
                ][:6],
            }
            anexadas += 1
    finally:
        con.close()
    return anexadas


# ---------------------------------------------------------------------------
# Saídas: JSON para o dashboard, Markdown para gente
# ---------------------------------------------------------------------------

_ROTULO_RISCO = {"critico": "CRÍTICO", "atencao": "atenção", "normal": "normal"}
_ROTULO_SITUACAO = {
    "irrigar": "irrigar",
    "programar": "programar irrigação",
    "ok": "manejo atual",
    "sequeiro": "sequeiro — acompanhar",
    "sequeiro_estresse": "sequeiro em estresse — sem atuador",
    "sem_leitura": "sem leitura de umidade",
}


def _parametros_md(acao: Acao) -> str:
    p = acao.parametros
    if acao.tipo == "irrigar":
        return f"{_fmt(p['lamina_mm'])} mm · {p['minutos']} min · {p['janela']}"
    if acao.tipo == "alerta_fitossanitario":
        return (
            f"{p['dias_ur_alta_7d']} dia(s) UR > {_fmt(p['limiar_ur_pct'], 0)} % · "
            f"T média {_fmt(p['temp_media_7d_c'])} °C"
        )
    if acao.tipo == "trocar_bateria":
        return f"bateria {_fmt(p['bateria_pct'])} % (limiar {_fmt(p['limiar_pct'], 0)} %)"
    if p.get("motivo_tecnico") == "sinal_fraco":
        return (
            f"RSSI {_fmt(p['rssi_medio_dbm'])} dBm · "
            f"fração fraca {_fmt(p['fracao_sinal_fraco'], 3)}"
        )
    if p.get("motivo_tecnico") == "cobertura_baixa":
        return f"cobertura mín. {_fmt(p['cobertura_min_7d_pct'])} % em {p['dias_abaixo']} dia(s)"
    return f"sem umidade há {p.get('dias')} dias"


def para_markdown(plano: Plano) -> str:
    r = plano.resumo
    tipos = ", ".join(f"{k} {v}" for k, v in r.por_tipo.items()) or "—"
    prio = " / ".join(f"{k} {v}" for k, v in r.por_prioridade.items())
    linhas = [
        f"# Plano de ações · {plano.data_ref}",
        "",
        f"Gerado em {plano.gerado_em} · fonte: `{plano.fonte}` · {r.talhoes} talhões · "
        f"**{r.acoes} ação(ões)** ({tipos}) · prioridade {prio}",
        "",
        "## Talhões",
        "",
        "| Talhão | Cultura · irrigação | Risco | Umidade (%) | FAD | Déficit (mm) | Estádio | "
        "Cobertura mín. 7d (%) | Decisão hídrica | Ações |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t in plano.talhoes:
        i = t.indicadores
        ids = ", ".join(a.id for a in plano.acoes_de(t.talhao_id)) or "nenhuma"
        cultura = f"{i.get('cultura') or 'n/d'} · {i.get('sistema_irrigacao') or 'n/d'}"
        situacao = _ROTULO_SITUACAO.get(i.get("situacao_hidrica"), i.get("situacao_hidrica"))
        linhas.append(
            f"| {t.talhao_id} | {cultura} | {_ROTULO_RISCO[t.risco]} | "
            f"{_fmt(i.get('umidade_media_pct'))} | {_fmt(i.get('fracao_agua_disponivel'), 3)} | "
            f"{_fmt(i.get('deficit_mm'))} | {i.get('estadio') or 'n/d'} | "
            f"{_fmt(i.get('cobertura_min_7d_pct'))} | {situacao} | {ids} |"
        )
    linhas += [
        "",
        "## Ações",
        "",
    ]
    if not plano.acoes:
        linhas.append("Nenhuma ação: todos os talhões dentro dos limiares.")
    else:
        linhas += [
            "| ID | Talhão | Sensor | Tipo | Prioridade | Parâmetros | Motivo | Evidências | "
            "Comando MQTT | Status |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for a in plano.acoes:
            comando = (
                f"`{a.comando_mqtt.topico}` → `{json.dumps(a.comando_mqtt.payload)}`"
                if a.comando_mqtt
                else "—"
            )
            evid = ", ".join(f"`{e}`" for e in a.evidencias[:3])
            if len(a.evidencias) > 3:
                evid += f" (+{len(a.evidencias) - 3})"
            linhas.append(
                f"| {a.id} | {a.talhao_id} | {a.sensor_id or '—'} | {a.tipo} | {a.prioridade} | "
                f"{_parametros_md(a)} | {a.motivo} | {evid} | {comando} | {a.status} |"
            )
    com_copiloto = [t for t in plano.talhoes if t.copiloto]
    if com_copiloto:
        linhas += ["", "## Justificativa do copiloto", ""]
        for t in com_copiloto:
            c = t.copiloto or {}
            linhas += [
                f"### {t.talhao_id} — motor {c.get('motor')}"
                + (" (fallback)" if c.get("fallback") else "")
                + f" · confiança {c.get('confianca')} — {c.get('justificativa_confianca')}",
                "",
                f"> {c.get('diagnostico')}",
                "",
            ]
            recomendacoes = c.get("recomendacoes", [])
            linhas += [f"> {indice}. {rec}" for indice, rec in enumerate(recomendacoes, 1)]
            linhas.append("")
    linhas += [
        "",
        "## Como ler",
        "",
        "- **Risco**: `crítico` = estresse hídrico ou irrigação de prioridade alta; "
        "`atenção` = há ação pendente ou a fração de água se aproxima do limiar; "
        "`normal` = dentro dos limiares.",
        "- **Limiares** de `agronomia.py`: irrigar abaixo de "
        f"{_fmt(agronomia.FRACAO_IRRIGACAO, 2)} de água disponível, estresse abaixo de "
        f"{_fmt(agronomia.FRACAO_ESTRESSE, 2)}; bateria abaixo de "
        f"{_fmt(agronomia.BATERIA_MANUTENCAO_PCT, 0)} %; sinal abaixo de "
        f"{agronomia.RSSI_FRACO_DBM} dBm; risco fúngico com {agronomia.DIAS_UR_ALTA_RISCO}+ dias "
        f"de UR > {_fmt(agronomia.UR_ALTA_PCT, 0)} % e temperatura entre "
        f"{_fmt(agronomia.TEMP_RISCO_FUNGICO_C[0], 0)} e "
        f"{_fmt(agronomia.TEMP_RISCO_FUNGICO_C[1], 0)} °C.",
        "- **Status** `simulado`: o comando foi gerado e não publicado; `publicado`: entregue ao "
        "broker MQTT com QoS 1 e assinado pelo controlador de irrigação do talhão.",
        "",
        "_As regras decidem e executam o que tem atuador; o copiloto explica; o produtor pode "
        "vetar. Toda cifra vem do gold do Data Lake — nada foi recalculado aqui._",
        "",
    ]
    return "\n".join(linhas)


def salvar_plano(plano: Plano, config: Config) -> list[Path]:
    """Grava ``outputs/acoes/acoes_<data>.json`` e ``.md``. Reexecutar sobrescreve: idempotente."""
    pasta = config.dir_outputs / "acoes"
    pasta.mkdir(parents=True, exist_ok=True)
    destino_json = pasta / f"acoes_{plano.data_ref}.json"
    destino_md = pasta / f"acoes_{plano.data_ref}.md"
    destino_json.write_text(plano.para_json() + "\n", encoding="utf-8")
    destino_md.write_text(para_markdown(plano), encoding="utf-8")
    return [destino_json, destino_md]


# ---------------------------------------------------------------------------
# Ponto de entrada da CLI
# ---------------------------------------------------------------------------


def executar(
    config: Config | None = None,
    *,
    data: date | None = None,
    publicar: bool = False,
    broker: str = "localhost",
    porta: int = 1883,
    dry_run: bool = False,
    copiloto: str | None = "auto",
    armazenamento: Armazenamento | None = None,
    publicador: Any = None,
) -> dict[str, Any]:
    """Gold → plano → (MQTT) → ``outputs/acoes/``. Devolve o plano, os caminhos e a publicação.

    ``copiloto``: ``auto`` | ``claude`` | ``regras`` | ``None`` (sem justificativa).
    ``publicador``: injeção para testes; sem ele, com ``publicar=True``, usa paho-mqtt.
    """
    from agrosmart.automacao.atuadores import ErroPublicacao, PublicadorPaho, publicar_plano

    cfg = config or carregar_config()
    logger = obter_logger("automacao")
    lake = armazenamento or criar_armazenamento(cfg)
    tabelas = carregar_tabelas(lake)
    plano = gerar_plano(tabelas, data=data)
    logger.info(
        "plano %s: %d talhões, %d ação(ões) %s",
        plano.data_ref,
        plano.resumo.talhoes,
        plano.resumo.acoes,
        plano.resumo.por_tipo,
    )
    if copiloto:
        anexadas = anexar_copiloto(plano, cfg, lake, motor=copiloto)
        logger.info("copiloto: %d justificativa(s) anexada(s)", anexadas)

    publicacao: dict[str, Any] = {"solicitada": publicar, "publicadas": 0, "falhas": []}
    if publicar and not dry_run:
        try:
            if publicador is None:
                with PublicadorPaho(broker, porta) as paho:
                    publicacao = publicar_plano(plano, paho, config=cfg)
            else:
                publicacao = publicar_plano(plano, publicador, config=cfg)
        except ErroPublicacao as erro:
            # Broker fora do ar não pode custar o plano: tudo fica `simulado`, a falha
            # é registrada por comando e o chamador decide (a CLI sai com código 1).
            logger.error("publicação abortada: %s", erro)
            publicacao = {
                "solicitada": True,
                "publicadas": 0,
                "falhas": [
                    {"id": a.id, "topico": a.comando_mqtt.topico, "erro": str(erro)}
                    for a in plano.comandos_pendentes()
                    if a.comando_mqtt is not None
                ],
            }
        publicacao["solicitada"] = True

    caminhos: list[Path] = [] if dry_run else salvar_plano(plano, cfg)
    registrar_evento(
        Evento(
            etapa="automacao",
            acao="plano_gerado" if not dry_run else "plano_simulado",
            status="alerta" if publicacao["falhas"] else "ok",
            mensagem=f"{plano.data_ref} · {plano.resumo.acoes} ação(ões) · {plano.resumo.por_tipo}",
            metricas={
                "talhoes": plano.resumo.talhoes,
                "acoes": plano.resumo.acoes,
                "publicadas": publicacao["publicadas"],
                "falhas": len(publicacao["falhas"]),
                "dry_run": dry_run,
            },
        ),
        cfg,
        eco_console=False,
    )
    return {"plano": plano, "caminhos": caminhos, "publicacao": publicacao}
