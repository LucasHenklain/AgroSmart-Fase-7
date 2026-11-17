"""Representação do processo sem Graphviz: DFG desenhado em camadas, em SVG (e PNG).

O layout é calculado uma vez (``montar_dfg``) como primitivas geométricas — caixas,
polilinhas e textos — e renderizado por dois back-ends: SVG escrito à mão (sempre) e
matplotlib (PNG, se instalado). As etapas ficam numa coluna, na ordem natural do pipeline;
as atividades ``(falha)`` numa coluna à direita, na altura da própria etapa; arestas que
saltam camadas correm em "trilhos" laterais, para não atravessar as caixas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from agrosmart.mineracao.eventlog import ATIVIDADE_COMANDO, ETAPAS, FIM, INICIO, SUFIXO_FALHA

LARGURA_NO, ALTURA_NO, PASSO = 210, 50, 88
COR_FALHA = "#c0392b"
COR_ARESTA = "#4a5568"
COR_TEXTO = "#1a202c"
ESCALA_FREQ = ("#eef4fb", "#1f5fa8")
ESCALA_DUR = ("#fdf1e3", "#b9471a")


def formatar_duracao(segundos: float | None) -> str:
    if segundos is None:
        return "—"
    if segundos < 1:
        texto = f"{segundos * 1000:.0f} ms"
    elif segundos < 60:
        texto = f"{segundos:.1f} s"
    elif segundos < 3600:
        texto = f"{segundos / 60:.1f} min"
    else:
        texto = f"{segundos / 3600:.1f} h"
    return texto.replace(".", ",")


def _hex(cor: str) -> tuple[int, int, int]:
    return int(cor[1:3], 16), int(cor[3:5], 16), int(cor[5:7], 16)


def _interpolar(escala: tuple[str, str], t: float) -> str:
    a, b = _hex(escala[0]), _hex(escala[1])
    t = min(1.0, max(0.0, t))
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b, strict=True))


def _texto_sobre(fundo: str) -> str:
    r, g, b = _hex(fundo)
    return "#ffffff" if 0.299 * r + 0.587 * g + 0.114 * b < 140 else COR_TEXTO


@dataclass(slots=True)
class Caixa:
    nome: str
    x: float  # centro
    y: float
    linhas: list[str]
    fundo: str
    borda: str = "#2d3748"
    largura_borda: float = 1.2
    pilula: bool = False


@dataclass(slots=True)
class Traco:
    pontos: list[tuple[float, float]]
    cor: str
    largura: float
    rotulo: str = ""
    rotulo_xy: tuple[float, float] = (0.0, 0.0)
    ancora: str = "start"


@dataclass(slots=True)
class Texto:
    x: float
    y: float
    texto: str
    tamanho: float = 12
    cor: str = COR_TEXTO
    ancora: str = "start"
    negrito: bool = False


@dataclass(slots=True)
class Desenho:
    largura: float
    altura: float
    caixas: list[Caixa] = field(default_factory=list)
    tracos: list[Traco] = field(default_factory=list)
    textos: list[Texto] = field(default_factory=list)
    amostras: list[tuple[float, float, float, float, str]] = field(default_factory=list)


def _base(atividade: str) -> str:
    return atividade.removesuffix(SUFIXO_FALHA)


def _ordem_principal(atividades: set[str]) -> list[str]:
    bases = {_base(a) for a in atividades}
    ordem = [e for e in ETAPAS if e in bases]
    if ATIVIDADE_COMANDO in bases:
        ordem.insert(
            ordem.index("atuar") + 1 if "atuar" in ordem else len(ordem), ATIVIDADE_COMANDO
        )
    ordem += sorted(bases - set(ordem))
    return [INICIO, *ordem, FIM]


def montar_dfg(
    dfg: dict[str, Any], desempenho: dict[str, Any], *, modo: str, titulo: str
) -> Desenho:
    """``modo`` = "frequencia" (nós e arestas por contagem) ou "desempenho" (duração/espera)."""
    nos: dict[str, int] = dfg["nos"]
    casos_no: dict[str, int] = dfg["casos_por_no"]
    arestas: dict[tuple[str, str], int] = dfg["_arestas"]
    duracoes: dict[str, dict[str, float]] = desempenho["atividades"]
    esperas: dict[tuple[str, str], dict[str, float]] = desempenho["_esperas"]

    camadas = {nome: i for i, nome in enumerate(_ordem_principal(set(nos)))}

    def posicao(nome: str) -> tuple[str, int]:
        coluna = "falha" if nome.endswith(SUFIXO_FALHA) else "principal"
        return coluna, camadas[_base(nome)]

    tem_falha = any(n.endswith(SUFIXO_FALHA) for n in nos)
    esquerda: list[tuple[str, str]] = []
    direita: list[tuple[str, str]] = []
    for a, b in sorted(arestas, key=lambda e: (camadas[_base(e[0])], camadas[_base(e[1])], e)):
        (ca, la), (cb, lb) = posicao(a), posicao(b)
        if ca == cb == "principal" and lb != la + 1:
            esquerda.append((a, b))
        elif ca == "falha" and cb == "principal":
            direita.append((a, b))
    esquerda.sort(key=lambda e: (abs(posicao(e[1])[1] - posicao(e[0])[1]), e))
    trilho_esq = {e: k for k, e in enumerate(esquerda)}
    trilho_dir = {e: k for k, e in enumerate(direita)}

    margem_esq = 70 + 18 * len(esquerda)
    x_principal = margem_esq + LARGURA_NO / 2
    x_falha = x_principal + LARGURA_NO + 110
    direita_max = (x_falha + LARGURA_NO / 2 + 18 * (len(direita) + 1)) if tem_falha else 0
    largura = max(x_principal + LARGURA_NO / 2 + 230, direita_max + 70, 620)
    topo = 70
    altura_fluxo = topo + PASSO * (len(camadas) - 1) + ALTURA_NO
    desenho = Desenho(largura=largura, altura=altura_fluxo + 120)

    def centro(nome: str) -> tuple[float, float]:
        coluna, camada = posicao(nome)
        return (
            x_falha if coluna == "falha" else x_principal
        ), topo + ALTURA_NO / 2 + camada * PASSO

    # ---- cores dos nós
    max_freq = max(nos.values(), default=1)
    medias = {n: duracoes.get(n, {}).get("media", 0.0) for n in nos}
    positivas = [math.log10(v + 1e-3) for v in medias.values() if v > 0]
    lo, hi = (min(positivas), max(positivas)) if positivas else (0.0, 1.0)

    def cor_no(nome: str) -> str:
        if modo == "frequencia":
            return _interpolar(ESCALA_FREQ, nos[nome] / max_freq)
        media = medias.get(nome, 0.0)
        if media <= 0 or hi == lo:
            return _interpolar(ESCALA_DUR, 0.5 if media > 0 else 0.0)
        return _interpolar(ESCALA_DUR, (math.log10(media + 1e-3) - lo) / (hi - lo))

    total_casos = sum(n for (a, _), n in arestas.items() if a == INICIO)
    for nome in [INICIO, *sorted(nos, key=lambda n: (camadas[_base(n)], n)), FIM]:
        x, y = centro(nome)
        if nome in (INICIO, FIM):
            desenho.caixas.append(
                Caixa(nome, x, y, [nome, f"{total_casos} casos"], "#e2e8f0", pilula=True)
            )
            continue
        if modo == "frequencia":
            detalhe = f"{nos[nome]}× · {casos_no.get(nome, 0)} casos"
        else:
            estat = duracoes.get(nome, {})
            detalhe = (
                f"Ø {formatar_duracao(estat.get('media'))} · "
                f"p95 {formatar_duracao(estat.get('p95'))}"
            )
        falha = nome.endswith(SUFIXO_FALHA)
        desenho.caixas.append(
            Caixa(
                nome,
                x,
                y,
                [nome, detalhe],
                cor_no(nome),
                borda=COR_FALHA if falha else "#2d3748",
                largura_borda=2.4 if falha else 1.2,
            )
        )

    # ---- arestas
    max_aresta = max(arestas.values(), default=1)
    saidas_esq: dict[str, int] = {}
    entradas_esq: dict[str, int] = {}
    meia_l, meia_a = LARGURA_NO / 2, ALTURA_NO / 2
    for (a, b), freq in sorted(arestas.items(), key=lambda e: (camadas[_base(e[0][0])], e[0])):
        (ca, la), (cb, lb) = posicao(a), posicao(b)
        (xa, ya), (xb, yb) = centro(a), centro(b)
        cor = COR_FALHA if (a.endswith(SUFIXO_FALHA) or b.endswith(SUFIXO_FALHA)) else COR_ARESTA
        largura_traco = 1.2 + 4.8 * freq / max_aresta
        if modo == "frequencia":
            rotulo = str(freq)
        else:
            rotulo = formatar_duracao(esperas.get((a, b), {}).get("media"))
        if (a, b) in trilho_esq:
            k = trilho_esq[(a, b)]
            trilho = x_principal - meia_l - 18 * (k + 1)
            ms, me = saidas_esq.get(a, 0), entradas_esq.get(b, 0)
            saidas_esq[a], entradas_esq[b] = ms + 1, me + 1
            y0 = ya + meia_a - 8 - 7 * ms if lb > la else ya - meia_a + 8 + 7 * ms
            y1 = yb - meia_a + 8 + 7 * me if lb > la else yb + meia_a - 8 - 7 * me
            pontos = [(xa - meia_l, y0), (trilho, y0), (trilho, y1), (xb - meia_l, y1)]
            desenho.tracos.append(
                Traco(pontos, cor, largura_traco, rotulo, (trilho - 5, (y0 + y1) / 2 + 4), "end")
            )
        elif (a, b) in trilho_dir:
            k = trilho_dir[(a, b)]
            trilho = x_falha + meia_l + 18 * (k + 1)
            y_destino = yb - meia_a + 10 + 8 * (k % 4)  # chegadas separadas no lado direito
            pontos = [
                (xa + meia_l, ya),
                (trilho, ya),
                (trilho, y_destino),
                (xb + meia_l, y_destino),
            ]
            desenho.tracos.append(
                Traco(pontos, cor, largura_traco, rotulo, (trilho + 5, (ya + y_destino) / 2 + 4))
            )
        elif cb == "falha" and la == lb:
            pontos = [(xa + meia_l, ya), (xb - meia_l, yb)]
            desenho.tracos.append(
                Traco(pontos, cor, largura_traco, rotulo, ((xa + xb) / 2, ya - 6), "middle")
            )
        elif cb == "falha" or ca == "falha":
            origem = (xa + meia_l, ya + 6) if ca == "principal" else (xa, ya + meia_a)
            destino = (xb, yb - meia_a) if lb > la else (xb, yb + meia_a)
            pontos = [origem, destino]
            meio = ((origem[0] + destino[0]) / 2 + 6, (origem[1] + destino[1]) / 2)
            desenho.tracos.append(Traco(pontos, cor, largura_traco, rotulo, meio))
        else:  # vizinhos na coluna principal
            pontos = [(xa, ya + meia_a), (xb, yb - meia_a)]
            desenho.tracos.append(
                Traco(pontos, cor, largura_traco, rotulo, (xa + 8, (ya + yb) / 2 + 4))
            )

    # ---- título e legenda
    desenho.textos.append(Texto(24, 34, titulo, 17, negrito=True))
    y_leg = altura_fluxo + 40
    escala = ESCALA_FREQ if modo == "frequencia" else ESCALA_DUR
    legenda_cor = (
        "cor do nó = frequência"
        if modo == "frequencia"
        else ("cor do nó = duração média (escala log)")
    )
    desenho.textos.append(Texto(24, y_leg, legenda_cor, 12))
    for i in range(5):
        desenho.amostras.append((24 + i * 26, y_leg + 8, 24, 14, _interpolar(escala, i / 4)))
    desenho.textos.append(Texto(24, y_leg + 40, "menor", 11, "#4a5568"))
    desenho.textos.append(Texto(150, y_leg + 40, "maior", 11, "#4a5568", "end"))
    rotulo_aresta = (
        "rótulo = nº de transições"
        if modo == "frequencia"
        else ("rótulo = espera média entre as atividades")
    )
    desenho.textos.append(Texto(300, y_leg, "espessura da aresta = frequência", 12))
    desenho.textos.append(Texto(300, y_leg + 20, rotulo_aresta, 12))
    desenho.textos.append(
        Texto(300, y_leg + 40, "vermelho = caminho de falha (etapa_falhou)", 12, COR_FALHA)
    )
    return desenho


# ------------------------------------------------------------------------------- SVG


def _num(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def svg(desenho: Desenho) -> str:
    partes = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(desenho.largura)}" '
        f'height="{_num(desenho.altura)}" viewBox="0 0 {_num(desenho.largura)} '
        f'{_num(desenho.altura)}" font-family="Segoe UI, Helvetica, Arial, sans-serif">',
        "<defs>",
    ]
    for nome, cor in (("seta", COR_ARESTA), ("seta-falha", COR_FALHA)):
        partes.append(
            f'<marker id="{nome}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
            f'fill="{cor}"/></marker>'
        )
    partes.append("</defs>")
    partes.append('<rect width="100%" height="100%" fill="#ffffff"/>')
    for traco in desenho.tracos:
        pontos = " ".join(f"{_num(x)},{_num(y)}" for x, y in traco.pontos)
        marcador = "seta-falha" if traco.cor == COR_FALHA else "seta"
        partes.append(
            f'<polyline points="{pontos}" fill="none" stroke="{traco.cor}" '
            f'stroke-width="{_num(traco.largura)}" stroke-linejoin="round" '
            f'stroke-opacity="0.85" marker-end="url(#{marcador})"/>'
        )
    for traco in desenho.tracos:
        if traco.rotulo:
            x, y = traco.rotulo_xy
            partes.append(
                f'<text x="{_num(x)}" y="{_num(y)}" font-size="11" fill="{traco.cor}" '
                f'text-anchor="{traco.ancora}" stroke="#ffffff" stroke-width="3" '
                f'paint-order="stroke">{escape(traco.rotulo)}</text>'
            )
    for caixa in desenho.caixas:
        x0, y0 = caixa.x - LARGURA_NO / 2, caixa.y - ALTURA_NO / 2
        raio = ALTURA_NO / 2 if caixa.pilula else 8
        partes.append(
            f'<rect x="{_num(x0)}" y="{_num(y0)}" width="{LARGURA_NO}" height="{ALTURA_NO}" '
            f'rx="{_num(raio)}" fill="{caixa.fundo}" stroke="{caixa.borda}" '
            f'stroke-width="{_num(caixa.largura_borda)}"/>'
        )
        cor = _texto_sobre(caixa.fundo)
        partes.append(
            f'<text x="{_num(caixa.x)}" y="{_num(caixa.y - 4)}" font-size="13" '
            f'font-weight="600" text-anchor="middle" fill="{cor}">{escape(caixa.linhas[0])}</text>'
        )
        if len(caixa.linhas) > 1:
            partes.append(
                f'<text x="{_num(caixa.x)}" y="{_num(caixa.y + 14)}" font-size="11" '
                f'text-anchor="middle" fill="{cor}">{escape(caixa.linhas[1])}</text>'
            )
    for x, y, w, h, cor in desenho.amostras:
        partes.append(
            f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(w)}" height="{_num(h)}" '
            f'fill="{cor}" stroke="#a0aec0" stroke-width="0.6"/>'
        )
    for t in desenho.textos:
        peso = ' font-weight="700"' if t.negrito else ""
        partes.append(
            f'<text x="{_num(t.x)}" y="{_num(t.y)}" font-size="{_num(t.tamanho)}"{peso} '
            f'fill="{t.cor}" text-anchor="{t.ancora}">{escape(t.texto)}</text>'
        )
    partes.append("</svg>")
    return "\n".join(partes) + "\n"


def salvar_svg(desenho: Desenho, destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(svg(desenho), encoding="utf-8")
    return destino


# ------------------------------------------------------------------------- matplotlib


def _pyplot() -> Any | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # noqa: BLE001 - matplotlib é opcional
        return None
    return plt


def salvar_png_dfg(desenho: Desenho, destino: Path) -> Path | None:
    """O mesmo desenho do SVG, renderizado por matplotlib (1 unidade = 1 px a 100 dpi)."""
    plt = _pyplot()
    if plt is None:
        return None
    from matplotlib.patches import FancyBboxPatch, Rectangle

    pt = 0.72  # px → pt a 100 dpi
    fig = plt.figure(figsize=(desenho.largura / 100, desenho.altura / 100))
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, desenho.largura)
    ax.set_ylim(desenho.altura, 0)
    ax.axis("off")
    for traco in desenho.tracos:
        if len(traco.pontos) > 2:  # trechos iniciais como linha; o último leva a seta
            xs, ys = zip(*traco.pontos[:-1], strict=True)
            ax.plot(
                xs, ys, color=traco.cor, lw=traco.largura * pt, alpha=0.85, solid_joinstyle="round"
            )
        ax.annotate(
            "",
            xy=traco.pontos[-1],
            xytext=traco.pontos[-2],
            arrowprops={
                "arrowstyle": "-|>,head_length=0.6,head_width=0.3",
                "color": traco.cor,
                "lw": traco.largura * pt,
                "alpha": 0.85,
                "shrinkA": 0,
                "shrinkB": 0,
            },
        )
        if traco.rotulo:
            ha = {"start": "left", "end": "right", "middle": "center"}[traco.ancora]
            ax.text(
                *traco.rotulo_xy,
                traco.rotulo,
                fontsize=11 * pt,
                color=traco.cor,
                ha=ha,
                va="baseline",
                bbox={"fc": "white", "ec": "none", "pad": 0.5, "alpha": 0.8},
            )
    for caixa in desenho.caixas:
        raio = ALTURA_NO / 2 if caixa.pilula else 8
        ax.add_patch(
            FancyBboxPatch(
                (caixa.x - LARGURA_NO / 2 + raio, caixa.y - ALTURA_NO / 2 + raio),
                LARGURA_NO - 2 * raio,
                ALTURA_NO - 2 * raio,
                boxstyle=f"round,pad={raio}",
                fc=caixa.fundo,
                ec=caixa.borda,
                lw=caixa.largura_borda * pt,
                zorder=3,
            )
        )
        cor = _texto_sobre(caixa.fundo)
        ax.text(
            caixa.x,
            caixa.y - 4,
            caixa.linhas[0],
            fontsize=13 * pt,
            weight="bold",
            ha="center",
            va="baseline",
            color=cor,
            zorder=4,
        )
        if len(caixa.linhas) > 1:
            ax.text(
                caixa.x,
                caixa.y + 14,
                caixa.linhas[1],
                fontsize=11 * pt,
                ha="center",
                va="baseline",
                color=cor,
                zorder=4,
            )
    for x, y, w, h, cor in desenho.amostras:
        ax.add_patch(Rectangle((x, y), w, h, fc=cor, ec="#a0aec0", lw=0.5))
    for t in desenho.textos:
        ha = {"start": "left", "end": "right", "middle": "center"}[t.ancora]
        ax.text(
            t.x,
            t.y,
            t.texto,
            fontsize=t.tamanho * pt,
            color=t.cor,
            ha=ha,
            va="baseline",
            weight="bold" if t.negrito else "normal",
        )
    destino.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destino, dpi=150, facecolor="white", metadata={"Software": None})
    plt.close(fig)
    return destino


CORES_VERSAO = {"v1": "#1f5fa8", "v2": "#2e9e6a"}


def grafico_duracoes(casos: list[Any], destino: Path) -> Path | None:
    plt = _pyplot()
    if plt is None:
        return None
    dados: dict[str, list[float]] = {}
    for caso in casos:
        for ativ in caso.etapas():
            dados.setdefault(ativ.atividade, []).append(ativ.duracao_s)
    if not dados:
        return None
    ordem = sorted(dados, key=lambda a: (ETAPAS.index(_base(a)) if _base(a) in ETAPAS else 99, a))
    fig, ax = plt.subplots(figsize=(9, 0.55 * len(ordem) + 1.6))
    series = [dados[a] for a in ordem]
    try:
        caixa = ax.boxplot(series, orientation="horizontal", patch_artist=True, widths=0.6)
    except TypeError:  # matplotlib < 3.10
        caixa = ax.boxplot(series, vert=False, patch_artist=True, widths=0.6)
    for patch, nome in zip(caixa["boxes"], ordem, strict=True):
        patch.set_facecolor("#f6c8bf" if nome.endswith(SUFIXO_FALHA) else "#bcd3ee")
        patch.set_edgecolor("#2d3748")
    ax.set_yticks(range(1, len(ordem) + 1), ordem)
    ax.invert_yaxis()
    ax.set_xlabel("duração da atividade (s)")
    ax.set_title("Duração por atividade (ciclos do worker)", loc="left", fontsize=12)
    ax.grid(axis="x", alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(destino, dpi=150, metadata={"Software": None})
    plt.close(fig)
    return destino


def grafico_lead_time(casos: list[Any], destino: Path) -> Path | None:
    plt = _pyplot()
    if plt is None or not casos:
        return None
    fig, ax = plt.subplots(figsize=(10, 4.2))
    cargas = [c for c in casos if c.tipo_caso == "carga_inicial"]
    if cargas:  # fora da linha: a carga inicial não é um ciclo comparável
        ax.scatter(
            [c.inicio for c in cargas],
            [c.lead_time_s for c in cargas],
            marker="D",
            s=40,
            color="#718096",
            zorder=4,
            label=f"carga inicial ({len(cargas)})",
        )
    ciclos = [c for c in casos if c.tipo_caso != "carga_inicial"]
    for versao in sorted({c.versao_processo for c in ciclos}):
        membros = [c for c in ciclos if c.versao_processo == versao]
        cor = CORES_VERSAO.get(versao, "#718096")
        ax.plot(
            [c.inicio for c in membros],
            [c.lead_time_s for c in membros],
            "-o",
            color=cor,
            ms=4,
            lw=1,
            alpha=0.85,
            label=f"{versao} ({len(membros)} casos)",
        )
    falhos = [c for c in casos if c.falhou]
    if falhos:
        ax.scatter(
            [c.inicio for c in falhos],
            [c.lead_time_s for c in falhos],
            marker="X",
            s=70,
            color=COR_FALHA,
            zorder=5,
            label=f"caso com falha ({len(falhos)})",
        )
    ax.set_ylabel("lead time do caso (s)")
    ax.set_title("Lead time por caso ao longo do tempo", loc="left", fontsize=12)
    ax.grid(alpha=0.3)
    ax.legend(frameon=False, fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(destino, dpi=150, metadata={"Software": None})
    plt.close(fig)
    return destino


def _abreviar(variante: list[str]) -> str:
    curtos = {"publicar comando": "cmd", "catalogo": "catál."}
    return " → ".join(
        (curtos.get(_base(a), _base(a)) + (" ✗" if a.endswith(SUFIXO_FALHA) else ""))
        for a in variante
    )


def grafico_variantes(lista: list[dict[str, Any]], destino: Path, limite: int = 10) -> Path | None:
    plt = _pyplot()
    if plt is None or not lista:
        return None
    topo = lista[:limite]
    fig, ax = plt.subplots(figsize=(11, 0.5 * len(topo) + 1.6))
    cores = ["#f2b1a8" if v["com_falha"] else "#a9c8ec" for v in topo]
    ax.barh(range(len(topo)), [v["casos"] for v in topo], color=cores, edgecolor="#2d3748", lw=0.5)
    ax.set_yticks(range(len(topo)), [f"{v['id']} · {v['pct_casos']:.0f}%" for v in topo])
    ax.invert_yaxis()
    for i, v in enumerate(topo):
        ax.text(
            0.01,
            i,
            _abreviar(v["atividades"]),
            va="center",
            fontsize=8,
            color="#1a202c",
            transform=ax.get_yaxis_transform(),
        )
    ax.set_xlabel("casos")
    ax.set_title("Variantes do processo (vermelho = contém falha)", loc="left", fontsize=12)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(destino, dpi=150, metadata={"Software": None})
    plt.close(fig)
    return destino
