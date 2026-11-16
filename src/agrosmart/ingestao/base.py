"""Protocolo comum dos coletores: landing → camada bruta.

Todo coletor faz a mesma sequência, na mesma ordem:

1. **descobrir lotes** na landing (um arquivo, ou arquivo + anexo);
2. **validar o schema na porta** contra o contrato — cabeçalho do CSV, chaves
   do JSON, envelope da API. Lote que não bate é *rejeitado*: vai para a
   quarentena com o motivo, nunca para o bronze;
3. **gravar sem transformar**: os bytes vão para o bronze exatamente como
   vieram, com metadados de ingestão no objeto;
4. **escrever o manifest** do lote em ``_manifests/``: contagem, checksum,
   versão, origem — é o que a reconciliação e a linhagem usam depois;
5. **registrar o evento** na trilha.

Idempotência: o ``batch_id`` é determinístico (fonte + janela + arquivo). Se o
manifest já existe com o mesmo checksum, o lote é pulado; se existe com
checksum diferente, a origem reenviou conteúdo novo — grava-se uma nova
versão (o bronze é versionado) e o manifest registra ``versao + 1``.
"""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from agrosmart.config import Camada, Config
from agrosmart.contratos import Contrato, ModoIngestao
from agrosmart.log import Evento, obter_logger, registrar_evento
from agrosmart.storage import Armazenamento, checksum_sha256


class ErroSchema(Exception):
    """O dado de origem não corresponde ao contrato — reprovado na porta."""


@dataclass(frozen=True, slots=True)
class Lote:
    """Uma unidade de ingestão: o arquivo principal e o que o acompanha."""

    fonte: str
    origem: Path
    janela_inicio: datetime
    janela_fim: datetime
    particao: date
    anexos: tuple[Path, ...] = ()

    @property
    def rotulo(self) -> str:
        return self.origem.name

    @property
    def batch_id(self) -> str:
        base = "|".join(
            (
                self.fonte,
                self.janela_inicio.isoformat(),
                self.janela_fim.isoformat(),
                self.origem.name,
            )
        )
        return hashlib.sha256(base.encode()).hexdigest()[:16]


@dataclass(slots=True)
class ResultadoLote:
    rotulo: str
    batch_id: str
    status: str  # gravado | atualizado | pulado | rejeitado
    registros: int = 0
    bytes: int = 0
    chave: str | None = None
    versao: int = 0
    motivo: str | None = None


@dataclass(slots=True)
class ResultadoIngestao:
    fonte: str
    modo: str
    lotes: list[ResultadoLote] = field(default_factory=list)
    duracao_s: float = 0.0

    def contar(self, status: str) -> int:
        return sum(1 for r in self.lotes if r.status == status)

    @property
    def registros(self) -> int:
        return sum(r.registros for r in self.lotes if r.status in {"gravado", "atualizado"})

    @property
    def bytes(self) -> int:
        return sum(r.bytes for r in self.lotes if r.status in {"gravado", "atualizado"})

    def para_dict(self) -> dict[str, Any]:
        return {
            "fonte": self.fonte,
            "modo": self.modo,
            "lotes": len(self.lotes),
            "gravados": self.contar("gravado"),
            "atualizados": self.contar("atualizado"),
            "pulados": self.contar("pulado"),
            "rejeitados": self.contar("rejeitado"),
            "registros": self.registros,
            "bytes": self.bytes,
            "duracao_s": round(self.duracao_s, 2),
            "rejeicoes": [
                {"lote": r.rotulo, "batch_id": r.batch_id, "motivo": r.motivo}
                for r in self.lotes
                if r.status == "rejeitado"
            ],
        }


class Coletor(ABC):
    """Base de todo coletor. Subclasses só dizem *onde* estão os lotes e *como* validá-los."""

    modo: ModoIngestao

    def __init__(self, contrato: Contrato, armazenamento: Armazenamento, config: Config) -> None:
        self.contrato = contrato
        self.armazenamento = armazenamento
        self.config = config
        self.logger = obter_logger(f"ingestao.{contrato.fonte}")

    # --- o que cada modo define ---------------------------------------------

    @abstractmethod
    def descobrir_lotes(self) -> list[Lote]:
        """Lotes disponíveis na landing, em ordem de janela."""

    @abstractmethod
    def validar_schema(self, lote: Lote) -> int:
        """Confere o lote contra o contrato e devolve o número de registros.

        Levanta :class:`ErroSchema` quando o dado não corresponde ao contrato.
        """

    # --- endereçamento no lake ---------------------------------------------

    @property
    def fonte(self) -> str:
        return self.contrato.fonte

    @property
    def schema_version(self) -> str:
        return self.contrato.nome_completo

    def _prefixo_particao(self, particao: date) -> str:
        return f"ano={particao:%Y}/mes={particao:%m}/dia={particao:%d}"

    def chave_bronze(self, lote: Lote, arquivo: Path) -> str:
        return f"{self.fonte}/{self._prefixo_particao(lote.particao)}/{arquivo.name}"

    def chave_manifest(self, lote: Lote) -> str:
        return (
            f"_manifests/{self.fonte}/{self._prefixo_particao(lote.particao)}/{lote.batch_id}.json"
        )

    def chave_rejeitado(self, lote: Lote, arquivo: Path) -> str:
        return f"_rejeitados/{self.fonte}/{lote.batch_id}/{arquivo.name}"

    # --- execução -----------------------------------------------------------

    def executar(self, *, forcar: bool = False) -> ResultadoIngestao:
        inicio = time.perf_counter()
        resultado = ResultadoIngestao(fonte=self.fonte, modo=self.modo.value)
        lotes = self.descobrir_lotes()
        self.logger.info("%d lote(s) descobertos em landing", len(lotes))

        def processar(lote: Lote) -> ResultadoLote:
            try:
                registros = self.validar_schema(lote)
            except ErroSchema as erro:
                return self._rejeitar(lote, str(erro))
            return self._gravar(lote, registros, forcar=forcar)

        # Milhares de arquivos pequenos (IoT) são limitados pela latência de cada
        # chamada ao object store, não por CPU. Lotes distintos nunca compartilham
        # chave nem manifest, então paralelizar é seguro; a ordem dos resultados
        # é preservada pelo ``map``.
        paralelismo = max(1, self.config.paralelismo)
        if paralelismo == 1 or len(lotes) < 4:
            resultado.lotes.extend(processar(lote) for lote in lotes)
        else:
            with ThreadPoolExecutor(
                max_workers=paralelismo, thread_name_prefix=f"ingestao-{self.fonte}"
            ) as executor:
                resultado.lotes.extend(executor.map(processar, lotes))

        resultado.duracao_s = time.perf_counter() - inicio
        self.logger.info(
            "%d gravados · %d atualizados · %d pulados · %d rejeitados · %d registros · %.1fs",
            resultado.contar("gravado"),
            resultado.contar("atualizado"),
            resultado.contar("pulado"),
            resultado.contar("rejeitado"),
            resultado.registros,
            resultado.duracao_s,
        )
        return resultado

    def _arquivos_do_lote(self, lote: Lote) -> list[tuple[Path, str]]:
        return [(lote.origem, "principal"), *((anexo, "anexo") for anexo in lote.anexos)]

    def _gravar(self, lote: Lote, registros: int, *, forcar: bool) -> ResultadoLote:
        batch_id = lote.batch_id
        chave_manifest = self.chave_manifest(lote)
        arquivos = [
            (caminho, papel, caminho.read_bytes())
            for caminho, papel in self._arquivos_do_lote(lote)
        ]
        checksums = [checksum_sha256(dados) for _, _, dados in arquivos]
        checksum_lote = hashlib.sha256("".join(checksums).encode()).hexdigest()

        anterior: dict[str, Any] | None = None
        if self.armazenamento.existe(Camada.BRONZE, chave_manifest):
            anterior = self.armazenamento.ler_json(Camada.BRONZE, chave_manifest)
            if anterior.get("checksum_sha256") == checksum_lote and not forcar:
                self._evento(
                    "lote_pulado",
                    lote,
                    batch_id,
                    registros,
                    0,
                    "ok",
                    "conteúdo idêntico já ingerido",
                )
                return ResultadoLote(
                    lote.rotulo,
                    batch_id,
                    "pulado",
                    registros,
                    0,
                    anterior.get("arquivos", [{}])[0].get("chave"),
                    int(anterior.get("versao", 1)),
                )

        versao = int(anterior["versao"]) + 1 if anterior else 1
        ingerido_em = datetime.now(UTC).isoformat(timespec="seconds")
        descritores: list[dict[str, Any]] = []
        total_bytes = 0

        for (caminho, papel, dados), digest in zip(arquivos, checksums, strict=True):
            chave = self.chave_bronze(lote, caminho)
            self.armazenamento.gravar_bytes(
                Camada.BRONZE,
                chave,
                dados,
                metadados={
                    "source": self.contrato.ingestao.origem,
                    "batch_id": batch_id,
                    "ingested_at": ingerido_em,
                    "sha256": digest,
                    "schema_version": self.schema_version,
                    "papel": papel,
                },
            )
            descritores.append(
                {
                    "chave": chave,
                    "nome": caminho.name,
                    "papel": papel,
                    "bytes": len(dados),
                    "checksum_sha256": digest,
                }
            )
            total_bytes += len(dados)

        manifest = {
            "batch_id": batch_id,
            "fonte": self.fonte,
            "schema_version": self.schema_version,
            "modo": self.modo.value,
            "coletor": type(self).__name__,
            "janela": {
                "inicio": lote.janela_inicio.isoformat(),
                "fim": lote.janela_fim.isoformat(),
            },
            "particao": lote.particao.isoformat(),
            "origem": self._caminho_relativo(lote.origem),
            "arquivos": descritores,
            "registros": registros,
            "bytes": total_bytes,
            "checksum_sha256": checksum_lote,
            "ingerido_em": ingerido_em,
            "versao": versao,
            "versao_anterior_checksum": anterior.get("checksum_sha256") if anterior else None,
        }
        self.armazenamento.gravar_json(Camada.BRONZE, chave_manifest, manifest)

        status = "atualizado" if anterior else "gravado"
        self._evento(
            f"lote_{status}", lote, batch_id, registros, total_bytes, "ok", f"versão {versao}"
        )
        return ResultadoLote(
            lote.rotulo, batch_id, status, registros, total_bytes, descritores[0]["chave"], versao
        )

    def _rejeitar(self, lote: Lote, motivo: str) -> ResultadoLote:
        batch_id = lote.batch_id
        total = 0
        for caminho, _papel in self._arquivos_do_lote(lote):
            if caminho.exists():
                dados = caminho.read_bytes()
                total += len(dados)
                self.armazenamento.gravar_bytes(
                    Camada.QUARENTENA,
                    self.chave_rejeitado(lote, caminho),
                    dados,
                    metadados={
                        "batch_id": batch_id,
                        "motivo": motivo[:200],
                        "schema_version": self.schema_version,
                    },
                )
        self.armazenamento.gravar_json(
            Camada.QUARENTENA,
            f"_rejeitados/{self.fonte}/{batch_id}/_motivo.json",
            {
                "batch_id": batch_id,
                "fonte": self.fonte,
                "schema_version": self.schema_version,
                "origem": self._caminho_relativo(lote.origem),
                "motivo": motivo,
                "rejeitado_em": datetime.now(UTC).isoformat(timespec="seconds"),
                "acao_sugerida": "conferir mudança de schema na origem; ver runbook INC-02",
            },
        )
        self.logger.warning("lote %s rejeitado na porta: %s", lote.rotulo, motivo)
        self._evento(
            "lote_rejeitado", lote, batch_id, 0, total, "erro", motivo, camada=Camada.QUARENTENA
        )
        return ResultadoLote(lote.rotulo, batch_id, "rejeitado", 0, total, None, 0, motivo)

    # --- utilidades ---------------------------------------------------------

    def _caminho_relativo(self, caminho: Path) -> str:
        try:
            return caminho.resolve().relative_to(self.config.dir_landing.resolve()).as_posix()
        except ValueError:
            return caminho.as_posix()

    def _evento(
        self,
        acao: str,
        lote: Lote,
        batch_id: str,
        registros: int,
        bytes_: int,
        status: str,
        mensagem: str,
        camada: Camada = Camada.BRONZE,
    ) -> None:
        registrar_evento(
            Evento(
                etapa="ingestao",
                acao=acao,
                fonte=self.fonte,
                camada=camada.value,
                batch_id=batch_id,
                status=status,
                mensagem=f"{lote.rotulo}: {mensagem}",
                metricas={
                    "registros": registros,
                    "bytes": bytes_,
                    "particao": lote.particao.isoformat(),
                },
            ),
            self.config,
            eco_console=False,
        )


def ler_json_arquivo(caminho: Path) -> Any:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except json.JSONDecodeError as erro:
        raise ErroSchema(
            f"{caminho.name}: JSON inválido — {erro.msg} (linha {erro.lineno})"
        ) from erro
