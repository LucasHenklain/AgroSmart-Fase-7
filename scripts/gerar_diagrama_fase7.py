"""Diagrama da arquitetura final (Fase 7): componentes, containers e fluxos.

    python scripts/gerar_diagrama_fase7.py --png   # docs/diagramas/arquitetura-fase7.{svg,png}

Mesma folha de estilo do diagrama da Fase 6 (``gerar_diagrama_fase6.py``), agora na
visão de implantação: cada caixa com ``:porta`` é um container do
``infra/docker-compose.yml``; as faixas de baixo são observabilidade (monitoramento +
mineração de processos) e a esteira DevSecOps que produz a imagem.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gerar_diagrama_fase6 import ESTILO, _esc, gerar_png  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
DESTINO_SVG = RAIZ / "docs" / "diagramas" / "arquitetura-fase7.svg"

LARGURA, ALTURA = 1224, 806
COL_X = (40, 274, 508, 742, 976)
COL_W = 208
TOPO, ALT_COL = 30, 318
CHIP_W, CHIP_H, PASSO = 180, 28, 35

ESTILO7 = (
    ESTILO
    + "\n"
    + "\n".join(
        [
            "    .obs { fill: #F3ECDD; stroke: #9C7A1E; stroke-opacity: .5; }",
            "    .dev { fill: #E7E4F0; stroke: #5B4A8A; stroke-opacity: .5; }",
            "    .r-obs { fill: #9C7A1E; } .r-dev { fill: #5B4A8A; }",
            "    .sim { fill: #FFF7E6; stroke: #C77700; stroke-dasharray: 4 3; }",
            "    .metr { stroke: #9C7A1E; stroke-width: 1.3; fill: none; stroke-dasharray: 3 3; }",
            "    .headmetr { fill: #9C7A1E; }",
            "    .rot { font-family: Consolas, monospace; font-size: 8.4px; fill: #616B62; }",
        ]
    )
)

# (estágio, título, subtítulo, faixa, chips: (texto, estilo) estilo ∈ chip|chipnovo|sim)
COLUNAS = (
    (
        "CAMPO",
        "Dispositivos",
        "SENSORES + ATUADOR + ORIGENS",
        "r-campo",
        (
            ("ESP32 no Wokwi (firmware/)", "chip"),
            ("dispositivos · 12 sensores", "sim"),
            ("  (emulador · SIMULAÇÃO)", "sim"),
            ("Atuador de irrigação (relé)", "chip"),
            ("Clima API · ERP CSV", "chip"),
            ("Drone JPEG · Laudos PDF", "chip"),
            ("(carga inicial simulada)", "sim"),
        ),
    ),
    (
        "TRANSPORTE E COLETA",
        "Broker + gateway",
        "MQTT · LANDING JSONL",
        "r-coleta",
        (
            ("mosquitto :1883", "chip"),
            ("sensores/+/leitura (QoS 0)", "chip"),
            ("atuadores/+/irrigacao (QoS 1)", "chip"),
            ("gateway · coletar-mqtt", "chip"),
            ("janela 15 min · dedup chave", "chip"),
            ("volume landing/", "chip"),
        ),
    ),
    (
        "PROCESSAMENTO CONTÍNUO",
        "worker :8000",
        "agrosmart operar · CICLO 60 s",
        "r-coleta",
        (
            ("1 ingerir → bronze", "chipnovo"),
            ("2 silver (58 regras · LGPD)", "chipnovo"),
            ("3 gold (14 tabelas · SQL)", "chipnovo"),
            ("4 catálogo + linhagem", "chipnovo"),
            ("5 decidir (motor de regras)", "chipnovo"),
            ("6 atuar (MQTT QoS 1)", "chipnovo"),
            ("v2 incremental · /metrics", "chipnovo"),
        ),
    ),
    (
        "DATA LAKE",
        "minio :9000",
        "S3 · PARQUET · VERSIONADO",
        "r-bronze",
        (
            ("agrosmart-bronze (imutável)", "chip"),
            ("agrosmart-silver (Parquet)", "chip"),
            ("agrosmart-quarentena", "chip"),
            ("agrosmart-gold (dimensional)", "chip"),
            ("manifest por lote · sha256", "chip"),
            ("ILM: retenção por camada", "chip"),
        ),
    ),
    (
        "SAÍDA E CONSUMO",
        "dashboard :8501",
        "STREAMLIT · RELATÓRIOS",
        "r-ouro",
        (
            ("10 seções sobre o gold", "chip"),
            ("aba Automação (ações)", "chip"),
            ("aba Operação (ciclos)", "chipnovo"),
            ("plano outputs/acoes/*.json", "chip"),
            ("copiloto IA + guardrails", "chip"),
            ("SQL ad hoc (DuckDB)", "chip"),
        ),
    ),
)

Y_OBS, ALT_OBS = 382, 132
Y_DEV, ALT_DEV = 538, 120
Y_GOV, ALT_GOV = 682, 96


def chip(x: int, y: int, w: int, texto: str, classe: str = "chip", h: int = CHIP_H) -> str:
    return (
        f'  <rect class="{classe}" x="{x}" y="{y}" width="{w}" height="{h}" rx="5"/>'
        f'<text class="chiptxt" x="{x + 10}" y="{y + 18}">{_esc(texto)}</text>'
    )


def seta(x1: float, y1: float, x2: float, y2: float, classe: str = "arrow", cabeca: str = "head"):
    """Seta reta (horizontal ou vertical) com ponta no destino."""
    if y1 == y2:
        s = 1 if x2 > x1 else -1
        ponta = f"{x2 - 8 * s},{y2 - 4} {x2},{y2} {x2 - 8 * s},{y2 + 4}"
        corpo = f"M{x1} {y1} H{x2 - 8 * s}"
    else:
        s = 1 if y2 > y1 else -1
        ponta = f"{x2 - 4},{y2 - 8 * s} {x2},{y2} {x2 + 4},{y2 - 8 * s}"
        corpo = f"M{x1} {y1} V{y2 - 8 * s}"
    return f'  <path class="{classe}" d="{corpo}"/><polygon class="{cabeca}" points="{ponta}"/>'


def rotulo(x: float, y: float, texto: str, ancora: str = "middle") -> str:
    return f'  <text class="rot" x="{x}" y="{y}" text-anchor="{ancora}">{_esc(texto)}</text>'


def faixa(classe: str, barra: str, y: int, alt: int, titulo: str, sub: str) -> list[str]:
    x0, w = COL_X[0], COL_X[4] + COL_W - COL_X[0]
    return [
        f'  <rect class="{classe}" x="{x0}" y="{y}" width="{w}" height="{alt}" rx="9"/>',
        f'  <rect class="{barra}" x="{x0}" y="{y}" width="{w}" height="3.5" rx="1.75"/>',
        f'  <text class="titulo" x="{x0 + 16}" y="{y + 26}">{_esc(titulo)}</text>',
        f'  <text class="sub" x="{x0 + 16}" y="{y + 41}">{_esc(sub)}</text>',
    ]


def gerar_svg() -> str:
    L: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {LARGURA} {ALTURA}" '
        f'width="{LARGURA}" height="{ALTURA}" role="img" '
        'aria-label="Arquitetura final do AgroSmart — Fase 7">',
        "  <title>Arquitetura final do AgroSmart — Fase 7: IoT, processamento contínuo, "
        "Data Lake, consumo, observabilidade e DevSecOps</title>",
        f"  <style>\n{ESTILO7}\n  </style>",
        f'  <rect class="fundo" x="0" y="0" width="{LARGURA}" height="{ALTURA}"/>',
    ]
    # Colunas (containers e componentes)
    for x, (estagio, titulo, sub, barra, chips) in zip(COL_X, COLUNAS, strict=True):
        L.append(f'  <text class="stage" x="{x}" y="20">{_esc(estagio)}</text>')
        L.append(
            f'  <rect class="box" x="{x}" y="{TOPO}" width="{COL_W}" height="{ALT_COL}" rx="9"/>'
        )
        L.append(
            f'  <rect class="{barra}" x="{x}" y="{TOPO}" width="{COL_W}" height="3.5" rx="1.75"/>'
        )
        L.append(f'  <text class="titulo" x="{x + 14}" y="{TOPO + 26}">{_esc(titulo)}</text>')
        L.append(f'  <text class="sub" x="{x + 14}" y="{TOPO + 41}">{_esc(sub)}</text>')
        for i, (texto, classe) in enumerate(chips):
            L.append(chip(x + 14, TOPO + 54 + i * PASSO, CHIP_W, texto, classe))

    # Fluxo principal (entrada → processamento → saída)
    y_fluxo = TOPO + 54 + PASSO + CHIP_H // 2
    L.append(seta(COL_X[0] + COL_W + 2, y_fluxo, COL_X[1], y_fluxo))
    L.append(rotulo((COL_X[0] + COL_W + COL_X[1]) / 2, y_fluxo - 6, "MQTT"))
    y_land = TOPO + 54 + 5 * PASSO + CHIP_H // 2
    L.append(seta(COL_X[1] + COL_W + 2, y_land, COL_X[2], y_land))
    L.append(rotulo((COL_X[1] + COL_W + COL_X[2]) / 2, y_land - 6, "landing"))
    y_lake = TOPO + 54 + 1 * PASSO + CHIP_H // 2
    L.append(seta(COL_X[2] + COL_W + 2, y_lake, COL_X[3], y_lake))
    L.append(rotulo((COL_X[2] + COL_W + COL_X[3]) / 2, y_lake - 6, "S3 put"))
    y_le = TOPO + 54 + 3 * PASSO + CHIP_H // 2
    L.append(seta(COL_X[3], y_le, COL_X[2] + COL_W + 2, y_le))
    L.append(rotulo((COL_X[2] + COL_W + COL_X[3]) / 2, y_le - 6, "gold"))
    y_cons = TOPO + 54 + 3 * PASSO + CHIP_H // 2
    L.append(seta(COL_X[3] + COL_W + 2, y_cons, COL_X[4], y_cons))
    L.append(rotulo((COL_X[3] + COL_W + COL_X[4]) / 2, y_cons - 6, "lê gold"))

    # Retorno ao campo: atuar → broker → atuador (tracejado azul), pelas margens das
    # colunas e pelo canal da esquerda, sem cruzar nenhum chip.
    y_atuar = TOPO + 54 + 5 * PASSO + CHIP_H // 2
    y_baixo = TOPO + ALT_COL - 8
    y_atuador = TOPO + 54 + 3 * PASSO + CHIP_H // 2
    x_margem = COL_X[2] + 7
    L.append(
        f'  <path class="retorno" d="M{COL_X[2] + 14} {y_atuar} H{x_margem} V{y_baixo} '
        f'H22 V{y_atuador} H{COL_X[0] + 6}"/>'
        f'<polygon class="headretorno" points="{COL_X[0] + 6},{y_atuador - 4} '
        f'{COL_X[0] + 14},{y_atuador} {COL_X[0] + 6},{y_atuador + 4}"/>'
    )
    L.append(
        f'  <text class="nota" x="{COL_X[1] + 10}" y="{y_baixo - 4}">'
        "comando {acao: ligar, minutos} → broker → relé (QoS 1)</text>"
    )

    # Observabilidade
    L += faixa(
        "obs",
        "r-obs",
        Y_OBS,
        ALT_OBS,
        "Observabilidade e análise de processo",
        "MONITORAMENTO CONTÍNUO · MINERAÇÃO DE PROCESSOS",
    )
    obs = (
        (232, 190, "prometheus :9090 (15 s)", "chip"),
        (232, 190, "6 regras de alerta", "chip"),
        (436, 190, "grafana :3000 · painel", "chip"),
        (436, 190, "saúde · etapas · lake", "chip"),
        (640, 200, "eventos.jsonl (case id)", "chipnovo"),
        (640, 200, "status/operacao.json", "chipnovo"),
        (854, 162, "event log CSV / XES", "chipnovo"),
        (854, 162, "DFG · variantes · pm4py", "chipnovo"),
        (1030, 146, "insight → v2", "chipnovo"),
        (1030, 146, "(ciclo incremental)", "chipnovo"),
    )
    for i, (x, w, texto, classe) in enumerate(obs):
        L.append(chip(x, Y_OBS + 54 + (i % 2) * 36, w, texto, classe))
    # setas na faixa
    y_o = Y_OBS + 54 + CHIP_H // 2
    L.append(seta(422, y_o, 436, y_o))
    L.append(seta(840, y_o, 854, y_o))
    L.append(seta(1016, y_o, 1030, y_o))
    # worker → prometheus (métricas) e worker → eventos
    x_w = COL_X[2] + 40
    L.append(seta(x_w, TOPO + ALT_COL, x_w, Y_OBS, "metr", "headmetr"))
    L.append(rotulo(x_w + 6, Y_OBS - 10, "/metrics · eventos", "start"))
    # melhoria volta ao worker
    x_m = 1100
    L.append(
        f'  <path class="metr" d="M{x_m} {Y_OBS} V{TOPO + ALT_COL + 18} H{COL_X[2] + COL_W - 30} '
        f'V{TOPO + ALT_COL + 8}"/>'
        f'<polygon class="headmetr" points="{COL_X[2] + COL_W - 34},{TOPO + ALT_COL + 8} '
        f'{COL_X[2] + COL_W - 30},{TOPO + ALT_COL} {COL_X[2] + COL_W - 26},{TOPO + ALT_COL + 8}"/>'
    )
    L.append(rotulo(x_m - 6, TOPO + ALT_COL + 14, "melhoria aplicada", "end"))

    # DevSecOps
    L += faixa("dev", "r-dev", Y_DEV, ALT_DEV, "DevSecOps", "GITHUB ACTIONS = RUNNER LOCAL")
    dev = (
        "git · commits",
        "ruff · complexidade",
        "pytest + cobertura",
        "bandit (SAST)",
        "pip-audit (SCA)",
        "docker build",
        "trivy (imagem)",
        "agrosmart:7.0.0",
    )
    x = 232
    for i, texto in enumerate(dev):
        w = 110
        L.append(chip(x, Y_DEV + 58, w, texto, "chipnovo" if i else "chip", h=30))
        if i < len(dev) - 1:
            L.append(seta(x + w, Y_DEV + 73, x + w + 9, Y_DEV + 73))
        x += w + 9
    L.append(
        rotulo(
            232,
            Y_DEV + 106,
            "não-root (UID 10001) · sem pip · cap_drop ALL · "
            "no-new-privileges · CA corporativa só como build secret",
            "start",
        )
    )

    # Governança
    L += faixa("box", "r-prata", Y_GOV, ALT_GOV, "Governança transversal", "ATRAVESSA TUDO")
    gov = (
        ("Contratos YAML por fonte", "LGPD por campo"),
        ("Quarentena com motivo", "Reconciliação por fonte"),
        ("Linhagem por lote", "Retenção e ciclo de vida"),
        ("Portas só em 127.0.0.1", "Segredos fora da imagem"),
        ("Testes pytest + cobertura", "Semente fixa · reprodutível"),
    )
    for i, (cima, baixo) in enumerate(gov):
        x = 232 + i * 190
        L.append(chip(x, Y_GOV + 22, 180, cima))
        L.append(chip(x, Y_GOV + 58, 180, baixo, h=26))

    L += [
        f'  <text class="sub" x="{COL_X[0]}" y="{ALTURA - 12}">CAIXA COM :PORTA = CONTAINER DO '
        "infra/docker-compose.yml · BORDA AZUL = NOVO NA FASE 7 · TRACEJADO LARANJA = SIMULAÇÃO "
        "ROTULADA · SETA AZUL = COMANDO AO CAMPO · docs/fase7/01-arquitetura-final.md</text>",
        "</svg>",
        "",
    ]
    return "\n".join(L)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saida", default=str(DESTINO_SVG))
    parser.add_argument("--png", action="store_true")
    args = parser.parse_args()
    destino = Path(args.saida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(gerar_svg(), encoding="utf-8")
    print(destino)
    if args.png:
        png = destino.with_suffix(".png")
        gerar_png(destino, png)
        print(f"{png} · {png.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
