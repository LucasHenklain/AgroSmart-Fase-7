"""Atuadores: o plano vira comando MQTT para o controlador de irrigação do talhão.

Contrato com o firmware/emulador (``firmware/``, ``simuladores/dispositivo_mqtt.py``):

* tópico ``agrosmart/atuadores/<talhao_id>/irrigacao``
* payload ``{"acao": "ligar", "minutos": N}``
* QoS 1 — o comando chega pelo menos uma vez; o controlador ignora repetição
  enquanto a válvula já está aberta.

Só ações com ``comando_mqtt`` são publicadas (hoje, ``irrigar``). Alertas e
manutenção ficam no plano para o dashboard e para quem vai a campo. O
:class:`Publicador` é um protocolo: o paho-mqtt é a implementação real e
:class:`PublicadorEmMemoria` é o dublê dos testes e do ``--dry-run``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from agrosmart.config import Config
from agrosmart.log import Evento, obter_logger, registrar_evento

if TYPE_CHECKING:
    from agrosmart.automacao.motor import Plano

QOS = 1
CLIENT_ID = "agrosmart-automacao"


class ErroPublicacao(Exception):
    """O broker não confirmou a entrega do comando."""


class Publicador(Protocol):
    def publicar(self, topico: str, payload: dict[str, Any]) -> None: ...


@dataclass(slots=True)
class PublicadorEmMemoria:
    """Dublê: guarda o que publicaria. ``falhar_em`` simula um broker que não confirma."""

    publicados: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    falhar_em: set[str] = field(default_factory=set)

    def publicar(self, topico: str, payload: dict[str, Any]) -> None:
        if topico in self.falhar_em:
            raise ErroPublicacao(f"broker não confirmou {topico}")
        self.publicados.append((topico, dict(payload)))


class PublicadorPaho:
    """Cliente MQTT real (paho-mqtt ≥ 2), QoS 1, com espera pela confirmação (PUBACK)."""

    def __init__(
        self,
        broker: str = "localhost",
        porta: int = 1883,
        *,
        timeout_s: float = 5.0,
        client_id: str = CLIENT_ID,
    ) -> None:
        self.broker = broker
        self.porta = porta
        self.timeout_s = timeout_s
        self.client_id = client_id
        self._cliente: Any = None

    def conectar(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as erro:
            raise ErroPublicacao("paho-mqtt não instalado — `pip install -e .[iot]`") from erro
        cliente = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id, protocol=mqtt.MQTTv311
        )
        try:
            cliente.connect(self.broker, self.porta, keepalive=30)
        except OSError as erro:
            raise ErroPublicacao(
                f"broker MQTT {self.broker}:{self.porta} inalcançável — {erro}"
            ) from erro
        cliente.loop_start()
        self._cliente = cliente

    def fechar(self) -> None:
        if self._cliente is None:
            return
        self._cliente.loop_stop()
        self._cliente.disconnect()
        self._cliente = None

    def __enter__(self) -> PublicadorPaho:
        self.conectar()
        return self

    def __exit__(self, *_: object) -> None:
        self.fechar()

    def publicar(self, topico: str, payload: dict[str, Any]) -> None:
        if self._cliente is None:
            self.conectar()
        info = self._cliente.publish(topico, json.dumps(payload), qos=QOS)
        try:
            info.wait_for_publish(timeout=self.timeout_s)
        except (RuntimeError, ValueError) as erro:
            raise ErroPublicacao(f"{topico}: {erro}") from erro
        if not info.is_published():
            raise ErroPublicacao(f"{topico}: sem confirmação do broker em {self.timeout_s}s")


def publicar_plano(
    plano: Plano, publicador: Publicador, *, config: Config | None = None
) -> dict[str, Any]:
    """Publica cada comando pendente; quem confirmou vira ``publicado``, quem falhou fica
    ``simulado`` e aparece em ``falhas``. Reexecutar não republica o que já foi."""
    logger = obter_logger("automacao.atuadores")
    publicadas = 0
    falhas: list[dict[str, str]] = []
    for acao in plano.comandos_pendentes():
        assert acao.comando_mqtt is not None
        topico, payload = acao.comando_mqtt.topico, acao.comando_mqtt.payload
        try:
            publicador.publicar(topico, payload)
        except ErroPublicacao as erro:
            falhas.append({"id": acao.id, "topico": topico, "erro": str(erro)})
            logger.error("%s não publicada: %s", acao.id, erro)
            continue
        acao.status = "publicado"
        publicadas += 1
        logger.info("%s publicada em %s → %s", acao.id, topico, json.dumps(payload))
        if config is not None:
            registrar_evento(
                Evento(
                    etapa="automacao",
                    acao="comando_publicado",
                    fonte=acao.talhao_id,
                    mensagem=f"{acao.id} {topico} {json.dumps(payload)}",
                    metricas={"qos": QOS, "minutos": payload.get("minutos")},
                ),
                config,
                eco_console=False,
            )
    return {"solicitada": True, "publicadas": publicadas, "falhas": falhas}
