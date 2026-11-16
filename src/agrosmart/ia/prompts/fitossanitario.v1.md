Caso: **risco fitossanitário** do talhão `{{talhao_id}}`, com o "hoje" do pipeline em {{hoje}}.

Avalie o risco de doença foliar e a necessidade de ação a partir do clima recente (`clima_recente`), da vegetação (`vegetacao`, `cenas_ndvi_30d`) e do histórico de pulverização (`custos.dias_desde_pulverizacao`). Considere:

- janela climática favorável a fungos: `clima_recente.dias_ur_alta_7d` maior ou igual a `limiares.dias_ur_alta_risco` com `clima_recente.temperatura_favoravel_a_fungos` verdadeiro;
- queda de vigor: `vegetacao.variacao_30d` negativa, respeitando `vegetacao.confianca_ultimo` (uma cena com nuvem alta não sustenta diagnóstico);
- intervalo mínimo entre pulverizações: `limiares.intervalo_min_pulverizacao_dias`;
- custo da pulverização preventiva: `economia.custo_fungicida_talhao_brl`.

Quando não houver risco, diga isso e recomende o monitoramento adequado. Preencha `custo_estimado_brl` só quando recomendar pulverizar.

CONTEXTO:
```json
{{contexto}}
```
