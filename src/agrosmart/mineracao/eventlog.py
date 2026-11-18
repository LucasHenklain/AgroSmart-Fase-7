"""Extração do event log a partir da trilha ``outputs/logs/eventos.jsonl``.

Modelagem (Fase 7, mineração de processos):

* **caso** (*case id*) = ``execucao_id`` — um ciclo do worker, uma carga inicial ou um
  ``run`` da CLI. Eventos antigos, sem ``execucao_id``, não pertencem a caso nenhum e ficam
  fora (são contados no relatório);
* **atividade** = etapa do pipeline (``ingerir``, ``silver``, ``gold``…). Uma etapa que
  falhou vira a atividade ``"<etapa> (falha)"``: no DFG e nas variantes o caminho de falha
  aparece como um desvio explícito (nó/arestas vermelhos) em vez de se esconder num
  atributo, e a variante já diz *onde* o caso quebrou;
* **etapas puladas** (``etapa_pulada``) ficam **fora** do fluxo de controle: têm duração
  zero, e no worker ``simular``/``copiloto`` são puladas em todo ciclo — como atividades,
  só poluiriam o DFG. Elas viram atributo do caso (``puladas``); assim o atalho do ciclo
  incremental (v2: ``ingerir → decidir``) aparece no DFG como a aresta que de fato é;
* **lifecycle** = ``start``/``complete`` (``etapa_iniciada`` / ``etapa_concluida`` ou
  ``etapa_falhou``); a forma "intervalo" junta o par numa linha com início, fim e duração;
* **atividade de domínio** ``publicar comando``: os eventos ``comando_publicado`` de um caso
  viram uma única instância (início = primeiro comando, fim = último, atributo
  ``comandos``), para o número de comandos não multiplicar variantes;
* **recurso** = ``origem`` (worker | cli).

Tudo aqui é Python puro (sem pandas/pm4py), determinístico e reexecutável.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ETAPAS: tuple[str, ...] = (
    "simular",
    "ingerir",
    "silver",
    "gold",
    "catalogo",
    "copiloto",
    "decidir",
    "atuar",
)
ATIVIDADE_COMANDO = "publicar comando"
SUFIXO_FALHA = " (falha)"
INICIO = "▶ início"
FIM = "■ fim"

# Atributos numéricos das etapas que valem uma coluna no event log.
ATRIBUTOS_NUMERICOS: tuple[str, ...] = (
    "lotes",
    "gravados",
    "atualizados",
    "pulados",
    "rejeitados",
    "lidos",
    "aprovados",
    "quarentena",
    "tabelas",
    "talhoes",
    "acoes",
    "comandos",
    "publicadas",
    "falhas",
)

_RE_COMANDO = re.compile(r"^(?P<id>\S+)\s+(?P<topico>\S+)\s+(?P<payload>\{.*\})\s*$")


@dataclass(frozen=True, slots=True)
class Comando:
    """Um comando MQTT publicado para um atuador (evento de domínio)."""

    caso: str
    ocorrido_em: datetime
    talhao: str
    acao_id: str
    topico: str
    payload: dict[str, Any]

    @property
    def assinatura(self) -> str:
        """Tópico + payload canônico: dois comandos iguais têm a mesma assinatura."""
        return f"{self.topico} {json.dumps(self.payload, sort_keys=True, ensure_ascii=False)}"


@dataclass(slots=True)
class Atividade:
    """Uma instância de atividade (forma intervalo)."""

    caso: str
    atividade: str
    etapa: str
    inicio: datetime
    fim: datetime
    status: str  # ok | erro
    tipo: str = "etapa"  # etapa | dominio
    mensagem: str = ""
    atributos: dict[str, float] = field(default_factory=dict)

    @property
    def duracao_s(self) -> float:
        return max(0.0, (self.fim - self.inicio).total_seconds())

    @property
    def falhou(self) -> bool:
        return self.status == "erro"


@dataclass(slots=True)
class Caso:
    id: str
    origem: str
    versao_processo: str
    tipo_caso: str
    inicio: datetime
    fim: datetime
    status: str  # ok | falhou | incompleto
    atividades: list[Atividade] = field(default_factory=list)
    puladas: list[str] = field(default_factory=list)
    comandos: list[Comando] = field(default_factory=list)

    @property
    def lead_time_s(self) -> float:
        return max(0.0, (self.fim - self.inicio).total_seconds())

    @property
    def variante(self) -> tuple[str, ...]:
        return tuple(a.atividade for a in self.atividades)

    @property
    def falhou(self) -> bool:
        return self.status != "ok"

    def etapas(self) -> list[Atividade]:
        return [a for a in self.atividades if a.tipo == "etapa"]


@dataclass(slots=True)
class EventLog:
    casos: list[Caso]
    total_eventos: int = 0
    eventos_sem_caso: int = 0
    eventos_fora_do_escopo: int = 0  # de casos de outra origem (ex.: CLI com apenas_worker)
    casos_fora_do_escopo: int = 0
    linhas_invalidas: int = 0

    def por_id(self) -> dict[str, Caso]:
        return {c.id: c for c in self.casos}


def _ts(valor: str) -> datetime:
    return datetime.fromisoformat(valor.replace("Z", "+00:00"))


def ler_eventos(caminho: Path) -> tuple[list[dict[str, Any]], int]:
    """Lê o JSONL; devolve (eventos válidos, linhas inválidas)."""
    eventos: list[dict[str, Any]] = []
    invalidas = 0
    if not caminho.exists():
        return eventos, invalidas
    with caminho.open(encoding="utf-8") as arquivo:
        for linha in arquivo:
            if not linha.strip():
                continue
            try:
                evento = json.loads(linha)
                _ts(evento["ocorrido_em"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                invalidas += 1
                continue
            eventos.append(evento)
    return eventos, invalidas


def _numericos(metricas: dict[str, Any] | None) -> dict[str, float]:
    saida: dict[str, float] = {}
    for chave, valor in (metricas or {}).items():
        if chave in ATRIBUTOS_NUMERICOS and isinstance(valor, int | float):
            if not isinstance(valor, bool):
                saida[chave] = float(valor)
    return saida


def _comando(evento: dict[str, Any], caso: str, quando: datetime) -> Comando | None:
    casamento = _RE_COMANDO.match(evento.get("mensagem") or "")
    if not casamento:
        return None
    try:
        payload = json.loads(casamento["payload"])
    except json.JSONDecodeError:
        payload = {"bruto": casamento["payload"]}
    topico = casamento["topico"]
    talhao = evento.get("fonte") or (topico.split("/")[2] if topico.count("/") >= 2 else "?")
    return Comando(caso, quando, talhao, casamento["id"], topico, payload)


def _ordem_etapa(nome: str) -> int:
    return ETAPAS.index(nome) if nome in ETAPAS else len(ETAPAS)


def _montar_caso(caso_id: str, eventos: list[tuple[int, dict[str, Any]]], dominio: bool) -> Caso:
    eventos = sorted(eventos, key=lambda par: (_ts(par[1]["ocorrido_em"]), par[0]))
    primeiro = eventos[0][1]
    inicio = fim = None
    status_run: str | None = None
    abertas: dict[str, datetime] = {}
    atividades: list[Atividade] = []
    puladas: list[str] = []
    comandos: list[Comando] = []

    for _, evento in eventos:
        quando = _ts(evento["ocorrido_em"])
        etapa, acao = evento.get("etapa"), evento.get("acao")
        metricas = evento.get("metricas") or {}
        if etapa == "run":
            if acao == "iniciada":
                inicio = inicio or quando
            elif acao in ("concluida", "falhou"):
                fim = quando
                status_run = "ok" if acao == "concluida" else "falhou"
        elif acao == "etapa_iniciada":
            abertas[etapa] = quando
        elif acao in ("etapa_concluida", "etapa_falhou"):
            falhou = acao == "etapa_falhou"
            comeco = abertas.pop(etapa, None)
            if comeco is None:  # sem o par de início: reconstrói pela duração relatada
                duracao = metricas.get("duracao_s")
                comeco = quando
                if isinstance(duracao, int | float):
                    comeco = datetime.fromtimestamp(quando.timestamp() - duracao, tz=quando.tzinfo)
            atividades.append(
                Atividade(
                    caso=caso_id,
                    atividade=f"{etapa}{SUFIXO_FALHA}" if falhou else etapa,
                    etapa=etapa,
                    inicio=comeco,
                    fim=quando,
                    status="erro" if falhou else "ok",
                    mensagem=(evento.get("mensagem") or "")[:300],
                    atributos=_numericos(metricas),
                )
            )
        elif acao == "etapa_pulada":
            puladas.append(etapa)
        elif etapa == "automacao" and acao == "comando_publicado":
            comando = _comando(evento, caso_id, quando)
            if comando:
                comandos.append(comando)

    ultimo = _ts(eventos[-1][1]["ocorrido_em"])
    inicio = inicio or _ts(primeiro["ocorrido_em"])
    fim = fim or ultimo
    for etapa, comeco in abertas.items():  # iniciada e nunca concluída: o caso abortou
        atividades.append(
            Atividade(
                caso_id,
                f"{etapa}{SUFIXO_FALHA}",
                etapa,
                comeco,
                fim,
                "erro",
                mensagem="etapa sem evento de conclusão (caso abortado)",
            )
        )
    if dominio and comandos:
        atividades.append(
            Atividade(
                caso_id,
                ATIVIDADE_COMANDO,
                "atuar",
                comandos[0].ocorrido_em,
                comandos[-1].ocorrido_em,
                "ok",
                tipo="dominio",
                atributos={"comandos": float(len(comandos))},
            )
        )
    atividades.sort(key=lambda a: (a.inicio, a.fim, _ordem_etapa(a.etapa), a.tipo))

    if status_run is None:
        status_run = "falhou" if any(a.falhou for a in atividades) else "incompleto"
    elif status_run == "ok" and any(a.falhou for a in atividades):
        status_run = "falhou"
    return Caso(
        id=caso_id,
        origem=str(primeiro.get("origem") or "cli"),
        versao_processo=str(primeiro.get("versao_processo") or "v1"),
        tipo_caso=str(primeiro.get("tipo_caso") or "run"),
        inicio=inicio,
        fim=max(fim, inicio),
        status=status_run,
        atividades=atividades,
        puladas=puladas,
        comandos=comandos,
    )


def extrair_event_log(
    eventos: Iterable[dict[str, Any]],
    *,
    apenas_worker: bool = True,
    incluir_dominio: bool = True,
    linhas_invalidas: int = 0,
) -> EventLog:
    """Agrupa os eventos por ``execucao_id`` e monta os casos (ordenados pelo início)."""
    grupos: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    total = sem_caso = 0
    for indice, evento in enumerate(eventos):
        total += 1
        caso = evento.get("execucao_id")
        if not caso:
            sem_caso += 1
            continue
        grupos.setdefault(str(caso), []).append((indice, evento))

    casos: list[Caso] = []
    fora_eventos = fora_casos = 0
    for caso_id, lista in grupos.items():
        caso = _montar_caso(caso_id, lista, incluir_dominio)
        if apenas_worker and caso.origem != "worker":
            fora_eventos += len(lista)
            fora_casos += 1
            continue
        casos.append(caso)
    casos.sort(key=lambda c: (c.inicio, c.id))
    return EventLog(casos, total, sem_caso, fora_eventos, fora_casos, linhas_invalidas)


def carregar_event_log(caminho: Path, **opcoes: Any) -> EventLog:
    eventos, invalidas = ler_eventos(caminho)
    return extrair_event_log(eventos, linhas_invalidas=invalidas, **opcoes)


# --------------------------------------------------------------------------- exportação


def _iso(instante: datetime) -> str:
    return instante.isoformat(timespec="milliseconds")


COLUNAS_LIFECYCLE: tuple[str, ...] = (
    "case:concept:name",
    "concept:name",
    "lifecycle:transition",
    "time:timestamp",
    "org:resource",
    "case:versao_processo",
    "case:tipo_caso",
    "case:status",
    "etapa",
    "status",
    "tipo_atividade",
    "duracao_s",
)


def linhas_lifecycle(log: EventLog, casos: Iterable[Caso] | None = None) -> list[dict[str, Any]]:
    """Uma linha por (caso, atividade, start|complete) — o formato padrão pm4py/XES."""
    linhas: list[dict[str, Any]] = []
    for caso in casos if casos is not None else log.casos:
        eventos: list[tuple[datetime, int, dict[str, Any]]] = []
        for ordem, ativ in enumerate(caso.atividades):
            for transicao, instante in (("start", ativ.inicio), ("complete", ativ.fim)):
                eventos.append(
                    (
                        instante,
                        ordem * 2 + (transicao == "complete"),
                        {
                            "case:concept:name": caso.id,
                            "concept:name": ativ.atividade,
                            "lifecycle:transition": transicao,
                            "time:timestamp": _iso(instante),
                            "org:resource": caso.origem,
                            "case:versao_processo": caso.versao_processo,
                            "case:tipo_caso": caso.tipo_caso,
                            "case:status": caso.status,
                            "etapa": ativ.etapa,
                            "status": ativ.status,
                            "tipo_atividade": ativ.tipo,
                            "duracao_s": round(ativ.duracao_s, 3)
                            if transicao == "complete"
                            else "",
                        },
                    )
                )
        eventos.sort(key=lambda t: (t[0], t[1]))
        linhas.extend(linha for _, _, linha in eventos)
    return linhas


def linhas_intervalo(log: EventLog, casos: Iterable[Caso] | None = None) -> list[dict[str, Any]]:
    """Uma linha por instância de atividade, com início, fim, duração e atributos."""
    linhas: list[dict[str, Any]] = []
    for caso in casos if casos is not None else log.casos:
        for ativ in caso.atividades:
            linha: dict[str, Any] = {
                "case_id": caso.id,
                "activity": ativ.atividade,
                "start_timestamp": _iso(ativ.inicio),
                "end_timestamp": _iso(ativ.fim),
                "duracao_s": round(ativ.duracao_s, 3),
                "status": ativ.status,
                "resource": caso.origem,
                "versao_processo": caso.versao_processo,
                "tipo_caso": caso.tipo_caso,
                "tipo_atividade": ativ.tipo,
            }
            for chave in ATRIBUTOS_NUMERICOS:
                valor = ativ.atributos.get(chave)
                linha[chave] = "" if valor is None else int(valor) if valor.is_integer() else valor
            linha["mensagem"] = ativ.mensagem if ativ.falhou else ""
            linhas.append(linha)
    return linhas


def escrever_csv(linhas: list[dict[str, Any]], destino: Path, colunas: Iterable[str]) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=list(colunas), lineterminator="\n")
        escritor.writeheader()
        escritor.writerows(linhas)
    return destino


def exportar_csv(log: EventLog, destino: Path, casos: Iterable[Caso] | None = None) -> Path:
    return escrever_csv(linhas_lifecycle(log, casos), destino, COLUNAS_LIFECYCLE)


def exportar_intervalos_csv(log: EventLog, destino: Path) -> Path:
    linhas = linhas_intervalo(log)
    colunas = list(linhas[0]) if linhas else ["case_id", "activity"]
    return escrever_csv(linhas, destino, colunas)


def exportar_xes_manual(log: EventLog, destino: Path) -> Path:
    """XES 1.0 (IEEE 1849) escrito à mão — o fallback quando pm4py não está instalado."""
    # Só *gera* XML a partir do nosso próprio event log; nunca faz parsing de entrada externa.
    import xml.etree.ElementTree as ET  # nosec B405 - escrita apenas, sem parsing

    raiz = ET.Element(
        "log",
        {
            "xes.version": "1.0",
            "xes.features": "nested-attributes",
            "openxes.version": "1.0RC7",
            "xmlns": "http://www.xes-standard.org/",
        },
    )
    for nome, prefixo in (
        ("Concept", "concept"),
        ("Time", "time"),
        ("Lifecycle", "lifecycle"),
        ("Organizational", "org"),
    ):
        ET.SubElement(
            raiz,
            "extension",
            {
                "name": nome,
                "prefix": prefixo,
                "uri": f"http://www.xes-standard.org/{prefixo}.xesext",
            },
        )
    g_trace = ET.SubElement(raiz, "global", {"scope": "trace"})
    ET.SubElement(g_trace, "string", {"key": "concept:name", "value": "__INVALID__"})
    g_evento = ET.SubElement(raiz, "global", {"scope": "event"})
    ET.SubElement(g_evento, "string", {"key": "concept:name", "value": "__INVALID__"})
    ET.SubElement(g_evento, "date", {"key": "time:timestamp", "value": "1970-01-01T00:00:00Z"})
    ET.SubElement(g_evento, "string", {"key": "lifecycle:transition", "value": "complete"})
    ET.SubElement(raiz, "classifier", {"name": "Activity", "keys": "concept:name"})
    ET.SubElement(
        raiz,
        "classifier",
        {"name": "Activity lifecycle", "keys": "concept:name lifecycle:transition"},
    )

    linhas = linhas_lifecycle(log)
    por_caso: dict[str, list[dict[str, Any]]] = {}
    for linha in linhas:
        por_caso.setdefault(linha["case:concept:name"], []).append(linha)
    for caso in log.casos:
        trace = ET.SubElement(raiz, "trace")
        ET.SubElement(trace, "string", {"key": "concept:name", "value": caso.id})
        ET.SubElement(trace, "string", {"key": "versao_processo", "value": caso.versao_processo})
        ET.SubElement(trace, "string", {"key": "tipo_caso", "value": caso.tipo_caso})
        ET.SubElement(trace, "string", {"key": "status", "value": caso.status})
        ET.SubElement(trace, "float", {"key": "lead_time_s", "value": f"{caso.lead_time_s:.3f}"})
        for linha in por_caso.get(caso.id, []):
            evento = ET.SubElement(trace, "event")
            ET.SubElement(evento, "string", {"key": "concept:name", "value": linha["concept:name"]})
            ET.SubElement(
                evento,
                "string",
                {"key": "lifecycle:transition", "value": linha["lifecycle:transition"]},
            )
            ET.SubElement(
                evento, "date", {"key": "time:timestamp", "value": linha["time:timestamp"]}
            )
            ET.SubElement(evento, "string", {"key": "org:resource", "value": linha["org:resource"]})
            ET.SubElement(evento, "string", {"key": "etapa", "value": linha["etapa"]})
            ET.SubElement(evento, "string", {"key": "status", "value": linha["status"]})
            if linha["duracao_s"] != "":
                ET.SubElement(
                    evento, "float", {"key": "duracao_s", "value": str(linha["duracao_s"])}
                )
    ET.indent(raiz)
    destino.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(raiz).write(destino, encoding="UTF-8", xml_declaration=True)
    return destino


def selecionar_exemplo(log: EventLog, quantidade: int = 3) -> list[Caso]:
    """Recorte pequeno para a entrega: casos com falha primeiro, depois um de cada versão."""
    escolhidos: list[Caso] = []
    com_falha = [c for c in log.casos if c.falhou]
    if com_falha:
        escolhidos.append(com_falha[0])
    for versao in sorted({c.versao_processo for c in log.casos}):
        candidato = next(
            (c for c in log.casos if c.versao_processo == versao and not c.falhou), None
        )
        if candidato and candidato not in escolhidos:
            escolhidos.append(candidato)
    for caso in log.casos:
        if len(escolhidos) >= quantidade:
            break
        if caso not in escolhidos:
            escolhidos.append(caso)
    return sorted(escolhidos[:quantidade], key=lambda c: (c.inicio, c.id))
