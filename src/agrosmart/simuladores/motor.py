"""Motor da simulação: uma trajetória diária coerente para todas as fontes.

Roda o clima por estação, o balanço hídrico por talhão e as decisões de manejo
que decorrem dele (irrigar, adubar, pulverizar, trocar bateria), dia a dia.
Os escritores de cada fonte só *renderizam* esta trajetória no formato que a
origem produziria — por isso sensores, clima, ERP e imagens nunca se
contradizem.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from agrosmart.simuladores.base import Contexto, arredondar
from agrosmart.simuladores.catalogo import RESPONSAVEIS, Sensor, Talhao
from agrosmart.simuladores.modelo import (
    CULTURAS,
    CUSTO_IRRIGACAO_BRL_MM_HA,
    FRACAO_ESTRESSE,
    FRACAO_IRRIGACAO,
    dias_apos_plantio,
    estadio_em,
    eto_hargreaves,
    fracao_agua_disponivel,
    lamina_irrigacao_mm,
    ndvi_potencial,
    passo_balanco_hidrico,
    radiacao_extraterrestre_mm,
    radiacao_global_mj,
)

# ---------------------------------------------------------------------------
# Estruturas
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DiaClima:
    estacao_id: str
    fazenda_id: str
    data: date
    tmin: float
    tmax: float
    tmed: float
    ur: float
    chuva: float
    vento: float
    radiacao: float
    eto: float


@dataclass(slots=True)
class DiaTalhao:
    talhao_id: str
    data: date
    dap: int
    estadio: str
    kc: float
    theta_ini: float
    theta_fim: float
    chuva: float
    irrigacao: float
    eto: float
    etc: float
    drenagem: float
    fad: float
    estresse_dias: int
    ndvi_pot: float
    ndvi: float
    ur_alta_dias: int


@dataclass(slots=True)
class Operacao:
    operacao_id: str
    talhao_id: str
    data: date
    tipo_operacao: str
    insumo: str | None
    quantidade: float
    unidade: str
    custo_brl: float
    responsavel_id: str
    observacao: str | None
    lancado_em: date
    correcao: bool = False


@dataclass(frozen=True, slots=True)
class AgendaLaudo:
    laudo_id: str
    talhao_id: str
    profundidade_cm: str
    coletado_em: date
    emitido_em: date


@dataclass(slots=True)
class Trajetoria:
    clima: dict[tuple[str, date], DiaClima] = field(default_factory=dict)
    talhoes: dict[tuple[str, date], DiaTalhao] = field(default_factory=dict)
    operacoes: list[Operacao] = field(default_factory=list)
    laudos: list[AgendaLaudo] = field(default_factory=list)
    # sensor_id -> data -> bateria (None = sensor calado naquele dia)
    bateria: dict[str, dict[date, float | None]] = field(default_factory=dict)
    silencio: dict[str, tuple[date | None, date | None]] = field(default_factory=dict)

    def clima_do_talhao(self, ctx: Contexto, talhao: Talhao, dia: date) -> DiaClima:
        return self.clima[(ctx.fazenda_de(talhao).estacao_id, dia)]

    def dias_do_talhao(self, talhao_id: str) -> list[DiaTalhao]:
        return [d for (tid, _), d in sorted(self.talhoes.items()) if tid == talhao_id]

    def estatisticas(self) -> dict[str, object]:
        irrigacoes = [
            o for o in self.operacoes if o.tipo_operacao == "irrigacao" and not o.correcao
        ]
        return {
            "operacoes": len(self.operacoes),
            "correcoes_retroativas": sum(1 for o in self.operacoes if o.correcao),
            "irrigacoes": len(irrigacoes),
            "lamina_total_mm": arredondar(sum(o.quantidade for o in irrigacoes), 1),
            "chuva_total_mm_por_estacao": {
                estacao: arredondar(
                    sum(c.chuva for (e, _), c in self.clima.items() if e == estacao), 1
                )
                for estacao in sorted({e for e, _ in self.clima})
            },
            "laudos": len(self.laudos),
            "sensores_com_silencio": {
                sid: {
                    "calou_em": s[0].isoformat() if s[0] else None,
                    "manutencao_em": s[1].isoformat() if s[1] else None,
                }
                for sid, s in sorted(self.silencio.items())
                if s[0] is not None
            },
        }


# ---------------------------------------------------------------------------
# Clima
# ---------------------------------------------------------------------------

_P_CHUVA_APOS_SECO = 0.12
_P_CHUVA_APOS_CHUVA = 0.45


def _simular_clima(ctx: Contexto, traj: Trajetoria) -> None:
    rng = ctx.rng("clima")
    anomalia_regional = 0.0
    choveu_regional = False

    for indice, dia in enumerate(ctx.dias):
        anomalia_regional = 0.7 * anomalia_regional + rng.gauss(0.0, 1.6)
        p_chuva = _P_CHUVA_APOS_CHUVA if choveu_regional else _P_CHUVA_APOS_SECO
        choveu_regional = rng.random() < p_chuva

        # Aquecimento gradual de agosto para setembro no interior paulista.
        tmin_base = 13.8 + 0.09 * indice
        tmax_base = 27.5 + 0.11 * indice

        for fazenda in ctx.fazendas:
            tmax = tmax_base + anomalia_regional + rng.gauss(0.0, 0.7)
            tmin = tmin_base + 0.6 * anomalia_regional + rng.gauss(0.0, 0.8)

            if choveu_regional:
                chove_aqui = rng.random() < 0.80
            else:
                chove_aqui = rng.random() < 0.04
            chuva = 0.0
            if chove_aqui:
                chuva = max(0.2, rng.gammavariate(1.6, 8.0))
                tmax -= 3.5
                tmin += 0.8
            tmax = max(tmax, tmin + 3.0)
            tmed = (tmin + tmax) / 2 + rng.gauss(0.0, 0.3)

            ur = 58.0 + (22.0 if chove_aqui else 0.0) - 1.3 * (tmax - 28.0) + rng.gauss(0.0, 4.0)
            ur = max(30.0, min(98.0, ur))
            vento = max(0.3, min(9.0, rng.gammavariate(4.0, 0.6)))

            ra_mm = radiacao_extraterrestre_mm(fazenda.latitude, dia.timetuple().tm_yday)
            eto = eto_hargreaves(tmin, tmax, ra_mm) * (0.75 if chove_aqui else 1.0)
            radiacao = radiacao_global_mj(tmin, tmax, ra_mm) * (0.65 if chove_aqui else 1.0)

            traj.clima[(fazenda.estacao_id, dia)] = DiaClima(
                estacao_id=fazenda.estacao_id,
                fazenda_id=fazenda.id,
                data=dia,
                tmin=arredondar(tmin, 1),
                tmax=arredondar(tmax, 1),
                tmed=arredondar(tmed, 1),
                ur=arredondar(ur, 1),
                chuva=arredondar(chuva, 1),
                vento=arredondar(vento, 1),
                radiacao=arredondar(radiacao, 1),
                eto=arredondar(eto, 2),
            )


# ---------------------------------------------------------------------------
# Manejo: insumos por estádio e regras de decisão
# ---------------------------------------------------------------------------

# cultura -> estádio -> (insumo, kg/ha, preço R$/kg)
_ADUBACAO_POR_ESTADIO: dict[str, dict[str, tuple[str, float, float]]] = {
    "milho": {"V8": ("ureia 45%", 160.0, 3.05), "V12": ("ureia 45%", 120.0, 3.05)},
    "feijao": {"V4": ("formulado 20-00-20", 250.0, 2.90)},
    "trigo": {"alongamento": ("ureia 45%", 90.0, 3.05)},
    "soja": {"R1": ("cloreto de potássio", 100.0, 3.60)},
}
_ADUBACAO_CAFE_AGOSTO = ("formulado 20-05-20", 400.0, 3.40)
_CULTURAS_COM_FUNGICIDA = {"milho", "feijao", "trigo", "cafe", "soja"}
_CUSTO_FUNGICIDA_BRL_HA = 186.0
_DOSE_FUNGICIDA_L_HA = 0.75
_P_CORRECAO_RETROATIVA = 0.03
# Monitoramento fitossanitário: uma volta semanal por talhão, custo de mão de obra por hectare.
_CUSTO_MONITORAMENTO_BRL_HA = 18.0


def _simular_talhoes_e_manejo(ctx: Contexto, traj: Trajetoria) -> None:
    rng_ops = ctx.rng("operacoes")
    brutas: list[Operacao] = []

    for talhao in ctx.talhoes:
        cultura = CULTURAS[talhao.cultura]
        responsaveis = RESPONSAVEIS[talhao.fazenda_id]
        theta = talhao.umidade_inicial_pct
        historico_estresse: list[int] = []
        historico_ur_alta: list[int] = []
        ultima_pulverizacao: date | None = None
        adubou_cafe = False

        dia_da_semana_monitoramento = int(talhao.id.split("-")[1]) % 7

        for indice_dia, dia in enumerate(ctx.dias):
            clima = traj.clima_do_talhao(ctx, talhao, dia)
            dap = dias_apos_plantio(talhao, dia)
            estadio = estadio_em(cultura, dap, dia)
            etc = estadio.kc * clima.eto

            # --- irrigação: decisão pela umidade do início do dia -------------
            irrigacao = 0.0
            if (
                talhao.irrigado
                and fracao_agua_disponivel(
                    theta, talhao.capacidade_campo_pct, talhao.ponto_murcha_pct
                )
                < FRACAO_IRRIGACAO
            ):
                irrigacao = lamina_irrigacao_mm(theta, talhao.capacidade_campo_pct)
                custo = (
                    irrigacao * talhao.area_ha * CUSTO_IRRIGACAO_BRL_MM_HA[talhao.sistema_irrigacao]
                )
                brutas.append(
                    Operacao(
                        operacao_id="",
                        talhao_id=talhao.id,
                        data=dia,
                        tipo_operacao="irrigacao",
                        insumo="água",
                        quantidade=irrigacao,
                        unidade="mm",
                        custo_brl=arredondar(custo),
                        responsavel_id=rng_ops.choice(responsaveis),
                        observacao="janela 04h-07h" if rng_ops.random() < 0.7 else None,
                        lancado_em=dia + timedelta(days=1),
                    )
                )

            theta_fim, drenagem = passo_balanco_hidrico(
                theta,
                clima.chuva,
                irrigacao,
                etc,
                talhao.capacidade_campo_pct,
                talhao.ponto_murcha_pct,
            )
            fad = fracao_agua_disponivel(
                theta_fim, talhao.capacidade_campo_pct, talhao.ponto_murcha_pct
            )

            historico_estresse.append(1 if fad < FRACAO_ESTRESSE else 0)
            estresse_dias = sum(historico_estresse[-7:])
            historico_ur_alta.append(1 if clima.ur > 80 else 0)
            ur_alta_dias = sum(historico_ur_alta[-3:])

            ndvi_pot = ndvi_potencial(cultura, dap, dia)
            ndvi = max(0.2, ndvi_pot * (1 - 0.045 * estresse_dias))

            traj.talhoes[(talhao.id, dia)] = DiaTalhao(
                talhao_id=talhao.id,
                data=dia,
                dap=dap,
                estadio=estadio.nome,
                kc=estadio.kc,
                theta_ini=arredondar(theta, 2),
                theta_fim=arredondar(theta_fim, 2),
                chuva=clima.chuva,
                irrigacao=irrigacao,
                eto=clima.eto,
                etc=arredondar(etc, 2),
                drenagem=arredondar(drenagem, 2),
                fad=arredondar(fad, 3),
                estresse_dias=estresse_dias,
                ndvi_pot=arredondar(ndvi_pot, 3),
                ndvi=arredondar(ndvi, 3),
                ur_alta_dias=ur_alta_dias,
            )

            # --- adubação por estádio -------------------------------------------
            receita = _ADUBACAO_POR_ESTADIO.get(talhao.cultura, {}).get(estadio.nome)
            if receita and not cultura.perene and dap == estadio.dia_inicio:
                insumo, kg_ha, preco = receita
                quantidade = arredondar(kg_ha * talhao.area_ha, 1)
                brutas.append(
                    Operacao(
                        "",
                        talhao.id,
                        dia,
                        "adubacao",
                        insumo,
                        quantidade,
                        "kg",
                        arredondar(quantidade * preco),
                        rng_ops.choice(responsaveis),
                        f"cobertura em {estadio.nome}",
                        dia + timedelta(days=1),
                    )
                )
            if cultura.perene and dia.month == 8 and not adubou_cafe:
                insumo, kg_ha, preco = _ADUBACAO_CAFE_AGOSTO
                quantidade = arredondar(kg_ha * talhao.area_ha, 1)
                brutas.append(
                    Operacao(
                        "",
                        talhao.id,
                        dia,
                        "adubacao",
                        insumo,
                        quantidade,
                        "kg",
                        arredondar(quantidade * preco),
                        rng_ops.choice(responsaveis),
                        "adubação de pré-florada",
                        dia + timedelta(days=1),
                    )
                )
                adubou_cafe = True

            # --- pulverização preventiva após dias úmidos ---------------------------
            janela_ok = ultima_pulverizacao is None or (dia - ultima_pulverizacao).days >= 10
            if (
                talhao.cultura in _CULTURAS_COM_FUNGICIDA
                and ur_alta_dias >= 3
                and 17.0 <= clima.tmed <= 27.0
                and janela_ok
            ):
                quantidade = arredondar(_DOSE_FUNGICIDA_L_HA * talhao.area_ha, 2)
                brutas.append(
                    Operacao(
                        "",
                        talhao.id,
                        dia,
                        "pulverizacao",
                        "fungicida triazol + estrobilurina",
                        quantidade,
                        "L",
                        arredondar(_CUSTO_FUNGICIDA_BRL_HA * talhao.area_ha),
                        rng_ops.choice(responsaveis),
                        "preventivo após 3 dias de UR alta",
                        dia + timedelta(days=1),
                    )
                )
                ultima_pulverizacao = dia

            # --- monitoramento fitossanitário semanal ---------------------------
            if indice_dia % 7 == dia_da_semana_monitoramento:
                brutas.append(
                    Operacao(
                        "",
                        talhao.id,
                        dia,
                        "monitoramento",
                        None,
                        talhao.area_ha,
                        "ha",
                        arredondar(_CUSTO_MONITORAMENTO_BRL_HA * talhao.area_ha),
                        rng_ops.choice(responsaveis),
                        "monitoramento fitossanitário semanal",
                        dia + timedelta(days=1),
                    )
                )

            theta = theta_fim

    traj.operacoes.extend(brutas)


# ---------------------------------------------------------------------------
# Sensores: bateria, silêncio e manutenção
# ---------------------------------------------------------------------------


def _simular_baterias(ctx: Contexto, traj: Trajetoria) -> None:
    for sensor in ctx.sensores:
        linha: dict[date, float | None] = {}
        calou_em: date | None = None
        manutencao_em: date | None = None

        for indice, dia in enumerate(ctx.dias):
            if not sensor.falha_bateria:
                linha[dia] = sensor.bateria_inicial_pct
                continue

            if manutencao_em is not None and dia >= manutencao_em:
                dias_pos = (dia - manutencao_em).days
                linha[dia] = max(5.0, 100.0 - 0.9 * dias_pos)
                continue

            bateria = sensor.bateria_inicial_pct - sensor.taxa_descarga_dia * indice
            if bateria < 5.0:
                if calou_em is None:
                    calou_em = dia
                    candidata = dia + timedelta(days=5)
                    if candidata <= ctx.fim:
                        manutencao_em = candidata
                        _agendar_manutencao(ctx, traj, sensor, candidata)
                linha[dia] = None
            else:
                linha[dia] = arredondar(bateria, 1)

        traj.bateria[sensor.id] = linha
        traj.silencio[sensor.id] = (calou_em, manutencao_em)


def _agendar_manutencao(ctx: Contexto, traj: Trajetoria, sensor: Sensor, dia: date) -> None:
    talhao = ctx.talhao(sensor.talhao_id)
    traj.operacoes.append(
        Operacao(
            operacao_id="",
            talhao_id=talhao.id,
            data=dia,
            tipo_operacao="manutencao",
            insumo="bateria Li-ion 3,7 V 6.000 mAh",
            quantidade=1.0,
            unidade="un",
            custo_brl=95.0,
            responsavel_id=RESPONSAVEIS[talhao.fazenda_id][0],
            observacao=(
                f"troca de bateria do sensor {sensor.id} "
                f"(sem leitura desde {(dia - timedelta(days=5)).isoformat()})"
            ),
            lancado_em=dia + timedelta(days=1),
        )
    )


# ---------------------------------------------------------------------------
# Laudos: coletados antes da janela, emitidos dentro dela
# ---------------------------------------------------------------------------


def _agendar_laudos(ctx: Contexto, traj: Trajetoria) -> None:
    sequencia = 471
    for indice, talhao in enumerate(ctx.talhoes):
        if indice >= len(ctx.dias):
            break
        emitido = ctx.inicio + timedelta(days=indice)
        coletado = emitido - timedelta(days=12)
        traj.laudos.append(
            AgendaLaudo(f"LAU-2026-{sequencia:05d}", talhao.id, "0-20", coletado, emitido)
        )
        sequencia += 1
        if talhao.id in {"TLH-01", "TLH-06"}:
            traj.laudos.append(
                AgendaLaudo(f"LAU-2026-{sequencia:05d}", talhao.id, "20-40", coletado, emitido)
            )
            sequencia += 1


# ---------------------------------------------------------------------------
# Identificadores e correções retroativas
# ---------------------------------------------------------------------------


def _numerar_e_corrigir(ctx: Contexto, traj: Trajetoria) -> None:
    rng = ctx.rng("operacoes.correcoes")
    ordenadas = sorted(
        traj.operacoes, key=lambda o: (o.data, o.talhao_id, o.tipo_operacao, o.insumo or "")
    )
    for numero, operacao in enumerate(ordenadas, start=18001):
        operacao.operacao_id = f"OPR-2026-{numero:06d}"

    correcoes: list[Operacao] = []
    for operacao in ordenadas:
        if operacao.tipo_operacao in {"manutencao"}:
            continue
        if rng.random() < _P_CORRECAO_RETROATIVA:
            correcoes.append(
                Operacao(
                    operacao_id=operacao.operacao_id,
                    talhao_id=operacao.talhao_id,
                    data=operacao.data,
                    tipo_operacao=operacao.tipo_operacao,
                    insumo=operacao.insumo,
                    quantidade=operacao.quantidade,
                    unidade=operacao.unidade,
                    custo_brl=arredondar(operacao.custo_brl * 1.04),
                    responsavel_id=operacao.responsavel_id,
                    observacao="correção de custo: estorno e relançamento",
                    lancado_em=operacao.lancado_em + timedelta(days=3),
                    correcao=True,
                )
            )
    traj.operacoes = ordenadas + correcoes


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------


def simular_trajetoria(ctx: Contexto) -> Trajetoria:
    traj = Trajetoria()
    _simular_clima(ctx, traj)
    _simular_talhoes_e_manejo(ctx, traj)
    _simular_baterias(ctx, traj)
    _agendar_laudos(ctx, traj)
    _numerar_e_corrigir(ctx, traj)
    return traj


def hora_decimal(minutos_desde_meia_noite: int) -> float:
    return minutos_desde_meia_noite / 60.0


def temperatura_ar_em(hora: float, tmin: float, tmax: float) -> float:
    """Ciclo diário com mínima às 05h e máxima às 15h."""
    return (tmax + tmin) / 2 + (tmax - tmin) / 2 * math.cos(2 * math.pi * (hora - 15) / 24)
