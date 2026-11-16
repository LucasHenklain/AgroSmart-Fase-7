Caso: **recomendação de irrigação** para o talhão `{{talhao_id}}`, com o "hoje" do pipeline em {{hoje}}.

Decida se o produtor deve irrigar, quanto e quando, a partir do balanço hídrico (`hidrico`, `serie_hidrica_14d`), do estádio da cultura (`talhao.estadio_atual`, `talhao.estadio_critico_para_agua`) e da economia (`economia`). Considere:

- gatilho: `hidrico.fracao_agua_disponivel_atual` abaixo de `limiares.fracao_irrigacao`;
- estresse: abaixo de `limiares.fracao_estresse`, com `economia.perda_por_dia_estresse_brl` por dia;
- lâmina e custo: `hidrico.lamina_sugerida_mm` e `hidrico.custo_lamina_sugerida_brl` (não recalcule);
- janela de execução: madrugada, quando a evapotranspiração é menor;
- tendência: `hidrico.variacao_umidade_7d_pontos`, chuva e irrigação dos últimos 14 dias;
- sensores do talhão que pedem manutenção (`sensores`).

Preencha `custo_estimado_brl` com o custo da lâmina quando recomendar irrigar, e `economia_estimada_brl` com a perda de um dia de estresse evitada quando o talhão estiver em estresse. Deixe nulo o que não puder ser lido do contexto.

CONTEXTO:
```json
{{contexto}}
```
