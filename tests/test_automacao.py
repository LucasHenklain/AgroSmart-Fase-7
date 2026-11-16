"""Automação: decisões por cenário, esquema do plano, idempotência, publicação MQTT e CLI."""

from __future__ import annotations

import json
import socket
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from typer.testing import CliRunner

from agrosmart import agronomia
from agrosmart.automacao.atuadores import (
    ErroPublicacao,
    PublicadorEmMemoria,
    PublicadorPaho,
    publicar_plano,
)
from agrosmart.automacao.motor import (
    FONTE,
    TAXA_APLICACAO_MM_H,
    Plano,
    TabelasGold,
    data_de_referencia,
    executar,
    gerar_plano,
    para_markdown,
    salvar_plano,
)

HOJE = date(2026, 9, 1)
INSTANTE = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
TALHAO = "TLH-01"
SENSORES = ("SNS-0101", "SNS-0102")


# ---------------------------------------------------------------------------
# Fixtures em memória: o gold de um talhão, cenário a cenário
# ---------------------------------------------------------------------------


def _tabelas(
    *,
    fad: float = 0.70,
    deficit: float = 10.0,
    umidade: float | None = 25.0,
    irrigado: bool = True,
    estadio: str = "V8",
    sistema: str = "pivo_central",
    bateria: tuple[float, float] = (90.0, 92.0),
    rssi: tuple[float, float] = (-70.0, -72.0),
    fracas: tuple[int, int] = (0, 0),
    dias_leitura: tuple[int, int] = (31, 31),
    cobertura: tuple[float, ...] = (100.0,) * 7,
    ur: tuple[float, ...] = (60.0,) * 7,
    temp: tuple[tuple[float, float], ...] = ((15.0, 30.0),) * 7,
) -> TabelasGold:
    """Um talhão com 14 dias de balanço (o último com os valores pedidos) e 2 sensores."""
    dias = [HOJE - timedelta(days=13 - i) for i in range(14)]
    estresse = fad < agronomia.FRACAO_ESTRESSE
    balanco = pl.DataFrame(
        {
            "talhao_id": [TALHAO] * 14,
            "dia": dias,
            "estadio": [estadio] * 14,
            "umidade_media_pct": [umidade] * 14,
            "fracao_agua_disponivel": [None if umidade is None else fad] * 14,
            "deficit_mm": [None if umidade is None else deficit] * 14,
            "estresse_hidrico": [estresse] * 14,
            "recomenda_irrigar": [irrigado and fad < agronomia.FRACAO_IRRIGACAO] * 14,
            "irrigado": [irrigado] * 14,
        },
        schema_overrides={
            "umidade_media_pct": pl.Float64,
            "fracao_agua_disponivel": pl.Float64,
            "deficit_mm": pl.Float64,
        },
    )
    semana = [HOJE - timedelta(days=7 - i) for i in range(7)]
    leituras = pl.DataFrame(
        {
            "talhao_id": [TALHAO] * 7,
            "dia": semana,
            "temp_ar_min_c": [t[0] for t in temp],
            "temp_ar_max_c": [t[1] for t in temp],
            "umidade_ar_media_pct": list(ur),
            "cobertura_pct": list(cobertura),
        }
    )
    sensores = pl.DataFrame(
        {
            "sensor_id": list(SENSORES),
            "talhao_id": [TALHAO] * 2,
            "ultima_leitura": [datetime(2026, 9, 1, 2, 45, tzinfo=UTC)] * 2,
            "leituras": [2800, 2800],
            "dias_com_leitura": list(dias_leitura),
            "bateria_ultima_pct": list(bateria),
            "rssi_medio_dbm": list(rssi),
            "leituras_sinal_fraco": list(fracas),
        }
    )
    talhoes = pl.DataFrame(
        {
            "talhao_id": [TALHAO],
            "cultura": ["milho"],
            "area_ha": [18.6],
            "sistema_irrigacao": [sistema],
            "irrigado": [irrigado],
        }
    )
    return TabelasGold(balanco=balanco, leituras=leituras, sensores=sensores, talhoes=talhoes)


def _plano(**cenario: Any) -> Plano:
    return gerar_plano(_tabelas(**cenario), gerado_em=INSTANTE)


def _tipos(plano: Plano) -> list[str]:
    return [a.tipo for a in plano.acoes]


# ---------------------------------------------------------------------------
# Decisões por cenário
# ---------------------------------------------------------------------------


def test_talhao_seco_irriga_com_lamina_minutos_e_comando_mqtt() -> None:
    plano = _plano(fad=0.40, deficit=26.5)
    assert _tipos(plano) == ["irrigar"]
    acao = plano.acoes[0]
    # mesma lâmina do Context Pack: 85 % do déficit, múltiplos de 0,5 mm
    assert acao.parametros["lamina_mm"] == 22.5
    assert acao.parametros["minutos"] == 170  # 22,5 mm a 8 mm/h = 168,75 → múltiplo de 5
    assert acao.parametros["janela"] == "04:00-07:00"
    assert acao.comando_mqtt is not None
    assert acao.comando_mqtt.topico == "agrosmart/atuadores/TLH-01/irrigacao"
    assert acao.comando_mqtt.payload == {"acao": "ligar", "minutos": 170}
    assert acao.status == "simulado"
    assert acao.evidencias == ["fato_balanco_hidrico:TLH-01:2026-09-01"]
    assert "0,400" in acao.motivo and "0,50" in acao.motivo
    assert acao.prioridade == "media"  # V8 não é estádio crítico para água
    assert plano.talhoes[0].risco == "atencao"


def test_estadio_critico_ou_estresse_sobe_prioridade_e_risco() -> None:
    critico = _plano(fad=0.40, deficit=26.5, estadio="VT")
    assert critico.acoes[0].prioridade == "alta"
    assert critico.talhoes[0].risco == "critico"
    estresse = _plano(fad=0.20, deficit=40.0)
    assert estresse.acoes[0].prioridade == "alta"
    assert estresse.acoes[0].parametros["lamina_mm"] == 30.0  # teto de 30 mm
    assert estresse.talhoes[0].risco == "critico"
    assert "estresse" in estresse.acoes[0].motivo


@pytest.mark.parametrize("sistema", sorted(TAXA_APLICACAO_MM_H))
def test_minutos_seguem_a_taxa_do_sistema(sistema: str) -> None:
    plano = _plano(fad=0.40, deficit=20.0, sistema=sistema)
    minutos = plano.acoes[0].parametros["minutos"]
    esperado = 17.0 / TAXA_APLICACAO_MM_H[sistema] * 60
    assert minutos % 5 == 0 and esperado <= minutos < esperado + 5


def test_sequeiro_em_estresse_nunca_irriga_mas_e_critico() -> None:
    plano = _plano(fad=0.05, deficit=50.0, irrigado=False, sistema="sequeiro")
    assert plano.acoes == []
    talhao = plano.talhoes[0]
    assert talhao.risco == "critico"
    assert talhao.indicadores["situacao_hidrica"] == "sequeiro_estresse"
    assert plano.resumo.por_tipo == {"nenhuma": 1}


def test_bateria_baixa_pede_troca_do_sensor_certo() -> None:
    plano = _plano(bateria=(10.0, 92.0))
    assert _tipos(plano) == ["trocar_bateria"]
    acao = plano.acoes[0]
    assert acao.sensor_id == "SNS-0101"
    assert acao.prioridade == "media"
    assert acao.parametros == {
        "bateria_pct": 10.0,
        "limiar_pct": agronomia.BATERIA_MANUTENCAO_PCT,
        "janela": "próxima entrada no talhão",
    }
    assert acao.evidencias == ["dim_sensor:SNS-0101:2026-09-01"]
    assert acao.comando_mqtt is None
    assert plano.talhoes[0].risco == "atencao"


def test_talhao_normal_nao_gera_acao() -> None:
    plano = _plano()
    assert plano.acoes == []
    assert plano.talhoes[0].risco == "normal"
    assert plano.talhoes[0].indicadores["situacao_hidrica"] == "ok"
    assert plano.resumo.model_dump() == {
        "talhoes": 1,
        "acoes": 0,
        "por_tipo": {"nenhuma": 1},
        "por_prioridade": {"alta": 0, "media": 0, "baixa": 0},
    }


def test_fracao_perto_do_limiar_e_atencao_sem_acao() -> None:
    plano = _plano(fad=0.55)
    assert plano.acoes == []
    assert plano.talhoes[0].risco == "atencao"
    assert plano.talhoes[0].indicadores["situacao_hidrica"] == "programar"


def test_alerta_fitossanitario_exige_ur_alta_e_temperatura_na_faixa() -> None:
    ur_alta = (85.0, 86.0, 84.0, 60.0, 60.0, 60.0, 60.0)
    com_risco = _plano(ur=ur_alta)  # temperatura média (15+30)/2 = 22,5 °C: na faixa
    assert _tipos(com_risco) == ["alerta_fitossanitario"]
    acao = com_risco.acoes[0]
    assert acao.parametros["dias_ur_alta_7d"] == 3
    assert acao.parametros["temp_media_7d_c"] == 22.5
    assert len(acao.evidencias) == 3
    assert acao.evidencias[0] == "fato_leitura_diaria_talhao:TLH-01:2026-08-25"
    assert acao.comando_mqtt is None

    quente = _plano(ur=ur_alta, temp=((25.0, 40.0),) * 7)  # 32,5 °C: fora da faixa
    assert quente.acoes == []
    poucos_dias = _plano(ur=(85.0, 86.0, 60.0, 60.0, 60.0, 60.0, 60.0))
    assert poucos_dias.acoes == []


def test_sinal_fraco_pede_inspecao_do_sensor() -> None:
    por_rssi = _plano(rssi=(-70.0, -97.0))
    assert [(a.tipo, a.sensor_id, a.prioridade) for a in por_rssi.acoes] == [
        ("inspecionar_sensor", "SNS-0102", "baixa")
    ]
    por_fracao = _plano(fracas=(300, 0))  # 300/2800 = 10,7 % > 5 %
    assert [(a.tipo, a.sensor_id) for a in por_fracao.acoes] == [("inspecionar_sensor", "SNS-0101")]
    assert por_fracao.acoes[0].parametros["fracao_sinal_fraco"] == 0.107


def test_cobertura_baixa_pede_inspecao_e_aponta_o_sensor_que_calou() -> None:
    plano = _plano(cobertura=(50.0,) + (100.0,) * 6, dias_leitura=(31, 26))
    assert _tipos(plano) == ["inspecionar_sensor"]
    acao = plano.acoes[0]
    assert acao.sensor_id == "SNS-0102" and acao.prioridade == "media"
    assert acao.parametros["motivo_tecnico"] == "cobertura_baixa"
    assert acao.evidencias == ["fato_leitura_diaria_talhao:TLH-01:2026-08-25"]
    assert plano.talhoes[0].indicadores["cobertura_min_7d_pct"] == 50.0


def test_sem_leitura_de_umidade_vira_inspecao_alta() -> None:
    plano = _plano(umidade=None)
    assert [(a.tipo, a.prioridade, a.sensor_id) for a in plano.acoes] == [
        ("inspecionar_sensor", "alta", None)
    ]
    assert plano.talhoes[0].indicadores["situacao_hidrica"] == "sem_leitura"
    assert plano.talhoes[0].risco == "atencao"


def test_data_de_referencia_e_a_ultima_com_umidade_ou_a_pedida() -> None:
    tabelas = _tabelas()
    assert data_de_referencia(tabelas) == HOJE
    anterior = HOJE - timedelta(days=3)
    plano = gerar_plano(tabelas, data=anterior, gerado_em=INSTANTE)
    assert plano.data_ref == anterior.isoformat()
    assert plano.talhoes[0].indicadores["dia_leitura"] == anterior.isoformat()


# ---------------------------------------------------------------------------
# Esquema, idempotência e saídas
# ---------------------------------------------------------------------------


def test_json_segue_exatamente_o_esquema_combinado_com_o_dashboard() -> None:
    plano = _plano(fad=0.40, deficit=26.5, bateria=(10.0, 92.0))
    dados = json.loads(plano.para_json())
    assert list(dados) == ["gerado_em", "data_ref", "fonte", "resumo", "talhoes", "acoes"]
    assert (
        dados["fonte"]
        == FONTE
        == ("gold.fato_balanco_hidrico + fato_leitura_diaria_talhao + dim_sensor")
    )
    assert dados["gerado_em"] == "2026-09-01T03:00:00+00:00" and dados["data_ref"] == "2026-09-01"
    assert list(dados["resumo"]) == ["talhoes", "acoes", "por_tipo", "por_prioridade"]
    assert list(dados["talhoes"][0]) == ["talhao_id", "risco", "indicadores"]
    assert [a["id"] for a in dados["acoes"]] == ["ACT-0001", "ACT-0002"]
    for acao in dados["acoes"]:
        assert list(acao) == [
            "id",
            "talhao_id",
            "sensor_id",
            "tipo",
            "prioridade",
            "parametros",
            "motivo",
            "evidencias",
            "comando_mqtt",
            "status",
        ]
        assert acao["status"] == "simulado"
        assert all(":" in e for e in acao["evidencias"])
    irrigar, bateria = dados["acoes"]
    assert irrigar["comando_mqtt"] == {
        "topico": "agrosmart/atuadores/TLH-01/irrigacao",
        "payload": {"acao": "ligar", "minutos": 170},
    }
    assert bateria["comando_mqtt"] is None and bateria["sensor_id"] == "SNS-0101"
    assert Plano.model_validate(dados).para_dict() == dados  # round-trip sem perda


def test_plano_e_deterministico_e_idempotente(tmp_path: Path) -> None:
    from tests.conftest import fabricar_config

    primeiro = _plano(fad=0.40, deficit=26.5, rssi=(-70.0, -97.0))
    segundo = _plano(fad=0.40, deficit=26.5, rssi=(-70.0, -97.0))
    assert primeiro.para_dict() == segundo.para_dict()
    outro_instante = gerar_plano(
        _tabelas(fad=0.40, deficit=26.5, rssi=(-70.0, -97.0)),
        gerado_em=INSTANTE + timedelta(hours=1),
    )
    sem_instante = {k: v for k, v in outro_instante.para_dict().items() if k != "gerado_em"}
    assert sem_instante == {k: v for k, v in primeiro.para_dict().items() if k != "gerado_em"}

    cfg = fabricar_config(tmp_path)
    caminhos = salvar_plano(primeiro, cfg)
    conteudo = [c.read_text(encoding="utf-8") for c in caminhos]
    assert salvar_plano(segundo, cfg) == caminhos  # mesmos arquivos, sobrescritos
    assert [c.read_text(encoding="utf-8") for c in caminhos] == conteudo
    assert caminhos[0].name == "acoes_2026-09-01.json" and caminhos[1].suffix == ".md"


def test_markdown_tem_tabela_por_talhao_e_por_acao() -> None:
    plano = _plano(fad=0.40, deficit=26.5, bateria=(10.0, 92.0))
    md = para_markdown(plano)
    assert md.startswith("# Plano de ações · 2026-09-01")
    assert "## Talhões" in md and "## Ações" in md
    assert "| TLH-01 | milho · pivo_central | atenção |" in md
    assert "| ACT-0001 | TLH-01 | — | irrigar | media | 22,5 mm · 170 min · 04:00-07:00 |" in md
    assert "| ACT-0002 | TLH-01 | SNS-0101 | trocar_bateria |" in md
    assert "`agrosmart/atuadores/TLH-01/irrigacao`" in md
    assert "Nenhuma ação" in para_markdown(_plano())


# ---------------------------------------------------------------------------
# Publicação MQTT
# ---------------------------------------------------------------------------


def test_publicacao_com_cliente_dublê_marca_publicado_e_nao_repete() -> None:
    plano = _plano(fad=0.40, deficit=26.5, bateria=(10.0, 92.0))
    dublê = PublicadorEmMemoria()
    resultado = publicar_plano(plano, dublê)
    assert resultado == {"solicitada": True, "publicadas": 1, "falhas": []}
    assert dublê.publicados == [
        ("agrosmart/atuadores/TLH-01/irrigacao", {"acao": "ligar", "minutos": 170})
    ]
    assert [a.status for a in plano.acoes] == ["publicado", "simulado"]  # bateria não tem atuador
    assert publicar_plano(plano, dublê)["publicadas"] == 0  # nada pendente: não republica
    assert len(dublê.publicados) == 1


def test_broker_que_nao_confirma_deixa_a_acao_simulada() -> None:
    plano = _plano(fad=0.40, deficit=26.5)
    dublê = PublicadorEmMemoria(falhar_em={"agrosmart/atuadores/TLH-01/irrigacao"})
    resultado = publicar_plano(plano, dublê)
    assert resultado["publicadas"] == 0
    assert resultado["falhas"][0]["id"] == "ACT-0001"
    assert plano.acoes[0].status == "simulado"
    assert dublê.publicados == []
    with pytest.raises(ErroPublicacao):
        dublê.publicar("agrosmart/atuadores/TLH-01/irrigacao", {"acao": "ligar", "minutos": 1})


def test_broker_fora_do_ar_nao_custa_o_plano(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem broker, o plano é gravado com tudo `simulado` e cada comando aparece em falhas."""
    from agrosmart.automacao import motor
    from agrosmart.storage import criar_armazenamento
    from tests.conftest import fabricar_config

    cfg = fabricar_config(tmp_path)
    monkeypatch.setattr(motor, "carregar_tabelas", lambda _lake: _tabelas(fad=0.40, deficit=26.5))

    def _sem_broker(self: PublicadorPaho) -> None:
        raise ErroPublicacao(f"broker MQTT {self.broker}:{self.porta} inalcançável")

    monkeypatch.setattr(PublicadorPaho, "conectar", _sem_broker)
    saida = executar(
        cfg,
        armazenamento=criar_armazenamento(cfg),
        publicar=True,
        broker="broker-inexistente",
        porta=1,
        copiloto=None,
    )
    assert saida["publicacao"]["publicadas"] == 0
    assert saida["publicacao"]["falhas"][0]["id"] == "ACT-0001"
    assert "inalcançável" in saida["publicacao"]["falhas"][0]["erro"]
    assert saida["plano"].acoes[0].status == "simulado"
    assert saida["caminhos"][0].exists()  # o plano foi gravado mesmo assim


def _broker_local_disponivel(porta: int = 1883) -> bool:
    try:
        with socket.create_connection(("localhost", porta), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _broker_local_disponivel(), reason="sem broker MQTT em localhost:1883")
def test_publicador_paho_entrega_no_broker_local() -> None:
    with PublicadorPaho("localhost", 1883, client_id="agrosmart-teste") as paho:
        paho.publicar("agrosmart/atuadores/TLH-99/irrigacao", {"acao": "ligar", "minutos": 1})


# ---------------------------------------------------------------------------
# Integração: o lake de teste de ponta a ponta, mais a CLI
# ---------------------------------------------------------------------------


def test_executar_sobre_o_lake_de_teste(lake_processado: dict) -> None:
    from agrosmart.ia.contexto import abrir_gold, construir_context_pack

    cfg, lake = lake_processado["cfg"], lake_processado["lake"]
    dublê = PublicadorEmMemoria()
    saida = executar(cfg, armazenamento=lake, publicar=True, publicador=dublê)
    plano: Plano = saida["plano"]
    assert plano.resumo.talhoes == 6 and plano.data_ref == "2026-09-01"
    assert plano.fonte == FONTE

    # as decisões de irrigar coincidem com o que o Context Pack (Fase 5) já calculava
    con = abrir_gold(cfg, lake)
    try:
        for talhao in plano.talhoes:
            pack = construir_context_pack(talhao.talhao_id, con=con, config=cfg)
            irrigar = [a for a in plano.acoes_de(talhao.talhao_id) if a.tipo == "irrigar"]
            if pack.hidrico.recomenda_irrigar_hoje and pack.hidrico.lamina_sugerida_mm:
                assert len(irrigar) == 1
                assert irrigar[0].parametros["lamina_mm"] == pack.hidrico.lamina_sugerida_mm
            else:
                assert irrigar == []
            if not pack.talhao.irrigado:
                assert irrigar == []
                assert talhao.indicadores["situacao_hidrica"].startswith("sequeiro")
            # o copiloto explica cada talhão com o motor de regras (sem chave de API)
            assert talhao.copiloto is not None
            assert talhao.copiloto["motor"] == "regras" and talhao.copiloto["guardrails_aprovados"]
            assert talhao.copiloto["recomendacoes"]
    finally:
        con.close()

    publicados = [a for a in plano.acoes if a.status == "publicado"]
    assert len(publicados) == len(dublê.publicados) == saida["publicacao"]["publicadas"]
    assert all(a.tipo == "irrigar" for a in publicados)

    json_path, md_path = saida["caminhos"]
    assert json_path == cfg.dir_outputs / "acoes" / "acoes_2026-09-01.json"
    gravado = json.loads(json_path.read_text(encoding="utf-8"))
    assert gravado["resumo"]["acoes"] == plano.resumo.acoes
    assert "copiloto" in gravado["talhoes"][0]
    assert md_path.read_text(encoding="utf-8").startswith("# Plano de ações · 2026-09-01")
    eventos = (cfg.dir_logs / "eventos.jsonl").read_text(encoding="utf-8")
    assert '"etapa": "automacao"' in eventos and '"acao": "plano_gerado"' in eventos


def test_cli_dry_run_mostra_o_plano_sem_gravar(
    lake_processado: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agrosmart.pipeline as pipeline

    cfg = lake_processado["cfg"]
    monkeypatch.setattr(pipeline, "carregar_config", lambda: cfg)
    destino = cfg.dir_outputs / "acoes" / "acoes_2026-09-01.json"
    antes = destino.read_text(encoding="utf-8") if destino.exists() else None
    resultado = CliRunner().invoke(
        pipeline.app, ["automacao", "--dry-run", "--motor", "nenhum", "--data", "2026-09-01"]
    )
    assert resultado.exit_code == 0, resultado.output
    assert "Plano de ações" in resultado.output and "dry-run" in resultado.output
    depois = destino.read_text(encoding="utf-8") if destino.exists() else None
    assert depois == antes  # dry-run não grava nem sobrescreve
