# Checklist da rubrica → artefato

**Equipe (turma 4ESOA):** Lucas Dalmas RM551178 · Lucas Emanuel RM97881 · Kayque Moraes RM97592 ·
Lucas Henklain RM99350 · Vinicius Pinheiro RM99198.
**Zip:** `lucasmoraeshenklaindasilva_99350_4ESOA_fase5_atividade.zip`

Uso: antes de empacotar, percorrer a lista e confirmar que cada item aponta para algo que
existe no zip. `agrosmart empacotar --simular` lista o que entra; os avisos do comando
apontam o que ainda falta (vídeo, prints, trechos pendentes).

```bash
agrosmart empacotar --nome "Lucas Moraes Henklain da Silva" --rm 99350 --turma 4ESOA
```

## Entrega 1 · Arquitetura de Data Lake (25 %)

| Critério | Onde está | Estado |
|---|---|---|
| Diagrama da arquitetura | `docs/diagramas/arquitetura-datalake.svg` (também embutido em `docs/01`) | ✅ |
| Explicação de cada camada (função, formato, partição, retenção, invariantes) | `docs/01-arquitetura-datalake.md` §3 | ✅ |
| Camada de consumo demonstrada | `app/dashboard.py`, `outputs/prints/passo5-dashboard-*.png` | ✅ |
| Governança: catálogo, linhagem, LGPD, segurança, observabilidade, ciclo de vida | `docs/01` §5, `docs/dicionario-de-dados.md`, `outputs/catalogo.json` | ✅ |
| Decisões justificadas | `docs/adr/0001–0010` | ✅ |
| Equivalência com nuvem | `docs/01` §6 | ✅ |
| Evidência de que existe (buckets, objetos) | `outputs/prints/passo2-*.txt`, `passo3-*.txt`, prints do console MinIO `passo2-minio-*.png` | ✅ |

## Entrega 2 · Ingestão, administração e manutenção (25 %)

| Critério | Onde está | Estado |
|---|---|---|
| Fluxo de ingestão (batch, streaming, API, watch-folder) | `docs/02-ingestao-e-manutencao.md` §2–§4, `src/agrosmart/ingestao/` | ✅ |
| Dados de exemplo | `data/exemplos/` (landing, bronze, manifests, quarentena) | ✅ |
| Contratos de dados como fonte única | `contratos/*.v1.yaml`, `agrosmart contratos` | ✅ |
| Qualidade: regras, quarentena, reconciliação, verificação contra defeitos injetados | `outputs/dq_report.json`, `docs/02` §6–§8 | ✅ |
| Administração: catálogo, dicionário, trilha de eventos | `agrosmart catalogo`, `outputs/logs/eventos.jsonl` (amostra em prints) | ✅ |
| Manutenção: compactação, retenção, versionamento, ILM | `agrosmart manter`, `infra/docker-compose.yml` (bootstrap), `docs/02` §9 | ✅ |
| Runbook de incidentes validado | `docs/runbook-operacao.md` INC-01…INC-05 | ✅ |
| Orquestração e backfill | `agrosmart run --all`, `run --from --to`, `outputs/run.json`, `outputs/prints/passo5-*.txt` | ✅ |

## Entrega 3 · IA generativa para apoio à decisão (25 %)

| Critério | Onde está | Estado |
|---|---|---|
| Exemplo de saída gerada | `outputs/relatorios/<caso>/<talhão>_<data>.{md,pdf}` — 18 saídas | ✅ |
| Como funciona (contexto, prompt, saída estruturada, guardrails, fallback) | `docs/03-ia-generativa.md` §1–§2, ADR 0009 | ✅ |
| Contexto usado é rastreável | `*.contexto.json` e `*.proveniencia.json` ao lado de cada relatório | ✅ |
| Prompts versionados | `src/agrosmart/ia/prompts/*.v1.md` | ✅ |
| Avaliação | `outputs/avaliacao_ia.json`, `outputs/relatorios/avaliacao_ia.md`, `docs/03` §4 | ✅ |
| Execução com o modelo generativo (concordância regras × modelo) | exige `ANTHROPIC_API_KEY` no `.env` → `agrosmart ia avaliar` | ⬜ opcional |

## Entrega 4 · Modelo de negócio e inteligência de dados (25 %)

| Critério | Onde está | Estado |
|---|---|---|
| Canvas atualizado | `docs/diagramas/canvas-fase5.svg`, `docs/04-modelo-de-negocio.md` §2 | ✅ |
| Escada de monetização / Data Monetization Canvas | `docs/04` §3 | ✅ |
| LGPD e consentimento no modelo | `docs/04` §4 | ✅ |
| Métricas do negócio orientado a dados | `docs/04` §5 | ✅ |
| Vídeo de 2–3 min | `entrega/apresentacao-agrosmart-fase5-4ESOA.mp4` (dentro do zip); link opcional em `entrega/LINK_VIDEO.txt`. Duração gravada: 6 min 39 s — acima do pedido; a equipe optou por manter | ⚠ |

## Empacotamento

| Item | Como | Estado |
|---|---|---|
| Nome do zip `nomecompleto_rm_turma_fase5_atividade.zip` | `agrosmart empacotar --nome … --rm … --turma …` valida e gera | ✅ |
| Zip testado e inventariado | `zipfile.testzip` + `entrega/CONTEUDO_DO_ZIP.txt` | ✅ |
| Sem venv, lake local, landing ou caches dentro do zip | lista `INCLUIR`/`EXCLUIR_*` em `src/agrosmart/empacotar.py` | ✅ |
| Nome, RM e turma no README | `README.md → Equipe` | ✅ |
| Vídeo dentro do zip | `entrega/apresentacao-agrosmart-fase5-4ESOA.mp4` · zip final com ~15 MB, abaixo do aviso de 100 MB | ✅ |
| Upload antes de 05/09/2026 | manual | ⬜ |
