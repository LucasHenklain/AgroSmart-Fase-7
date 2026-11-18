"""Empacotamento da entrega: o zip no padrão exigido, conferido antes de sair.

Regras da plataforma: um único ``.zip`` com documentos, código, prints e o
link do vídeo, nomeado ``nomecompleto_rm_turma_fase<N>_atividade.zip``. Errar o
nome ou mandar um zip corrompido no último minuto é a forma mais comum de
perder nota — então o nome é validado, o conteúdo é listado, o arquivo é
testado (``zipfile.testzip``) e o inventário fica em ``entrega/``.

O que entra: tudo que é artefato ou reproduz artefato, inclusive o vídeo se
estiver em ``entrega/``. O que fica fora: o ambiente virtual, o lake local, a
landing gerada e caches. Sem vídeo em ``entrega/`` o comando exige o link em
``entrega/LINK_VIDEO.txt`` (avisa, não bloqueia) e alerta se o zip passar de
``LIMITE_AVISO_ZIP_MB``.

Fase 6 (``fase=6``, o padrão): entram também ``firmware/`` (ESP32/Wokwi) e
``outputs/acoes/`` (ações do motor de decisão); ``docs/fase6/``,
``data/exemplos/fase6/`` e os prints ``fase6-*`` já estão cobertos pelas pastas
que sempre entraram. Vídeos em ``entrega/`` nomeados para outra fase
(``...fase5...``) ficam de fora do zip da Fase 6, e vídeos acima de
``LIMITE_VIDEO_MB`` ficam de fora com aviso — nesse caso vale o link.
"""

from __future__ import annotations

import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agrosmart.config import Config, carregar_config

FASE_PADRAO = 7
FASES_CONHECIDAS: frozenset[int] = frozenset({5, 6, 7})
INCLUIR: tuple[str, ...] = (
    "README.md",
    "pyproject.toml",
    "Makefile",
    ".env.example",
    ".gitignore",
    "contratos",
    "docs",
    "infra",
    "src",
    "tests",
    "scripts",
    "app",
    "data/exemplos",
    "outputs/prints",
    "outputs/relatorios",
    "entrega",
)
INCLUIR_POR_FASE: dict[int, tuple[str, ...]] = {
    5: (),
    6: ("firmware", "outputs/acoes"),
    # Fase 7: plataforma em containers, DevSecOps, monitoramento e mineração de processos.
    7: (
        "firmware",
        "outputs/acoes",
        "outputs/processos",
        "outputs/plataforma",
        "outputs/pipeline",
        ".github",
        "Dockerfile",
        ".dockerignore",
        "sonar-project.properties",
        ".trivyignore",
        ".pip-audit-ignore",
    ),
}
INCLUIR_ARQUIVOS_OUTPUTS: tuple[str, ...] = (
    "simulacao.json",
    "ingestao.json",
    "dq_report.json",
    "gold.json",
    "catalogo.json",
    "avaliacao_ia.json",
    "run.json",
)
EXCLUIR_PARTES: frozenset[str] = frozenset(
    {
        ".git",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "_work",
    }
)
EXCLUIR_SUFIXOS: frozenset[str] = frozenset({".pyc", ".pyo", ".zip", ".rar"})
SUFIXOS_VIDEO: frozenset[str] = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi"})
# Documentos que precisam existir e não ter trechos pendentes, por fase (globs em docs/).
DOCUMENTOS_POR_FASE: dict[int, tuple[str, ...]] = {
    5: (
        "01-arquitetura-datalake.md",
        "02-ingestao-e-manutencao.md",
        "03-ia-generativa.md",
        "04-modelo-de-negocio.md",
    ),
    6: (
        "fase6/01-*.md",
        "fase6/02-*.md",
        "fase6/03-*.md",
        "fase6/04-*.md",
        "fase6/05-*.md",
    ),
    7: tuple(f"fase7/0{n}-*.md" for n in range(1, 7)),
}
MARCA_PENDENTE = "_a preencher_"
LIMITE_AVISO_ZIP_MB = 100
LIMITE_VIDEO_MB = 100
_PADRAO_TOKEN = re.compile(r"^[A-Za-z0-9]+$")
_PADRAO_FASE_NO_NOME = re.compile(r"fase[\s_-]?(\d+)", re.IGNORECASE)


class ErroEmpacotamento(Exception):
    pass


def validar_fase(fase: int) -> int:
    if fase not in FASES_CONHECIDAS:
        raise ErroEmpacotamento(
            f"fase inválida: {fase!r} (conhecidas: {', '.join(map(str, sorted(FASES_CONHECIDAS)))})"
        )
    return fase


def normalizar_token(texto: str, *, minusculas: bool = True) -> str:
    """``Lucas Henrique da Silva`` → ``lucashenriquedasilva``: sem acento nem espaço.

    O nome vai em minúsculas (é o padrão da plataforma); RM e turma preservam a
    caixa em que foram informados (``4ESOA`` continua ``4ESOA``).
    """
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    limpo = re.sub(r"[^A-Za-z0-9]", "", sem_acento)
    return limpo.lower() if minusculas else limpo


def nome_do_zip(nome_completo: str, rm: str, turma: str, fase: int = FASE_PADRAO) -> str:
    partes = {
        "nome completo": normalizar_token(nome_completo),
        "RM": normalizar_token(rm, minusculas=False),
        "turma": normalizar_token(turma, minusculas=False),
    }
    for rotulo, valor in partes.items():
        if not valor or not _PADRAO_TOKEN.match(valor):
            raise ErroEmpacotamento(f"{rotulo} inválido após normalização: {valor!r}")
    fase = validar_fase(fase)
    return f"{partes['nome completo']}_{partes['RM']}_{partes['turma']}_fase{fase}_atividade.zip"


@dataclass(slots=True)
class ResultadoEmpacotamento:
    destino: Path
    arquivos: int
    bytes_originais: int
    bytes_zip: int
    inventario: list[str] = field(default_factory=list)
    integridade_ok: bool = False
    avisos: list[str] = field(default_factory=list)
    fase: int = FASE_PADRAO

    def para_dict(self) -> dict:
        return {
            "destino": str(self.destino),
            "fase": self.fase,
            "arquivos": self.arquivos,
            "bytes_originais": self.bytes_originais,
            "bytes_zip": self.bytes_zip,
            "integridade_ok": self.integridade_ok,
            "avisos": self.avisos,
        }


def _excluido(caminho: Path) -> bool:
    if any(parte in EXCLUIR_PARTES for parte in caminho.parts):
        return True
    if caminho.suffix.lower() in SUFIXOS_VIDEO:
        # O vídeo da entrega entra só quando está em entrega/; vídeo solto em outra
        # pasta (gravação de teste, tela capturada) fica fora. Qual vídeo de entrega/
        # entra é decidido por ``videos_da_entrega`` (fase e tamanho).
        return caminho.parts[0] != "entrega"
    return caminho.suffix.lower() in EXCLUIR_SUFIXOS


def _video_de_outra_fase(nome: str, fase: int) -> bool:
    """``apresentacao-fase5.mp4`` é de outra fase quando empacotamos a 6; sem número, entra."""
    fases = {int(n) for n in _PADRAO_FASE_NO_NOME.findall(nome)}
    return bool(fases) and fase not in fases


def _todos_os_videos(raiz: Path) -> list[Path]:
    pasta = raiz / "entrega"
    if not pasta.is_dir():
        return []
    return sorted(p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() in SUFIXOS_VIDEO)


def videos_da_entrega(raiz: Path, fase: int = FASE_PADRAO) -> list[Path]:
    """Vídeos de ``entrega/`` que entram no zip desta fase (nem de outra fase, nem grandes)."""
    limite = LIMITE_VIDEO_MB * 1024 * 1024
    return [
        p
        for p in _todos_os_videos(raiz)
        if not _video_de_outra_fase(p.name, fase) and p.stat().st_size <= limite
    ]


def videos_grandes(raiz: Path, fase: int = FASE_PADRAO) -> list[Path]:
    """Vídeos desta fase que ficam fora por tamanho — entram só pelo link."""
    limite = LIMITE_VIDEO_MB * 1024 * 1024
    return [
        p
        for p in _todos_os_videos(raiz)
        if not _video_de_outra_fase(p.name, fase) and p.stat().st_size > limite
    ]


def selecionar_arquivos(raiz: Path, fase: int = FASE_PADRAO) -> list[Path]:
    """Arquivos que entram no zip, relativos à raiz, em ordem estável."""
    fase = validar_fase(fase)
    videos_ok = set(videos_da_entrega(raiz, fase))
    escolhidos: set[Path] = set()
    for item in (*INCLUIR, *INCLUIR_POR_FASE.get(fase, ())):
        alvo = raiz / item
        if alvo.is_file():
            escolhidos.add(alvo)
        elif alvo.is_dir():
            for caminho in alvo.rglob("*"):
                if not caminho.is_file() or _excluido(caminho.relative_to(raiz)):
                    continue
                if caminho.suffix.lower() in SUFIXOS_VIDEO and caminho not in videos_ok:
                    continue
                escolhidos.add(caminho)
    for nome in INCLUIR_ARQUIVOS_OUTPUTS:
        alvo = raiz / "outputs" / nome
        if alvo.is_file():
            escolhidos.add(alvo)
    return sorted(escolhidos)


def verificar_pre_requisitos(raiz: Path, fase: int = FASE_PADRAO) -> list[str]:
    """O que ainda falta para a entrega estar completa — avisos, não erros."""
    fase = validar_fase(fase)
    avisos: list[str] = []
    link = raiz / "entrega" / "LINK_VIDEO.txt"
    texto_link = link.read_text(encoding="utf-8").strip() if link.exists() else ""
    tem_link = bool(texto_link) and "preencher" not in texto_link.lower()
    if not videos_da_entrega(raiz, fase) and not tem_link:
        avisos.append(
            f"vídeo ausente: nenhum vídeo da fase {fase} em entrega/ e entrega/LINK_VIDEO.txt "
            "vazio, inexistente ou ainda com o placeholder"
        )
    for grande in videos_grandes(raiz, fase):
        avisos.append(
            f"entrega/{grande.name} tem {grande.stat().st_size / (1024 * 1024):.0f} MB "
            f"(> {LIMITE_VIDEO_MB} MB) e ficou fora do zip — deixe o link em LINK_VIDEO.txt"
        )
    for padrao in DOCUMENTOS_POR_FASE[fase]:
        encontrados = sorted((raiz / "docs").glob(padrao))
        if not encontrados:
            avisos.append(f"docs/{padrao} ausente")
            continue
        for caminho in encontrados:
            if MARCA_PENDENTE in caminho.read_text(encoding="utf-8"):
                relativo = caminho.relative_to(raiz).as_posix()
                avisos.append(f"{relativo} ainda tem trechos '{MARCA_PENDENTE}'")
    prints = list((raiz / "outputs" / "prints").glob("*.png"))
    if not prints:
        avisos.append(
            "nenhuma captura de tela (.png) em outputs/prints — faltam os prints do MinIO"
        )
    if not list((raiz / "outputs" / "relatorios").rglob("*.md")):
        avisos.append("outputs/relatorios vazio — rodar `agrosmart ia gerar`")
    if fase == 6:
        if not list((raiz / "outputs" / "prints").glob("fase6-*.png")):
            avisos.append(
                "nenhum print fase6-*.png em outputs/prints — rodar "
                "`python scripts/capturar_dashboard.py --prefixo fase6-05-dashboard`"
            )
        if not list((raiz / "outputs" / "acoes").glob("acoes_*.json")):
            avisos.append("outputs/acoes sem acoes_<data>.json — rodar `agrosmart automacao`")
        if not (raiz / "firmware").is_dir():
            avisos.append("firmware/ ausente — o código do ESP32 (Wokwi) precisa estar no zip")
    if fase == 7:
        exigidos = {
            "outputs/prints/fase7-*.png": "prints da Fase 7 (python scripts/capturar_operacao.py)",
            "outputs/processos/event_log.csv": "event log (agrosmart processos)",
            "outputs/processos/event_log.xes": "event log XES (agrosmart processos)",
            "outputs/pipeline/ultimo/resumo.json": "evidência do pipeline (pipeline_local.py)",
            "entrega/pitch/slides-pitch-agrosmart.pdf": "slides do pitch",
            ".github/workflows/*.yml": "workflow do GitHub Actions",
            "Dockerfile": "Dockerfile",
        }
        for padrao, descricao in exigidos.items():
            if not list(raiz.glob(padrao)):
                avisos.append(f"{padrao} ausente — {descricao}")
        if not any("pitch" in p.name.lower() for p in videos_da_entrega(raiz, fase)) and (
            "pitch" not in texto_link.lower()
        ):
            avisos.append(
                "vídeo pitch ausente: nenhum entrega/*pitch*fase7*.mp4 e sem link 'pitch' em "
                "entrega/LINK_VIDEO.txt"
            )
    return avisos


def empacotar(
    nome_completo: str,
    rm: str,
    turma: str,
    *,
    fase: int = FASE_PADRAO,
    config: Config | None = None,
    pasta_saida: Path | None = None,
    simular: bool = False,
) -> ResultadoEmpacotamento:
    fase = validar_fase(fase)
    cfg = config or carregar_config()
    raiz = cfg.raiz
    destino = (pasta_saida or (raiz / "entrega")) / nome_do_zip(nome_completo, rm, turma, fase)
    arquivos = selecionar_arquivos(raiz, fase)
    if not arquivos:
        raise ErroEmpacotamento(f"nada a empacotar em {raiz}")
    avisos = verificar_pre_requisitos(raiz, fase)
    bytes_originais = sum(a.stat().st_size for a in arquivos)
    inventario = [a.relative_to(raiz).as_posix() for a in arquivos]

    if simular:
        return ResultadoEmpacotamento(
            destino, len(arquivos), bytes_originais, 0, inventario, False, avisos, fase
        )

    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for arquivo in arquivos:
            zf.write(arquivo, arcname=arquivo.relative_to(raiz).as_posix())
        zf.writestr(
            "ENTREGA.json",
            json.dumps(
                {
                    "atividade": f"AgroSmart Fase {fase}",
                    "fase": fase,
                    "gerado_em": datetime.now(UTC).isoformat(timespec="seconds"),
                    "arquivos": len(arquivos),
                    "avisos": avisos,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    with zipfile.ZipFile(destino) as zf:
        corrompido = zf.testzip()
        listados = len(zf.namelist())
    integridade_ok = corrompido is None and listados == len(arquivos) + 1
    if not integridade_ok:
        avisos.append(f"integridade falhou: corrompido={corrompido!r} listados={listados}")
    tamanho_mb = destino.stat().st_size / (1024 * 1024)
    if tamanho_mb > LIMITE_AVISO_ZIP_MB:
        avisos.append(
            f"zip com {tamanho_mb:.0f} MB — confirmar o limite de upload da plataforma; "
            "se estourar, tirar o vídeo de entrega/ e deixar só o link"
        )

    gerado_em = datetime.now(UTC).isoformat(timespec="seconds")
    cabecalho = [f"# {destino.name}", f"# {len(arquivos)} arquivos · gerado em {gerado_em}"]
    (destino.parent / "CONTEUDO_DO_ZIP.txt").write_text(
        "\n".join([*cabecalho, *inventario, ""]), encoding="utf-8"
    )
    return ResultadoEmpacotamento(
        destino,
        len(arquivos),
        bytes_originais,
        destino.stat().st_size,
        inventario,
        integridade_ok,
        avisos,
        fase,
    )
