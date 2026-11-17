"""Emulador dos dispositivos de campo: o par local do firmware ESP32 (Fase 6).

Publica no broker MQTT **exatamente** o payload que
``firmware/wokwi-esp32/sketch.ino`` publica — mesmas chaves, mesma ordem,
mesmo tópico — para os 12 sensores do catálogo (``simuladores.catalogo``),
com valores tirados do mesmo modelo agronômico dos simuladores da Fase 5
(``sensores`` e ``motor``): o estado do último dia da trajetória é o "hoje" do
dispositivo, e o ciclo diário de umidade, temperatura e bateria é o mesmo que
gerava os micro-batches. Serve para demonstrar a coleta de ponta a ponta sem
internet nem placa; com o Wokwi, o ESP32 é só mais um dispositivo no mesmo
tópico.

Como o gateway de campo tem buffer offline, ~2 % das leituras saem atrasadas
(um a três ciclos depois, com o ``medido_em`` original) e ~0,5 % saem
duplicadas — é o que exercita a reabertura de janela e a deduplicação do
gateway (``ingestao.mqtt_gateway``).

Também assina o tópico dos atuadores e reage ao comando de irrigação como o
firmware: liga o "LED", publica o estado e deixa a umidade do solo subir
enquanto a irrigação está ligada.
"""

from __future__ import annotations

import json
import math
import random
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from agrosmart.config import Config, carregar_config
from agrosmart.log import obter_logger
from agrosmart.simuladores.base import Contexto, arredondar, iso_utc
from agrosmart.simuladores.catalogo import Sensor, Talhao
from agrosmart.simuladores.motor import DiaClima, simular_trajetoria, temperatura_ar_em
from agrosmart.simuladores.sensores import _bateria_em, _temperatura_solo_em, _umidade_em

TOPICO_LEITURA = "agrosmart/sensores/{sensor_id}/leitura"
TOPICO_ATUADOR = "agrosmart/atuadores/{talhao_id}/irrigacao"
TOPICO_ATUADORES = "agrosmart/atuadores/+/irrigacao"
TOPICO_ESTADO = "agrosmart/atuadores/{talhao_id}/irrigacao/estado"

#: Ordem das chaves do payload — a mesma do firmware. As dez primeiras são o
#: contrato ``sensores.v1``; ``luminosidade_lux`` é extra (roadmap v2) e o
#: gateway a descarta.
CHAVES_PAYLOAD: tuple[str, ...] = (
    "sensor_id",
    "talhao_id",
    "medido_em",
    "umidade_solo_pct",
    "temperatura_solo_c",
    "temperatura_ar_c",
    "umidade_ar_pct",
    "bateria_pct",
    "rssi_dbm",
    "luminosidade_lux",
)

P_ATRASO = 0.02
P_DUPLICATA = 0.005
#: Bateria atribuída a um sensor que a trajetória deixou calado: baixa, para
#: que o alerta SEN-10 e o LED vermelho do firmware tenham o que mostrar.
BATERIA_SENSOR_CALADO_PCT = 12.0
#: Quanto a umidade do solo sobe por ciclo com a irrigação ligada (ponto percentual).
GANHO_IRRIGACAO_POR_CICLO = 0.15


@dataclass(slots=True)
class EstadoTalhao:
    talhao: Talhao
    theta_ini: float
    theta_fim: float
    clima: DiaClima
    irrigando_ate: datetime | None = None
    umidade_irrigacao: float = 0.0  # acréscimo acumulado pela irrigação comandada

    def irrigando(self, agora: datetime) -> bool:
        return self.irrigando_ate is not None and agora < self.irrigando_ate


@dataclass(slots=True)
class Dispositivo:
    sensor: Sensor
    estado: EstadoTalhao
    nivel_bateria: float

    @property
    def topico(self) -> str:
        return TOPICO_LEITURA.format(sensor_id=self.sensor.id)


@dataclass(slots=True)
class ResumoFrota:
    dispositivos: int
    ciclos: int = 0
    publicadas: int = 0
    atrasadas: int = 0
    duplicadas: int = 0
    comandos: int = 0
    por_sensor: Counter[str] = field(default_factory=Counter)

    def para_dict(self) -> dict[str, Any]:
        return {
            "dispositivos": self.dispositivos,
            "ciclos": self.ciclos,
            "publicadas": self.publicadas,
            "atrasadas": self.atrasadas,
            "duplicadas": self.duplicadas,
            "comandos_recebidos": self.comandos,
            "por_sensor": dict(sorted(self.por_sensor.items())),
        }


def luminosidade_em(hora: float, radiacao_mj: float) -> float:
    """Lux aproximado do ciclo diário: zero à noite, pico ao meio-dia, atenuado por nuvem."""
    if not 6.0 <= hora <= 18.0:
        return 0.0
    fator_nuvem = max(0.3, min(1.0, radiacao_mj / 22.0))
    return 95_000.0 * math.sin(math.pi * (hora - 6.0) / 12.0) * fator_nuvem


class Frota:
    """Os ``n`` primeiros dispositivos do catálogo, com o modelo agronômico da Fase 5.

    Toda a lógica é pura (``ciclo``, ``aplicar_comando``): quem fala MQTT é
    :func:`publicar`. Isso é o que permite testar o payload sem broker.
    """

    def __init__(self, config: Config | None = None, *, n: int = 12, seed: int = 1) -> None:
        cfg = config or carregar_config()
        # Seis talhões: o catálogo inteiro, para que ``n`` chegue aos 12 sensores.
        ctx = Contexto(replace(cfg, qtd_talhoes=6))
        traj = simular_trajetoria(ctx)
        ultimo_dia = ctx.fim
        if not 1 <= n <= len(ctx.sensores):
            raise ValueError(f"n precisa estar entre 1 e {len(ctx.sensores)}, recebi {n}")

        self.estados: dict[str, EstadoTalhao] = {}
        for talhao in ctx.talhoes:
            dia = traj.talhoes[(talhao.id, ultimo_dia)]
            self.estados[talhao.id] = EstadoTalhao(
                talhao,
                dia.theta_ini,
                dia.theta_fim,
                traj.clima_do_talhao(ctx, talhao, ultimo_dia),
            )
        self.dispositivos: list[Dispositivo] = []
        for sensor in ctx.sensores[:n]:
            nivel = traj.bateria[sensor.id][ultimo_dia]
            self.dispositivos.append(
                Dispositivo(
                    sensor,
                    self.estados[sensor.talhao_id],
                    nivel if nivel is not None else BATERIA_SENSOR_CALADO_PCT,
                )
            )
        # nosec B311: só emula ruído de sensores (reprodutível por semente), sem uso criptográfico
        self.rng = random.Random(seed)  # nosec B311
        self.resumo = ResumoFrota(dispositivos=len(self.dispositivos))
        self._pendentes: dict[int, list[tuple[str, str]]] = {}
        self.logger = obter_logger("simuladores.dispositivo")

    # --- leitura ------------------------------------------------------------

    def leitura(self, dispositivo: Dispositivo, agora: datetime) -> dict[str, Any]:
        """Uma leitura do dispositivo no instante ``agora`` — o payload do firmware."""
        e = dispositivo.estado
        sensor = dispositivo.sensor
        clima = e.clima
        rng = self.rng
        hora = agora.hour + agora.minute / 60 + agora.second / 3600

        if e.irrigando(agora):
            teto = e.talhao.capacidade_campo_pct - _umidade_em(hora, e.theta_ini, e.theta_fim)
            e.umidade_irrigacao = min(
                e.umidade_irrigacao + GANHO_IRRIGACAO_POR_CICLO, max(0.0, teto)
            )

        theta = _umidade_em(hora, e.theta_ini, e.theta_fim) + e.umidade_irrigacao
        theta += rng.gauss(0.0, 0.25)
        temp_ar = temperatura_ar_em(hora, clima.tmin, clima.tmax) + rng.gauss(0.0, 0.3)
        temp_solo = _temperatura_solo_em(hora, clima.tmed, clima.tmax - clima.tmin)
        temp_solo += rng.gauss(0.0, 0.2)
        ur = max(20.0, min(100.0, clima.ur - 1.6 * (temp_ar - clima.tmed) + rng.gauss(0.0, 1.5)))
        bateria = _bateria_em(sensor, dispositivo.nivel_bateria, hora)
        rssi = int(round(sensor.rssi_medio_dbm + rng.gauss(0.0, 4.0)))
        lux = max(0.0, luminosidade_em(hora, clima.radiacao) + rng.gauss(0.0, 150.0))

        return {
            "sensor_id": sensor.id,
            "talhao_id": e.talhao.id,
            "medido_em": iso_utc(agora),
            "umidade_solo_pct": arredondar(max(0.0, min(100.0, theta)), 1),
            "temperatura_solo_c": arredondar(temp_solo, 1),
            "temperatura_ar_c": arredondar(temp_ar, 1),
            "umidade_ar_pct": arredondar(ur, 1),
            "bateria_pct": arredondar(max(0.0, min(100.0, bateria)), 1),
            "rssi_dbm": max(-120, min(0, rssi)),
            "luminosidade_lux": arredondar(lux, 0),
        }

    def ciclo(self, agora: datetime | None = None) -> list[tuple[str, str]]:
        """Mensagens ``(tópico, payload JSON)`` deste ciclo, já com atrasos e duplicatas.

        Uma leitura atrasada não sai agora: fica pendente e sai um a três ciclos
        depois, com o ``medido_em`` de quando foi medida. Uma duplicada sai agora
        e de novo no ciclo seguinte — igual ao reenvio do buffer do gateway.
        """
        agora = agora or datetime.now(UTC)
        indice = self.resumo.ciclos
        mensagens = self._pendentes.pop(indice, [])
        for dispositivo in self.dispositivos:
            linha = json.dumps(
                self.leitura(dispositivo, agora), ensure_ascii=False, separators=(",", ":")
            )
            item = (dispositivo.topico, linha)
            if self.rng.random() < P_ATRASO:
                self._pendentes.setdefault(indice + self.rng.randint(1, 3), []).append(item)
                self.resumo.atrasadas += 1
            else:
                mensagens.append(item)
            if self.rng.random() < P_DUPLICATA:
                self._pendentes.setdefault(indice + 1, []).append(item)
                self.resumo.duplicadas += 1
        self.resumo.ciclos += 1
        self.resumo.publicadas += len(mensagens)
        for topico, _ in mensagens:
            self.resumo.por_sensor[topico.split("/")[2]] += 1
        return mensagens

    # --- atuadores ----------------------------------------------------------

    def aplicar_comando(
        self, topico: str, payload: bytes | str, agora: datetime | None = None
    ) -> tuple[str, str] | None:
        """Trata ``{"acao": "ligar"|"desligar", "minutos": N}`` no tópico de um talhão.

        Devolve ``(tópico de estado, payload)`` a publicar, ou ``None`` se o
        comando foi ignorado (talhão desconhecido, JSON inválido, ação inválida).
        """
        agora = agora or datetime.now(UTC)
        partes = topico.split("/")
        talhao_id = partes[2] if len(partes) >= 3 else ""
        estado = self.estados.get(talhao_id)
        if estado is None:
            self.logger.warning("comando para talhão desconhecido ignorado: %s", topico)
            return None
        try:
            comando = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            self.logger.warning("comando com JSON inválido ignorado em %s", topico)
            return None
        if not isinstance(comando, dict):
            return None
        acao = comando.get("acao")
        if acao == "ligar":
            minutos = comando.get("minutos", 10)
            try:
                minutos = max(1, int(minutos))
            except (TypeError, ValueError):
                minutos = 10
            estado.irrigando_ate = agora + timedelta(minutes=minutos)
            self.logger.info("irrigação ligada em %s por %d min", talhao_id, minutos)
        elif acao == "desligar":
            estado.irrigando_ate = None
            self.logger.info("irrigação desligada em %s", talhao_id)
        else:
            self.logger.warning("ação desconhecida %r ignorada em %s", acao, topico)
            return None
        self.resumo.comandos += 1
        resposta = {
            "talhao_id": talhao_id,
            "irrigacao": "ligada" if estado.irrigando(agora) else "desligada",
            "ate": iso_utc(estado.irrigando_ate) if estado.irrigando_ate else None,
            "atualizado_em": iso_utc(agora),
        }
        return TOPICO_ESTADO.format(talhao_id=talhao_id), json.dumps(resposta, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Cliente MQTT
# ---------------------------------------------------------------------------


def publicar(
    frota: Frota,
    *,
    broker: str = "localhost",
    porta: int = 1883,
    intervalo_s: float = 5.0,
    duracao_s: float | None = None,
) -> ResumoFrota:
    """Publica um ciclo a cada ``intervalo_s`` até ``duracao_s`` segundos ou Ctrl+C."""
    import paho.mqtt.client as mqtt  # dependência opcional: pip install -e ".[iot]"

    logger = frota.logger
    cliente = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"agrosmart-dispositivos-{uuid.uuid4().hex[:8]}",
    )

    def ao_conectar(cli: Any, _dados: Any, _flags: Any, codigo: Any, _props: Any) -> None:
        if getattr(codigo, "is_failure", False):
            logger.error("broker recusou a conexão: %s", codigo)
            return
        cli.subscribe(TOPICO_ATUADORES, qos=1)
        logger.info("conectado a %s:%d · ouvindo comandos em %s", broker, porta, TOPICO_ATUADORES)

    def ao_receber(cli: Any, _dados: Any, mensagem: Any) -> None:
        try:
            resposta = frota.aplicar_comando(mensagem.topic, mensagem.payload)
        except Exception:  # noqa: BLE001 - comando hostil não derruba o emulador
            logger.exception("falha ao tratar comando em %s", mensagem.topic)
            return
        if resposta is not None:
            cli.publish(resposta[0], resposta[1], qos=1)

    cliente.on_connect = ao_conectar
    cliente.on_message = ao_receber
    cliente.reconnect_delay_set(min_delay=1, max_delay=10)
    logger.info(
        "%d dispositivos · intervalo %.1f s · conectando a %s:%d",
        len(frota.dispositivos),
        intervalo_s,
        broker,
        porta,
    )
    try:
        cliente.connect(broker, porta, keepalive=30)
    except OSError as erro:
        raise ConnectionError(f"broker {broker}:{porta} inalcançável — {erro}") from erro
    cliente.loop_start()

    limite = time.monotonic() + duracao_s if duracao_s else None
    try:
        while limite is None or time.monotonic() < limite:
            inicio = time.monotonic()
            agora = datetime.now(UTC)
            mensagens = frota.ciclo(agora)
            for topico, linha in mensagens:
                cliente.publish(topico, linha, qos=1)
                logger.debug("%s %s", topico, linha)
            exemplo = json.loads(mensagens[0][1]) if mensagens else {}
            logger.info(
                "ciclo %d · %d publicadas · pendentes %d · ex.: %s umid %s %% · bat %s %% · "
                "irrigando %s",
                frota.resumo.ciclos,
                len(mensagens),
                sum(len(v) for v in frota._pendentes.values()),
                exemplo.get("sensor_id", "—"),
                exemplo.get("umidade_solo_pct", "—"),
                exemplo.get("bateria_pct", "—"),
                sorted(t for t, e in frota.estados.items() if e.irrigando(agora)) or "ninguém",
            )
            restante = intervalo_s - (time.monotonic() - inicio)
            if limite is not None:
                restante = min(restante, limite - time.monotonic())
            if restante > 0:
                time.sleep(restante)
        logger.info("duração de %.0f s atingida — encerrando", duracao_s or 0)
    except KeyboardInterrupt:
        logger.info("interrompido pelo operador (Ctrl+C) — encerrando")
    finally:
        cliente.disconnect()
        cliente.loop_stop()
    return frota.resumo
