# ADR 0001 — Object store S3-compatível (MinIO) como base do Data Lake

**Status:** aceito · **Data:** 2026-09-02 · **Decisores:** E1, E2

## Contexto

O Data Lake precisa de um armazenamento que aceite qualquer formato (JSON, CSV, JPEG, PDF, Parquet), separe camadas com controle de acesso independente, suporte versionamento e ciclo de vida, e seja **reproduzível na máquina de um avaliador** sem conta em nuvem nem cartão de crédito.

## Decisão

Usar um object store com API S3 — **MinIO** em Docker no desenvolvimento e na demonstração — com um bucket por camada (`agrosmart-bronze`, `-silver`, `-gold`, `-quarentena`). Todo acesso ao lake passa por uma única abstração (`agrosmart.storage.Armazenamento`) com dois backends: MinIO (`boto3`) e filesystem local. A chave lógica do objeto é idêntica nos dois.

## Consequências

- Trocar MinIO por Amazon S3, GCS (modo S3) ou Azure (via gateway) é mudar o endpoint e as credenciais — o código não muda.
- Versionamento de bucket e regras de ciclo de vida são reais desde o primeiro dia (`infra/docker-compose.yml`), não uma promessa para a produção.
- O backend filesystem permite rodar a suíte de testes sem Docker e serve de plano B quando o Docker falha na máquina do avaliador.
- Custo: latência por chamada. Milhares de arquivos pequenos de IoT são lentos em série; mitigado com paralelismo no coletor e compactação no silver (ADR 0008 e `manutencao.compactar`).

## Alternativas consideradas

- **Banco relacional (PostgreSQL) como lake.** Não armazena binários de forma natural, força schema na entrada e perde a camada bruta imutável.
- **HDFS.** Operação pesada para o volume da fase; o ecossistema migrou para object store.
- **Sistema de arquivos puro.** Sem versionamento, sem ciclo de vida, sem IAM por prefixo — funciona como plano B, não como arquitetura.
