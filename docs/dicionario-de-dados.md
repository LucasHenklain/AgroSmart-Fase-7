# Dicionário de dados

> **Arquivo gerado** por `agrosmart catalogo` a partir de `contratos/*.yaml` e das
> definições do gold em `src/agrosmart/processamento/gold.py`. Não edite à mão:
> altere o contrato ou a definição e regenere.

Gerado em 2026-09-05T23:04:31+00:00 a partir do catálogo de 2026-09-05T23:04:31+00:00.

## Camadas

| Camada | Propósito |
|---|---|
| **bronze** (Bruta) | Preserva o dado exatamente como chegou. Imutável, append-only, particionado por fonte e data. É a única fonte da verdade para reprocessar o lake inteiro quando uma regra muda. |
| **silver** (Confiável) | Dado validado, tipado, deduplicado e com unidades no SI. Aqui o lake deixa de ser um pântano: nada entra sem passar pelas regras do contrato da fonte. |
| **gold** (Refinada) | Modelo dimensional e indicadores prontos para consumo. Serve dashboard, API e o Context Pack do copiloto de IA a partir do mesmo conjunto de tabelas. |
| **quarentena** (Quarentena) | Registros reprovados na validação, guardados com o motivo da reprovação. Nada é descartado em silêncio — dado ruim é evidência de problema na origem. |

---

# Silver — uma tabela por fonte

## erp_talhoes — Cadastro de talhões, cultura e parâmetros de solo

Contrato `erp_talhoes.v1` · tipo **estruturado** · LGPD **pessoal** · ingestão `batch` (diária (02h30, carga completa)) · SLA 1440 min · dedup `versao` · chave `talhao_id` · partição por `extraido_em`

Extração diária completa (full load) da tabela de talhões do ERP do produtor. Muda pouco, mas muda: replantio, mudança de variedade e correção de área são eventos reais que não podem ser tratados como imutáveis.

No lake: **6** registros em 1 arquivo(s), 1 lotes do bronze, eventos de 2026-09-01 02:30:00+00:00 a 2026-09-01 02:30:00+00:00.

| Campo | Tipo | Unidade | Obrig. | LGPD | Descrição |
|---|---|---|---|---|---|
| `talhao_id` | string |  | ● |  | Chave primária do talhão. Conformada em todo o lake. |
| `fazenda_id` | string |  | ● |  | Fazenda a que o talhão pertence. |
| `nome` | string |  | ● | identificador | Nome usado pelo produtor no dia a dia. |
| `area_ha` | float | ha | ● |  | Área produtiva do talhão. Denominador de quase todo indicador. |
| `cultura` | string |  | ● |  | Cultura implantada na safra corrente. |
| `variedade` | string |  |  |  | Cultivar ou híbrido plantado. |
| `data_plantio` | date |  | ● |  | Data de semeadura. Base do cálculo do estádio fenológico. |
| `data_colheita_prevista` | date |  |  |  | Previsão de colheita informada pelo produtor. |
| `latitude` | float | graus | ● | localizacao | Latitude do centroide do talhão (WGS-84). |
| `longitude` | float | graus | ● | localizacao | Longitude do centroide do talhão (WGS-84). |
| `tipo_solo` | string |  |  |  | Classe textural predominante. |
| `sistema_irrigacao` | string |  | ● |  | Sistema disponível. Define se a recomendação de irrigação é executável. |
| `capacidade_campo_pct` | float | % |  |  | Umidade na capacidade de campo. Limite superior do balanço hídrico. |
| `ponto_murcha_pct` | float | % |  |  | Umidade no ponto de murcha permanente. Limite inferior crítico. |
| `extraido_em` | timestamp | UTC | ● |  | Momento da extração no ERP. Define a partição do snapshot. |
| `_batch_id` | proveniência | | | | lote do bronze que originou o registro |
| `_ingested_at` | proveniência | | | | instante da ingestão no bronze |
| `_schema_version` | proveniência | | | | contrato (fonte.versão) que validou o registro |
| `_alertas` | proveniência | | | | ids das regras de alerta acionadas pelo registro |

**Regras de qualidade**

| Id | Regra | Severidade | Campos / parâmetros | Justificativa |
|---|---|---|---|---|
| TLH-01 | nao_nulo | critica | talhao_id, fazenda_id, area_ha, cultura, data_plantio, latitude, longitude | São os campos que sustentam todas as junções e agregações do gold. |
| TLH-02 | unicidade | critica | talhao_id | Talhão duplicado multiplica área e distorce todo custo por hectare. |
| TLH-03 | intervalo | critica | area_ha · min=0.1, max=5000.0 | Área zero ou negativa gera divisão por zero nos indicadores. |
| TLH-04 | intervalo | critica | latitude · min=-34.0, max=6.0 | Fora do território brasileiro é erro de digitação ou troca lat/lon. |
| TLH-05 | intervalo | critica | longitude · min=-74.5, max=-33.0 | Idem - a troca de latitude por longitude é o erro mais comum aqui. |
| TLH-06 | dominio | alerta | cultura · valores=['milho', 'soja', 'cafe', 'cana', 'algodao', 'feijao', 'trigo'] | Cultura nova é possível, mas exige limiares agronômicos que a IA ainda não tem. O alerta força a revisão do prompt antes de recomendar. |
| TLH-07 | dominio | critica | sistema_irrigacao · valores=['pivo_central', 'gotejamento', 'aspersao', 'sulco', 'sequeiro'] | Recomendar irrigação para talhão de sequeiro é o pior erro possível do copiloto. Este domínio é a trava agronômica dessa recomendação. |
| TLH-08 | comparacao | critica | data_plantio <= data_colheita_prevista | Ordem invertida quebra o cálculo do ciclo da cultura. |
| TLH-09 | comparacao | critica | ponto_murcha_pct <= capacidade_campo_pct | Inversão torna o balanço hídrico matematicamente impossível. |
| TLH-10 | nao_futuro | alerta | data_plantio · tolerancia_min=0 | Plantio programado é legítimo, mas não deve entrar na safra corrente. |

## clima_diario — Série meteorológica diária por estação

Contrato `clima.v1` · tipo **semiestruturado** · LGPD **publico** · ingestão `api_pull` (diária (03h00, janela D-1 fechada)) · SLA 360 min · dedup `evento` · chave `estacao_id, data` · partição por `data`

Observações diárias consolidadas obtidas por API pública de meteorologia, associadas à estação mais próxima de cada fazenda. Fornece a precipitação e a evapotranspiração de referência (ETo) que fecham o balanço hídrico - sem esta fonte, a umidade do solo sozinha não diz se irrigar ou esperar a chuva.

No lake: **57** registros em 30 arquivo(s), 57 lotes do bronze, eventos de 2026-08-02 a 2026-08-31.

| Campo | Tipo | Unidade | Obrig. | LGPD | Descrição |
|---|---|---|---|---|---|
| `estacao_id` | string |  | ● |  | Identificador da estação meteorológica de referência. |
| `fazenda_id` | string |  | ● |  | Fazenda atendida por esta estação. |
| `data` | date |  | ● |  | Dia de referência da observação. |
| `temp_min_c` | float | °C | ● |  | Temperatura mínima do dia. |
| `temp_max_c` | float | °C | ● |  | Temperatura máxima do dia. |
| `temp_media_c` | float | °C | ● |  | Média das temperaturas horárias. |
| `umidade_relativa_pct` | float | % | ● |  | Umidade relativa média do ar. |
| `precipitacao_mm` | float | mm | ● |  | Chuva acumulada em 24 h. |
| `velocidade_vento_ms` | float | m/s |  |  | Vento médio a 2 m. Entra no cálculo de ETo e na janela de pulverização. |
| `radiacao_mj_m2` | float | MJ/m² |  |  | Radiação solar global acumulada no dia. |
| `eto_mm` | float | mm | ● |  | Evapotranspiração de referência (Penman-Monteith, FAO-56). |
| `origem_api` | string |  | ● |  | Provedor da observação, para rastreabilidade e troca de fornecedor. |
| `_batch_id` | proveniência | | | | lote do bronze que originou o registro |
| `_ingested_at` | proveniência | | | | instante da ingestão no bronze |
| `_schema_version` | proveniência | | | | contrato (fonte.versão) que validou o registro |
| `_alertas` | proveniência | | | | ids das regras de alerta acionadas pelo registro |

**Regras de qualidade**

| Id | Regra | Severidade | Campos / parâmetros | Justificativa |
|---|---|---|---|---|
| CLI-01 | nao_nulo | critica | estacao_id, fazenda_id, data, temp_media_c, precipitacao_mm, eto_mm | Campos sem os quais o balanço hídrico não fecha. |
| CLI-02 | unicidade | critica | estacao_id, data | Reexecução do pull diário não pode duplicar o dia. |
| CLI-03 | comparacao | critica | temp_min_c <= temp_max_c | Inversão indica troca de colunas no parser da API - erro silencioso que só aparece semanas depois, no gráfico errado do dashboard. |
| CLI-04 | intervalo | critica | precipitacao_mm · min=0.0, max=500.0 | 500 mm em 24 h já é recorde nacional; acima disso é erro de unidade. |
| CLI-05 | intervalo | critica | umidade_relativa_pct · min=0.0, max=100.0 | Faixa física da grandeza. |
| CLI-06 | intervalo | alerta | eto_mm · min=0.0, max=15.0 | ETo acima de 15 mm/dia é implausível no Brasil; revisar o cálculo. |
| CLI-07 | nao_futuro | alerta | data · tolerancia_min=0 | Alerta, não crítica: a mesma API entrega previsão futura, que é dado legítimo em outra tabela. Aqui o alerta sinaliza mistura de observado com previsto. |
| CLI-08 | freshness | alerta | max_atraso_min=360, detectar_lacunas=True | Sem o clima de ontem a recomendação de hoje perde metade do contexto. A série é diária: o atraso conta a partir do fim do dia observado, e um dia faltando entre dois observados é lacuna. |

## erp_operacoes — Operações executadas no talhão com insumo e custo

Contrato `erp_operacoes.v1` · tipo **estruturado** · LGPD **pessoal** · ingestão `batch` (diária (02h45, incremental por data_lancamento)) · SLA 1440 min · dedup `versao` · chave `operacao_id` · partição por `data`

Lançamentos incrementais de tudo que acontece no campo: irrigação, adubação, pulverização, plantio, colheita e manutenção. Carga incremental diária pela data de lançamento, com correções retroativas permitidas em até 7 dias.

No lake: **40** registros em 25 arquivo(s), 24 lotes do bronze, eventos de 2026-08-02 a 2026-08-30.

| Campo | Tipo | Unidade | Obrig. | LGPD | Descrição |
|---|---|---|---|---|---|
| `operacao_id` | string |  | ● |  | Chave primária do lançamento no ERP. |
| `talhao_id` | string |  | ● |  | Talhão onde a operação foi executada. |
| `data` | date |  | ● |  | Data de execução da operação no campo. |
| `tipo_operacao` | string |  | ● |  | Natureza da operação. |
| `insumo` | string |  |  |  | Insumo aplicado, quando houver. |
| `quantidade` | float |  | ● |  | Quantidade aplicada ou volume da operação. |
| `unidade` | string |  | ● |  | Unidade da quantidade. Normalizada para o SI no silver. |
| `custo_brl` | float | BRL | ● |  | Custo total da operação, já com insumo, energia e mão de obra. |
| `responsavel_id` | string |  |  | pessoal → pseudonimizar | Operador responsável. DADO PESSOAL - pseudonimizado no silver e nunca exposto na camada gold nem em qualquer produto de dados agregado.
 |
| `observacao` | string |  |  |  | Texto livre digitado no campo. Fonte não estruturada secundária. |
| `extraido_em` | timestamp | UTC | ● |  | Momento da extração no ERP. |
| `_batch_id` | proveniência | | | | lote do bronze que originou o registro |
| `_ingested_at` | proveniência | | | | instante da ingestão no bronze |
| `_schema_version` | proveniência | | | | contrato (fonte.versão) que validou o registro |
| `_alertas` | proveniência | | | | ids das regras de alerta acionadas pelo registro |

**Regras de qualidade**

| Id | Regra | Severidade | Campos / parâmetros | Justificativa |
|---|---|---|---|---|
| OPR-01 | nao_nulo | critica | operacao_id, talhao_id, data, tipo_operacao, quantidade, unidade, custo_brl | Sem estes campos o lançamento não entra em nenhum indicador financeiro. |
| OPR-02 | unicidade | critica | operacao_id | A janela de reingestão de 7 dias reenvia lançamentos já processados. Sem esta regra, o custo por hectare é contado várias vezes. |
| OPR-03 | dominio | critica | tipo_operacao · valores=['plantio', 'irrigacao', 'adubacao', 'pulverizacao', 'colheita', 'manutencao', 'amostragem', 'monitoramento'] | Tipo desconhecido não tem regra de agregação definida no gold. |
| OPR-04 | dominio | critica | unidade · valores=['mm', 'L', 'm3', 'kg', 't', 'ha', 'h', 'un'] | Unidade fora do domínio impede a conversão para o SI. Misturar litro com milímetro em irrigação é erro de ordem de grandeza, não de arredondamento. |
| OPR-05 | intervalo | critica | quantidade · min=0.0 | Quantidade negativa deve virar estorno explícito, não valor negativo. |
| OPR-06 | intervalo | critica | custo_brl · min=0.0, max=25000.0 | Teto detecta erro de centavos versus reais na exportação do ERP. A maior operação plausível nestes talhões (adubação de 400 kg/ha em 41 ha) fica abaixo de R$ 20 mil; acima de R$ 25 mil é erro de unidade monetária ou lançamento consolidado indevido. Limitação conhecida: um teto por registro não captura o erro de centavos em operações pequenas — isso exige a regra de custo por hectare, que depende do cadastro e vive no gold. |
| OPR-07 | referencia | critica | talhao_id · tabela_referencia=erp_talhoes, campo_referencia=talhao_id | Custo lançado em talhão inexistente some do rateio. |
| OPR-08 | nao_futuro | critica | data · tolerancia_min=0, referencia=lote | Operação futura é planejamento, e planejamento não é custo realizado. |
| OPR-09 | freshness | alerta | max_atraso_min=2880, detectar_lacunas=False | Custo defasado distorce o relatório executivo semanal. Um dia sem operação lançada é normal na fazenda; dois dias seguidos sem nenhum lançamento é sinal de exportação do ERP parada — daí o limite de 48 h, avaliado sobre a tabela inteira (uma operação não é uma série). |

## imagens_ndvi_meta — Imagens de drone e satélite com índice NDVI por talhão

Contrato `imagens_ndvi.v1` · tipo **nao_estruturado** · LGPD **pessoal** · ingestão `watch_folder` (sob demanda (após cada voo ou passagem)) · SLA 10080 min · dedup `evento` · chave `imagem_id` · partição por `capturada_em`

Voos de drone (semanais) e passagens de satélite (a cada 5 dias) geram ortomosaicos por talhão. O NDVI derivado é o indicador de vigor da lavoura: uma queda sustentada antecede em dias o que o produtor veria a olho nu.

No lake: **59** registros em 9 arquivo(s), 59 lotes do bronze, eventos de 2026-08-03 12:53:00+00:00 a 2026-08-28 13:18:00+00:00.

| Campo | Tipo | Unidade | Obrig. | LGPD | Descrição |
|---|---|---|---|---|---|
| `imagem_id` | string |  | ● |  | Identificador do produto de imagem gerado pelo voo/passagem. |
| `talhao_id` | string |  | ● |  | Talhão coberto pela imagem. Chave conformada com o ERP. |
| `capturada_em` | timestamp | UTC | ● |  | Instante da captura. |
| `plataforma` | string |  | ● |  | Origem da imagem. |
| `arquivo_bruto` | string |  | ● |  | Chave do binário na camada bruta. Referência, não cópia. |
| `checksum_sha256` | string |  | ● |  | Impressão digital do binário. Base da idempotência e da auditoria. |
| `tamanho_bytes` | int | bytes | ● |  | Tamanho do arquivo original. |
| `largura_px` | int | px |  |  | Largura do ortomosaico. |
| `altura_px` | int | px |  |  | Altura do ortomosaico. |
| `altitude_m` | float | m |  |  | Altitude de voo. Determina a resolução do produto. |
| `resolucao_cm_px` | float | cm/px |  |  | Tamanho do pixel no terreno (GSD). |
| `nuvem_pct` | float | % | ● |  | Cobertura de nuvem na cena. Acima de 30% o NDVI é pouco confiável. |
| `ndvi_medio` | float | índice | ● |  | NDVI médio da área útil do talhão. |
| `ndvi_min` | float | índice |  |  | Menor NDVI observado - localiza a falha mais severa. |
| `ndvi_max` | float | índice |  |  | Maior NDVI observado. |
| `ndvi_desvio` | float | índice |  |  | Desvio padrão do NDVI. Alto = lavoura desuniforme. |
| `area_analisada_ha` | float | ha | ● |  | Área efetivamente coberta e analisada. |
| `_batch_id` | proveniência | | | | lote do bronze que originou o registro |
| `_ingested_at` | proveniência | | | | instante da ingestão no bronze |
| `_schema_version` | proveniência | | | | contrato (fonte.versão) que validou o registro |
| `_alertas` | proveniência | | | | ids das regras de alerta acionadas pelo registro |

**Regras de qualidade**

| Id | Regra | Severidade | Campos / parâmetros | Justificativa |
|---|---|---|---|---|
| IMG-01 | nao_nulo | critica | imagem_id, talhao_id, capturada_em, arquivo_bruto, checksum_sha256, ndvi_medio | Metadado sem ponteiro para o binário é registro órfão. |
| IMG-02 | unicidade | critica | imagem_id | Reprocessar a pasta observada não pode duplicar a cena. |
| IMG-03 | intervalo | critica | ndvi_medio, ndvi_min, ndvi_max · min=-1.0, max=1.0 | NDVI é normalizado por definição; fora da faixa é erro de cálculo. |
| IMG-04 | comparacao | critica | ndvi_min <= ndvi_max | Inconsistência interna da estatística da cena. |
| IMG-05 | dominio | critica | plataforma · valores=['drone', 'satelite'] | Plataforma desconhecida impede comparar séries de resoluções diferentes. |
| IMG-06 | intervalo | critica | nuvem_pct · min=0.0, max=100.0 | Faixa física da grandeza. |
| IMG-07 | intervalo | alerta | nuvem_pct · max=30.0 | Cena muito nublada não é descartada - é marcada. O copiloto de IA precisa saber que aquele NDVI tem confiança baixa antes de recomendar qualquer coisa. |
| IMG-08 | referencia | critica | talhao_id · tabela_referencia=erp_talhoes, campo_referencia=talhao_id | Imagem de talhão inexistente é erro de rotulagem do voo. |
| IMG-09 | nao_futuro | critica | capturada_em · tolerancia_min=60 | Data de captura no futuro indica EXIF corrompido. |
| IMG-10 | intervalo | alerta | tamanho_bytes · min=2048.0 | Arquivo minúsculo geralmente é upload truncado. Um JPEG com menos de 2 KB não contém um ortomosaico de talhão — nem no menor produto de satélite; o alerta pede reenvio antes que o NDVI da cena seja usado. |

## laudos_solo — Laudos laboratoriais de fertilidade do solo

Contrato `laudos_solo.v1` · tipo **nao_estruturado** · LGPD **pessoal** · ingestão `watch_folder` (sob demanda (1 a 2 vezes por safra)) · SLA 43200 min · dedup `evento` · chave `laudo_id` · partição por `coletado_em`

Análises químicas de solo emitidas por laboratório credenciado, uma a duas vezes por safra por talhão. Definem a recomendação de calagem e adubação - são o dado de menor volume e maior valor por registro do lake inteiro.

No lake: **6** registros em 5 arquivo(s), 6 lotes do bronze, eventos de 2026-07-21 a 2026-07-26.

| Campo | Tipo | Unidade | Obrig. | LGPD | Descrição |
|---|---|---|---|---|---|
| `laudo_id` | string |  | ● |  | Número do laudo emitido pelo laboratório. |
| `talhao_id` | string |  | ● |  | Talhão amostrado. |
| `laboratorio` | string |  | ● |  | Laboratório emissor. Metodologias diferentes não são comparáveis. |
| `coletado_em` | date |  | ● |  | Data da coleta da amostra no campo. |
| `emitido_em` | date |  | ● |  | Data de emissão do laudo pelo laboratório. |
| `profundidade_cm` | string |  | ● |  | Faixa de profundidade amostrada. |
| `arquivo_bruto` | string |  | ● |  | Chave do PDF na camada bruta. |
| `checksum_sha256` | string |  | ● |  | Impressão digital do PDF. Prova de não adulteração. |
| `ph_cacl2` | float | pH | ● |  | pH em cloreto de cálcio, padrão do sistema IAC. |
| `materia_organica_g_dm3` | float | g/dm³ | ● |  | Teor de matéria orgânica. |
| `fosforo_mg_dm3` | float | mg/dm³ | ● |  | Fósforo disponível (resina). |
| `potassio_mmolc_dm3` | float | mmolc/dm³ | ● |  | Potássio trocável. |
| `calcio_mmolc_dm3` | float | mmolc/dm³ |  |  | Cálcio trocável. |
| `magnesio_mmolc_dm3` | float | mmolc/dm³ |  |  | Magnésio trocável. |
| `ctc_mmolc_dm3` | float | mmolc/dm³ | ● |  | Capacidade de troca catiônica a pH 7. |
| `saturacao_bases_pct` | float | % | ● |  | Saturação por bases (V%). Dispara a recomendação de calagem. |
| `extrator_versao` | string |  | ● |  | Versão do extrator que leu o PDF. Torna a extração reproduzível. |
| `_batch_id` | proveniência | | | | lote do bronze que originou o registro |
| `_ingested_at` | proveniência | | | | instante da ingestão no bronze |
| `_schema_version` | proveniência | | | | contrato (fonte.versão) que validou o registro |
| `_alertas` | proveniência | | | | ids das regras de alerta acionadas pelo registro |

**Regras de qualidade**

| Id | Regra | Severidade | Campos / parâmetros | Justificativa |
|---|---|---|---|---|
| LAU-01 | nao_nulo | critica | laudo_id, talhao_id, coletado_em, arquivo_bruto, checksum_sha256, ph_cacl2, saturacao_bases_pct | Laudo sem ponteiro para o PDF perde o valor probatório. |
| LAU-02 | unicidade | critica | laudo_id | Reprocessar a pasta observada não pode duplicar o laudo. |
| LAU-03 | intervalo | critica | ph_cacl2 · min=3.0, max=9.0 | Fora da faixa é erro de leitura do extrator, não solo real. |
| LAU-04 | intervalo | critica | saturacao_bases_pct · min=0.0, max=100.0 | V% é percentual por definição. |
| LAU-05 | intervalo | critica | materia_organica_g_dm3, fosforo_mg_dm3, potassio_mmolc_dm3, ctc_mmolc_dm3 · min=0.0 | Teor negativo é impossível; indica campo mal recortado no PDF. |
| LAU-06 | comparacao | critica | coletado_em <= emitido_em | Laudo emitido antes da coleta é troca de campos na extração. |
| LAU-07 | referencia | critica | talhao_id · tabela_referencia=erp_talhoes, campo_referencia=talhao_id | Laudo de talhão inexistente é erro de rotulagem da amostra. |
| LAU-08 | nao_futuro | critica | coletado_em, emitido_em · tolerancia_min=0 | Data futura em documento legal invalida a auditoria. |
| LAU-09 | nao_nulo | alerta | extrator_versao | Sem a versão do extrator não é possível saber quais laudos precisam ser relidos quando o extrator melhora. |

## sensores_leitura — Leituras de sensores de solo e ar

Contrato `sensores.v1` · tipo **estruturado** · LGPD **interno** · ingestão `streaming` (15 minutos) · SLA 30 min · dedup `evento` · chave `sensor_id, medido_em` · partição por `medido_em`

Telemetria das estações de sensoriamento instaladas nos talhões. Cada dispositivo publica uma leitura consolidada a cada 15 minutos por MQTT. É a fonte de maior volume do lake e a base do balanço hídrico diário.

No lake: **34,040** registros em 31 arquivo(s), 2892 lotes do bronze, eventos de 2026-08-02 00:00:00+00:00 a 2026-09-01 02:45:00+00:00.

| Campo | Tipo | Unidade | Obrig. | LGPD | Descrição |
|---|---|---|---|---|---|
| `sensor_id` | string |  | ● |  | Identificador único do dispositivo de campo. |
| `talhao_id` | string |  | ● |  | Talhão onde o sensor está instalado. Chave conformada com o ERP. |
| `medido_em` | timestamp | UTC | ● |  | Instante da medição no relógio do dispositivo, normalizado para UTC. |
| `umidade_solo_pct` | float | % | ● |  | Umidade volumétrica do solo a 20 cm de profundidade. |
| `temperatura_solo_c` | float | °C | ● |  | Temperatura do solo a 20 cm. |
| `temperatura_ar_c` | float | °C |  |  | Temperatura do ar a 1,5 m do solo. |
| `umidade_ar_pct` | float | % |  |  | Umidade relativa do ar. Entra no cálculo de risco fitossanitário. |
| `condutividade_ds_m` | float | dS/m |  |  | Condutividade elétrica do solo, indicador indireto de salinidade. |
| `ph` | float | pH |  |  | pH do solo medido em água. |
| `bateria_pct` | float | % | ● |  | Carga da bateria do dispositivo. Aciona ordem de manutenção. |
| `rssi_dbm` | int | dBm |  |  | Intensidade do sinal de rádio no momento do envio. |
| `_batch_id` | proveniência | | | | lote do bronze que originou o registro |
| `_ingested_at` | proveniência | | | | instante da ingestão no bronze |
| `_schema_version` | proveniência | | | | contrato (fonte.versão) que validou o registro |
| `_alertas` | proveniência | | | | ids das regras de alerta acionadas pelo registro |

**Regras de qualidade**

| Id | Regra | Severidade | Campos / parâmetros | Justificativa |
|---|---|---|---|---|
| SEN-01 | nao_nulo | critica | sensor_id, talhao_id, medido_em, umidade_solo_pct, bateria_pct | Sem identificação, instante ou medida principal a leitura é inútil. |
| SEN-02 | unicidade | critica | sensor_id, medido_em | O buffer offline do gateway reenvia leituras já entregues. Sem esta regra o balanço hídrico conta a mesma medição duas vezes. |
| SEN-03 | regex | critica | sensor_id · padrao=^SNS-\d{4}$ | Detecta dispositivo não homologado publicando no tópico. |
| SEN-04 | intervalo | critica | umidade_solo_pct · min=0.0, max=100.0 | Fora da faixa física indica sensor descalibrado ou seco no ar. |
| SEN-05 | intervalo | critica | temperatura_solo_c · min=-10.0, max=70.0 | Faixa plausível para solo agrícola no Brasil. |
| SEN-06 | intervalo | critica | ph · min=3.0, max=10.0 | Fora disso o eletrodo está com falha, não é solo real. |
| SEN-07 | intervalo | alerta | condutividade_ds_m · min=0.0, max=10.0 | Valores altos podem ser salinidade real - alerta, não descarte. |
| SEN-08 | nao_futuro | critica | medido_em · tolerancia_min=5, referencia=lote | Relógio do dispositivo dessincronizado corrompe qualquer série temporal. A comparação é com o fim da janela do lote, não com o "agora" do pipeline: é assim que se detecta o relógio adiantado no momento em que a leitura chega. A tolerância de 5 min cobre a deriva normal de RTC sem NTP. |
| SEN-09 | referencia | critica | talhao_id · tabela_referencia=erp_talhoes, campo_referencia=talhao_id | Sensor apontando para talhão inexistente quebra toda agregação. |
| SEN-10 | intervalo | alerta | bateria_pct · min=15.0, max=100.0 | Abaixo de 15% o dispositivo ainda mede corretamente, mas vai calar em poucos dias. O alerta vira ordem de manutenção preventiva - é exatamente o tipo de insight que a IA transforma em recomendação acionável. |
| SEN-11 | intervalo | alerta | rssi_dbm · min=-95.0, max=0.0 | Sinal fraco antecipa perda de dados; entra no plano de rede. |
| SEN-12 | freshness | alerta | max_atraso_min=30, detectar_lacunas=True | Dado velho leva a recomendação de irrigação errada. Por sensor: atraso da última leitura em relação ao "agora" do pipeline, e lacunas na série — é o que detecta um dispositivo calado (INC-01) mesmo quando os outros do talhão continuam publicando. |

---

# Gold — modelo dimensional e indicadores

## dim_fenologia

**Grão:** um estádio por cultura (ou um mês, para perenes)

Estádios fenológicos com coeficiente de cultura Kc (FAO-56). Vem do conhecimento agronômico compartilhado, não de uma fonte ingerida.

No lake: **48** registros, 7 colunas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `cultura` | String | cultura no domínio do contrato erp_talhoes |
| `estadio` | String | nome do estádio fenológico |
| `dia_inicio` | Int64 | dias após o plantio em que o estádio começa (nulo para perenes) |
| `dia_fim` | Int64 | último dia após o plantio do estádio (9999 = até o fim do ciclo) |
| `mes` | Int64 | mês do ano que define o estádio (só perenes) |
| `kc` | Float64 | coeficiente de cultura para ETc = Kc × ETo |
| `perene` | Boolean | verdadeiro quando o estádio é definido pelo calendário |

## dim_talhao

**Grão:** um talhão

Cadastro vigente dos talhões: a versão mais recente e válida do ERP.

Fontes silver: `erp_talhoes`

No lake: **6** registros, 16 colunas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `talhao_id` | String | chave conformada usada em todo o lake |
| `fazenda_id` | String | fazenda a que pertence |
| `nome` | String | nome usado pelo produtor (identificador — não sai em agregados) |
| `area_ha` | Float64 | área produtiva em hectares |
| `cultura` | String | cultura da safra corrente |
| `variedade` | String | cultivar ou híbrido |
| `data_plantio` | Date | data de semeadura |
| `data_colheita_prevista` | Date | previsão do produtor |
| `latitude` | Float64 | centroide WGS-84 (localização — não sai em agregados) |
| `longitude` | Float64 | centroide WGS-84 (localização — não sai em agregados) |
| `tipo_solo` | String | classe textural |
| `sistema_irrigacao` | String | pivô, aspersão, gotejamento, sulco ou sequeiro |
| `capacidade_campo_pct` | Float64 | umidade na capacidade de campo |
| `ponto_murcha_pct` | Float64 | umidade no ponto de murcha permanente |
| `irrigado` | Boolean | verdadeiro quando o talhão tem sistema de irrigação |
| `cadastro_atualizado_em` | Datetime(time_unit='us', time_zone='UTC') | extração do ERP que originou a versão vigente |

## dim_fazenda

**Grão:** uma fazenda

Fazendas com sua estação meteorológica de referência e totais de área.

Fontes silver: `erp_talhoes`, `clima_diario`

No lake: **2** registros, 5 colunas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `fazenda_id` | String | identificador da fazenda |
| `estacao_id` | String | estação meteorológica que a atende |
| `qtd_talhoes` | Int64 | talhões cadastrados |
| `area_total_ha` | Float64 | soma das áreas dos talhões |
| `culturas` | String | culturas presentes, separadas por vírgula |

## dim_estacao

**Grão:** uma estação meteorológica

Estações observadas e a cobertura temporal de cada uma.

Fontes silver: `clima_diario`

No lake: **2** registros, 6 colunas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `estacao_id` | String | identificador da estação |
| `fazenda_id` | String | fazenda atendida |
| `dias_observados` | Int64 | dias com observação no silver |
| `primeiro_dia` | Date | primeira observação |
| `ultimo_dia` | Date | última observação |
| `chuva_total_mm` | Float64 | precipitação acumulada no período |

## dim_sensor

**Grão:** um sensor de campo

Inventário dos sensores a partir do que publicaram: cobertura, bateria e sinal.

Fontes silver: `sensores_leitura`

No lake: **12** registros, 12 colunas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `sensor_id` | String | identificador do dispositivo |
| `talhao_id` | String | talhão onde está instalado |
| `primeira_leitura` | Datetime(time_unit='us', time_zone='UTC') | primeira leitura aprovada |
| `ultima_leitura` | Datetime(time_unit='us', time_zone='UTC') | última leitura aprovada |
| `leituras` | Int64 | leituras aprovadas no período |
| `dias_com_leitura` | Int64 | dias distintos com pelo menos uma leitura |
| `bateria_ultima_pct` | Float64 | bateria na última leitura |
| `bateria_min_pct` | Float64 | menor bateria observada |
| `rssi_medio_dbm` | Float64 | intensidade média do sinal |
| `leituras_com_alerta` | Decimal(precision=38, scale=0) | leituras que acionaram alguma regra de alerta |
| `leituras_bateria_baixa` | Decimal(precision=38, scale=0) | leituras com bateria abaixo do limiar (SEN-10) |
| `leituras_sinal_fraco` | Decimal(precision=38, scale=0) | leituras com sinal abaixo do limiar (SEN-11) |

## fato_clima_diario

**Grão:** uma estação em um dia

Observação meteorológica diária, pronta para junção por fazenda.

Fontes silver: `clima_diario`

No lake: **57** registros, 13 colunas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `estacao_id` | String | estação |
| `fazenda_id` | String | fazenda atendida |
| `data` | Date | dia observado |
| `temp_min_c` | Float64 | temperatura mínima |
| `temp_max_c` | Float64 | temperatura máxima |
| `temp_media_c` | Float64 | temperatura média |
| `umidade_relativa_pct` | Float64 | umidade relativa média |
| `precipitacao_mm` | Float64 | chuva acumulada |
| `velocidade_vento_ms` | Float64 | vento médio |
| `radiacao_mj_m2` | Float64 | radiação solar global |
| `eto_mm` | Float64 | evapotranspiração de referência (FAO-56) |
| `dia_chuvoso` | Boolean | verdadeiro quando houve precipitação |
| `origem_api` | String | provedor da observação |

## fato_leitura_diaria_talhao

**Grão:** um talhão em um dia

Agregação diária das leituras de sensores por talhão, com cobertura e alertas.

Fontes silver: `sensores_leitura`

No lake: **186** registros, 18 colunas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `talhao_id` | String | talhão |
| `dia` | Date | dia das leituras |
| `n_leituras` | Int64 | leituras aprovadas no dia |
| `n_sensores_ativos` | Int64 | sensores que publicaram no dia |
| `umidade_media_pct` | Float64 | umidade volumétrica média do solo |
| `umidade_min_pct` | Float64 | mínima do dia |
| `umidade_max_pct` | Float64 | máxima do dia |
| `temp_solo_media_c` | Float64 | temperatura média do solo |
| `temp_ar_min_c` | Float64 | temperatura mínima do ar |
| `temp_ar_max_c` | Float64 | temperatura máxima do ar |
| `umidade_ar_media_pct` | Float64 | umidade relativa média do ar |
| `condutividade_media_ds_m` | Float64 | condutividade elétrica média |
| `ph_medio` | Float64 | pH médio |
| `bateria_min_pct` | Float64 | menor bateria entre os sensores do talhão |
| `rssi_medio_dbm` | Float64 | sinal médio |
| `leituras_esperadas` | Int64 | sensores instalados × 96 leituras/dia |
| `cobertura_pct` | Float64 | n_leituras / leituras_esperadas |
| `leituras_com_alerta` | Decimal(precision=38, scale=0) | leituras que acionaram regra de alerta |

## fato_balanco_hidrico

**Grão:** um talhão em um dia

Balanço hídrico diário: clima da fazenda, Kc do estádio, irrigação lançada e umidade medida, com fração de água disponível, déficit e sinal de irrigação. É a tabela que o copiloto lê para recomendar.

Fontes silver: `erp_talhoes`, `clima_diario`, `erp_operacoes`, `sensores_leitura`

No lake: **186** registros, 17 colunas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `talhao_id` | String | talhão |
| `dia` | Date | dia do balanço |
| `dap` | Int64 | dias após o plantio |
| `estadio` | String | estádio fenológico (dim_fenologia) |
| `kc` | Float64 | coeficiente de cultura |
| `precipitacao_mm` | Float64 | chuva do dia na estação da fazenda |
| `eto_mm` | Float64 | evapotranspiração de referência |
| `etc_mm` | Float64 | evapotranspiração da cultura = Kc × ETo |
| `irrigacao_mm` | Float64 | lâmina lançada no ERP para o dia |
| `umidade_media_pct` | Float64 | umidade média medida pelos sensores |
| `fracao_agua_disponivel` | Float64 | (θ − PMP) / (CC − PMP), 0 a 1 |
| `deficit_mm` | Float64 | lâmina para voltar à capacidade de campo na camada de 0–30 cm |
| `estresse_hidrico` | Boolean | fração de água disponível < 0.35 |
| `recomenda_irrigar` | Boolean | fração < 0.5 em talhão irrigado (trava: sequeiro nunca recomenda) |
| `dias_desde_irrigacao` | Int64 | dias desde a última lâmina lançada (nulo se nunca irrigou) |
| `irrigado` | Boolean | talhão possui sistema de irrigação |
| `clima_disponivel` | Boolean | falso quando o dia da estação está ausente ou em quarentena (chuva, ETo e ETc ficam nulos; a umidade medida continua válida) |

## fato_ndvi_talhao

**Grão:** uma cena (drone ou satélite) de um talhão

Índice de vegetação por cena, com confiança pela cobertura de nuvem e variação em relação à cena anterior da mesma plataforma.

Fontes silver: `imagens_ndvi_meta`

No lake: **59** registros, 15 colunas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `imagem_id` | String | identificador da cena |
| `talhao_id` | String | talhão |
| `dia` | Date | dia da captura |
| `capturada_em` | Datetime(time_unit='us', time_zone='UTC') | instante da captura |
| `plataforma` | String | drone ou satélite |
| `ndvi_medio` | Float64 | NDVI médio da área útil |
| `ndvi_min` | Float64 | menor NDVI |
| `ndvi_max` | Float64 | maior NDVI |
| `ndvi_desvio` | Float64 | desvio padrão (desuniformidade) |
| `nuvem_pct` | Float64 | cobertura de nuvem |
| `confianca` | String | alta (< 10 % nuvem), media (< 30 %) ou baixa |
| `variacao_vs_anterior` | Float64 | NDVI médio menos o da cena anterior da mesma plataforma |
| `area_analisada_ha` | Float64 | área efetivamente analisada |
| `arquivo_bruto` | String | chave do binário no bronze |
| `com_alerta_dq` | Boolean | a cena acionou alguma regra de alerta (ex.: nuvem alta) |

## fato_operacao_custo

**Grão:** uma operação de campo

Operações vigentes (última versão do ERP) com custo por hectare. O responsável já está pseudonimizado desde o silver.

Fontes silver: `erp_operacoes`, `erp_talhoes`

No lake: **40** registros, 11 colunas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `operacao_id` | String | lançamento do ERP |
| `talhao_id` | String | talhão |
| `data` | Date | dia da execução |
| `tipo_operacao` | String | natureza da operação |
| `insumo` | String | insumo aplicado |
| `quantidade` | Float64 | quantidade na unidade canônica |
| `unidade` | String | unidade canônica (mm, m3, kg, ha, h, un) |
| `custo_brl` | Float64 | custo total |
| `custo_brl_ha` | Float64 | custo dividido pela área do talhão |
| `responsavel_id` | String | operador (pseudônimo) |
| `observacao` | String | texto livre do campo |

## fato_custo_diario_talhao

**Grão:** um talhão, um dia, um tipo de operação

Custo agregado por dia e tipo, para o relatório executivo e o rateio por hectare.

Fontes silver: `erp_operacoes`, `erp_talhoes`

No lake: **40** registros, 6 colunas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `talhao_id` | String | talhão |
| `dia` | Date | dia |
| `tipo_operacao` | String | tipo |
| `operacoes` | Int64 | quantidade de lançamentos |
| `custo_brl` | Float64 | custo total |
| `custo_brl_ha` | Float64 | custo por hectare |

## fato_fertilidade_talhao

**Grão:** um laudo de solo

Resultados analíticos com interpretação: classe de saturação por bases e necessidade de calagem (método da saturação por bases, PRNT 100 %).

Fontes silver: `laudos_solo`, `erp_talhoes`

No lake: **6** registros, 17 colunas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `laudo_id` | String | laudo |
| `talhao_id` | String | talhão |
| `coletado_em` | Date | data da coleta |
| `emitido_em` | Date | data de emissão |
| `profundidade_cm` | String | faixa amostrada |
| `ph_cacl2` | Float64 | pH em CaCl2 |
| `materia_organica_g_dm3` | Float64 | matéria orgânica |
| `fosforo_mg_dm3` | Float64 | P resina |
| `potassio_mmolc_dm3` | Float64 | K trocável |
| `calcio_mmolc_dm3` | Float64 | Ca trocável |
| `magnesio_mmolc_dm3` | Float64 | Mg trocável |
| `ctc_mmolc_dm3` | Float64 | CTC a pH 7 |
| `saturacao_bases_pct` | Float64 | V % |
| `classe_saturacao` | String | baixa (< 50), media (< 70) ou alta |
| `necessidade_calagem_t_ha` | Float64 | (70 − V) × CTC / 1000 quando V < 70; senão 0 |
| `classe_fosforo` | String | baixo (< 12), medio (< 25) ou alto |
| `arquivo_bruto` | String | chave do PDF no bronze |

## benchmark_regional

**Grão:** uma cultura

Agregado por cultura para o produto de dados externo (degrau 3 do modelo de negócio). Não contém identificador de talhão, fazenda, pessoa nem coordenada; só é publicável quando o grupo tem pelo menos 3 talhões.

Fontes silver: `erp_talhoes`, `fato_balanco_hidrico`, `fato_ndvi_talhao`, `fato_operacao_custo`

No lake: **5** registros, 11 colunas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `cultura` | String | cultura |
| `n_talhoes` | Int64 | talhões no grupo |
| `area_total_ha` | Float64 | área agregada |
| `umidade_media_pct` | Float64 | umidade média do solo no período |
| `fad_media` | Float64 | fração de água disponível média |
| `dias_estresse_pct` | Float64 | percentual de dias-talhão em estresse hídrico |
| `irrigacao_mm_media_talhao` | Float64 | lâmina total média por talhão |
| `chuva_mm_media` | Float64 | chuva média acumulada nas fazendas do grupo |
| `ndvi_medio` | Float64 | NDVI médio das cenas com confiança alta ou média |
| `custo_brl_ha` | Float64 | custo total do período por hectare |
| `publicavel` | Boolean | n_talhoes ≥ 3 (k-anonimato) |

## fato_alerta_qualidade

**Grão:** um alerta de qualidade (por regra ou por entidade)

Alertas do último relatório de qualidade, prontos para o dashboard e para o Context Pack do copiloto: regras de alerta acionadas por fonte e alertas de frescor por entidade (sensor atrasado, lacuna na série).

No lake: **6** registros, 7 colunas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `fonte` | String | fonte de dados |
| `regra` | String | id da regra do contrato |
| `tipo` | String | registro (regra de alerta acionada) ou tabela (frescor) |
| `subtipo` | String | para frescor: atrasado, lacuna ou sem_dados |
| `entidade` | String | sensor, estação ou talhão afetado (nulo para o total da fonte) |
| `quantidade` | Int64 | registros afetados ou ocorrências |
| `detalhe` | String | JSON com os campos específicos do alerta |
