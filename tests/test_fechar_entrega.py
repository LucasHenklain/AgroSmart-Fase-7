"""Funções de texto do scripts/fechar_entrega.py (sem tocar no repositório nem no zip)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_CAMINHO = Path(__file__).resolve().parents[1] / "scripts" / "fechar_entrega.py"
_spec = importlib.util.spec_from_file_location("fechar_entrega", _CAMINHO)
fe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fe)

FINAL = "https://youtu.be/AbCdEf12345"
PITCH = "https://www.youtube.com/watch?v=XyZ_987-654"

LINK_VIDEO = """AgroSmart · Fase 7

Links no YouTube (não listado), para quem preferir assistir online:
- vídeo final: [A PREENCHER PELA EQUIPE]
- pitch:       [A PREENCHER PELA EQUIPE]

Repositório (público): https://github.com/x/y
"""


@pytest.mark.parametrize(
    "url",
    [FINAL, PITCH, "https://youtube.com/shorts/AbCdEf12", "https://youtu.be/AbCdEf12?si=abc"],
)
def test_aceita_links_do_youtube(url):
    assert fe.validar_link(f"  {url} ") == url


@pytest.mark.parametrize(
    "url",
    [
        "http://youtu.be/AbCdEf12",
        "https://vimeo.com/123456",
        "youtu.be/AbCdEf12",
        "https://youtu.be/",
    ],
)
def test_recusa_link_que_nao_e_youtube(url):
    with pytest.raises(ValueError):
        fe.validar_link(url)


def test_preenche_link_video_e_e_idempotente():
    uma = fe.preencher_link_video(LINK_VIDEO, FINAL, PITCH)
    assert "PREENCHER" not in uma
    assert f"- vídeo final: {FINAL}" in uma and f"- pitch:       {PITCH}" in uma
    assert "Repositório (público): https://github.com/x/y" in uma
    outra = "https://youtu.be/Novo123456"
    duas = fe.preencher_link_video(uma, outra, PITCH)
    assert outra in duas and FINAL not in duas


def test_link_video_sem_as_linhas_falha():
    with pytest.raises(ValueError):
        fe.preencher_link_video("texto qualquer", FINAL, PITCH)


def test_readme_com_e_sem_links():
    base = f"# t\nantes\n\n{fe.MARCA_INI}\nqualquer coisa\n{fe.MARCA_FIM}\n\ndepois\n"
    com = fe.preencher_readme(base, FINAL, PITCH)
    assert f"]({FINAL})" in com and f"]({PITCH})" in com
    assert com.startswith("# t\nantes") and com.endswith("depois\n")
    sem = fe.preencher_readme(com, None, None)
    assert FINAL not in sem and fe.TEXTO_SEM_LINK in sem
    assert fe.preencher_readme(sem, None, None) == sem


def test_readme_sem_marcadores_falha():
    with pytest.raises(ValueError):
        fe.preencher_readme("# sem marcadores", FINAL, PITCH)


def test_readme_real_tem_os_marcadores():
    texto = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert fe.preencher_readme(texto, None, None) == texto
