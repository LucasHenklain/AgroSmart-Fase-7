"""Escritor da fonte ``laudos_solo``: PDFs de laboratório com sidecar de extração.

O PDF é o documento legal — texto de verdade, com cabeçalho do laboratório,
tabela de resultados e interpretação. O sidecar representa o que um extrator
(OCR/LLM) leu do PDF, incluindo a versão do extrator: é por isso que os dois
defeitos injetados aqui são *erros de extração*, não de laboratório.
"""

from __future__ import annotations

from agrosmart.simuladores.base import Contexto, DefeitoInjetado, arredondar
from agrosmart.simuladores.defeitos import POR_ID, distribuir_posicoes
from agrosmart.simuladores.motor import Trajetoria
from agrosmart.simuladores.pdf import gerar_pdf

FONTE = "laudos_solo"
SUBPASTA = "laudos"
LABORATORIO = "LabSolo Campinas Análises Agronômicas"
EXTRATOR = "extrator-laudo-v1.2"

# Valores-base por classe de solo: (pH CaCl2, MO g/dm³, P mg/dm³, K, Ca, Mg mmolc/dm³, H+Al)
_BASE_SOLO: dict[str, tuple[float, float, float, float, float, float, float]] = {
    "latossolo vermelho": (5.3, 28.0, 18.0, 3.2, 30.0, 12.0, 30.0),
    "latossolo vermelho-amarelo": (5.1, 24.0, 14.0, 2.6, 24.0, 9.0, 34.0),
    "argissolo vermelho-amarelo": (5.0, 22.0, 12.0, 2.4, 22.0, 8.0, 36.0),
    "argissolo vermelho": (5.0, 21.0, 11.0, 2.2, 21.0, 8.0, 38.0),
    "nitossolo vermelho": (5.6, 31.0, 22.0, 3.8, 36.0, 14.0, 26.0),
}


def _interpretar(v_pct: float, p: float) -> str:
    if v_pct < 50:
        calagem = "recomenda-se calagem para elevar V% a 70"
    elif v_pct < 65:
        calagem = "calagem de manutenção recomendada"
    else:
        calagem = "saturação por bases adequada"
    fosforo = "fósforo baixo" if p < 12 else ("fósforo médio" if p < 25 else "fósforo alto")
    return f"{calagem}; {fosforo}."


def gerar_laudos(ctx: Contexto, traj: Trajetoria) -> None:
    pasta = ctx.landing(SUBPASTA)
    rng = ctx.rng("laudos")
    rng_def = ctx.rng("defeitos.laudos")

    posicoes = distribuir_posicoes(
        rng_def, len(traj.laudos), [POR_ID[t] for t in ("LAU-PH", "LAU-DAT")]
    )

    for indice, agenda in enumerate(traj.laudos):
        talhao = ctx.talhao(agenda.talhao_id)
        fazenda = ctx.fazenda_de(talhao)
        base = _BASE_SOLO[talhao.tipo_solo]
        profundo = agenda.profundidade_cm == "20-40"
        fator = 0.72 if profundo else 1.0

        ph = base[0] + (0.0 if not profundo else -0.2) + rng.gauss(0.0, 0.12)
        mo = base[1] * fator + rng.gauss(0.0, 1.5)
        p = base[2] * fator + rng.gauss(0.0, 1.8)
        k = base[3] * fator + rng.gauss(0.0, 0.25)
        ca = base[4] * fator + rng.gauss(0.0, 2.0)
        mg = base[5] * fator + rng.gauss(0.0, 0.8)
        h_al = base[6] * (1.15 if profundo else 1.0) + rng.gauss(0.0, 2.0)
        soma_bases = k + ca + mg
        ctc = soma_bases + h_al
        v_pct = soma_bases / ctc * 100

        valores = {
            "ph_cacl2": arredondar(ph, 1),
            "materia_organica_g_dm3": arredondar(max(mo, 5.0), 1),
            "fosforo_mg_dm3": arredondar(max(p, 1.0), 1),
            "potassio_mmolc_dm3": arredondar(max(k, 0.3), 1),
            "calcio_mmolc_dm3": arredondar(max(ca, 3.0), 1),
            "magnesio_mmolc_dm3": arredondar(max(mg, 1.0), 1),
            "ctc_mmolc_dm3": arredondar(ctc, 1),
            "saturacao_bases_pct": arredondar(v_pct, 1),
        }

        interpretacao = _interpretar(valores["saturacao_bases_pct"], valores["fosforo_mg_dm3"])
        linhas = [
            f"Laudo nº {agenda.laudo_id}    Emissão: {agenda.emitido_em:%d/%m/%Y}",
            "Rua das Análises, 120 - Campinas/SP - CRL-SP 04.512",
            "",
            f"Cliente: {fazenda.nome} ({fazenda.id}) - {fazenda.municipio}",
            f"Talhão: {talhao.id} - {talhao.nome}",
            f"Cultura: {talhao.cultura}    Área: {talhao.area_ha} ha",
            f"Coordenadas: {talhao.latitude:.4f}, {talhao.longitude:.4f}",
            f"Coleta: {agenda.coletado_em:%d/%m/%Y}    Profundidade: {agenda.profundidade_cm} cm",
            "",
            "RESULTADOS ANALÍTICOS",
            f"  pH (CaCl2 0,01 mol/L) ............. {valores['ph_cacl2']}",
            f"  Matéria orgânica (g/dm³) .......... {valores['materia_organica_g_dm3']}",
            f"  Fósforo - resina (mg/dm³) ......... {valores['fosforo_mg_dm3']}",
            f"  Potássio (mmolc/dm³) .............. {valores['potassio_mmolc_dm3']}",
            f"  Cálcio (mmolc/dm³) ................ {valores['calcio_mmolc_dm3']}",
            f"  Magnésio (mmolc/dm³) .............. {valores['magnesio_mmolc_dm3']}",
            f"  H+Al (mmolc/dm³) .................. {arredondar(h_al, 1)}",
            f"  CTC a pH 7 (mmolc/dm³) ............ {valores['ctc_mmolc_dm3']}",
            f"  Saturação por bases - V (%) ....... {valores['saturacao_bases_pct']}",
            "",
            "MÉTODO: Sistema IAC (Raij et al., 2001).",
            "Extração de P e K por resina trocadora de íons.",
            f"INTERPRETAÇÃO: {interpretacao}",
            "",
            "Responsável técnico: Eng. Agr. Helena Matsuda - CREA-SP 5069xxxxx",
            "Documento gerado eletronicamente. Válido sem assinatura.",
        ]
        pdf = gerar_pdf(linhas, titulo=LABORATORIO)

        sidecar: dict[str, object] = {
            "laudo_id": agenda.laudo_id,
            "talhao_id": talhao.id,
            "laboratorio": LABORATORIO,
            "coletado_em": agenda.coletado_em.isoformat(),
            "emitido_em": agenda.emitido_em.isoformat(),
            "profundidade_cm": agenda.profundidade_cm,
            **valores,
            "extrator_versao": EXTRATOR,
        }

        nome_pdf, nome_json = f"{agenda.laudo_id}.pdf", f"{agenda.laudo_id}.json"
        if indice in posicoes["LAU-PH"]:
            sidecar["ph_cacl2"] = 11.2
            ctx.registro.adicionar(
                DefeitoInjetado(
                    "LAU-PH",
                    FONTE,
                    "LAU-03",
                    {"laudo_id": agenda.laudo_id},
                    nome_json,
                    POR_ID["LAU-PH"].descricao,
                )
            )
        if indice in posicoes["LAU-DAT"]:
            sidecar["coletado_em"], sidecar["emitido_em"] = (
                sidecar["emitido_em"],
                sidecar["coletado_em"],
            )
            ctx.registro.adicionar(
                DefeitoInjetado(
                    "LAU-DAT",
                    FONTE,
                    "LAU-06",
                    {"laudo_id": agenda.laudo_id},
                    nome_json,
                    POR_ID["LAU-DAT"].descricao,
                )
            )

        ctx.gravar_bytes(FONTE, pasta / nome_pdf, pdf, registros=0)
        ctx.gravar_json(FONTE, pasta / nome_json, sidecar, registros=1)
