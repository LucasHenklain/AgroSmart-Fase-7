# ADR 0003 — Polars para processamento e DuckDB para SQL analítico, em vez de Spark

**Status:** aceito · **Data:** 2026-09-03 · **Decisores:** E1, E2

## Contexto

O volume da fase é de dezenas de milhares de registros por mês (34 mil leituras de sensores, centenas de registros nas demais fontes). Spark é o padrão da indústria para lakes de grande escala, mas exige JVM, cluster ou modo local pesado, e adiciona minutos de partida a cada execução.

## Decisão

- **Polars** para leitura, tipagem, qualidade e escrita do silver — expressões vetorizadas, tipagem explícita, sem JVM.
- **DuckDB** para o gold e para consultas ad hoc — cada tabela do gold é uma consulta SQL declarada em `processamento/gold.py`, e `agrosmart sql` abre o lake inteiro para o analista.

As tabelas do silver são registradas no DuckDB como *views* a partir dos Parquet lidos pela abstração de armazenamento — o mesmo caminho em MinIO e em filesystem, sem a extensão `httpfs`.

## Consequências

- Pipeline completo de 30 dias roda em menos de dois minutos em um notebook; a suíte de testes em poucos minutos.
- O gold é **documentação executável**: o balanço hídrico está escrito em SQL com CTEs nomeadas, exatamente como se explica em sala.
- Limite de escala: dezenas de milhões de linhas em memória. Acima disso, DuckDB lê Parquet direto do S3 com `httpfs`, e o passo seguinte natural é Spark ou Trino lendo **os mesmos arquivos** — a decisão não cria dívida de formato.
- Equivalência em nuvem documentada: Athena/Glue (SQL sobre Parquet no S3) cumprem o papel do DuckDB; EMR/Glue Jobs, o do Polars.

## Alternativas consideradas

- **Spark local.** Partida lenta, dependência de Java, e nenhuma vantagem no volume da fase.
- **Pandas.** Tipagem frouxa e sem expressões lazy; Polars entrega o mesmo com tipos estritos e desempenho.
- **SQL direto em PostgreSQL.** Traria o dado para fora do lake e duplicaria a verdade.
