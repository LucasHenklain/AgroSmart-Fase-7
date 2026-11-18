from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

import agrosmart.empacotar as modulo
from agrosmart.config import RAIZ_PROJETO
from agrosmart.empacotar import (
    FASE_PADRAO,
    ErroEmpacotamento,
    empacotar,
    nome_do_zip,
    normalizar_token,
    selecionar_arquivos,
    verificar_pre_requisitos,
    videos_da_entrega,
)
from tests.conftest import fabricar_config


def test_nome_do_zip_segue_o_padrao_da_plataforma() -> None:
    assert FASE_PADRAO == 7
    assert nome_do_zip("Lucas Moraes Henklain da Silva", "99350", "4ESOA") == (
        "lucasmoraeshenklaindasilva_99350_4ESOA_fase7_atividade.zip"
    )
    assert nome_do_zip("Lucas Moraes Henklain da Silva", "99350", "4ESOA", fase=5) == (
        "lucasmoraeshenklaindasilva_99350_4ESOA_fase5_atividade.zip"
    )
    # nome em minúsculas e sem acento; RM e turma como informados, sem espaço
    assert nome_do_zip("João Ávila-Neto", "RM 12345", "1tdsa", fase=5) == (
        "joaoavilaneto_RM12345_1tdsa_fase5_atividade.zip"
    )
    assert normalizar_token("João Ávila-Neto") == "joaoavilaneto"


def test_nome_invalido_e_recusado() -> None:
    with pytest.raises(ErroEmpacotamento, match="RM"):
        nome_do_zip("Lucas", "", "1TDSA")
    with pytest.raises(ErroEmpacotamento, match="fase"):
        nome_do_zip("Lucas", "RM1", "1TDSA", fase=8)


def test_selecao_inclui_artefatos_e_exclui_ambiente_e_lake() -> None:
    arquivos = [p.relative_to(RAIZ_PROJETO).as_posix() for p in selecionar_arquivos(RAIZ_PROJETO)]
    assert "README.md" in arquivos and "pyproject.toml" in arquivos
    assert any(a.startswith("contratos/") and a.endswith(".yaml") for a in arquivos)
    assert any(a.startswith("docs/adr/") for a in arquivos)
    assert any(a.startswith("src/agrosmart/ia/prompts/") for a in arquivos)
    assert any(a.startswith("data/exemplos/bronze/") for a in arquivos)
    assert any(a.startswith("data/exemplos/fase6/") for a in arquivos)
    assert any(a.startswith("docs/fase6/") for a in arquivos)
    assert not any(a.startswith(".venv") or "__pycache__" in a for a in arquivos)
    assert not any(a.startswith("landing/") or a.startswith("datalake/") for a in arquivos)
    assert not any(a.startswith("outputs/logs/") for a in arquivos)
    assert not any(a.endswith(".pyc") for a in arquivos)


def _projeto_minimo(raiz: Path) -> None:
    (raiz / "entrega").mkdir(parents=True)
    (raiz / "outputs" / "prints").mkdir(parents=True)
    (raiz / "README.md").write_text("# x", encoding="utf-8")


def test_video_entra_so_quando_esta_em_entrega(tmp_path: Path) -> None:
    raiz = tmp_path / "projeto"
    _projeto_minimo(raiz)
    (raiz / "entrega" / "apresentacao.mp4").write_bytes(b"\x00" * 10)
    (raiz / "outputs" / "prints" / "teste.mp4").write_bytes(b"\x00" * 10)
    escolhidos = {p.relative_to(raiz).as_posix() for p in selecionar_arquivos(raiz)}
    assert "entrega/apresentacao.mp4" in escolhidos
    assert "outputs/prints/teste.mp4" not in escolhidos


def test_video_de_outra_fase_fica_fora(tmp_path: Path) -> None:
    raiz = tmp_path / "projeto"
    _projeto_minimo(raiz)
    (raiz / "entrega" / "apresentacao-agrosmart-fase5-4ESOA.mp4").write_bytes(b"\x00" * 10)
    (raiz / "entrega" / "apresentacao-agrosmart-fase6-4ESOA.mp4").write_bytes(b"\x00" * 10)
    fase6 = {p.relative_to(raiz).as_posix() for p in selecionar_arquivos(raiz, fase=6)}
    fase5 = {p.relative_to(raiz).as_posix() for p in selecionar_arquivos(raiz, fase=5)}
    assert "entrega/apresentacao-agrosmart-fase6-4ESOA.mp4" in fase6
    assert "entrega/apresentacao-agrosmart-fase5-4ESOA.mp4" not in fase6
    assert "entrega/apresentacao-agrosmart-fase5-4ESOA.mp4" in fase5
    assert "entrega/apresentacao-agrosmart-fase6-4ESOA.mp4" not in fase5


def test_video_acima_do_limite_fica_fora_com_aviso(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raiz = tmp_path / "projeto"
    _projeto_minimo(raiz)
    (raiz / "entrega" / "apresentacao-fase6.mp4").write_bytes(b"\x00" * 10)
    monkeypatch.setattr(modulo, "LIMITE_VIDEO_MB", 0)
    assert videos_da_entrega(raiz, 6) == []
    escolhidos = {p.relative_to(raiz).as_posix() for p in selecionar_arquivos(raiz, fase=6)}
    assert "entrega/apresentacao-fase6.mp4" not in escolhidos
    avisos = verificar_pre_requisitos(raiz, 6)
    assert any("ficou fora do zip" in a for a in avisos)
    assert any("vídeo ausente" in a for a in avisos)  # sem link, o vídeo grande não conta


def test_fase6_inclui_firmware_e_acoes(tmp_path: Path) -> None:
    raiz = tmp_path / "projeto"
    _projeto_minimo(raiz)
    (raiz / "firmware" / "esp32").mkdir(parents=True)
    (raiz / "firmware" / "esp32" / "main.ino").write_text("// esp32", encoding="utf-8")
    (raiz / "outputs" / "acoes").mkdir(parents=True)
    (raiz / "outputs" / "acoes" / "acoes_2026-10-08.json").write_text("{}", encoding="utf-8")
    fase6 = {p.relative_to(raiz).as_posix() for p in selecionar_arquivos(raiz, fase=6)}
    fase5 = {p.relative_to(raiz).as_posix() for p in selecionar_arquivos(raiz, fase=5)}
    assert {"firmware/esp32/main.ino", "outputs/acoes/acoes_2026-10-08.json"} <= fase6
    assert not any(a.startswith(("firmware/", "outputs/acoes/")) for a in fase5)


def test_pre_requisitos_da_fase6_apontam_o_que_falta(tmp_path: Path) -> None:
    raiz = tmp_path / "projeto"
    _projeto_minimo(raiz)
    (raiz / "entrega" / "LINK_VIDEO.txt").write_text("[A PREENCHER]", encoding="utf-8")
    avisos = verificar_pre_requisitos(raiz, 6)
    assert any("vídeo ausente" in a for a in avisos)  # placeholder não conta como link
    assert any("docs/fase6/01-*.md ausente" in a for a in avisos)
    assert any("fase6-*.png" in a for a in avisos)
    assert any("outputs/acoes" in a for a in avisos)
    assert any("firmware/" in a for a in avisos)
    (raiz / "entrega" / "LINK_VIDEO.txt").write_text("https://youtu.be/x", encoding="utf-8")
    assert not any("vídeo ausente" in a for a in verificar_pre_requisitos(raiz, 6))


def test_empacota_testa_e_inventaria(tmp_path: Path) -> None:
    cfg = fabricar_config(RAIZ_PROJETO)
    resultado = empacotar("Equipe AgroSmart", "RM000", "1TDSA", config=cfg, pasta_saida=tmp_path)
    assert resultado.destino.name == "equipeagrosmart_RM000_1TDSA_fase7_atividade.zip"
    assert resultado.fase == 7
    assert resultado.integridade_ok
    assert resultado.bytes_zip < resultado.bytes_originais
    with zipfile.ZipFile(resultado.destino) as zf:
        nomes = zf.namelist()
        assert "ENTREGA.json" in nomes and "README.md" in nomes
        assert '"fase": 7' in zf.read("ENTREGA.json").decode("utf-8")
        assert zf.testzip() is None
    inventario = (tmp_path / "CONTEUDO_DO_ZIP.txt").read_text(encoding="utf-8")
    assert "README.md" in inventario and str(resultado.arquivos) in inventario


def test_empacota_fase5_mantem_o_nome_antigo(tmp_path: Path) -> None:
    cfg = fabricar_config(RAIZ_PROJETO)
    resultado = empacotar(
        "Equipe", "RM1", "T1", fase=5, config=cfg, pasta_saida=tmp_path, simular=True
    )
    assert resultado.destino.name == "equipe_RM1_T1_fase5_atividade.zip"
    assert resultado.fase == 5


def test_simulacao_nao_grava_nada(tmp_path: Path) -> None:
    cfg = fabricar_config(RAIZ_PROJETO)
    resultado = empacotar("Equipe", "RM1", "T1", config=cfg, pasta_saida=tmp_path, simular=True)
    assert resultado.arquivos > 50 and resultado.bytes_zip == 0
    assert not resultado.destino.exists()


def test_fase7_inclui_devsecops_processos_e_exclui_intermediarios(tmp_path: Path) -> None:
    raiz = tmp_path
    for relativo in (
        "Dockerfile",
        ".github/workflows/devsecops.yml",
        "outputs/processos/event_log.csv",
        "outputs/pipeline/ultimo/resumo.json",
        "outputs/plataforma/eventos.jsonl",
        "entrega/video_fase7/_work/slide_01.png",
        "entrega/video_fase7/telas/grafana.webm",
        "docs/fase7/01-arquitetura-final.md",
    ):
        caminho = raiz / relativo
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text("x", encoding="utf-8")
    fase7 = {p.relative_to(raiz).as_posix() for p in selecionar_arquivos(raiz, fase=7)}
    fase6 = {p.relative_to(raiz).as_posix() for p in selecionar_arquivos(raiz, fase=6)}
    for esperado in (
        "Dockerfile",
        ".github/workflows/devsecops.yml",
        "outputs/processos/event_log.csv",
        "outputs/pipeline/ultimo/resumo.json",
        "outputs/plataforma/eventos.jsonl",
        "docs/fase7/01-arquitetura-final.md",
    ):
        assert esperado in fase7
    assert "entrega/video_fase7/_work/slide_01.png" not in fase7
    assert "entrega/video_fase7/telas/grafana.webm" not in fase7
    assert "Dockerfile" not in fase6
    avisos = verificar_pre_requisitos(raiz, fase=7)
    assert any("event_log.xes" in a for a in avisos)
    assert any("pitch" in a for a in avisos)
