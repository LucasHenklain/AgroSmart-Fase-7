# Roteiro do vídeo — 2 min 40 s, falado ao avaliador

Gravar em blocos, um take por bloco, sem edição elaborada. Tela gravada + voz. Salvar o mp4
nesta pasta `entrega/` (entra no zip); como rede de segurança, subir no YouTube como "não
listado" e colar o link em `LINK_VIDEO.txt`. Os números falados são os da execução de
referência — não arredondar para cima.

## Antes de gravar: deixe estas cinco coisas abertas

| # | O que | Onde |
|---|---|---|
| 1 | Dashboard, talhão TLH-02, seção Água | navegador: `http://localhost:8501/?talhao=TLH-02&secao=agua` (subir com `streamlit run app/dashboard.py` se não estiver no ar) |
| 2 | Relatório do copiloto em PDF | arquivo `outputs\relatorios\irrigacao\TLH-02_2026-09-01.pdf` (abrir no leitor de PDF ou arrastar para o navegador) |
| 3 | Console do MinIO, logado, bucket `agrosmart-bronze` | navegador: `http://localhost:9001` → Buckets → `agrosmart-bronze` → `sensores` → `ano=2026` → `mes=08` → `dia=28` |
| 4 | Canvas do modelo de negócio | arquivo `docs\diagramas\canvas-fase5.svg` (arrastar para o navegador) |
| 5 | Projeto aberto no editor com o `README.md` na tela (seção Equipe) | VS Code ou Explorer na pasta do projeto |

Ordem de uso na gravação: 1 → 2 → 3 → 1 (seção Qualidade) → 4 → 1 (seção Benchmark) → 4 → 5.

## Blocos

### Bloco 1 · 0:00–0:20 · O problema

**Tela:** dashboard, TLH-02, seção **Água**. Passe o mouse pela linha azul até onde ela cruza a linha tracejada de 0,50.

**Fala:** "Somos a equipe AgroSmart, turma 4ESOA. Este gráfico é a fração de água disponível no solo do talhão TLH-02, um milho em pendoamento. Quando a linha cruza o limiar de 0,50, o talhão pede irrigação. Nesse estádio, cada dia de estresse hídrico custa 2 % do potencial de produção — neste talhão, R$ 2.591 por dia. Hoje essa decisão é tomada no olho, com dados espalhados entre sensores, planilhas, imagens e laudos."

### Bloco 2 · 0:20–0:50 · A solução

**Tela:** o PDF `TLH-02_2026-09-01.pdf`. Mostre o título, a linha "Confiança: alta", a recomendação "Irrigar 22,5 mm" e, ao fim, a lista de dados usados.

**Fala:** "Esta é a recomendação que o nosso copiloto de IA gerou para esse talhão: irrigar 22,5 mm na próxima madrugada, ao custo de R$ 404,55, com confiança alta. Cada número tem origem. A lâmina e o custo foram calculados pelo Data Lake; o copiloto só cita o que está no contexto que recebeu, e a lista de dados usados fica no rodapé. Se o modelo inventar um número, ou recomendar irrigar um talhão de sequeiro, os guardrails barram a resposta e um motor de regras responde no lugar. A IA recomenda; o produtor decide."

### Bloco 3 · 0:50–1:15 · Por baixo

**Tela:** console do MinIO na pasta `sensores/ano=2026/mes=08/dia=28` (os 96 lotes do dia). Depois volte ao dashboard e clique na seção **Qualidade**.

**Fala:** "Por baixo existe um Data Lake em quatro camadas, rodando em MinIO. O bronze guarda o dado bruto exatamente como veio da origem — aqui, os 96 lotes de um dia de sensores, cada um com o seu manifest de proveniência. O silver aplica 58 regras de qualidade declaradas em contratos por fonte: 34.208 registros aprovados, 405 na quarentena com o motivo, e os 406 defeitos que injetamos de propósito foram todos localizados. O gold calcula os indicadores agronômicos que o copiloto e este painel leem. Tudo isso roda com um comando."

### Bloco 4 · 1:15–1:50 · O modelo de negócio

**Tela:** o Canvas. Percorra com o cursor: Proposta de valor → Segmentos de clientes → Fontes de receita.

**Fala:** "No modelo de negócio, o dado deixou de ser subproduto do sensor e virou o ativo. A proposta de valor é decidir com custo e benefício explícitos, não apenas monitorar. O cliente principal é o produtor irrigado de 10 a 500 hectares, e chegamos a ele pelas cooperativas, que reduzem o custo de aquisição. A receita é recorrente por hectare-safra, com sensores como serviço e créditos de IA para casos avançados, como o fitossanitário."

### Bloco 5 · 1:50–2:20 · Os dados como ativo

**Tela:** dashboard, seção **Benchmark**. Aponte a coluna `publicavel`.

**Fala:** "A monetização tem três degraus. Interno: o dado reduz o próprio custo — a irrigação na hora certa, a bateria trocada antes de o sensor calar. Produto: o copiloto e o painel, cobrados por hectare. Externo: este benchmark regional para seguradoras e bancos — agregado por cultura, sem identificar talhão, fazenda ou pessoa, e publicável só com pelo menos três talhões e consentimento registrado. Nesta amostra nenhum grupo é publicável, e o lake diz isso em vez de esconder. A LGPD aqui é regra dentro do dado, não anexo jurídico."

### Bloco 6 · 2:20–2:40 · Parcerias e próximos passos

**Tela:** Canvas, bloco Parcerias-chave; feche no editor com o `README.md` (seção Equipe) na tela.

**Fala:** "Nossos parceiros: cooperativas, fabricantes de sensores, Embrapa para calibrar os limiares agronômicos, bancos e seguradoras no terceiro degrau. O próximo passo é uma safra completa com produtores reais, medindo a taxa de aceite das recomendações — a métrica que diz se a IA está ajudando de verdade. Todo o código, os documentos e as evidências estão no repositório. Obrigado."

## Regras de ouro

- Mostrar a tela real, não slide da tela. O avaliador precisa ver que existe.
- Nunca dizer "a IA decide". Dizer "a IA recomenda, o produtor decide".
- Se passar de 3 minutos, encurtar o bloco 3 para 15 segundos; não cortar o Canvas.
- Errou uma frase: regrava só aquele bloco.
