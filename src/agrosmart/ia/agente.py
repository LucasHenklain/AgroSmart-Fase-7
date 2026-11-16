"""O copiloto: escolhe o motor, chama, valida, cai no fallback quando preciso.

Fluxo de :meth:`Copiloto.responder`:

1. monta os prompts (versionados) com o Context Pack;
2. se o motor generativo está disponível e foi pedido, chama a API com saída
   estruturada e valida o JSON contra :class:`SaidaCopiloto`;
3. passa a saída pelos guardrails — reprovada, cai no motor de regras;
4. o motor de regras também passa pelos guardrails (mesmo critério para todos);
5. devolve a resposta com toda a proveniência: motor usado, houve fallback e
   por quê, violações, versão dos prompts, tokens e latência.

A resposta nunca é só o texto: quem lê o relatório sabe de onde ele veio.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from agrosmart.config import Config, carregar_config
from agrosmart.ia import regras
from agrosmart.ia.cliente_claude import ClienteClaude, ErroCliente, GeradorEstruturado
from agrosmart.ia.contexto import ContextPack
from agrosmart.ia.esquema import Caso, SaidaCopiloto, esquema_json_saida
from agrosmart.ia.guardrails import ResultadoGuardrails, validar
from agrosmart.ia.prompts import montar
from agrosmart.log import Evento, obter_logger, registrar_evento

MotorPreferido = Literal["auto", "claude", "regras"]
FERRAMENTA = "emitir_recomendacao"
DESCRICAO_FERRAMENTA = (
    "Emite o diagnóstico e as recomendações do copiloto agronômico para o talhão do contexto, "
    "no formato estruturado exigido."
)


@dataclass(slots=True)
class Resposta:
    caso: Caso
    talhao_id: str
    motor: str
    fallback: bool
    saida: SaidaCopiloto
    guardrails: ResultadoGuardrails
    pack: ContextPack
    latencia_s: float
    prompt_sistema: str | None = None
    prompt_caso: str | None = None
    uso: dict[str, Any] | None = None
    erro_motor: str | None = None
    guardrails_rejeitados: ResultadoGuardrails | None = None
    saida_rejeitada: dict[str, Any] | None = None
    detalhes: list[str] = field(default_factory=list)

    def resumo(self) -> dict[str, Any]:
        return {
            "caso": self.caso.value,
            "talhao_id": self.talhao_id,
            "motor": self.motor,
            "fallback": self.fallback,
            "erro_motor": self.erro_motor,
            "confianca": self.saida.confianca,
            "prioridade_maxima": self.saida.prioridade_maxima,
            "tipos": sorted(self.saida.tipos),
            "recomenda_irrigar": self.saida.recomenda_irrigar(),
            "recomendacoes": len(self.saida.recomendacoes),
            "guardrails": self.guardrails.para_dict(),
            "guardrails_rejeitados": self.guardrails_rejeitados.para_dict()
            if self.guardrails_rejeitados
            else None,
            "prompt_sistema": self.prompt_sistema,
            "prompt_caso": self.prompt_caso,
            "uso": self.uso,
            "latencia_s": round(self.latencia_s, 2),
        }


class Copiloto:
    def __init__(
        self,
        config: Config | None = None,
        *,
        cliente: GeradorEstruturado | None = None,
        motor_preferido: MotorPreferido = "auto",
    ) -> None:
        self.config = config or carregar_config()
        self.cliente: GeradorEstruturado = cliente or ClienteClaude.da_config(self.config)
        self.motor_preferido = motor_preferido
        self.logger = obter_logger("ia.copiloto")

    @property
    def usa_modelo(self) -> bool:
        if self.motor_preferido == "regras":
            return False
        disponivel = self.cliente.disponivel()
        if self.motor_preferido == "claude" and not disponivel:
            self.logger.warning("motor 'claude' pedido, mas indisponível — usando regras")
        return disponivel

    def _pelas_regras(
        self, pack: ContextPack, caso: Caso
    ) -> tuple[SaidaCopiloto, ResultadoGuardrails]:
        saida = regras.gerar(pack, caso)
        resultado = validar(saida, pack, caso)
        if not resultado.aprovado or resultado.saida is None:
            # As regras são nossas: reprovar aqui é bug, não dado ruim. Não escondemos.
            self.logger.error(
                "motor de regras reprovado pelos guardrails: %s",
                [v.mensagem for v in resultado.criticas],
            )
            return saida, resultado
        return resultado.saida, resultado

    def responder(self, pack: ContextPack, caso: Caso) -> Resposta:
        inicio = time.perf_counter()
        sistema, do_caso, texto_sistema, texto_usuario = montar(caso, pack)
        detalhes: list[str] = []
        erro_motor: str | None = None
        uso: dict[str, Any] | None = None
        rejeitados: ResultadoGuardrails | None = None
        saida_rejeitada: dict[str, Any] | None = None

        if self.usa_modelo:
            try:
                dados, uso = self.cliente.gerar_estruturado(
                    sistema=texto_sistema,
                    usuario=texto_usuario,
                    ferramenta=FERRAMENTA,
                    descricao=DESCRICAO_FERRAMENTA,
                    schema=esquema_json_saida(),
                )
                try:
                    saida = SaidaCopiloto.model_validate(dados)
                except ValidationError as erro:
                    saida_rejeitada = dados
                    raise ErroCliente(
                        f"saída fora do schema: {erro.error_count()} erro(s)"
                    ) from erro
                resultado = validar(saida, pack, caso)
                if resultado.aprovado and resultado.saida is not None:
                    resposta = Resposta(
                        caso=caso,
                        talhao_id=pack.talhao.talhao_id,
                        motor=self.cliente.nome,
                        fallback=False,
                        saida=resultado.saida,
                        guardrails=resultado,
                        pack=pack,
                        latencia_s=time.perf_counter() - inicio,
                        prompt_sistema=sistema.rotulo,
                        prompt_caso=do_caso.rotulo,
                        uso=uso,
                        detalhes=[v.mensagem for v in resultado.ajustes],
                    )
                    self._evento(resposta)
                    return resposta
                rejeitados = resultado
                saida_rejeitada = saida.model_dump(mode="json")
                erro_motor = "guardrails: " + "; ".join(v.mensagem for v in resultado.criticas)
            except ErroCliente as erro:
                erro_motor = str(erro)
            detalhes.append(f"fallback para regras — {erro_motor}")
            self.logger.warning("%s/%s: %s", pack.talhao.talhao_id, caso.value, detalhes[-1])

        saida, resultado = self._pelas_regras(pack, caso)
        resposta = Resposta(
            caso=caso,
            talhao_id=pack.talhao.talhao_id,
            motor=regras.NOME_MOTOR,
            fallback=erro_motor is not None,
            saida=saida,
            guardrails=resultado,
            pack=pack,
            latencia_s=time.perf_counter() - inicio,
            prompt_sistema=sistema.rotulo if erro_motor is not None else None,
            prompt_caso=do_caso.rotulo if erro_motor is not None else None,
            uso=uso,
            erro_motor=erro_motor,
            guardrails_rejeitados=rejeitados,
            saida_rejeitada=saida_rejeitada,
            detalhes=detalhes + [v.mensagem for v in resultado.ajustes],
        )
        self._evento(resposta)
        return resposta

    def _evento(self, resposta: Resposta) -> None:
        registrar_evento(
            Evento(
                etapa="ia",
                acao="recomendacao_gerada",
                fonte=resposta.talhao_id,
                status="alerta" if resposta.fallback else "ok",
                mensagem=(
                    f"{resposta.caso.value} · motor={resposta.motor} · "
                    f"confiança={resposta.saida.confianca}"
                ),
                metricas={
                    "recomendacoes": len(resposta.saida.recomendacoes),
                    "fallback": resposta.fallback,
                    "latencia_s": round(resposta.latencia_s, 2),
                    "tokens": (resposta.uso or {}).get("input_tokens"),
                },
            ),
            self.config,
            eco_console=False,
        )
