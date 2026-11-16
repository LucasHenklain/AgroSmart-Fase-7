"""Conhecimento agronômico compartilhado: culturas, estádios, Kc e limiares.

É a única fonte desses parâmetros para todo o projeto — os simuladores usam
para gerar dados coerentes, o gold usa para calcular balanço hídrico e
estádio, e o copiloto de IA usa como limiares de recomendação. Se um valor
mudar (um Kc revisado pela equipe agronômica), muda em um lugar e todas as
camadas passam a concordar.

Referências: FAO-56 (Allen et al., 1998) para Kc por estádio; escalas
fenológicas usuais de milho (Ritchie), feijão e trigo (Zadoks simplificada).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

# Profundidade da camada de solo representada pela umidade do sensor (0–30 cm).
PROFUNDIDADE_RAIZ_MM = 300.0
# Parte da chuva que efetivamente infiltra na zona radicular.
EFICIENCIA_CHUVA = 0.80
# Fração da água disponível abaixo da qual a planta entra em estresse.
FRACAO_ESTRESSE = 0.35
# Fração da água disponível abaixo da qual o produtor irriga (50 % de depleção).
FRACAO_IRRIGACAO = 0.50
# Cobertura de nuvem acima da qual um NDVI de satélite perde confiança.
NUVEM_CONFIANCA_MEDIA_PCT = 10.0
NUVEM_CONFIANCA_BAIXA_PCT = 30.0
# Mínimo de talhões por grupo para um agregado ser publicável (k-anonimato).
K_ANONIMATO = 3

# --- Parâmetros econômicos de referência (safra 2026, valores redondos) ------------
# Usados pelo simulador para lançar custos, pelo gold para custo/ha e pelo copiloto
# para estimar economia. Um só lugar: a IA nunca inventa preço.
CUSTO_IRRIGACAO_BRL_MM_HA: dict[str, float] = {
    "pivo_central": 1.45,
    "aspersao": 1.70,
    "gotejamento": 1.10,
    "sulco": 0.90,
}
CUSTO_FUNGICIDA_BRL_HA = 186.0
CUSTO_MONITORAMENTO_BRL_HA = 18.0
# Produtividade e preço de referência por cultura (t/ha, R$/t).
PRODUTIVIDADE_REF_T_HA: dict[str, float] = {
    "milho": 9.5,
    "feijao": 2.4,
    "trigo": 3.2,
    "cafe": 2.0,
    "cana": 85.0,
    "soja": 3.6,
    "algodao": 4.2,
}
PRECO_REF_BRL_T: dict[str, float] = {
    "milho": 1100.0,
    "feijao": 5200.0,
    "trigo": 1400.0,
    "cafe": 28000.0,
    "cana": 140.0,
    "soja": 2300.0,
    "algodao": 9000.0,
}
# Perda de produtividade por dia de estresse hídrico (% do potencial), por sensibilidade
# do estádio: reprodutivo/enchimento é crítico, vegetativo tolera mais.
PERDA_DIA_ESTRESSE_PCT_CRITICO = 2.0
PERDA_DIA_ESTRESSE_PCT_NORMAL = 0.8
ESTADIOS_CRITICOS_AGUA: frozenset[str] = frozenset(
    {
        "VT",
        "R1",
        "R3",
        "R5",
        "R7",
        "espigamento",
        "enchimento",
        "florada",
        "chumbinho",
        "crescimento",
    }
)
# Janela climática favorável a doenças fúngicas foliares: UR alta por dias seguidos
# com temperatura amena.
DIAS_UR_ALTA_RISCO = 3
UR_ALTA_PCT = 80.0
TEMP_RISCO_FUNGICO_C = (17.0, 27.0)
INTERVALO_MIN_PULVERIZACAO_DIAS = 10
# Sinais de sensor que pedem manutenção.
BATERIA_MANUTENCAO_PCT = 15.0
RSSI_FRACO_DBM = -95


def valor_producao_brl(cultura: str, area_ha: float) -> float:
    """Valor bruto esperado da produção do talhão na safra (R$)."""
    return PRODUTIVIDADE_REF_T_HA.get(cultura, 0.0) * PRECO_REF_BRL_T.get(cultura, 0.0) * area_ha


def perda_dia_estresse_pct(estadio: str | None) -> float:
    return (
        PERDA_DIA_ESTRESSE_PCT_CRITICO
        if estadio in ESTADIOS_CRITICOS_AGUA
        else PERDA_DIA_ESTRESSE_PCT_NORMAL
    )


@dataclass(frozen=True, slots=True)
class Estadio:
    nome: str
    dia_inicio: int
    kc: float


@dataclass(frozen=True, slots=True)
class Cultura:
    nome: str
    estadios: tuple[Estadio, ...]
    ciclo_dias: int
    ndvi_base: float
    ndvi_max: float
    dia_pico_ndvi: int
    dia_senescencia: int
    perene: bool = False


CULTURAS: dict[str, Cultura] = {
    "milho": Cultura(
        nome="milho",
        estadios=(
            Estadio("VE", 0, 0.40),
            Estadio("V4", 12, 0.60),
            Estadio("V8", 28, 0.85),
            Estadio("V12", 42, 1.05),
            Estadio("VT", 58, 1.20),
            Estadio("R1", 66, 1.20),
            Estadio("R3", 80, 1.10),
            Estadio("R5", 105, 0.80),
            Estadio("R6", 125, 0.60),
        ),
        ciclo_dias=135,
        ndvi_base=0.18,
        ndvi_max=0.86,
        dia_pico_ndvi=70,
        dia_senescencia=105,
    ),
    "feijao": Cultura(
        nome="feijao",
        estadios=(
            Estadio("V1", 0, 0.40),
            Estadio("V3", 12, 0.60),
            Estadio("V4", 22, 0.85),
            Estadio("R5", 30, 1.05),
            Estadio("R7", 42, 1.10),
            Estadio("R8", 60, 0.90),
            Estadio("R9", 78, 0.55),
        ),
        ciclo_dias=90,
        ndvi_base=0.18,
        ndvi_max=0.80,
        dia_pico_ndvi=45,
        dia_senescencia=65,
    ),
    "trigo": Cultura(
        nome="trigo",
        estadios=(
            Estadio("emergencia", 0, 0.40),
            Estadio("perfilhamento", 15, 0.70),
            Estadio("alongamento", 40, 1.00),
            Estadio("espigamento", 65, 1.15),
            Estadio("enchimento", 85, 1.00),
            Estadio("maturacao", 105, 0.50),
        ),
        ciclo_dias=125,
        ndvi_base=0.20,
        ndvi_max=0.82,
        dia_pico_ndvi=75,
        dia_senescencia=100,
    ),
    "cana": Cultura(
        nome="cana",
        estadios=(
            Estadio("brotacao", 0, 0.40),
            Estadio("perfilhamento", 45, 0.80),
            Estadio("crescimento", 120, 1.25),
            Estadio("maturacao", 300, 0.75),
        ),
        ciclo_dias=365,
        ndvi_base=0.22,
        ndvi_max=0.85,
        dia_pico_ndvi=240,
        dia_senescencia=330,
    ),
    "cafe": Cultura(
        nome="cafe",
        estadios=(),  # perene: estádio pelo calendário, ver CAFE_POR_MES
        ciclo_dias=365,
        ndvi_base=0.55,
        ndvi_max=0.74,
        dia_pico_ndvi=0,
        dia_senescencia=0,
        perene=True,
    ),
    "soja": Cultura(
        nome="soja",
        estadios=(
            Estadio("VE", 0, 0.40),
            Estadio("V4", 20, 0.75),
            Estadio("R1", 40, 1.10),
            Estadio("R5", 65, 1.15),
            Estadio("R7", 95, 0.70),
        ),
        ciclo_dias=115,
        ndvi_base=0.18,
        ndvi_max=0.84,
        dia_pico_ndvi=65,
        dia_senescencia=95,
    ),
    "algodao": Cultura(
        nome="algodao",
        estadios=(
            Estadio("emergencia", 0, 0.35),
            Estadio("vegetativo", 25, 0.75),
            Estadio("florescimento", 60, 1.15),
            Estadio("macas", 100, 1.05),
            Estadio("maturacao", 140, 0.65),
        ),
        ciclo_dias=170,
        ndvi_base=0.18,
        ndvi_max=0.82,
        dia_pico_ndvi=95,
        dia_senescencia=140,
    ),
}

# Café: fenologia pelo mês (hemisfério sul, sudeste do Brasil).
CAFE_POR_MES: dict[int, Estadio] = {
    1: Estadio("expansao_frutos", 0, 1.05),
    2: Estadio("expansao_frutos", 0, 1.05),
    3: Estadio("granacao", 0, 1.00),
    4: Estadio("granacao", 0, 1.00),
    5: Estadio("maturacao", 0, 0.95),
    6: Estadio("colheita", 0, 0.90),
    7: Estadio("colheita", 0, 0.90),
    8: Estadio("repouso_pre_florada", 0, 0.85),
    9: Estadio("florada", 0, 0.95),
    10: Estadio("chumbinho", 0, 1.00),
    11: Estadio("chumbinho", 0, 1.00),
    12: Estadio("expansao_frutos", 0, 1.05),
}


def dias_apos_plantio(data_plantio: date, dia: date) -> int:
    return (dia - data_plantio).days


def estadio_em(cultura: Cultura, dap: int, dia: date) -> Estadio:
    """Estádio fenológico para ``dap`` dias após o plantio (ou pelo mês, se perene)."""
    if cultura.perene:
        return CAFE_POR_MES[dia.month]
    atual = cultura.estadios[0]
    for estadio in cultura.estadios:
        if dap >= estadio.dia_inicio:
            atual = estadio
    return atual


def ndvi_potencial(cultura: Cultura, dap: int, dia: date) -> float:
    """NDVI que a lavoura teria sem estresse: sobe em logística, platô, declina."""
    if cultura.perene:
        ajuste = {6: -0.03, 7: -0.05, 8: -0.06, 9: -0.02}.get(dia.month, 0.0)
        return cultura.ndvi_max + ajuste

    if dap <= 0:
        return cultura.ndvi_base
    amplitude = cultura.ndvi_max - cultura.ndvi_base
    if dap <= cultura.dia_pico_ndvi:
        meio = cultura.dia_pico_ndvi / 2
        escala = cultura.dia_pico_ndvi / 8
        return cultura.ndvi_base + amplitude / (1 + math.exp(-(dap - meio) / escala))
    if dap <= cultura.dia_senescencia:
        return cultura.ndvi_max
    restante = max(cultura.ciclo_dias - cultura.dia_senescencia, 1)
    fracao = min((dap - cultura.dia_senescencia) / restante, 1.0)
    return cultura.ndvi_max - fracao * (cultura.ndvi_max - 0.35)


def fracao_agua_disponivel(theta: float, cc: float, pmp: float) -> float:
    """0 no ponto de murcha, 1 na capacidade de campo."""
    return max(0.0, min(1.0, (theta - pmp) / (cc - pmp)))


def tabela_fenologia() -> list[dict[str, object]]:
    """Linhas da dimensão ``dim_fenologia`` do gold — uma por estádio (ou por mês, se perene)."""
    linhas: list[dict[str, object]] = []
    for cultura in CULTURAS.values():
        if cultura.perene:
            for mes, estadio in CAFE_POR_MES.items():
                linhas.append(
                    {
                        "cultura": cultura.nome,
                        "estadio": estadio.nome,
                        "dia_inicio": None,
                        "dia_fim": None,
                        "mes": mes,
                        "kc": estadio.kc,
                        "perene": True,
                    }
                )
            continue
        for indice, estadio in enumerate(cultura.estadios):
            proximo = (
                cultura.estadios[indice + 1].dia_inicio
                if indice + 1 < len(cultura.estadios)
                else None
            )
            linhas.append(
                {
                    "cultura": cultura.nome,
                    "estadio": estadio.nome,
                    "dia_inicio": estadio.dia_inicio,
                    "dia_fim": (proximo - 1) if proximo is not None else 9999,
                    "mes": None,
                    "kc": estadio.kc,
                    "perene": False,
                }
            )
    return linhas
