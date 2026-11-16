# ADR 0005 — Um contrato YAML por fonte como fonte única da verdade

**Status:** aceito · **Data:** 2026-09-02 · **Decisores:** E1, E2, E3

## Contexto

Schema, regras de qualidade, particionamento, retenção e documentação de uma fonte tendem a viver em lugares diferentes — código do coletor, código do processamento, planilha do dicionário, slide da arquitetura — e a divergir em semanas.

## Decisão

Cada fonte tem exatamente um arquivo `contratos/<fonte>.<versão>.yaml` com: identidade e classificação LGPD; modo de ingestão, janela e SLA; particionamento e chave natural; estratégia de deduplicação; destino no silver e no gold; retenção por camada; campos (tipo, unidade, obrigatoriedade, marcação LGPD e tratamento); e regras de qualidade com **justificativa obrigatória**.

O contrato é carregado por Pydantic com validação cruzada e rejeita inconsistências internas (regra citando campo inexistente, referência a tabela que nenhum contrato produz, id de regra duplicado). Coletor, tipagem, motor de qualidade, dedup, LGPD, retenção, catálogo e dicionário são todos **derivados** dele.

## Consequências

- Uma mudança de schema é feita em um lugar e propaga; o dicionário de dados é gerado e nunca fica defasado.
- A regra precisa dizer **por que existe** — o campo `justificativa` transforma o YAML em documentação de decisão agronômica e de negócio.
- Versionamento explícito: a `v2` de uma fonte é um arquivo novo; cada registro do lake carrega `_schema_version`.
- Custo: disciplina. Nada de "só um ajuste rápido no coletor" — o ajuste é no contrato, e os testes de contrato (`tests/test_contratos.py`) impedem contratos incoerentes de subir.

## Alternativas consideradas

- **Schema em código (dataclasses/Pydantic direto).** Menos acessível à equipe de negócio e ao avaliador; o YAML é legível por quem não programa.
- **Great Expectations / dbt tests.** Ferramentas excelentes, mas separariam regras do schema e adicionariam dependências pesadas para o mesmo resultado.
- **Sem contrato, com validação ad hoc.** É o que produz o pântano de dados que a camada confiável existe para evitar.
