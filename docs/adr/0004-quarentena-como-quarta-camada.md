# ADR 0004 — Quarentena como quarta camada, ao lado de bruta, confiável e refinada

**Status:** aceito · **Data:** 2026-09-02 · **Decisores:** E1, E2

## Contexto

O enunciado pede três camadas (bruta, confiável, refinada). Um registro que falha em uma regra de qualidade crítica não pode ir para a confiável — mas descartá-lo em silêncio destrói a evidência do problema e impossibilita a reconciliação (declarados = aprovados + reprovados).

## Decisão

Criar uma quarta camada, **quarentena** (`agrosmart-quarentena`), com dois usos:

1. `_rejeitados/<fonte>/<batch_id>/` — lotes inteiros recusados **na porta** (schema divergente do contrato), com o arquivo original e um `_motivo.json`.
2. `<fonte>/ano=/mes=/dia=/` — registros individuais reprovados pelo motor de qualidade, em Parquet, com `_regras_violadas` (lista de ids) e `_motivo` legível, particionados pela data de ingestão (a data em que entraram na quarentena, da qual a retenção conta — não pela data do evento, que pode ser antiga).

Versionamento ligado; retenção de 30 dias (regra de ciclo de vida do bucket e `manutencao.aplicar_retencao`).

## Consequências

- **Nada é descartado em silêncio**: `lidos = aprovados + quarentena + substituídos` fecha em toda fonte, e o relatório mostra o que caiu e por quê.
- A quarentena é a **verdade-terreno** que valida o motor: os defeitos injetados pela simulação têm de aparecer nela (`dq_report.json → defeitos_injetados`).
- O runbook ganha um lugar para investigar: INC-02 (schema) e INC-04 (quarentena crescendo) apontam para prefixos concretos.
- Custo: mais um bucket e uma retenção a operar; irrelevante frente ao ganho de auditabilidade.

## Alternativas consideradas

- **Coluna de flag no silver.** Contamina a camada confiável com dado reprovado; todo consumidor teria de filtrar.
- **Descartar e logar.** Perde o registro; impossibilita reprocessar quando a regra for corrigida.
- **Devolver para o bronze.** O bronze é imutável e já tem o registro; a quarentena guarda o *veredito*, não o dado.
