"""Captura as telas do dashboard em PNG para a entrega (outputs/prints/).

    streamlit run app/dashboard.py            # em outro terminal
    python scripts/capturar_dashboard.py [--talhao TLH-02] [--url http://localhost:8501]
    python scripts/capturar_dashboard.py --prefixo fase6-05-dashboard   # capturas da Fase 6

Requer ``pip install playwright`` e um navegador: o Chromium do Playwright
(``playwright install chromium``) ou, na falta dele, o Microsoft Edge instalado na
máquina (canal ``msedge``). Não é dependência do pacote: só serve para gerar
evidência. Cada seção do dashboard tem link direto (``?talhao=&secao=``), então a
captura é uma página por seção, sem clicar em nada.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SECOES = (
    "talhao",
    "agua",
    "vegetacao",
    "custos",
    "copiloto",
    "qualidade",
    "sensores",
    "benchmark",
    "automacao",
)
RAIZ = Path(__file__).resolve().parents[1]


def lancar_navegador(playwright):
    """Chromium do Playwright; se não estiver instalado, o Edge da máquina."""
    try:
        return playwright.chromium.launch()
    except Exception as erro:  # noqa: BLE001 - qualquer falha de lançamento cai no Edge
        print(f"chromium do playwright indisponível ({str(erro).splitlines()[0]}); usando Edge")
        return playwright.chromium.launch(channel="msedge")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--talhao", default="TLH-02")
    parser.add_argument("--url", default="http://localhost:8501")
    parser.add_argument("--saida", default=str(RAIZ / "outputs" / "prints"))
    parser.add_argument("--secoes", nargs="*", default=list(SECOES))
    parser.add_argument(
        "--prefixo",
        default="passo5-dashboard",
        help="prefixo dos arquivos: <prefixo>-<secao>.png (Fase 6: fase6-05-dashboard)",
    )
    parser.add_argument(
        "--altura",
        type=int,
        default=900,
        help="altura do viewport em px (o Streamlit rola dentro da página: aumente para "
        "capturar seções longas, ex.: 1800 para a aba Automação)",
    )
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright não instalado: pip install playwright && playwright install chromium")
        return 2

    saida = Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        navegador = lancar_navegador(p)
        pagina = navegador.new_page(
            viewport={"width": 1440, "height": args.altura}, device_scale_factor=1
        )
        for secao in args.secoes:
            url = f"{args.url}/?talhao={args.talhao}&secao={secao}"
            pagina.goto(url, wait_until="networkidle")
            # O Streamlit desenha depois do websocket: espera a seção aparecer e os
            # gráficos (canvas do Vega) terminarem.
            pagina.wait_for_selector("h3", timeout=60_000)
            pagina.wait_for_function(
                "() => !document.querySelector('[data-testid=\"stStatusWidget\"]')",
                timeout=60_000,
            )
            time.sleep(2.5)
            destino = saida / f"{args.prefixo}-{secao}.png"
            pagina.screenshot(path=str(destino), full_page=True)
            print(f"{destino.relative_to(RAIZ)} · {destino.stat().st_size / 1024:.0f} KB")
        navegador.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
