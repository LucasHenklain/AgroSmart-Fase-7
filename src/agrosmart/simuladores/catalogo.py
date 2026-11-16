"""Dados mestres da simulação: fazendas, talhões e sensores.

São fixos de propósito. O que varia entre execuções é o clima e o que decorre
dele; o cadastro é o mesmo, para que ``TLH-03`` signifique sempre o mesmo
talhão em qualquer print, documento ou relatório da IA.

Culturas escolhidas para serem plausíveis em agosto no interior de São Paulo:
milho e feijão de inverno irrigados, trigo em maturação, café perene em
pré-florada e cana em sequeiro — este último é o caso que a trava agronômica
do copiloto precisa: um talhão para o qual recomendar irrigação é erro.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Fazenda:
    id: str
    nome: str
    municipio: str
    estacao_id: str
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class Talhao:
    id: str
    fazenda_id: str
    nome: str
    area_ha: float
    cultura: str
    variedade: str
    data_plantio: date
    data_colheita_prevista: date
    latitude: float
    longitude: float
    tipo_solo: str
    sistema_irrigacao: str
    capacidade_campo_pct: float
    ponto_murcha_pct: float
    umidade_inicial_pct: float
    condutividade_base_ds_m: float
    ph_base: float

    @property
    def irrigado(self) -> bool:
        return self.sistema_irrigacao != "sequeiro"


@dataclass(frozen=True, slots=True)
class Sensor:
    id: str
    talhao_id: str
    solar: bool
    bateria_inicial_pct: float
    taxa_descarga_dia: float
    rssi_medio_dbm: int
    falha_bateria: bool = False


FAZENDAS: tuple[Fazenda, ...] = (
    Fazenda(
        id="FAZ-01",
        nome="Fazenda Santa Efigênia",
        municipio="Limeira/SP",
        estacao_id="EST-SP-014",
        latitude=-22.7402,
        longitude=-47.3241,
    ),
    Fazenda(
        id="FAZ-02",
        nome="Sítio Boa Vista",
        municipio="Campinas/SP",
        estacao_id="EST-SP-027",
        latitude=-22.9031,
        longitude=-47.0644,
    ),
)

TALHOES: tuple[Talhao, ...] = (
    Talhao(
        id="TLH-01",
        fazenda_id="FAZ-01",
        nome="Alto da Sede",
        area_ha=18.6,
        cultura="milho",
        variedade="AG 8088 PRO4",
        data_plantio=date(2026, 6, 14),
        data_colheita_prevista=date(2026, 10, 20),
        latitude=-22.7381,
        longitude=-47.3312,
        tipo_solo="latossolo vermelho",
        sistema_irrigacao="pivo_central",
        capacidade_campo_pct=34.0,
        ponto_murcha_pct=16.0,
        umidade_inicial_pct=27.0,
        condutividade_base_ds_m=1.05,
        ph_base=6.1,
    ),
    Talhao(
        id="TLH-02",
        fazenda_id="FAZ-01",
        nome="Baixada do Córrego",
        area_ha=12.4,
        cultura="milho",
        variedade="DKB 390 PRO3",
        data_plantio=date(2026, 7, 5),
        data_colheita_prevista=date(2026, 11, 8),
        latitude=-22.7455,
        longitude=-47.3208,
        tipo_solo="argissolo vermelho-amarelo",
        sistema_irrigacao="pivo_central",
        capacidade_campo_pct=30.0,
        ponto_murcha_pct=13.0,
        umidade_inicial_pct=25.0,
        condutividade_base_ds_m=0.92,
        ph_base=5.8,
    ),
    Talhao(
        id="TLH-03",
        fazenda_id="FAZ-01",
        nome="Várzea Norte",
        area_ha=9.8,
        cultura="feijao",
        variedade="IPR Tangará",
        data_plantio=date(2026, 7, 20),
        data_colheita_prevista=date(2026, 10, 12),
        latitude=-22.7302,
        longitude=-47.3151,
        tipo_solo="nitossolo vermelho",
        sistema_irrigacao="aspersao",
        capacidade_campo_pct=36.0,
        ponto_murcha_pct=18.0,
        umidade_inicial_pct=30.0,
        condutividade_base_ds_m=1.18,
        ph_base=6.3,
    ),
    Talhao(
        id="TLH-04",
        fazenda_id="FAZ-02",
        nome="Espigão",
        area_ha=22.0,
        cultura="trigo",
        variedade="TBIO Sonic",
        data_plantio=date(2026, 5, 10),
        data_colheita_prevista=date(2026, 9, 15),
        latitude=-22.9014,
        longitude=-47.0587,
        tipo_solo="latossolo vermelho-amarelo",
        sistema_irrigacao="aspersao",
        capacidade_campo_pct=31.0,
        ponto_murcha_pct=14.0,
        umidade_inicial_pct=22.0,
        condutividade_base_ds_m=0.88,
        ph_base=5.9,
    ),
    Talhao(
        id="TLH-05",
        fazenda_id="FAZ-02",
        nome="Encosta do Café",
        area_ha=7.5,
        cultura="cafe",
        variedade="Catuaí Vermelho IAC 144",
        data_plantio=date(2021, 3, 15),
        data_colheita_prevista=date(2027, 6, 30),
        latitude=-22.9102,
        longitude=-47.0641,
        tipo_solo="latossolo vermelho",
        sistema_irrigacao="gotejamento",
        capacidade_campo_pct=33.0,
        ponto_murcha_pct=15.0,
        umidade_inicial_pct=26.0,
        condutividade_base_ds_m=1.32,
        ph_base=5.6,
    ),
    Talhao(
        id="TLH-06",
        fazenda_id="FAZ-02",
        nome="Canavial da Estrada",
        area_ha=41.3,
        cultura="cana",
        variedade="RB867515",
        data_plantio=date(2025, 10, 1),
        data_colheita_prevista=date(2026, 10, 30),
        latitude=-22.8956,
        longitude=-47.0710,
        tipo_solo="argissolo vermelho",
        sistema_irrigacao="sequeiro",
        capacidade_campo_pct=29.0,
        ponto_murcha_pct=12.0,
        umidade_inicial_pct=20.0,
        condutividade_base_ds_m=0.76,
        ph_base=5.4,
    ),
)

RESPONSAVEIS: dict[str, tuple[str, ...]] = {
    "FAZ-01": ("OPE-011", "OPE-012", "OPE-013"),
    "FAZ-02": ("OPE-021", "OPE-022"),
}


def talhoes_ativos(quantidade: int) -> tuple[Talhao, ...]:
    """Os primeiros ``quantidade`` talhões do catálogo (1 a 6)."""
    if not 1 <= quantidade <= len(TALHOES):
        raise ValueError(f"qtd_talhoes precisa estar entre 1 e {len(TALHOES)}, recebi {quantidade}")
    return TALHOES[:quantidade]


def sensores_para(talhoes: tuple[Talhao, ...]) -> tuple[Sensor, ...]:
    """Dois sensores por talhão. Dois deles carregam cenários operacionais.

    * ``SNS-0302`` (TLH-03) não é solar e tem bateria em declínio: dispara o
      alerta SEN-10, depois cala — é o incidente INC-01 do runbook acontecendo.
    * ``SNS-0602`` (TLH-06, o canavial mais distante do gateway) tem sinal
      fraco: alimenta o alerta SEN-11 sem nunca reprovar registro.
    """
    sensores: list[Sensor] = []
    for talhao in talhoes:
        numero = int(talhao.id.split("-")[1])
        for posicao in (1, 2):
            sensor_id = f"SNS-{numero:02d}{posicao:02d}"
            if sensor_id == "SNS-0302":
                sensores.append(
                    Sensor(
                        id=sensor_id,
                        talhao_id=talhao.id,
                        solar=False,
                        bateria_inicial_pct=58.0,
                        taxa_descarga_dia=2.9,
                        rssi_medio_dbm=-74,
                        falha_bateria=True,
                    )
                )
            elif sensor_id == "SNS-0602":
                sensores.append(
                    Sensor(
                        id=sensor_id,
                        talhao_id=talhao.id,
                        solar=True,
                        bateria_inicial_pct=90.0,
                        taxa_descarga_dia=0.0,
                        rssi_medio_dbm=-90,
                    )
                )
            else:
                sensores.append(
                    Sensor(
                        id=sensor_id,
                        talhao_id=talhao.id,
                        solar=True,
                        bateria_inicial_pct=88.0 + (numero * 3 + posicao * 2) % 9,
                        taxa_descarga_dia=0.0,
                        rssi_medio_dbm=-66 - (numero * 5 + posicao * 3) % 14,
                    )
                )
    return tuple(sensores)
