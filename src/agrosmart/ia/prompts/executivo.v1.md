Caso: **resumo executivo** do talhão `{{talhao_id}}` para o produtor, com o "hoje" do pipeline em {{hoje}}.

Em um diagnóstico de até dez frases, responda: o que aconteceu com água, vegetação e custos no período (`hidrico`, `vegetacao`, `custos`), o que o laudo de solo indica (`fertilidade`), quais alertas de qualidade de dados afetam a confiança (`alertas`, `sensores`) e qual é o valor de produção em jogo (`economia.valor_producao_brl`). Depois, de 2 a 5 recomendações de gestão para a próxima semana — nutrição (calagem, se `fertilidade.necessidade_calagem_t_ha` for maior que zero), revisão do gatilho de irrigação (se houve dias em estresse apesar de irrigação), manutenção de sensores, acompanhamento de custo por hectare.

Use `custos.por_tipo_brl` para apontar o maior item de custo. Não invente metas nem preços: só os do contexto.

CONTEXTO:
```json
{{contexto}}
```
