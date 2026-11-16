"""Copiloto: Context Pack, motor de regras, guardrails, fallback, renderização e avaliação."""

from __future__ import annotations

import json
from typing import Any

import pytest

from agrosmart.ia import regras
from agrosmart.ia.agente import Copiloto
from agrosmart.ia.avaliacao import avaliar
from agrosmart.ia.cliente_claude import ErroCliente
from agrosmart.ia.contexto import (
    ContextPack,
    abrir_gold,
    construir_context_pack,
    talhoes_disponiveis,
)
from agrosmart.ia.esquema import Caso, Recomendacao, SaidaCopiloto, esquema_json_saida
from agrosmart.ia.guardrails import extrair_numeros, validar
from agrosmart.ia.prompts import carregar, listar, montar
from agrosmart.ia.render import para_markdown, para_pdf, salvar


@pytest.fixture(scope="module")
def packs(lake_processado: dict) -> dict[str, ContextPack]:
    cfg = lake_processado["cfg"]
    con = abrir_gold(cfg, lake_processado["lake"])
    try:
        return {t: construir_context_pack(t, con=con, config=cfg) for t in talhoes_disponiveis(con)}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Context Pack
# ---------------------------------------------------------------------------


def test_context_pack_e_completo_e_rastreavel(packs: dict[str, ContextPack]) -> None:
    pack = packs["TLH-03"]
    assert pack.talhao.cultura == "feijao" and pack.talhao.irrigado
    assert pack.hidrico.umidade_atual_pct is not None
    assert pack.hidrico.lamina_sugerida_mm is not None
    assert pack.hidrico.previsao_chuva_72h_mm is None
    assert 1 <= len(pack.serie_hidrica_14d) <= 14
    assert len(pack.tabelas_gold_usadas) == 9
    assert "hidrico.deficit_atual_mm" in pack.caminhos()
    assert pack.hidrico.deficit_atual_mm in pack.numeros()
    assert any("previsão" in aviso for aviso in pack.avisos)
    json.loads(pack.para_json())  # serializável


def test_context_pack_do_sequeiro_nao_tem_lamina(packs: dict[str, ContextPack]) -> None:
    pack = packs["TLH-06"]
    assert not pack.talhao.irrigado
    assert pack.hidrico.lamina_sugerida_mm is None
    assert pack.hidrico.custo_lamina_sugerida_brl is None
    assert pack.talhao.cultura == "cana"


def test_context_pack_carrega_alertas_relevantes_e_economia(packs: dict[str, ContextPack]) -> None:
    pack = packs["TLH-06"]
    assert pack.economia.valor_producao_brl > 0
    # SNS-0602 tem sinal fraco: aparece no pacote do talhão
    fracos = [s for s in pack.sensores.sensores if s.sinal_fraco]
    assert fracos and fracos[0].sensor_id == "SNS-0602"


# ---------------------------------------------------------------------------
# Motor de regras + guardrails
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("caso", list(Caso))
@pytest.mark.parametrize("talhao", ["TLH-01", "TLH-03", "TLH-05", "TLH-06"])
def test_motor_de_regras_passa_nos_proprios_guardrails(
    packs: dict[str, ContextPack], talhao: str, caso: Caso
) -> None:
    pack = packs[talhao]
    saida = regras.gerar(pack, caso)
    resultado = validar(saida, pack, caso)
    assert resultado.aprovado, [v.mensagem for v in resultado.criticas]
    assert resultado.numeros_nao_aterrados == [], resultado.numeros_nao_aterrados
    assert saida.talhao_id == talhao and saida.caso is caso
    assert 1 <= len(saida.recomendacoes) <= 6


def test_sequeiro_nunca_recebe_irrigacao_pelas_regras(packs: dict[str, ContextPack]) -> None:
    for caso in Caso:
        saida = regras.gerar(packs["TLH-06"], caso)
        assert "irrigacao" not in saida.tipos


def _saida_base(pack: ContextPack, caso: Caso = Caso.IRRIGACAO) -> SaidaCopiloto:
    return regras.gerar(pack, caso)


def test_guardrail_bloqueia_irrigacao_em_sequeiro(packs: dict[str, ContextPack]) -> None:
    pack = packs["TLH-06"]
    saida = _saida_base(pack)
    saida = saida.model_copy(
        update={
            "recomendacoes": [
                Recomendacao(
                    prioridade="alta",
                    tipo="irrigacao",
                    acao="Irrigar 20 mm no canavial",
                    detalhe="Solo seco, irrigar para recuperar o vigor da lavoura.",
                )
            ]
        }
    )
    resultado = validar(saida, pack, Caso.IRRIGACAO)
    assert not resultado.aprovado
    assert any(v.codigo == "G2-sequeiro" for v in resultado.criticas)


def test_guardrail_bloqueia_irrigacao_em_solo_umido(packs: dict[str, ContextPack]) -> None:
    pack = packs["TLH-03"]
    umido = pack.model_copy(
        update={"hidrico": pack.hidrico.model_copy(update={"fracao_agua_disponivel_atual": 0.95})}
    )
    saida = _saida_base(umido).model_copy(
        update={
            "recomendacoes": [
                Recomendacao(
                    prioridade="alta",
                    tipo="irrigacao",
                    acao="Irrigar o talhão agora",
                    detalhe="Aplicar a lâmina sugerida hoje mesmo para garantir o estádio.",
                )
            ]
        }
    )
    resultado = validar(saida, umido, Caso.IRRIGACAO)
    assert any(v.codigo == "G2-umido" for v in resultado.criticas)


def test_guardrail_rejeita_numeros_inventados_em_excesso(packs: dict[str, ContextPack]) -> None:
    pack = packs["TLH-01"]
    saida = _saida_base(pack).model_copy(
        update={
            "diagnostico": "A umidade caiu de 47,3 % para 18,9 % em 6 dias, NDVI de 0,912 e "
            "déficit de 61,7 mm; a produtividade esperada é de 12,8 t/ha."
        }
    )
    resultado = validar(saida, pack, Caso.IRRIGACAO)
    assert not resultado.aprovado
    assert any(
        v.codigo == "G3-aterramento" and v.gravidade == "critica" for v in resultado.criticas
    )
    assert len(resultado.numeros_nao_aterrados) >= 4


def test_guardrail_anota_numero_solto_e_rebaixa_confianca(packs: dict[str, ContextPack]) -> None:
    pack = packs["TLH-01"]
    base = _saida_base(pack)
    saida = base.model_copy(
        update={
            "diagnostico": base.diagnostico + " A produtividade esperada é de 12,8 t/ha.",
            "confianca": "alta",
        }
    )
    resultado = validar(saida, pack, Caso.IRRIGACAO)
    assert resultado.aprovado and resultado.saida is not None
    assert resultado.numeros_nao_aterrados == ["12,8"]
    assert resultado.saida.confianca in {"media", "baixa"}
    assert any("12,8" in item for item in resultado.saida.limitacoes)


def test_guardrail_rebaixa_confianca_alta_com_ndvi_de_baixa_confianca(
    packs: dict[str, ContextPack],
) -> None:
    pack = packs["TLH-02"]
    nublado = pack.model_copy(
        update={"vegetacao": pack.vegetacao.model_copy(update={"confianca_ultimo": "baixa"})}
    )
    saida = regras.gerar(nublado, Caso.FITOSSANITARIO).model_copy(update={"confianca": "alta"})
    resultado = validar(saida, nublado, Caso.FITOSSANITARIO)
    assert resultado.aprovado and resultado.saida is not None
    assert resultado.saida.confianca == "media"
    assert any(v.codigo == "G4-confianca" for v in resultado.ajustes)


def test_guardrail_remove_dados_usados_fora_do_contexto(packs: dict[str, ContextPack]) -> None:
    pack = packs["TLH-01"]
    saida = _saida_base(pack).model_copy(
        update={"dados_usados": ["hidrico.deficit_atual_mm", "previsao.chuva_amanha"]}
    )
    resultado = validar(saida, pack, Caso.IRRIGACAO)
    assert resultado.aprovado and resultado.saida is not None
    assert resultado.saida.dados_usados == ["hidrico.deficit_atual_mm"]
    assert any(v.codigo == "G5-dados-usados" for v in resultado.ajustes)


def test_guardrail_identidade(packs: dict[str, ContextPack]) -> None:
    pack = packs["TLH-01"]
    saida = _saida_base(pack).model_copy(update={"talhao_id": "TLH-02"})
    resultado = validar(saida, pack, Caso.IRRIGACAO)
    assert any(v.codigo == "G1-talhao" for v in resultado.criticas)


def test_extrair_numeros_ignora_identificadores_datas_e_horas() -> None:
    numeros = extrair_numeros(
        "Sensor SNS-0302 do TLH-03 em 2026-08-28, laudo LAU-2026-00474 e operação OPR-2026-018001, "
        "janela 04h–07h, camada 0-20 cm: umidade 18,4 %, 1.234,5 mm e déficit de 25.7 mm em 28/08."
    )
    valores = [v for _, v in numeros]
    assert 18.4 in valores and 1234.5 in valores and 25.7 in valores
    assert 302.0 not in valores and 2026.0 not in valores and 28.0 not in valores
    assert -474.0 not in valores and 474.0 not in valores and 18001.0 not in valores


def test_literais_do_pacote_podem_ser_citados(packs: dict[str, ContextPack]) -> None:
    """Variedade 'AG 8088 PRO4' contém 8088, que não é um número inventado."""
    pack = packs["TLH-01"]
    assert pack.talhao.variedade in pack.textos()
    saida = _saida_base(pack).model_copy(
        update={
            "diagnostico": f"O híbrido {pack.talhao.variedade} do talhão está em fase reprodutiva."
        }
    )
    resultado = validar(saida, pack, Caso.IRRIGACAO)
    assert resultado.numeros_nao_aterrados == []


# ---------------------------------------------------------------------------
# Copiloto: motor, fallback, proveniência
# ---------------------------------------------------------------------------


class _Dublê:
    """Motor generativo falso: devolve o que lhe mandarem."""

    nome = "claude"

    def __init__(self, resposta: dict[str, Any] | Exception) -> None:
        self.resposta = resposta
        self.chamadas: list[dict[str, Any]] = []

    def disponivel(self) -> bool:
        return True

    def gerar_estruturado(self, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        self.chamadas.append(kwargs)
        if isinstance(self.resposta, Exception):
            raise self.resposta
        return self.resposta, {
            "modelo": "dublê",
            "input_tokens": 1000,
            "output_tokens": 300,
            "latencia_s": 0.1,
        }


def test_copiloto_aceita_saida_valida_do_modelo(
    lake_processado: dict, packs: dict[str, ContextPack]
) -> None:
    pack = packs["TLH-03"]
    boa = regras.gerar(pack, Caso.IRRIGACAO).model_dump(mode="json")
    dublê = _Dublê(boa)
    copiloto = Copiloto(lake_processado["cfg"], cliente=dublê, motor_preferido="claude")
    resposta = copiloto.responder(pack, Caso.IRRIGACAO)
    assert resposta.motor == "claude" and not resposta.fallback
    assert resposta.prompt_sistema == "sistema.v1" and resposta.prompt_caso == "irrigacao.v1"
    assert resposta.uso and resposta.uso["input_tokens"] == 1000
    chamada = dublê.chamadas[0]
    assert chamada["ferramenta"] == "emitir_recomendacao"
    assert "irrigado" in chamada["sistema"] and pack.talhao.talhao_id in chamada["usuario"]
    assert chamada["schema"]["title"] == "SaidaCopiloto"


def test_copiloto_cai_no_fallback_quando_o_modelo_viola_guardrail(
    lake_processado: dict, packs: dict[str, ContextPack]
) -> None:
    pack = packs["TLH-06"]
    ruim = regras.gerar(pack, Caso.IRRIGACAO).model_dump(mode="json")
    ruim["recomendacoes"] = [
        {
            "prioridade": "alta",
            "tipo": "irrigacao",
            "acao": "Irrigar 25 mm no canavial",
            "detalhe": "O solo está seco e a cana precisa de água agora para não perder produção.",
            "janela_execucao": None,
            "dados_usados": [],
        }
    ]
    copiloto = Copiloto(lake_processado["cfg"], cliente=_Dublê(ruim), motor_preferido="claude")
    resposta = copiloto.responder(pack, Caso.IRRIGACAO)
    assert resposta.motor == "regras" and resposta.fallback
    assert resposta.guardrails_rejeitados is not None
    assert any(v.codigo == "G2-sequeiro" for v in resposta.guardrails_rejeitados.criticas)
    assert "irrigacao" not in resposta.saida.tipos
    assert resposta.saida_rejeitada is not None


def test_copiloto_cai_no_fallback_com_json_fora_do_schema(
    lake_processado: dict, packs: dict[str, ContextPack]
) -> None:
    copiloto = Copiloto(
        lake_processado["cfg"], cliente=_Dublê({"diagnostico": "só isso"}), motor_preferido="claude"
    )
    resposta = copiloto.responder(packs["TLH-01"], Caso.EXECUTIVO)
    assert resposta.fallback and resposta.motor == "regras"
    assert resposta.erro_motor and "schema" in resposta.erro_motor


def test_copiloto_cai_no_fallback_com_erro_de_api(
    lake_processado: dict, packs: dict[str, ContextPack]
) -> None:
    copiloto = Copiloto(
        lake_processado["cfg"],
        cliente=_Dublê(ErroCliente("API Claude: timeout")),
        motor_preferido="claude",
    )
    resposta = copiloto.responder(packs["TLH-01"], Caso.FITOSSANITARIO)
    assert resposta.fallback and "timeout" in (resposta.erro_motor or "")
    assert resposta.guardrails.aprovado


def test_copiloto_sem_chave_usa_regras_sem_fallback(
    lake_processado: dict, packs: dict[str, ContextPack]
) -> None:
    cfg = lake_processado["cfg"]
    assert cfg.anthropic_api_key is None
    copiloto = Copiloto(cfg, motor_preferido="auto")
    resposta = copiloto.responder(packs["TLH-01"], Caso.IRRIGACAO)
    assert resposta.motor == "regras" and not resposta.fallback and resposta.erro_motor is None


# ---------------------------------------------------------------------------
# Prompts, schema, renderização, avaliação
# ---------------------------------------------------------------------------


def test_prompts_versionados_carregam_e_renderizam(packs: dict[str, ContextPack]) -> None:
    assert set(listar()) == {"sistema", "irrigacao", "fitossanitario", "executivo"}
    assert carregar("sistema").versao == 1
    _, do_caso, sistema, usuario = montar(Caso.IRRIGACAO, packs["TLH-03"])
    assert do_caso.rotulo == "irrigacao.v1"
    assert "{{" not in usuario and '"talhao_id": "TLH-03"' in usuario
    assert "Trava agronômica" in sistema


def test_schema_json_da_saida_e_util_para_tool_use() -> None:
    schema = esquema_json_saida()
    assert schema["type"] == "object"
    assert set(schema["required"]) >= {
        "caso",
        "talhao_id",
        "diagnostico",
        "recomendacoes",
        "confianca",
    }


def test_render_markdown_e_pdf(
    lake_processado: dict, packs: dict[str, ContextPack], tmp_path
) -> None:
    cfg = lake_processado["cfg"]
    resposta = Copiloto(cfg, motor_preferido="regras").responder(packs["TLH-03"], Caso.IRRIGACAO)
    md = para_markdown(resposta)
    assert "## Diagnóstico" in md and "## Recomendações" in md and "## Proveniência" in md
    assert "Motor: **regras**" in md
    pdf = para_pdf(resposta)
    assert pdf.startswith(b"%PDF-1.4") and b"%%EOF" in pdf
    caminhos = salvar(resposta, cfg)
    assert {c.suffix for c in caminhos} == {".md", ".pdf", ".json"}
    proveniencia = json.loads(
        next(c for c in caminhos if c.name.endswith("proveniencia.json")).read_text(
            encoding="utf-8"
        )
    )
    assert proveniencia["motor"] == "regras" and proveniencia["guardrails"]["aprovado"]


def test_avaliacao_so_com_regras(lake_processado: dict) -> None:
    cfg = lake_processado["cfg"]
    relatorio = avaliar(cfg, talhoes=["TLH-01", "TLH-06"], incluir_modelo=False)
    assert relatorio["motor_generativo_ativo"] is False
    regras_ = relatorio["por_motor"]["regras"]
    assert (
        regras_["execucoes"] == 6
        and regras_["aprovados_guardrails"] == 6
        and regras_["fallbacks"] == 0
    )
    assert (cfg.dir_outputs / "avaliacao_ia.json").exists()
    assert (cfg.dir_outputs / "relatorios" / "avaliacao_ia.md").exists()


def test_avaliacao_com_modelo_mede_concordancia(
    lake_processado: dict, packs: dict[str, ContextPack]
) -> None:
    cfg = lake_processado["cfg"]

    class _DublêEspelho(_Dublê):
        """Devolve a saída das regras do próprio pedido — concordância total."""

        _CASO_POR_TITULO = {
            "recomendação de irrigação": Caso.IRRIGACAO,
            "risco fitossanitário": Caso.FITOSSANITARIO,
            "resumo executivo": Caso.EXECUTIVO,
        }

        def gerar_estruturado(self, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
            usuario: str = kwargs["usuario"]
            talhao = next(t for t in packs if f'"talhao_id": "{t}"' in usuario)
            primeira_linha = usuario.splitlines()[0].lower()
            caso = next(
                c for titulo, c in self._CASO_POR_TITULO.items() if titulo in primeira_linha
            )
            saida = regras.gerar(packs[talhao], caso).model_dump(mode="json")
            return saida, {"modelo": "espelho", "input_tokens": 1, "output_tokens": 1}

    relatorio = avaliar(cfg, talhoes=["TLH-03"], cliente=_DublêEspelho({}), incluir_modelo=True)
    assert relatorio["motor_generativo_ativo"] is True
    conc = relatorio["concordancia_regras_vs_modelo"]
    assert (
        conc["pares"] == 3
        and conc["irrigar_igual_pct"] == 100.0
        and conc["tipos_jaccard_medio"] == 1.0
    )
