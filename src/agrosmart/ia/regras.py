"""Motor de regras: recomendações determinísticas a partir do Context Pack.

Cumpre três papéis:

1. **Fallback** — quando não há chave de API, a API falha ou a resposta do
   modelo é rejeitada pelos guardrails, a recomendação ainda sai, marcada
   como vinda das regras.
2. **Baseline de avaliação** — a concordância entre regras e modelo é o
   número que diz o que a IA generativa acrescenta (ou não).
3. **Referência de limiares** — as regras usam exatamente os limiares de
   ``agronomia.py`` que o gold usa. O modelo recebe os mesmos limiares no
   pacote; se discordar deles, o guardrail pergunta por quê.

Toda frase produzida aqui cita apenas números que estão no pacote — o motor
passa pelos mesmos guardrails que o modelo.
"""

from __future__ import annotations

from agrosmart.ia.contexto import ContextPack
from agrosmart.ia.esquema import Caso, Recomendacao, SaidaCopiloto

NOME_MOTOR = "regras"


def _fmt(valor: float | None, casas: int = 1) -> str:
    if valor is None:
        return "n/d"
    texto = f"{valor:.{casas}f}".replace(".", ",")
    return texto


def _confianca_por_dados(pack: ContextPack, *, exige_ndvi: bool = False) -> tuple[str, str]:
    """Confiança decorre da qualidade dos dados, não da convicção da regra."""
    motivos: list[str] = []
    nivel = 2  # 2 = alta, 1 = media, 0 = baixa
    cobertura = pack.sensores.menor_cobertura_7d_pct
    if cobertura is not None and cobertura < 80:
        nivel -= 1
        motivos.append(
            f"cobertura de leituras de {_fmt(cobertura)} % em pelo menos um dia da semana"
        )
    if pack.hidrico.dias_sem_clima_14d:
        nivel -= 1
        motivos.append(f"{pack.hidrico.dias_sem_clima_14d} dia(s) sem clima na série")
    if pack.hidrico.umidade_atual_pct is None:
        nivel -= 2
        motivos.append("sem leitura de umidade recente")
    if exige_ndvi and pack.vegetacao.confianca_ultimo in {None, "baixa"}:
        nivel -= 1
        motivos.append("última cena NDVI sem confiança (nuvem ou ausência)")
    if any(a.regra == "SEN-12" for a in pack.alertas):
        nivel -= 1
        motivos.append("lacuna recente em sensor do talhão (SEN-12)")
    confianca = {2: "alta", 1: "media"}.get(max(nivel, 0), "baixa")
    justificativa = (
        "dados completos: sensores com cobertura plena, clima em toda a série e sem alertas abertos"
        if not motivos
        else "; ".join(motivos)
    )
    return confianca, justificativa


def _recomendacoes_de_sensores(pack: ContextPack) -> list[Recomendacao]:
    saida: list[Recomendacao] = []
    for indice, sensor in enumerate(pack.sensores.sensores):
        if sensor.precisa_manutencao:
            saida.append(
                Recomendacao(
                    prioridade="media",
                    tipo="manutencao",
                    acao=f"Trocar a bateria do sensor {sensor.sensor_id}",
                    detalhe=(
                        f"Bateria em {_fmt(sensor.bateria_ultima_pct)} %, abaixo do limiar de "
                        f"{_fmt(pack.limiares.bateria_manutencao_pct)} %. O dispositivo ainda mede, "
                        "mas vai calar em poucos dias; programar a troca junto da próxima operação."
                    ),
                    janela_execucao="próxima entrada no talhão",
                    dados_usados=[
                        f"sensores.sensores[{indice}].bateria_ultima_pct",
                        "limiares.bateria_manutencao_pct",
                    ],
                )
            )
        if sensor.sinal_fraco:
            saida.append(
                Recomendacao(
                    prioridade="baixa",
                    tipo="manutencao",
                    acao=f"Reposicionar antena ou gateway para o sensor {sensor.sensor_id}",
                    detalhe=(
                        f"Sinal médio de {_fmt(sensor.rssi_medio_dbm)} dBm e {sensor.leituras_sinal_fraco} "
                        f"leitura(s) abaixo de {pack.limiares.rssi_fraco_dbm} dBm no período "
                        f"(fração {_fmt(sensor.fracao_sinal_fraco, 3)}): risco de perda de leituras."
                    ),
                    janela_execucao=None,
                    dados_usados=[
                        f"sensores.sensores[{indice}].rssi_medio_dbm",
                        f"sensores.sensores[{indice}].leituras_sinal_fraco",
                        "limiares.rssi_fraco_dbm",
                    ],
                )
            )
    return saida


def _irrigacao(pack: ContextPack) -> SaidaCopiloto:
    h, t, e = pack.hidrico, pack.talhao, pack.economia
    recs: list[Recomendacao] = []
    custo = None
    economia = None
    fad = h.fracao_agua_disponivel_atual

    if not t.irrigado:
        diagnostico = (
            f"Talhão {t.talhao_id} ({t.cultura}, {_fmt(t.area_ha)} ha) é de sequeiro: não há sistema de "
            f"irrigação, então a decisão é de acompanhamento. Fração de água disponível em "
            f"{_fmt(fad, 3)} e umidade média de {_fmt(h.umidade_atual_pct)} % em {h.ultimo_dia}; "
            f"{h.dias_em_estresse_14d} dia(s) em estresse nos últimos {pack.janela.dias}, chuva acumulada de "
            f"{_fmt(h.chuva_14d_mm)} mm."
        )
        recs.append(
            Recomendacao(
                prioridade="media" if h.estresse_hoje else "baixa",
                tipo="monitoramento",
                acao="Acompanhar o estresse hídrico do canavial pelo NDVI e pelos sensores",
                detalhe=(
                    f"Sem irrigação disponível, a resposta possível é operacional: com "
                    f"{h.dias_em_estresse_14d} dia(s) em estresse e NDVI de {_fmt(pack.vegetacao.ndvi_ultimo, 3)}, "
                    "priorizar o monitoramento da desuniformidade e planejar a colheita conforme a maturação."
                ),
                janela_execucao=None,
                dados_usados=[
                    "talhao.irrigado",
                    "hidrico.dias_em_estresse_14d",
                    "vegetacao.ndvi_ultimo",
                ],
            )
        )
    else:
        limiar = pack.limiares.fracao_irrigacao
        if fad is not None and fad < limiar:
            prioridade = "alta"
            custo = h.custo_lamina_sugerida_brl
            if h.estresse_hoje:
                economia = round(e.perda_por_dia_estresse_brl, 2)
            recs.append(
                Recomendacao(
                    prioridade=prioridade,
                    tipo="irrigacao",
                    acao=f"Irrigar {_fmt(h.lamina_sugerida_mm)} mm no talhão {t.talhao_id}",
                    detalhe=(
                        f"Fração de água disponível em {_fmt(fad, 3)}, abaixo do limiar de {_fmt(limiar, 2)} "
                        f"para irrigar; déficit de {_fmt(h.deficit_atual_mm)} mm até a capacidade de campo. "
                        f"Lâmina sugerida de {_fmt(h.lamina_sugerida_mm)} mm ao custo de "
                        f"R$ {_fmt(h.custo_lamina_sugerida_brl, 2)} "
                        f"({_fmt(h.custo_irrigacao_brl_mm_ha, 2)} R$/mm/ha)."
                        + (
                            f" O talhão está em {t.estadio_atual}, estádio crítico para água: cada dia de "
                            f"estresse custa {_fmt(e.perda_por_dia_estresse_pct)} % do potencial "
                            f"(R$ {_fmt(e.perda_por_dia_estresse_brl, 2)})."
                            if t.estadio_critico_para_agua
                            else ""
                        )
                    ),
                    janela_execucao="próxima madrugada, 04h–07h (menor evapotranspiração)",
                    dados_usados=[
                        "hidrico.fracao_agua_disponivel_atual",
                        "limiares.fracao_irrigacao",
                        "hidrico.deficit_atual_mm",
                        "hidrico.lamina_sugerida_mm",
                        "hidrico.custo_lamina_sugerida_brl",
                        "economia.perda_por_dia_estresse_brl",
                    ],
                )
            )
        elif fad is not None and fad < limiar + 0.15:
            recs.append(
                Recomendacao(
                    prioridade="media",
                    tipo="irrigacao",
                    acao=f"Programar irrigação do talhão {t.talhao_id} para os próximos dias",
                    detalhe=(
                        f"Fração de água disponível em {_fmt(fad, 3)}, aproximando-se do limiar de "
                        f"{_fmt(limiar, 2)}; variação de umidade de {_fmt(h.variacao_umidade_7d_pontos)} pontos "
                        f"em 7 dias e {_fmt(h.chuva_14d_mm)} mm de chuva em {pack.janela.dias} dias. "
                        "Sem previsão disponível, preparar a lâmina para quando o limiar for cruzado."
                    ),
                    janela_execucao="reavaliar diariamente",
                    dados_usados=[
                        "hidrico.fracao_agua_disponivel_atual",
                        "limiares.fracao_irrigacao",
                        "hidrico.variacao_umidade_7d_pontos",
                        "hidrico.chuva_14d_mm",
                    ],
                )
            )
        else:
            recs.append(
                Recomendacao(
                    prioridade="baixa",
                    tipo="monitoramento",
                    acao=f"Manter o manejo atual do talhão {t.talhao_id}",
                    detalhe=(
                        f"Fração de água disponível em {_fmt(fad, 3)}, acima do limiar de {_fmt(limiar, 2)}; "
                        f"umidade média de {_fmt(h.umidade_atual_pct)} % e {_fmt(h.irrigacao_14d_mm)} mm "
                        f"irrigados em {pack.janela.dias} dias. Nenhuma ação de irrigação necessária hoje."
                    ),
                    janela_execucao=None,
                    dados_usados=[
                        "hidrico.fracao_agua_disponivel_atual",
                        "hidrico.umidade_atual_pct",
                    ],
                )
            )
        diagnostico = (
            f"Talhão {t.talhao_id} ({t.cultura} {t.variedade or ''}, {_fmt(t.area_ha)} ha, "
            f"{t.sistema_irrigacao}) em {t.estadio_atual}, {t.dap_atual} dias após o plantio. "
            f"Umidade média de {_fmt(h.umidade_atual_pct)} % em {h.ultimo_dia}, fração de água disponível "
            f"{_fmt(fad, 3)}, déficit de {_fmt(h.deficit_atual_mm)} mm. Nos últimos {pack.janela.dias} dias: "
            f"{_fmt(h.chuva_14d_mm)} mm de chuva, {_fmt(h.etc_14d_mm)} mm de ETc, "
            f"{_fmt(h.irrigacao_14d_mm)} mm irrigados, {h.dias_em_estresse_14d} dia(s) em estresse."
        )

    recs.extend(_recomendacoes_de_sensores(pack))
    confianca, justificativa = _confianca_por_dados(pack)
    limitacoes = list(pack.avisos)
    return SaidaCopiloto(
        caso=Caso.IRRIGACAO,
        talhao_id=t.talhao_id,
        diagnostico=diagnostico,
        recomendacoes=recs[:6],
        custo_estimado_brl=custo,
        economia_estimada_brl=economia,
        confianca=confianca,
        justificativa_confianca=justificativa,
        limitacoes=limitacoes[:8],
        dados_usados=[
            "talhao",
            "hidrico",
            "serie_hidrica_14d",
            "limiares.fracao_irrigacao",
            "sensores",
        ],
    )


def _fitossanitario(pack: ContextPack) -> SaidaCopiloto:
    c, v, t, e = pack.clima_recente, pack.vegetacao, pack.talhao, pack.economia
    recs: list[Recomendacao] = []
    risco_clima = (
        c.dias_ur_alta_7d >= pack.limiares.dias_ur_alta_risco and c.temperatura_favoravel_a_fungos
    )
    queda_ndvi = (
        v.variacao_30d is not None and v.variacao_30d <= -0.05 and v.confianca_ultimo != "baixa"
    )
    intervalo_ok = (
        pack.custos.dias_desde_pulverizacao is None
        or pack.custos.dias_desde_pulverizacao >= pack.limiares.intervalo_min_pulverizacao_dias
    )
    custo = None

    if risco_clima and intervalo_ok:
        custo = e.custo_fungicida_talhao_brl
        recs.append(
            Recomendacao(
                prioridade="alta" if queda_ndvi else "media",
                tipo="fitossanitario",
                acao=f"Pulverização preventiva de fungicida no talhão {t.talhao_id}",
                detalhe=(
                    f"{c.dias_ur_alta_7d} dia(s) com umidade relativa acima de {_fmt(pack.limiares.ur_alta_pct)} % "
                    f"na última semana (média de {_fmt(c.ur_media_7d)} %) com temperatura média favorável "
                    f"a fungos (faixa {_fmt(pack.limiares.temp_risco_fungico_c[0])}–"
                    f"{_fmt(pack.limiares.temp_risco_fungico_c[1])} °C). Custo estimado de "
                    f"R$ {_fmt(e.custo_fungicida_talhao_brl, 2)} ({_fmt(e.custo_fungicida_brl_ha, 2)} R$/ha)."
                    + (
                        f" NDVI caiu {_fmt(v.variacao_30d, 3)} em 30 dias, reforçando o sinal."
                        if queda_ndvi
                        else ""
                    )
                ),
                janela_execucao="primeira janela sem chuva e com vento baixo",
                dados_usados=[
                    "clima_recente.dias_ur_alta_7d",
                    "clima_recente.temperatura_favoravel_a_fungos",
                    "limiares.dias_ur_alta_risco",
                    "economia.custo_fungicida_talhao_brl",
                ]
                + (["vegetacao.variacao_30d"] if queda_ndvi else []),
            )
        )
    elif risco_clima and not intervalo_ok:
        recs.append(
            Recomendacao(
                prioridade="media",
                tipo="monitoramento",
                acao=f"Inspecionar folhas do talhão {t.talhao_id} antes de reaplicar",
                detalhe=(
                    f"Clima favorável a fungos ({c.dias_ur_alta_7d} dia(s) de UR alta), mas a última "
                    f"pulverização foi há {pack.custos.dias_desde_pulverizacao} dia(s) — abaixo do intervalo "
                    f"mínimo de {pack.limiares.intervalo_min_pulverizacao_dias}. Inspeção dirigida antes de nova aplicação."
                ),
                janela_execucao="próximos 2 dias",
                dados_usados=[
                    "clima_recente.dias_ur_alta_7d",
                    "custos.dias_desde_pulverizacao",
                    "limiares.intervalo_min_pulverizacao_dias",
                ],
            )
        )
    elif queda_ndvi:
        recs.append(
            Recomendacao(
                prioridade="media",
                tipo="monitoramento",
                acao=f"Vistoriar em campo a queda de vigor do talhão {t.talhao_id}",
                detalhe=(
                    f"NDVI variou {_fmt(v.variacao_30d, 3)} em 30 dias (último {_fmt(v.ndvi_ultimo, 3)}, "
                    f"confiança {v.confianca_ultimo}) sem clima favorável a fungos: investigar estresse "
                    f"hídrico ({pack.hidrico.dias_em_estresse_14d} dia(s) em estresse), praga ou deficiência nutricional."
                ),
                janela_execucao="próximos 3 dias",
                dados_usados=[
                    "vegetacao.variacao_30d",
                    "vegetacao.ndvi_ultimo",
                    "hidrico.dias_em_estresse_14d",
                ],
            )
        )
    else:
        recs.append(
            Recomendacao(
                prioridade="baixa",
                tipo="monitoramento",
                acao=f"Manter o monitoramento semanal do talhão {t.talhao_id}",
                detalhe=(
                    f"Sem janela climática de risco ({c.dias_ur_alta_7d} dia(s) de UR alta em 7) e NDVI "
                    f"estável ({_fmt(v.ndvi_ultimo, 3)}, variação de {_fmt(v.variacao_30d, 3)} em 30 dias)."
                ),
                janela_execucao=None,
                dados_usados=[
                    "clima_recente.dias_ur_alta_7d",
                    "vegetacao.ndvi_ultimo",
                    "vegetacao.variacao_30d",
                ],
            )
        )

    diagnostico = (
        f"Risco fitossanitário do talhão {t.talhao_id} ({t.cultura}, {t.estadio_atual}). Última semana: "
        f"{c.dias_observados_7d} dia(s) de clima, UR média {_fmt(c.ur_media_7d)} %, {c.dias_ur_alta_7d} dia(s) "
        f"acima de {_fmt(pack.limiares.ur_alta_pct)} %, temperatura máxima média {_fmt(c.tmax_media_7d)} °C, "
        f"{c.dias_chuva_7d} dia(s) de chuva. Vegetação: NDVI {_fmt(v.ndvi_ultimo, 3)} "
        f"(confiança {v.confianca_ultimo}), variação de {_fmt(v.variacao_30d, 3)} em 30 dias, "
        f"{v.cenas_baixa_confianca_30d} de {v.cenas_30d} cenas com nuvem alta."
    )
    confianca, justificativa = _confianca_por_dados(pack, exige_ndvi=True)
    return SaidaCopiloto(
        caso=Caso.FITOSSANITARIO,
        talhao_id=t.talhao_id,
        diagnostico=diagnostico,
        recomendacoes=recs[:6],
        custo_estimado_brl=custo,
        economia_estimada_brl=None,
        confianca=confianca,
        justificativa_confianca=justificativa,
        limitacoes=list(pack.avisos)[:8],
        dados_usados=[
            "clima_recente",
            "vegetacao",
            "cenas_ndvi_30d",
            "custos.dias_desde_pulverizacao",
            "limiares",
        ],
    )


def _executivo(pack: ContextPack) -> SaidaCopiloto:
    t, h, v, cu, f = pack.talhao, pack.hidrico, pack.vegetacao, pack.custos, pack.fertilidade
    recs: list[Recomendacao] = []
    maior_tipo = max(cu.por_tipo_brl.items(), key=lambda kv: kv[1]) if cu.por_tipo_brl else None

    if f is not None and f.necessidade_calagem_t_ha > 0:
        recs.append(
            Recomendacao(
                prioridade="media",
                tipo="nutricao",
                acao=f"Planejar calagem de {_fmt(f.necessidade_calagem_t_ha, 2)} t/ha no talhão {t.talhao_id}",
                detalhe=(
                    f"Laudo {f.laudo_id} ({f.coletado_em}, {f.profundidade_cm} cm): saturação por bases de "
                    f"{_fmt(f.saturacao_bases_pct)} % (classe {f.classe_saturacao}), pH {_fmt(f.ph_cacl2)}. "
                    f"Fósforo {f.classe_fosforo} ({_fmt(f.fosforo_mg_dm3)} mg/dm³)."
                ),
                janela_execucao="entressafra, antes do próximo plantio",
                dados_usados=[
                    "fertilidade.necessidade_calagem_t_ha",
                    "fertilidade.saturacao_bases_pct",
                    "fertilidade.ph_cacl2",
                ],
            )
        )
    if h.dias_em_estresse_14d >= 3 and t.irrigado:
        recs.append(
            Recomendacao(
                prioridade="alta",
                tipo="gestao",
                acao=f"Revisar o gatilho de irrigação do talhão {t.talhao_id}",
                detalhe=(
                    f"{h.dias_em_estresse_14d} dia(s) em estresse hídrico em {pack.janela.dias} apesar de "
                    f"{_fmt(h.irrigacao_14d_mm)} mm irrigados: o gatilho está atrasado em relação ao limiar de "
                    f"{_fmt(pack.limiares.fracao_irrigacao, 2)} de água disponível. Cada dia de estresse custa "
                    f"R$ {_fmt(pack.economia.perda_por_dia_estresse_brl, 2)} em potencial produtivo."
                ),
                janela_execucao="esta semana",
                dados_usados=[
                    "hidrico.dias_em_estresse_14d",
                    "hidrico.irrigacao_14d_mm",
                    "economia.perda_por_dia_estresse_brl",
                ],
            )
        )
    recs.append(
        Recomendacao(
            prioridade="baixa",
            tipo="gestao",
            acao=f"Acompanhar o custo por hectare do talhão {t.talhao_id}",
            detalhe=(
                f"R$ {_fmt(cu.custo_total_brl, 2)} em {cu.operacoes} operação(ões) nos últimos "
                f"{cu.periodo_dias} dias (R$ {_fmt(cu.custo_por_ha_brl, 2)}/ha)"
                + (
                    f"; maior item: {maior_tipo[0]} com R$ {_fmt(maior_tipo[1], 2)}."
                    if maior_tipo
                    else "."
                )
            ),
            janela_execucao=None,
            dados_usados=[
                "custos.custo_total_brl",
                "custos.custo_por_ha_brl",
                "custos.por_tipo_brl",
            ],
        )
    )
    recs.extend(_recomendacoes_de_sensores(pack))

    diagnostico = (
        f"Resumo executivo do talhão {t.talhao_id} — {t.nome} ({t.cultura}, {_fmt(t.area_ha)} ha, "
        f"{t.estadio_atual}). Água: {h.dias_em_estresse_14d} dia(s) em estresse em {pack.janela.dias}, "
        f"{_fmt(h.chuva_14d_mm)} mm de chuva e {_fmt(h.irrigacao_14d_mm)} mm irrigados; fração de água disponível "
        f"atual {_fmt(h.fracao_agua_disponivel_atual, 3)}. Vegetação: NDVI {_fmt(v.ndvi_ultimo, 3)} "
        f"({v.confianca_ultimo}), variação {_fmt(v.variacao_30d, 3)} em 30 dias. Custos: R$ {_fmt(cu.custo_total_brl, 2)} "
        f"no período (R$ {_fmt(cu.custo_por_ha_brl, 2)}/ha). Valor de produção de referência: "
        f"R$ {_fmt(pack.economia.valor_producao_brl, 2)}. {len(pack.alertas)} alerta(s) de qualidade de dados relevante(s)."
    )
    confianca, justificativa = _confianca_por_dados(pack)
    return SaidaCopiloto(
        caso=Caso.EXECUTIVO,
        talhao_id=t.talhao_id,
        diagnostico=diagnostico,
        recomendacoes=recs[:6],
        custo_estimado_brl=None,
        economia_estimada_brl=None,
        confianca=confianca,
        justificativa_confianca=justificativa,
        limitacoes=list(pack.avisos)[:8],
        dados_usados=[
            "talhao",
            "hidrico",
            "vegetacao",
            "custos",
            "fertilidade",
            "economia",
            "alertas",
        ],
    )


def gerar(pack: ContextPack, caso: Caso) -> SaidaCopiloto:
    if caso is Caso.IRRIGACAO:
        return _irrigacao(pack)
    if caso is Caso.FITOSSANITARIO:
        return _fitossanitario(pack)
    return _executivo(pack)
