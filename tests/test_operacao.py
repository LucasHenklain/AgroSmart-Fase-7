"""Fase 7: estado da operação, métricas Prometheus, servidor HTTP e utilitários do ciclo."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

from agrosmart.compat import para_arrow
from agrosmart.ingestao import datas_com_lote_novo
from agrosmart.ingestao.base import ResultadoIngestao, ResultadoLote
from agrosmart.log import Evento, abrir_execucao, execucao_atual, fechar_execucao
from agrosmart.operacao.metricas import EstadoOperacao, _valor, gravar_status, medir_landing
from agrosmart.operacao.servidor import iniciar_servidor
from agrosmart.orquestracao import _escalares, novo_execucao_id


def _resumo(sucesso: bool = True, silver_s: float = 20.0) -> dict:
    etapas = [
        {"nome": "simular", "status": "pulada", "duracao_s": 0.0, "resumo": {}, "erro": None},
        {
            "nome": "ingerir",
            "status": "ok",
            "duracao_s": 1.5,
            "resumo": {"gravados": 2, "atualizados": 1, "pulados": 10, "rejeitados": 0},
            "erro": None,
        },
        {
            "nome": "silver",
            "status": "ok" if sucesso else "erro",
            "duracao_s": silver_s,
            "resumo": {"lidos": 100, "aprovados": 95, "quarentena": 5} if sucesso else {},
            "erro": None if sucesso else "EndpointConnectionError: minio",
        },
    ]
    if sucesso:
        etapas += [
            {
                "nome": "decidir",
                "status": "ok",
                "duracao_s": 0.1,
                "resumo": {"talhoes": 6, "acoes": 2, "comandos": 1},
                "erro": None,
            },
            {
                "nome": "atuar",
                "status": "ok",
                "duracao_s": 0.5,
                "resumo": {"publicadas": 1},
                "erro": None,
            },
        ]
    return {
        "execucao_id": "ciclo-teste",
        "sucesso": sucesso,
        "duracao_s": 22.1,
        "etapas": etapas,
    }


def test_valor_preserva_precisao_do_epoch():
    # regressão: com {:g} o epoch virava 1.79493e+09 e o alerta disparava à toa
    assert _valor(1794932319.1907887) == "1794932319.1907887"
    assert float(_valor(1794932319.25)) == pytest.approx(1794932319.25)
    assert _valor(83) == "83"
    assert _valor(True) == "1"


def test_estado_registra_ciclo_e_expoe_metricas():
    estado = EstadoOperacao(intervalo_s=60)
    estado.registrar_ciclo(_resumo(), {"arquivos": 3, "idade_ultima_s": 4.0})
    texto = estado.para_prometheus()
    assert 'agrosmart_ciclos_total{status="ok"} 1' in texto
    assert 'agrosmart_etapa_duracao_segundos{etapa="silver"} 20.0' in texto
    assert 'agrosmart_lotes_ingeridos_total{status="gravado"} 2' in texto
    assert "agrosmart_comandos_publicados_total 1" in texto
    assert 'agrosmart_silver_registros{resultado="quarentena"} 5' in texto
    # etapa pulada não sobrescreve a duração da última execução real
    assert 'etapa="simular"} 0' not in texto.split("agrosmart_etapa_duracao_segundos")[2]
    for linha in texto.splitlines():
        assert linha.startswith("#") or " " in linha


def test_saude_aquecendo_ok_falha_e_atraso():
    estado = EstadoOperacao(intervalo_s=60)
    assert estado.saude() == (True, "aquecendo (iniciando)")
    estado.registrar_ciclo(_resumo(), {})
    assert estado.saude()[0] is True
    assert estado.saude(agora=time.time() + 3 * 60 + 301)[0] is False
    estado.registrar_ciclo(_resumo(sucesso=False), {})
    ok, motivo = estado.saude()
    assert not ok and "falhou" in motivo
    assert estado.para_dict()["ciclos"] == {"ok": 1, "erro": 1}


def test_gravar_status_atomico(tmp_path):
    estado = EstadoOperacao(intervalo_s=30, modo="incremental", versao_processo="v2")
    estado.registrar_ciclo(_resumo(), {"arquivos": 1, "idade_ultima_s": 2})
    destino = gravar_status(estado, tmp_path)
    dados = json.loads(destino.read_text(encoding="utf-8"))
    assert dados["versao_processo"] == "v2" and dados["saudavel"] is True
    assert not list(tmp_path.rglob("*.tmp"))


def test_medir_landing(tmp_path):
    assert medir_landing(tmp_path / "nao-existe") == {"arquivos": 0, "idade_ultima_s": -1}
    pasta = tmp_path / "20261117"
    pasta.mkdir()
    (pasta / "leituras_20261117T1300Z.jsonl").write_text("{}\n", encoding="utf-8")
    medida = medir_landing(tmp_path, agora=time.time() + 10)
    assert medida["arquivos"] == 1 and 9 <= medida["idade_ultima_s"] <= 12


def test_servidor_health_metrics_status():
    estado = EstadoOperacao(intervalo_s=60)
    servidor = iniciar_servidor(estado, 0)  # porta 0: o SO escolhe uma livre
    porta = servidor.server_address[1]
    base = f"http://127.0.0.1:{porta}"
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=5) as r:
            assert r.status == 200
        estado.registrar_ciclo(_resumo(sucesso=False), {})
        with pytest.raises(urllib.error.HTTPError) as erro:
            urllib.request.urlopen(f"{base}/health", timeout=5)
        assert erro.value.code == 503
        with urllib.request.urlopen(f"{base}/metrics", timeout=5) as r:
            assert b'agrosmart_ciclos_total{status="erro"} 1' in r.read()
        with urllib.request.urlopen(f"{base}/status", timeout=5) as r:
            assert json.loads(r.read())["ultimo_ciclo"]["execucao_id"] == "ciclo-teste"
        with pytest.raises(urllib.error.HTTPError) as erro:
            urllib.request.urlopen(f"{base}/admin", timeout=5)
        assert erro.value.code == 404
    finally:
        servidor.shutdown()


def test_contexto_de_execucao_entra_nos_eventos():
    abrir_execucao("ciclo-x", origem="worker", versao_processo="v2")
    try:
        assert execucao_atual()["execucao_id"] == "ciclo-x"
        dados = Evento(etapa="silver", acao="etapa_iniciada").para_dict()
        assert dados["execucao_id"] == "ciclo-x" and dados["versao_processo"] == "v2"
        assert len(dados["ocorrido_em"].split(".")[1]) >= 3  # milissegundos
    finally:
        fechar_execucao()
    assert "execucao_id" not in Evento(etapa="x", acao="y").para_dict()


def test_datas_com_lote_novo():
    r = ResultadoIngestao(fonte="sensores", modo="streaming")
    r.lotes = [
        ResultadoLote("a", "b1", "gravado", chave="sensores/ano=2026/mes=11/dia=17/x.jsonl"),
        ResultadoLote("b", "b2", "atualizado", chave="sensores/ano=2026/mes=11/dia=16/y.jsonl"),
        ResultadoLote("c", "b3", "pulado", chave="sensores/ano=2026/mes=10/dia=01/z.jsonl"),
        ResultadoLote("d", "b4", "gravado", chave=None),
    ]
    assert datas_com_lote_novo([r]) == ["2026-11-16", "2026-11-17"]


def test_escalares_e_execucao_id():
    assert _escalares({"a": 1, "b": "x" * 61, "c": [1], "d": True}) == {"a": 1, "d": True}
    assert novo_execucao_id("ciclo").startswith("ciclo-2")


def test_para_arrow_nas_duas_apis():
    class Antiga:
        def fetch_arrow_table(self):
            return "antiga"

    class Nova:
        def to_arrow_table(self):
            return "nova"

        def fetch_arrow_table(self):  # pragma: no cover - não deve ser usada
            raise AssertionError

    assert para_arrow(Antiga()) == "antiga"
    assert para_arrow(Nova()) == "nova"
