"""Escritor da fonte ``sensores``: micro-batches de 15 minutos do gateway MQTT.

Um arquivo JSONL por intervalo de 15 min, uma linha por sensor ativo. O
gateway real tem buffer offline e reenvia o que acha que não entregou — por
isso ~3 % das leituras chegam atrasadas (no arquivo de um intervalo seguinte,
com o ``medido_em`` original) e ~0,5 % chegam duplicadas. A primeira é
comportamento normal que o silver precisa tolerar; a segunda é defeito
(SEN-02) e está no registro.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta

from agrosmart.simuladores.base import Contexto, DefeitoInjetado, arredondar, iso_utc
from agrosmart.simuladores.catalogo import Sensor
from agrosmart.simuladores.defeitos import POR_ID, sortear
from agrosmart.simuladores.motor import Trajetoria, temperatura_ar_em

FONTE = "sensores"
SUBPASTA = "sensores"
INTERVALO_MIN = 15
SLOTS_POR_DIA = 24 * 60 // INTERVALO_MIN
_P_ATRASO = 0.03


def _umidade_em(hora: float, theta_ini: float, theta_fim: float) -> float:
    linear = theta_ini + (theta_fim - theta_ini) * hora / 24
    queda_diurna = 0.6 * math.sin(math.pi * (hora - 6) / 12) if 6 <= hora <= 18 else 0.0
    return linear - queda_diurna


def _temperatura_solo_em(hora: float, tmed: float, amplitude: float) -> float:
    return tmed + 1.0 + amplitude * 0.45 * math.cos(2 * math.pi * (hora - 18) / 24)


def _bateria_em(sensor: Sensor, nivel_dia: float, hora: float) -> float:
    if sensor.solar:
        return max(0.0, min(100.0, nivel_dia + 5.0 * math.sin(2 * math.pi * (hora - 14) / 24)))
    return max(0.0, nivel_dia - sensor.taxa_descarga_dia * hora / 24)


def gerar_sensores(ctx: Contexto, traj: Trajetoria) -> None:
    pasta = ctx.landing(SUBPASTA)
    rng = ctx.rng("sensores")
    rng_def = ctx.rng("defeitos.sensores")
    tipos_por_registro = [POR_ID[t] for t in ("SEN-NUL", "SEN-UMI", "SEN-TLH", "SEN-ID", "SEN-FUT")]
    tipo_dup = POR_ID["SEN-DUP"]

    pendentes: dict[int, list[str]] = {}
    atrasadas = 0

    # As leituras existem até o "agora" do pipeline (03h00 da data de referência):
    # o dia seguinte à janela entra com os intervalos anteriores a esse instante,
    # como no mundo real — senão todo sensor pareceria atrasado às três da manhã.
    referencia = ctx.instante_referencia.replace(tzinfo=None)
    dia_extra = ctx.fim + timedelta(days=1)
    meia_noite_extra = datetime(dia_extra.year, dia_extra.month, dia_extra.day)
    slots_extra = [
        s
        for s in range(SLOTS_POR_DIA)
        if meia_noite_extra + timedelta(minutes=s * INTERVALO_MIN) < referencia
    ]
    agenda = [(i, d, range(SLOTS_POR_DIA)) for i, d in enumerate(ctx.dias)]
    agenda.append((len(ctx.dias), dia_extra, range(len(slots_extra))))
    total_slots = len(ctx.dias) * SLOTS_POR_DIA + len(slots_extra)

    for indice_dia, dia, slots in agenda:
        # Estado agronômico do dia extra: o fim do último dia simulado (3 h de diferença).
        dia_estado = dia if dia <= ctx.fim else ctx.fim
        for slot in slots:
            indice_slot = indice_dia * SLOTS_POR_DIA + slot
            minutos = slot * INTERVALO_MIN
            hora = minutos / 60
            instante = datetime(dia.year, dia.month, dia.day, minutos // 60, minutos % 60)
            arquivo = (
                f"{dia:%Y%m%d}/leituras_{dia:%Y%m%d}T{minutos // 60:02d}{minutos % 60:02d}Z.jsonl"
            )
            linhas = pendentes.pop(indice_slot, [])

            for sensor in ctx.sensores:
                nivel_bateria = traj.bateria[sensor.id][dia_estado]
                if nivel_bateria is None:
                    continue  # sensor calado: nenhuma leitura publicada

                talhao = ctx.talhao(sensor.talhao_id)
                estado = traj.talhoes[(talhao.id, dia_estado)]
                clima = traj.clima_do_talhao(ctx, talhao, dia_estado)

                theta_ini = estado.theta_ini if dia <= ctx.fim else estado.theta_fim
                theta = _umidade_em(hora, theta_ini, estado.theta_fim) + rng.gauss(0.0, 0.25)
                temp_ar = temperatura_ar_em(hora, clima.tmin, clima.tmax) + rng.gauss(0.0, 0.3)
                temp_solo = _temperatura_solo_em(
                    hora, clima.tmed, clima.tmax - clima.tmin
                ) + rng.gauss(0.0, 0.2)
                ur = max(
                    20.0, min(100.0, clima.ur - 1.6 * (temp_ar - clima.tmed) + rng.gauss(0.0, 1.5))
                )
                condutividade = talhao.condutividade_base_ds_m * (
                    1 + 0.015 * (talhao.capacidade_campo_pct - theta)
                ) + rng.gauss(0.0, 0.03)
                ph = talhao.ph_base + 0.004 * indice_dia + rng.gauss(0.0, 0.05)
                bateria = _bateria_em(sensor, nivel_bateria, hora)
                rssi = int(round(sensor.rssi_medio_dbm + rng.gauss(0.0, 4.0)))

                registro: dict[str, object] = {
                    "sensor_id": sensor.id,
                    "talhao_id": talhao.id,
                    "medido_em": iso_utc(instante),
                    "umidade_solo_pct": arredondar(theta, 1),
                    "temperatura_solo_c": arredondar(temp_solo, 1),
                    "temperatura_ar_c": arredondar(temp_ar, 1),
                    "umidade_ar_pct": arredondar(ur, 1),
                    "condutividade_ds_m": arredondar(max(condutividade, 0.01), 2),
                    "ph": arredondar(ph, 2),
                    "bateria_pct": arredondar(bateria, 1),
                    "rssi_dbm": max(-120, min(0, rssi)),
                }

                # No máximo um defeito por leitura: o primeiro sorteado vence.
                for tipo in tipos_por_registro:
                    if not sortear(rng_def, tipo):
                        continue
                    chave = {"sensor_id": sensor.id, "medido_em": registro["medido_em"]}
                    if tipo.id == "SEN-NUL":
                        registro["umidade_solo_pct"] = None
                    elif tipo.id == "SEN-UMI":
                        registro["umidade_solo_pct"] = arredondar(
                            100.0 + rng_def.uniform(1.0, 40.0), 1
                        )
                    elif tipo.id == "SEN-TLH":
                        registro["talhao_id"] = "TLH-99"
                    elif tipo.id == "SEN-ID":
                        registro["sensor_id"] = "SNSX-01"
                        chave["sensor_id"] = "SNSX-01"
                    elif tipo.id == "SEN-FUT":
                        registro["medido_em"] = iso_utc(instante + timedelta(days=2))
                        chave["medido_em"] = registro["medido_em"]
                    ctx.registro.adicionar(
                        DefeitoInjetado(tipo.id, FONTE, tipo.regra, chave, arquivo, tipo.descricao)
                    )
                    break

                linha = json.dumps(registro, ensure_ascii=False, separators=(",", ":"))

                if rng.random() < _P_ATRASO:
                    destino = min(indice_slot + rng.randint(1, 3), total_slots - 1)
                    if destino == indice_slot:
                        linhas.append(linha)
                    else:
                        pendentes.setdefault(destino, []).append(linha)
                        atrasadas += 1
                else:
                    linhas.append(linha)

                if sortear(rng_def, tipo_dup):
                    destino = min(indice_slot + 1, total_slots - 1)
                    if destino == indice_slot:
                        linhas.append(linha)
                    else:
                        pendentes.setdefault(destino, []).append(linha)
                    ctx.registro.adicionar(
                        DefeitoInjetado(
                            "SEN-DUP",
                            FONTE,
                            tipo_dup.regra,
                            {
                                "sensor_id": registro["sensor_id"],
                                "medido_em": registro["medido_em"],
                            },
                            arquivo,
                            tipo_dup.descricao,
                        )
                    )

            conteudo = "\n".join(linhas) + ("\n" if linhas else "")
            ctx.gravar_texto(FONTE, pasta / arquivo, conteudo, registros=len(linhas))

    # Métrica operacional, não defeito: quantas leituras chegaram fora de ordem.
    ctx.metricas_extras["sensores_leituras_atrasadas"] = atrasadas


def instante_do_arquivo(nome: str) -> datetime:
    """``leituras_20260828T0915Z.jsonl`` → ``datetime(2026, 8, 28, 9, 15)``."""
    marca = nome.removeprefix("leituras_").removesuffix(".jsonl")
    return datetime.strptime(marca, "%Y%m%dT%H%MZ")


def dia_do_arquivo(nome: str) -> date:
    return instante_do_arquivo(nome).date()
