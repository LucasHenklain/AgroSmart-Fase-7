# ADR 0007 — Deduplicação declarada no contrato: fatos imutáveis (evento) versus registros reenviados (versão)

**Status:** aceito · **Data:** 2026-09-03 · **Decisores:** E1, E2

## Contexto

Repetição da chave natural significa coisas diferentes conforme a fonte. Uma leitura de sensor repetida é o gateway reenviando — duplicata. Um talhão que reaparece a cada snapshot diário do ERP é o mesmo cadastro em outro dia — versão. Um lançamento de operação relançado com custo corrigido é uma versão legítima. Tratar tudo como duplicata reprova dado bom; tratar tudo como versão esconde reenvios.

## Decisão

Cada contrato declara `dedup.estrategia`:

- **`evento`** (sensores, clima, imagens, laudos): repetição da chave é **duplicata** — viola a regra de unicidade, vai para a quarentena; fica a primeira ingestão.
- **`versao`** (erp_talhoes, erp_operacoes): repetição da chave **entre lotes** é nova versão — fica a mais recente por `campo_ordem` (`extraido_em`), as anteriores são *substituídas* (contadas, gravadas em `silver/_substituidos/`, não quarentenadas). Repetição **no mesmo lote** continua sendo duplicata.

As regras de valor rodam **antes** da deduplicação, sobre todos os registros: se a versão mais recente é defeituosa, ela vai para a quarentena e a última versão **válida** é a que fica no silver.

## Consequências

- Reconciliação fecha com três parcelas: `lidos = aprovados + quarentena + substituídos`.
- Snapshots diários de cadastro (SCD tipo 1) e correções retroativas funcionam sem código específico por fonte.
- O silver nunca regride para uma versão defeituosa (testado: `test_versao_mais_recente_reprovada_deixa_a_ultima_valida_no_silver`).
- Custo: a semântica precisa ser decidida no contrato — o que é exatamente o lugar certo.

## Alternativas consideradas

- **Sempre "último vence".** Esconde reenvios do gateway e infla contagens de leituras como se fossem novas.
- **Sempre "primeiro vence".** Congela o cadastro no primeiro snapshot e ignora correções do ERP.
- **SCD tipo 2 no silver.** Histórico completo de versões; útil no futuro, desnecessário para a rubrica — o histórico está no bronze e em `_substituidos`.
