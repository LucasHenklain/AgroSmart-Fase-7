"""Coleta IoT por MQTT (Fase 6): gateway e emulador, sem broker.

Tudo aqui passa pelas funções puras — ``GatewayMqtt.processar`` e ``Frota.ciclo``
— com dublês de mensagem. O cliente MQTT de verdade só entra na demonstração
(``outputs/prints/fase6-02-*.txt``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agrosmart.config import Camada, Config
from agrosmart.contratos import contratos_ativos
from agrosmart.ingestao import ingerir
from agrosmart.ingestao.mqtt_gateway import (
    ErroGateway,
    EscritorJanelas,
    GatewayMqtt,
    caminho_da_janela,
    inicio_da_janela,
    instante_utc,
    normalizar_leitura,
    sensor_do_topico,
)
from agrosmart.ingestao.streaming import _PADRAO_NOME
from agrosmart.simuladores.dispositivo_mqtt import (
    CHAVES_PAYLOAD,
    TOPICO_ATUADOR,
    Frota,
)
from agrosmart.simuladores.sensores import instante_do_arquivo
from agrosmart.storage import criar_armazenamento

CONTRATO = contratos_ativos()["sensores"]
CAMPOS = CONTRATO.campos_de_origem
INSTANTE = datetime(2026, 10, 7, 14, 32, 17, tzinfo=UTC)


def _payload(**sobrescritas: object) -> dict[str, object]:
    """O payload do firmware: contrato v1 + ``luminosidade_lux`` (fora do contrato)."""
    base: dict[str, object] = {
        "sensor_id": "SNS-0301",
        "talhao_id": "TLH-03",
        "medido_em": "2026-10-07T14:32:17Z",
        "umidade_solo_pct": 41.2,
        "temperatura_solo_c": 24.8,
        "temperatura_ar_c": 27.1,
        "umidade_ar_pct": 58.0,
        "bateria_pct": 83.6,
        "rssi_dbm": -61,
        "luminosidade_lux": 8123.0,
    }
    base.update(sobrescritas)
    return base


class MensagemFalsa:
    """O que o paho entrega ao ``on_message``: só ``topic`` e ``payload`` importam."""

    def __init__(self, topico: str, payload: object) -> None:
        self.topic = topico
        self.payload = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        )


# ---------------------------------------------------------------------------
# Janela e nome do arquivo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("janela_min", "esperado"),
    [
        (15, "20261007/leituras_20261007T1430Z.jsonl"),
        (1, "20261007/leituras_20261007T1432Z.jsonl"),
        (5, "20261007/leituras_20261007T1430Z.jsonl"),
        (60, "20261007/leituras_20261007T1400Z.jsonl"),
    ],
)
def test_janela_define_o_nome_do_arquivo_no_layout_do_simulador(
    tmp_path: Path, janela_min: int, esperado: str
) -> None:
    caminho = caminho_da_janela(tmp_path, inicio_da_janela(INSTANTE, janela_min))
    assert caminho.relative_to(tmp_path).as_posix() == esperado
    # O coletor streaming e o simulador reconhecem o nome — a fronteira não mudou.
    assert _PADRAO_NOME.match(caminho.name)
    assert instante_do_arquivo(caminho.name) == inicio_da_janela(INSTANTE, janela_min).replace(
        tzinfo=None
    )


def test_janela_fora_da_faixa_e_rejeitada() -> None:
    with pytest.raises(ErroGateway):
        inicio_da_janela(INSTANTE, 0)
    with pytest.raises(ErroGateway):
        inicio_da_janela(INSTANTE, 1441)


def test_instante_normalizado_para_utc() -> None:
    assert instante_utc("2026-10-07T11:32:17-03:00") == INSTANTE
    assert instante_utc("2026-10-07T14:32:17Z") == INSTANTE
    assert instante_utc("2026-10-07T14:32:17") == INSTANTE  # sem fuso = UTC, como o contrato
    assert instante_utc("ontem às três") is None
    assert instante_utc(1759847537) is None
    assert instante_utc(None) is None


def test_sensor_vem_do_topico() -> None:
    assert sensor_do_topico("agrosmart/sensores/SNS-0301/leitura") == "SNS-0301"
    assert sensor_do_topico("x") is None


# ---------------------------------------------------------------------------
# Filtro de chaves
# ---------------------------------------------------------------------------


def test_mantem_so_as_chaves_do_contrato_na_ordem_do_contrato() -> None:
    norm = normalizar_leitura(_payload(), CAMPOS)
    assert norm.registro is not None
    assert list(norm.registro) == CAMPOS
    assert norm.extras == ("luminosidade_lux",)
    assert norm.registro["ph"] is None and norm.registro["condutividade_ds_m"] is None
    assert norm.registro["rssi_dbm"] == -61
    assert norm.motivo is None


def test_instante_com_fuso_sai_em_utc_no_formato_do_contrato() -> None:
    norm = normalizar_leitura(_payload(medido_em="2026-10-07T11:32:17-03:00"), CAMPOS)
    assert norm.registro is not None
    assert norm.registro["medido_em"] == "2026-10-07T14:32:17Z"


def test_sem_instante_ou_sem_objeto_e_descartado() -> None:
    assert normalizar_leitura(_payload(medido_em=None), CAMPOS).motivo == "sem_instante"
    assert normalizar_leitura(["lista"], CAMPOS).motivo == "nao_objeto"


def test_sensor_id_ausente_vem_do_topico() -> None:
    payload = _payload()
    del payload["sensor_id"]
    norm = normalizar_leitura(payload, CAMPOS, sensor_id_topico="SNS-0301")
    assert norm.registro is not None and norm.registro["sensor_id"] == "SNS-0301"


# ---------------------------------------------------------------------------
# Dedup e reabertura de janela
# ---------------------------------------------------------------------------


def test_reabrir_janela_faz_append_sem_duplicar_a_chave_natural(tmp_path: Path) -> None:
    primeiro = EscritorJanelas(tmp_path, 15, CONTRATO.chave_natural)
    registro = normalizar_leitura(_payload(), CAMPOS).registro
    assert registro is not None
    assert primeiro.gravar(registro, INSTANTE) is True
    assert primeiro.gravar(registro, INSTANTE) is False  # mesma execução

    # Outro processo (reinício do gateway, leitura atrasada): mesmo arquivo, sem repetir.
    segundo = EscritorJanelas(tmp_path, 15, CONTRATO.chave_natural)
    assert segundo.gravar(registro, INSTANTE) is False
    outra = dict(registro, medido_em="2026-10-07T14:33:02Z")
    assert segundo.gravar(outra, instante_utc(outra["medido_em"])) is True  # type: ignore[arg-type]

    arquivo = tmp_path / "20261007" / "leituras_20261007T1430Z.jsonl"
    linhas = arquivo.read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 2
    assert arquivo.read_bytes().count(b"\r") == 0  # mesmo formato de linha do simulador
    assert segundo.reabertos == {arquivo}


def test_gateway_processa_grava_deduplica_e_descarta(config_temporaria: Config) -> None:
    gateway = GatewayMqtt(config_temporaria, janela_min=15)
    topico = "agrosmart/sensores/SNS-0301/leitura"

    assert gateway.processar(topico, MensagemFalsa(topico, _payload()).payload) == "gravada"
    assert gateway.processar(topico, MensagemFalsa(topico, _payload()).payload) == "duplicata"
    assert gateway.processar(topico, b"isto nao e json") == "json_invalido"
    assert gateway.processar(topico, b"[1, 2, 3]") == "nao_objeto"
    assert gateway.processar(topico, json.dumps(_payload(medido_em="x")).encode()) == "sem_instante"
    # Leitura atrasada de outro sensor na mesma janela: cai no mesmo arquivo.
    atrasada = _payload(sensor_id="SNS-0302", medido_em="2026-10-07T14:40:00Z")
    assert gateway.processar(topico, json.dumps(atrasada).encode()) == "gravada"

    r = gateway.resumo
    assert (r.recebidas, r.gravadas, r.duplicatas, r.total_descartadas) == (6, 2, 1, 3)
    assert r.descartadas == {"json_invalido": 1, "nao_objeto": 1, "sem_instante": 1}
    assert r.chaves_extras == {"luminosidade_lux": 3}
    assert r.por_sensor == {"SNS-0301": 1, "SNS-0302": 1}
    assert list(r.arquivos.values()) == [2]

    arquivo = (
        config_temporaria.dir_landing / "sensores" / "20261007" / "leituras_20261007T1430Z.jsonl"
    )
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        assert list(json.loads(linha)) == CAMPOS


# ---------------------------------------------------------------------------
# A landing do gateway entra no pipeline da Fase 5 sem mudança
# ---------------------------------------------------------------------------


def test_arquivos_do_gateway_sao_ingeridos_e_aprovados_no_silver(config_temporaria: Config) -> None:
    from agrosmart.processamento.silver import construir_silver

    gateway = GatewayMqtt(config_temporaria, janela_min=1)
    frota = Frota(config_temporaria, n=4, seed=7)
    for ciclo in range(30):  # um ciclo a cada 5 s a partir das 14:30
        agora = datetime(2026, 10, 7, 14, 30 + ciclo // 12, (ciclo % 12) * 5, tzinfo=UTC)
        for topico, linha in frota.ciclo(agora):
            gateway.processar(topico, linha)
    assert len(gateway.resumo.arquivos) == 3  # 14:30, 14:31, 14:32

    ingestao = ingerir(config_temporaria, fontes=["sensores"], exportar_exemplos=False)
    fonte = ingestao["fontes"][0]
    assert fonte["modo"] == "streaming"
    assert fonte["lotes"] == 3 and fonte["gravados"] == 3 and fonte["rejeitados"] == 0
    assert fonte["registros"] == gateway.resumo.gravadas

    relatorio = construir_silver(config_temporaria, fontes=["sensores"])
    sensores = relatorio["fontes"]["sensores"]
    assert sensores["reconciliacao_ok"]
    assert sensores["lidos"] == gateway.resumo.gravadas
    assert sensores["quarentena"] == 0  # o gateway já deduplicou: SEN-02 não dispara
    assert sensores["falhas_tipagem"] == 0

    lake = criar_armazenamento(config_temporaria)
    assert lake.contar(Camada.SILVER, "sensores_leitura/") == 1  # um dia de evento


# ---------------------------------------------------------------------------
# Emulador: payload do firmware, válido contra o contrato
# ---------------------------------------------------------------------------


def test_payload_do_emulador_tem_as_chaves_do_firmware_e_passa_no_contrato(
    config_temporaria: Config,
) -> None:
    frota = Frota(config_temporaria, n=12, seed=1)
    assert [d.sensor.id for d in frota.dispositivos][:3] == ["SNS-0101", "SNS-0102", "SNS-0201"]
    assert len({d.estado.talhao.id for d in frota.dispositivos}) == 6

    agora = datetime(2026, 10, 7, 15, 0, 0, tzinfo=UTC)
    tipos = {c.nome: c.tipo.value for c in CONTRATO.campos}
    for dispositivo in frota.dispositivos:
        leitura = frota.leitura(dispositivo, agora)
        assert tuple(leitura) == CHAVES_PAYLOAD
        assert set(leitura) - set(CAMPOS) == {"luminosidade_lux"}
        for obrigatorio in CONTRATO.obrigatorios_de_origem:
            if obrigatorio in leitura:
                assert leitura[obrigatorio] is not None, obrigatorio
        for chave, valor in leitura.items():
            if chave not in tipos:
                continue
            esperado = {"string": str, "float": float, "int": int, "timestamp": str}[tipos[chave]]
            assert isinstance(valor, esperado), chave
        # Faixas das regras do contrato (SEN-04, SEN-05, SEN-10, SEN-11).
        assert 0 <= leitura["umidade_solo_pct"] <= 100
        assert -10 <= leitura["temperatura_solo_c"] <= 70
        assert 0 <= leitura["bateria_pct"] <= 100
        assert -120 <= leitura["rssi_dbm"] <= 0
        assert leitura["medido_em"] == "2026-10-07T15:00:00Z"
        assert leitura["luminosidade_lux"] >= 0

    norm = normalizar_leitura(frota.leitura(frota.dispositivos[0], agora), CAMPOS)
    assert norm.registro is not None and norm.extras == ("luminosidade_lux",)


def test_ciclo_do_emulador_atrasa_e_duplica_uma_fracao_pequena(config_temporaria: Config) -> None:
    frota = Frota(config_temporaria, n=12, seed=3)
    emitidas: list[str] = []
    for ciclo in range(40):
        agora = datetime(2026, 10, 7, 15, ciclo // 12, (ciclo % 12) * 5, tzinfo=UTC)
        for topico, linha in frota.ciclo(agora):
            assert topico == f"agrosmart/sensores/{json.loads(linha)['sensor_id']}/leitura"
            emitidas.append(linha)
    r = frota.resumo
    geradas = 40 * 12
    assert r.ciclos == 40
    assert 0 < r.atrasadas < geradas * 0.06
    assert r.duplicadas <= geradas * 0.03
    assert len(emitidas) == r.publicadas
    # Só as duplicatas se repetem; o restante pendente sai nos ciclos seguintes.
    assert len(emitidas) - len(set(emitidas)) <= r.duplicadas
    assert geradas - r.atrasadas <= r.publicadas <= geradas + r.duplicadas


def test_emulador_obedece_ao_comando_de_irrigacao(config_temporaria: Config) -> None:
    frota = Frota(config_temporaria, n=12, seed=1)
    agora = datetime(2026, 10, 7, 15, 0, 0, tzinfo=UTC)
    topico = TOPICO_ATUADOR.format(talhao_id="TLH-03")

    resposta = frota.aplicar_comando(topico, b'{"acao":"ligar","minutos":10}', agora)
    assert resposta is not None
    assert resposta[0] == "agrosmart/atuadores/TLH-03/irrigacao/estado"
    assert json.loads(resposta[1])["irrigacao"] == "ligada"
    assert frota.estados["TLH-03"].irrigando(agora)
    assert not frota.estados["TLH-03"].irrigando(agora.replace(minute=11))

    # A umidade do talhão irrigado sobe ao longo dos ciclos (até a capacidade de
    # campo, nunca além); a dos outros talhões não muda.
    sensor_03 = next(d for d in frota.dispositivos if d.sensor.id == "SNS-0301")
    sensor_01 = next(d for d in frota.dispositivos if d.sensor.id == "SNS-0101")
    estado_03 = frota.estados["TLH-03"]
    assert estado_03.umidade_irrigacao == 0.0
    for _ in range(20):
        frota.leitura(sensor_03, agora)
        frota.leitura(sensor_01, agora)
    assert 0.0 < estado_03.umidade_irrigacao <= estado_03.talhao.capacidade_campo_pct
    assert frota.estados["TLH-01"].umidade_irrigacao == 0.0
    depois_03 = frota.leitura(sensor_03, agora)["umidade_solo_pct"]
    assert depois_03 <= estado_03.talhao.capacidade_campo_pct + 0.5  # +ruído do sensor

    resposta = frota.aplicar_comando(topico, b'{"acao":"desligar"}', agora)
    assert resposta is not None and json.loads(resposta[1])["irrigacao"] == "desligada"
    assert frota.aplicar_comando(topico, b"nao e json", agora) is None
    assert frota.aplicar_comando(topico, b'{"acao":"explodir"}', agora) is None
    assert (
        frota.aplicar_comando("agrosmart/atuadores/TLH-99/irrigacao", b'{"acao":"ligar"}') is None
    )
    assert frota.resumo.comandos == 2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_comandos_da_fase6_estao_registrados_sem_remover_os_antigos() -> None:
    from agrosmart.pipeline import app

    nomes = {c.name or (c.callback.__name__ if c.callback else "") for c in app.registered_commands}
    assert {"coletar-mqtt", "dispositivo"} <= nomes
    assert {"simular", "ingerir", "processar", "run"} <= nomes
