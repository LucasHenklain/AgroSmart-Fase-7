# Entrega 3 — Inteligência Artificial Generativa para Apoio à Decisão

> Peso: 25 % · Artefatos: exemplo de saída da IA + descrição do funcionamento e do contexto utilizado
> Status: **implementado e verificado**. Saídas reais em `outputs/relatorios/`, avaliação em `outputs/avaliacao_ia.json`; as mesmas recomendações aparecem no dashboard (`app/dashboard.py`, seção Copiloto) com a proveniência ao lado.

## 1. O que o copiloto faz — e o que ele não faz

O copiloto do AgroSmart recebe **tudo que o Data Lake sabe sobre um talhão** e devolve um diagnóstico com recomendações priorizadas, custo e economia estimados, confiança justificada e a lista dos dados que usou. Três casos de uso, um prompt para cada:

| Caso | Pergunta que responde | Frequência | Tabelas do gold que lê |
|---|---|---|---|
| **Irrigação** | Irrigar hoje? Quanto, quando, a que custo, evitando que perda? | diária | `fato_balanco_hidrico`, `dim_talhao`, `dim_sensor`, `fato_alerta_qualidade` |
| **Fitossanitário** | O clima dos últimos dias favorece doença? O NDVI caiu? Pulverizar ou inspecionar? | por evento | `fato_clima_diario`, `fato_ndvi_talhao`, `fato_custo_diario_talhao` |
| **Executivo** | O que aconteceu na semana com água, vegetação e custos; o que fazer na próxima? | semanal | todas as anteriores mais `fato_fertilidade_talhao` |

O que ele **não** faz, por decisão de arquitetura ([ADR 0009](adr/0009-ia-le-o-gold-com-saida-estruturada-e-guardrails.md)):

- não consulta o lake livremente: lê um pacote fechado, montado por código;
- não faz aritmética: lâmina, custo, perda e valor de produção já vêm calculados no pacote;
- não decide: recomenda, com prioridade e confiança, e o produtor decide — a frase está no rodapé de todo relatório.

## 2. Como funciona

```
gold ──► Context Pack ──► prompt (sistema + caso, versionados) ──► API Claude (saída estruturada)
                │                                                        │
                │                                                        ▼
                │                                              SaidaCopiloto (JSON tipado)
                │                                                        │
                ▼                                                        ▼
        motor de regras ◄──── fallback se rejeitado ◄──────────── guardrails
                │                                                        │
                └──────────────────► guardrails ──► relatório MD/PDF + proveniência
```

### 2.1 Context Pack (`src/agrosmart/ia/contexto.py`)

Um JSON por talhão (`context-pack.v1`), montado por consultas ao gold via DuckDB. Blocos:

| Bloco | Conteúdo | Origem |
|---|---|---|
| `talhao` | cultura, área, sistema de irrigação, `irrigado`, estádio atual e se é crítico para água, CC e PMP | `dim_talhao` + `agronomia.py` |
| `hidrico` + `serie_hidrica_14d` | umidade atual, fração de água disponível, déficit, chuva/ETo/ETc/irrigação de 14 dias, dias em estresse, dias desde a irrigação, **lâmina sugerida e seu custo já calculados**, `previsao_chuva_72h_mm = null` | `fato_balanco_hidrico` |
| `vegetacao` + `cenas_ndvi_30d` | último NDVI com confiança, variação em 30 dias só sobre cenas confiáveis, cenas com nuvem alta | `fato_ndvi_talhao` |
| `clima_recente` | 7 dias: médias, dias de UR alta, dias de chuva, temperatura favorável a fungos | `fato_clima_diario` |
| `custos` | total, por hectare, por tipo, última operação, dias desde a pulverização | `fato_custo_diario_talhao` |
| `fertilidade` | último laudo 0–20 cm: pH, V %, classe, **necessidade de calagem já calculada** | `fato_fertilidade_talhao` |
| `sensores` | por sensor: bateria, sinal, `precisa_manutencao`, `sinal_fraco`; cobertura da semana | `dim_sensor`, `fato_leitura_diaria_talhao` |
| `alertas` | alertas de qualidade relevantes ao talhão (seus sensores, sua estação) | `fato_alerta_qualidade` |
| `economia` | produtividade e preço de referência, valor da produção, perda por dia de estresse em % e R$, custo de fungicida | `agronomia.py` |
| `limiares` | gatilho de irrigação, estresse, nuvem, UR, temperatura, intervalo de pulverização, bateria, sinal | `agronomia.py` — os mesmos do gold |
| `avisos` | o que falta: sem previsão, dias sem clima, cena nublada, sem laudo | montagem |

Dois métodos do pacote sustentam os guardrails: `numeros()` — o conjunto de todos os números presentes — e `caminhos()` — todos os caminhos pontilhados válidos para `dados_usados`.

### 2.2 Saída estruturada (`ia/esquema.py`)

A resposta é uma chamada de ferramenta (`emitir_recomendacao`) cujo `input_schema` é o JSON Schema de `SaidaCopiloto`:

```json
{
  "caso": "irrigacao", "talhao_id": "TLH-02",
  "diagnostico": "…",
  "recomendacoes": [
    {"prioridade": "alta", "tipo": "irrigacao", "acao": "Irrigar 22,5 mm no talhão TLH-02",
     "detalhe": "…", "janela_execucao": "próxima madrugada, 04h–07h (menor evapotranspiração)",
     "dados_usados": ["hidrico.fracao_agua_disponivel_atual", "hidrico.lamina_sugerida_mm",
                      "hidrico.custo_lamina_sugerida_brl", "economia.perda_por_dia_estresse_brl"]}
  ],
  "custo_estimado_brl": 404.55, "economia_estimada_brl": 0.0,
  "confianca": "alta", "justificativa_confianca": "dados completos: …",
  "limitacoes": ["previsão meteorológica não disponível nesta fase — …"],
  "dados_usados": ["talhao", "hidrico", "serie_hidrica_14d", "limiares.fracao_irrigacao", "sensores"]
}
```

(É a saída real do TLH-02 na execução de referência, resumida — seção 3.1. A economia é zero porque o talhão ainda não está em estresse: a irrigação é preventiva; quando há estresse, a economia estimada é a perda de um dia evitada.)

Tipos de recomendação: `irrigacao`, `fitossanitario`, `nutricao`, `manutencao`, `monitoramento`, `gestao`. Prioridade e confiança em três níveis.

### 2.3 Prompts versionados (`ia/prompts/`)

`sistema.v1.md` fixa o papel e oito regras inegociáveis — só existe o que está no contexto; trava agronômica; limiares do contexto; confiança honesta; `dados_usados` rastreáveis; sem previsão; português direto; até 6 recomendações. `irrigacao.v1.md`, `fitossanitario.v1.md` e `executivo.v1.md` descrevem a tarefa e embutem o Context Pack. A versão do prompt fica registrada em cada relatório; mudar um prompt é criar `v2`.

### 2.4 Guardrails (`ia/guardrails.py`) — para qualquer motor

| Código | Verifica | Gravidade |
|---|---|---|
| G1 | talhão e caso da saída são os pedidos | crítica |
| G2 | recomendação de irrigar em talhão de sequeiro, ou com fração de água disponível ≥ limiar + 0,15 | crítica |
| G3 | todo número citado (diagnóstico, detalhes, custo, economia) existe no Context Pack, com tolerância de arredondamento; identificadores, datas e horas são ignorados | até 3 números soltos: ajuste (anotados em `limitacoes`, confiança rebaixada); mais que isso: crítica |
| G4 | confiança `alta` só com dados completos: cobertura ≥ 80 %, sem lacuna `SEN-12`, umidade recente, NDVI confiável quando o caso usa vegetação | ajuste (rebaixa) |
| G5 | `dados_usados` apontam para caminhos que existem no pacote | ajuste (remove inválidos) |

Violação crítica → a saída do modelo é **rejeitada** e o motor de regras responde, com a saída rejeitada e o motivo guardados na proveniência. O motor de regras passa pelos mesmos guardrails — reprovar ali seria bug nosso, e o teste `test_motor_de_regras_passa_nos_proprios_guardrails` garante que não acontece.

### 2.5 Motor de regras (`ia/regras.py`)

Determinístico, com os limiares de `agronomia.py`:

- **Irrigação**: fração de água disponível < `fracao_irrigacao` → irrigar a lâmina sugerida (prioridade alta; se em estresse, economia = perda de um dia); entre o limiar e +0,15 → programar (média); acima → manter (baixa). Sequeiro → acompanhamento. Sensores com bateria < 15 %, ou sinal médio < −95 dBm, ou mais de 5 % das leituras abaixo desse limiar → manutenção.
- **Fitossanitário**: ≥ 3 dias de UR > 80 % com temperatura entre 17 e 27 °C → pulverização preventiva (se respeitado o intervalo mínimo de 10 dias; senão, inspeção); queda de NDVI ≥ 0,05 em cenas confiáveis → vistoria; senão, monitoramento.
- **Executivo**: calagem se V % < 70; revisão do gatilho se ≥ 3 dias de estresse apesar de irrigação; acompanhamento de custo/ha; manutenção de sensores.
- **Confiança** decorre da qualidade dos dados (cobertura, dias sem clima, lacunas, NDVI), nunca da convicção da regra.

### 2.6 Proveniência

Cada saída é gravada em `outputs/relatorios/<caso>/<talhão>_<data>.*`: Markdown, PDF, o Context Pack usado (`.contexto.json`) e a proveniência (`.proveniencia.json`: motor, fallback e motivo, versões dos prompts, tokens, latência, violações e ajustes dos guardrails, saída rejeitada quando houve).

## 3. Exemplos de saída (execução de referência, 01/09/2026)

As 18 saídas — 3 casos × 6 talhões — estão em `outputs/relatorios/<caso>/`, cada uma com Markdown, PDF, o Context Pack usado e a proveniência. Três delas mostram o comportamento que importa.

### 3.1 Irrigação · TLH-02 (milho em VT, pivô central) — a recomendação de agir

> **Confiança:** alta — dados completos: sensores com cobertura plena, clima em toda a série e sem alertas abertos
>
> **Diagnóstico.** Talhão TLH-02 (milho DKB 390 PRO3, 12,4 ha, pivo_central) em VT, 58 dias após o plantio. Umidade média de 21,2 % em 2026-09-01, fração de água disponível 0,480, déficit de 26,5 mm. Nos últimos 14 dias: 17,2 mm de chuva, 54,7 mm de ETc, 0,0 mm irrigados, 0 dia(s) em estresse.
>
> **1. [ALTA] Irrigar 22,5 mm no talhão TLH-02** (irrigacao) · _quando:_ próxima madrugada, 04h–07h (menor evapotranspiração)
> Fração de água disponível em 0,480, abaixo do limiar de 0,50 para irrigar; déficit de 26,5 mm até a capacidade de campo. Lâmina sugerida de 22,5 mm ao custo de R$ 404,55 (1,45 R$/mm/ha). O talhão está em VT, estádio crítico para água: cada dia de estresse custa 2,0 % do potencial (R$ 2591,60).
>
> **Custo estimado das ações:** R$ 404,55 · **Limitações:** previsão meteorológica não disponível nesta fase; sem laudo de solo 0–20 cm aprovado para o talhão (o laudo do TLH-02 está na quarentena).

Todo número acima existe no Context Pack anexado (`TLH-02_2026-09-01.contexto.json`, 10 KB): `hidrico.fracao_agua_disponivel_atual = 0.48`, `hidrico.lamina_sugerida_mm = 22.5`, `hidrico.custo_lamina_sugerida_brl = 404.55`, `economia.perda_por_dia_estresse_brl = 2591.6`. A lâmina e o custo foram calculados pelo lake, não pelo motor.

### 3.2 Irrigação · TLH-03 (feijão em R7, aspersão) — a recomendação de não agir, com confiança baixa

> **Confiança:** baixa — cobertura de leituras de 50,0 % em pelo menos um dia da semana; lacuna recente em sensor do talhão (SEN-12)
>
> **1. [BAIXA] Manter o manejo atual do talhão TLH-03** (monitoramento)
> Fração de água disponível em 0,655, acima do limiar de 0,50; umidade média de 29,8 % e 46,5 mm irrigados em 14 dias. Nenhuma ação de irrigação necessária hoje.

A confiança baixa não é insegurança da regra: é o sensor `SNS-0302`, que ficou calado cinco dias (INC-01) e voltou há poucos dias. O copiloto diz isso em vez de esconder.

### 3.3 Executivo · TLH-06 (cana, sequeiro) — a trava agronômica em ação

> **Diagnóstico.** … Água: 10 dia(s) em estresse em 14, 9,0 mm de chuva e 0,0 mm irrigados; fração de água disponível atual 0,002. Vegetação: NDVI 0,635 (alta), variação −0,199 em 30 dias. Custos: R$ 3003,34 no período (R$ 72,72/ha). Valor de produção de referência: R$ 491470,00.
>
> **1. [MÉDIA] Planejar calagem de 1,91 t/ha no talhão TLH-06** (nutricao) — Laudo LAU-2026-00477 (2026-07-26, 0–20 cm): saturação por bases de 42,1 % (classe baixa), pH 4,9. Fósforo baixo (11,3 mg/dm³).
> **2. [BAIXA] Acompanhar o custo por hectare do talhão TLH-06** (gestao)
> **3. [BAIXA] Reposicionar antena ou gateway para o sensor SNS-0602** (manutencao) — Sinal médio de −90,2 dBm e 276 leitura(s) abaixo de −95 dBm no período (fração 0,096).

Dez dias em estresse e fração de água disponível quase zero — e **nenhuma** recomendação de irrigar, porque o talhão é de sequeiro. Se o modelo generativo recomendasse, o guardrail G2 rejeitaria a saída e o motor de regras responderia no lugar (testado: `test_copiloto_cai_no_fallback_quando_o_modelo_viola_guardrail`).

### 3.4 Fitossanitário · TLH-04 (trigo em maturação) — o sinal certo sem o remédio errado

> **1. [MÉDIA] Vistoriar em campo a queda de vigor do talhão TLH-04** (monitoramento) · _quando:_ próximos 3 dias
> NDVI variou −0,184 em 30 dias (último 0,620, confiança alta) sem clima favorável a fungos: investigar estresse hídrico (0 dia(s) em estresse), praga ou deficiência nutricional.

Queda de NDVI sem janela climática de risco → vistoria, não pulverização. (No trigo em maturação a queda é esperada — senescência; a recomendação continua correta: quem confirma é o agrônomo em campo.)

## 4. Avaliação (`agrosmart ia avaliar`)

Para cada talhão e caso, o motor de regras e o modelo (quando há chave de API) são executados sobre o mesmo Context Pack. Mede-se, por motor: aprovação nos guardrails, ajustes, fallbacks, latência, distribuição de confiança, decisões de irrigar. Entre motores: **concordância** na decisão de irrigar, na prioridade máxima, na confiança e a sobreposição dos tipos de recomendação (Jaccard). Saída em `outputs/avaliacao_ia.json` e `outputs/relatorios/avaliacao_ia.md`.

### 4.1 Execução de referência (`agrosmart ia avaliar`, sem chave de API)

| Motor | Execuções | Aprovadas nos guardrails | Ajustes | Fallbacks | Confiança alta / média / baixa | Recomendam irrigar |
|---|---|---|---|---|---|---|
| regras | 18 | **18** | 0 | 0 | 15 / 0 / 3 | 2 (TLH-02 alta, TLH-01 média) |

- As 3 saídas com confiança baixa são as do TLH-03 — sensor `SNS-0302` com lacuna na semana (INC-01). As demais têm dados completos.
- Nenhum número solto: o motor de regras cita só o que está no pacote, e o guardrail confirma (`numeros_nao_aterrados = []` em 18/18).
- Context Pack do TLH-03: **166 números citáveis, 476 caminhos**, ~10 KB por talhão; `ia gerar` produz as 18 saídas com PDF em 3 s.
- **Motor generativo inativo** nesta execução (sem `ANTHROPIC_API_KEY`): a concordância regras × modelo não foi medida. Com a chave no `.env`, `agrosmart ia avaliar` roda os dois motores e preenche `concordancia_regras_vs_modelo`; o comportamento do fallback está coberto por testes com um dublê do modelo (saída válida aceita; saída que viola G2 rejeitada; JSON fora do schema; erro de API).

O que a avaliação **não** afirma: que a recomendação está agronomicamente certa. Ela afirma que é rastreável, aterrada nos dados, coerente com os limiares declarados e comparável entre motores — condições necessárias para que um agrônomo a revise com confiança.

## 5. Limitações — ditas antes que perguntem

- **Sem previsão do tempo.** A fonte de previsão é roadmap; o pacote traz `previsao_chuva_72h_mm = null` e o prompt manda dizer isso quando pesa na decisão. Recomendar irrigar às vésperas de uma chuva prevista é o erro que essa fonte evitaria.
- **Aterramento por lista de números tolera coincidência.** Um número inventado que por acaso existe no pacote passa. Mitigação: `dados_usados` por recomendação — o modelo precisa dizer *de onde* veio.
- **Limiares fixos por cultura.** `agronomia.py` é uma tabela, não um modelo de cultura calibrado; a equipe agronômica revisa e o efeito propaga a simulador, gold e IA de uma vez.
- **Custo por chamada e latência.** Um Context Pack tem ~10 KB; três casos × seis talhões por dia é barato, mas uma fazenda com centenas de talhões pede cache do pacote e chamada só quando o balanço muda.
- **Concordância não é acerto.** A avaliação mede se o modelo concorda com as regras, não se qualquer dos dois acertou — para isso seria preciso o resultado da safra. Discordância documentada é o ponto de partida para a revisão agronômica, não um defeito.
- **O modelo não vê imagens.** O NDVI entra como número calculado a partir da cena; a cena em si fica no bronze.
