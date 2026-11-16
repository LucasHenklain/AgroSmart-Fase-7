# ADR 0010 — Orquestração na própria CLI e backfill por intervalo

**Status:** aceita · **Data:** 03/09/2026 · **Passo:** 5

## Contexto

O pipeline tem seis etapas (simular, ingerir, silver, gold, catálogo, copiloto)
que até aqui eram comandos separados. A entrega precisa de um caminho de um
comando — para o avaliador reproduzir e para a operação agendar — e de um
mecanismo de **reprocessamento parcial** quando uma origem corrige o passado
(um lote de sensores recuperado do gateway, uma regra de qualidade ajustada).

Opções consideradas para a orquestração: Airflow/Dagster/Prefect, `make`, um
script shell, ou um comando da própria CLI.

## Decisão

1. **A orquestração é `agrosmart run`**, implementada em `orquestracao.py`,
   reaproveitando as mesmas funções que os comandos individuais chamam. Cada
   etapa é medida, o resumo vai para `outputs/run.json`, a trilha de eventos
   recebe um evento `run`, e a execução **para na primeira falha** (uma
   etapa que falha deixa as seguintes com entrada inconsistente).
2. **Backfill é `run --from A --to B`** e tem semântica diferente por tipo de
   fonte, declarado no contrato (`dedup.estrategia`):
   - **eventos** (sensores, clima, imagens, laudos): só os lotes cujas
     partições do bronze caem em `[A, B]` são lidos, e o silver recebe
     **exatamente o que veio deles** — por partição afetada, as linhas cujo
     `_batch_id` pertence aos lotes reprocessados saem, as novas entram, e a
     partição é deduplicada pela chave natural na mesma ordem do motor.
     Reescrever partições inteiras seria mais simples, mas perderia eventos
     entregues em lote de outro dia (chegada atrasada), que o teste de
     backfill flagrou: uma linha a menos. As partições visitadas são o
     intervalo com margem de um dia para cada lado mais os dias de evento das
     novas linhas.
   - **versões** (ERP de talhões e operações): a deduplicação precisa ver
     **todas** as versões da chave, inclusive fora do intervalo — então a
     tabela é recomputada por inteiro. É pequena; o custo é irrelevante.
   - a **quarentena acumula** no backfill (é trilha de auditoria, particionada
     pela data de ingestão; a retenção de 30 dias poda) e o **gold é sempre
     recomputado do silver completo** (ADR 0008).
3. **Não há agendador na entrega.** Em produção, `agrosmart run --sem-simular`
   é o alvo de um cron, de um Step Functions ou de um DAG de uma tarefa. O que
   um orquestrador de mercado acrescenta (retry, alertas, UI de histórico) é
   valor real, mas não é o que esta fase avalia — e instalá-lo esconderia a
   lógica atrás de uma ferramenta.

## Consequências

- Reproduzir a entrega é `agrosmart run --all`. Um avaliador com Docker e
  Python chega ao mesmo lake, aos mesmos relatórios e ao mesmo dashboard.
- O backfill tem dois casos que ele **não** resolve sozinho: uma fonte de
  eventos cujo lote foi ingerido com a partição errada (caminho: INC-03 do
  runbook, reingestão com `--forcar`, depois o backfill) e um evento
  entregue com mais de um dia de atraso em relação ao intervalo, cuja
  partição do silver fica fora da margem visitada (caminho: alargar o
  intervalo em um dia).
- Duplicatas eliminadas na mesclagem da partição não vão para a quarentena
  nesta passada — já estão lá desde a execução original, ou são as próprias
  cópias reprocessadas.
- `run` cresce junto com o pipeline: uma nova etapa entra como mais um bloco
  `rodar(...)` com o seu resumo. Testes em `tests/test_orquestracao.py`
  cobrem execução completa, backfill que recompõe partição apagada, parada na
  primeira falha e modos `--sem-simular`/`--sem-ia`.
