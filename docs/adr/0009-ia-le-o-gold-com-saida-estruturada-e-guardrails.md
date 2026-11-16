# ADR 0009 — O copiloto lê o gold via Context Pack, responde em JSON estruturado e passa por guardrails com fallback determinístico

**Status:** aceito · **Data:** 2026-09-03 · **Decisores:** E3, E1

## Contexto

IA generativa aplicada a decisão agronômica falha de duas maneiras previsíveis: inventa números (limiar, custo, produtividade "de memória") e recomenda o que não pode ser feito (irrigar um talhão de sequeiro). Um chatbot com acesso livre ao lake não resolve nenhuma das duas — piora a primeira. E sem uma saída comparável não há como medir o que o modelo acrescenta sobre uma regra simples.

## Decisão

1. **Context Pack** (`ia/contexto.py`): o modelo só vê um JSON montado do gold para o talhão — balanço hídrico de 14 dias, NDVI de 30 dias, clima de 7 dias, custos, laudo, sensores, alertas de qualidade — mais os **limiares** (`agronomia.py`, os mesmos do gold) e os **parâmetros econômicos**. Quantidades que o modelo poderia querer calcular (lâmina, custo da lâmina, perda por dia de estresse, valor da produção) já vêm calculadas. O pacote é o universo do citável.
2. **Saída estruturada** (`ia/esquema.py`): a resposta é uma chamada de ferramenta cujo `input_schema` é o JSON Schema de `SaidaCopiloto` — diagnóstico, recomendações tipadas com prioridade, custo e economia, confiança com justificativa, limitações e `dados_usados`. Nunca prosa livre.
3. **Guardrails** (`ia/guardrails.py`) aplicados a **qualquer** motor: identidade (talhão/caso), trava agronômica (irrigar sem sistema, ou com solo úmido → rejeição), aterramento numérico (número fora do pacote → anotado; muitos → rejeição), confiança compatível com a qualidade dos dados, `dados_usados` que existem no pacote.
4. **Motor de regras** (`ia/regras.py`) com os mesmos limiares: é o fallback quando a API falha ou a saída é rejeitada, e é o **baseline** contra o qual a avaliação mede o modelo (`ia/avaliacao.py`).
5. **Prompts versionados** em arquivos (`ia/prompts/*.vN.md`); cada relatório registra a versão que o produziu.

## Consequências

- Nenhum número sai do copiloto sem existir no lake — e isso é verificado por código, não pedido no prompt (o prompt também pede; o guardrail garante).
- A demonstração funciona sem chave de API: o motor de regras produz a mesma estrutura, marcado como tal. Com chave, o relatório de avaliação mostra concordância e violações do modelo em número.
- Cada saída carrega proveniência completa: motor, fallback e motivo, versões dos prompts, tokens, ajustes dos guardrails, Context Pack anexado.
- Custo: o Context Pack é grande (~15–25 KB por talhão) e o modelo não pode "explorar" além dele. É a troca certa para apoio à decisão: menos criatividade, zero alucinação numérica.
- Limite conhecido: o aterramento por lista de números tolera coincidências (um número inventado que por acaso existe no pacote). A mitigação é `dados_usados` por recomendação, que exige apontar *de onde* veio.

## Alternativas consideradas

- **RAG sobre documentos/tabelas com consulta livre pelo modelo (text-to-SQL).** Flexível, mas o modelo decide o que ler e pode errar a consulta; a auditoria fica difícil. Pode entrar como camada adicional para perguntas em linguagem natural, nunca para recomendações.
- **Prosa livre com pós-processamento por regex.** Frágil e impossível de comparar entre motores.
- **Só motor de regras, sem IA.** Cumpre a trava, mas não produz o diagnóstico em linguagem natural nem combina sinais fora das regras — que é o valor pedido pela Entrega 3. A avaliação existe exatamente para medir esse valor.
