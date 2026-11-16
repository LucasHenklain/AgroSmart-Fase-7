# Checklist da rubrica → artefato · Fase 6

**Equipe (turma 4ESOA):** Lucas Dalmas RM551178 · Lucas Emanuel RM97881 · Kayque Moraes RM97592 ·
Lucas Henklain RM99350 · Vinicius Pinheiro RM99198.
**Zip:** `lucasmoraeshenklaindasilva_99350_4ESOA_fase6_atividade.zip` · **prazo:** sábado 10/10/2026

Uso: antes de empacotar, percorrer a lista e confirmar que cada item aponta para algo que existe.
`agrosmart empacotar --simular` lista o que entra; os avisos apontam o que falta (link do vídeo,
prints `fase6-*`, `outputs/acoes/`, `firmware/`).

```bash
agrosmart empacotar --nome "Lucas Moraes Henklain da Silva" --rm 99350 --turma 4ESOA --fase 6
```

Legenda: ✅ pronto · ⬜ pendente (quem) · ⚠ atenção

## Requisito 1 · Arquitetura integrada (20 %)

| Critério | Onde está | Estado |
|---|---|---|
| Diagrama da arquitetura ponta a ponta (dispositivos → broker → gateway → lake → automação → consumo) | `docs/diagramas/arquitetura-fase6.svg` e `.png` (gerados por `scripts/gerar_diagrama_fase6.py`) | ✅ |
| Breve descrição de cada componente (responsabilidade, tecnologia, onde está no código, fase) | `docs/fase6/01-arquitetura-integrada.md` §3 | ✅ |
| Interfaces entre os blocos (tópicos MQTT, landing, Parquet, JSON do plano) | `docs/fase6/01` §4 | ✅ |
| O que mudou da Fase 5 e como reproduzir | `docs/fase6/01` §6–§7; `README.md` seção Fase 6 | ✅ |
| PDF do documento | `docs/pdf/fase6/01-arquitetura-integrada.pdf` (`scripts/exportar_pdf.py`) | ✅ |

## Requisito 2 · Coleta de dados IoT (20 %)

| Critério | Onde está | Estado |
|---|---|---|
| Firmware do ESP32 (Wokwi) lendo sensores e publicando por MQTT | `firmware/wokwi-esp32/` (`sketch.ino`, `diagram.json`, `wokwi.toml`, `libraries.txt`, `README.md`) | ✅ (presente) |
| Broker MQTT na infraestrutura | `infra/docker-compose.yml` (serviço `mosquitto`), `infra/mosquitto/mosquitto.conf` | ✅ (presente) |
| Gateway que materializa a landing | `src/agrosmart/ingestao/mqtt_gateway.py` · `agrosmart coletar-mqtt` · `outputs/coleta_mqtt.json` | ✅ (código presente) |
| Emulador dos dispositivos (sem placa, mesmo payload) | `src/agrosmart/simuladores/dispositivo_mqtt.py` · `agrosmart dispositivo` | ✅ (código presente) |
| Documento da coleta (circuito, tópicos, payload, execução) | `docs/fase6/02-coleta-iot.md` (+ PDF em `docs/pdf/fase6/`) | ✅ |
| Dados coletados de exemplo | `data/exemplos/fase6/landing/sensores/` (emulador) e `.../wokwi_hivemq/` (ESP32 no Wokwi via broker público) | ✅ |
| Captura do Wokwi em execução | `firmware/wokwi-esp32/captura-wokwi.png` (gerada por `capturar_wokwi.py`: código + circuito + simulação rodando) | ✅ |
| Prova de que o ESP32 simulado publicou de verdade | `outputs/prints/fase6-02-wokwi-hivemq.txt` — gateway desta máquina recebeu as leituras pelo broker.hivemq.com (valores do DHT22, sonda, bateria) | ✅ |
| Prints da demonstração local (emulador + gateway + ingestão + silver) | `outputs/prints/fase6-02-*.txt` | ✅ |

## Requisito 3 · Processamento e preparação (20 %)

| Critério | Onde está | Estado |
|---|---|---|
| Fluxo documentado landing → bronze → silver → gold com scripts e caminhos reais | `docs/fase6/03-processamento.md` §1 | ✅ |
| Scripts de processamento | `src/agrosmart/ingestao/`, `processamento/{leitura,tipagem,silver,gold}.py`, `qualidade/motor.py` | ✅ (Fase 5, inalterados) |
| Dataset processado de exemplo (silver e gold, extraídos do lake real) | `data/exemplos/fase6/processado/sensores_leitura_amostra.csv` (48 leituras) · `fato_leitura_diaria_talhao.csv` (186 linhas) | ✅ |
| Qualidade, quarentena e deduplicação explicadas | `docs/fase6/03` §3 | ✅ |
| Tempos medidos | `docs/fase6/03` §4 (`outputs/run.json`, `outputs/dq_report.json`) | ✅ |
| PDF | `docs/pdf/fase6/03-processamento.pdf` | ✅ |

## Requisito 4 · Automação inteligente (20 %)

| Critério | Onde está | Estado |
|---|---|---|
| Motor de decisão sobre o gold (risco por talhão, ações com motivo e evidências) | `src/agrosmart/automacao/motor.py` · `agrosmart automacao` | ✅ (código presente) |
| Atuação: comando MQTT ao atuador com QoS 1, `--publicar` explícito | `src/agrosmart/automacao/atuadores.py` | ✅ (código presente) |
| Plano de ações gerado de uma execução real | `outputs/acoes/acoes_2026-09-01.json` e `.md` (3 ações; `irrigar` TLH-02 publicada via MQTT) | ✅ |
| Documento da automação (regras, limiares, esquema do JSON, segurança) | `docs/fase6/04-automacao-inteligente.md` · PDF em `docs/pdf/fase6/` | ✅ |
| Testes | `tests/test_automacao.py` | ✅ (presente) |

## Requisito 5 · Dashboard analítico (20 %)

| Critério | Onde está | Estado |
|---|---|---|
| Arquivo do dashboard | `app/dashboard.py` (`streamlit run app/dashboard.py`) | ✅ |
| Indicadores do ambiente (umidade, temperatura, bateria, sinal, cobertura) | seções Talhão, Sensores; `docs/fase6/05-dashboard.md` §2 | ✅ |
| Evolução no tempo (balanço hídrico, NDVI, custos, cobertura diária) | seções Água, Vegetação, Custos, Sensores | ✅ |
| Alertas e recomendações (qualidade, copiloto, **ações da automação**) | seções Qualidade, Copiloto, **Automação** (nova) | ✅ |
| Capturas de tela de cada seção | `outputs/prints/fase6-05-dashboard-*.png` (`scripts/capturar_dashboard.py --prefixo fase6-05-dashboard`) | ✅ |
| Descrição de cada indicador | `docs/fase6/05-dashboard.md` | ✅ |
| Captura da aba Automação com o JSON **real** de `outputs/acoes/` | `outputs/prints/fase6-05-dashboard-automacao.png` (viewport 1440×1800, `--altura 1800`) | ✅ |
| PDF | `docs/pdf/fase6/05-dashboard.pdf` | ✅ |

## Requisito 6 · Vídeo (3–5 min)

| Critério | Onde está | Estado |
|---|---|---|
| Roteiro de 4 min (visão geral, arquitetura, coleta ao vivo, automação, dashboard) | `entrega/roteiro-video-fase6.md` | ✅ |
| Vídeo gravado | `entrega/apresentacao-agrosmart-fase6-4ESOA.mp4` (até 100 MB entra no zip) | ⬜ **você** |
| Link | `entrega/LINK_VIDEO.txt` (hoje `[A PREENCHER]`) | ⬜ **você** |

## Empacotamento

| Item | Como | Estado |
|---|---|---|
| Nome `nomecompleto_rm_turma_fase6_atividade.zip` | `agrosmart empacotar … --fase 6` valida e gera | ✅ |
| Conteúdo da Fase 6 | `firmware/`, `docs/fase6/`, `outputs/acoes/`, `data/exemplos/fase6/`, prints `fase6-*`, mais tudo da Fase 5 | ✅ (`src/agrosmart/empacotar.py`, testado em `tests/test_empacotar.py`) |
| Vídeo da Fase 5 fora do zip da Fase 6 · vídeo > 100 MB fora, com aviso | regra por nome (`fase5`) e por tamanho em `empacotar.py` | ✅ |
| Zip testado e inventariado | `zipfile.testzip` + `entrega/CONTEUDO_DO_ZIP.txt` | ✅ |
| PDFs dos documentos novos | `docs/pdf/fase6/{01,02,03,04,05}.pdf` por `python scripts/exportar_pdf.py` | ✅ |
| Lint e testes | `ruff check src tests app` · `python -m pytest -q` | ver relatório final |
| Upload até 10/10/2026 | manual | ⬜ **você** |
