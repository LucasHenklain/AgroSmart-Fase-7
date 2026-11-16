# ADR 0008 — Silver e gold recomputados por inteiro a cada execução

**Status:** aceito (revisar quando o volume exigir) · **Data:** 2026-09-03 · **Decisores:** E1

## Contexto

Processamento incremental (só os lotes novos) é mais eficiente, mas introduz a classe mais cara de bugs em pipelines: estado parcial, partições meio escritas, dedup que depende de ordem de chegada, regras alteradas que não retroagem. Com 30 dias e 35 mil registros, o recomputo completo leva menos de um minuto.

## Decisão

`processar` lê **todo** o bronze da fonte, avalia todas as regras, deduplica sobre o conjunto completo e reescreve o silver da fonte (removendo os objetos anteriores da tabela). O gold é derivado do silver completo. A escrita permanece **um arquivo por dia de evento**, como uma execução diária produziria — e é isso que dá à compactação (`manutencao.compactar`) algo real para consolidar.

## Consequências

- Determinismo: o mesmo bronze produz sempre o mesmo silver; regra corrigida hoje vale para o histórico inteiro sem *backfill* especial.
- Simplicidade: nenhum estado de "última execução" a manter fora do lake.
- Custo cresce linearmente com o histórico. O gatilho para rever esta decisão é o recomputo passar de alguns minutos — quando isso acontecer, o caminho é processar por partição de lote (`_batch_id`/`_particao` já estão em cada linha) com *merge* por chave natural, ou adotar Iceberg (ADR 0002).
- O `backfill` do Passo 5 (`run --from --to`) reaproveita o mesmo código restringindo o intervalo de partições lidas.

## Alternativas consideradas

- **Incremental desde já.** Otimização prematura que trocaria correção garantida por desempenho que ninguém pediu.
- **CDC / streaming contínuo.** Adequado para latência de segundos; a decisão agronômica aqui é diária.
