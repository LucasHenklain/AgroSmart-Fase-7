"""Gateway MQTT → landing: a origem **real** da fonte ``sensores`` (Fase 6).

Na Fase 5 o simulador escrevia os micro-batches JSONL *como se fosse* o
gateway do campo. Aqui o gateway existe de verdade: assina o tópico dos
dispositivos no broker, valida cada mensagem, mantém só as chaves do contrato
``sensores.v1`` e materializa as leituras em arquivos de janela —
``landing/sensores/<AAAAMMDD>/leituras_<AAAAMMDD>T<HHMM>Z.jsonl`` — no mesmo
layout, com a mesma ordem de chaves e o mesmo formato de linha que o simulador
produzia. Do ``ColetorStreaming`` em diante nada muda: o contrato é a fronteira.

O que o gateway faz e o que não faz, de propósito:

* **faz** — rejeita o que não é um objeto JSON; descarta chaves fora do
  contrato (``luminosidade_lux`` do firmware, por exemplo — roadmap v2);
  normaliza ``medido_em`` para UTC; agrupa pela janela do *instante medido*,
  não do instante de chegada (leitura atrasada cai no arquivo certo); e não
  grava a chave natural ``(sensor_id, medido_em)`` duas vezes no mesmo
  arquivo, mesmo depois de reiniciar;
* **não faz** — não julga valores. Umidade de 130 % passa aqui e é a regra
  SEN-04 do silver que a reprova. A porta protege o *schema*; a qualidade é da
  camada seguinte — o mesmo princípio dos coletores.

Idempotência: reabrir uma janela já gravada (leitura atrasada do buffer do
gateway, reinício do processo) faz *append*; as chaves naturais já presentes
no arquivo são carregadas antes do primeiro append e a repetição é contada
como duplicata, não gravada. O ``ingerir`` enxerga o arquivo alterado pelo
checksum e grava uma nova versão do lote — exatamente como na Fase 5.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agrosmart.config import Config, carregar_config
from agrosmart.contratos import Contrato, contratos_ativos
from agrosmart.log import Evento, obter_logger, registrar_evento

FONTE = "sensores"
SUBPASTA = "sensores"
TOPICO_PADRAO = "agrosmart/sensores/+/leitura"
JANELA_PADRAO_MIN = 15
FORMATO_INSTANTE = "%Y-%m-%dT%H:%M:%SZ"
ARQUIVO_RESUMO = "coleta_mqtt.json"


class ErroGateway(Exception):
    """O gateway não conseguiu operar (broker inalcançável, parâmetro inválido)."""


# ---------------------------------------------------------------------------
# Janela e normalização — funções puras, sem broker
# ---------------------------------------------------------------------------


def instante_utc(valor: Any) -> datetime | None:
    """``"2026-10-07T14:32:17Z"`` → ``datetime`` em UTC; ``None`` se não for ISO-8601."""
    if not isinstance(valor, str) or not valor.strip():
        return None
    texto = valor.strip()
    if texto[-1] in "Zz":
        texto = texto[:-1] + "+00:00"
    try:
        instante = datetime.fromisoformat(texto)
    except ValueError:
        return None
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=UTC)
    return instante.astimezone(UTC)


def inicio_da_janela(instante: datetime, janela_min: int) -> datetime:
    """Início da janela de ``janela_min`` minutos que contém ``instante`` (piso no dia)."""
    if not 1 <= janela_min <= 24 * 60:
        raise ErroGateway(f"janela precisa estar entre 1 e 1440 minutos, recebi {janela_min}")
    minutos = (instante.hour * 60 + instante.minute) // janela_min * janela_min
    return instante.replace(hour=minutos // 60, minute=minutos % 60, second=0, microsecond=0)


def caminho_da_janela(pasta: Path, inicio: datetime) -> Path:
    """Mesmo layout do simulador: ``<AAAAMMDD>/leituras_<AAAAMMDD>T<HHMM>Z.jsonl``."""
    return pasta / f"{inicio:%Y%m%d}" / f"leituras_{inicio:%Y%m%dT%H%M}Z.jsonl"


def sensor_do_topico(topico: str) -> str | None:
    """``agrosmart/sensores/SNS-0301/leitura`` → ``SNS-0301``."""
    partes = topico.split("/")
    return partes[2] if len(partes) >= 3 and partes[2] else None


@dataclass(frozen=True, slots=True)
class Normalizacao:
    """Resultado de :func:`normalizar_leitura`: o registro pronto ou o motivo do descarte."""

    registro: dict[str, Any] | None
    instante: datetime | None
    extras: tuple[str, ...]
    motivo: str | None = None


def normalizar_leitura(
    payload: Any, campos: Sequence[str], *, sensor_id_topico: str | None = None
) -> Normalizacao:
    """Projeta o payload do dispositivo no schema de origem do contrato.

    Todas as chaves de ``campos`` saem no registro, na ordem do contrato —
    ausente vira ``null`` (campo opcional que o dispositivo não mede, como
    ``ph``). Chaves fora do contrato são devolvidas em ``extras`` e não entram.
    Valores não são convertidos: tipagem é papel do silver. A única exceção é
    ``medido_em``, normalizado para ``AAAA-MM-DDTHH:MM:SSZ`` porque é ele que
    define a janela — e sem ele a leitura não tem onde cair.
    """
    if not isinstance(payload, dict):
        return Normalizacao(None, None, (), "nao_objeto")
    instante = instante_utc(payload.get("medido_em"))
    if instante is None:
        return Normalizacao(None, None, (), "sem_instante")
    registro: dict[str, Any] = {campo: payload.get(campo) for campo in campos}
    registro["medido_em"] = instante.strftime(FORMATO_INSTANTE)
    if registro.get("sensor_id") in (None, "") and sensor_id_topico:
        registro["sensor_id"] = sensor_id_topico
    extras = tuple(sorted(set(payload) - set(campos)))
    return Normalizacao(registro, instante, extras)


# ---------------------------------------------------------------------------
# Escrita em janelas
# ---------------------------------------------------------------------------


class EscritorJanelas:
    """Grava leituras em arquivos de janela sem repetir a chave natural no arquivo."""

    def __init__(self, pasta: Path, janela_min: int, chave_natural: Sequence[str]) -> None:
        inicio_da_janela(datetime.now(UTC), janela_min)  # valida a janela cedo
        self.pasta = pasta
        self.janela_min = janela_min
        self.chave_natural = tuple(chave_natural)
        self._chaves: dict[Path, set[tuple[Any, ...]]] = {}
        #: arquivo → linhas gravadas nesta execução (reaberto conta a partir de zero)
        self.gravadas_por_arquivo: dict[Path, int] = {}
        #: arquivos que já existiam com conteúdo quando esta execução os tocou
        self.reabertos: set[Path] = set()

    def destino(self, instante: datetime) -> Path:
        return caminho_da_janela(self.pasta, inicio_da_janela(instante, self.janela_min))

    def gravar(self, registro: dict[str, Any], instante: datetime) -> bool:
        """``True`` se gravou; ``False`` se a chave natural já estava no arquivo."""
        caminho = self.destino(instante)
        chaves = self._chaves.get(caminho)
        if chaves is None:
            chaves = self._chaves_existentes(caminho)
            self._chaves[caminho] = chaves
        chave = tuple(registro.get(c) for c in self.chave_natural)
        if chave in chaves:
            return False
        caminho.parent.mkdir(parents=True, exist_ok=True)
        linha = json.dumps(registro, ensure_ascii=False, separators=(",", ":"))
        with caminho.open("a", encoding="utf-8", newline="\n") as arquivo:
            arquivo.write(linha + "\n")
        chaves.add(chave)
        self.gravadas_por_arquivo[caminho] = self.gravadas_por_arquivo.get(caminho, 0) + 1
        return True

    def _chaves_existentes(self, caminho: Path) -> set[tuple[Any, ...]]:
        """Chaves naturais já gravadas por uma execução anterior (reabertura idempotente)."""
        if not caminho.exists() or caminho.stat().st_size == 0:
            return set()
        self.reabertos.add(caminho)
        chaves: set[tuple[Any, ...]] = set()
        with caminho.open(encoding="utf-8") as arquivo:
            for linha in arquivo:
                if not linha.strip():
                    continue
                try:
                    registro = json.loads(linha)
                except json.JSONDecodeError:
                    continue
                if isinstance(registro, dict):
                    chaves.add(tuple(registro.get(c) for c in self.chave_natural))
        return chaves


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ResumoColeta:
    broker: str
    topico: str
    janela_min: int
    pasta: str
    inicio: str
    fim: str | None = None
    recebidas: int = 0
    gravadas: int = 0
    duplicatas: int = 0
    descartadas: Counter[str] = field(default_factory=Counter)  # motivo → n
    chaves_extras: Counter[str] = field(default_factory=Counter)  # chave → n
    por_sensor: Counter[str] = field(default_factory=Counter)
    arquivos: dict[str, int] = field(default_factory=dict)  # arquivo → linhas gravadas
    reconexoes: int = 0

    @property
    def total_descartadas(self) -> int:
        return sum(self.descartadas.values())

    def para_dict(self) -> dict[str, Any]:
        return {
            "broker": self.broker,
            "topico": self.topico,
            "janela_min": self.janela_min,
            "pasta": self.pasta,
            "inicio": self.inicio,
            "fim": self.fim,
            "recebidas": self.recebidas,
            "gravadas": self.gravadas,
            "duplicatas": self.duplicatas,
            "descartadas": dict(sorted(self.descartadas.items())),
            "chaves_extras_ignoradas": dict(sorted(self.chaves_extras.items())),
            "por_sensor": dict(sorted(self.por_sensor.items())),
            "arquivos": self.arquivos,
            "reconexoes": self.reconexoes,
        }


class GatewayMqtt:
    """Assina o tópico dos sensores e materializa as leituras na landing.

    :meth:`processar` é a única lógica — recebe tópico e payload e devolve o
    desfecho (``gravada``, ``duplicata`` ou o motivo do descarte). O cliente
    MQTT em :meth:`executar` só a chama; os testes a chamam diretamente.
    """

    def __init__(
        self,
        config: Config | None = None,
        *,
        broker: str = "localhost",
        porta: int = 1883,
        topico: str = TOPICO_PADRAO,
        janela_min: int = JANELA_PADRAO_MIN,
        saida: Path | None = None,
        contrato: Contrato | None = None,
    ) -> None:
        self.config = config or carregar_config()
        self.contrato = contrato or contratos_ativos()[FONTE]
        self.campos = list(self.contrato.campos_de_origem)
        self.broker = broker
        self.porta = porta
        self.topico = topico
        self.pasta = Path(saida) if saida is not None else self.config.dir_landing_fonte(SUBPASTA)
        self.escritor = EscritorJanelas(self.pasta, janela_min, self.contrato.chave_natural)
        self.logger = obter_logger("ingestao.mqtt")
        self.resumo = ResumoColeta(
            broker=f"{broker}:{porta}",
            topico=topico,
            janela_min=janela_min,
            pasta=self.pasta.as_posix(),
            inicio=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        self._trava = threading.Lock()
        self._extras_avisadas: set[str] = set()
        self._encerrando = False

    # --- lógica -------------------------------------------------------------

    def processar(self, topico: str, payload: bytes | str) -> str:
        """Trata uma mensagem. Devolve ``gravada`` | ``duplicata`` | motivo do descarte."""
        with self._trava:
            self.resumo.recebidas += 1
            try:
                bruto = json.loads(payload)
            except (ValueError, UnicodeDecodeError):
                return self._descartar("json_invalido", topico, payload)

            norm = normalizar_leitura(bruto, self.campos, sensor_id_topico=sensor_do_topico(topico))
            if norm.registro is None or norm.instante is None:
                return self._descartar(norm.motivo or "invalida", topico, payload)

            for chave in norm.extras:
                self.resumo.chaves_extras[chave] += 1
                if chave not in self._extras_avisadas:
                    self._extras_avisadas.add(chave)
                    self.logger.info(
                        "chave fora do contrato %s ignorada: %r (avisado uma vez; "
                        "contagem no resumo)",
                        self.contrato.nome_completo,
                        chave,
                    )

            destino = self.escritor.destino(norm.instante)
            nova_janela = destino not in self.escritor.gravadas_por_arquivo
            registro = norm.registro
            if not self.escritor.gravar(registro, norm.instante):
                self.resumo.duplicatas += 1
                self.logger.debug(
                    "duplicata ignorada: %s @ %s", registro["sensor_id"], registro["medido_em"]
                )
                return "duplicata"

            self.resumo.gravadas += 1
            self.resumo.por_sensor[str(registro["sensor_id"])] += 1
            self.resumo.arquivos[self._relativo(destino)] = self.escritor.gravadas_por_arquivo[
                destino
            ]
            if nova_janela:
                reaberta = destino in self.escritor.reabertos
                self.logger.info(
                    "janela %s: %s", "reaberta" if reaberta else "aberta", self._relativo(destino)
                )
                self._evento(
                    "janela_reaberta" if reaberta else "janela_aberta",
                    "ok",
                    self._relativo(destino),
                    {"janela_min": self.escritor.janela_min},
                    eco_console=False,
                )
            return "gravada"

    def _descartar(self, motivo: str, topico: str, payload: bytes | str) -> str:
        self.resumo.descartadas[motivo] += 1
        amostra = payload if isinstance(payload, str) else payload[:80].decode("utf-8", "replace")
        self.logger.warning("mensagem descartada (%s) em %s: %.80r", motivo, topico, amostra)
        return motivo

    def _relativo(self, caminho: Path) -> str:
        try:
            return caminho.resolve().relative_to(self.config.raiz.resolve()).as_posix()
        except ValueError:
            return caminho.as_posix()

    def _evento(
        self,
        acao: str,
        status: str,
        mensagem: str,
        metricas: dict[str, Any],
        *,
        eco_console: bool = True,
    ) -> None:
        registrar_evento(
            Evento(
                etapa="coleta_iot",
                acao=acao,
                fonte=FONTE,
                camada="landing",
                status=status,
                mensagem=mensagem,
                metricas=metricas,
            ),
            self.config,
            eco_console=eco_console,
        )

    # --- cliente MQTT -------------------------------------------------------

    def executar(self, *, duracao_s: float | None = None) -> ResumoColeta:
        """Conecta, assina e grava até ``duracao_s`` segundos ou Ctrl+C. Devolve o resumo."""
        import paho.mqtt.client as mqtt  # dependência opcional: pip install -e ".[iot]"

        cliente = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"agrosmart-gateway-{uuid.uuid4().hex[:8]}",
        )
        cliente.on_connect = self._ao_conectar
        cliente.on_message = self._ao_receber
        cliente.on_disconnect = self._ao_desconectar
        cliente.reconnect_delay_set(min_delay=1, max_delay=10)

        self.logger.info(
            "conectando a %s:%d · tópico %s · janela %d min · saída %s",
            self.broker,
            self.porta,
            self.topico,
            self.escritor.janela_min,
            self._relativo(self.pasta),
        )
        try:
            cliente.connect(self.broker, self.porta, keepalive=30)
        except OSError as erro:
            raise ErroGateway(f"broker {self.broker}:{self.porta} inalcançável — {erro}") from erro

        cliente.loop_start()
        limite = time.monotonic() + duracao_s if duracao_s else None
        try:
            while limite is None or time.monotonic() < limite:
                time.sleep(0.5)
            self.logger.info("duração de %.0f s atingida — encerrando", duracao_s or 0)
        except KeyboardInterrupt:
            self.logger.info("interrompido pelo operador (Ctrl+C) — encerrando")
        finally:
            self._encerrando = True
            cliente.disconnect()
            cliente.loop_stop()

        self.resumo.fim = datetime.now(UTC).isoformat(timespec="seconds")
        self._persistir_resumo()
        return self.resumo

    def _ao_conectar(
        self, cliente: Any, _dados: Any, _flags: Any, codigo: Any, _props: Any
    ) -> None:
        if getattr(codigo, "is_failure", False):
            self.logger.error("broker recusou a conexão: %s", codigo)
            return
        cliente.subscribe(self.topico, qos=1)
        self.logger.info("conectado a %s:%d · assinando %s", self.broker, self.porta, self.topico)

    def _ao_receber(self, _cliente: Any, _dados: Any, mensagem: Any) -> None:
        try:
            self.processar(mensagem.topic, mensagem.payload)
        except Exception:  # noqa: BLE001 - um payload hostil não pode derrubar o gateway
            self.logger.exception("falha inesperada ao tratar mensagem em %s", mensagem.topic)
            with self._trava:
                self.resumo.descartadas["erro_interno"] += 1

    def _ao_desconectar(
        self, _cliente: Any, _dados: Any, _flags: Any, codigo: Any, _props: Any
    ) -> None:
        if self._encerrando:
            return
        self.resumo.reconexoes += 1
        self.logger.warning("conexão perdida (%s) — tentando reconectar", codigo)

    def _persistir_resumo(self) -> None:
        self.config.dir_outputs.mkdir(parents=True, exist_ok=True)
        destino = self.config.dir_outputs / ARQUIVO_RESUMO
        destino.write_text(
            json.dumps(self.resumo.para_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        r = self.resumo
        self._evento(
            "concluida",
            "alerta" if r.total_descartadas else "ok",
            f"{r.recebidas} mensagens · {r.gravadas} gravadas em {len(r.arquivos)} arquivo(s)",
            {
                "recebidas": r.recebidas,
                "gravadas": r.gravadas,
                "duplicatas": r.duplicatas,
                "descartadas": r.total_descartadas,
                "arquivos": len(r.arquivos),
                "sensores": len(r.por_sensor),
                "reconexoes": r.reconexoes,
            },
        )


def coletar(
    config: Config | None = None,
    *,
    broker: str = "localhost",
    porta: int = 1883,
    topico: str = TOPICO_PADRAO,
    janela_min: int = JANELA_PADRAO_MIN,
    duracao_s: float | None = None,
    saida: Path | None = None,
) -> ResumoColeta:
    """Atalho de uma chamada: monta o gateway e executa até a duração ou Ctrl+C."""
    gateway = GatewayMqtt(
        config, broker=broker, porta=porta, topico=topico, janela_min=janela_min, saida=saida
    )
    return gateway.executar(duracao_s=duracao_s)
