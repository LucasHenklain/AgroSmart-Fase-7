"""Camada de armazenamento do Data Lake.

Todo acesso ao lake passa por :class:`Armazenamento`. O código de ingestão e
processamento fala em termos de ``(camada, chave)`` e recebe bytes de volta —
não sabe, e não precisa saber, se atrás disso há um bucket S3 ou uma pasta.

Duas implementações concretas:

* :class:`ArmazenamentoMinio` — object store S3-compatível (MinIO em Docker,
  ou o próprio S3 trocando o endpoint). Caminho principal do projeto.
* :class:`ArmazenamentoFilesystem` — pastas locais com a mesma topologia
  lógica. Plano B para máquina sem Docker e backend padrão dos testes.

A regra de ouro das duas: **a chave é o mesmo texto nos dois backends**, no
formato ``fonte/ano=2026/mes=08/dia=28/arquivo.ext``. O que muda é só o
prefixo físico. Isso é o que faz o diagrama da Entrega 1 continuar verdadeiro
independentemente de onde o lake está rodando.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agrosmart.config import Backend, Camada, Config, carregar_config


class ErroArmazenamento(Exception):
    """Falha genérica de acesso ao lake."""


class ObjetoNaoEncontrado(ErroArmazenamento):
    """A chave pedida não existe na camada."""


@dataclass(frozen=True, slots=True)
class Objeto:
    """Descrição de um objeto armazenado — o que o catálogo precisa saber."""

    camada: Camada
    chave: str
    tamanho_bytes: int
    atualizado_em: datetime
    checksum_sha256: str | None = None
    metadados: dict[str, str] = field(default_factory=dict)


def checksum_sha256(dados: bytes) -> str:
    """Impressão digital usada para idempotência e auditoria."""
    return hashlib.sha256(dados).hexdigest()


def normalizar_chave(chave: str) -> str:
    """Garante chaves relativas, com ``/`` e sem truques de diretório."""
    limpa = chave.replace("\\", "/").strip().lstrip("/")
    if not limpa:
        raise ErroArmazenamento("Chave vazia não é permitida.")
    if ".." in limpa.split("/"):
        raise ErroArmazenamento(f"Chave inválida (contém '..'): {chave!r}")
    return limpa


def _normalizar_metadados(metadados: dict[str, Any] | None) -> dict[str, str]:
    """Metadados de usuário no S3 são texto ASCII com chave minúscula."""
    if not metadados:
        return {}
    return {str(k).lower().replace("_", "-"): str(v) for k, v in metadados.items()}


class Armazenamento(ABC):
    """Contrato que qualquer backend do lake precisa cumprir."""

    nome: str = "abstrato"

    def __init__(self, config: Config) -> None:
        self.config = config

    # --- operações primitivas (cada backend implementa) ---------------------

    @abstractmethod
    def preparar(self) -> None:
        """Cria buckets/pastas das camadas. Idempotente."""

    @abstractmethod
    def gravar_bytes(
        self,
        camada: Camada,
        chave: str,
        dados: bytes,
        *,
        metadados: dict[str, Any] | None = None,
        content_type: str | None = None,
    ) -> Objeto: ...

    @abstractmethod
    def ler_bytes(self, camada: Camada, chave: str) -> bytes: ...

    @abstractmethod
    def existe(self, camada: Camada, chave: str) -> bool: ...

    @abstractmethod
    def listar(self, camada: Camada, prefixo: str = "") -> Iterator[Objeto]: ...

    @abstractmethod
    def remover(self, camada: Camada, chave: str) -> None: ...

    @abstractmethod
    def uri(self, camada: Camada, chave: str) -> str:
        """Endereço legível do objeto (``s3://…`` ou ``file://…``)."""

    @abstractmethod
    def uri_camada(self, camada: Camada) -> str:
        """Endereço da raiz de uma camada — o bucket ou a pasta."""

    @abstractmethod
    def diagnostico(self) -> dict[str, Any]:
        """Estado do backend para o comando ``doctor``."""

    # --- conveniências construídas sobre as primitivas ----------------------

    def gravar_texto(
        self, camada: Camada, chave: str, texto: str, *, metadados: dict[str, Any] | None = None
    ) -> Objeto:
        return self.gravar_bytes(
            camada, chave, texto.encode("utf-8"), metadados=metadados, content_type="text/plain"
        )

    def gravar_json(
        self, camada: Camada, chave: str, conteudo: Any, *, metadados: dict[str, Any] | None = None
    ) -> Objeto:
        corpo = json.dumps(conteudo, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        return self.gravar_bytes(
            camada, chave, corpo, metadados=metadados, content_type="application/json"
        )

    def ler_texto(self, camada: Camada, chave: str) -> str:
        return self.ler_bytes(camada, chave).decode("utf-8")

    def ler_json(self, camada: Camada, chave: str) -> Any:
        return json.loads(self.ler_texto(camada, chave))

    def contar(self, camada: Camada, prefixo: str = "") -> int:
        return sum(1 for _ in self.listar(camada, prefixo))


# ---------------------------------------------------------------------------
# Backend 1: sistema de arquivos local
# ---------------------------------------------------------------------------


class ArmazenamentoFilesystem(Armazenamento):
    """Lake em pastas locais. Metadados ficam em um espelho ``_meta/``.

    Layout::

        datalake/
        ├── bronze/<chave>
        ├── silver/<chave>
        ├── gold/<chave>
        ├── quarentena/<chave>
        └── _meta/<camada>/<chave>.json     # metadados + checksum
    """

    nome = "filesystem"

    def _raiz(self, camada: Camada) -> Path:
        return self.config.caminho_local(camada)

    def _caminho(self, camada: Camada, chave: str) -> Path:
        return self._raiz(camada) / normalizar_chave(chave)

    def _caminho_meta(self, camada: Camada, chave: str) -> Path:
        return (
            self.config.dir_datalake / "_meta" / camada.value / (normalizar_chave(chave) + ".json")
        )

    def preparar(self) -> None:
        for camada in Camada:
            self._raiz(camada).mkdir(parents=True, exist_ok=True)
            (self.config.dir_datalake / "_meta" / camada.value).mkdir(parents=True, exist_ok=True)

    def gravar_bytes(
        self,
        camada: Camada,
        chave: str,
        dados: bytes,
        *,
        metadados: dict[str, Any] | None = None,
        content_type: str | None = None,
    ) -> Objeto:
        destino = self._caminho(camada, chave)
        destino.parent.mkdir(parents=True, exist_ok=True)

        # Escrita atômica: grava em temporário e renomeia. Um processo que morre
        # no meio nunca deixa um arquivo pela metade no lake.
        temporario = destino.with_suffix(destino.suffix + ".tmp")
        temporario.write_bytes(dados)
        temporario.replace(destino)

        digest = checksum_sha256(dados)
        meta = _normalizar_metadados(metadados)
        meta.setdefault("sha256", digest)
        if content_type:
            meta.setdefault("content-type", content_type)

        caminho_meta = self._caminho_meta(camada, chave)
        caminho_meta.parent.mkdir(parents=True, exist_ok=True)
        caminho_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        return Objeto(
            camada=camada,
            chave=normalizar_chave(chave),
            tamanho_bytes=len(dados),
            atualizado_em=datetime.now(UTC),
            checksum_sha256=digest,
            metadados=meta,
        )

    def ler_bytes(self, camada: Camada, chave: str) -> bytes:
        caminho = self._caminho(camada, chave)
        if not caminho.is_file():
            raise ObjetoNaoEncontrado(f"{self.uri(camada, chave)} não existe.")
        return caminho.read_bytes()

    def existe(self, camada: Camada, chave: str) -> bool:
        return self._caminho(camada, chave).is_file()

    def listar(self, camada: Camada, prefixo: str = "") -> Iterator[Objeto]:
        raiz = self._raiz(camada)
        if not raiz.exists():
            return
        prefixo_norm = normalizar_chave(prefixo) if prefixo else ""
        for caminho in sorted(raiz.rglob("*")):
            if not caminho.is_file() or caminho.suffix == ".tmp":
                continue
            chave = caminho.relative_to(raiz).as_posix()
            if prefixo_norm and not chave.startswith(prefixo_norm):
                continue
            stat = caminho.stat()
            meta: dict[str, str] = {}
            caminho_meta = self._caminho_meta(camada, chave)
            if caminho_meta.exists():
                meta = json.loads(caminho_meta.read_text(encoding="utf-8"))
            yield Objeto(
                camada=camada,
                chave=chave,
                tamanho_bytes=stat.st_size,
                atualizado_em=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                checksum_sha256=meta.get("sha256"),
                metadados=meta,
            )

    def contar(self, camada: Camada, prefixo: str = "") -> int:
        """Conta sem abrir os metadados — ``listar`` os lê, e isso pesa com milhares de objetos."""
        raiz = self._raiz(camada)
        if not raiz.exists():
            return 0
        prefixo_norm = normalizar_chave(prefixo) if prefixo else ""
        return sum(
            1
            for caminho in raiz.rglob("*")
            if caminho.is_file()
            and caminho.suffix != ".tmp"
            and caminho.relative_to(raiz).as_posix().startswith(prefixo_norm)
        )

    def remover(self, camada: Camada, chave: str) -> None:
        self._caminho(camada, chave).unlink(missing_ok=True)
        self._caminho_meta(camada, chave).unlink(missing_ok=True)

    def uri(self, camada: Camada, chave: str) -> str:
        return self._caminho(camada, chave).resolve().as_uri()

    def uri_camada(self, camada: Camada) -> str:
        return self._raiz(camada).resolve().as_uri()

    def diagnostico(self) -> dict[str, Any]:
        camadas = {}
        for camada in Camada:
            raiz = self._raiz(camada)
            camadas[camada.value] = {
                "existe": raiz.is_dir(),
                "local": str(raiz),
                "objetos": self.contar(camada) if raiz.is_dir() else 0,
            }
        return {"backend": self.nome, "raiz": str(self.config.dir_datalake), "camadas": camadas}


# ---------------------------------------------------------------------------
# Backend 2: MinIO / S3
# ---------------------------------------------------------------------------


class ArmazenamentoMinio(Armazenamento):
    """Lake em object store S3-compatível.

    O cliente usa assinatura v4 e endereçamento *path-style*, que é o que o
    MinIO espera em ``localhost``. Trocar ``AGROSMART_MINIO_ENDPOINT`` para o
    endpoint do S3 e as credenciais para um IAM user é tudo que separa esta
    classe de rodar em produção.
    """

    nome = "minio"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        import boto3
        from botocore.config import Config as BotoConfig

        self._cliente = boto3.client(
            "s3",
            endpoint_url=config.minio_endpoint,
            aws_access_key_id=config.minio_access_key,
            aws_secret_access_key=config.minio_secret_key,
            region_name=config.minio_region,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=3,
                read_timeout=15,
            ),
        )

    def _bucket(self, camada: Camada) -> str:
        return self.config.bucket(camada)

    def _bucket_existe(self, bucket: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._cliente.head_bucket(Bucket=bucket)
            return True
        except ClientError as erro:
            codigo = erro.response.get("Error", {}).get("Code", "")
            if codigo in {"404", "NoSuchBucket", "NotFound"}:
                return False
            raise ErroArmazenamento(f"Falha ao consultar bucket {bucket}: {erro}") from erro

    def preparar(self) -> None:
        for camada in Camada:
            bucket = self._bucket(camada)
            if not self._bucket_existe(bucket):
                self._cliente.create_bucket(Bucket=bucket)
        # Bronze e quarentena são imutáveis por contrato: versionamento é a
        # rede de segurança contra sobrescrita durante um backfill.
        for camada in (Camada.BRONZE, Camada.QUARENTENA):
            self._cliente.put_bucket_versioning(
                Bucket=self._bucket(camada), VersioningConfiguration={"Status": "Enabled"}
            )

    def gravar_bytes(
        self,
        camada: Camada,
        chave: str,
        dados: bytes,
        *,
        metadados: dict[str, Any] | None = None,
        content_type: str | None = None,
    ) -> Objeto:
        chave_norm = normalizar_chave(chave)
        digest = checksum_sha256(dados)
        meta = _normalizar_metadados(metadados)
        meta.setdefault("sha256", digest)
        tipo = content_type or mimetypes.guess_type(chave_norm)[0] or "application/octet-stream"

        self._cliente.put_object(
            Bucket=self._bucket(camada),
            Key=chave_norm,
            Body=dados,
            ContentType=tipo,
            Metadata=meta,
        )
        return Objeto(
            camada=camada,
            chave=chave_norm,
            tamanho_bytes=len(dados),
            atualizado_em=datetime.now(UTC),
            checksum_sha256=digest,
            metadados=meta,
        )

    def ler_bytes(self, camada: Camada, chave: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            resposta = self._cliente.get_object(
                Bucket=self._bucket(camada), Key=normalizar_chave(chave)
            )
        except ClientError as erro:
            codigo = erro.response.get("Error", {}).get("Code", "")
            if codigo in {"NoSuchKey", "404", "NotFound"}:
                raise ObjetoNaoEncontrado(f"{self.uri(camada, chave)} não existe.") from erro
            raise ErroArmazenamento(str(erro)) from erro
        return resposta["Body"].read()

    def existe(self, camada: Camada, chave: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._cliente.head_object(Bucket=self._bucket(camada), Key=normalizar_chave(chave))
            return True
        except ClientError as erro:
            codigo = erro.response.get("Error", {}).get("Code", "")
            if codigo in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise ErroArmazenamento(str(erro)) from erro

    def listar(self, camada: Camada, prefixo: str = "") -> Iterator[Objeto]:
        paginador = self._cliente.get_paginator("list_objects_v2")
        parametros: dict[str, Any] = {"Bucket": self._bucket(camada)}
        if prefixo:
            parametros["Prefix"] = normalizar_chave(prefixo)
        for pagina in paginador.paginate(**parametros):
            for item in pagina.get("Contents", []):
                yield Objeto(
                    camada=camada,
                    chave=item["Key"],
                    tamanho_bytes=int(item["Size"]),
                    atualizado_em=item["LastModified"],
                )

    def remover(self, camada: Camada, chave: str) -> None:
        self._cliente.delete_object(Bucket=self._bucket(camada), Key=normalizar_chave(chave))

    def uri(self, camada: Camada, chave: str) -> str:
        return f"s3://{self._bucket(camada)}/{normalizar_chave(chave)}"

    def uri_camada(self, camada: Camada) -> str:
        return f"s3://{self._bucket(camada)}"

    def diagnostico(self) -> dict[str, Any]:
        inicio = time.perf_counter()
        try:
            self._cliente.list_buckets()
            alcancavel = True
            erro = None
        except Exception as exc:  # noqa: BLE001 - diagnóstico quer capturar qualquer falha
            alcancavel = False
            erro = str(exc).splitlines()[0][:200]
        latencia_ms = round((time.perf_counter() - inicio) * 1000, 1)

        camadas: dict[str, Any] = {}
        if alcancavel:
            for camada in Camada:
                bucket = self._bucket(camada)
                existe = self._bucket_existe(bucket)
                camadas[camada.value] = {
                    "existe": existe,
                    "local": f"s3://{bucket}",
                    "objetos": self.contar(camada) if existe else 0,
                }
        return {
            "backend": self.nome,
            "endpoint": self.config.minio_endpoint,
            "alcancavel": alcancavel,
            "latencia_ms": latencia_ms,
            "erro": erro,
            "camadas": camadas,
        }


# ---------------------------------------------------------------------------
# Fábrica
# ---------------------------------------------------------------------------


def criar_armazenamento(config: Config | None = None) -> Armazenamento:
    """Devolve o backend configurado em ``AGROSMART_BACKEND``."""
    cfg = config or carregar_config()
    if cfg.backend is Backend.FILESYSTEM:
        return ArmazenamentoFilesystem(cfg)
    return ArmazenamentoMinio(cfg)
