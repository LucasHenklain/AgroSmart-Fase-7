"""Capturas de tela da operação (Fase 7): Grafana, Prometheus, worker e dashboard.

    python scripts/capturar_operacao.py --prefixo fase7-04            # uma rodada de prints
    python scripts/capturar_operacao.py --timelapse 60 --duracao 1800  # Grafana a cada minuto

Usa o Chromium do Playwright (ou o Edge da máquina). Só lê páginas locais
(127.0.0.1); nada é enviado para fora. As imagens saem de telas reais da
plataforma rodando — é a evidência dos requisitos 2, 4 e 5.
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
GRAFANA = "http://127.0.0.1:3000/d/agrosmart-operacao/agrosmart-operacao?orgId=1&kiosk&refresh=10s"
STREAMLIT = "http://127.0.0.1:8501"
PAGINAS = {
    "grafana-operacao": (GRAFANA, 1600, 1500, 9000),
    "prometheus-alertas": ("http://127.0.0.1:9090/alerts", 1440, 1100, 2500),
    "prometheus-alvos": ("http://127.0.0.1:9090/targets", 1440, 800, 2500),
    "worker-status": ("http://127.0.0.1:8000/status", 1100, 1400, 800),
    "worker-metrics": ("http://127.0.0.1:8000/metrics", 1100, 1400, 800),
    "dashboard-operacao": ("http://127.0.0.1:8501/?secao=operacao", 1440, 1700, 4000),
    "dashboard-automacao": (
        "http://127.0.0.1:8501/?talhao=TLH-02&secao=automacao",
        1440,
        1800,
        4000,
    ),
}


def lancar(p):
    try:
        return p.chromium.launch()
    except Exception:  # noqa: BLE001 - sem Chromium do Playwright, usa o Edge
        return p.chromium.launch(channel="msedge")


def capturar(pagina, url: str, largura: int, altura: int, espera_ms: int, destino: Path) -> None:
    pagina.set_viewport_size({"width": largura, "height": altura})
    pagina.goto(url, wait_until="domcontentloaded", timeout=60_000)
    if ":8501" in url:
        # Streamlit desenha depois do websocket e da leitura do gold: espera o título.
        pagina.get_by_text("AgroSmart · Camada de consumo").first.wait_for(timeout=120_000)
        pagina.wait_for_function(
            "() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=120_000
        )
    pagina.wait_for_timeout(espera_ms)
    pagina.screenshot(path=str(destino), full_page=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefixo", default="fase7-04")
    parser.add_argument("--paginas", nargs="*", default=list(PAGINAS))
    parser.add_argument("--saida", default=str(RAIZ / "outputs" / "prints"))
    parser.add_argument("--timelapse", type=int, default=0, help="segundos entre prints do Grafana")
    parser.add_argument("--duracao", type=int, default=0, help="duração do timelapse (s)")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    saida = Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        navegador = lancar(p)
        pagina = navegador.new_page(device_scale_factor=1)
        if args.timelapse:
            pasta = saida / "timelapse"
            pasta.mkdir(exist_ok=True)
            fim = time.monotonic() + args.duracao
            while time.monotonic() < fim:
                carimbo = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                url, largura, altura, espera = PAGINAS["grafana-operacao"]
                try:
                    capturar(pagina, url, largura, altura, 6000, pasta / f"grafana-{carimbo}.png")
                    print(f"{carimbo} ok", flush=True)
                except Exception as erro:  # noqa: BLE001 - um print perdido não para o timelapse
                    print(f"{carimbo} falhou: {erro}", flush=True)
                time.sleep(max(1, args.timelapse - 8))
        else:
            for nome in args.paginas:
                url, largura, altura, espera = PAGINAS[nome]
                destino = saida / f"{args.prefixo}-{nome}.png"
                try:
                    capturar(pagina, url, largura, altura, espera, destino)
                    print(f"ok  {destino.as_posix()}")
                except Exception as erro:  # noqa: BLE001
                    print(f"ERRO {nome}: {erro}")
        navegador.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
