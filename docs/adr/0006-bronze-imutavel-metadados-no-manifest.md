# ADR 0006 — Bronze byte a byte igual à origem; metadados de ingestão no objeto e no manifest

**Status:** aceito · **Data:** 2026-09-03 · **Decisores:** E1

## Contexto

É comum "enriquecer" o dado bruto na entrada — anexar `_ingested_at`, `_batch_id` dentro de cada registro JSON ou como colunas extras no CSV. Isso é conveniente para o silver, mas torna o bronze uma *versão* da origem, não a origem.

## Decisão

O bronze guarda os **bytes exatos** que a origem produziu (testado: `test_objeto_bruto_e_identico_ao_da_landing`). A proveniência vive em dois lugares:

- **metadados do objeto** (S3 user metadata): `batch-id`, `ingested-at`, `sha256`, `schema-version`, `source`, `papel`;
- **manifest do lote** em `_manifests/<fonte>/<partição>/<batch_id>.json`: janela, origem, arquivos (chave, checksum, bytes, papel), registros declarados, versão, checksum do lote.

O silver anexa a proveniência como colunas (`_batch_id`, `_ingested_at`, `_schema_version`) ao ler cada lote.

## Consequências

- Auditoria e reprocessamento partem do dado verdadeiro: qualquer regra pode mudar e o lake inteiro é reconstruível.
- Idempotência e versionamento ficam triviais: o manifest carrega o checksum do lote; conteúdo igual → pula, diferente → nova versão.
- Reconciliação tem um número declarado para comparar (`manifest.registros`) que não depende de reinterpretar o arquivo.
- Custo: um objeto extra por lote (dobra a contagem de objetos do bronze) e um `read` a mais por lote no silver. Irrelevante frente à garantia.

## Alternativas consideradas

- **Colunas de proveniência dentro do registro bruto.** Altera o dado; um `diff` com a origem deixa de ser zero.
- **Tabela de controle em banco relacional.** Introduz um segundo sistema de verdade fora do lake; o manifest no próprio bucket viaja junto com o dado em qualquer cópia ou restauração.
