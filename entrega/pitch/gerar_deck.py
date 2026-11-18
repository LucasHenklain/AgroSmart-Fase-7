# -*- coding: utf-8 -*-
"""Gera o deck do pitch: cada slides/*.html -> PNG 1920x1080 (Edge headless) -> PDF e PPTX (uma imagem por slide).
Uso: python gerar_deck.py
"""
from pathlib import Path
from PIL import Image
from build_video import render_slide

AQUI = Path(__file__).resolve().parent
work = AQUI / "_work" / "deck"; work.mkdir(parents=True, exist_ok=True)
pngs = []
for html in sorted((AQUI / "slides").glob("*.html")):
    png = work / f"deck_{html.stem}.png"
    if png.exists():
        png.unlink()
    render_slide(html, png, 1920, 1080)
    pngs.append(png); print("ok", png.name)

imgs = [Image.open(p).convert("RGB") for p in pngs]
pdf = AQUI / "slides-pitch-agrosmart.pdf"
imgs[0].save(pdf, save_all=True, append_images=imgs[1:], resolution=144)
print("PDF ->", pdf)

try:
    from pptx import Presentation
    from pptx.util import Emu
    prs = Presentation(); prs.slide_width = Emu(12192000); prs.slide_height = Emu(6858000)  # 16:9
    for p in pngs:
        s = prs.slides.add_slide(prs.slide_layouts[6])
        s.shapes.add_picture(str(p), 0, 0, prs.slide_width, prs.slide_height)
    out = AQUI / "slides-pitch-agrosmart.pptx"; prs.save(out); print("PPTX ->", out)
except ImportError:
    print("python-pptx ausente: PPTX não gerado")
