# Pitch AgroSmart — roteiro falado (apresentação ao vivo, até 5 min)

Deck: `slides-pitch-agrosmart.pdf` (ou `.pptx`), 9 slides. Fontes dos números: `docs/fase7/06-pitch-visao-produto.md`
e `docs/fase7/05-analise-de-processos.md`. O que o doc marca como **hipótese** deve ser dito como hipótese.
Tempos de referência ≈ 1 slide a cada 30–40 s; o vídeo narrado (`../pitch-agrosmart-fase7-4ESOA.mp4`) segue este texto.

## 1. Capa · 0:00–0:20
Somos a equipe AgroSmart, turma 4ESOA da FIAP. Nosso produto em uma frase: do sensor à irrigação certa, na hora
certa e na dose certa, com o motivo escrito e com evidência. Em cinco minutos: o problema, a solução funcionando
e o piloto que propomos.

## 2. Problema · 0:20–1:00
O produtor irrigado de pequeno e médio porte decide quando e quanto irrigar pela experiência, pela previsão do
tempo e, quando tem, por um painel que mostra gráficos, mas não diz o que fazer. Resultado: água e energia gastas
na hora errada; produtividade perdida no estádio crítico — na referência simulada da Fase 5, **um dia de estresse
do TLH-02 em VT custa R$ 2.591,60**; dados espalhados, sem qualidade medida; ferramentas que não operam sozinhas.

## 3. Solução · 1:00–1:45
AgroSmart transforma o dado do campo em decisão e ação de irrigação, com evidência, sozinho e monitorado.
Cinco passos: **coletar** (MQTT a cada 5 s), **confiar** (contrato por fonte, 58 regras de qualidade, quarentena
com motivo, 99 %+ aprovado), **decidir** (regras agronômicas + copiloto de IA: lâmina e minutos por talhão),
**agir** (comando ao atuador com QoS 1, nada age sem autorização), **operar** (worker contínuo, alertas, painel).
*Apontar o diagrama de arquitetura à direita.*

## 4. Como funciona — telas reais · 1:45–2:25
Isto não é maquete. No dashboard: talhões, registros aprovados e em quarentena, risco por talhão e cada ação com
motivo, parâmetros e evidência (tabela, chave e data do gold) — o que o produtor vê é o que a automação leu.
No Grafana: saúde do worker e do lake, ciclos ok e com erro, duração por etapa, comandos publicados.
No Prometheus: alertas operacionais.

## 5. Diferencial · 2:25–3:10
Decisão explicável, não gráfico; dado confiável o bastante para automatizar; automação segura por desenho
(trava do sequeiro em três camadas); plataforma que se mede e melhora — **a mineração de processos sobre 445
ciclos reais cortou o ciclo de 30,3 s para 10,3 s (66 % mais rápido)**; stack aberta, custo marginal baixo.

## 6. Público-alvo e persona · 3:10–3:40
Cliente principal: produtor irrigado de **10 a 500 ha** (pivô, gotejo, aspersão). Canal e cliente B2B:
cooperativas; usuário profissional: consultorias. Seguradoras e crédito rural ficam para a fase seguinte.
Persona: **Sr. Antônio, 52 anos, 180 ha, dois pivôs**, soja e milho safrinha; perdeu produtividade numa estiagem
no pendoamento e desconfia de app que "só mostra gráfico".

## 7. Proposta de valor · 3:40–4:00
"Irrigar na hora certa, na dose certa, com o motivo escrito — e saber que o sistema está de pé."
Risco por talhão com lâmina e minutos; alerta de prioridade alta no estádio crítico; sensor ruim vira manutenção.
Economia de água e energia de **10–20 % — hipótese** a medir no piloto.

## 8. Modelo de negócio · 4:00–4:25
Faixas são **hipótese**: SaaS de decisão R$ 25–40 por hectare-safra; sensores como serviço R$ 300–400 por
talhão-mês; API/painel para cooperativas por cooperado ativo; dado agregado e anonimizado na fase 2.
Régua de venda: um dia de estresse evitado no TLH-02 paga várias safras da assinatura do talhão de 12,4 ha.

## 9. Tração e chamada · 4:25–4:55
Pronto, com evidência: plataforma em containers, **445 ciclos** autônomos, pipeline DevSecOps aprovado, melhoria de
processo aplicada. Próximos 90 dias: **piloto com uma cooperativa (2–3 fazendas irrigadas)** para medir aceite e
economia real de água. Métrica norte: hectares sob recomendação ativa. **Chamada:** procuramos essa cooperativa
parceira. AgroSmart: do sensor à irrigação certa, com evidência. Obrigado.

## Regerar
```
python gerar_audio.py                                   # narração (edge-tts, pt-BR-AntonioNeural, +14 %)
python gerar_deck.py                                    # PNG -> PDF + PPTX
python build_video.py roteiro.json ..\pitch-agrosmart-fase7-4ESOA.mp4
```
