# Checklist da rubrica — Fase 7 (entrega final)

Cada item do enunciado → artefato no pacote. Tudo foi executado nesta máquina; simulações estão rotuladas.

## 1. Arquitetura final (15 %)

| Exigido | Artefato |
|---|---|
| Diagrama com todos os componentes | `docs/diagramas/arquitetura-fase7.{svg,png}` (gerado por `scripts/gerar_diagrama_fase7.py`) |
| Integração entre serviços | `docs/fase7/01-arquitetura-final.md` §4 (interfaces, protocolos, garantias) · `infra/docker-compose.yml` |
| Fluxo de dados completo (entrada, processamento, saída) | `docs/fase7/01` §2 |
| Descrição dos componentes | `docs/fase7/01` §3 (tabela com container, porta, tecnologia, código) |

## 2. Execução da plataforma (20 %)

| Exigido | Artefato |
|---|---|
| Funcionamento completo e integrado | `docker compose -f infra/docker-compose.yml up -d --build` · `outputs/prints/fase7-02-compose-ps.txt` |
| Entrada IoT/simulação → processamento → automação → saída | `docs/fase7/02` §2 · worker `src/agrosmart/operacao/` · `outputs/plataforma/` |
| Evidências (prints, logs, execução) | `outputs/prints/fase7-02-*`, `fase7-04-*` · `outputs/plataforma/eventos.jsonl` (13.733 eventos) · vídeo final |
| Exemplo de entrada e saída | `docs/fase7/02` §3–4 · `outputs/prints/fase7-02-entrada-landing.jsonl.txt` · `outputs/plataforma/acoes/` · `fase7-02-saida-comandos-publicados.jsonl.txt` |

## 3. Pipeline DevSecOps (20 %)

| Exigido | Artefato |
|---|---|
| Build | estágio `build` (docker multi-stage) · `Dockerfile` |
| Execução via container | estágio `build` com smoke tests · `infra/docker-compose.yml` |
| Análise de qualidade de código | ruff + formatação + complexidade · cobertura 84,9 % (execução final; 85,1 % na 1ª aprovada) · SonarCloud opcional (`sonar-project.properties`) |
| Análise de vulnerabilidades | bandit (SAST) · pip-audit (SCA) · trivy (imagem) |
| Arquivos de configuração | `Dockerfile`, `.dockerignore`, `infra/docker-compose.yml`, `.github/workflows/devsecops.yml`, `.github/dependabot.yml` |
| Descrição | `docs/fase7/03-pipeline-devsecops.md` |
| Evidência de execução | `outputs/pipeline/<execução>/` (logs e artefatos por estágio) · `outputs/pipeline/ultimo/resumo.md` · execução reprovada anterior guardada como "antes" |

## 4. Monitoramento e execução automatizada (10 %)

| Exigido | Artefato |
|---|---|
| Execução contínua | worker com ciclo de 60 s, `restart: unless-stopped`, healthcheck, init · ≈ 24 h em operação |
| Monitoramento (logs, status, indicadores) | `/health`, `/metrics`, `/status` · Prometheus + 6 alertas · Grafana provisionado · trilha JSONL · aba Operação |
| Prints/logs do sistema rodando | `outputs/prints/fase7-04-*.png` (Grafana 1 h e 24 h, Prometheus, worker, dashboard) · `fase7-04-alerta-*` · `timelapse/` |
| Descrição do monitoramento e como acompanhar | `docs/fase7/04-monitoramento.md` (§5 runbook) |

## 5. Análise de processo + demonstração final (25 %)

| Exigido | Artefato |
|---|---|
| Funcionamento ponta a ponta | vídeo final · `docs/fase7/02` |
| Representação do processo | DFG `outputs/processos/dfg_{frequencia,desempenho}.{svg,png}` · árvore Inductive Miner, Petri/BPMN (`docs/fase7/05` §3) |
| Mineração de processos: gargalos, variações, melhorias | `docs/fase7/05` §4–7 · `outputs/processos/relatorio.md`, `metricas.json` |
| Exemplo de event log | `data/exemplos/fase7/event_log_exemplo.csv` · `outputs/processos/event_log.{csv,xes}` |
| Análise com insights (e melhoria aplicada) | v1 × v2 medido: `outputs/processos/comparacao_justa_v1_v2.json` · `docs/fase7/05` §6 |
| Vídeo final 5–8 min | `entrega/apresentacao-agrosmart-fase7-4ESOA.mp4` (roteiro `entrega/video_fase7/roteiro.json`; telas reais em `entrega/video_fase7/telas/`) |

## 6. Pitch e visão de produto (10 %)

| Exigido | Artefato |
|---|---|
| Problema, solução, diferencial, público-alvo, proposta de valor | `docs/fase7/06-pitch-visao-produto.md` |
| Slides | `entrega/pitch/slides-pitch-agrosmart.pdf` |
| Vídeo pitch até 5 min | `entrega/pitch-agrosmart-fase7-4ESOA.mp4` |

## Pacote

`python -m agrosmart empacotar --nome "Lucas Moraes Henklain da Silva" --rm 99350 --turma 4ESOA --fase 7`
→ `entrega/lucasmoraeshenklaindasilva_99350_4ESOA_fase7_atividade.zip` (inventário em `entrega/CONTEUDO_DO_ZIP.txt`).
Documentos também em PDF: `docs/pdf/fase7/`.
