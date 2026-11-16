# Runbook de operação

> Como diagnosticar e resolver os incidentes previstos do pipeline.
> Status: INC-01 a INC-05 **validados contra o pipeline real**: cada um foi provocado pela
> simulação ou por teste automatizado e resolvido com os comandos descritos; o INC-05 foi
> executado na referência (`outputs/prints/passo5-backfill.txt`).

## Como ler este documento

Cada incidente tem: **sintoma** (o que aparece), **diagnóstico** (o que olhar,
em ordem), **correção** e **prevenção**. Os comandos citados são os da CLI
`agrosmart`; os arquivos citados estão em `outputs/`.

## INC-01 · Sensor mudo — validado com o cenário `SNS-0302`

**Sintoma.** `agrosmart qualidade` mostra, em *Alertas de frescor*, a regra `SEN-12` para um
`sensor_id` — tipo `atrasado` (última leitura além de 30 min do instante de referência) ou
`lacuna` (intervalo entre leituras consecutivas maior que 30 min). Em `fato_leitura_diaria_talhao`
a `cobertura_pct` do talhão cai abaixo de 100 nos dias afetados.

**Como a simulação o provoca.** O sensor `SNS-0302` (TLH-03) não é solar e parte de 58 % de
bateria com descarga de 2,9 %/dia: cruza 15 % por volta do 15º dia (alertas `SEN-10` em cada
leitura), cala quando chega a 5 % (~19º dia) e volta cinco dias depois, após a ordem de
manutenção lançada no ERP. O relatório registra uma lacuna de ~5 dias para esse sensor.

**Diagnóstico.**
1. `agrosmart qualidade` → tabela *Alertas de frescor*: qual sensor, `maior_lacuna_min`,
   `maior_lacuna_inicio`/`fim`.
2. `agrosmart sql "SELECT * FROM dim_sensor WHERE sensor_id = 'SNS-0302'"` → `bateria_min_pct`,
   `bateria_ultima_pct`, `rssi_medio_dbm`, `leituras_com_alerta`.
3. Se `bateria_min_pct` < 15 %: bateria. Se `rssi_medio_dbm` < −95: rede. Se ambos normais:
   gateway ou broker — comparar com o outro sensor do mesmo talhão
   (`SELECT * FROM dim_sensor WHERE talhao_id = 'TLH-03'`).
4. Confirmar no ERP se já existe ordem de manutenção:
   `agrosmart sql "SELECT * FROM fato_operacao_custo WHERE tipo_operacao = 'manutencao'"`.

**Correção.**
- Bateria → abrir ordem de manutenção (troca) no ERP; ela entra pelo coletor batch como operação
  `manutencao` e aparece no gold. Enquanto o sensor não volta, o balanço hídrico do talhão usa o
  sensor remanescente — `n_sensores_ativos` em `fato_leitura_diaria_talhao` mostra isso.
- Rede → reposicionar antena/gateway; o alerta `SEN-11` (RSSI) antecipa esse caso.
- Gateway/broker → verificar o buffer offline: quando a conectividade volta, as leituras chegam
  atrasadas e a lacuna **se fecha sozinha** no próximo `agrosmart processar` (o silver é
  recomputado por inteiro). Nenhuma ação no lake.

**Prevenção.** `SEN-10` (bateria < 15 %) e `SEN-11` (sinal < −95 dBm) são alertas que chegam ao
`fato_alerta_qualidade` dias antes da falha; o copiloto os transforma em recomendação de
manutenção preventiva.

## INC-02 · Schema quebrado na origem

**Sintoma.** `agrosmart ingerir` mostra `rejeitados > 0` para uma fonte e imprime o motivo:
`talhoes_20260901.csv: cabeçalho divergente do contrato erp_talhoes.v1 — colunas inesperadas ['coluna_nova']`.
Nenhum objeto novo dessa fonte no bronze para o dia.

**Diagnóstico.**
1. `outputs/logs/eventos.jsonl` → filtrar `"acao": "lote_rejeitado"`; o campo `mensagem` traz o motivo completo e `batch_id` identifica o lote.
2. Na quarentena, `_rejeitados/<fonte>/<batch_id>/_motivo.json` tem origem, motivo e a ação sugerida; ao lado está o arquivo original, intocado, para inspeção.
3. Decidir: a origem errou (coluna renomeada por engano) ou o layout mudou de verdade?

**Correção.**
- Origem errou → corrigir a exportação e depositar o arquivo de novo na landing. Como o lote nunca ganhou manifest, `agrosmart ingerir` o trata como novo.
- Layout mudou → criar `contratos/<fonte>.v2.yaml` (a v1 sai da pasta; só uma versão fica ativa), ajustar o mapeamento no silver, rodar `agrosmart contratos` para validar e reingerir. O bronze antigo continua legível pela `schema_version` gravada em cada objeto e manifest.

**Prevenção.** Comparação de cabeçalho na porta de entrada, antes de gravar no bronze — o dado fora de contrato nunca contamina a camada bruta. Testado em `tests/test_ingestao.py` com coluna a mais e a menos.

## INC-03 · Lote duplicado ou reenviado

**Sintoma.** A origem reenviou um arquivo já ingerido (ou o operador rodou `ingerir` duas vezes).

**Diagnóstico.**
1. Se o conteúdo é idêntico: o evento é `lote_pulado` e nada muda no bronze — não há incidente.
2. Se o conteúdo mudou (ERP reexportou o dia com correção): o evento é `lote_atualizado`, o manifest passa a `versao: 2` e guarda `versao_anterior_checksum`. O objeto anterior sobrevive no versionamento do bucket.
3. Duplicata **dentro** do dado (gateway reenviando leitura) não é lote duplicado: passa pelo bronze e é a regra `SEN-02` no silver que a captura.

**Correção.** Nenhuma para os casos 1 e 2 — é o comportamento desejado. Se a versão nova estiver errada, restaurar a anterior pelo versionamento do bucket (`mc undo` ou console) e reingerir.

**Prevenção.** `batch_id` determinístico (fonte + janela + arquivo), manifest com checksum e deduplicação pela chave natural no silver. Testado em `tests/test_ingestao.py` (`reingerir_nao_duplica_nada`, `origem_alterada_gera_nova_versao`).

## INC-04 · Quarentena crescendo

**Sintoma.** Em `agrosmart qualidade`, a coluna *quarentena* de uma fonte salta em relação às
execuções anteriores (referência: sensores reprovam ~1,2 % na execução padrão; as demais fontes,
unidades). Ou a reconciliação de alguma fonte aparece como *divergente*.

**Diagnóstico.**
1. `agrosmart qualidade` → tabela *Regras acionadas*: **qual regra** concentra as violações.
   Uma regra só disparando muito é problema da origem (ou da regra); várias ao mesmo tempo é
   mudança de formato que passou pela porta (valores no lugar errado, unidade trocada).
2. Ler a quarentena da fonte com o motivo legível:
   `agrosmart sql "SELECT _motivo, count(*) FROM quarentena_sensores GROUP BY 1"` — ou, direto no
   Parquet, `quarentena/<fonte>/ano=/mes=/dia=/` (coluna `_regras_violadas`, `_motivo`, e todos
   os campos originais **sem pseudonimização**, para rastrear a origem).
3. Localizar o lote: cada linha da quarentena tem `_batch_id`; o manifest em
   `bronze/_manifests/<fonte>/…/<batch_id>.json` diz de que arquivo e janela veio.
4. Se a reconciliação divergiu (`declarados ≠ lidos`), o problema é de leitura do bronze, não de
   qualidade: conferir o manifest do lote e o evento `fonte_processada` em `eventos.jsonl`.

**Correção.**
- Origem errada (sensor descalibrado, ERP exportando em centavos) → corrigir na origem e
  reingerir; o silver é recomputado e os registros deixam a quarentena sozinhos.
- Regra errada (limite agronômico apertado demais) → ajustar no contrato, com a justificativa
  atualizada, e rodar `agrosmart processar`. A regra vale retroativamente para todo o histórico.
- Regra certa, dado errado e irrecuperável → não há o que fazer no lake: o registro fica na
  quarentena até a retenção de 30 dias, e o gold trabalha com cobertura reduzida
  (`cobertura_pct`, `clima_disponivel`).

**Prevenção.** Os defeitos injetados pela simulação são a bateria de regressão deste incidente:
`dq_report.json → defeitos_injetados.cobertura` precisa continuar em 1,0 (testado).

## INC-05 · Backfill de um intervalo

**Quando.** Regra de qualidade mudou; bug corrigido no silver; origem reenviou dados.

```
agrosmart run --from 2026-08-01 --to 2026-08-31
```

**Dois caminhos, conforme o caso** (ADR 0010):

| Situação | Caminho | Por quê |
|---|---|---|
| Regra de qualidade mudou; bug corrigido no silver | `agrosmart processar` | silver e gold são recomputados por inteiro do bronze (ADR 0008): todo o histórico recebe a correção |
| Origem reenviou ou recuperou dados de alguns dias (gateway que ficou sem rede, ERP que corrigiu lançamentos) | depositar na landing → `agrosmart run --from A --to B` | ingere os lotes novos e reprocessa **só** os lotes do intervalo nas fontes de eventos, substituindo no silver exatamente o que veio deles (eventos atrasados de outros dias são preservados); fontes de versões (ERP) são recomputadas por inteiro porque a deduplicação precisa ver todas as versões da chave |
| Lote ingerido com partição errada | INC-03 (`ingerir --forcar`) e depois o backfill | o backfill escolhe lotes pela partição do manifest; uma partição errada precisa ser corrigida na ingestão |

**O que o backfill garante** (testado em `tests/test_orquestracao.py`): a tabela silver da fonte
de eventos fica com as mesmas partições e o mesmo total de antes; uma partição apagada do
silver é recomposta a partir do bronze; a reconciliação declarados = lidos = aprovados +
quarentena + substituídos fecha dentro do intervalo; o relatório do backfill vai para
`outputs/dq_report_backfill_<A>_<B>.json` (o `dq_report.json` da última execução completa, que o
gold e o dashboard leem, não é substituído). A quarentena **acumula** (auditoria; a retenção
poda) e o gold é recomputado do silver completo.

**Validação (execução de referência).** `run --from 2026-08-21 --to 2026-08-25` sobre a janela
do INC-01 reprocessa 5 × 96 lotes de sensores e 5 dias de clima/imagens/laudos, reescreve 5
partições do silver de cada fonte de eventos e deixa `sensores_leitura` com o mesmo total de
linhas — evidência em `outputs/prints/passo5-backfill.txt`.
