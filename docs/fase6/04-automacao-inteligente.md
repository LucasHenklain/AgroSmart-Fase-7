# Entrega 4 — Automação Inteligente Baseada em Dados

> Artefatos: código/lógica da automação (`src/agrosmart/automacao/`), descrição da decisão automatizada (este documento), exemplo de saída (`outputs/acoes/acoes_2026-09-01.{json,md}`, `outputs/prints/fase6-04-automacao*.txt`)
> Status: **implementado, testado e executado** sobre o lake da Fase 5 (MinIO, referência 2026-09-01). Comando: `agrosmart automacao`.

## 1. O que muda da Fase 5 para a Fase 6

Na Fase 5 o ciclo parava na recomendação: o gold calculava os indicadores, o copiloto lia o Context Pack e escrevia *"Irrigar 22,5 mm no talhão TLH-02"* — e alguém precisava ler o relatório e abrir a válvula. A Fase 6 fecha o ciclo **dado → decisão → ação**:

| | Fase 5 (copiloto) | Fase 6 (automação) |
|---|---|---|
| Entrada | Context Pack (9 tabelas do gold) | `fato_balanco_hidrico`, `fato_leitura_diaria_talhao`, `dim_sensor` (+ `dim_talhao` só para rótulos) |
| Saída | recomendação em prosa, com prioridade e confiança | **plano de ações** estruturado: tipo, parâmetros, motivo, evidências, comando MQTT, status |
| Quem executa | o produtor, lendo o relatório | o próprio processo: comando `{"acao":"ligar","minutos":170}` no tópico do controlador de irrigação do talhão |
| Limiares | `agronomia.py` | **os mesmos** `agronomia.py` — nenhum limiar novo |
| Papel da IA | decide o texto da recomendação (com guardrails) | **explica** a decisão já tomada pelas regras (com os mesmos guardrails); o plano não depende dela |

O princípio da Fase 5 continua de pé, só muda de forma: *as regras decidem e executam o que tem atuador; a IA explica; o produtor pode vetar* — o plano fica em `outputs/acoes/` e no dashboard antes e depois de qualquer publicação.

## 2. A decisão automatizada

Tudo em `src/agrosmart/automacao/motor.py`. O motor **não recalcula indicadores**: fração de água disponível, déficit, estresse, cobertura, bateria e sinal vêm prontos do gold; ele só compara com os limiares e transforma a comparação em ação.

| Regra | Condição (por talhão, na data de referência) | Limiar e origem | Ação | Parâmetros | Prioridade |
|---|---|---|---|---|---|
| **Irrigar** | talhão irrigado e `fracao_agua_disponivel` < limiar | `FRACAO_IRRIGACAO = 0.50` (`agronomia.py`; é o mesmo que gera `recomenda_irrigar` no gold) | `irrigar` + comando MQTT | `lamina_mm` = 85 % do `deficit_mm` até a capacidade de campo, máx. 30 mm, múltiplos de 0,5 (regra idêntica à de `lamina_sugerida_mm` no Context Pack, `ia/contexto.py`); `minutos` = lâmina ÷ taxa de aplicação do sistema, arredondado para cima em passos de 5 min; `janela` = 04:00–07:00 (menor evapotranspiração, como em `ia/regras.py`) | `alta` se o talhão está em estresse (`FRACAO_ESTRESSE = 0.35`) ou o estádio é crítico para água (`ESTADIOS_CRITICOS_AGUA`); senão `media` |
| **Trava do sequeiro** | talhão sem sistema de irrigação | `irrigado = false` em `fato_balanco_hidrico` (mesma trava do gold e do guardrail G2) | nenhuma ação de irrigação; risco reflete o estresse | — | — |
| **Alerta fitossanitário** | nos 7 dias fechados antes da referência, ≥ N dias com umidade do ar dos sensores acima de UR e temperatura média do ar na faixa de risco | `DIAS_UR_ALTA_RISCO = 3`, `UR_ALTA_PCT = 80`, `TEMP_RISCO_FUNGICO_C = (17, 27)` (`agronomia.py`; a mesma regra de `ia/regras.py`, aqui com `umidade_ar_media_pct` e `temp_ar_min/max_c` de `fato_leitura_diaria_talhao` em vez da estação) | `alerta_fitossanitario` (sem atuador) | dias de UR alta, UR média, temperatura média, faixa, janela "próximos 2 dias, primeira janela sem chuva" | `media` |
| **Trocar bateria** | `bateria_ultima_pct` do sensor abaixo do limiar | `BATERIA_MANUTENCAO_PCT = 15` (`agronomia.py`; regra SEN-10 do contrato) | `trocar_bateria` por sensor | bateria, limiar, janela "próxima entrada no talhão" | `media` |
| **Inspecionar sensor — sinal** | `rssi_medio_dbm` abaixo do limiar **ou** mais de 5 % das leituras com SEN-11 | `RSSI_FRACO_DBM = −95` (`agronomia.py`); 5 % = `sinal_fraco` de `ia/contexto.py` | `inspecionar_sensor` por sensor | RSSI, leituras fracas, fração | `baixa` |
| **Inspecionar sensor — cobertura** | `cobertura_pct` abaixo do mínimo em algum dos 7 dias fechados | 80 % = o que rebaixa a confiança do copiloto (`ia/regras.py` `_confianca_por_dados`, guardrail G4) | `inspecionar_sensor` no talhão, apontando o sensor com menos `dias_com_leitura` | cobertura mínima, dias abaixo | `media` |
| **Inspecionar sensor — sem dado** | nenhuma umidade medida nos 14 dias do balanço | janela de 14 dias = `serie_hidrica_14d` do Context Pack | `inspecionar_sensor` no talhão | — | `alta` |
| **Nenhuma** | nada acima | — | o talhão entra no plano sem ação (`por_tipo.nenhuma` conta esses talhões) | — | — |

Dois parâmetros são da automação, não da agronomia, e estão declarados com esse rótulo no código:

- **Taxa de aplicação por sistema** (`TAXA_APLICACAO_MM_H`: pivô 8, aspersão 10, gotejamento 4, sulco 15 mm/h) — é o que converte lâmina em minutos de válvula aberta. Valores típicos de projeto, a calibrar por equipamento; não alteram *se* irrigar, só *por quanto tempo*.
- **Faixa "programar"** (limiar + 0,15): entre 0,50 e 0,65 de água disponível não há ação, mas o talhão sai como `atenção` com decisão `programar irrigação` — é a mesma faixa em que `ia/regras.py` recomenda "programar" e acima da qual o guardrail G2 proíbe irrigar.

### 2.1 Classificação de risco por talhão

| Risco | Quando |
|---|---|
| `critico` | estresse hídrico (`estresse_hidrico` do gold) **ou** ação de irrigar com prioridade alta — inclui o sequeiro em estresse, que não tem atuador e vai para o produtor |
| `atencao` | há pelo menos uma ação pendente, ou a fração de água está na faixa "programar", ou não há leitura de umidade |
| `normal` | dentro de todos os limiares |

### 2.2 Determinismo, evidências e idempotência

- Mesmo gold + mesma data ⇒ mesmo plano (só `gerado_em` muda). Ações numeradas `ACT-0001…` em ordem fixa (talhão, tipo, sensor).
- Toda ação cita `evidencias` no formato `tabela:chave:data` — a linha do gold que a sustenta (`fato_balanco_hidrico:TLH-02:2026-09-01`, `dim_sensor:SNS-0602:2026-09-01`, um item por dia de cobertura baixa ou de UR alta).
- Reexecutar sobrescreve `acoes_<data>.{json,md}`; `publicar_plano` só publica o que está `simulado`, então rodar duas vezes não liga a válvula duas vezes.
- A data de referência é `--data` ou o último dia do balanço com umidade medida. Cobertura e clima usam os **7 dias fechados** anteriores (o dia corrente só tem a madrugada, como no Context Pack).

## 3. Fluxo: dado → decisão → atuador → registro

```
 gold (MinIO/filesystem)                    automacao/motor.py                         saídas
 ┌──────────────────────────┐   ler_tabela   ┌───────────────────────────┐   JSON    outputs/acoes/acoes_<data>.json ──► app/dashboard.py
 │ fato_balanco_hidrico     │ ─────────────► │ 1. data de referência     │ ────────► outputs/acoes/acoes_<data>.md
 │ fato_leitura_diaria_talh.│                │ 2. por talhão:            │
 │ dim_sensor (+dim_talhao) │                │    hídrico → irrigar?     │  copiloto (ia/agente.py, guardrails G1–G5)
 └──────────────────────────┘                │    ar     → alerta?      │ ◄───────── justificativa por talhão (motor regras
                                             │    sensor → bateria/sinal│             sem chave; Claude com chave; fallback)
      limiares agronomia.py ───────────────► │ 3. risco por talhão       │
      (FRACAO_IRRIGACAO, UR, T, bateria…)    │ 4. plano + evidências     │
                                             └─────────────┬─────────────┘
                                                           │ --publicar (paho-mqtt, QoS 1)
                                                           ▼
                             automacao/atuadores.py ──► broker MQTT ──► agrosmart/atuadores/<talhao>/irrigacao
                                                           │                      {"acao":"ligar","minutos":170}
                                                           ▼                      (firmware/ e simuladores/dispositivo_mqtt.py assinam)
                                 outputs/logs/eventos.jsonl: plano_gerado · comando_publicado (por ação)
```

1. **Leitura** — `carregar_tabelas` usa `ler_tabela` sobre o `Armazenamento` configurado: MinIO ou filesystem, sem SQL e sem recálculo.
2. **Decisão** — `gerar_plano` aplica a tabela da seção 2 talhão a talhão e classifica o risco.
3. **Explicação (opcional)** — `anexar_copiloto` monta o Context Pack de cada talhão e pede ao copiloto a justificativa (caso `irrigacao`; mais `fitossanitario` quando há alerta). Sem `ANTHROPIC_API_KEY` responde o motor de regras; com chave, o modelo — e, se os guardrails rejeitarem, cai nas regras. Qualquer falha é registrada e o plano segue sem a justificativa daquele talhão.
4. **Atuação** — com `--publicar`, cada ação com `comando_mqtt` é publicada com QoS 1 e espera o PUBACK; quem confirmou vira `publicado`, quem falhou fica `simulado` e aparece em `falhas`. Broker fora do ar não custa o plano: tudo fica `simulado`, o arquivo é gravado e a CLI sai com código 1.
5. **Registro** — `outputs/acoes/acoes_<data>.json` (contrato com o dashboard), `.md` (para gente) e a trilha `outputs/logs/eventos.jsonl` (`etapa=automacao`: `plano_gerado`, `comando_publicado`).

## 4. Como o copiloto de IA complementa

O copiloto da Fase 5 não foi alterado — é chamado como está (`Copiloto.responder`), com seus prompts versionados e os guardrails G1–G5 (`docs/03-ia-generativa.md` §2.4). O que muda é a ordem: **primeiro as regras decidem, depois a IA explica**. A justificativa entra em cada talhão do plano como `copiloto`: motor usado, se houve fallback, confiança e sua justificativa, diagnóstico e até 6 recomendações. Três consequências:

- **Coerência garantida.** Regras e copiloto usam os mesmos limiares e as mesmas tabelas; no exemplo real, o plano manda irrigar 22,5 mm no TLH-02 e o copiloto escreve "Irrigar 22,5 mm no talhão TLH-02" — o teste `test_executar_sobre_o_lake_de_teste` confere que a lâmina do plano é a `lamina_sugerida_mm` do Context Pack para todo talhão.
- **Confiança como sinal de dado fraco.** No TLH-03 a confiança do copiloto é `baixa` (cobertura de 50 % e lacuna SEN-12) e o plano, pela mesma evidência, abre `inspecionar_sensor` para o SNS-0302.
- **Sem dependência.** `--motor nenhum` gera o plano sem IA; sem chave de API o motor de regras responde; a API só entra com `--motor auto|claude` e chave no `.env`.

## 5. Exemplo real de saída (`agrosmart automacao`, lake da Fase 5, referência 2026-09-01)

Console (`outputs/prints/fase6-04-automacao.txt`):

```
  talhão   cultura                risco       FAD   déficit mm   cobertura 7d   decisão             ações
  TLH-01   milho · pivo_central   atencao   0.522         25.8   99 %           programar           nenhuma
  TLH-02   milho · pivo_central   critico   0.480         26.5   98 %           irrigar             ACT-0001
  TLH-03   feijao · aspersao      atencao   0.655         18.6   50 %           ok                  ACT-0002
  TLH-04   trigo · aspersao       normal    0.833          8.5   99 %           ok                  nenhuma
  TLH-05   cafe · gotejamento     normal    0.776         12.1   99 %           ok                  nenhuma
  TLH-06   cana · sequeiro        critico   0.002         50.9   100 %          sequeiro_estresse   ACT-0003

  id         talhão   sensor     tipo                 prioridade   parâmetros                        MQTT                                            status
  ACT-0001   TLH-02   —          irrigar              alta         22.5 mm · 170 min · 04:00-07:00   agrosmart/atuadores/TLH-02/irrigacao {'acao': 'ligar', 'minutos': 170}   simulado
  ACT-0002   TLH-03   SNS-0302   inspecionar_sensor   media        cobertura_baixa                   —                                               simulado
  ACT-0003   TLH-06   SNS-0602   inspecionar_sensor   baixa        sinal_fraco                       —                                               simulado

6 talhões · 3 ação(ões) · por tipo {'irrigar': 1, 'inspecionar_sensor': 2, 'nenhuma': 3} · por prioridade {'alta': 1, 'media': 1, 'baixa': 1}
✔ plano em outputs/acoes/acoes_2026-09-01.json · outputs/acoes/acoes_2026-09-01.md
```

Trecho do JSON (`outputs/acoes/acoes_2026-09-01.json` — esquema completo na seção 6):

```json
{
  "gerado_em": "2026-10-08T12:33:12+00:00",
  "data_ref": "2026-09-01",
  "fonte": "gold.fato_balanco_hidrico + fato_leitura_diaria_talhao + dim_sensor",
  "resumo": {"talhoes": 6, "acoes": 3,
             "por_tipo": {"irrigar": 1, "inspecionar_sensor": 2, "nenhuma": 3},
             "por_prioridade": {"alta": 1, "media": 1, "baixa": 1}},
  "talhoes": [
    {"talhao_id": "TLH-02", "risco": "critico",
     "indicadores": {"cultura": "milho", "sistema_irrigacao": "pivo_central", "irrigado": true,
                     "dia_leitura": "2026-09-01", "umidade_media_pct": 21.16,
                     "fracao_agua_disponivel": 0.48, "deficit_mm": 26.5, "estadio": "VT",
                     "estadio_critico_para_agua": true, "estresse_hidrico": false,
                     "situacao_hidrica": "irrigar", "dias_ur_alta_7d": 0, "temp_media_7d_c": 22.8,
                     "sensores": 2, "bateria_min_pct": 88.0, "cobertura_min_7d_pct": 98.4, "acoes": 1},
     "copiloto": {"motor": "regras", "fallback": false, "confianca": "alta",
                  "guardrails_aprovados": true,
                  "diagnostico": "Talhão TLH-02 (milho DKB 390 PRO3, 12,4 ha, pivo_central) em VT, 58 dias após o plantio. Umidade média de 21,2 % em 2026-09-01, fração de água disponível 0,480, déficit de 26,5 mm. …",
                  "recomendacoes": ["[ALTA] Irrigar 22,5 mm no talhão TLH-02 (irrigacao)"]}}
  ],
  "acoes": [
    {"id": "ACT-0001", "talhao_id": "TLH-02", "sensor_id": null, "tipo": "irrigar", "prioridade": "alta",
     "parametros": {"lamina_mm": 22.5, "minutos": 170, "janela": "04:00-07:00",
                    "fracao_agua_disponivel": 0.48, "limiar_fracao_irrigacao": 0.5, "deficit_mm": 26.5,
                    "sistema_irrigacao": "pivo_central", "taxa_aplicacao_mm_h": 8.0},
     "motivo": "Fração de água disponível em 0,480, abaixo do limiar de 0,50 para irrigar; déficit de 26,5 mm até a capacidade de campo. Lâmina de 22,5 mm (85 % do déficit, máx. 30 mm) = 170 min de pivo_central a 8,0 mm/h. Estádio VT é crítico para água.",
     "evidencias": ["fato_balanco_hidrico:TLH-02:2026-09-01"],
     "comando_mqtt": {"topico": "agrosmart/atuadores/TLH-02/irrigacao",
                      "payload": {"acao": "ligar", "minutos": 170}},
     "status": "simulado"},
    {"id": "ACT-0002", "talhao_id": "TLH-03", "sensor_id": "SNS-0302", "tipo": "inspecionar_sensor",
     "prioridade": "media",
     "parametros": {"motivo_tecnico": "cobertura_baixa", "cobertura_min_7d_pct": 50.0, "dias_abaixo": 1,
                    "limiar_cobertura_pct": 80.0},
     "motivo": "Cobertura de leituras de 50,0 % em 1 dia(s) da última semana, abaixo de 80 %: lacuna de sensor que rebaixa a confiança das decisões hídricas. Sensor com menos dias de leitura: SNS-0302.",
     "evidencias": ["fato_leitura_diaria_talhao:TLH-03:2026-08-25"], "comando_mqtt": null, "status": "simulado"},
    {"id": "ACT-0003", "talhao_id": "TLH-06", "sensor_id": "SNS-0602", "tipo": "inspecionar_sensor",
     "prioridade": "baixa",
     "parametros": {"motivo_tecnico": "sinal_fraco", "rssi_medio_dbm": -90.2, "leituras_sinal_fraco": 276,
                    "fracao_sinal_fraco": 0.096, "limiar_rssi_dbm": -95},
     "motivo": "Sensor SNS-0602 com sinal médio de -90,2 dBm e 276 leitura(s) abaixo de -95 dBm (fração 0,096): reposicionar antena ou gateway para evitar perda de leituras.",
     "evidencias": ["dim_sensor:SNS-0602:2026-09-01"], "comando_mqtt": null, "status": "simulado"}
  ]
}
```

Tabela por talhão do `outputs/acoes/acoes_2026-09-01.md`:

| Talhão | Cultura · irrigação | Risco | Umidade (%) | FAD | Déficit (mm) | Estádio | Cobertura mín. 7d (%) | Decisão hídrica | Ações |
|---|---|---|---|---|---|---|---|---|---|
| TLH-01 | milho · pivo_central | atenção | 25,4 | 0,522 | 25,8 | R1 | 99,0 | programar irrigação | nenhuma |
| TLH-02 | milho · pivo_central | CRÍTICO | 21,2 | 0,480 | 26,5 | VT | 98,4 | irrigar | ACT-0001 |
| TLH-03 | feijao · aspersao | atenção | 29,8 | 0,655 | 18,6 | R7 | 50,0 | manejo atual | ACT-0002 |
| TLH-04 | trigo · aspersao | normal | 28,2 | 0,833 | 8,5 | maturacao | 99,0 | manejo atual | nenhuma |
| TLH-05 | cafe · gotejamento | normal | 29,0 | 0,776 | 12,1 | florada | 99,0 | manejo atual | nenhuma |
| TLH-06 | cana · sequeiro | CRÍTICO | 12,0 | 0,002 | 50,9 | maturacao | 99,5 | sequeiro em estresse — sem atuador | ACT-0003 |

O que a execução mostra:

- **TLH-02** — a única ação com atuador: 0,480 < 0,50, estádio VT (crítico) ⇒ `irrigar` **alta**, 22,5 mm = 170 min de pivô. É a mesma lâmina que o copiloto já sugeria na Fase 5 (`docs/03-ia-generativa.md` §3.1), agora como comando.
- **TLH-06** — fração 0,002 e 10 dias em estresse, mas sequeiro: risco `crítico` **sem** ação de irrigação (trava); o sensor SNS-0602 ganha inspeção por sinal fraco (9,6 % das leituras abaixo de −95 dBm).
- **TLH-03** — balanço em ordem, mas a cobertura caiu a 50 % em 25/08 (INC-01, SNS-0302 calado): `inspecionar_sensor` **média**, e o copiloto confirma com confiança `baixa`.
- **TLH-01** — 0,522: ainda não irriga, mas entra como `atenção`/`programar`, igual à recomendação "média" do copiloto.
- Nenhum alerta fitossanitário nem bateria baixa nesta janela (o SNS-0302 teve a bateria trocada na simulação; cenários cobertos por teste).

### 5.1 Publicação real (`agrosmart automacao --publicar`, Mosquitto em `localhost:1883`)

`outputs/prints/fase6-04-automacao-publicar.txt` registra a execução com o broker da `infra/` no ar e um assinante de prova em `agrosmart/atuadores/+/irrigacao`:

```
10:05:34 INFO  ACT-0001 publicada em agrosmart/atuadores/TLH-02/irrigacao → {"acao": "ligar", "minutos": 170}
  ACT-0001   TLH-02   —   irrigar   alta   22.5 mm · 170 min · 04:00-07:00   agrosmart/atuadores/TLH-02/irrigacao {'acao': 'ligar', 'minutos': 170}   publicado
✔ MQTT localhost:1883: 1 comando(s) publicado(s)
--- assinante de prova (agrosmart/atuadores/+/irrigacao, QoS 1) recebeu ---
agrosmart/atuadores/TLH-02/irrigacao  {"acao": "ligar", "minutos": 170}  (qos=1)
```

Depois dessa execução, `outputs/acoes/acoes_2026-09-01.json` traz `ACT-0001` com `"status": "publicado"` (as duas inspeções continuam `simulado`: não têm atuador) e `outputs/logs/eventos.jsonl` ganha o evento `comando_publicado`. A execução anterior, **sem** broker (`outputs/prints/fase6-04-automacao-publicar-sem-broker.txt`), mostra o outro lado: `✘ MQTT localhost:1883: 0 comando(s) publicado(s), 1 falha(s)`, o plano gravado com tudo `simulado` e código de saída 1.

## 6. Esquema do plano (`acoes_<data>.json`)

```
{
  "gerado_em": ISO-UTC, "data_ref": "YYYY-MM-DD",
  "fonte": "gold.fato_balanco_hidrico + fato_leitura_diaria_talhao + dim_sensor",
  "resumo": {"talhoes": n, "acoes": n, "por_tipo": {tipo: n, "nenhuma": talhões sem ação},
             "por_prioridade": {"alta": n, "media": n, "baixa": n}},
  "talhoes": [{"talhao_id", "risco": "critico|atencao|normal", "indicadores": {...},
               "copiloto": {...}  ← opcional (ausente com --motor nenhum ou se o Context Pack falhar)}],
  "acoes": [{"id": "ACT-0001", "talhao_id", "sensor_id": null|"SNS-0301",
             "tipo": "irrigar|alerta_fitossanitario|trocar_bateria|inspecionar_sensor",
             "prioridade": "alta|media|baixa", "parametros": {...}, "motivo": "...",
             "evidencias": ["tabela:chave:data"],
             "comando_mqtt": {"topico": "agrosmart/atuadores/<talhao>/irrigacao",
                              "payload": {"acao": "ligar", "minutos": N}} | null,
             "status": "simulado|publicado"}]
}
```

É o contrato com `app/dashboard.py` (que lê o JSON) e com o firmware/emulador (que assina o tópico). `Plano.model_validate` (pydantic, `extra="forbid"`) valida o arquivo; o teste `test_json_segue_exatamente_o_esquema_combinado_com_o_dashboard` fixa a ordem e o nome de cada chave.

## 7. Como rodar

```bash
agrosmart automacao                                  # plano para a última data do gold → outputs/acoes/, status simulado
agrosmart automacao --data 2026-08-28                # outra data de referência (histórico)
agrosmart automacao --dry-run                        # só mostra: não grava nem publica
agrosmart automacao --motor nenhum                   # sem justificativa do copiloto (mais rápido)
agrosmart automacao --motor regras                   # copiloto só com o motor de regras, mesmo com chave
agrosmart automacao --publicar --broker localhost --porta 1883   # publica os comandos de irrigação (QoS 1) e marca publicado
```

Pré-requisito: gold materializado (`agrosmart processar` ou `agrosmart run --all`). Para `--publicar`: broker MQTT (`infra/`, Mosquitto) e `pip install -e ".[iot]"` (paho-mqtt). O comando é uma **etapa separada** do `run --all` de propósito: o plano é determinístico a partir do gold, pode ser reemitido para qualquer data e a publicação exige decisão explícita (`--publicar`) — um `run --all` agendado não deve abrir válvulas sem que alguém tenha olhado o plano. Em produção, o agendador roda `run --all` e, na sequência, `automacao --publicar` para a janela da madrugada.

Código de saída: 0 (plano gravado, tudo publicado quando pedido), 1 (gold ausente ou alguma publicação falhou — o plano é gravado mesmo assim, com as falhas listadas), 2 (argumento inválido).

## 8. Testes (`tests/test_automacao.py`)

`python -m pytest -q tests/test_automacao.py` — 24 testes (o do paho-mqtt real só roda com broker em `localhost:1883`; sem broker é pulado): decisões por cenário com fixtures Polars em memória (seco → irrigar com lâmina e minutos; estádio crítico/estresse → alta e risco crítico; minutos por sistema; sequeiro em estresse → crítico sem irrigar; bateria baixa → trocar no sensor certo; normal → nenhuma; faixa "programar" → atenção; UR alta + temperatura na faixa → alerta, fora da faixa ou poucos dias → nada; RSSI/fração → inspecionar; cobertura baixa → inspecionar apontando o sensor que calou; sem umidade → inspecionar alta; `--data`), esquema e ordem exata das chaves do JSON, determinismo e idempotência (dois planos iguais, dois `salvar` iguais), Markdown, publicação com cliente dublê (marca `publicado`, não republica, broker que não confirma deixa `simulado`), broker fora do ar não custa o plano, integração sobre o lake de teste (lâmina do plano = `lamina_sugerida_mm` do Context Pack para todo talhão, sequeiro sem irrigar, copiloto `regras` em todo talhão com guardrails aprovados, arquivos e eventos gravados) e CLI `--dry-run`. `ruff check src tests` limpo.

## 9. Limitações — ditas antes que perguntem

- **Sem previsão do tempo.** Como na Fase 5: a decisão de irrigar olha o observado. Com a fonte de previsão (roadmap), a regra ganha um "não irrigar se chover ≥ X mm em 72 h" — o campo `previsao_chuva_72h_mm` já existe no Context Pack, vazio.
- **Minutos dependem de uma taxa de aplicação nominal.** `TAXA_APLICACAO_MM_H` é um valor de projeto por sistema; o certo é calibrar por equipamento (vazão × área) e, com o hidrômetro no MQTT, fechar a malha pelo volume aplicado em vez do tempo.
- **O alerta fitossanitário é alerta, não pulverização.** Sem atuador e sem ler o histórico de operações, o plano não verifica o intervalo mínimo de 10 dias desde a última pulverização — quem verifica é o copiloto (caso `fitossanitario`, anexado ao talhão quando há alerta) e o agrônomo.
- **Sensor suspeito é indício.** `dim_sensor` traz `dias_com_leitura` do período todo; para uma data histórica (`--data`), a cobertura é do dia certo, mas o sensor apontado é o que menos publicou no período inteiro.
- **Publicação é "pelo menos uma vez".** QoS 1 pode entregar o comando duas vezes ao controlador; o firmware ignora `ligar` com a válvula já aberta. Confirmação de execução (telemetria de volta) fica para o firmware/emulador.
- **Sem camada de aprovação.** Hoje `--publicar` é a aprovação. Uma fazenda real pede fila de aprovação por risco (crítico publica, atenção espera o produtor) — o campo `status` e o JSON por ação já comportam isso.
