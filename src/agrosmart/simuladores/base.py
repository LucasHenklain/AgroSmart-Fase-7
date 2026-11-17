"""Infraestrutura comum dos simuladores: contexto, aleatoriedade e registro.

Determinismo é requisito, não conveniência. Cada fonte tira seus números de um
*fluxo* próprio de aleatoriedade derivado da semente global — assim mudar o
simulador de imagens não altera um único byte das leituras de sensores, e o
avaliador consegue refazer qualquer print do zero.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from agrosmart.config import Config
from agrosmart.simuladores.catalogo import (
    FAZENDAS,
    Fazenda,
    Sensor,
    Talhao,
    sensores_para,
    talhoes_ativos,
)


def semente_fluxo(seed: int, fluxo: str) -> int:
    """Semente estável e independente por fluxo (``"clima"``, ``"sensores"``…)."""
    digest = hashlib.sha256(f"{seed}:{fluxo}".encode()).hexdigest()
    return int(digest[:16], 16)


def iso_utc(instante: datetime) -> str:
    """``2026-08-28T09:15:00Z`` — o formato que os contratos esperam."""
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=UTC)
    return instante.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def arredondar(valor: float, casas: int = 2) -> float:
    """Arredondamento explícito para saída estável entre execuções."""
    return round(valor + 0.0, casas)


@dataclass(slots=True)
class DefeitoInjetado:
    """Um registro deliberadamente defeituoso, com a regra que deve capturá-lo."""

    tipo: str
    fonte: str
    regra: str
    chave: dict[str, Any]
    arquivo: str
    descricao: str


@dataclass(slots=True)
class RegistroDefeitos:
    """Verdade-terreno dos defeitos injetados — o teste do Passo 3 confere contra ela."""

    itens: list[DefeitoInjetado] = field(default_factory=list)

    def adicionar(self, defeito: DefeitoInjetado) -> None:
        self.itens.append(defeito)

    def por_tipo(self) -> dict[str, int]:
        contagem: dict[str, int] = {}
        for item in self.itens:
            contagem[item.tipo] = contagem.get(item.tipo, 0) + 1
        return dict(sorted(contagem.items()))

    def por_regra(self) -> dict[str, int]:
        contagem: dict[str, int] = {}
        for item in self.itens:
            contagem[item.regra] = contagem.get(item.regra, 0) + 1
        return dict(sorted(contagem.items()))

    def para_lista(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self.itens]


@dataclass(slots=True)
class ContagemFonte:
    arquivos: int = 0
    registros: int = 0
    bytes: int = 0


class Contexto:
    """Tudo que um simulador precisa saber sobre a execução corrente."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.inicio, self.fim = config.janela_historico
        self.dias: list[date] = [
            self.inicio + timedelta(days=i) for i in range((self.fim - self.inicio).days + 1)
        ]
        self.talhoes: tuple[Talhao, ...] = talhoes_ativos(config.qtd_talhoes)
        self.fazendas: tuple[Fazenda, ...] = tuple(
            f for f in FAZENDAS if any(t.fazenda_id == f.id for t in self.talhoes)
        )
        self.sensores: tuple[Sensor, ...] = sensores_para(self.talhoes)
        self.registro = RegistroDefeitos()
        self.contagens: dict[str, ContagemFonte] = {}
        self.metricas_extras: dict[str, Any] = {}
        self._rngs: dict[str, random.Random] = {}

    # --- aleatoriedade ------------------------------------------------------

    def rng(self, fluxo: str) -> random.Random:
        """Gerador dedicado a um fluxo. Mesmo nome → mesma sequência, sempre."""
        if fluxo not in self._rngs:
            # nosec B311: dados sintéticos reprodutíveis por semente, nunca uso criptográfico
            self._rngs[fluxo] = random.Random(semente_fluxo(self.config.seed, fluxo))  # nosec B311
        return self._rngs[fluxo]

    # --- consultas ----------------------------------------------------------

    def fazenda_de(self, talhao: Talhao) -> Fazenda:
        for fazenda in FAZENDAS:
            if fazenda.id == talhao.fazenda_id:
                return fazenda
        raise KeyError(talhao.fazenda_id)

    def talhao(self, talhao_id: str) -> Talhao:
        for talhao in self.talhoes:
            if talhao.id == talhao_id:
                return talhao
        raise KeyError(talhao_id)

    @property
    def instante_referencia(self) -> datetime:
        return self.config.instante_referencia

    # --- landing ------------------------------------------------------------

    def landing(self, subpasta: str) -> Path:
        pasta = self.config.dir_landing_fonte(subpasta)
        pasta.mkdir(parents=True, exist_ok=True)
        return pasta

    def limpar_landing(self, subpasta: str, padrao: str = "*") -> int:
        """Remove o que uma execução anterior deixou. Devolve quantos itens saíram."""
        pasta = self.config.dir_landing_fonte(subpasta)
        if not pasta.exists():
            return 0
        removidos = 0
        for item in sorted(pasta.glob(padrao)):
            if item.name == ".gitkeep":
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            removidos += 1
        return removidos

    # --- escrita ------------------------------------------------------------

    def gravar_texto(self, fonte: str, caminho: Path, texto: str, registros: int) -> int:
        dados = texto.encode("utf-8")
        return self.gravar_bytes(fonte, caminho, dados, registros)

    def gravar_bytes(self, fonte: str, caminho: Path, dados: bytes, registros: int) -> int:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(dados)
        contagem = self.contagens.setdefault(fonte, ContagemFonte())
        contagem.arquivos += 1
        contagem.registros += registros
        contagem.bytes += len(dados)
        return len(dados)

    def gravar_json(self, fonte: str, caminho: Path, conteudo: Any, registros: int) -> int:
        texto = json.dumps(conteudo, ensure_ascii=False, indent=2)
        return self.gravar_texto(fonte, caminho, texto, registros)

    # --- resumo -------------------------------------------------------------

    def resumo(self) -> dict[str, Any]:
        return {
            "seed": self.config.seed,
            "janela": {"inicio": self.inicio.isoformat(), "fim": self.fim.isoformat()},
            "dias": len(self.dias),
            "talhoes": [t.id for t in self.talhoes],
            "sensores": [s.id for s in self.sensores],
            "fontes": {fonte: asdict(c) for fonte, c in sorted(self.contagens.items())},
            "defeitos_por_tipo": self.registro.por_tipo(),
            "defeitos_por_regra": self.registro.por_regra(),
            "defeitos": self.registro.para_lista(),
        }
