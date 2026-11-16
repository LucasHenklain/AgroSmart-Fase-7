# Registros de decisão de arquitetura (ADR)

Cada decisão estruturante do Data Lake AgroSmart está registrada aqui no formato
*contexto → decisão → consequências → alternativas consideradas*. Um ADR não é
apagado quando a decisão muda: ganha status **substituído** e aponta para o novo.

| # | Decisão | Status |
|---|---|---|
| [0001](0001-object-store-s3-com-minio.md) | Object store S3-compatível (MinIO) como base do lake, com abstração de dois backends | aceito |
| [0002](0002-parquet-sem-formato-de-tabela.md) | Parquet particionado em silver e gold, sem Delta/Iceberg nesta fase | aceito |
| [0003](0003-duckdb-e-polars-em-vez-de-spark.md) | Polars para processamento e DuckDB para SQL, em vez de Spark | aceito |
| [0004](0004-quarentena-como-quarta-camada.md) | Quarentena como quarta camada | aceito |
| [0005](0005-contratos-yaml-como-fonte-unica.md) | Um contrato YAML por fonte como fonte única da verdade | aceito |
| [0006](0006-bronze-imutavel-metadados-no-manifest.md) | Bronze byte a byte igual à origem; proveniência no objeto e no manifest | aceito |
| [0007](0007-dedup-evento-versus-versao.md) | Deduplicação declarada: evento (duplicata) versus versão (substituição) | aceito |
| [0008](0008-recomputo-total-do-silver.md) | Silver e gold recomputados por inteiro a cada execução | aceito, revisar com volume |
| [0009](0009-ia-le-o-gold-com-saida-estruturada-e-guardrails.md) | Copiloto lê o gold via Context Pack, saída estruturada, guardrails e fallback determinístico | aceito |
| [0010](0010-orquestracao-cli-e-backfill-por-intervalo.md) | Orquestração na própria CLI (`run`), parada na primeira falha, backfill por intervalo com semântica por tipo de fonte | aceito |
