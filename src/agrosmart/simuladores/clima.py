"""Escritor da fonte ``clima``: respostas diárias de uma API meteorológica.

Produz um JSON por estação e por dia no envelope do Open-Meteo. Os nomes de
campo são os da API, não os do contrato — é o silver que faz a tradução. Isso
é o que torna a fonte *semiestruturada* de verdade, e não um CSV disfarçado.
"""

from __future__ import annotations

from datetime import date, timedelta

from agrosmart.simuladores.base import Contexto, DefeitoInjetado
from agrosmart.simuladores.defeitos import POR_ID, distribuir_posicoes
from agrosmart.simuladores.motor import DiaClima, Trajetoria

FONTE = "clima"
SUBPASTA = "clima"

_UNIDADES = {
    "time": "iso8601",
    "temperature_2m_max": "°C",
    "temperature_2m_min": "°C",
    "temperature_2m_mean": "°C",
    "relative_humidity_2m_mean": "%",
    "precipitation_sum": "mm",
    "wind_speed_10m_max": "m/s",
    "shortwave_radiation_sum": "MJ/m²",
    "et0_fao_evapotranspiration": "mm",
}


def _colunas(dia: DiaClima) -> dict[str, object]:
    return {
        "time": dia.data.isoformat(),
        "temperature_2m_max": dia.tmax,
        "temperature_2m_min": dia.tmin,
        "temperature_2m_mean": dia.tmed,
        "relative_humidity_2m_mean": dia.ur,
        "precipitation_sum": dia.chuva,
        "wind_speed_10m_max": dia.vento,
        "shortwave_radiation_sum": dia.radiacao,
        "et0_fao_evapotranspiration": dia.eto,
    }


def _montar_daily(dias: list[dict[str, object]]) -> dict[str, list[object]]:
    return {chave: [d[chave] for d in dias] for chave in _UNIDADES}


def gerar_clima(ctx: Contexto, traj: Trajetoria) -> None:
    pasta = ctx.landing(SUBPASTA)
    rng = ctx.rng("defeitos.clima")

    chaves = sorted(traj.clima)
    posicoes = distribuir_posicoes(
        rng, len(chaves), [POR_ID[t] for t in ("CLI-INV", "CLI-UR", "CLI-DUP")]
    )

    for indice, (estacao_id, dia) in enumerate(chaves):
        registro = traj.clima[(estacao_id, dia)]
        fazenda = next(f for f in ctx.fazendas if f.estacao_id == estacao_id)
        colunas = _colunas(registro)
        arquivo = f"{estacao_id}_{dia:%Y%m%d}.json"
        linhas = [colunas]

        if indice in posicoes["CLI-INV"]:
            colunas["temperature_2m_max"], colunas["temperature_2m_min"] = (
                colunas["temperature_2m_min"],
                colunas["temperature_2m_max"],
            )
            ctx.registro.adicionar(
                DefeitoInjetado(
                    "CLI-INV",
                    FONTE,
                    "CLI-03",
                    {"estacao_id": estacao_id, "data": dia.isoformat()},
                    arquivo,
                    POR_ID["CLI-INV"].descricao,
                )
            )
        if indice in posicoes["CLI-UR"]:
            colunas["relative_humidity_2m_mean"] = 103.4
            ctx.registro.adicionar(
                DefeitoInjetado(
                    "CLI-UR",
                    FONTE,
                    "CLI-05",
                    {"estacao_id": estacao_id, "data": dia.isoformat()},
                    arquivo,
                    POR_ID["CLI-UR"].descricao,
                )
            )
        if indice in posicoes["CLI-DUP"]:
            anterior = traj.clima.get((estacao_id, dia - timedelta(days=1)))
            if anterior is not None:
                linhas = [_colunas(anterior), colunas]
                ctx.registro.adicionar(
                    DefeitoInjetado(
                        "CLI-DUP",
                        FONTE,
                        "CLI-02",
                        {"estacao_id": estacao_id, "data": anterior.data.isoformat()},
                        arquivo,
                        POR_ID["CLI-DUP"].descricao,
                    )
                )

        envelope = {
            "latitude": fazenda.latitude,
            "longitude": fazenda.longitude,
            "elevation": 640.0 if fazenda.id == "FAZ-01" else 690.0,
            "timezone": "UTC",
            "provedor": "open-meteo",
            "estacao_id": estacao_id,
            "fazenda_id": fazenda.id,
            "consultado_em": f"{(dia + timedelta(days=1)).isoformat()}T03:00:00Z",
            "daily_units": _UNIDADES,
            "daily": _montar_daily(linhas),
        }
        ctx.gravar_json(FONTE, pasta / arquivo, envelope, registros=len(linhas))


def dias_cobertos(ctx: Contexto) -> list[date]:
    return list(ctx.dias)
