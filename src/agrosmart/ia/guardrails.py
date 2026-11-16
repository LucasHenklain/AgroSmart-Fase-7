"""Guardrails: o que uma recomendação precisa respeitar para sair do copiloto.

Os guardrails não confiam em quem gerou a saída — motor de regras e modelo
passam pelos mesmos. Há dois graus:

* **crítica** — a saída é rejeitada e o copiloto cai no fallback (regras):
  talhão errado, recomendação de irrigar em sequeiro ou em solo úmido, muitos
  números que não existem no contexto.
* **ajuste** — a saída segue, corrigida e marcada: confiança rebaixada quando
  o dado é fraco, ``dados_usados`` inválidos removidos, número solto anotado
  em ``limitacoes``.

O aterramento numérico é o guardrail central: todo número citado no texto
tem de existir no Context Pack (com tolerância de arredondamento). É o que
impede o modelo de "lembrar" um limiar ou inventar uma economia.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Literal

from agrosmart.ia.contexto import ContextPack
from agrosmart.ia.esquema import Caso, SaidaCopiloto

# Números que podem aparecer sem estar no pacote: contagens pequenas, dias do mês,
# horas do dia, porcentagens redondas de uso corrente e anos próximos.
_NUMEROS_LIVRES: frozenset[float] = frozenset(
    {float(n) for n in range(0, 32)} | {50.0, 100.0, 24.0, 48.0, 72.0, 2025.0, 2026.0, 2027.0}
)
_PADRAO_NUMERO = re.compile(r"(?<![\w/])[-+]?\d{1,3}(?:[.\d]{0,3}\d)?(?:,\d+)?(?![\w])")
_MAX_NAO_ATERRADOS_TOLERAVEL = 3
_ORDEM_CONFIANCA = ["baixa", "media", "alta"]


@dataclass(slots=True)
class Violacao:
    codigo: str
    gravidade: Literal["critica", "ajuste"]
    mensagem: str


@dataclass(slots=True)
class ResultadoGuardrails:
    aprovado: bool
    saida: SaidaCopiloto | None
    violacoes: list[Violacao] = field(default_factory=list)
    numeros_nao_aterrados: list[str] = field(default_factory=list)

    @property
    def criticas(self) -> list[Violacao]:
        return [v for v in self.violacoes if v.gravidade == "critica"]

    @property
    def ajustes(self) -> list[Violacao]:
        return [v for v in self.violacoes if v.gravidade == "ajuste"]

    def para_dict(self) -> dict:
        return {
            "aprovado": self.aprovado,
            "criticas": [asdict(v) for v in self.criticas],
            "ajustes": [asdict(v) for v in self.ajustes],
            "numeros_nao_aterrados": self.numeros_nao_aterrados,
        }


def _para_float(texto: str) -> float | None:
    limpo = texto.strip()
    if "," in limpo and "." in limpo:
        limpo = limpo.replace(".", "").replace(",", ".")
    elif "," in limpo:
        limpo = limpo.replace(",", ".")
    elif limpo.count(".") > 1:
        limpo = limpo.replace(".", "")
    try:
        return float(limpo)
    except ValueError:
        return None


def sem_literais(texto: str, literais: list[str]) -> str:
    """Remove do texto os literais do pacote (nomes, variedades, ids): citá-los é legítimo."""
    for literal in literais:
        texto = re.sub(re.escape(literal), " ", texto, flags=re.IGNORECASE)
    return texto


def extrair_numeros(texto: str) -> list[tuple[str, float]]:
    """Números do texto como (trecho, valor). Ignora ids (TLH-03, LAU-2026-00474), datas e horas."""
    sem_ids = re.sub(r"\b[A-Z]{2,4}(?:-\d{2,6})+\b", " ", texto)
    sem_ids = re.sub(r"\b\d{1,2}-\d{1,2}\s*cm\b", " ", sem_ids)
    sem_datas = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", sem_ids)
    sem_datas = re.sub(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", " ", sem_datas)
    sem_horas = re.sub(r"\b\d{1,2}h(?:\d{2})?\b", " ", sem_datas)
    encontrados: list[tuple[str, float]] = []
    for casamento in _PADRAO_NUMERO.finditer(sem_horas):
        valor = _para_float(casamento.group(0))
        if valor is not None:
            encontrados.append((casamento.group(0), valor))
    return encontrados


def _aterrado(valor: float, universo: set[float]) -> bool:
    if valor in _NUMEROS_LIVRES:
        return True
    alvo = round(valor, 3)
    if alvo in universo:
        return True
    tolerancia = max(0.051, abs(alvo) * 0.011)
    return any(abs(alvo - n) <= tolerancia for n in universo)


def _rebaixar(confianca: str, degraus: int = 1) -> str:
    indice = max(_ORDEM_CONFIANCA.index(confianca) - degraus, 0)
    return _ORDEM_CONFIANCA[indice]


def validar(saida: SaidaCopiloto, pack: ContextPack, caso: Caso) -> ResultadoGuardrails:
    """Aplica todos os guardrails e devolve a saída ajustada (ou rejeitada)."""
    violacoes: list[Violacao] = []
    dados = saida.model_dump()

    # G1 — identidade: talhão e caso pedidos
    if saida.talhao_id != pack.talhao.talhao_id:
        violacoes.append(
            Violacao(
                "G1-talhao",
                "critica",
                f"saída fala de {saida.talhao_id}, contexto é {pack.talhao.talhao_id}",
            )
        )
    if saida.caso is not caso:
        violacoes.append(
            Violacao("G1-caso", "critica", f"saída é do caso {saida.caso}, pedido foi {caso}")
        )

    # G2 — trava agronômica: irrigar sem sistema de irrigação, ou com solo úmido
    fad = pack.hidrico.fracao_agua_disponivel_atual
    for rec in saida.recomendacoes:
        if rec.tipo != "irrigacao" or rec.prioridade == "baixa":
            continue
        if not pack.talhao.irrigado:
            violacoes.append(
                Violacao(
                    "G2-sequeiro",
                    "critica",
                    f"recomenda irrigar talhão de sequeiro ({pack.talhao.sistema_irrigacao})",
                )
            )
        elif fad is not None and fad >= pack.limiares.fracao_irrigacao + 0.15:
            violacoes.append(
                Violacao(
                    "G2-umido",
                    "critica",
                    f"recomenda irrigar com fração de água disponível {fad:.3f}, bem acima "
                    f"do limiar {pack.limiares.fracao_irrigacao}",
                )
            )

    # G3 — aterramento numérico
    universo = pack.numeros()
    textos = [saida.diagnostico, saida.justificativa_confianca, *saida.limitacoes]
    for rec in saida.recomendacoes:
        textos.extend([rec.acao, rec.detalhe, rec.janela_execucao or ""])
    literais = pack.textos()
    nao_aterrados: list[str] = []
    for texto in textos:
        for trecho, valor in extrair_numeros(sem_literais(texto, literais)):
            if not _aterrado(valor, universo):
                nao_aterrados.append(trecho.strip())
    for campo in ("custo_estimado_brl", "economia_estimada_brl"):
        valor = dados.get(campo)
        if valor is not None and not _aterrado(float(valor), universo):
            nao_aterrados.append(f"{campo}={valor}")
    nao_aterrados = sorted(set(nao_aterrados))
    if len(nao_aterrados) > _MAX_NAO_ATERRADOS_TOLERAVEL:
        violacoes.append(
            Violacao(
                "G3-aterramento",
                "critica",
                f"{len(nao_aterrados)} números fora do contexto: {nao_aterrados[:6]}",
            )
        )
    elif nao_aterrados:
        violacoes.append(
            Violacao("G3-aterramento", "ajuste", f"números fora do contexto: {nao_aterrados}")
        )

    # G4 — confiança compatível com a qualidade dos dados
    confianca = saida.confianca
    motivos_rebaixe: list[str] = []
    if pack.vegetacao.confianca_ultimo == "baixa" and caso is not Caso.IRRIGACAO:
        motivos_rebaixe.append("última cena NDVI com confiança baixa")
    if pack.hidrico.umidade_atual_pct is None:
        motivos_rebaixe.append("sem umidade recente")
    cobertura = pack.sensores.menor_cobertura_7d_pct
    if cobertura is not None and cobertura < 80:
        motivos_rebaixe.append(f"cobertura de sensores {cobertura} % em algum dia da semana")
    if any(a.regra == "SEN-12" for a in pack.alertas):
        motivos_rebaixe.append("lacuna recente em sensor do talhão")
    if motivos_rebaixe and confianca == "alta":
        confianca = "media"
        violacoes.append(
            Violacao(
                "G4-confianca",
                "ajuste",
                "confiança 'alta' rebaixada: " + "; ".join(motivos_rebaixe),
            )
        )
    if nao_aterrados and confianca != "baixa":
        confianca = _rebaixar(confianca)
        violacoes.append(
            Violacao("G4-confianca", "ajuste", "confiança rebaixada por números fora do contexto")
        )

    # G5 — dados_usados apontam para o pacote
    caminhos = pack.caminhos()
    caminhos_raiz = {c.split(".")[0].split("[")[0] for c in caminhos}

    def _valido(caminho: str) -> bool:
        base = caminho.strip()
        return base in caminhos or base in caminhos_raiz

    invalidos = [d for d in saida.dados_usados if not _valido(d)]
    for rec in saida.recomendacoes:
        invalidos += [d for d in rec.dados_usados if not _valido(d)]
    if invalidos:
        violacoes.append(
            Violacao(
                "G5-dados-usados",
                "ajuste",
                f"referências fora do contexto removidas: {sorted(set(invalidos))}",
            )
        )

    criticas = [v for v in violacoes if v.gravidade == "critica"]
    if criticas:
        return ResultadoGuardrails(False, None, violacoes, nao_aterrados)

    limitacoes = list(saida.limitacoes)
    if nao_aterrados:
        limitacoes.append(
            f"números citados sem correspondência no contexto: {', '.join(nao_aterrados)}"
        )
    ajustada = saida.model_copy(
        update={
            "confianca": confianca,
            "limitacoes": limitacoes[:8],
            "dados_usados": [d for d in saida.dados_usados if _valido(d)],
            "recomendacoes": [
                r.model_copy(update={"dados_usados": [d for d in r.dados_usados if _valido(d)]})
                for r in saida.recomendacoes
            ],
        }
    )
    return ResultadoGuardrails(True, ajustada, violacoes, nao_aterrados)
