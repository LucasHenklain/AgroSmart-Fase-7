# Requisito 6 — Pitch e visão de produto

> Peso: 10 % · Artefatos: slides (`entrega/pitch/slides-pitch-agrosmart.pdf`) + vídeo pitch de até 5 min
> (`entrega/pitch-agrosmart-fase7-4ESOA.mp4`). Números de operação vêm da plataforma rodando
> (`docs/fase7/02`–`05`); números de mercado e preço estão marcados como **hipótese** a validar.

## 1. Problema

O produtor irrigado de pequeno e médio porte decide **quando e quanto irrigar** com base em
experiência, previsão do tempo e, quando tem, um painel de sensores que mostra gráficos — mas
não diz o que fazer. As consequências:

- **água e energia** gastas na hora errada (irrigar talhão que ainda tem água disponível);
- **perda de produtividade** quando o estresse hídrico chega num estádio crítico (na referência
  simulada da Fase 5, um dia de estresse do TLH-02 em VT custa R$ 2.591,60);
- **dados espalhados** — sensores, ERP de operações, laudos de solo, imagens — sem qualidade medida,
  então ninguém confia o bastante para automatizar;
- **ferramentas que não operam sozinhas** e sem garantia de que estão funcionando agora.

## 2. Solução

**AgroSmart é a plataforma que transforma o dado do campo em decisão e ação de irrigação, com
evidência, sozinha e monitorada.**

| Passo | O que acontece | Prova |
|---|---|---|
| Coleta | sensores (ESP32/Wokwi ou rede LoRa/Wi-Fi) publicam por MQTT a cada 5 s | gateway gravando a landing em tempo real |
| Confiança | data lake com contrato por fonte, 58 regras de qualidade, quarentena com motivo, LGPD | 99 %+ dos registros aprovados; reprovados guardados com a regra |
| Decisão | motor de regras agronômicas (+ copiloto de IA que explica) decide por talhão, com lâmina, minutos e evidência | `outputs/acoes/acoes_<data>.json` |
| Ação | comando ao atuador de irrigação (MQTT QoS 1); nada age sem autorização | comandos publicados e confirmados pelo broker |
| Operação | worker contínuo, alertas e painel; mineração de processos para melhorar o próprio ciclo | 445 ciclos reais, MTTR medido, ciclo 66 % mais rápido após a melhoria |

## 3. Diferencial competitivo

1. **Decisão explicável, não gráfico.** Cada ação cita a linha do gold que a motivou (tabela, chave,
   data) e os limiares usados; o copiloto de IA explica, com guardrails que impedem número inventado.
2. **Dado em que se pode confiar o bastante para automatizar.** Contratos, quarentena com motivo,
   reconciliação e linhagem por lote — o que concorrentes tratam como "importar planilha".
3. **Automação segura por desenho.** Trava do sequeiro em três camadas, publicação só com autorização,
   QoS 1 com confirmação.
4. **Plataforma que se mede e melhora.** Monitoramento (Prometheus/Grafana, 6 alertas) e mineração de
   processos sobre o próprio ciclo: a primeira análise já cortou o ciclo de 30,3 s para 10,3 s.
5. **Engenharia de produto.** Uma imagem não-root escaneada, pipeline DevSecOps com gates; roda local,
   na cooperativa ou em qualquer nuvem (mesmo código, troca de endpoint).
6. **Custo marginal baixo.** Stack aberta (MinIO, Mosquitto, DuckDB, Polars); o custo está no sensor e no
   suporte, não no software.

Alternativas que o produtor usa hoje (categorias, não marcas): planilha + experiência; plataformas de
telemetria de sensores (mostram dados, não decidem); ERPs agrícolas (registram operações, não
monitoram o campo); consultoria agronômica (decide bem, mas não 24 h e não em escala).

## 4. Público-alvo

| Segmento | Por que agora | Papel |
|---|---|---|
| **Produtor irrigado de 10–500 ha** (pivô, gotejo, aspersão) | água e energia são os maiores custos variáveis do irrigado | cliente principal (assinatura) |
| **Cooperativas agrícolas** | precisam de visão da safra dos cooperados e de canal de assistência técnica | canal de venda e cliente B2B (API, painel agregado) |
| **Consultorias agronômicas** | um agrônomo atende dezenas de fazendas | usuário profissional (relatórios prontos para validar) |
| Seguradoras e bancos de crédito rural | precisam precificar risco climático | dado agregado e anonimizado, com consentimento (fase seguinte) |

**Persona.** *Sr. Antônio, 52 anos, 180 ha com dois pivôs em soja e milho safrinha, cooperado.* Decide a
irrigação pela manhã olhando o tempo e "o jeito da planta"; perdeu produtividade numa estiagem em
pendoamento; desconfia de app que "só mostra gráfico".

## 5. Proposta de valor

> **"Irrigar na hora certa, na dose certa, com o motivo escrito — e saber que o sistema está de pé."**

| Dores do produtor | Como o AgroSmart alivia | Ganhos criados |
|---|---|---|
| não sabe se precisa irrigar hoje | risco por talhão + ação com lâmina e minutos | economia de água e energia (**hipótese** a medir no piloto: 10–20 %) |
| estresse em estádio crítico passa despercebido | alerta de prioridade alta quando a água disponível cai abaixo do limiar | perda evitada (R$ por dia de estresse, calculado no gold) |
| não confia em dado de sensor | qualidade medida, sensor com bateria baixa ou sinal fraco vira ação de manutenção | rede de sensores confiável |
| sistema caiu e ninguém viu | alertas operacionais e painel de saúde | tranquilidade, SLA |

## 6. Modelo de negócio (resumo; detalhes em `docs/04-modelo-de-negocio.md`)

| Receita | Unidade | Faixa (**hipótese**) |
|---|---|---|
| SaaS de decisão (dashboard, automação, copiloto) | hectare-safra | R$ 25–40 |
| Sensores como serviço (instalação + manutenção) | talhão-mês | R$ 300–400 |
| API e painel para cooperativas/consultorias | assinatura | por cooperado ativo |
| Dado agregado e anonimizado (k ≥ 3, consentimento) | assinatura anual | fase 2 |

Régua de venda: um dia de estresse evitado no TLH-02 (R$ 2.591,60, referência simulada) paga várias safras
da assinatura do talhão de 12,4 ha.

## 7. Tração e próximos passos

- **Pronto (evidência no pacote):** plataforma integrada em containers, ciclo autônomo, 445 ciclos
  observados, pipeline DevSecOps aprovado, análise de processo com melhoria aplicada.
- **Próximos 90 dias:** piloto com uma cooperativa (2–3 fazendas irrigadas) para medir aceite das
  recomendações e economia real de água; rede LoRa no lugar do Wi-Fi; partição horária no silver;
  idempotência da atuação; registro de consentimento LGPD como dado.
- **Métrica norte:** hectares sob recomendação ativa; **métrica de valor:** % de recomendações executadas.

## 8. Roteiro do pitch (até 5 min)

| Tempo | Slide | Mensagem |
|---|---|---|
| 0:00–0:25 | Capa | AgroSmart: do sensor à irrigação certa, com evidência |
| 0:25–1:05 | Problema | decide-se no olho; água, energia e produtividade em jogo; dado espalhado |
| 1:05–1:45 | Solução | coletar → confiar → decidir → agir → operar |
| 1:45–2:35 | Como funciona (telas reais) | dashboard, ação com evidência, painel de operação |
| 2:35–3:15 | Diferencial | explicável, confiável, seguro, que se mede e melhora |
| 3:15–3:50 | Público e proposta de valor | irrigado 10–500 ha via cooperativas; persona |
| 3:50–4:25 | Modelo de negócio | hectare-safra, sensores como serviço, API |
| 4:25–4:55 | Tração e chamada | o que está pronto e o piloto que pedimos |
