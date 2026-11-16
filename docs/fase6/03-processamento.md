# Requisito 3 — Processamento e preparação dos dados

> Peso: 20 % · Artefatos: script(s) de processamento + dataset processado de exemplo + fluxo documentado
> Status: **implementado e verificado**. O fluxo é o mesmo da Fase 5 (`agrosmart ingerir` → `agrosmart processar`), agora alimentado pela coleta IoT real da Fase 6 (`agrosmart coletar-mqtt`). Os datasets de exemplo desta página foram extraídos do lake de referência em MinIO com DuckDB — nada foi digitado à mão.

## 1. O fluxo em uma página

```
ESP32 / emulador ──MQTT──► coletar-mqtt ──► landing/sensores/<dia>/leituras_<dia>T<hhmm>Z.jsonl
                                                   │
                                        agrosmart ingerir  (ColetorStreaming: schema na porta, manifest, idempotência)
                                                   ▼
                                     bronze  sensores/ano=/mes=/dia=/leituras_*.jsonl  +  _manifests/
                                                   │
                                        agrosmart processar --camada silver
                                        leitura (texto) → tipagem (contrato) → 12 regras SEN-* → dedup → LGPD
                                                   ▼
                              silver  sensores_leitura/ano=/mes=/dia=/*.parquet      quarentena  sensores/…/*.parquet
                                                   │
                                        agrosmart processar --camada gold   (SQL no DuckDB)
                                                   ▼
                              gold  dim_sensor · fato_leitura_diaria_talhao · fato_balanco_hidrico · fato_alerta_qualidade
                                                   │
                                   agrosmart automacao · copiloto · dashboard · agrosmart sql
```

| # | Etapa | Entrada → saída | Script / módulo | Comando |
|---|---|---|---|---|
| 1 | **Coletar** (Fase 6) | mensagens MQTT dos dispositivos → arquivos JSONL de 15 min na landing, só com as chaves do contrato, janela pelo **instante medido**, sem repetir chave natural no mesmo arquivo | `src/agrosmart/ingestao/mqtt_gateway.py` | `agrosmart coletar-mqtt` |
| 2 | **Ingerir** | landing → bronze: schema conferido na porta (JSON válido, nenhuma chave fora de `sensores.v1`), bytes intocados, manifest por lote, `batch_id` determinístico; lote repetido é pulado pelo checksum | `src/agrosmart/ingestao/streaming.py` (+ `base.py`) | `agrosmart ingerir` |
| 3 | **Ler** | todos os lotes do bronze → um DataFrame *todo texto* com `_batch_id`, `_ingested_at`, `_janela_fim`, `_particao` vindos do manifest | `src/agrosmart/processamento/leitura.py` | parte de `processar` |
| 4 | **Tipar** | texto → tipo do contrato (`medido_em` timestamp UTC, `umidade_solo_pct` float…); o que não converte vira nulo e cai em `TIP-01` | `src/agrosmart/processamento/tipagem.py` | parte de `processar` |
| 5 | **Validar** | regras do contrato: críticas reprovam (quarentena com `_regras_violadas` e `_motivo`), alertas marcam (`_alertas`); depois dedup e frescor por sensor | `src/agrosmart/qualidade/motor.py`, `contratos/sensores.v1.yaml` | parte de `processar` |
| 6 | **Preparar e gravar o silver** | aprovados → unidades canônicas, UTC, LGPD, Parquet zstd particionado pelo **dia do evento**; reconciliação `declarados = lidos = aprovados + quarentena + substituídos` | `src/agrosmart/processamento/silver.py` | `agrosmart processar --camada silver` |
| 7 | **Refinar** | silver → 14 tabelas gold por SQL (DuckDB) com os limiares de `agronomia.py` | `src/agrosmart/processamento/gold.py` | `agrosmart processar --camada gold` |
| 8 | **Catalogar** | inventário, linhagem bronze → silver → gold e dicionário regenerados | `src/agrosmart/catalogo/__init__.py` | `agrosmart catalogo` (ou `processar`) |
| 9 | **Manter** | compactação mensal do silver e retenção por contrato | `src/agrosmart/processamento/manutencao.py` | `agrosmart manter` |
| 10 | **Orquestrar** | etapas 2–8 + copiloto em um comando, com medição; backfill por intervalo | `src/agrosmart/orquestracao.py` | `agrosmart run --all` · `run --from --to` |

O contrato `contratos/sensores.v1.yaml` é a fronteira entre a coleta e o processamento: o gateway da Fase 6 produz arquivos com o mesmo layout, mesma ordem de chaves e mesmo formato de linha que o simulador da Fase 5 produzia, então **nenhuma linha das etapas 2–10 mudou** para receber dados reais do ESP32. É a prova prática do princípio "contrato antes de código".

## 2. Dataset processado de exemplo

Dois arquivos em `data/exemplos/fase6/processado/`, extraídos do lake de referência (MinIO, semente `20260905`, data de referência `2026-09-01`) com DuckDB sobre o silver e o gold — a consulta está na seção 6 e pode ser refeita por qualquer avaliador.

### 2.1 `sensores_leitura_amostra.csv` — silver, 48 leituras

Os 12 sensores × 4 janelas de 15 min (20/08/2026, 09:00–09:45 UTC), exatamente como estão em `silver/sensores_leitura/ano=2026/mes=08/dia=20/`. Colunas: os 11 campos do contrato já tipados mais a proveniência (`_batch_id`, `_ingested_at`, `_schema_version`, `_alertas`).

```
sensor_id,talhao_id,medido_em,umidade_solo_pct,temperatura_solo_c,temperatura_ar_c,umidade_ar_pct,condutividade_ds_m,ph,bateria_pct,rssi_dbm,_batch_id,_ingested_at,_schema_version,_alertas
SNS-0101,TLH-01,2026-08-20T09:00:00Z,29.8,19.3,22.3,60.2,1.12,6.2,88.2,-73,e4f8b7d2ae803073,2026-09-03T15:53:33Z,sensores.v1,""
SNS-0302,TLH-03,2026-08-20T09:00:00Z,28.9,19.3,21.7,58.9,1.26,6.33,4.7,-74,e4f8b7d2ae803073,2026-09-03T15:53:33Z,sensores.v1,SEN-10
```

O que a amostra mostra:

- **Tipos e unidades do contrato**: `medido_em` em UTC ISO-8601, numéricos como float, `rssi_dbm` inteiro. No bronze tudo isso era texto dentro de um JSONL.
- **Alerta sem descarte**: as 4 leituras do `SNS-0302` têm `bateria_pct` 4,6–4,7 % e carregam `SEN-10` em `_alertas`. A leitura continua válida (o sensor mede bem com bateria baixa), mas o alerta sobe para `dim_sensor`, para `fato_alerta_qualidade` e, na Fase 6, vira a ação `trocar_bateria` do motor de decisão. Dez horas depois desta amostra o sensor calou por cinco dias (seção 2.2).
- **Linhagem por linha**: cada leitura sabe de que lote veio. As 48 leituras vêm de **5 lotes** para 4 janelas: a leitura do `SNS-0401` das 09:45 chegou atrasada, no lote seguinte (`_batch_id` `9cc1162c…`, buffer offline do gateway), e mesmo assim está no silver pelo seu `medido_em` — ao lado das outras 11 da mesma janela — e não pela hora em que chegou.
- **Nenhuma duplicata**: a chave natural `(sensor_id, medido_em)` é única; as 205 repetições que o gateway reenviou na janela de 30 dias estão na quarentena por `SEN-02`, não aqui.

### 2.2 `fato_leitura_diaria_talhao.csv` — gold, 186 linhas

A tabela inteira (6 talhões × 31 dias): agregação diária das leituras por talhão com **cobertura** (`n_leituras / leituras_esperadas`, onde o esperado é sensores instalados × 96 leituras/dia), mínimos e máximos, bateria mínima e leituras com alerta.

| talhão | dia | n_leituras | sensores ativos | umidade média | cobertura | leituras c/ alerta | bateria mín. |
|---|---|---|---|---|---|---|---|
| TLH-03 | 2026-08-19 | 191 | 2 | 27,17 % | 99,5 % | 96 | 5,8 % |
| TLH-03 | 2026-08-20 | 190 | 2 | 29,70 % | 99,0 % | 95 | 2,9 % |
| TLH-03 | 2026-08-21 | 96 | **1** | 32,42 % | **50,0 %** | 0 | 85,0 % |
| TLH-03 | 2026-08-25 | 96 | 1 | — | 50,0 % | 0 | 85,0 % |
| TLH-03 | 2026-08-28 | 190 | 2 | 27,44 % | 99,0 % | 0 | 85,0 % |
| TLH-03 | 2026-09-01 | 24 | 2 | 29,79 % | 12,5 % | 0 | 89,0 % |

É o INC-01 visto do gold: enquanto o `SNS-0302` esteve calado (21 a 25/08) o talhão ficou com um sensor ativo e cobertura de 50 %; o dia 01/09 tem só as 3 h da madrugada até o "agora" do pipeline (03h00 UTC), por isso 12,5 %. Cobertura abaixo de 80 % em algum dia da semana é um dos gatilhos de `inspecionar_sensor` na automação (`docs/fase6/04`), e é o que rebaixa a confiança do copiloto. Cobertura média da tabela: 95,3 %; mínima: 12,0 %.

## 3. Qualidade, quarentena e deduplicação — em um parágrafo cada

**Qualidade.** O silver só recebe o que passou por todas as regras *críticas* do contrato da fonte; para `sensores.v1` são 12 regras (`SEN-01` a `SEN-12`) mais a regra de sistema `TIP-01`. Regras de valor rodam registro a registro, sobre todos os registros, antes de qualquer outra coisa — `nao_nulo` (identificação, instante e umidade), `regex` (`^SNS-\d{4}$`), `intervalo` (umidade 0–100, temperatura do solo −10–70, pH 3–10), `nao_futuro` relativo ao **fim da janela do lote** com 5 min de tolerância (relógio adiantado é pego no momento em que a leitura chega), `referencia` (o `talhao_id` precisa existir no silver de `erp_talhoes`, por isso o ERP é processado antes). Severidade `critica` reprova; `alerta` (bateria < 15 %, sinal < −95 dBm, condutividade > 10) mantém a leitura e grava o id da regra em `_alertas`. No lake de referência, 34.431 leituras lidas → 34.040 aprovadas, 391 na quarentena, 673 com alerta — e os 391 defeitos injetados pela simulação nessa fonte foram todos localizados (406 de 406 no total das seis fontes, `outputs/dq_report.json`).

**Quarentena.** Nada é descartado em silêncio: o registro reprovado vai para `agrosmart-quarentena/sensores/ano=/mes=/dia=/` em Parquet, com `_regras_violadas` (lista de ids) e `_motivo` legível (`SEN-04 intervalo umidade_solo_pct min=0 max=100`), particionado pela **data em que entrou na quarentena** — é dela que a retenção de 30 dias conta. Lotes que nem passaram pela porta (JSON inválido, chave fora do contrato) ficam em `_rejeitados/<fonte>/<batch_id>/` com o arquivo original e `_motivo.json`. A quarentena é consultável por SQL (`quarentena_sensores` em `agrosmart sql`) e é o que fecha a reconciliação: `declarados nos manifests = lidos = aprovados + quarentena + substituídos`, verificada a cada execução e registrada em `dq_report.json → fontes.sensores.reconciliacao_ok`. Por regra, na referência: `SEN-02` 205 · `SEN-04` 76 · `SEN-09` 45 · `SEN-08` 29 · `SEN-01` 25 · `SEN-03` 11.

**Deduplicação.** Leitura de sensor é fato imutável, então o contrato declara `dedup.estrategia: evento`: a chave natural `(sensor_id, medido_em)` só pode aparecer uma vez e qualquer repetição é duplicata do buffer do gateway → quarentena por `SEN-02` (a regra de unicidade). A dedup roda **só entre os aprovados** e depois das regras de valor, ordenando por `_ingested_at`, `_batch_id` e linha — a primeira ingestão vence, e se a repetição tiver também outro defeito ela cai pela regra mais específica (foi o caso de 2 das 207 duplicatas injetadas). Fontes de *versões* (ERP) usam a estratégia `versao`: fica a mais recente **válida** por `extraido_em`, e as anteriores são contadas como substituídas, não como defeito. Na Fase 6 há uma segunda linha de defesa antes do lake: o gateway `coletar-mqtt` não grava a mesma chave natural duas vezes no mesmo arquivo de janela (carrega as chaves já presentes antes de fazer *append*), de modo que a repetição do dispositivo é contada no `outputs/coleta_mqtt.json` e a regra `SEN-02` fica para o que escapar — reinício entre janelas, reenvio em janela diferente.

## 4. Tempos medidos

Execução de referência (`outputs/run.json`, backend MinIO, 30 dias, 6 talhões, 12 sensores; notebook comum):

| Etapa | Duração | O que foi feito |
|---|---|---|
| `simular` | 12,8 s | 3.149 arquivos · 34.794 registros · 406 defeitos (semente fixa) |
| `ingerir` | 16,1 s | 3.081 lotes, **3.081 pulados** (checksum idêntico — idempotência) |
| `silver` | 14,6 s | 34.794 lidos · 34.208 aprovados · 405 quarentena · 181 substituídos · reconciliação ok |
| `gold` | 5,0 s | 14 tabelas |
| `catalogo` | 1,8 s | 6 fontes · 14 tabelas gold · dicionário |
| `copiloto` | 1,0 s | 18 saídas (motor de regras) |
| **total** | **53,8 s** | |

Em lake limpo a ingestão leva 45 s (38 s são os 2.892 lotes de sensores, o problema dos arquivos pequenos — `docs/02` §8.3); só o silver dos sensores, 11,9 s (`dq_report.json → fontes.sensores.duracao_s`). Medidos ao gerar os exemplos desta página (DuckDB sobre MinIO): abrir a conexão com todas as tabelas do silver e do gold registradas, 4,7 s; a consulta da amostra do silver, 1,7 s; a leitura inteira de `fato_leitura_diaria_talhao`, 8 ms. A coleta MQTT em si não entra nesta conta — é contínua; a janela de 15 min é fechada pelo relógio do dado, não pelo tempo de processamento.

## 5. Decisões que explicam o desenho

- **Texto primeiro, tipo depois, regra por último** (`leitura` → `tipagem` → `qualidade`): separar as três portas é o que permite dizer, para cada registro reprovado, *onde* ele caiu.
- **Recomputo total do silver** a cada execução ([ADR 0008](../adr/0008-recomputo-total-do-silver.md)): regra corrigida hoje vale para o histórico; o backfill por intervalo (`run --from --to`) existe para a origem que reenvia alguns dias.
- **Bronze particiona pela janela do lote; silver, pelo dia do evento; quarentena, pela ingestão.** A leitura atrasada da seção 2.1 é a razão prática: o dado precisa estar onde a análise o procura.
- **Gold em SQL, não em Polars** ([ADR 0003](../adr/0003-duckdb-e-polars-em-vez-de-spark.md)): a consulta é a documentação do indicador; `fato_leitura_diaria_talhao` está escrita como se explica em sala.
- **Limiares em um só lugar** (`src/agrosmart/agronomia.py`): simulador, gold, copiloto e, agora, o motor de automação leem os mesmos números.

## 6. Como reproduzir

```bash
docker compose -f infra/docker-compose.yml up -d        # MinIO (+ Mosquitto, Fase 6)
agrosmart coletar-mqtt --duracao-s 900                   # Fase 6: gateway real → landing/sensores (ou `agrosmart simular`)
agrosmart ingerir                                        # landing → bronze
agrosmart processar                                      # bronze → silver → gold → catálogo
agrosmart qualidade                                      # relatório de qualidade e reconciliação
```

Os dois CSVs de `data/exemplos/fase6/processado/` saem destas consultas (`agrosmart sql` ou DuckDB via `abrir_conexao(..., silver=True, gold=True)` de `processamento/gold.py`):

```sql
-- silver: 12 sensores x 4 janelas, com proveniencia e alertas
SELECT sensor_id, talhao_id, medido_em, umidade_solo_pct, temperatura_solo_c, temperatura_ar_c,
       umidade_ar_pct, condutividade_ds_m, ph, bateria_pct, rssi_dbm,
       _batch_id, _ingested_at, _schema_version, coalesce(array_to_string(_alertas, ';'), '') AS _alertas
FROM sensores_leitura
WHERE medido_em >= TIMESTAMPTZ '2026-08-20 09:00:00+00' AND medido_em < TIMESTAMPTZ '2026-08-20 10:00:00+00'
ORDER BY medido_em, sensor_id;

-- gold: a tabela inteira
SELECT * FROM fato_leitura_diaria_talhao ORDER BY talhao_id, dia;
```

Para conferir a quarentena da mesma janela: `agrosmart sql "SELECT sensor_id, medido_em, _motivo FROM quarentena_sensores WHERE CAST(medido_em AS DATE) = DATE '2026-08-20' ORDER BY medido_em"`.
