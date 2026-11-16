# ADR 0002 — Parquet particionado no silver e no gold, sem formato de tabela (Delta/Iceberg) nesta fase

**Status:** aceito · **Data:** 2026-09-03 · **Decisores:** E1, E2

## Contexto

As camadas confiável e refinada precisam de um formato colunar, tipado e legível por qualquer engine. Formatos de tabela como Delta Lake e Apache Iceberg acrescentam transações ACID, *time travel* e evolução de schema sobre o Parquet — ao custo de uma camada de metadados e de bibliotecas adicionais.

## Decisão

Gravar silver e gold em **Parquet** (compressão zstd) com **partição Hive** (`ano=/mes=/dia=`) pela data do evento, um arquivo por dia. Não adotar Delta nem Iceberg nesta fase.

## Consequências

- DuckDB, Polars, Spark e Athena leem as tabelas sem dependência extra; a partição vira coluna automaticamente.
- Sem transações: a escrita do silver é **recomputada por inteiro** a cada execução (ADR 0008), o que elimina a necessidade de *merge* transacional com os volumes atuais.
- *Time travel* é coberto de outra forma: o bronze é imutável e versionado, e o silver guarda `_batch_id`/`_ingested_at` em cada linha — qualquer estado passado é reconstruível.
- Evolução de schema fica no contrato (`contratos/<fonte>.v2.yaml`), com a `_schema_version` gravada em cada registro.
- Migração futura para Iceberg é aditiva: os mesmos arquivos Parquet passam a ser referenciados por um catálogo de metadados.

## Alternativas consideradas

- **Delta Lake.** Excelente com Spark; fora dele exige `delta-rs`, e traria complexidade sem ganho demonstrável na fase.
- **Apache Iceberg.** Melhor escolha para o futuro multi-engine; adotar agora custaria um catálogo (REST/Glue) que não acrescenta nada à rubrica.
- **CSV/JSON no silver.** Sem tipos nem compressão colunar — anularia o propósito da camada confiável.
