from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agrosmart.contratos import (
    ErroContrato,
    Severidade,
    TipoDado,
    carregar_contrato,
    carregar_contratos,
    ordenar_por_dependencia,
)

# ---------------------------------------------------------------------------
# Os contratos reais do projeto
# ---------------------------------------------------------------------------


def test_todos_os_contratos_do_projeto_carregam(dir_contratos_projeto: Path) -> None:
    contratos = carregar_contratos(dir_contratos_projeto)
    assert len(contratos) >= 5
    assert "erp_talhoes" in contratos


def test_projeto_cobre_dados_estruturados_e_nao_estruturados(dir_contratos_projeto: Path) -> None:
    """Requisito explícito da Entrega 1."""
    tipos = {c.tipo_dado for c in carregar_contratos(dir_contratos_projeto).values()}
    assert TipoDado.ESTRUTURADO in tipos
    assert TipoDado.NAO_ESTRUTURADO in tipos


def test_toda_fonte_tem_regras_criticas_e_de_alerta(dir_contratos_projeto: Path) -> None:
    for contrato in carregar_contratos(dir_contratos_projeto).values():
        assert contrato.regras_criticas, f"{contrato.fonte} sem regra crítica"
        assert contrato.regras_alerta, f"{contrato.fonte} sem regra de alerta"


def test_ids_de_regra_sao_globalmente_unicos(dir_contratos_projeto: Path) -> None:
    ids = [r.id for c in carregar_contratos(dir_contratos_projeto).values() for r in c.qualidade]
    assert len(ids) == len(set(ids))


def test_talhoes_e_ingerido_antes_de_quem_o_referencia(dir_contratos_projeto: Path) -> None:
    ordem = [c.fonte for c in ordenar_por_dependencia(carregar_contratos(dir_contratos_projeto))]
    assert ordem[0] == "erp_talhoes"
    assert ordem.index("erp_talhoes") < ordem.index("sensores")


def test_toda_chave_natural_e_obrigatoria(dir_contratos_projeto: Path) -> None:
    """Chave natural nula não deduplica — precisa ser obrigatória no schema."""
    for contrato in carregar_contratos(dir_contratos_projeto).values():
        for nome in contrato.chave_natural:
            assert contrato.campo(nome).obrigatorio, f"{contrato.fonte}.{nome}"


# ---------------------------------------------------------------------------
# Contratos sintéticos: o que o validador precisa rejeitar
# ---------------------------------------------------------------------------


def _contrato_minimo() -> dict:
    return {
        "fonte": "teste",
        "versao": "v1",
        "titulo": "t",
        "descricao": "d",
        "responsavel": "r",
        "tipo_dado": "estruturado",
        "classificacao_lgpd": "interno",
        "ingestao": {
            "modo": "batch",
            "origem": "x",
            "formato_bruto": "csv",
            "janela": "diária",
            "sla_freshness_min": 60,
        },
        "particionamento": {
            "estrategia": "fonte/ano/mes/dia",
            "coluna_evento": "quando",
            "colunas": ["ano"],
        },
        "chave_natural": ["id"],
        "dedup": {"estrategia": "evento"},
        "destino": {"silver_tabela": "teste"},
        "retencao": {"bronze_dias": 10},
        "campos": [
            {"nome": "id", "tipo": "string", "obrigatorio": True, "descricao": "id"},
            {"nome": "quando", "tipo": "timestamp", "obrigatorio": True, "descricao": "ts"},
            {"nome": "valor", "tipo": "float", "descricao": "v"},
        ],
        "qualidade": [],
    }


def _escrever(tmp_path: Path, conteudo: dict, nome: str = "teste.v1.yaml") -> Path:
    caminho = tmp_path / nome
    caminho.write_text(yaml.safe_dump(conteudo, allow_unicode=True), encoding="utf-8")
    return caminho


def test_contrato_minimo_valido(tmp_path: Path) -> None:
    contrato = carregar_contrato(_escrever(tmp_path, _contrato_minimo()))
    assert contrato.nome_completo == "teste.v1"
    assert contrato.mapa_tipos()["valor"].value == "float"


def test_rejeita_chave_natural_fora_do_schema(tmp_path: Path) -> None:
    bruto = _contrato_minimo()
    bruto["chave_natural"] = ["nao_existe"]
    with pytest.raises(ErroContrato, match="chave_natural"):
        carregar_contrato(_escrever(tmp_path, bruto))


def test_rejeita_regra_que_cita_campo_inexistente(tmp_path: Path) -> None:
    bruto = _contrato_minimo()
    bruto["qualidade"] = [
        {
            "id": "TST-01",
            "regra": "nao_nulo",
            "campos": ["fantasma"],
            "severidade": "critica",
            "justificativa": "campo que não existe",
        }
    ]
    with pytest.raises(ErroContrato, match="fantasma"):
        carregar_contrato(_escrever(tmp_path, bruto))


def test_rejeita_intervalo_sem_limites(tmp_path: Path) -> None:
    bruto = _contrato_minimo()
    bruto["qualidade"] = [
        {
            "id": "TST-01",
            "regra": "intervalo",
            "campos": ["valor"],
            "severidade": "critica",
            "justificativa": "intervalo precisa de min ou max",
        }
    ]
    with pytest.raises(ErroContrato, match="min"):
        carregar_contrato(_escrever(tmp_path, bruto))


def test_rejeita_ids_de_regra_duplicados(tmp_path: Path) -> None:
    bruto = _contrato_minimo()
    regra = {
        "id": "TST-01",
        "regra": "nao_nulo",
        "campos": ["id"],
        "severidade": "alerta",
        "justificativa": "duplicada de propósito",
    }
    bruto["qualidade"] = [regra, dict(regra)]
    with pytest.raises(ErroContrato, match="duplicados"):
        carregar_contrato(_escrever(tmp_path, bruto))


def test_rejeita_nome_de_arquivo_divergente(tmp_path: Path) -> None:
    with pytest.raises(ErroContrato, match="nome do arquivo"):
        carregar_contrato(_escrever(tmp_path, _contrato_minimo(), nome="outro.v9.yaml"))


def test_rejeita_referencia_para_tabela_inexistente(tmp_path: Path) -> None:
    bruto = _contrato_minimo()
    bruto["qualidade"] = [
        {
            "id": "TST-01",
            "regra": "referencia",
            "campos": ["id"],
            "tabela_referencia": "nao_existe",
            "campo_referencia": "id",
            "severidade": "critica",
            "justificativa": "referência quebrada",
        }
    ]
    _escrever(tmp_path, bruto)
    with pytest.raises(ErroContrato, match="tabela_referencia"):
        carregar_contratos(tmp_path)


def test_dedup_versao_exige_campo_de_ordem(tmp_path: Path) -> None:
    bruto = _contrato_minimo()
    bruto["dedup"] = {"estrategia": "versao"}
    with pytest.raises(ErroContrato, match="campo_ordem"):
        carregar_contrato(_escrever(tmp_path, bruto))


def test_dedup_campo_de_ordem_precisa_existir(tmp_path: Path) -> None:
    bruto = _contrato_minimo()
    bruto["dedup"] = {"estrategia": "versao", "campo_ordem": "fantasma"}
    with pytest.raises(ErroContrato, match="campo_ordem"):
        carregar_contrato(_escrever(tmp_path, bruto))


def test_tratamento_lgpd_exige_classificacao(tmp_path: Path) -> None:
    bruto = _contrato_minimo()
    bruto["campos"][2]["tratamento"] = "pseudonimizar"
    with pytest.raises(ErroContrato, match="lgpd"):
        carregar_contrato(_escrever(tmp_path, bruto))


def test_fontes_mutaveis_do_erp_usam_versao_e_as_demais_evento(dir_contratos_projeto: Path) -> None:
    contratos = carregar_contratos(dir_contratos_projeto)
    for fonte, contrato in contratos.items():
        esperado = "versao" if fonte.startswith("erp_") else "evento"
        assert contrato.dedup.estrategia == esperado, fonte


def test_dado_pessoal_do_erp_e_pseudonimizado(dir_contratos_projeto: Path) -> None:
    operacoes = carregar_contratos(dir_contratos_projeto)["erp_operacoes"]
    responsavel = operacoes.campo("responsavel_id")
    assert responsavel.lgpd == "pessoal"
    assert responsavel.tratamento == "pseudonimizar"
    assert {c.nome for c in operacoes.campos_lgpd} == {"responsavel_id"}


def test_severidade_separa_criticas_de_alertas(tmp_path: Path) -> None:
    bruto = _contrato_minimo()
    bruto["qualidade"] = [
        {
            "id": "TST-01",
            "regra": "nao_nulo",
            "campos": ["id"],
            "severidade": "critica",
            "justificativa": "x" * 10,
        },
        {
            "id": "TST-02",
            "regra": "intervalo",
            "campos": ["valor"],
            "min": 0,
            "severidade": "alerta",
            "justificativa": "y" * 10,
        },
    ]
    contrato = carregar_contrato(_escrever(tmp_path, bruto))
    assert [r.id for r in contrato.regras_criticas] == ["TST-01"]
    assert contrato.regras_alerta[0].severidade is Severidade.ALERTA
    assert contrato.regras_alerta[0].parametros == {"min": 0}
