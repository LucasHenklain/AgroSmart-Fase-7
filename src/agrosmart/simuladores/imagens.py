"""Escritor da fonte ``imagens_ndvi``: cenas de drone e satélite com sidecar.

Cada captura gera dois arquivos na pasta observada: o JPEG (um mapa de NDVI
renderizado a partir do estado do talhão naquele dia — gradiente, textura,
mancha de estresse quando houve, nuvem quando o satélite pegou dia nublado)
e o sidecar JSON com os metadados e os índices que a origem já calculou.

O JPEG é gerado pixel a pixel com ruído determinístico por hash, sem RNG,
para que a imagem seja idêntica entre execuções.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from io import BytesIO

from PIL import Image

from agrosmart.simuladores.base import Contexto, DefeitoInjetado, arredondar, iso_utc
from agrosmart.simuladores.defeitos import POR_ID, distribuir_posicoes
from agrosmart.simuladores.motor import Trajetoria

FONTE = "imagens_ndvi"
SUBPASTA = "imagens"

_DIAS_DRONE = (2, 9, 16, 23)
_PASSO_SATELITE = 5

_PALETA = (
    (0.10, (128, 92, 60)),
    (0.30, (176, 140, 84)),
    (0.50, (204, 190, 84)),
    (0.65, (120, 170, 70)),
    (0.80, (48, 128, 52)),
    (0.95, (18, 90, 40)),
)


def _cor(ndvi: float) -> tuple[int, int, int]:
    if ndvi <= _PALETA[0][0]:
        return _PALETA[0][1]
    for (v0, c0), (v1, c1) in zip(_PALETA, _PALETA[1:], strict=False):
        if ndvi <= v1:
            t = (ndvi - v0) / (v1 - v0)
            return tuple(int(round(c0[i] + (c1[i] - c0[i]) * t)) for i in range(3))  # type: ignore[return-value]
    return _PALETA[-1][1]


def _ruido(x: int, y: int, semente: int) -> float:
    """Ruído determinístico em [-1, 1] por hash inteiro."""
    h = (x * 73856093) ^ (y * 19349663) ^ (semente * 83492791)
    h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((h ^ (h >> 16)) % 20001) / 10000.0 - 1.0


def renderizar_ndvi(
    largura: int,
    altura: int,
    ndvi_medio: float,
    desvio: float,
    estresse_frac: float,
    nuvem_pct: float,
    semente: int,
) -> bytes:
    pixels: list[tuple[int, int, int]] = []
    cx, cy, raio = largura * 0.62, altura * 0.42, min(largura, altura) * 0.28
    fase = (semente % 97) / 97 * 2 * math.pi
    for y in range(altura):
        for x in range(largura):
            valor = ndvi_medio
            valor += 0.05 * ((x / largura) - 0.5) * 2  # gradiente leste-oeste
            valor += 0.7 * desvio * math.sin(x * 0.19 + fase) * math.cos(y * 0.16 - fase)
            valor += 0.5 * desvio * _ruido(x, y, semente)
            if estresse_frac > 0:
                d = math.hypot(x - cx, y - cy)
                if d < raio:
                    valor -= 0.28 * estresse_frac * (1 - d / raio)
            valor = max(0.02, min(0.98, valor))
            cor = _cor(valor)
            if nuvem_pct > 20:
                textura = (
                    _ruido(x // 6, y // 6, semente + 7) * 0.5
                    + _ruido(x // 2, y // 2, semente + 11) * 0.2
                )
                if textura > 1.0 - nuvem_pct / 100 * 1.4:
                    cor = (232, 234, 238)
            pixels.append(cor)
    imagem = Image.new("RGB", (largura, altura))
    imagem.putdata(pixels)
    buffer = BytesIO()
    imagem.save(buffer, format="JPEG", quality=82, optimize=True)
    return buffer.getvalue()


def _exif(capturada_em: datetime, descricao: str) -> Image.Exif:
    exif = Image.Exif()
    exif[0x9003] = capturada_em.strftime("%Y:%m:%d %H:%M:%S")  # DateTimeOriginal
    exif[0x010E] = descricao  # ImageDescription
    exif[0x0131] = "agrosmart-simulador"  # Software
    return exif


def gerar_imagens(ctx: Contexto, traj: Trajetoria) -> None:
    pasta = ctx.landing(SUBPASTA)
    rng = ctx.rng("imagens")
    rng_def = ctx.rng("defeitos.imagens")

    agenda: list[tuple[datetime, str, str]] = []
    for talhao in ctx.talhoes:
        for offset in _DIAS_DRONE:
            if offset < len(ctx.dias):
                dia = ctx.dias[offset]
                agenda.append((datetime(dia.year, dia.month, dia.day, 13, 40), talhao.id, "drone"))
        for offset in range(1, len(ctx.dias), _PASSO_SATELITE):
            dia = ctx.dias[offset]
            agenda.append((datetime(dia.year, dia.month, dia.day, 13, 5), talhao.id, "satelite"))
    agenda.sort()

    posicoes = distribuir_posicoes(
        rng_def, len(agenda), [POR_ID[t] for t in ("IMG-NDV", "IMG-TRU")]
    )

    for indice, (instante_base, talhao_id, plataforma) in enumerate(agenda):
        talhao = ctx.talhao(talhao_id)
        dia = instante_base.date()
        estado = traj.talhoes[(talhao_id, dia)]
        clima = traj.clima_do_talhao(ctx, talhao, dia)
        imagem_id = f"IMG-2026-{801 + indice:04d}"
        capturada_em = instante_base + timedelta(minutes=rng.randint(-20, 20))

        if plataforma == "drone":
            largura, altura, altitude, resolucao = 240, 180, 120.0, 3.2
            nuvem = rng.uniform(0.0, 8.0)
        else:
            largura, altura, altitude, resolucao = 160, 120, 786000.0, 1000.0
            nuvem = rng.uniform(35.0, 80.0) if clima.chuva > 0 else rng.uniform(0.0, 18.0)

        ndvi_medio = max(0.05, min(0.95, estado.ndvi + rng.gauss(0.0, 0.015)))
        ndvi_min = max(0.02, ndvi_medio - rng.uniform(0.18, 0.30))
        ndvi_max = min(0.95, ndvi_medio + rng.uniform(0.08, 0.14))
        estresse_frac = estado.estresse_dias / 7
        desvio = 0.05 + 0.07 * estresse_frac + rng.uniform(0.0, 0.01)
        area_analisada = talhao.area_ha * (1 - nuvem / 100 * 0.6)

        jpeg = renderizar_ndvi(
            largura,
            altura,
            ndvi_medio,
            desvio,
            estresse_frac,
            nuvem,
            semente=ctx.config.seed + indice,
        )
        buffer = BytesIO()
        Image.open(BytesIO(jpeg)).save(
            buffer,
            format="JPEG",
            quality=82,
            optimize=True,
            exif=_exif(capturada_em, f"{talhao.id} {plataforma} NDVI {ndvi_medio:.2f}"),
        )
        jpeg = buffer.getvalue()

        sidecar: dict[str, object] = {
            "imagem_id": imagem_id,
            "talhao_id": talhao.id,
            "capturada_em": iso_utc(capturada_em),
            "plataforma": plataforma,
            "largura_px": largura,
            "altura_px": altura,
            "altitude_m": altitude,
            "resolucao_cm_px": resolucao,
            "nuvem_pct": arredondar(nuvem, 1),
            "ndvi_medio": arredondar(ndvi_medio, 3),
            "ndvi_min": arredondar(ndvi_min, 3),
            "ndvi_max": arredondar(ndvi_max, 3),
            "ndvi_desvio": arredondar(desvio, 3),
            "area_analisada_ha": arredondar(area_analisada, 2),
        }

        nome_jpg, nome_json = f"{imagem_id}.jpg", f"{imagem_id}.json"
        if indice in posicoes["IMG-NDV"]:
            sidecar["ndvi_max"] = 1.27
            ctx.registro.adicionar(
                DefeitoInjetado(
                    "IMG-NDV",
                    FONTE,
                    "IMG-03",
                    {"imagem_id": imagem_id},
                    nome_json,
                    POR_ID["IMG-NDV"].descricao,
                )
            )
        if indice in posicoes["IMG-TRU"]:
            jpeg = jpeg[:1024]
            ctx.registro.adicionar(
                DefeitoInjetado(
                    "IMG-TRU",
                    FONTE,
                    "IMG-10",
                    {"imagem_id": imagem_id},
                    nome_jpg,
                    POR_ID["IMG-TRU"].descricao,
                )
            )

        ctx.gravar_bytes(FONTE, pasta / nome_jpg, jpeg, registros=0)
        ctx.gravar_json(FONTE, pasta / nome_json, sidecar, registros=1)
