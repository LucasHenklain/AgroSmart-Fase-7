from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from agrosmart.config import Config
from agrosmart.contratos import contratos_ativos
from agrosmart.simuladores import ORDEM, simular
from agrosmart.simuladores.base import Contexto
from agrosmart.simuladores.defeitos import CATALOGO, POR_ID
from agrosmart.simuladores.modelo import CULTURAS, eto_hargreaves, radiacao_extraterrestre_mm
from agrosmart.simuladores.motor import simular_trajetoria
from agrosmart.simuladores.pdf import gerar_pdf
from tests.conftest import fabricar_config

DIAS_TESTE = 6


@pytest.fixture(scope="module")
def simulacao(tmp_path_factory: pytest.TempPathFactory) -> tuple[Config, dict]:
    cfg = fabricar_config(tmp_path_factory.mktemp("sim"), dias_historico=DIAS_TESTE)
    resumo = simular(cfg)
    return cfg, resumo


def _assinaturas(raiz: Path) -> dict[str, str]:
    return {
        p.relative_to(raiz).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(raiz.rglob("*"))
        if p.is_file() and p.name != ".gitkeep"
    }


# ---------------------------------------------------------------------------
# Determinismo
# ---------------------------------------------------------------------------


def test_mesma_semente_produz_os_mesmos_bytes(tmp_path: Path) -> None:
    a = fabricar_config(tmp_path / "a", dias_historico=3)
    b = fabricar_config(tmp_path / "b", dias_historico=3)
    simular(a)
    simular(b)
    assinaturas_a = _assinaturas(a.dir_landing)
    assinaturas_b = _assinaturas(b.dir_landing)
    assert assinaturas_a, "a simulação não gerou arquivos"
    assert assinaturas_a == assinaturas_b


def test_semente_diferente_produz_dados_diferentes(tmp_path: Path) -> None:
    a = fabricar_config(tmp_path / "a", dias_historico=2, seed=1)
    b = fabricar_config(tmp_path / "b", dias_historico=2, seed=2)
    simular(a)
    simular(b)
    assert _assinaturas(a.dir_landing / "clima") != _assinaturas(b.dir_landing / "clima")


def test_simular_uma_fonte_gera_exatamente_o_mesmo_que_o_conjunto(tmp_path: Path) -> None:
    """A trajetória é única: filtrar fontes não pode alterar os sensores."""
    tudo = fabricar_config(tmp_path / "tudo", dias_historico=2)
    so_sensores = fabricar_config(tmp_path / "so", dias_historico=2)
    simular(tudo)
    simular(so_sensores, fontes=["sensores"])
    assert _assinaturas(tudo.dir_landing / "sensores") == _assinaturas(
        so_sensores.dir_landing / "sensores"
    )
    assert not (so_sensores.dir_landing / "clima").exists() or not any(
        (so_sensores.dir_landing / "clima").iterdir()
    )


# ---------------------------------------------------------------------------
# Modelo físico
# ---------------------------------------------------------------------------


def test_umidade_permanece_entre_ponto_de_murcha_e_capacidade_de_campo(
    simulacao: tuple[Config, dict],
) -> None:
    cfg, _ = simulacao
    ctx = Contexto(cfg)
    traj = simular_trajetoria(ctx)
    for (talhao_id, _), dia in traj.talhoes.items():
        talhao = ctx.talhao(talhao_id)
        assert talhao.ponto_murcha_pct - 1e-6 <= dia.theta_fim <= talhao.capacidade_campo_pct + 1e-6
        assert 0.0 <= dia.fad <= 1.0
        assert 0.2 <= dia.ndvi <= 0.95


def test_talhao_de_sequeiro_nunca_recebe_irrigacao(simulacao: tuple[Config, dict]) -> None:
    cfg, _ = simulacao
    ctx = Contexto(cfg)
    traj = simular_trajetoria(ctx)
    sequeiro = {t.id for t in ctx.talhoes if not t.irrigado}
    assert sequeiro, "o catálogo precisa ter um talhão de sequeiro para a trava agronômica"
    for op in traj.operacoes:
        if op.tipo_operacao == "irrigacao":
            assert op.talhao_id not in sequeiro


def test_irrigacao_acontece_quando_a_agua_disponivel_cai(simulacao: tuple[Config, dict]) -> None:
    cfg, _ = simulacao
    ctx = Contexto(cfg)
    traj = simular_trajetoria(ctx)
    irrigacoes = [
        op for op in traj.operacoes if op.tipo_operacao == "irrigacao" and not op.correcao
    ]
    for op in irrigacoes:
        estado = traj.talhoes[(op.talhao_id, op.data)]
        assert estado.irrigacao == op.quantidade
        assert estado.theta_fim >= estado.theta_ini  # irrigar sempre eleva ou mantém a umidade


def test_clima_e_fisicamente_plausivel(simulacao: tuple[Config, dict]) -> None:
    cfg, _ = simulacao
    traj = simular_trajetoria(Contexto(cfg))
    for dia in traj.clima.values():
        assert dia.tmin < dia.tmax
        assert 0 < dia.eto < 12
        assert 0 <= dia.chuva < 200
        assert 30 <= dia.ur <= 98


def test_hargreaves_da_valores_da_ordem_esperada_para_agosto_em_sp() -> None:
    ra = radiacao_extraterrestre_mm(-22.9, 240)
    assert 9.0 < ra < 12.5
    eto = eto_hargreaves(15.0, 29.0, ra)
    assert 3.0 < eto < 5.5


def test_todas_as_culturas_do_contrato_tem_modelo() -> None:
    dominio = next(
        r for r in contratos_ativos()["erp_talhoes"].qualidade if r.id == "TLH-06"
    ).valores
    assert dominio is not None
    assert set(dominio) <= set(CULTURAS)


def test_sensor_com_falha_de_bateria_cala_e_gera_manutencao(tmp_path: Path) -> None:
    cfg = fabricar_config(tmp_path, dias_historico=30)
    ctx = Contexto(cfg)
    traj = simular_trajetoria(ctx)
    calou, manutencao = traj.silencio["SNS-0302"]
    assert calou is not None and manutencao is not None
    assert manutencao > calou
    assert any(
        op.tipo_operacao == "manutencao" and "SNS-0302" in (op.observacao or "")
        for op in traj.operacoes
    )
    assert traj.bateria["SNS-0302"][calou] is None
    assert traj.bateria["SNS-0302"][manutencao] == 100.0


# ---------------------------------------------------------------------------
# Arquivos gerados
# ---------------------------------------------------------------------------


def test_gera_todas_as_fontes(simulacao: tuple[Config, dict]) -> None:
    _, resumo = simulacao
    assert set(resumo["fontes"]) == set(ORDEM)
    for fonte, contagem in resumo["fontes"].items():
        assert contagem["arquivos"] > 0, fonte


def test_csv_do_erp_tem_exatamente_o_cabecalho_do_contrato(simulacao: tuple[Config, dict]) -> None:
    cfg, _ = simulacao
    for fonte, prefixo in (("erp_talhoes", "talhoes_"), ("erp_operacoes", "operacoes_")):
        arquivo = sorted((cfg.dir_landing / "erp").glob(f"{prefixo}*.csv"))[0]
        with arquivo.open(encoding="utf-8", newline="") as f:
            cabecalho = next(csv.reader(f))
        assert cabecalho == contratos_ativos()[fonte].campos_de_origem


def test_sensores_um_arquivo_por_15_minutos(simulacao: tuple[Config, dict]) -> None:
    cfg, resumo = simulacao
    arquivos = list((cfg.dir_landing / "sensores").rglob("*.jsonl"))
    # 96 por dia da janela + os 12 intervalos do dia seguinte anteriores às 03h00 (o "agora")
    assert len(arquivos) == DIAS_TESTE * 96 + 12
    assert resumo["fontes"]["sensores"]["registros"] > DIAS_TESTE * 96 * 10


def test_leituras_de_sensores_respeitam_o_schema(simulacao: tuple[Config, dict]) -> None:
    cfg, _ = simulacao
    permitidas = set(contratos_ativos()["sensores"].campos_de_origem)
    arquivo = sorted((cfg.dir_landing / "sensores").rglob("*.jsonl"))[10]
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        registro = json.loads(linha)
        assert set(registro) <= permitidas


def test_duplicatas_nos_sensores_sao_exatamente_as_injetadas(
    simulacao: tuple[Config, dict],
) -> None:
    cfg, resumo = simulacao
    linhas: Counter[str] = Counter()
    for arquivo in (cfg.dir_landing / "sensores").rglob("*.jsonl"):
        linhas.update(arquivo.read_text(encoding="utf-8").splitlines())
    duplicadas = sum(n - 1 for n in linhas.values() if n > 1)
    assert duplicadas == resumo["defeitos_por_tipo"].get("SEN-DUP", 0)


def test_defeitos_de_quantidade_fixa_sao_injetados_na_quantidade_declarada(
    simulacao: tuple[Config, dict],
) -> None:
    _, resumo = simulacao
    por_tipo = resumo["defeitos_por_tipo"]
    for tipo in CATALOGO:
        if tipo.quantidade is not None:
            assert por_tipo.get(tipo.id, 0) == tipo.quantidade, tipo.id


def test_todo_defeito_registrado_aponta_para_uma_regra_existente(
    simulacao: tuple[Config, dict],
) -> None:
    _, resumo = simulacao
    regras = {r.id for c in contratos_ativos().values() for r in c.qualidade}
    for defeito in resumo["defeitos"]:
        assert defeito["regra"] in regras
        assert POR_ID[defeito["tipo"]].fonte == defeito["fonte"]


def test_clima_no_envelope_da_api(simulacao: tuple[Config, dict]) -> None:
    cfg, _ = simulacao
    arquivo = sorted((cfg.dir_landing / "clima").glob("*.json"))[0]
    envelope = json.loads(arquivo.read_text(encoding="utf-8"))
    assert {"daily", "daily_units", "estacao_id", "fazenda_id"} <= set(envelope)
    assert len(envelope["daily"]["time"]) == len(envelope["daily"]["precipitation_sum"])


def test_imagens_sao_jpeg_com_sidecar(simulacao: tuple[Config, dict]) -> None:
    cfg, _ = simulacao
    sidecars = sorted((cfg.dir_landing / "imagens").glob("*.json"))
    assert sidecars
    for sidecar in sidecars:
        binario = sidecar.with_suffix(".jpg")
        assert binario.exists()
        assert binario.read_bytes()[:3] == b"\xff\xd8\xff"
        conteudo = json.loads(sidecar.read_text(encoding="utf-8"))
        assert conteudo["plataforma"] in {"drone", "satelite"}


def test_laudos_sao_pdf_validos(simulacao: tuple[Config, dict]) -> None:
    cfg, _ = simulacao
    pdfs = sorted((cfg.dir_landing / "laudos").glob("*.pdf"))
    assert pdfs
    for pdf in pdfs:
        dados = pdf.read_bytes()
        assert dados.startswith(b"%PDF-1.4")
        assert dados.rstrip().endswith(b"%%EOF")
        assert pdf.with_suffix(".json").exists()


def test_pdf_minimo_e_deterministico_e_referencia_offsets_corretos() -> None:
    a = gerar_pdf(["linha um", "acentuação: ção"], titulo="Teste")
    b = gerar_pdf(["linha um", "acentuação: ção"], titulo="Teste")
    assert a == b
    inicio_xref = int(a.split(b"startxref\n")[1].split(b"\n")[0])
    assert a[inicio_xref:].startswith(b"xref")


def test_resumo_persistido_em_outputs(simulacao: tuple[Config, dict]) -> None:
    cfg, resumo = simulacao
    arquivo = cfg.dir_outputs / "simulacao.json"
    assert arquivo.exists()
    persistido = json.loads(arquivo.read_text(encoding="utf-8"))
    assert persistido["defeitos_por_tipo"] == resumo["defeitos_por_tipo"]
    assert persistido["trajetoria"]["laudos"] == len(
        list((cfg.dir_landing / "laudos").glob("*.pdf"))
    )
