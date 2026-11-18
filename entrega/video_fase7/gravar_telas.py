"""Grava telas REAIS da plataforma rodando (vídeo .webm do navegador via Playwright).

    python entrega/video_fase7/gravar_telas.py grafana prometheus dashboard_operacao ...

Cada roteiro abre uma página local (127.0.0.1), espera carregar e faz uma navegação
curta (rolagem, troca de seção). O vídeo sai em ``entrega/video_fase7/telas/<nome>.webm``
e entra no vídeo final pelo ``build_video.py`` (itens do tipo ``video``). Nada aqui é
montagem: é a tela do serviço no momento da gravação.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
SAIDA = AQUI / "telas"
W, H = 1920, 1080
GRAFANA = "http://127.0.0.1:3000/d/agrosmart-operacao/agrosmart-operacao?orgId=1&kiosk&refresh=10s"
ST = "http://127.0.0.1:8501"


def _esperar_streamlit(p):
    p.get_by_text("AgroSmart · Camada de consumo").first.wait_for(timeout=180_000)
    p.wait_for_function(
        "() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=180_000
    )
    p.wait_for_timeout(1500)


def _rolar(p, passos: int, dy: int = 220, pausa: int = 900, seletor: str | None = None):
    for _ in range(passos):
        if seletor:
            p.evaluate(f"document.querySelector('{seletor}').scrollBy(0, {dy})")
        else:
            p.mouse.wheel(0, dy)
        p.wait_for_timeout(pausa)


def grafana(p):
    p.goto(GRAFANA)
    p.wait_for_timeout(9000)
    _rolar(p, 10, 160, 1100)
    p.wait_for_timeout(2500)


def prometheus(p):
    p.goto("http://127.0.0.1:9090/alerts")
    p.wait_for_timeout(4000)
    for texto in ("LandingParada", "CicloFalhando", "WorkerFora"):
        alvo = p.get_by_text(texto).first
        if alvo.count():
            alvo.click()
            p.wait_for_timeout(2200)
    p.goto("http://127.0.0.1:9090/targets")
    p.wait_for_timeout(5000)


def worker(p):
    p.goto("http://127.0.0.1:8000/status")
    p.wait_for_timeout(4000)
    _rolar(p, 4, 260, 1000)
    p.goto("http://127.0.0.1:8000/metrics")
    p.wait_for_timeout(3000)
    _rolar(p, 6, 300, 800)


def dashboard_operacao(p):
    p.goto(f"{ST}/?talhao=TLH-02&secao=operacao")
    _esperar_streamlit(p)
    p.wait_for_timeout(2500)
    _rolar(p, 7, 200, 1100)
    p.wait_for_timeout(2000)


def dashboard_automacao(p):
    p.goto(f"{ST}/?talhao=TLH-02&secao=automacao")
    _esperar_streamlit(p)
    p.wait_for_timeout(2500)
    _rolar(p, 8, 220, 1100)
    p.wait_for_timeout(1500)


def dashboard_agua(p):
    p.goto(f"{ST}/?talhao=TLH-02&secao=agua")
    _esperar_streamlit(p)
    p.wait_for_timeout(2500)
    _rolar(p, 5, 220, 1000)


def html_local(nome_arquivo: str):
    """Grava um HTML local (relatório da mineração, resumo do pipeline) rolando."""

    def _gravar(p):
        p.goto((AQUI / nome_arquivo).resolve().as_uri())
        p.wait_for_timeout(3000)
        _rolar(p, 12, 240, 1100)
        p.wait_for_timeout(1500)

    return _gravar


ROTEIROS = {
    "grafana": grafana,
    "prometheus": prometheus,
    "worker": worker,
    "dashboard_operacao": dashboard_operacao,
    "dashboard_automacao": dashboard_automacao,
    "dashboard_agua": dashboard_agua,
    "processos": html_local("paginas/processos.html"),
    "pipeline": html_local("paginas/pipeline.html"),
}


def main(nomes: list[str]) -> int:
    from playwright.sync_api import sync_playwright

    SAIDA.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        try:
            nav = pw.chromium.launch()
        except Exception:  # noqa: BLE001
            nav = pw.chromium.launch(channel="msedge")
        for nome in nomes or list(ROTEIROS):
            tmp = SAIDA / f"_tmp_{nome}"
            ctx = nav.new_context(
                viewport={"width": W, "height": H},
                record_video_dir=str(tmp),
                record_video_size={"width": W, "height": H},
            )
            pagina = ctx.new_page()
            try:
                ROTEIROS[nome](pagina)
            finally:
                video = pagina.video
                ctx.close()
            destino = SAIDA / f"{nome}.webm"
            shutil.move(video.path(), destino)
            shutil.rmtree(tmp, ignore_errors=True)
            print(f"ok {destino.name} {destino.stat().st_size // 1024} KB", flush=True)
        nav.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
