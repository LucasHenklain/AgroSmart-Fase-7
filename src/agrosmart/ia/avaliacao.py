"""Avaliação do copiloto: casos × talhões × motores, com números em vez de adjetivos.

Para cada combinação registra: aprovação nos guardrails, ajustes aplicados,
fallback, confiança, prioridade máxima, tipos de recomendação, decisão de
irrigar e latência. Quando os dois motores rodam, mede a **concordância**
(mesma decisão de irrigar, mesma prioridade máxima, sobreposição dos tipos).

É o que transforma "a IA funciona" em ``outputs/avaliacao_ia.json``.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from agrosmart.config import Config, carregar_config
from agrosmart.ia.agente import Copiloto, Resposta
from agrosmart.ia.cliente_claude import GeradorEstruturado
from agrosmart.ia.contexto import (
    ContextPack,
    abrir_gold,
    construir_context_pack,
    talhoes_disponiveis,
)
from agrosmart.ia.esquema import Caso
from agrosmart.log import obter_logger


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return round(len(a & b) / len(a | b), 3)


def _concordancia(regras: Resposta, modelo: Resposta) -> dict[str, Any]:
    return {
        "irrigar_igual": regras.saida.recomenda_irrigar() == modelo.saida.recomenda_irrigar(),
        "prioridade_igual": regras.saida.prioridade_maxima == modelo.saida.prioridade_maxima,
        "confianca_igual": regras.saida.confianca == modelo.saida.confianca,
        "tipos_jaccard": _jaccard(regras.saida.tipos, modelo.saida.tipos),
    }


def _resumo_motor(itens: list[dict[str, Any]]) -> dict[str, Any]:
    if not itens:
        return {"execucoes": 0}
    violacoes: dict[str, int] = {}
    for item in itens:
        for v in item["guardrails"]["criticas"] + item["guardrails"]["ajustes"]:
            violacoes[v["codigo"]] = violacoes.get(v["codigo"], 0) + 1
        for v in (item.get("guardrails_rejeitados") or {}).get("criticas", []):
            violacoes[v["codigo"] + " (rejeitada)"] = (
                violacoes.get(v["codigo"] + " (rejeitada)", 0) + 1
            )
    return {
        "execucoes": len(itens),
        "aprovados_guardrails": sum(1 for i in itens if i["guardrails"]["aprovado"]),
        "com_ajustes": sum(1 for i in itens if i["guardrails"]["ajustes"]),
        "fallbacks": sum(1 for i in itens if i["fallback"]),
        "latencia_media_s": round(sum(i["latencia_s"] for i in itens) / len(itens), 2),
        "confianca": {
            nivel: sum(1 for i in itens if i["confianca"] == nivel)
            for nivel in ("alta", "media", "baixa")
        },
        "recomenda_irrigar": sum(1 for i in itens if i["recomenda_irrigar"]),
        "violacoes_por_codigo": dict(sorted(violacoes.items())),
        "tokens_entrada": sum((i.get("uso") or {}).get("input_tokens", 0) for i in itens) or None,
        "tokens_saida": sum((i.get("uso") or {}).get("output_tokens", 0) for i in itens) or None,
    }


def avaliar(
    config: Config | None = None,
    *,
    casos: list[Caso] | None = None,
    talhoes: list[str] | None = None,
    cliente: GeradorEstruturado | None = None,
    incluir_modelo: bool = True,
) -> dict[str, Any]:
    cfg = config or carregar_config()
    logger = obter_logger("ia.avaliacao")
    inicio = time.perf_counter()
    casos = casos or list(Caso)

    con = abrir_gold(cfg)
    try:
        alvos = talhoes or talhoes_disponiveis(con)
        packs: dict[str, ContextPack] = {
            t: construir_context_pack(t, con=con, config=cfg) for t in alvos
        }
    finally:
        con.close()

    copiloto_regras = Copiloto(cfg, cliente=cliente, motor_preferido="regras")
    copiloto_modelo = (
        Copiloto(cfg, cliente=cliente, motor_preferido="claude") if incluir_modelo else None
    )
    modelo_ativo = bool(copiloto_modelo and copiloto_modelo.usa_modelo)

    execucoes: list[dict[str, Any]] = []
    concordancias: list[dict[str, Any]] = []
    for talhao, pack in packs.items():
        for caso in casos:
            r_regras = copiloto_regras.responder(pack, caso)
            execucoes.append(r_regras.resumo())
            if modelo_ativo and copiloto_modelo is not None:
                r_modelo = copiloto_modelo.responder(pack, caso)
                execucoes.append(r_modelo.resumo())
                if not r_modelo.fallback:
                    concordancias.append(
                        {
                            "talhao_id": talhao,
                            "caso": caso.value,
                            **_concordancia(r_regras, r_modelo),
                        }
                    )
            logger.info("%s/%s avaliado", talhao, caso.value)

    por_motor = {
        motor: _resumo_motor([e for e in execucoes if e["motor"] == motor])
        for motor in sorted({e["motor"] for e in execucoes})
    }
    resumo_concordancia = None
    if concordancias:
        n = len(concordancias)
        resumo_concordancia = {
            "pares": n,
            "irrigar_igual_pct": round(100 * sum(c["irrigar_igual"] for c in concordancias) / n, 1),
            "prioridade_igual_pct": round(
                100 * sum(c["prioridade_igual"] for c in concordancias) / n, 1
            ),
            "confianca_igual_pct": round(
                100 * sum(c["confianca_igual"] for c in concordancias) / n, 1
            ),
            "tipos_jaccard_medio": round(sum(c["tipos_jaccard"] for c in concordancias) / n, 3),
        }

    relatorio = {
        "gerado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "instante_referencia": cfg.instante_referencia.isoformat(),
        "modelo_generativo": cfg.ia_modelo if modelo_ativo else None,
        "motor_generativo_ativo": modelo_ativo,
        "talhoes": list(packs),
        "casos": [c.value for c in casos],
        "por_motor": por_motor,
        "concordancia_regras_vs_modelo": resumo_concordancia,
        "concordancias": concordancias,
        "execucoes": execucoes,
        "duracao_s": round(time.perf_counter() - inicio, 2),
    }
    cfg.dir_outputs.mkdir(parents=True, exist_ok=True)
    (cfg.dir_outputs / "avaliacao_ia.json").write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (cfg.dir_outputs / "relatorios").mkdir(parents=True, exist_ok=True)
    (cfg.dir_outputs / "relatorios" / "avaliacao_ia.md").write_text(
        relatorio_markdown(relatorio), encoding="utf-8"
    )
    return relatorio


def relatorio_markdown(relatorio: dict[str, Any]) -> str:
    linhas = [
        "# Avaliação do copiloto",
        "",
        f"Gerado em {relatorio['gerado_em']} · referência {relatorio['instante_referencia']} · "
        f"{len(relatorio['talhoes'])} talhões × {len(relatorio['casos'])} casos",
        "",
        "Motor generativo: "
        + (
            f"`{relatorio['modelo_generativo']}`"
            if relatorio["motor_generativo_ativo"]
            else "**inativo** (sem chave de API — só o motor de regras foi avaliado)"
        ),
        "",
        "## Por motor",
        "",
        "| Motor | Execuções | Aprovadas nos guardrails | Com ajustes | Fallbacks | Latência média | Confiança alta/média/baixa | Recomenda irrigar |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for motor, m in relatorio["por_motor"].items():
        if not m.get("execucoes"):
            continue
        conf = m["confianca"]
        linhas.append(
            f"| {motor} | {m['execucoes']} | {m['aprovados_guardrails']} | {m['com_ajustes']} | {m['fallbacks']} | "
            f"{m['latencia_media_s']} s | {conf['alta']}/{conf['media']}/{conf['baixa']} | {m['recomenda_irrigar']} |"
        )
    linhas.append("")
    for motor, m in relatorio["por_motor"].items():
        if m.get("violacoes_por_codigo"):
            linhas.append(
                f"Violações de guardrail ({motor}): "
                + ", ".join(f"{k} ×{v}" for k, v in m["violacoes_por_codigo"].items())
            )
    conc = relatorio.get("concordancia_regras_vs_modelo")
    if conc:
        linhas += [
            "",
            "## Concordância regras × modelo",
            "",
            f"- Pares comparados: {conc['pares']}",
            f"- Mesma decisão de irrigar: {conc['irrigar_igual_pct']} %",
            f"- Mesma prioridade máxima: {conc['prioridade_igual_pct']} %",
            f"- Mesma confiança: {conc['confianca_igual_pct']} %",
            f"- Sobreposição dos tipos de recomendação (Jaccard médio): {conc['tipos_jaccard_medio']}",
        ]
    linhas += [
        "",
        "## Execuções",
        "",
        "| Talhão | Caso | Motor | Fallback | Confiança | Prioridade máx. | Tipos | Irrigar | Guardrails |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for e in relatorio["execucoes"]:
        g = e["guardrails"]
        estado = ("ok" if g["aprovado"] else "REPROVADO") + (
            f" ({len(g['ajustes'])} ajuste)" if g["ajustes"] else ""
        )
        linhas.append(
            f"| {e['talhao_id']} | {e['caso']} | {e['motor']} | {'sim' if e['fallback'] else 'não'} | {e['confianca']} | "
            f"{e['prioridade_maxima']} | {', '.join(e['tipos'])} | {'sim' if e['recomenda_irrigar'] else 'não'} | {estado} |"
        )
    linhas.append("")
    return "\n".join(linhas)
