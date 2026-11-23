"""Fechamento da entrega da Fase 7 em um comando.

    python scripts/fechar_entrega.py --video-final <url> --pitch <url>   # preenche e fecha
    python scripts/fechar_entrega.py                                     # só regenera e confere
    python scripts/fechar_entrega.py ... --commit                        # + commit local

O que faz, na ordem:

1. valida os links (YouTube: ``youtube.com/watch?v=``, ``youtu.be/`` ou ``youtube.com/shorts/``);
2. grava os links em ``entrega/LINK_VIDEO.txt`` e no README (entre os marcadores
   ``<!-- youtube:inicio -->`` e ``<!-- youtube:fim -->``) — idempotente: rodar de novo com
   outro link substitui o anterior;
3. regenera o ZIP com ``agrosmart empacotar --fase 7`` duas vezes: a primeira grava a saída
   em ``outputs/prints/fase7-empacotar.txt`` e a segunda leva essa evidência para dentro do zip;
4. confere o ZIP: integridade, ``ENTREGA.json`` sem avisos, os dois vídeos presentes e dentro
   do limite de tempo, links (ou ausência deles) refletidos lá dentro e **todo arquivo do zip
   idêntico (sha256) ao do disco**;
5. com ``--commit``, faz o commit local dos arquivos alterados. O ``git push`` fica impresso
   para quem tiver a credencial do repositório.

Código de saída 0 só se todas as conferências passarem.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
NOME, RM, TURMA, FASE = "Lucas Moraes Henklain da Silva", "99350", "4ESOA", 7
ZIP = RAIZ / "entrega" / f"lucasmoraeshenklaindasilva_{RM}_{TURMA}_fase{FASE}_atividade.zip"
LINK_VIDEO = RAIZ / "entrega" / "LINK_VIDEO.txt"
README = RAIZ / "README.md"
PRINT_EMPACOTAR = RAIZ / "outputs" / "prints" / "fase7-empacotar.txt"
VIDEOS = {  # arquivo → (mínimo, máximo) em segundos, da rubrica
    "entrega/apresentacao-agrosmart-fase7-4ESOA.mp4": (300, 480),
    "entrega/pitch-agrosmart-fase7-4ESOA.mp4": (0, 300),
}
MARCA_INI, MARCA_FIM = "<!-- youtube:inicio -->", "<!-- youtube:fim -->"
TEXTO_SEM_LINK = (
    "Vídeos: `entrega/apresentacao-agrosmart-fase7-4ESOA.mp4` (final, 7:10) e "
    "`entrega/pitch-agrosmart-fase7-4ESOA.mp4` (pitch, 4:44), dentro do ZIP da entrega."
)
PADRAO_YOUTUBE = re.compile(
    r"^https://(www\.|m\.)?(youtube\.com/(watch\?v=|shorts/)|youtu\.be/)[A-Za-z0-9_-]{6,}([?&#]\S*)?$"
)
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# --- texto (funções puras, testadas em tests/test_fechar_entrega.py) ---


def ler(caminho: Path) -> str:
    """Lê sem traduzir quebras de linha (o repositório usa LF; ``read_text`` no Windows não)."""
    return caminho.read_bytes().decode("utf-8")


def gravar(caminho: Path, texto: str) -> None:
    caminho.write_bytes(texto.encode("utf-8"))


def validar_link(url: str) -> str:
    url = url.strip()
    if not PADRAO_YOUTUBE.match(url):
        raise ValueError(f"link não parece do YouTube: {url!r}")
    return url


def preencher_link_video(texto: str, final: str, pitch: str) -> str:
    """Troca a linha do vídeo final e a do pitch (placeholder ou link antigo)."""
    # [^\r\n]* preserva a quebra de linha original (LF ou CRLF)
    novo, n1 = re.subn(r"(?m)^(- vídeo final:[ \t]*)[^\r\n]*", lambda m: m[1] + final, texto)
    novo, n2 = re.subn(r"(?m)^(- pitch:[ \t]*)[^\r\n]*", lambda m: m[1] + pitch, novo)
    if n1 != 1 or n2 != 1:
        raise ValueError("LINK_VIDEO.txt sem as linhas '- vídeo final:' e '- pitch:'")
    return novo


def preencher_readme(texto: str, final: str | None, pitch: str | None) -> str:
    if MARCA_INI not in texto or MARCA_FIM not in texto:
        raise ValueError("README sem os marcadores <!-- youtube:inicio/fim -->")
    if final and pitch:
        corpo = (
            f"Vídeos no YouTube (não listado): [vídeo final, 7:10]({final}) · "
            f"[pitch, 4:44]({pitch}). Os arquivos também estão no ZIP, em `entrega/`."
        )
    else:
        corpo = TEXTO_SEM_LINK
    antes, resto = texto.split(MARCA_INI, 1)
    _, depois = resto.split(MARCA_FIM, 1)
    return f"{antes}{MARCA_INI}\n{corpo}\n{MARCA_FIM}{depois}"


# --- execução ---


def _passo(msg: str) -> None:
    print(f"\n== {msg}")


def _empacotar() -> str:
    env = {**os.environ, "NO_COLOR": "1", "COLUMNS": "200", "PYTHONIOENCODING": "utf-8"}
    cmd = [
        sys.executable,
        "-m",
        "agrosmart",
        "empacotar",
        "--nome",
        NOME,
        "--rm",
        RM,
        "--turma",
        TURMA,
        "--fase",
        str(FASE),
    ]
    r = subprocess.run(cmd, cwd=RAIZ, env=env, capture_output=True, text=True, encoding="utf-8")
    saida = ANSI.sub("", r.stdout + r.stderr)
    if r.returncode != 0:
        print(saida)
        raise SystemExit(f"empacotar falhou (código {r.returncode})")
    return saida


def _duracao(caminho_no_zip: str) -> float | None:
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    r = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-i", str(RAIZ / caminho_no_zip)],
        capture_output=True,
        text=True,
        errors="replace",
    )
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None


def conferir_zip(final: str | None, pitch: str | None) -> list[str]:
    falhas: list[str] = []
    with zipfile.ZipFile(ZIP) as zf:
        if zf.testzip() is not None:
            falhas.append("zip corrompido")
        nomes = [n for n in zf.namelist() if not n.endswith("/")]
        meta = json.loads(zf.read("ENTREGA.json"))
        if meta.get("avisos"):
            falhas.append(f"ENTREGA.json com avisos: {meta['avisos']}")
        diferentes = [
            n
            for n in nomes
            if n != "ENTREGA.json"
            and (
                not (RAIZ / n).is_file()
                or hashlib.sha256(zf.read(n)).digest()
                != hashlib.sha256((RAIZ / n).read_bytes()).digest()
            )
        ]
        if diferentes:
            falhas.append(
                f"{len(diferentes)} arquivo(s) do zip diferentes do disco: {diferentes[:5]}"
            )
        link_zip = zf.read("entrega/LINK_VIDEO.txt").decode("utf-8")
        readme_zip = zf.read("README.md").decode("utf-8")
    for video, (minimo, maximo) in VIDEOS.items():
        if video not in nomes:
            falhas.append(f"vídeo fora do zip: {video}")
            continue
        dur = _duracao(video)
        if dur is not None and not minimo <= dur <= maximo:
            falhas.append(f"{video}: {dur:.0f}s fora do limite {minimo}–{maximo}s")
        print(f"   {video}: {'?' if dur is None else f'{int(dur // 60)}:{dur % 60:04.1f}'}")
    if final and pitch:
        for url in (final, pitch):
            if url not in link_zip or url not in readme_zip:
                falhas.append(f"link não chegou ao LINK_VIDEO/README do zip: {url}")
        if "PREENCHER" in link_zip.upper():
            falhas.append("LINK_VIDEO.txt do zip ainda tem placeholder")
    print(
        f"   {len(nomes)} arquivos · {ZIP.stat().st_size / 2**20:.1f} MB · "
        f"gerado em {meta.get('gerado_em')}"
    )
    return falhas


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--video-final", help="link do YouTube do vídeo final (5–8 min)")
    ap.add_argument("--pitch", help="link do YouTube do vídeo pitch (até 5 min)")
    ap.add_argument("--commit", action="store_true", help="faz o commit local ao final")
    args = ap.parse_args(argv)

    if bool(args.video_final) != bool(args.pitch):
        ap.error("informe os dois links (--video-final e --pitch) ou nenhum")
    final = validar_link(args.video_final) if args.video_final else None
    pitch = validar_link(args.pitch) if args.pitch else None

    _passo("links")
    if final and pitch:
        gravar(LINK_VIDEO, preencher_link_video(ler(LINK_VIDEO), final, pitch))
        gravar(README, preencher_readme(ler(README), final, pitch))
        print(f"   vídeo final: {final}\n   pitch:       {pitch}")
    else:
        # Sem links não mexe em nada: LINK_VIDEO.txt e README ficam como estão (com ou sem links).
        print("   sem links: LINK_VIDEO.txt e README ficam como estão")

    # A saída do empacotar é evidência e vai dentro do próprio zip: reempacota até a cópia de
    # dentro ser igual à saída da execução que gerou o zip (em geral 2 passadas; no máximo 4).
    _passo("empacotar")
    gravar(PRINT_EMPACOTAR, _empacotar())
    for passada in range(2, 5):
        saida = _empacotar()
        with zipfile.ZipFile(ZIP) as zf:
            dentro = zf.read("outputs/prints/fase7-empacotar.txt").decode("utf-8")
        gravar(PRINT_EMPACOTAR, saida)
        if dentro == saida:
            break
        print(f"   passada {passada}: evidência mudou, reempacotando")
    print("   " + saida.strip().replace("\n", "\n   "))

    _passo("conferência do zip")
    falhas = conferir_zip(final, pitch)
    for f in falhas:
        print(f"   ✘ {f}")
    if falhas:
        return 1
    print("   ✔ zip íntegro, sem avisos, vídeos no limite, idêntico ao disco")

    alterados = [
        "entrega/LINK_VIDEO.txt",
        "README.md",
        "entrega/CONTEUDO_DO_ZIP.txt",
        "outputs/prints/fase7-empacotar.txt",
    ]
    if args.commit:
        _passo("commit local")
        subprocess.run(["git", "add", *alterados], cwd=RAIZ, check=True)
        msg = (
            "Fechamento da entrega: " + ("links do YouTube e " if final else "") + "ZIP regenerado"
        )
        r = subprocess.run(["git", "commit", "-m", msg], cwd=RAIZ)
        if r.returncode not in (0, 1):  # 1 = nada a commitar
            return r.returncode
    print("\nPróximo passo (quem tem a credencial do repositório): git push origin main")
    print(f"Arquivo para enviar no portal: {ZIP.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
