"""Renderização das respostas do copiloto: Markdown, PDF e JSON de proveniência."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from agrosmart.config import Config
from agrosmart.ia.agente import Resposta
from agrosmart.pdf import gerar_pdf

_TITULOS_CASO = {
    "irrigacao": "Recomendação de irrigação",
    "fitossanitario": "Risco fitossanitário",
    "executivo": "Resumo executivo",
}
_SIMBOLO_PRIORIDADE = {"alta": "ALTA", "media": "MÉDIA", "baixa": "BAIXA"}


def _brl(valor: float | None) -> str:
    if valor is None:
        return "não estimado"
    return "R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def para_markdown(resposta: Resposta) -> str:
    s, p = resposta.saida, resposta.pack
    t = p.talhao
    linhas = [
        f"# {_TITULOS_CASO[s.caso.value]} · {t.talhao_id} — {t.nome}",
        "",
        f"**Cultura:** {t.cultura} {t.variedade or ''} · **Área:** {t.area_ha} ha · "
        f"**Estádio:** {t.estadio_atual} ({t.dap_atual} dias após o plantio) · "
        f"**Irrigação:** {t.sistema_irrigacao}",
        "",
        f"**Referência:** {p.janela.hoje} · **Confiança:** {s.confianca} — {s.justificativa_confianca}",
        "",
        "## Diagnóstico",
        "",
        s.diagnostico,
        "",
        "## Recomendações",
        "",
    ]
    for indice, rec in enumerate(s.recomendacoes, start=1):
        janela = f" · _quando:_ {rec.janela_execucao}" if rec.janela_execucao else ""
        linhas += [
            f"{indice}. **[{_SIMBOLO_PRIORIDADE[rec.prioridade]}] {rec.acao}** ({rec.tipo}){janela}",
            f"   {rec.detalhe}",
            "",
        ]
    linhas += [
        f"**Custo estimado das ações:** {_brl(s.custo_estimado_brl)} · **Perda evitada / economia:** {_brl(s.economia_estimada_brl)}",
        "",
    ]
    if s.limitacoes:
        linhas += ["## Limitações", ""] + [f"- {item}" for item in s.limitacoes] + [""]
    linhas += [
        "## Dados usados",
        "",
        "Tabelas do gold: " + ", ".join(f"`{tab}`" for tab in p.tabelas_gold_usadas),
        "",
        "Campos do contexto: " + (", ".join(f"`{d}`" for d in s.dados_usados) or "—"),
        "",
        "## Proveniência",
        "",
        f"- Motor: **{resposta.motor}**"
        + (" (fallback após falha do modelo)" if resposta.fallback else ""),
    ]
    if resposta.prompt_sistema:
        linhas.append(f"- Prompts: `{resposta.prompt_sistema}`, `{resposta.prompt_caso}`")
    if resposta.uso:
        linhas.append(
            f"- Modelo: `{resposta.uso.get('modelo')}` · tokens {resposta.uso.get('input_tokens')} → "
            f"{resposta.uso.get('output_tokens')} · {resposta.uso.get('latencia_s')} s"
        )
    if resposta.erro_motor:
        linhas.append(f"- Motivo do fallback: {resposta.erro_motor}")
    ajustes = resposta.guardrails.ajustes
    linhas.append(
        f"- Guardrails: {'aprovado' if resposta.guardrails.aprovado else 'REPROVADO'}"
        + (
            f", {len(ajustes)} ajuste(s): " + "; ".join(v.mensagem for v in ajustes)
            if ajustes
            else ", sem ajustes"
        )
    )
    linhas += [
        f"- Context Pack `{p.versao}` gerado em {p.gerado_em.isoformat()} · instante de referência {p.instante_referencia.isoformat()}",
        "",
        "_O copiloto recomenda; o produtor decide. Toda cifra e medida acima vem do Data Lake da fazenda; "
        "nenhuma foi estimada pelo modelo._",
        "",
    ]
    return "\n".join(linhas)


_TITULO_MD = re.compile(r"^\s*#{1,6}\s*")
_NEGRITO_MD = re.compile(r"(\*\*|__)(.+?)\1")
# Ênfase só quando o marcador delimita uma palavra: `_texto_` sim, `pivo_central` não.
_ENFASE_MD = re.compile(r"(?<!\w)[*_](?=\S)(.+?)(?<=\S)[*_](?!\w)")
_CRASE_MD = re.compile(r"`([^`]*)`")


def _markdown_para_texto(md: str) -> list[str]:
    """Tira a marcação do Markdown sem mexer no texto — `dim_sensor` continua `dim_sensor`."""
    linhas: list[str] = []
    for linha in md.splitlines():
        texto = _TITULO_MD.sub("", linha)
        texto = _NEGRITO_MD.sub(r"\2", texto)
        texto = _ENFASE_MD.sub(r"\1", texto)
        texto = _CRASE_MD.sub(r"\1", texto)
        linhas.append(texto.rstrip())
    return linhas


def para_pdf(resposta: Resposta) -> bytes:
    md = para_markdown(resposta)
    corpo = _markdown_para_texto(md)
    titulo = corpo[0] if corpo else "Relatório do copiloto"
    return gerar_pdf(corpo[1:], titulo=titulo, autor="AgroSmart Copiloto", largura_max=98)


def salvar(
    resposta: Resposta, config: Config, *, pdf: bool = True, pack: bool = True
) -> list[Path]:
    """Grava ``outputs/relatorios/<caso>/<talhao>_<hoje>.{md,pdf,contexto.json,proveniencia.json}``."""
    pasta = config.dir_outputs / "relatorios" / resposta.caso.value
    pasta.mkdir(parents=True, exist_ok=True)
    base = f"{resposta.talhao_id}_{resposta.pack.janela.hoje.isoformat()}"
    caminhos: list[Path] = []

    md = pasta / f"{base}.md"
    md.write_text(para_markdown(resposta), encoding="utf-8")
    caminhos.append(md)
    if pdf:
        arquivo_pdf = pasta / f"{base}.pdf"
        arquivo_pdf.write_bytes(para_pdf(resposta))
        caminhos.append(arquivo_pdf)
    if pack:
        contexto = pasta / f"{base}.contexto.json"
        contexto.write_text(resposta.pack.para_json(), encoding="utf-8")
        caminhos.append(contexto)
    proveniencia = pasta / f"{base}.proveniencia.json"
    proveniencia.write_text(
        json.dumps(
            {
                "gerado_em": datetime.now(UTC).isoformat(timespec="seconds"),
                **resposta.resumo(),
                "saida": resposta.saida.model_dump(mode="json"),
                "saida_rejeitada": resposta.saida_rejeitada,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    caminhos.append(proveniencia)
    return caminhos
