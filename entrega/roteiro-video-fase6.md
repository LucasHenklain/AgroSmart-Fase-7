# Roteiro do vídeo — Fase 6 · 4 min, falado ao avaliador

> **Como o vídeo foi produzido (08/10/2026):** os seis blocos abaixo viraram `entrega/apresentacao-agrosmart-fase6-4ESOA.mp4`,
> montado em `entrega/video_fase6/` a partir das evidências reais (captura do Wokwi, saídas do gateway, plano de ações,
> capturas do dashboard, diagrama) com narração sintetizada em português (`gerar_audio.py`) e legendas; `roteiro.json`
> é a fonte, `build_video.py` monta. Para regravar ao vivo, siga a lista de "sete coisas abertas" abaixo.

Gravar em blocos, um take por bloco, tela gravada + voz, sem edição elaborada. Salvar o mp4 em
`entrega/` como `apresentacao-agrosmart-fase6-4ESOA.mp4` (entra no zip se tiver até 100 MB) e,
como rede de segurança, subir no YouTube como "não listado" e colar o link em `LINK_VIDEO.txt`.
O limite da rubrica é 3–5 min: mirar em 4 min; se passar de 5, cortar o bloco 2 pela metade.
Os números falados são os da execução de referência — não arredondar para cima.

## Antes de gravar: deixe estas sete coisas abertas

| # | O que | Onde |
|---|---|---|
| 1 | Diagrama da arquitetura integrada | `docs\diagramas\arquitetura-fase6.svg` (arrastar para o navegador) |
| 2 | Wokwi com o projeto do ESP32 carregado e **parado** | navegador: projeto de `firmware/wokwi-esp32/` (ver `docs/fase6/02-coleta-iot.md`) |
| 3 | Terminal A: broker no ar e gateway pronto para rodar | `docker compose -f infra/docker-compose.yml up -d` já executado; comando digitado, sem Enter: `agrosmart coletar-mqtt --duracao-s 90` |
| 4 | Terminal B: emulador pronto | comando digitado, sem Enter: `agrosmart dispositivo --duracao-s 90` |
| 5 | Terminal C: pipeline e automação | `agrosmart ingerir && agrosmart processar` já rodado hoje; comando digitado: `agrosmart automacao --publicar` |
| 6 | Dashboard no ar | `streamlit run app/dashboard.py` → `http://localhost:8501/?talhao=TLH-02&secao=agua` |
| 7 | Explorer em `landing\sensores\<hoje>\` | para mostrar o JSONL aparecendo |

Ordem de uso: 1 → 2 + 3 + 4 + 7 → 5 (+ 2 para ver o LED) → 6 → 1.

## Blocos

### Bloco 1 · 0:00–0:35 · Visão geral

**Tela:** o diagrama (item 1), inteiro. Passe o cursor da esquerda para a direita e depois pela seta tracejada de volta.

**Fala:** "Somos a equipe AgroSmart, turma 4ESOA. Na Fase 5 construímos um Data Lake e um copiloto de IA que recomenda. Na Fase 6 fechamos o ciclo: o dado nasce num ESP32, viaja por MQTT, passa pelas camadas bronze, silver e gold, e um motor de decisão transforma indicadores em ação — irrigar, alertar, trocar bateria, inspecionar sensor — que volta ao campo como comando para o atuador. Esta seta tracejada é a Fase 6. Tudo que vou mostrar está rodando nesta máquina."

### Bloco 2 · 0:35–1:10 · Arquitetura

**Tela:** ainda o diagrama; aponte cada bloco enquanto fala. Depois abra rapidamente `contratos/sensores.v1.yaml` no editor (topo do arquivo).

**Fala:** "Da esquerda para a direita: dispositivos — o ESP32 no Wokwi ou o emulador que publica o mesmo payload; o broker Mosquitto; o gateway `coletar-mqtt`, que materializa as leituras em arquivos de quinze minutos na landing; e daí o lake da Fase 5, sem uma linha alterada, porque o contrato da fonte de sensores é a fronteira: schema, regras de qualidade, deduplicação, retenção. No gold, a automação lê o balanço hídrico e a cobertura dos sensores, decide, grava o plano de ações e publica o comando MQTT. O dashboard mostra o mesmo gold e o mesmo plano."

### Bloco 3 · 1:10–2:10 · Coleta IoT ao vivo

**Tela:** Wokwi (item 2) → aperte *Play* e mostre o monitor serial publicando. Terminal A → Enter no `coletar-mqtt`. Terminal B → Enter no `dispositivo`. Volte ao terminal A: as mensagens chegando. Explorer (item 7): o arquivo `leituras_<hoje>T<hhmm>Z.jsonl` crescendo; abra-o e mostre duas linhas.

**Fala:** "Aqui o ESP32 lê umidade e temperatura do solo, temperatura e umidade do ar, bateria e sinal, e publica em `agrosmart/sensores/<id>/leitura`. O emulador faz o mesmo para os doze sensores do catálogo, com dois por cento de leituras atrasadas e meio por cento duplicadas, como um gateway de campo com buffer. O gateway assina o tópico, descarta chave fora do contrato, agrupa pela janela do instante medido e não grava a mesma chave natural duas vezes. O resultado é este JSONL — exatamente o formato que a Fase 5 já ingeria. A partir daqui é `agrosmart ingerir` e `agrosmart processar`: schema na porta, manifest, doze regras de qualidade, quarentena com motivo."

### Bloco 4 · 2:10–3:05 · Automação inteligente

**Tela:** Terminal C → Enter em `agrosmart automacao --publicar`. Mostre a tabela de talhões (risco, FAD, déficit, cobertura) e a tabela de ações (tipo, prioridade, comando MQTT, status `publicado`). Volte ao Wokwi: o LED/relé de irrigação do talhão acende e o monitor serial mostra o comando recebido. Abra `outputs\acoes\acoes_<data>.json` e role até uma ação com `evidencias`.

**Fala:** "O motor de decisão lê o gold e compara com os mesmos limiares do copiloto: fração de água disponível abaixo de 0,50 em talhão irrigado — irrigar, com lâmina de 85 % do déficit e os minutos de válvula calculados pela taxa do sistema; sete dias de umidade alta e temperatura na faixa de fungo — alerta fitossanitário; bateria abaixo de 15 % — trocar bateria; cobertura abaixo de 80 % — inspecionar o sensor. Cada ação tem motivo e evidência: a tabela, a chave e a data do gold que a justificam. Com `--publicar`, o comando sai por MQTT com QoS 1 e o status muda para publicado — e o ESP32 liga a irrigação. Sem o flag, o plano é só simulado: nada age no campo sem autorização, e o talhão de sequeiro nunca recebe irrigação, por construção."

### Bloco 5 · 3:05–3:45 · Dashboard analítico

**Tela:** dashboard (item 6). Seção **Água** do TLH-02 (linha da fração de água cruzando o limiar). Clique em **Sensores** (cobertura diária e situação de cada sensor). Clique em **Automação**: métricas, gráfico por tipo e prioridade, risco por talhão, tabela de ações; marque "só o talhão selecionado".

**Fala:** "O dashboard lê só o gold — o que o produtor vê é o que a automação leu. Água: balanço hídrico com os limiares de irrigação e estresse. Sensores: cobertura de leituras por dia; aqui se vê o sensor que calou cinco dias. E a aba nova, Automação: o plano mais recente, ações por tipo e prioridade, risco por talhão, cada ação com motivo, parâmetros e status. Alertas e recomendações saem do mesmo lugar."

### Bloco 6 · 3:45–4:00 · Fechamento

**Tela:** volte ao diagrama; feche no editor com o `README.md` (seção Equipe).

**Fala:** "Contrato antes de código, bronze imutável, dado ruim quarentenado com motivo, e agora: nada age sem evidência. Código, firmware, documentos e evidências estão no repositório. Obrigado."

## Regras de ouro

- Mostrar a tela real, não slide da tela. O avaliador precisa ver o LED acender e o JSONL aparecer.
- Se o Wokwi estiver instável no dia, gravar o bloco 3 só com o emulador e dizer "o ESP32 publica no mesmo tópico" — o roteiro continua verdadeiro.
- Nunca dizer "a IA decide". Dizer "o motor decide com evidência; o produtor autoriza a publicação".
- Errou uma frase: regrava só aquele bloco.
