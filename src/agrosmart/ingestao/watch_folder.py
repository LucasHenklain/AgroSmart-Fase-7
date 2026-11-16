"""Coletor **watch_folder**: binário + sidecar (imagens e laudos).

O sidecar JSON é o arquivo principal do lote — é o que tem schema — e o
binário (JPEG, PDF) é anexo. Os dois vão para a mesma partição do bronze e
o manifest aponta para ambos. Binário ausente ou vazio é rejeição na porta;
binário truncado passa e será pego no silver pela regra de tamanho, porque o
coletor não abre o arquivo para saber se está inteiro — não é papel dele.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agrosmart.contratos import ModoIngestao
from agrosmart.ingestao.base import Coletor, ErroSchema, Lote, ler_json_arquivo


def _instante(valor: object, nome: str) -> datetime:
    if not isinstance(valor, str):
        raise ErroSchema(f"{nome}: campo de instante não é texto ({valor!r})")
    try:
        texto = valor.replace("Z", "+00:00")
        instante = datetime.fromisoformat(texto)
    except ValueError as erro:
        raise ErroSchema(f"{nome}: instante inválido {valor!r}") from erro
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=UTC)
    return instante


class ColetorPastaObservada(Coletor):
    modo = ModoIngestao.WATCH_FOLDER

    #: fonte -> (subpasta, extensão do binário, campo do sidecar que dá a partição)
    LOCALIZACAO: dict[str, tuple[str, str, str]] = {
        "imagens_ndvi": ("imagens", ".jpg", "capturada_em"),
        "laudos_solo": ("laudos", ".pdf", "coletado_em"),
    }

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        try:
            self.subpasta, self.extensao, self.campo_instante = self.LOCALIZACAO[self.fonte]
        except KeyError as erro:
            raise ValueError(
                f"ColetorPastaObservada não sabe onde a fonte {self.fonte!r} aterrissa"
            ) from erro

    def descobrir_lotes(self) -> list[Lote]:
        pasta = self.config.dir_landing_fonte(self.subpasta)
        lotes: list[Lote] = []
        for sidecar in sorted(pasta.glob("*.json")):
            binario = sidecar.with_suffix(self.extensao)
            instante = self._instante_do_sidecar(sidecar)
            anexos = (binario,) if binario.exists() else ()
            lotes.append(Lote(self.fonte, sidecar, instante, instante, instante.date(), anexos))
        return lotes

    def _instante_do_sidecar(self, sidecar: Path) -> datetime:
        try:
            conteudo = ler_json_arquivo(sidecar)
        except ErroSchema:
            # Sidecar ilegível ainda vira lote — para ser rejeitado com motivo, não ignorado.
            return datetime.fromtimestamp(sidecar.stat().st_mtime, tz=UTC)
        if isinstance(conteudo, dict) and self.campo_instante in conteudo:
            try:
                return _instante(conteudo[self.campo_instante], sidecar.name)
            except ErroSchema:
                pass
        return datetime.fromtimestamp(sidecar.stat().st_mtime, tz=UTC)

    def validar_schema(self, lote: Lote) -> int:
        conteudo = ler_json_arquivo(lote.origem)
        if not isinstance(conteudo, dict):
            raise ErroSchema(f"{lote.rotulo}: sidecar não é um objeto JSON")

        permitidas = set(self.contrato.campos_de_origem)
        desconhecidas = sorted(set(conteudo) - permitidas)
        if desconhecidas:
            raise ErroSchema(
                f"{lote.rotulo}: chaves fora do contrato {self.schema_version}: {desconhecidas}"
            )
        faltando = [c for c in self.contrato.obrigatorios_de_origem if c not in conteudo]
        if faltando:
            raise ErroSchema(f"{lote.rotulo}: campos obrigatórios ausentes no sidecar: {faltando}")
        _instante(conteudo.get(self.campo_instante), lote.rotulo)

        if not lote.anexos:
            raise ErroSchema(
                f"{lote.rotulo}: binário {lote.origem.stem}{self.extensao} não encontrado"
            )
        binario = lote.anexos[0]
        if binario.stat().st_size == 0:
            raise ErroSchema(f"{lote.rotulo}: binário {binario.name} está vazio")
        return 1
