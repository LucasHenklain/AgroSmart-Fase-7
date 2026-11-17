"""Gera ``infra/monitoramento/grafana/dashboards/agrosmart-operacao.json``.

O painel é código: cada painel é uma consulta PromQL sobre as métricas que o worker
expõe em ``/metrics`` (``src/agrosmart/operacao/metricas.py``) e as do MinIO. Gerar
o JSON a partir daqui mantém o painel revisável em PR e igual em qualquer máquina.

    python scripts/gerar_painel_grafana.py
"""

from __future__ import annotations

import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "infra" / "monitoramento" / "grafana" / "dashboards" / "agrosmart-operacao.json"
DS = {"type": "prometheus", "uid": "prometheus-agrosmart"}

_proximo_id = 0


def _id() -> int:
    global _proximo_id
    _proximo_id += 1
    return _proximo_id


def alvo(expr: str, legenda: str = "", ref: str = "A", instantaneo: bool = False) -> dict:
    return {
        "datasource": DS,
        "expr": expr,
        "legendFormat": legenda,
        "refId": ref,
        "instant": instantaneo,
        "range": not instantaneo,
    }


def stat(titulo, expr, x, y, w=4, h=4, unidade="none", limiares=None, mapas=None, descricao=""):
    return {
        "id": _id(),
        "type": "stat",
        "title": titulo,
        "description": descricao,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [alvo(expr, instantaneo=True)],
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "background",
            "graphMode": "none",
            "textMode": "value",
            "justifyMode": "center",
        },
        "fieldConfig": {
            "defaults": {
                "unit": unidade,
                "mappings": mapas or [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": limiares or [{"color": "green", "value": None}],
                },
            },
            "overrides": [],
        },
    }


def serie(titulo, alvos, x, y, w=12, h=8, unidade="none", empilhar=False, descricao="", barras=False):
    return {
        "id": _id(),
        "type": "timeseries",
        "title": titulo,
        "description": descricao,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": alvos,
        "options": {
            "legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "mean"]},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "fieldConfig": {
            "defaults": {
                "unit": unidade,
                "custom": {
                    "drawStyle": "bars" if barras else "line",
                    "lineWidth": 2,
                    "fillOpacity": 25 if empilhar or barras else 8,
                    "stacking": {"mode": "normal" if empilhar else "none"},
                    "showPoints": "never",
                    "spanNulls": True,
                },
            },
            "overrides": [],
        },
    }


def barras(titulo, expr, legenda, x, y, w=8, h=8, unidade="none", descricao=""):
    return {
        "id": _id(),
        "type": "bargauge",
        "title": titulo,
        "description": descricao,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [alvo(expr, legenda, instantaneo=True)],
        "options": {
            "orientation": "horizontal",
            "displayMode": "gradient",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showUnfilled": True,
        },
        "fieldConfig": {
            "defaults": {
                "unit": unidade,
                "thresholds": {"mode": "absolute", "steps": [{"color": "blue", "value": None}]},
            },
            "overrides": [],
        },
    }


def tabela_alertas(x, y, w=24, h=6):
    return {
        "id": _id(),
        "type": "table",
        "title": "Alertas disparados agora (regras em infra/monitoramento/alertas.yml)",
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [alvo('ALERTS{alertstate="firing"}', instantaneo=True)],
        "transformations": [
            {"id": "labelsToFields", "options": {}},
            {
                "id": "organize",
                "options": {
                    "excludeByName": {"Time": True, "Value": True, "__name__": True},
                    "renameByName": {"alertname": "alerta", "severidade": "severidade"},
                },
            },
        ],
        "options": {"showHeader": True},
        "fieldConfig": {"defaults": {}, "overrides": []},
    }


def linha(titulo, y):
    return {
        "id": _id(),
        "type": "row",
        "title": titulo,
        "collapsed": False,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
        "panels": [],
    }


def montar() -> dict:
    verde_vermelho = [{"color": "red", "value": None}, {"color": "green", "value": 1}]
    ok_falha = [
        {"type": "value", "options": {"0": {"text": "DEGRADADO", "color": "red"}}},
        {"type": "value", "options": {"1": {"text": "SAUDÁVEL", "color": "green"}}},
    ]
    paineis = [
        linha("Saúde da plataforma", 0),
        stat(
            "Worker",
            "agrosmart_worker_saudavel",
            0,
            1,
            limiares=verde_vermelho,
            mapas=ok_falha,
            descricao="Último ciclo ok e recente (mesma regra do /health).",
        ),
        stat(
            "Lake (MinIO)",
            'up{job="minio"}',
            4,
            1,
            limiares=verde_vermelho,
            mapas=[
                {"type": "value", "options": {"0": {"text": "FORA", "color": "red"}}},
                {"type": "value", "options": {"1": {"text": "NO AR", "color": "green"}}},
            ],
        ),
        stat(
            "Ciclos ok",
            'agrosmart_ciclos_total{status="ok"}',
            8,
            1,
            limiares=[{"color": "green", "value": None}],
        ),
        stat(
            "Ciclos com erro",
            'agrosmart_ciclos_total{status="erro"}',
            12,
            1,
            limiares=[{"color": "green", "value": None}, {"color": "orange", "value": 1}],
        ),
        stat(
            "Duração do último ciclo",
            "agrosmart_ultimo_ciclo_duracao_segundos",
            16,
            1,
            unidade="s",
            limiares=[
                {"color": "green", "value": None},
                {"color": "orange", "value": 45},
                {"color": "red", "value": 90},
            ],
        ),
        stat(
            "Idade da última leitura (landing)",
            'agrosmart_landing_sensores{medida="idade_ultima_s"}',
            20,
            1,
            unidade="s",
            limiares=[
                {"color": "green", "value": None},
                {"color": "orange", "value": 120},
                {"color": "red", "value": 300},
            ],
            descricao="Sinal de vida do gateway MQTT: tempo desde o arquivo de sensores mais novo.",
        ),
        linha("Execução do ciclo (processo)", 5),
        serie(
            "Duração por etapa (última execução)",
            [alvo("agrosmart_etapa_duracao_segundos", "{{etapa}}")],
            0,
            6,
            w=12,
            unidade="s",
            empilhar=True,
            descricao="Empilhado = lead time do ciclo. A etapa mais alta é o gargalo.",
        ),
        serie(
            "Ciclos por status (acumulado)",
            [alvo("agrosmart_ciclos_total", "{{status}}")],
            12,
            6,
            w=12,
            descricao="Degrau em 'erro' = ciclo falhou; o worker tenta de novo no ciclo seguinte.",
        ),
        serie(
            "Execuções de etapa por status (por 5 min)",
            [alvo("increase(agrosmart_etapa_execucoes_total[5m])", "{{etapa}} · {{status}}")],
            0,
            14,
            w=12,
            barras=True,
            empilhar=True,
        ),
        serie(
            "Lotes vistos pela ingestão (por 5 min)",
            [alvo("increase(agrosmart_lotes_ingeridos_total[5m])", "{{status}}")],
            12,
            14,
            w=12,
            empilhar=True,
        ),
        linha("Dados e decisão", 22),
        barras(
            "Último silver: registros",
            "agrosmart_silver_registros",
            "{{resultado}}",
            0,
            23,
        ),
        barras(
            "Último plano de ações",
            "agrosmart_plano_acoes",
            "{{tipo}}",
            8,
            23,
        ),
        stat(
            "Comandos de irrigação publicados (QoS 1)",
            "agrosmart_comandos_publicados_total",
            16,
            23,
            w=8,
            h=8,
            limiares=[{"color": "blue", "value": None}],
        ),
        barras(
            "Objetos por bucket do lake (MinIO)",
            "sum by (bucket) (minio_bucket_usage_object_total)",
            "{{bucket}}",
            0,
            31,
            w=12,
        ),
        serie(
            "Arquivos de sensores na landing",
            [alvo('agrosmart_landing_sensores{medida="arquivos"}', "arquivos")],
            12,
            31,
            w=12,
        ),
        tabela_alertas(0, 39),
    ]
    return {
        "uid": "agrosmart-operacao",
        "title": "AgroSmart · Operação da plataforma",
        "tags": ["agrosmart", "fase7"],
        "timezone": "browser",
        "editable": False,
        "refresh": "10s",
        "schemaVersion": 39,
        "time": {"from": "now-1h", "to": "now"},
        "panels": paineis,
        "templating": {"list": []},
        "annotations": {"list": []},
    }


def main() -> None:
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(json.dumps(montar(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"painel em {DESTINO.relative_to(RAIZ).as_posix()}")


if __name__ == "__main__":
    main()
