"""Estado da operação contínua e exposição em formato Prometheus.

O worker atualiza um :class:`EstadoOperacao` ao fim de cada ciclo; o servidor HTTP
lê o mesmo objeto (com trava) para responder ``/metrics``, ``/status`` e ``/health``.
O estado também é gravado em ``outputs/status/operacao.json`` — é o que o comando
``agrosmart status`` e o dashboard leem quando o endpoint não está acessível.

O formato texto do Prometheus é simples o bastante para ser escrito à mão
(https://prometheus.io/docs/instrumenting/exposition_formats/), o que evita uma
dependência a mais na imagem — menos superfície para o scanner de vulnerabilidades.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agrosmart import __version__

ARQUIVO_STATUS = Path("status") / "operacao.json"


def _rotulos(**rotulos: str) -> str:
    if not rotulos:
        return ""
    corpo = ",".join(
        f'{k}="{str(v).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
        for k, v in sorted(rotulos.items())
    )
    return "{" + corpo + "}"


@dataclass
class EstadoOperacao:
    """Tudo que o monitoramento sabe sobre o worker, atualizado a cada ciclo."""

    intervalo_s: float
    modo: str = "completo"  # completo | incremental
    versao_processo: str = "v1"
    publicar: bool = False
    iniciado_em: float = field(default_factory=time.time)
    fase: str = "iniciando"  # iniciando | carga_inicial | ciclo | aguardando | parado
    ciclos: Counter = field(default_factory=Counter)  # status -> n
    etapas: Counter = field(default_factory=Counter)  # (etapa, status) -> n
    duracao_etapa: dict[str, float] = field(default_factory=dict)  # última duração
    soma_duracao_ciclos: float = 0.0
    ultimo_ciclo: dict[str, Any] | None = None
    ultimo_sucesso_em: float | None = None
    lotes: Counter = field(default_factory=Counter)  # gravado|atualizado|pulado|rejeitado
    silver: dict[str, int] = field(default_factory=dict)
    acoes: dict[str, int] = field(default_factory=dict)
    comandos_publicados: int = 0
    landing: dict[str, float] = field(default_factory=dict)
    trava: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # --- atualização -------------------------------------------------------

    def registrar_ciclo(self, resumo: dict[str, Any], landing: dict[str, float]) -> None:
        with self.trava:
            status = "ok" if resumo.get("sucesso") else "erro"
            self.ciclos[status] += 1
            self.soma_duracao_ciclos += float(resumo.get("duracao_s") or 0.0)
            for etapa in resumo.get("etapas", []):
                self.etapas[(etapa["nome"], etapa["status"])] += 1
                if etapa["status"] != "pulada":
                    self.duracao_etapa[etapa["nome"]] = float(etapa["duracao_s"])
                r = etapa.get("resumo") or {}
                if etapa["nome"] == "ingerir" and etapa["status"] == "ok":
                    for chave_resumo, chave in (
                        ("gravados", "gravado"),
                        ("atualizados", "atualizado"),
                        ("pulados", "pulado"),
                        ("rejeitados", "rejeitado"),
                    ):
                        self.lotes[chave] += int(r.get(chave_resumo) or 0)
                elif etapa["nome"] == "silver" and etapa["status"] == "ok":
                    self.silver = {
                        "lidos": int(r.get("lidos") or 0),
                        "aprovados": int(r.get("aprovados") or 0),
                        "quarentena": int(r.get("quarentena") or 0),
                    }
                elif etapa["nome"] == "decidir" and etapa["status"] == "ok":
                    self.acoes = {
                        "talhoes": int(r.get("talhoes") or 0),
                        "acoes": int(r.get("acoes") or 0),
                        "comandos": int(r.get("comandos") or 0),
                    }
                elif etapa["nome"] == "atuar" and etapa["status"] == "ok":
                    self.comandos_publicados += int(r.get("publicadas") or 0)
            if status == "ok":
                self.ultimo_sucesso_em = time.time()
            self.ultimo_ciclo = {
                "execucao_id": resumo.get("execucao_id"),
                "sucesso": bool(resumo.get("sucesso")),
                "duracao_s": resumo.get("duracao_s"),
                "terminado_em": time.time(),
                "etapas": [
                    {
                        "nome": e["nome"],
                        "status": e["status"],
                        "duracao_s": e["duracao_s"],
                        "erro": e.get("erro"),
                    }
                    for e in resumo.get("etapas", [])
                ],
            }
            self.landing = dict(landing)

    def mudar_fase(self, fase: str) -> None:
        with self.trava:
            self.fase = fase

    # --- leitura ----------------------------------------------------------

    def saude(self, agora: float | None = None) -> tuple[bool, str]:
        """Saudável = último ciclo ok e recente. Antes do 1º ciclo, saudável (aquecendo)."""
        agora = agora or time.time()
        with self.trava:
            if self.ultimo_ciclo is None:
                return True, f"aquecendo ({self.fase})"
            if not self.ultimo_ciclo["sucesso"]:
                return False, f"último ciclo falhou ({self.ultimo_ciclo['execucao_id']})"
            idade = agora - (self.ultimo_sucesso_em or 0)
            limite = 3 * self.intervalo_s + 300
            if idade > limite:
                return False, f"sem ciclo bem-sucedido há {idade:.0f}s (limite {limite:.0f}s)"
            return True, "ok"

    def para_dict(self) -> dict[str, Any]:
        ok, motivo = self.saude()
        with self.trava:
            return {
                "versao": __version__,
                "modo": self.modo,
                "versao_processo": self.versao_processo,
                "publicar": self.publicar,
                "intervalo_s": self.intervalo_s,
                "iniciado_em": self.iniciado_em,
                "fase": self.fase,
                "saudavel": ok,
                "motivo": motivo,
                "ciclos": dict(self.ciclos),
                "duracao_media_ciclo_s": (
                    round(self.soma_duracao_ciclos / sum(self.ciclos.values()), 2)
                    if self.ciclos
                    else None
                ),
                "duracao_ultima_etapa_s": dict(self.duracao_etapa),
                "ultimo_ciclo": self.ultimo_ciclo,
                "ultimo_sucesso_em": self.ultimo_sucesso_em,
                "lotes": dict(self.lotes),
                "silver": dict(self.silver),
                "acoes": dict(self.acoes),
                "comandos_publicados": self.comandos_publicados,
                "landing": dict(self.landing),
            }

    def para_prometheus(self) -> str:
        ok, _ = self.saude()
        with self.trava:
            linhas: list[str] = []

            def metrica(nome: str, tipo: str, ajuda: str, amostras: list[tuple[dict, float]]):
                linhas.append(f"# HELP {nome} {ajuda}")
                linhas.append(f"# TYPE {nome} {tipo}")
                for rotulos, valor in amostras:
                    linhas.append(f"{nome}{_rotulos(**rotulos)} {valor:g}")

            metrica(
                "agrosmart_worker_info",
                "gauge",
                "Versão e modo do worker.",
                [
                    (
                        {
                            "versao": __version__,
                            "modo": self.modo,
                            "versao_processo": self.versao_processo,
                        },
                        1,
                    )
                ],
            )
            metrica(
                "agrosmart_worker_inicio_timestamp_seconds",
                "gauge",
                "Quando o worker subiu (epoch).",
                [({}, self.iniciado_em)],
            )
            metrica(
                "agrosmart_worker_saudavel",
                "gauge",
                "1 se o último ciclo foi bem-sucedido e é recente.",
                [({}, 1 if ok else 0)],
            )
            metrica(
                "agrosmart_ciclos_total",
                "counter",
                "Ciclos executados por status.",
                [({"status": s}, self.ciclos.get(s, 0)) for s in ("ok", "erro")],
            )
            metrica(
                "agrosmart_ciclo_duracao_segundos_soma",
                "counter",
                "Soma das durações dos ciclos (para média = soma/contagem).",
                [({}, round(self.soma_duracao_ciclos, 3))],
            )
            ultimo = self.ultimo_ciclo or {}
            metrica(
                "agrosmart_ultimo_ciclo_duracao_segundos",
                "gauge",
                "Duração do último ciclo.",
                [({}, float(ultimo.get("duracao_s") or 0))],
            )
            metrica(
                "agrosmart_ultimo_ciclo_sucesso",
                "gauge",
                "1 se o último ciclo terminou sem erro.",
                [({}, 1 if ultimo.get("sucesso") else 0)],
            )
            metrica(
                "agrosmart_ultimo_sucesso_timestamp_seconds",
                "gauge",
                "Fim do último ciclo bem-sucedido (epoch); 0 se nenhum.",
                [({}, self.ultimo_sucesso_em or 0)],
            )
            metrica(
                "agrosmart_etapa_duracao_segundos",
                "gauge",
                "Duração da última execução de cada etapa.",
                [({"etapa": e}, d) for e, d in sorted(self.duracao_etapa.items())],
            )
            metrica(
                "agrosmart_etapa_execucoes_total",
                "counter",
                "Execuções de etapa por status (ok, erro, pulada).",
                [({"etapa": e, "status": s}, n) for (e, s), n in sorted(self.etapas.items())],
            )
            metrica(
                "agrosmart_lotes_ingeridos_total",
                "counter",
                "Lotes vistos pela ingestão, por desfecho.",
                [({"status": s}, n) for s, n in sorted(self.lotes.items())],
            )
            metrica(
                "agrosmart_silver_registros",
                "gauge",
                "Registros do último silver por resultado.",
                [({"resultado": k}, v) for k, v in sorted(self.silver.items())],
            )
            metrica(
                "agrosmart_plano_acoes",
                "gauge",
                "Último plano de ações: talhões, ações e comandos MQTT.",
                [({"tipo": k}, v) for k, v in sorted(self.acoes.items())],
            )
            metrica(
                "agrosmart_comandos_publicados_total",
                "counter",
                "Comandos de irrigação confirmados pelo broker (QoS 1).",
                [({}, self.comandos_publicados)],
            )
            metrica(
                "agrosmart_landing_sensores",
                "gauge",
                "Landing de sensores: arquivos e idade do mais recente (s).",
                [({"medida": k}, v) for k, v in sorted(self.landing.items())],
            )
            return "\n".join(linhas) + "\n"


def gravar_status(estado: EstadoOperacao, dir_outputs: Path) -> Path:
    destino = dir_outputs / ARQUIVO_STATUS
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(".tmp")
    temporario.write_text(
        json.dumps(estado.para_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporario.replace(destino)
    return destino


def medir_landing(pasta_sensores: Path, agora: float | None = None) -> dict[str, float]:
    """Quantos arquivos de sensores existem e há quanto tempo o mais novo foi escrito.

    É o sinal de vida do gateway MQTT: se a idade cresce sem parar, ninguém está
    gravando a landing (broker caído, gateway parado ou dispositivos mudos).
    """
    agora = agora or time.time()
    if not pasta_sensores.is_dir():
        return {"arquivos": 0, "idade_ultima_s": -1}
    arquivos = [p for p in pasta_sensores.rglob("*.jsonl") if p.is_file()]
    if not arquivos:
        return {"arquivos": 0, "idade_ultima_s": -1}
    mais_novo = max(p.stat().st_mtime for p in arquivos)
    return {"arquivos": len(arquivos), "idade_ultima_s": round(agora - mais_novo, 1)}
