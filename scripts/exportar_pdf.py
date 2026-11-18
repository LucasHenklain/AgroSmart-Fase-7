"""Exporta os documentos da entrega para PDF e os diagramas SVG para PNG.

    python scripts/exportar_pdf.py            # docs/pdf/*.pdf + docs/diagramas/*.png

Os documentos são escritos em Markdown (renderizam no GitHub e em qualquer
editor); esta exportação existe para o avaliador que abre o zip sem nenhuma
ferramenta além de um leitor de PDF. Requer ``pip install markdown playwright``
e um navegador: o Chromium do Playwright (``playwright install chromium``) ou,
na falta dele, o Microsoft Edge da máquina (canal ``msedge``) — ferramentas de
evidência, não dependências do pacote.

O que faz: Markdown → HTML (tabelas, código, listas) com folha de estilo de
impressão → PDF A4 via Chromium; blocos ``mermaid`` são desenhados pela
biblioteca mermaid (CDN; sem rede, ficam como código). Cada SVG em
``docs/diagramas`` vira um PNG em 2× para quem não abre SVG. Os documentos da
Fase 5 vão para ``docs/pdf/``; os da Fase 6 (``docs/fase6/*.md``), para
``docs/pdf/fase6/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DOCS = RAIZ / "docs"
DIAGRAMAS = DOCS / "diagramas"
SAIDA_PDF = DOCS / "pdf"
DOCUMENTOS = (
    "01-arquitetura-datalake.md",
    "02-ingestao-e-manutencao.md",
    "03-ia-generativa.md",
    "04-modelo-de-negocio.md",
    "runbook-operacao.md",
    "dicionario-de-dados.md",
)
# (pasta relativa a docs/, rótulo do rodapé, subpasta de saída em docs/pdf/)
FASES = (
    (".", "AgroSmart Fase 5", "."),
    ("fase6", "AgroSmart Fase 6", "fase6"),
    ("fase7", "AgroSmart Fase 7", "fase7"),
)

CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
body { font-family: "Segoe UI", Helvetica, Arial, sans-serif; font-size: 10.5pt; line-height: 1.45;
       color: #1f2933; max-width: 100%; }
h1 { font-size: 20pt; color: #1b5e20; border-bottom: 2px solid #2e7d32; padding-bottom: 4px; }
h2 { font-size: 15pt; color: #1b5e20; margin-top: 22px; page-break-after: avoid; }
h3 { font-size: 12.5pt; color: #2e7d32; margin-top: 16px; page-break-after: avoid; }
blockquote { border-left: 3px solid #a5d6a7; margin: 8px 0; padding: 2px 12px; color: #37474f;
             background: #f1f8e9; }
table { border-collapse: collapse; width: 100%; font-size: 9pt; margin: 8px 0;
        page-break-inside: auto; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #cfd8dc; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #e8f5e9; }
code { font-family: Consolas, "Courier New", monospace; font-size: 9pt; background: #f5f5f5;
       padding: 0 3px; border-radius: 3px; }
pre { background: #f5f5f5; border: 1px solid #e0e0e0; padding: 8px; font-size: 8.5pt;
      white-space: pre-wrap; word-break: break-word; page-break-inside: avoid; }
pre code { background: none; padding: 0; }
img { max-width: 100%; page-break-inside: avoid; }
.mermaid { text-align: center; page-break-inside: avoid; }
a { color: #1565c0; text-decoration: none; }
"""

MERMAID = (
    '<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>'
    "<script>mermaid.initialize({startOnLoad: true, theme: 'neutral'});</script>"
)


def markdown_para_html(md_texto: str, titulo: str) -> str:
    import markdown

    html = markdown.markdown(
        md_texto,
        extensions=["tables", "fenced_code", "sane_lists", "toc"],
        output_format="html5",
    )
    # Blocos ```mermaid virarão <pre><code class="language-mermaid">…</code></pre>;
    # o mermaid quer <pre class="mermaid">texto</pre>.
    html = html.replace('<pre><code class="language-mermaid">', '<pre class="mermaid">').replace(
        "</code></pre>", "</pre>"
    )
    return (
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        f"<title>{titulo}</title><style>{CSS}</style>{MERMAID}</head>"
        f"<body>{html}</body></html>"
    )


def lancar_navegador(playwright):
    """Chromium do Playwright; se não estiver instalado, o Edge da máquina."""
    try:
        return playwright.chromium.launch()
    except Exception as erro:  # noqa: BLE001 - qualquer falha de lançamento cai no Edge
        print(f"chromium do playwright indisponível ({str(erro).splitlines()[0]}); usando Edge")
        return playwright.chromium.launch(channel="msedge")


def documentos_por_fase() -> list[tuple[Path, str, Path]]:
    """(origem .md, rótulo do rodapé, destino .pdf) para cada documento existente."""
    itens: list[tuple[Path, str, Path]] = []
    # `python scripts/exportar_pdf.py fase7` exporta só a pasta pedida (e os diagramas).
    pedidas = set(sys.argv[1:])
    for pasta, rotulo, saida in FASES:
        if pedidas and pasta not in pedidas:
            continue
        origem_dir = DOCS / pasta
        nomes = DOCUMENTOS if pasta == "." else sorted(p.name for p in origem_dir.glob("*.md"))
        for nome in nomes:
            origem = origem_dir / nome
            if not origem.exists():
                print(f"pulado (não existe): {origem.relative_to(RAIZ)}")
                continue
            itens.append((origem, rotulo, SAIDA_PDF / saida / f"{origem.stem}.pdf"))
    return itens


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright não instalado: pip install playwright && playwright install chromium")
        return 2
    SAIDA_PDF.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        navegador = lancar_navegador(p)
        pagina = navegador.new_page()

        for origem, rotulo, destino in documentos_por_fase():
            html = markdown_para_html(origem.read_text(encoding="utf-8"), origem.stem)
            # ao lado do .md: links relativos (diagramas, prints) funcionam
            temporario = origem.parent / f".{origem.stem}.html"
            temporario.write_text(html, encoding="utf-8")
            try:
                pagina.goto(temporario.resolve().as_uri(), wait_until="networkidle")
                pagina.wait_for_timeout(1200)  # mermaid termina de desenhar
                destino.parent.mkdir(parents=True, exist_ok=True)
                pagina.pdf(
                    path=str(destino),
                    format="A4",
                    print_background=True,
                    margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"},
                    display_header_footer=True,
                    header_template="<span></span>",
                    footer_template=(
                        "<div style='font-size:8px;color:#78909c;width:100%;text-align:center;'>"
                        f"{rotulo} · {origem.stem} · página "
                        "<span class='pageNumber'></span> de <span class='totalPages'></span></div>"
                    ),
                )
                print(f"{destino.relative_to(RAIZ)} · {destino.stat().st_size / 1024:.0f} KB")
            finally:
                temporario.unlink(missing_ok=True)

        for svg in sorted(DIAGRAMAS.glob("*.svg")):
            pagina_svg = navegador.new_page(device_scale_factor=2)
            pagina_svg.goto(svg.resolve().as_uri())
            elemento = pagina_svg.query_selector("svg")
            destino = svg.with_suffix(".png")
            if elemento is not None:
                elemento.screenshot(path=str(destino), omit_background=False)
            else:
                pagina_svg.screenshot(path=str(destino), full_page=True)
            pagina_svg.close()
            print(f"{destino.relative_to(RAIZ)} · {destino.stat().st_size / 1024:.0f} KB")
        navegador.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
