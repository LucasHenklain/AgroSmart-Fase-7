# Evidências de execução

Saídas capturadas da CLI, refeitas do zero pela execução de referência (semente
`20260905`, data de referência `2026-09-01`, 30 dias). Qualquer pessoa reproduz os
mesmos números com `agrosmart simular && agrosmart ingerir`.

## Passo 2 — simulação e ingestão para o bronze

| Arquivo | O que mostra |
|---|---|
| `passo2-simular.txt` | landing gerada: arquivos e registros por fonte, defeitos injetados por tipo e regra alvo, estatísticas da trajetória |
| `passo2-ingerir.txt` | ingestão em lake **limpo**: lotes gravados por fonte, 0 rejeitados, exemplos exportados |
| `passo2-ingerir-reexecucao.txt` | a mesma landing ingerida sobre um bronze já populado: sensores/clima/talhões **pulados** (conteúdo idêntico), operações **atualizadas** (versão 2, porque o gerador mudou entre as execuções) — idempotência e versionamento em ação |
| `passo2-doctor.txt` | estado do ambiente após a ingestão: contagem de objetos por camada |
| `passo2-bronze-arvore-completa.txt` | `mc ls --recursive` do bucket `agrosmart-bronze`: partições Hive, manifests, binários ao lado dos sidecars |
| `passo2-minio-bronze-particoes.png` | console do MinIO: os quatro buckets, `agrosmart-bronze` com 6.230 objetos, partição `sensores/ano=2026/mes=08/dia=28` com os lotes de 15 min e o painel do objeto (versão, ETag) |
| `passo2-minio-manifests.png` | console: `_manifests/sensores/ano=2026/mes=08/dia=28` — um manifest por lote, 865 B cada |
| `passo2-minio-versionamento-rewind.png` | console: raiz do bucket com as seis fontes e `_manifests`, e o diálogo **Rewind**, que só existe com versionamento habilitado |

## Passo 3 — qualidade, silver, gold, catálogo e manutenção

| Arquivo | O que mostra |
|---|---|
| `passo3-processar.txt` | `agrosmart processar`: qualidade por fonte (lidos, aprovados, quarentena, duplicatas, substituídos), regras acionadas, alertas de frescor por sensor, **defeitos injetados localizados**, reconciliação; gold materializado (13 tabelas) |
| `passo3-qualidade.txt` | `agrosmart qualidade`: o relatório `dq_report.json` renderizado |
| `passo3-manter.txt` | `agrosmart manter`: compactação dos 30 Parquet diários de sensores em 1 por mês; retenção simulada (nada vencido na janela de referência) |
| `passo3-catalogo.txt` | `agrosmart catalogo`: inventário silver/gold com linhagem, e regeneração de `docs/dicionario-de-dados.md` |
| `passo3-sql-*.txt` | consultas ad hoc via `agrosmart sql`: balanço hídrico do TLH-03, benchmark regional, alertas de qualidade |
| `passo3-doctor.txt` | contagem de objetos por camada após o processamento |
| `passo3-silver-arvore.txt` | `mc ls --recursive` dos buckets silver, gold e quarentena |

## Passo 4 — copiloto de IA generativa

| Arquivo | O que mostra |
|---|---|
| `passo4-ia-contexto-tlh03.txt` | `agrosmart ia contexto TLH-03`: o Context Pack completo do talhão — o universo do que o copiloto pode citar |
| `passo4-ia-gerar.txt` | `agrosmart ia gerar`: 3 casos × 6 talhões, motor usado, confiança, prioridade, guardrails e arquivo gerado |
| `passo4-ia-avaliar.txt` | `agrosmart ia avaliar`: aprovação nos guardrails, ajustes, fallbacks e latência por motor; concordância regras × modelo quando há chave de API |
| `../relatorios/<caso>/<talhão>_2026-09-01.{md,pdf,contexto.json,proveniencia.json}` | as 18 saídas do copiloto com Context Pack e proveniência anexados |
| `../relatorios/avaliacao_ia.md` | relatório de avaliação em Markdown |

## Passo 5 — orquestração, consumo e entrega

| Arquivo | O que mostra |
|---|---|
| `passo5-run-all.txt` | `agrosmart run --all` sobre o lake já populado: as seis etapas com duração e resumo; a ingestão **pula** os 3.081 lotes já presentes (idempotência), silver/gold/catálogo/copiloto recomputados; `outputs/run.json` |
| `passo5-backfill.txt` | `agrosmart run --from 2026-08-21 --to 2026-08-25` (a janela do INC-01): só os lotes do intervalo são lidos nas fontes de eventos, o ERP é recomputado por inteiro, e a tabela `sensores_leitura` termina com o mesmo total de linhas — a mesclagem por partição preserva eventos atrasados |
| `passo5-empacotar.txt` | `agrosmart empacotar --simular`: o que entra no zip, o nome validado e os avisos do que ainda falta (link do vídeo, prints do console) |
| `passo5-dashboard-<seção>.png` | a camada de consumo (`app/dashboard.py`) sobre o gold, talhão TLH-02: visão do talhão, balanço hídrico com limiares, NDVI, custos, relatório do copiloto com proveniência, qualidade dos dados, sensores e benchmark regional — capturadas por `scripts/capturar_dashboard.py` |
| `passo5-doctor.txt` | estado final do ambiente: objetos por camada |

## Fase 6 · requisito 2 — coleta IoT por MQTT

Demonstração local de ponta a ponta (broker mosquitto em Docker, emulador dos 12 dispositivos e
gateway MQTT rodando em paralelo), refeita com `scripts` equivalentes aos comandos mostrados no
cabeçalho de cada arquivo. Detalhes em `docs/fase6/02-coleta-iot.md`.

| Arquivo | O que mostra |
|---|---|
| `fase6-02-mosquitto-up.txt` | `docker compose up -d mosquitto`, `ps` e o healthcheck em `healthy` |
| `fase6-02-dispositivo.txt` | `agrosmart dispositivo` por 90 s: 12 dispositivos a cada 5 s, leituras atrasadas/duplicadas pelo "buffer", e a reação aos comandos de irrigação (ligar/desligar) publicados no tópico de atuadores |
| `fase6-02-coletar-mqtt.txt` | `agrosmart coletar-mqtt --janela-min 1` por 100 s: janelas abertas, chave `luminosidade_lux` fora do contrato descartada, resumo (recebidas, gravadas, duplicatas, arquivos) |
| `fase6-02-mosquitto-log.txt` | log do broker: conexões do gateway, do emulador e do publicador de comandos |
| `fase6-02-landing-arvore.txt` | a landing gerada pelo gateway — mesmo layout do simulador — com linhas por arquivo e 5 leituras de exemplo |
| `fase6-02-ingerir.txt` | `agrosmart ingerir --fonte sensores`: os lotes do gateway entram no bronze pelo `ColetorStreaming` da Fase 5, sem rejeição |
| `fase6-02-processar-silver.txt` | `agrosmart processar --camada silver --fonte sensores`: as leituras reais passam pelas mesmas regras do contrato |

## Capturas de tela do console do MinIO

Feitas pela equipe em 05/09/2026 (`passo2-minio-*.png`, acima) e referenciadas em `docs/02-ingestao-e-manutencao.md` §6. Não capturada: `agrosmart-quarentena/_rejeitados/` após provocar o INC-02 — o cenário está coberto por teste automatizado (`tests/test_ingestao.py`).
