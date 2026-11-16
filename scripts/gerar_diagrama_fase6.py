"""Gera o diagrama da arquitetura integrada da Fase 6 (SVG e, opcionalmente, PNG).

    python scripts/gerar_diagrama_fase6.py            # docs/diagramas/arquitetura-fase6.svg
    python scripts/gerar_diagrama_fase6.py --png      # ... e arquitetura-fase6.png (2×)

O diagrama da Fase 5 (``arquitetura-datalake.svg``) foi escrito à mão; este é
gerado por código para que a mesma folha de estilo (cores, fontes, chips) seja
reutilizada e o desenho possa ser ajustado sem editar coordenadas uma a uma.
O PNG usa o Playwright com o Chromium instalado ou, na falta dele, o Microsoft
Edge da máquina (``channel="msedge"``) — o mesmo caminho de ``exportar_pdf.py``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DESTINO_SVG = RAIZ / "docs" / "diagramas" / "arquitetura-fase6.svg"

LARGURA, ALTURA = 1052, 712
# Cinco colunas de 180 px com 24 px entre elas; o canal da esquerda (x < 40)
# recebe a seta de retorno do comando MQTT ao atuador.
COLUNA_X = (40, 244, 448, 652, 856)
COLUNA_W = 180
TOPO, ALT_COLUNA = 24, 276
CHIP_W, CHIP_H, CHIP_PASSO = 152, 28, 36

_SERIF = 'Georgia, "Times New Roman", serif'
_MONO = 'Consolas, "Cascadia Mono", monospace'
_SANS = '"Segoe UI", Helvetica, Arial, sans-serif'
ESTILO = "\n".join(
    [
        "    .fundo { fill: #FBFCFA; }",
        "    .box { fill: #E9ECE6; stroke: #B9C1B5; }",
        "    .band { fill: #DDEAE1; stroke: #2C6A4C; stroke-opacity: .45; }",
        "    .auto { fill: #E4E8F3; stroke: #2F4A8A; stroke-opacity: .5; }",
        "    .quar { fill: #F6E3DE; stroke: #9E3320; stroke-opacity: .5; }",
        "    .chip { fill: #FBFCFA; stroke: #D5DAD1; }",
        "    .chipnovo { fill: #FBFCFA; stroke: #2F4A8A; stroke-opacity: .55; }",
        f"    .titulo {{ font-family: {_SERIF}; font-size: 14px; font-weight: 600;",
        "              fill: #191E1A; }",
        f"    .sub {{ font-family: {_MONO}; font-size: 8.6px; letter-spacing: .09em;",
        "           fill: #616B62; }",
        f"    .chiptxt {{ font-family: {_SANS}; font-size: 10.5px; fill: #3D463F; }}",
        f"    .stage {{ font-family: {_MONO}; font-size: 8.6px; letter-spacing: .14em;",
        "             fill: #616B62; }",
        f"    .nota {{ font-family: {_MONO}; font-size: 8.2px; letter-spacing: .06em;",
        "            fill: #2F4A8A; }",
        "    .arrow { stroke: #B9C1B5; stroke-width: 1.4; fill: none; }",
        "    .retorno { stroke: #2F4A8A; stroke-width: 1.4; fill: none;",
        "               stroke-dasharray: 5 4; stroke-opacity: .8; }",
        "    .head { fill: #B9C1B5; }",
        "    .headretorno { fill: #2F4A8A; fill-opacity: .8; }",
        "    .r-campo { fill: #2C6A4C; } .r-coleta { fill: #2F4A8A; }",
        "    .r-bronze { fill: #8A5A2B; } .r-prata { fill: #6F7C77; }",
        "    .r-ouro { fill: #9C7A1E; } .r-quar { fill: #9E3320; }",
    ]
)

# (rótulo de estágio, título, subtítulo, cor da faixa, chips (texto, novo na Fase 6))
COLUNAS = (
    (
        "CAMPO",
        "Dispositivos e origens",
        "ESP32 (WOKWI) + 5 ORIGENS",
        "r-campo",
        (
            ("ESP32 · solo, ar, bateria", True),
            ("Atuador de irrigação (relé)", True),
            ("Firmware · firmware/", True),
            ("Clima · API REST", False),
            ("ERP · CSV · Drone · JPEG", False),
            ("Laudos de solo · PDF", False),
        ),
    ),
    (
        "TRANSPORTE E COLETA",
        "Broker e coletores",
        "MQTT + 4 MODOS · IDEMPOTENTES",
        "r-coleta",
        (
            ("Broker MQTT (Mosquitto)", True),
            ("agrosmart/sensores/+/leitura", True),
            ("coletar-mqtt → landing/", True),
            ("Emulador de frota (dispositivo)", True),
            ("Stream · API · batch · pasta", False),
            ("Schema na porta · manifest", False),
        ),
    ),
    (
        "CAMADA BRUTA",
        "bronze",
        "FORMATO ORIGINAL · MINIO",
        "r-bronze",
        (
            ("Imutável, append-only", False),
            ("Bytes iguais à origem", False),
            ("fonte/ano=/mes=/dia=", False),
            ("Manifest por lote · sha256", False),
            ("Versionamento de objeto", False),
            ("Retenção por contrato", False),
        ),
    ),
    (
        "CAMADA CONFIÁVEL",
        "silver",
        "PARQUET · VALIDADO",
        "r-prata",
        (
            ("Tipado pelo contrato (TIP-01)", False),
            ("58 regras · crítica / alerta", False),
            ("Dedup evento / versão", False),
            ("Chaves conformadas · UTC", False),
            ("Reprovado → quarentena", False),
            ("PII pseudonimizada", False),
        ),
    ),
    (
        "CAMADA REFINADA",
        "gold",
        "MODELO DIMENSIONAL · DUCKDB",
        "r-ouro",
        (
            ("dim_talhao · dim_sensor", False),
            ("fato_balanco_hidrico", False),
            ("fato_leitura_diaria_talhao", False),
            ("fato_ndvi · fato_custo", False),
            ("fato_alerta_qualidade", False),
            ("benchmark_regional", False),
        ),
    ),
)

Y_QUAR, ALT_QUAR = 328, 60
Y_AUTO, ALT_AUTO = 412, 72
Y_CONSUMO, ALT_CONSUMO = 504, 72
Y_GOV, ALT_GOV = 596, 92

CHIPS_AUTO = (
    (224, 148, "Motor de decisão (regras)", True),
    (382, 148, "Ações · outputs/acoes/*.json", True),
    (540, 148, "Comando MQTT → atuador", True),
    (698, 148, "Copiloto GenAI + guardrails", False),
    (856, 166, "Risco por talhão · evidências", True),
)
CHIPS_CONSUMO = (
    (224, 148, "Dashboard (9 seções)", False),
    (382, 148, "Aba Automação (ações)", True),
    (540, 148, "Relatórios MD / PDF", False),
    (698, 148, "SQL ad hoc (DuckDB)", False),
    (856, 166, "Benchmarks anonimizados", False),
)
CHIPS_GOV = (
    ("Contratos YAML e catálogo", "LGPD por campo"),
    ("Trilha de eventos JSONL", "Reconciliação por fonte"),
    ("Linhagem por lote", "Ciclo de vida e retenção"),
    ("IAM por bucket · SSE", "Decisões (ADR 0001–0010)"),
    ("Testes pytest isolados", "Semente fixa · reprodutível"),
)


def _esc(texto: str) -> str:
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def chip(x: int, y: int, w: int, texto: str, novo: bool = False, h: int = CHIP_H) -> str:
    classe = "chipnovo" if novo else "chip"
    return (
        f'  <rect class="{classe}" x="{x}" y="{y}" width="{w}" height="{h}" rx="5"/>'
        f'<text class="chiptxt" x="{x + 12}" y="{y + 18}">{_esc(texto)}</text>'
    )


def seta_h(x1: int, x2: int, y: int) -> str:
    return (
        f'    <path class="arrow" d="M{x1} {y} H{x2 - 8}"/>'
        f'<polygon class="head" points="{x2 - 8},{y - 4} {x2},{y} {x2 - 8},{y + 4}"/>'
    )


def seta_v(x: int, y1: int, y2: int) -> str:
    return (
        f'    <path class="arrow" d="M{x} {y1} V{y2 - 8}"/>'
        f'<polygon class="head" points="{x - 4},{y2 - 8} {x},{y2} {x + 4},{y2 - 8}"/>'
    )


def _colunas() -> list[str]:
    linhas: list[str] = []
    for x, (estagio, titulo, sub, faixa, chips) in zip(COLUNA_X, COLUNAS, strict=True):
        linhas.append(f'  <text class="stage" x="{x}" y="14">{_esc(estagio)}</text>')
        linhas.append(
            f'  <rect class="box" x="{x}" y="{TOPO}" width="{COLUNA_W}" '
            f'height="{ALT_COLUNA}" rx="9"/>'
        )
        linhas.append(
            f'  <rect class="{faixa}" x="{x}" y="{TOPO}" width="{COLUNA_W}" '
            'height="3.5" rx="1.75"/>'
        )
        linhas.append(f'  <text class="titulo" x="{x + 16}" y="50">{_esc(titulo)}</text>')
        linhas.append(f'  <text class="sub" x="{x + 16}" y="65">{_esc(sub)}</text>')
        for i, (texto, novo) in enumerate(chips):
            linhas.append(chip(x + 14, 80 + i * CHIP_PASSO, CHIP_W, texto, novo))
        linhas.append("")
    return linhas


def _setas() -> list[str]:
    y_seta = 80 + 2 * CHIP_PASSO + CHIP_H // 2
    linhas = ["  <g>"]
    for x_ant, x_prox in zip(COLUNA_X, COLUNA_X[1:], strict=False):
        linhas.append(seta_h(x_ant + COLUNA_W + 2, x_prox, y_seta))
    # silver → quarentena (registros) · coleta → quarentena (lotes rejeitados na porta)
    linhas.append(seta_v(COLUNA_X[3] + COLUNA_W // 2, TOPO + ALT_COLUNA, Y_QUAR))
    x_coleta = COLUNA_X[1] + COLUNA_W // 2
    x_fim = COLUNA_X[2]
    linhas.append(
        f'    <path class="arrow" d="M{x_coleta} {TOPO + ALT_COLUNA} V{Y_QUAR + 8} '
        f'H{x_fim - 8}"/>'
        f'<polygon class="head" points="{x_fim - 8},{Y_QUAR + 4} {x_fim},{Y_QUAR + 8} '
        f'{x_fim - 8},{Y_QUAR + 12}"/>'
    )
    # gold → automação → consumo
    x_gold = COLUNA_X[4] + COLUNA_W // 2
    linhas.append(seta_v(x_gold, TOPO + ALT_COLUNA, Y_AUTO))
    linhas.append(seta_v(x_gold, Y_AUTO + ALT_AUTO, Y_CONSUMO))
    linhas.append("  </g>")
    linhas.append("")
    return linhas


def _quarentena() -> list[str]:
    x_q, w_q = COLUNA_X[2], COLUNA_X[3] + COLUNA_W - COLUNA_X[2]
    return [
        f'  <rect class="quar" x="{x_q}" y="{Y_QUAR}" width="{w_q}" height="{ALT_QUAR}" rx="9"/>',
        f'  <rect class="r-quar" x="{x_q}" y="{Y_QUAR}" width="{w_q}" height="3.5" rx="1.75"/>',
        f'  <text class="titulo" x="{x_q + 16}" y="{Y_QUAR + 24}">quarentena</text>',
        f'  <text class="sub" x="{x_q + 16}" y="{Y_QUAR + 39}">REPROVADO COM MOTIVO</text>',
        f'  <rect class="chip" x="{x_q + 172}" y="{Y_QUAR + 12}" width="100" height="36" rx="5"/>'
        f'<text class="chiptxt" x="{x_q + 180}" y="{Y_QUAR + 27}">Lotes fora de</text>'
        f'<text class="chiptxt" x="{x_q + 180}" y="{Y_QUAR + 40}">schema (porta)</text>',
        f'  <rect class="chip" x="{x_q + 278}" y="{Y_QUAR + 12}" width="98" height="36" rx="5"/>'
        f'<text class="chiptxt" x="{x_q + 286}" y="{Y_QUAR + 27}">Registros com</text>'
        f'<text class="chiptxt" x="{x_q + 286}" y="{Y_QUAR + 40}">regra + motivo</text>',
        "",
    ]


def _faixa(classe: str, y: int, alt: int, titulo: str, subs: tuple[str, ...]) -> list[str]:
    x0, w_total = COLUNA_X[0], COLUNA_X[4] + COLUNA_W - COLUNA_X[0]
    linhas = [
        f'  <rect class="{classe}" x="{x0}" y="{y}" width="{w_total}" height="{alt}" rx="9"/>',
    ]
    if classe == "auto":
        linhas.append(
            f'  <rect class="r-coleta" x="{x0}" y="{y}" width="{w_total}" height="3.5" rx="1.75"/>'
        )
    linhas.append(f'  <text class="titulo" x="{x0 + 16}" y="{y + 26}">{_esc(titulo)}</text>')
    for i, sub in enumerate(subs):
        linhas.append(f'  <text class="sub" x="{x0 + 16}" y="{y + 41 + 15 * i}">{_esc(sub)}</text>')
    return linhas


def _retorno() -> list[str]:
    """Comando MQTT → atuador: sai do chip "Comando MQTT", desce, corre pelo canal da
    esquerda e entra no chip do atuador, na coluna do campo. Fecha o ciclo."""
    x_cmd = 540 + 148 // 2
    y_atuador = 80 + CHIP_PASSO + CHIP_H // 2
    x_fim = COLUNA_X[0]
    return [
        f'  <path class="retorno" d="M{x_cmd} {Y_AUTO + 44} V{Y_AUTO + ALT_AUTO - 6} '
        f'H20 V{y_atuador} H{x_fim - 8}"/>',
        f'  <polygon class="headretorno" points="{x_fim - 8},{y_atuador - 4} '
        f'{x_fim},{y_atuador} {x_fim - 8},{y_atuador + 4}"/>',
        f'  <text class="nota" transform="translate(14 {Y_AUTO - 30}) rotate(-90)">'
        "comando_mqtt → agrosmart/atuadores/+/comando · simulado | publicado</text>",
        "",
    ]


def gerar_svg() -> str:
    linhas: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {LARGURA} {ALTURA}" '
        f'width="{LARGURA}" height="{ALTURA}" role="img" '
        'aria-label="Arquitetura integrada do AgroSmart — Fase 6">',
        "  <title>Arquitetura integrada do AgroSmart — Fase 6: coleta IoT, Data Lake, "
        "automação e consumo</title>",
        f"  <style>\n{ESTILO}\n  </style>",
        f'  <rect class="fundo" x="0" y="0" width="{LARGURA}" height="{ALTURA}"/>',
        "",
    ]
    linhas += _colunas()
    linhas += _setas()
    linhas += _quarentena()

    linhas += _faixa(
        "auto",
        Y_AUTO,
        ALT_AUTO,
        "Automação inteligente",
        ("agrosmart automacao · FASE 6", "LÊ O GOLD · AGE NO CAMPO"),
    )
    for x, w, texto, novo in CHIPS_AUTO:
        linhas.append(chip(x, Y_AUTO + 14, w, texto, novo, h=30))
    linhas.append("")
    linhas += _retorno()

    linhas += _faixa("band", Y_CONSUMO, ALT_CONSUMO, "Consumo", ("CAMADA DE ENTREGA DE VALOR",))
    for x, w, texto, novo in CHIPS_CONSUMO:
        linhas.append(chip(x, Y_CONSUMO + 14, w, texto, novo, h=30))
    linhas.append("")

    linhas += _faixa(
        "box", Y_GOV, ALT_GOV, "Governança transversal", ("ATRAVESSA TODAS AS CAMADAS",)
    )
    for i, (cima, baixo) in enumerate(CHIPS_GOV):
        x = 250 + i * 152
        w = 160 if i == len(CHIPS_GOV) - 1 else 142
        linhas.append(chip(x, Y_GOV + 14, w, cima))
        linhas.append(chip(x, Y_GOV + 48, w, baixo, h=26))
    linhas += [
        "",
        f'  <text class="sub" x="{COLUNA_X[0]}" y="{ALTURA - 8}">CHIPS COM BORDA AZUL = NOVOS NA '
        "FASE 6 · SETA TRACEJADA = COMANDO DE VOLTA AO CAMPO · "
        "docs/fase6/01-arquitetura-integrada.md</text>",
        "</svg>",
        "",
    ]
    return "\n".join(linhas)


def gerar_png(svg: Path, png: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            navegador = p.chromium.launch()
        except Exception as erro:  # noqa: BLE001 - qualquer falha de lançamento cai no Edge
            print(f"chromium do playwright indisponível ({str(erro).splitlines()[0]}); usando Edge")
            navegador = p.chromium.launch(channel="msedge")
        pagina = navegador.new_page(device_scale_factor=2)
        pagina.goto(svg.resolve().as_uri())
        elemento = pagina.query_selector("svg")
        if elemento is not None:
            elemento.screenshot(path=str(png), omit_background=False)
        else:
            pagina.screenshot(path=str(png), full_page=True)
        navegador.close()


def _relativo(caminho: Path) -> str:
    return caminho.relative_to(RAIZ).as_posix() if caminho.is_relative_to(RAIZ) else str(caminho)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saida", default=str(DESTINO_SVG))
    parser.add_argument("--png", action="store_true", help="também gerar o PNG em 2×")
    args = parser.parse_args()
    destino = Path(args.saida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(gerar_svg(), encoding="utf-8")
    print(_relativo(destino))
    if args.png:
        png = destino.with_suffix(".png")
        gerar_png(destino, png)
        print(f"{_relativo(png)} · {png.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
