"""Orquestração ponta a ponta: uma execução, todas as etapas, um resumo.

``executar`` encadeia simular → ingerir → silver → gold → catálogo → copiloto,
mede cada etapa, para na primeira falha e grava ``outputs/run.json`` com o
que aconteceu. É o que ``agrosmart run --all`` chama — e o que um agendador
(cron, Step Functions, Airflow) chamaria em produção.

Backfill (``run --from A --to B``): reprocessa um intervalo de datas a partir
do bronze. Para fontes de **eventos** (sensores, clima, imagens, laudos) só os
lotes cujas partições caem no intervalo são lidos, e só essas partições do
silver são reescritas. Para fontes de **versões** (ERP), a deduplicação
precisa ver todo o histórico da chave, então a tabela inteira é recomputada
(é pequena). O gold é sempre derivado por inteiro do silver ([ADR 0008]).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from agrosmart.config import Config, carregar_config
from agrosmart.log import Evento, obter_logger, registrar_evento


@dataclass(slots=True)
class Etapa:
    nome: str
    status: str = "pendente"  # ok | erro | pulada
    duracao_s: float = 0.0
    resumo: dict[str, Any] = field(default_factory=dict)
    erro: str | None = None


def _resumir_simulacao(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "arquivos": sum(c["arquivos"] for c in r["fontes"].values()),
        "registros": sum(c["registros"] for c in r["fontes"].values()),
        "defeitos": len(r["defeitos"]),
    }


def _resumir_ingestao(r: dict[str, Any]) -> dict[str, Any]:
    t = r["totais"]
    return {
        "lotes": t["lotes"],
        "gravados": t["gravados"],
        "atualizados": t["atualizados"],
        "pulados": t["pulados"],
        "rejeitados": t["rejeitados"],
        "objetos_bronze": r["objetos_no_bronze"],
    }


def _resumir_silver(r: dict[str, Any]) -> dict[str, Any]:
    t = r["totais"]
    v = r["defeitos_injetados"]
    return {
        "lidos": t["lidos"],
        "aprovados": t["aprovados"],
        "quarentena": t["quarentena"],
        "substituidos": t["substituidos"],
        "reconciliacao_ok": t["reconciliacao_ok"],
        "defeitos_localizados": f"{v['localizados']}/{v['total']}" if v["total"] else "n/a",
        "intervalo": r.get("intervalo"),
    }


def _resumir_gold(r: dict[str, Any]) -> dict[str, Any]:
    return {"tabelas": len(r["tabelas"]), "puladas": len(r["puladas"])}


def _resumir_catalogo(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "fontes": len(r["fontes"]),
        "tabelas_gold": sum(1 for g in r["gold"].values() if g.get("existe")),
    }


def _resumir_ia(r: dict[str, Any]) -> dict[str, Any]:
    por_motor = r["por_motor"]
    return {
        "execucoes": sum(m.get("execucoes", 0) for m in por_motor.values()),
        "aprovadas": sum(m.get("aprovados_guardrails", 0) for m in por_motor.values()),
        "fallbacks": sum(m.get("fallbacks", 0) for m in por_motor.values()),
        "motor_generativo": r["modelo_generativo"],
        "concordancia": r.get("concordancia_regras_vs_modelo"),
    }


def executar(
    config: Config | None = None,
    *,
    simular_fontes: bool = True,
    ia: bool = True,
    backfill: tuple[date, date] | None = None,
    forcar_ingestao: bool = False,
    parar_no_erro: bool = True,
) -> dict[str, Any]:
    """Roda o pipeline completo (ou o backfill de um intervalo) e devolve o resumo."""
    cfg = config or carregar_config()
    logger = obter_logger("run")
    inicio_total = time.perf_counter()
    etapas: list[Etapa] = []

    Resumidor = Callable[[dict[str, Any]], dict[str, Any]]

    def rodar(nome: str, funcao: Callable[[], dict[str, Any]], resumir: Resumidor) -> bool:
        etapa = Etapa(nome)
        etapas.append(etapa)
        inicio = time.perf_counter()
        try:
            etapa.resumo = resumir(funcao())
            etapa.status = "ok"
        except Exception as erro:  # noqa: BLE001 - a etapa registra e a orquestração decide
            etapa.status = "erro"
            etapa.erro = f"{type(erro).__name__}: {erro}"
            logger.error("etapa %s falhou: %s", nome, etapa.erro)
        etapa.duracao_s = round(time.perf_counter() - inicio, 2)
        logger.info("etapa %-10s %-5s %6.1fs %s", nome, etapa.status, etapa.duracao_s, etapa.resumo)
        return etapa.status == "ok" or not parar_no_erro

    if backfill and simular_fontes:
        motivo = {"motivo": "backfill reprocessa o bronze existente"}
        etapas.append(Etapa("simular", "pulada", resumo=motivo))
        simular_fontes = False

    continuar = True
    if simular_fontes:
        from agrosmart.simuladores import simular

        continuar = rodar("simular", lambda: simular(cfg), _resumir_simulacao)
    else:
        if not backfill:
            etapas.append(Etapa("simular", "pulada", resumo={"motivo": "--sem-simular"}))

    if continuar:
        from agrosmart.ingestao import ingerir

        continuar = rodar(
            "ingerir",
            lambda: ingerir(cfg, forcar=forcar_ingestao, exportar_exemplos=not backfill),
            _resumir_ingestao,
        )
    if continuar:
        from agrosmart.processamento.silver import construir_silver

        continuar = rodar(
            "silver", lambda: construir_silver(cfg, intervalo=backfill), _resumir_silver
        )
    if continuar:
        from agrosmart.processamento.gold import construir_gold

        continuar = rodar("gold", lambda: construir_gold(cfg), _resumir_gold)
    if continuar:
        from agrosmart.catalogo import construir_catalogo, gerar_dicionario

        def _catalogo() -> dict[str, Any]:
            resultado = construir_catalogo(cfg)
            gerar_dicionario(cfg, resultado)
            return resultado

        continuar = rodar("catalogo", _catalogo, _resumir_catalogo)
    if continuar and ia:
        from agrosmart.ia.agente import Copiloto
        from agrosmart.ia.avaliacao import avaliar
        from agrosmart.ia.contexto import abrir_gold, construir_context_pack, talhoes_disponiveis
        from agrosmart.ia.esquema import Caso
        from agrosmart.ia.render import salvar

        def _copiloto() -> dict[str, Any]:
            copiloto = Copiloto(cfg)
            con = abrir_gold(cfg)
            try:
                packs = {
                    t: construir_context_pack(t, con=con, config=cfg)
                    for t in talhoes_disponiveis(con)
                }
            finally:
                con.close()
            for pack in packs.values():
                for caso in Caso:
                    salvar(copiloto.responder(pack, caso), cfg)
            return avaliar(cfg)

        continuar = rodar("copiloto", _copiloto, _resumir_ia)
    elif continuar:
        etapas.append(Etapa("copiloto", "pulada", resumo={"motivo": "--sem-ia"}))

    resumo = {
        "iniciado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "instante_referencia": cfg.instante_referencia.isoformat(),
        "backend": cfg.backend.value,
        "modo": "backfill" if backfill else "completo",
        "intervalo": [backfill[0].isoformat(), backfill[1].isoformat()] if backfill else None,
        "sucesso": all(e.status != "erro" for e in etapas),
        "etapas": [asdict(e) for e in etapas],
        "duracao_s": round(time.perf_counter() - inicio_total, 2),
    }
    cfg.dir_outputs.mkdir(parents=True, exist_ok=True)
    (cfg.dir_outputs / "run.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    registrar_evento(
        Evento(
            etapa="run",
            acao="concluida" if resumo["sucesso"] else "falhou",
            status="ok" if resumo["sucesso"] else "erro",
            mensagem=f"{resumo['modo']} · {sum(1 for e in etapas if e.status == 'ok')} etapas ok",
            metricas={"duracao_s": resumo["duracao_s"], "etapas": len(etapas)},
        ),
        cfg,
    )
    return resumo
