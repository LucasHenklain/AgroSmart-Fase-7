"""Testes unitários do motor de qualidade, regra a regra, com contrato sintético."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl
import pytest

from agrosmart.contratos import Contrato
from agrosmart.processamento.tipagem import REGRA_TIPAGEM, tipar
from agrosmart.qualidade.motor import avaliar

AGORA = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
JANELA_FIM = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _contrato(
    dedup: dict[str, Any] | None = None, extras: list[dict[str, Any]] | None = None
) -> Contrato:
    return Contrato.model_validate(
        {
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
                "janela": "d",
                "sla_freshness_min": 60,
            },
            "particionamento": {
                "estrategia": "fonte/ano/mes/dia",
                "coluna_evento": "quando",
                "colunas": ["ano"],
            },
            "chave_natural": ["id", "quando"],
            "dedup": dedup or {"estrategia": "evento"},
            "destino": {"silver_tabela": "teste"},
            "retencao": {},
            "campos": [
                {"nome": "id", "tipo": "string", "obrigatorio": True, "descricao": "id"},
                {"nome": "quando", "tipo": "timestamp", "obrigatorio": True, "descricao": "ts"},
                {
                    "nome": "extraido",
                    "tipo": "timestamp",
                    "obrigatorio": True,
                    "descricao": "versão",
                },
                {"nome": "valor", "tipo": "float", "descricao": "v"},
                {"nome": "categoria", "tipo": "string", "descricao": "c"},
                {"nome": "ref", "tipo": "string", "descricao": "r"},
                {"nome": "a", "tipo": "float", "descricao": "a"},
                {"nome": "b", "tipo": "float", "descricao": "b"},
                {"nome": "dia", "tipo": "date", "descricao": "dia"},
            ],
            "qualidade": [
                {
                    "id": "TST-01",
                    "regra": "nao_nulo",
                    "campos": ["id", "quando"],
                    "severidade": "critica",
                    "justificativa": "chave nula",
                },
                {
                    "id": "TST-02",
                    "regra": "unicidade",
                    "campos": ["id", "quando"],
                    "severidade": "critica",
                    "justificativa": "duplicata exata da chave",
                },
                {
                    "id": "TST-03",
                    "regra": "intervalo",
                    "campos": ["valor"],
                    "min": 0,
                    "max": 10,
                    "severidade": "critica",
                    "justificativa": "faixa física",
                },
                {
                    "id": "TST-04",
                    "regra": "dominio",
                    "campos": ["categoria"],
                    "valores": ["x", "y"],
                    "severidade": "critica",
                    "justificativa": "domínio fechado",
                },
                {
                    "id": "TST-05",
                    "regra": "regex",
                    "campos": ["id"],
                    "padrao": r"^ID-\d+$",
                    "severidade": "critica",
                    "justificativa": "padrão de id",
                },
                {
                    "id": "TST-06",
                    "regra": "nao_futuro",
                    "campos": ["quando"],
                    "tolerancia_min": 0,
                    "severidade": "critica",
                    "justificativa": "futuro vs agora",
                },
                {
                    "id": "TST-07",
                    "regra": "referencia",
                    "campos": ["ref"],
                    "tabela_referencia": "refs",
                    "campo_referencia": "codigo",
                    "severidade": "critica",
                    "justificativa": "integridade",
                },
                {
                    "id": "TST-08",
                    "regra": "comparacao",
                    "campo_a": "a",
                    "operador": "<=",
                    "campo_b": "b",
                    "severidade": "critica",
                    "justificativa": "ordem entre os campos a e b",
                },
                {
                    "id": "TST-09",
                    "regra": "intervalo",
                    "campos": ["valor"],
                    "max": 8,
                    "severidade": "alerta",
                    "justificativa": "aviso de valor alto",
                },
                {
                    "id": "TST-10",
                    "regra": "freshness",
                    "max_atraso_min": 60,
                    "detectar_lacunas": True,
                    "severidade": "alerta",
                    "justificativa": "dado velho",
                },
                {
                    "id": "TST-11",
                    "regra": "nao_futuro",
                    "campos": ["dia"],
                    "tolerancia_min": 0,
                    "referencia": "lote",
                    "severidade": "critica",
                    "justificativa": "futuro vs lote",
                },
                *(extras or []),
            ],
        }
    )


def _linha(**sobrescritas: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "ID-1",
        "quando": "2026-08-30T10:00:00Z",
        "extraido": "2026-08-30T11:00:00Z",
        "valor": "5",
        "categoria": "x",
        "ref": "R1",
        "a": "1",
        "b": "2",
        "dia": "2026-08-29",
        "_batch_id": "b1",
        "_ingested_at": datetime(2026, 8, 30, 12, 5, tzinfo=UTC),
        "_janela_fim": JANELA_FIM,
        "_particao": date(2026, 8, 30),
        "_schema_version": "teste.v1",
        "_versao_lote": 1,
        "_linha": 1,
    }
    base.update(sobrescritas)
    return base


def _df(*linhas: dict[str, Any]) -> pl.DataFrame:
    schema: dict[str, pl.DataType] = {
        c: pl.Utf8
        for c in ("id", "quando", "extraido", "valor", "categoria", "ref", "a", "b", "dia")
    }
    schema.update(
        {
            "_batch_id": pl.Utf8,
            "_ingested_at": pl.Datetime("us", "UTC"),
            "_janela_fim": pl.Datetime("us", "UTC"),
            "_particao": pl.Date,
            "_schema_version": pl.Utf8,
            "_versao_lote": pl.Int64,
            "_linha": pl.Int64,
        }
    )
    for indice, linha in enumerate(linhas, start=1):
        linha.setdefault("_linha", indice)
    return pl.from_dicts(list(linhas), schema=schema)


REFS = {"refs": pl.DataFrame({"codigo": ["R1", "R2"]})}


def _avaliar(
    *linhas: dict[str, Any], contrato: Contrato | None = None, referencias: dict | None = None
):
    ctr = contrato or _contrato()
    tipado = tipar(_df(*linhas), ctr)
    return avaliar(
        tipado,
        ctr,
        referencias=REFS if referencias is None else referencias,
        instante_referencia=AGORA,
    )


def _regras_da_quarentena(resultado) -> list[list[str]]:
    return resultado.quarentena.get_column("_regras_violadas").to_list()


# ---------------------------------------------------------------------------


def test_registro_limpo_e_aprovado_sem_alertas() -> None:
    r = _avaliar(_linha())
    assert r.n_aprovados == 1 and r.n_quarentena == 0
    assert r.aprovados.get_column("_alertas").to_list() == [[]]
    assert "_regras_violadas" not in r.aprovados.columns


@pytest.mark.parametrize(
    ("sobrescrita", "regra"),
    [
        ({"id": None}, "TST-01"),
        ({"valor": "11"}, "TST-03"),
        ({"valor": "-0.5"}, "TST-03"),
        ({"categoria": "z"}, "TST-04"),
        ({"id": "SNSX-1"}, "TST-05"),
        ({"quando": "2026-09-02T00:00:00Z"}, "TST-06"),
        ({"ref": "R9"}, "TST-07"),
        ({"a": "5", "b": "1"}, "TST-08"),
        ({"dia": "2026-08-31"}, "TST-11"),
    ],
)
def test_cada_regra_critica_reprova_o_registro_certo(
    sobrescrita: dict[str, Any], regra: str
) -> None:
    r = _avaliar(_linha(**sobrescrita), _linha(id="ID-2"))
    assert r.n_quarentena == 1
    assert regra in _regras_da_quarentena(r)[0]
    assert r.n_aprovados == 1
    assert regra in r.quarentena.get_column("_motivo").item()


def test_nao_futuro_relativo_ao_lote_aceita_o_que_e_passado_para_o_lote() -> None:
    # dia 31/08 > fim da janela do lote (30/08) → viola TST-11; TST-06 compara com o
    # "agora" (01/09) e não dispara
    r = _avaliar(_linha(dia="2026-08-31"))
    regras = _regras_da_quarentena(r)[0]
    assert "TST-11" in regras and "TST-06" not in regras


def test_nulos_nao_violam_regras_de_valor() -> None:
    r = _avaliar(_linha(valor=None, categoria=None, ref=None, a=None, dia=None))
    assert r.n_aprovados == 1


def test_falha_de_tipagem_vira_regra_de_sistema() -> None:
    r = _avaliar(_linha(valor="abc"))
    assert r.n_quarentena == 1
    assert REGRA_TIPAGEM in _regras_da_quarentena(r)[0]
    assert r.falhas_tipagem == 1


def test_regra_de_alerta_mantem_o_registro_e_marca() -> None:
    r = _avaliar(_linha(valor="9"))
    assert r.n_aprovados == 1
    assert r.aprovados.get_column("_alertas").to_list() == [["TST-09"]]
    assert r.registros_com_alerta == 1


def test_varias_violacoes_no_mesmo_registro_sao_todas_listadas() -> None:
    r = _avaliar(_linha(valor="99", categoria="z", ref="R9"))
    regras = _regras_da_quarentena(r)[0]
    assert {"TST-03", "TST-04", "TST-07"} <= set(regras)


def test_contagem_por_regra_no_relatorio() -> None:
    r = _avaliar(_linha(valor="11"), _linha(id="ID-2", valor="12"), _linha(id="ID-3"))
    por_id = {x.id: x for x in r.regras}
    assert por_id["TST-03"].violacoes == 2
    assert por_id["TST-03"].avaliados == 3
    assert r.quarentena_por_regra() == {"TST-03": 2}


# --- deduplicação ------------------------------------------------------------


def test_evento_repetido_e_duplicata_e_fica_a_primeira_ingestao() -> None:
    primeira = _linha(
        _batch_id="b1", _ingested_at=datetime(2026, 8, 30, 12, 5, tzinfo=UTC), valor="1"
    )
    reenvio = _linha(
        _batch_id="b2", _ingested_at=datetime(2026, 8, 30, 12, 20, tzinfo=UTC), valor="1"
    )
    r = _avaliar(reenvio, primeira)
    assert r.n_aprovados == 1 and r.duplicatas == 1
    assert r.aprovados.get_column("_batch_id").item() == "b1"
    assert _regras_da_quarentena(r) == [["TST-02"]]


def test_versao_reenviada_entre_lotes_substitui_sem_ir_para_quarentena() -> None:
    contrato = _contrato(dedup={"estrategia": "versao", "campo_ordem": "extraido"})
    original = _linha(_batch_id="b1", extraido="2026-08-30T11:00:00Z", valor="1")
    correcao = _linha(
        _batch_id="b2",
        extraido="2026-08-31T11:00:00Z",
        valor="2",
        _ingested_at=datetime(2026, 8, 31, 12, 0, tzinfo=UTC),
    )
    r = _avaliar(original, correcao, contrato=contrato)
    assert r.n_aprovados == 1 and r.n_quarentena == 0 and r.n_substituidos == 1
    assert r.aprovados.get_column("valor").item() == 2.0
    assert r.substituidos.get_column("valor").item() == 1.0


def test_versao_repetida_no_mesmo_lote_ainda_e_duplicata() -> None:
    contrato = _contrato(dedup={"estrategia": "versao", "campo_ordem": "extraido"})
    r = _avaliar(_linha(_batch_id="b1"), _linha(_batch_id="b1", _linha=2), contrato=contrato)
    assert r.duplicatas == 1 and r.n_substituidos == 0


def test_versao_mais_recente_reprovada_deixa_a_ultima_valida_no_silver() -> None:
    contrato = _contrato(dedup={"estrategia": "versao", "campo_ordem": "extraido"})
    boa = _linha(_batch_id="b1", extraido="2026-08-30T11:00:00Z", valor="1")
    ruim = _linha(_batch_id="b2", extraido="2026-08-31T11:00:00Z", valor="99")
    r = _avaliar(boa, ruim, contrato=contrato)
    assert r.n_aprovados == 1 and r.n_quarentena == 1
    assert r.aprovados.get_column("valor").item() == 1.0


# --- frescor -------------------------------------------------------------------


def test_entidade_atrasada_gera_alerta_de_frescor() -> None:
    r = _avaliar(
        _linha(quando="2026-08-31T20:00:00Z"), _linha(id="ID-2", quando="2026-09-01T02:30:00Z")
    )
    atrasados = [a for a in r.alertas_tabela if a.tipo == "atrasado"]
    assert [a.entidade for a in atrasados] == ["ID-1"]
    assert atrasados[0].detalhe["atraso_min"] == 420.0


def test_lacuna_entre_observacoes_gera_alerta() -> None:
    base = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    linhas = [
        _linha(quando=(base + timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ"), _linha=i)
        for i, m in enumerate((0, 30, 60, 170, 175), start=1)
    ]
    r = _avaliar(*linhas)
    lacunas = [a for a in r.alertas_tabela if a.tipo == "lacuna"]
    assert len(lacunas) == 1
    assert lacunas[0].detalhe["lacunas"] == 1
    assert lacunas[0].detalhe["maior_lacuna_min"] == 110.0


def test_referencia_indisponivel_nao_derruba_a_avaliacao() -> None:
    r = _avaliar(_linha(), referencias={})
    assert r.n_aprovados == 1
    nao_avaliada = next(x for x in r.regras if x.id == "TST-07")
    assert nao_avaliada.avaliados == 0 and nao_avaliada.observacao


def test_dataframe_vazio() -> None:
    contrato = _contrato()
    r = avaliar(tipar(_df(), contrato), contrato, referencias=REFS, instante_referencia=AGORA)
    assert r.lidos == 0 and r.n_aprovados == 0 and r.n_quarentena == 0


def test_tipagem_converte_pelo_contrato() -> None:
    contrato = _contrato()
    tipado = tipar(
        _df(_linha(valor="7.5", quando="2026-08-30T10:00:00+00:00", dia="2026-08-29")), contrato
    )
    assert tipado.schema["valor"] == pl.Float64
    assert tipado.schema["quando"] == pl.Datetime("us", "UTC")
    assert tipado.schema["dia"] == pl.Date
    assert tipado.get_column("quando").item() == datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    assert tipado.get_column("_falha_tipagem").to_list() == [[]]
