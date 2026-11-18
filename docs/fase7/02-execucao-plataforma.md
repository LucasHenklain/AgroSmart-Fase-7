# Requisito 2 — Execução da plataforma (funcionamento completo e integrado)

> Peso: 20 % · Artefatos: evidências (prints, logs, execução), descrição do funcionamento, exemplo de entrada e saída
> Status: **em execução contínua** desde 17/11/2026 nesta máquina (Windows 11 + Docker Desktop 27.5).
> Prints e logs em `outputs/prints/fase7-02-*`, `fase7-04-*`, `fase7-05-*`; trilha completa em
> `outputs/plataforma/eventos.jsonl`.

## 1. Como subir (do zero)

```bash
python -m pip install -e ".[dev,ia,app,iot]"                # só para usar a CLI no host (opcional)
docker compose -f infra/docker-compose.yml up -d --build    # 9 serviços; imagem agrosmart:7.0.0
```

Rede com inspeção TLS (proxy corporativo): `AGROSMART_CA_EXTRA=/caminho/ca.pem docker compose … up -d --build`
— o certificado entra só como *build secret*, nunca numa camada da imagem.

| Serviço | Endereço | Para quê |
|---|---|---|
| dashboard | <http://localhost:8501> (`?secao=automacao`, `?secao=operacao`) | consumo: gold, ações, operação |
| grafana | <http://localhost:3000> (leitura anônima) | painel **AgroSmart · Operação da plataforma** |
| prometheus | <http://localhost:9090/alerts> | regras de alerta |
| worker | <http://localhost:8000/status> · `/health` · `/metrics` | estado do ciclo |
| minio | <http://localhost:9001> (agrosmart / agrosmart123, só dev local) | buckets do lake |
| mosquitto | `localhost:1883` | broker MQTT (ESP32/Wokwi pode publicar aqui ou no HiveMQ) |

Na primeira subida com o lake vazio, o worker faz a **carga inicial** (30 dias simulados das seis
fontes, ~1 min) e depois entra no ciclo de 60 s. Evidência do estado dos containers:
`outputs/prints/fase7-02-compose-ps.txt` — 8 serviços de pé (dashboard, worker e MinIO *healthy*), mais
o `bootstrap` que roda uma vez.

## 2. O funcionamento, ponta a ponta

```
[dispositivos] --MQTT 5 s--> [mosquitto] --> [gateway] --JSONL 15 min--> landing/
     ^                                                                       |
     |                                                       [worker, a cada 60 s]
     |                                    ingerir → silver → gold → catálogo → decidir → atuar
     |                                         |        |        |                  |       |
     |                                       bronze   silver   gold (MinIO)     plano JSON  |
     +------------- agrosmart/atuadores/<talhão>/irrigacao (QoS 1) <-------------------------+
                                                   |
                       dashboard (gold + plano + operação) · Prometheus/Grafana · eventos.jsonl
```

1. **Entrada (IoT/simulação).** O container `dispositivos` emula os 12 sensores do catálogo e publica um
   JSON por sensor a cada 5 s (*simulação rotulada*; o ESP32 do Wokwi da Fase 6 publica o mesmo
   payload). O `gateway` assina `agrosmart/sensores/+/leitura` e grava no volume `landing`.
2. **Processamento.** O `worker` roda um caso por ciclo: ingere os micro-batches no bronze (MinIO),
   valida e tipa no silver (quarentena com motivo), materializa o gold (14 tabelas), atualiza o catálogo.
   Em modo incremental (padrão), pula silver/gold quando não chegou lote novo.
3. **Automação.** O motor de decisão lê o gold e produz o plano de ações; ações `irrigar` viram comando
   MQTT ao atuador do talhão, confirmado pelo broker. O emulador (ou o ESP32) liga a irrigação, a
   umidade do solo sobe nas leituras seguintes e a ação deixa de ser necessária — o ciclo fecha.
4. **Saídas.** Dashboard (gold, Automação, Operação), alertas (Prometheus/Grafana e ações de prioridade
   alta), relatórios (`outputs/acoes/acoes_<data>.{json,md}`, `run.json`, relatórios do copiloto).

## 3. Exemplo de entrada (real)

Linhas do micro-batch gravado pelo gateway no volume `landing`
(`outputs/prints/fase7-02-entrada-landing.jsonl.txt`, arquivo `landing/sensores/20261117/leituras_20261117T1645Z.jsonl`):

```json
{"sensor_id":"SNS-0601","talhao_id":"TLH-06","medido_em":"2026-11-17T16:48:44Z","umidade_solo_pct":11.8,
 "temperatura_solo_c":29.7,"temperatura_ar_c":29.0,"umidade_ar_pct":45.3,"condutividade_ds_m":null,
 "ph":null,"bateria_pct":93.4,"rssi_dbm":-74}
{"sensor_id":"SNS-0602","talhao_id":"TLH-06","medido_em":"2026-11-17T16:48:44Z","umidade_solo_pct":11.5,
 "temperatura_solo_c":29.8,"temperatura_ar_c":29.2,"umidade_ar_pct":48.3,"condutividade_ds_m":null,
 "ph":null,"bateria_pct":93.4,"rssi_dbm":-85}
```

## 4. Exemplos de saída (reais)

**Plano de ações** (`outputs/plataforma/acoes/acoes_2026-11-17.json`, gerado pelo worker às 00:00 UTC de
18/11): 6 talhões avaliados — TLH-06 em risco **crítico** (fração de água disponível 0,0,
`sequeiro_estresse`), TLH-01 em **atenção**, quatro normais — e uma ação:

```json
{"id": "ACT-0001", "talhao_id": "TLH-06", "sensor_id": "SNS-0602", "tipo": "inspecionar_sensor",
 "prioridade": "baixa",
 "parametros": {"motivo_tecnico": "sinal_fraco", "rssi_medio_dbm": -90.1, "leituras_sinal_fraco": 700,
                "fracao_sinal_fraco": 0.09, "limiar_rssi_dbm": -95},
 "motivo": "Sensor SNS-0602 com sinal médio de -90,1 dBm e 700 leitura(s) abaixo de -95 dBm (fração 0,090): reposicionar antena ou gateway para evitar perda de leituras.",
 "evidencias": ["dim_sensor:SNS-0602:2026-11-17"], "comando_mqtt": null, "status": "simulado"}
```

Note a **trava do sequeiro** funcionando: TLH-06 está crítico, mas é de sequeiro, então o motor **não**
gera irrigação (não há atuador para acionar) — a decisão correta é alertar, não agir.

**Comando ao atuador** publicado pelo worker (evento da trilha, `outputs/prints/fase7-02-saida-comandos-publicados.jsonl.txt`):

```json
{"ocorrido_em": "2026-11-17T13:43:29.710+00:00", "etapa": "automacao", "acao": "comando_publicado",
 "fonte": "TLH-02", "mensagem": "ACT-0001 agrosmart/atuadores/TLH-02/irrigacao {\"acao\": \"ligar\", \"minutos\": 165}",
 "metricas": {"qos": 1, "minutos": 165}, "execucao_id": "ciclo-20261117T134242-2c8e", "origem": "worker",
 "versao_processo": "v1", "tipo_caso": "ciclo"}
```

Depois dos comandos das 13:43–13:45 o emulador irrigou o TLH-02, a umidade subiu e os planos seguintes
deixaram de pedir irrigação (`comandos: 0` no painel) — o laço sensor → decisão → atuador → sensor fechou
sozinho, sem intervenção.

**Resumo de um ciclo** (`agrosmart status --url http://localhost:8000/status`, `outputs/prints/fase7-04-agrosmart-status.txt`):
etapas `ingerir → silver → gold → catalogo → decidir → atuar` todas `ok`; lotes, registros do silver,
plano e idade da landing.

**Dashboard** — `outputs/prints/fase7-04-dashboard-automacao.png` (plano, risco por talhão, ações com
motivo e status) e `fase7-04-dashboard-operacao.png` (saúde do worker, duração por etapa nos últimos
ciclos, participação no tempo de ciclo).

## 5. Números da execução

| Indicador | Valor | Fonte |
|---|---|---|
| Operação contínua observada | 13:06 de 17/11 → 13:00 de 18/11 (≈ 24 h) | `fase7-04-grafana-24h.png`, `fase7-02-compose-ps.txt` |
| Ciclos analisados na janela da mineração (7,75 h) | 445 (401 ok, 44 com falha — 32 do incidente DuckDB, 11 das quedas provocadas do MinIO e 1 ciclo abortado na troca v1 → v2) | `outputs/processos/metricas.json` |
| Ciclos desde a última subida do worker (≈ 20 h) | **1.183 ok, 0 com erro**, duração média 11,1 s | `fase7-04-worker-status.png` |
| Registros no último silver | 113.167 lidos · 112.978 aprovados · 8 em quarentena | painel Grafana |
| Objetos no lake | 6.524 objetos, 14.250 versões | painel Grafana (métricas do MinIO) |
| Comandos de irrigação publicados (QoS 1) | 4 no período v1 (TLH-02), confirmados pelo broker | trilha de eventos |

## 6. Rastreabilidade de uma execução

Cada ciclo deixa: o caso no event log (`execucao_id`), o `outputs/run.json` do último ciclo, o plano de
ações do dia, o estado do worker em `outputs/status/operacao.json` e as séries no Prometheus. Para
inspecionar um ciclo: `docker compose -f infra/docker-compose.yml logs worker | grep ciclo-<id>`.
