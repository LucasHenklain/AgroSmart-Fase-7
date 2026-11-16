"""Modelo físico simplificado que dá coerência aos dados simulados.

Nada aqui pretende substituir um modelo de cultura; o objetivo é que os dados
das seis fontes **contem a mesma história**: a chuva que a estação registra é
a que molha o solo que o sensor mede; a irrigação que o ERP lança é a que
responde à umidade baixa; o NDVI que o drone vê cai quando o talhão passou
dias em estresse. Sem essa coerência o copiloto de IA não teria o que
raciocinar — e o avaliador perceberia.

O conhecimento agronômico (culturas, estádios, Kc, limiares) vive em
:mod:`agrosmart.agronomia`, compartilhado com o gold e a IA. Este módulo só
tem a física: radiação, evapotranspiração e balanço hídrico.

Referências:
* FAO-56 (Allen et al., 1998) — radiação extraterrestre (eq. 21).
* Hargreaves & Samani (1985) — ETo e radiação global a partir de Tmin/Tmax.
"""

from __future__ import annotations

import math

from agrosmart.agronomia import (  # noqa: F401 - reexportado para os simuladores
    CULTURAS,
    EFICIENCIA_CHUVA,
    FRACAO_ESTRESSE,
    FRACAO_IRRIGACAO,
    PROFUNDIDADE_RAIZ_MM,
    Cultura,
    Estadio,
    estadio_em,
    fracao_agua_disponivel,
    ndvi_potencial,
)
from agrosmart.simuladores.catalogo import Talhao

PROFUNDIDADE_MM = PROFUNDIDADE_RAIZ_MM


def dias_apos_plantio(talhao: Talhao, dia: object) -> int:
    return (dia - talhao.data_plantio).days  # type: ignore[operator]


def radiacao_extraterrestre_mm(latitude_graus: float, dia_do_ano: int) -> float:
    """Radiação extraterrestre diária (FAO-56, eq. 21) em mm de evaporação equivalente."""
    phi = math.radians(latitude_graus)
    dr = 1 + 0.033 * math.cos(2 * math.pi * dia_do_ano / 365)
    delta = 0.409 * math.sin(2 * math.pi * dia_do_ano / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    gsc = 0.0820  # MJ m-2 min-1
    ra_mj = (
        (24 * 60 / math.pi)
        * gsc
        * dr
        * (ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws))
    )
    return ra_mj / 2.45  # MJ m-2 dia-1 → mm dia-1


def eto_hargreaves(tmin: float, tmax: float, ra_mm: float) -> float:
    """Evapotranspiração de referência por Hargreaves-Samani (mm/dia)."""
    tmed = (tmin + tmax) / 2
    amplitude = max(tmax - tmin, 0.1)
    return max(0.0023 * ra_mm * (tmed + 17.8) * math.sqrt(amplitude), 0.3)


def radiacao_global_mj(tmin: float, tmax: float, ra_mm: float) -> float:
    """Radiação solar global estimada (Hargreaves-Samani), em MJ m-2 dia-1."""
    amplitude = max(tmax - tmin, 0.1)
    return 0.16 * math.sqrt(amplitude) * ra_mm * 2.45


def passo_balanco_hidrico(
    theta: float,
    chuva_mm: float,
    irrigacao_mm: float,
    etc_mm: float,
    cc: float,
    pmp: float,
) -> tuple[float, float]:
    """Um dia de balanço hídrico na camada de 0–30 cm.

    Devolve ``(theta_novo, drenagem_mm)``. A umidade nunca cai abaixo do ponto
    de murcha (a planta reduz a transpiração) nem passa da capacidade de campo
    (o excedente drena).
    """
    entrada_mm = chuva_mm * EFICIENCIA_CHUVA + irrigacao_mm
    delta_pct = (entrada_mm - etc_mm) / PROFUNDIDADE_MM * 100
    bruto = theta + delta_pct
    drenagem = 0.0
    if bruto > cc:
        drenagem = (bruto - cc) / 100 * PROFUNDIDADE_MM
        bruto = cc
    return max(bruto, pmp), drenagem


def lamina_irrigacao_mm(theta: float, cc: float) -> float:
    """Lâmina que repõe ~85 % do déficit até a capacidade de campo, em passos de 0,5 mm."""
    deficit_mm = (cc - theta) / 100 * PROFUNDIDADE_MM
    lamina = min(deficit_mm * 0.85, 30.0)
    return max(round(lamina * 2) / 2, 4.0)


from agrosmart.agronomia import CUSTO_IRRIGACAO_BRL_MM_HA  # noqa: E402,F401 - reexportado
