# 03 — Pipeline DevSecOps (build, container, qualidade e vulnerabilidades)

> **Situação da evidência.** O pipeline roda nos **dois lugares**: no **GitHub Actions**
> do repositório público <https://github.com/LucasHenklain/AgroSmart-Fase-7> (execução
> `devsecops #2` aprovada, ver §5.4) e no **runner local** (`scripts/pipeline_local.py`), que
> executa os mesmos estágios com os mesmos critérios e grava logs e artefatos em `outputs/pipeline/`.

## 1. Objetivo

Garantir que nenhuma mudança chegue à `main` (nem à imagem que roda o worker, o gateway
e o dashboard) sem passar, de forma automática e repetível, por:

1. **Qualidade de código** — lint, formatação e complexidade ciclomática;
2. **Testes** — suíte pytest com cobertura mínima;
3. **SAST** — análise estática de segurança do código (bandit);
4. **SCA** — vulnerabilidades conhecidas nas dependências (pip-audit);
5. **Build em container** — imagem Docker multi-stage + smoke tests;
6. **Scan da imagem** — vulnerabilidades do SO e dos pacotes da imagem (Trivy);
7. **SonarCloud** (opcional) — painel de qualidade/segurança contínua.

Arquivos de configuração entregues:

| Arquivo | Papel |
|---|---|
| `Dockerfile` | imagem multi-stage, runtime não-root (UID 10001), sem pip, pacotes Debian atualizados |
| `.dockerignore` | contexto de build mínimo (sem `.git`, testes, dados, cache, segredos) |
| `infra/docker-compose.yml` | plataforma completa (MinIO, Mosquitto, worker, gateway, Prometheus, Grafana) |
| `.github/workflows/devsecops.yml` | pipeline no GitHub Actions |
| `.github/dependabot.yml` | atualização semanal de actions, dependências Python e imagem base |
| `scripts/pipeline_local.py` (+ `.ps1`/`.sh`) | runner local com os mesmos estágios; grava a evidência |
| `scripts/requisitos_auditoria.py` | fecho das dependências de runtime auditado pelo SCA |
| `pyproject.toml` (`[tool.bandit]`, `[tool.coverage.*]`) | configuração de SAST e cobertura; pisos de segurança das dependências |
| `.trivyignore`, `.pip-audit-ignore` | allowlists com motivo e prazo (hoje vazias) |
| `sonar-project.properties` | configuração do SonarCloud (opcional) |

## 2. Visão geral

```mermaid
flowchart LR
    gatilho([push / PR na main<br/>workflow_dispatch<br/>agenda semanal]) --> Q & T & S & D & B
    Q[qualidade<br/>ruff check · format · C901]
    T[testes<br/>pytest + cobertura]
    S[sast<br/>bandit]
    D[sca<br/>pip-audit]
    B[build<br/>docker buildx + smoke] --> I[imagem<br/>Trivy]
    T --> SC[sonarcloud<br/>opcional]
    T -. junit.xml / coverage.xml .-> A[(artifacts)]
    S -. bandit.sarif .-> CS[(Code Scanning)]
    I -. trivy.sarif .-> CS
```

Os cinco primeiros jobs rodam em paralelo; o scan depende do build (recebe a imagem por
artifact `docker save`) e o SonarCloud depende dos relatórios dos testes. Gatilhos: `push`
e `pull_request` na `main`, `workflow_dispatch` e `schedule` semanal (segunda, 06:17 UTC —
pega CVEs publicadas depois do último commit).

**Segurança do próprio pipeline**: `permissions: {}` no topo e permissão mínima por job
(`contents: read`; `security-events: write` só em SAST/scan para subir SARIF); `checkout`
com `persist-credentials: false`; versões das ferramentas fixadas (ruff 0.16.10,
bandit 1.9.4, pip-audit 2.10.1, `aquasec/trivy:0.76.2`); actions fixadas por versão e
atualizadas pelo Dependabot. O Trivy roda pela **imagem oficial com tag fixa**, não pela
`trivy-action`, para usar o mesmo comando do runner local e não depender de uma tag
de action mutável. `concurrency` cancela execuções antigas do mesmo ref.

## 3. Estágios

| Estágio | Ferramenta | O que verifica | Critério de falha | Artefatos |
|---|---|---|---|---|
| qualidade | ruff 0.16.10 | lint (E, F, I, UP, B) em `src tests app scripts`; formatação; complexidade McCabe em `src app scripts` | qualquer erro de lint; arquivo fora do padrão de formatação; função com complexidade > **30**. Funções > 20 são listadas como alerta (não reprovam) | log |
| testes | pytest + pytest-cov | suíte completa; cobertura de `agrosmart` (linhas + ramos) | teste falhando ou cobertura combinada < **70%** | `junit.xml`, `coverage.xml`, `cobertura.txt` |
| sast | bandit 1.9.4 | código que vai para a imagem (`src/`, `app/`); config em `[tool.bandit]` | qualquer achado de severidade **HIGH**. Relatório completo (todas as severidades) sempre gerado | `bandit.json`, `bandit.sarif` |
| sca | pip-audit 2.10.1 | fecho transitivo das dependências de runtime (`agrosmart[app,iot,ia]`), com as versões instaladas, contra o banco PyPI/OSV | **qualquer** vulnerabilidade conhecida não listada em `.pip-audit-ignore` | `requisitos-runtime.txt`, `pip-audit.json` |
| build | docker buildx | build multi-stage; usuário da imagem; smoke tests `versao`, `doctor` e `init + contratos` com `AGROSMART_BACKEND=filesystem`; ausência de pip e UID ≠ 0 em runtime | build quebrado, imagem com usuário root, ou smoke test com código ≠ 0 | log (tamanho e usuário da imagem no resumo) |
| imagem | Trivy 0.76.2 | pacotes do SO (Debian) e Python dentro da imagem | vulnerabilidade **CRITICAL com correção disponível** (`--ignore-unfixed`), salvo exceção em `.trivyignore` | `trivy.json`, `trivy.sarif` |
| sonarcloud | SonarQube Scan Action | bugs, code smells, hotspots, duplicação, cobertura (lê `coverage.xml`) | Quality Gate do SonarCloud | painel no SonarCloud |

Por que auditar o *fecho de runtime* no SCA, e não o ambiente inteiro: na máquina de
desenvolvimento o Python global tem centenas de pacotes alheios ao projeto (o `pip-audit`
do ambiente inteiro acusava 444 vulnerabilidades em 45 pacotes, quase todas fora do
AgroSmart). `scripts/requisitos_auditoria.py` percorre os `Requires-Dist` a partir de
`agrosmart[app,iot,ia]` e gera a lista `nome==versão` exatamente do que o projeto usa — no
CI (ambiente limpo) e localmente o resultado é comparável.

## 4. Segurança da imagem

- **Multi-stage**: o estágio `builder` cria o venv e instala o pacote; o `runtime` recebe
  só `/opt/venv`, `contratos/` e `app/` — sem compilador, sem código-fonte de testes, sem cache.
- **Sem pip em runtime**: `pip`, `setuptools` e `wheel` são removidos do venv e do Python
  base (o smoke test confirma `find_spec('pip') is None`).
- **Não-root**: usuário `agro` UID/GID 10001, `USER 10001:10001`, shell `nologin`
  (o build reprova se `Config.User` for vazio/root).
- **Pacotes do SO atualizados**: `apt-get upgrade` no runtime aplica as correções de
  segurança do Debian já publicadas (corrigiu o `zlib1g` apontado pelo Trivy) e limpa as
  listas do apt.
- **CA corporativa como build secret**: `--secret id=ca_extra,...` é montado só durante o
  `RUN` do pip e nunca entra numa camada; sem o secret o build usa as CAs públicas.
- **Contexto mínimo**: `.dockerignore` exclui `.git`, `tests`, `data`, `outputs`, `.env`,
  `*.pem`, `*.key`, `.cache` (banco do Trivy) e `.certs`.
- **Compose endurecido**: os serviços da aplicação herdam `security_opt:
  no-new-privileges:true` e `cap_drop: [ALL]`.

## 5. Evidência de execução (runner local)

Duas execuções completas foram mantidas como evidência — a primeira mostra o gate
**reprovando**; a segunda, depois das correções, **aprovando**. Números copiados de
`resumo.json` de cada pasta (nada estimado).

### 5.1 Antes das correções — `outputs/pipeline/20261117T114128/` (FALHOU)

| Estágio | Status | Números |
|---|---|---|
| qualidade | ❌ | `ruff check` e `ruff format --check` reprovaram (código novo de mineração/scripts em edição paralela); complexidade OK (3 funções > 20, nenhuma > 30) |
| testes | ✅ | 252 testes: 251 passaram, 1 pulado, 0 falhas; cobertura 80,39% linhas / 65,5% ramos (77% combinado) |
| sast | ✅ | 0 HIGH · 4 MEDIUM · 8 LOW (B101 ×3, B110, B310, B311 ×2, B405, B406, B608 ×3) |
| sca | ❌ | **92 vulnerabilidades em 8 pacotes** de 71 auditados (pillow 42, gitpython 34, python-multipart 6, urllib3 5, anyio 2, certifi 1, pygments 1, requests 1) |
| build | ✅ | imagem 291,5 MB, usuário `10001:10001`, smoke tests OK |
| imagem | ✅ | Debian 13.7 · 0 CRITICAL · 48 HIGH · 93 MEDIUM · 65 LOW · 5 UNKNOWN; corrigíveis: 1 HIGH + 1 MEDIUM (zlib1g) |

### 5.2 Depois das correções — `outputs/pipeline/20261117T134927/` (PASSOU)

Cópia do resumo em `outputs/pipeline/ultimo/`. Duração total 1.620,7 s (Windows 11,
Python 3.13.1; a máquina estava compartilhada com outras tarefas, por isso os tempos
longos).

| Estágio | Status | Duração (s) | Números |
|---|---|---:|---|
| qualidade | ✅ | 11,0 | lint e formatação limpos; complexidade máxima ≤ 30; 3 funções acima de 20 (alerta) |
| testes | ✅ | 684,9 | **262 testes, 262 passaram**, 0 falhas, 0 pulados; cobertura **85,1% linhas / 74,75% ramos** (83% combinado; mínimo 70%) |
| sast | ✅ | 167,5 | **0 HIGH** · 1 MEDIUM · 3 LOW; 5 achados justificados com `# nosec`; 15.980 linhas analisadas |
| sca | ✅ | 50,0 | **0 vulnerabilidades** em 66 dependências de runtime auditadas |
| build | ✅ | 464,4 | imagem **292,5 MB**, usuário `10001:10001`; smoke `versao` → `agrosmart 7.0.0 · python 3.13.18`; `doctor`, `init + contratos` OK; `uid 10001 pip False` |
| imagem | ✅ | 241,3 | Debian 13.7 · **0 CRITICAL** · 47 HIGH · 92 MEDIUM · 65 LOW · 5 UNKNOWN — **0 com correção disponível** em qualquer severidade |

### 5.3 Execução final, sobre o código entregue — `outputs/pipeline/20261118T110823/` (PASSOU)

Rodada no commit `14b3d16` (depois da correção do `/metrics`, do comando `processos`, do
empacotador `--fase 7` e dos testes de operação); é a que está em `outputs/pipeline/ultimo/`.
Duração total 1.684,5 s.

| Estágio | Status | Duração (s) | Números |
|---|---|---:|---|
| qualidade | ✅ | 3,9 | lint e formatação limpos; complexidade máxima ≤ 30; 3 funções acima de 20 (alerta) |
| testes | ✅ | 964,9 | **263 testes, 263 passaram**, 0 falhas, 0 pulados; cobertura **84,94 % linhas / 74,46 % ramos** (mínimo 70 %) |
| sast | ✅ | 44,7 | **0 HIGH** · 1 MEDIUM · 3 LOW; 5 `# nosec` justificados; 16.056 linhas analisadas |
| sca | ✅ | 57,4 | **0 vulnerabilidades** em 66 dependências |
| build | ✅ | 445,9 | imagem **292,5 MB**, usuário `10001:10001`, smoke tests OK |
| imagem | ✅ | 166,9 | Debian 13.7 · **0 CRITICAL** · 47 HIGH · 92 MEDIUM · 65 LOW · 20 UNKNOWN — **0 com correção disponível** |

### 5.4 Execução no GitHub Actions — `devsecops #2` (PASSOU)

<https://github.com/LucasHenklain/AgroSmart-Fase-7/actions/runs/44336326618> · disparada pelo
push do commit `0ad719a` na `main` em 18/11/2026 · **Success em 4 min 49 s** · print em
`outputs/prints/fase7-03-github-actions-run2.png`.

| Job | Resultado | Duração |
|---|---|---|
| Qualidade de código (ruff + complexidade) | ✅ | 12 s |
| Testes + cobertura | ✅ | 1 min 42 s |
| SAST (bandit) — SARIF enviado ao Code Scanning | ✅ | 25 s |
| SCA (pip-audit) | ✅ | 40 s |
| Build da imagem + smoke test | ✅ | 3 min 35 s |
| Scan da imagem (Trivy) — SARIF enviado ao Code Scanning | ✅ | 48 s |
| SonarCloud configurado? → SonarCloud (opcional) | pulado (sem `SONAR_TOKEN`) | — |

Artefatos publicados: `testes-relatorios` (junit + coverage.xml), `sast-bandit`, `sca-pip-audit`,
`imagem-ci` (278 MB), `imagem-trivy` e o registro do build. A execução #1 (push anterior) foi
**cancelada pelo próprio workflow** (`concurrency` com `cancel-in-progress`) quando o segundo push
chegou — comportamento esperado. No mesmo push o **Dependabot** rodou e abriu 5 PRs de atualização
das actions (`setup-buildx-action` 4, `upload-artifact` 7, `download-artifact` 8,
`codeql-action` 4, `sonarqube-scan-action` 8), cada um validado pelo mesmo pipeline. Avisos do run:
actions em Node.js 20 (resolvidos pelos PRs do Dependabot) e uma falha não bloqueante ao salvar o
cache do banco do Trivy.

As vulnerabilidades restantes da imagem são do Debian 13 **sem correção publicada**
(`--ignore-unfixed`); o gate só reprova CRITICAL corrigível e o agendamento semanal
refaz o scan para pegar as correções quando saírem. Localmente o `bandit.sarif` não é
gerado porque o formatador SARIF exige `bandit[sarif]` (o log registra o erro, que não é
gate); no CI ele é instalado.

## 6. Achados

### 6.1 Corrigidos

| Origem | Achado | Correção |
|---|---|---|
| bandit B101 | `assert` em produção em `automacao/atuadores.py`, `processamento/gold.py`, `processamento/manutencao.py` (some com `python -O`) | verificação explícita (`if ... is None: continue` / `raise TypeError` / `raise ValueError`) |
| pip-audit | 92 vulnerabilidades em pillow, gitpython, python-multipart, urllib3, anyio, certifi, pygments, requests | pisos no `pyproject.toml`: `requests>=2.33`, `Pillow>=13.0`, `urllib3>=2.8`, `certifi>=2026.10.18`, `pygments>=2.20`; extras `app`/`ia`: `gitpython>=3.2.1`, `python-multipart>=0.0.31`, `anyio>=4.14.2`; ambiente reinstalado com `pip install -e ".[dev,ia,app,iot]"` |
| Trivy | `zlib1g` (CVE-2026-85091 HIGH, CVE-2026-27171 MEDIUM) com correção no Debian | `apt-get upgrade` no estágio runtime do `Dockerfile` |

### 6.2 Justificados (falso positivo, com `# nosec` e motivo na linha)

| Arquivo | Regra | Motivo |
|---|---|---|
| `processamento/gold.py` (3 SQLs) | B608 SQL montada com f-string | só interpola constantes numéricas do próprio módulo (limiares agronômicos, k-anonimato); nenhuma entrada externa chega à SQL |
| `simuladores/base.py`, `simuladores/dispositivo_mqtt.py` | B311 `random` | geração de dados sintéticos reprodutíveis por semente; nunca uso criptográfico |

`.trivyignore` e `.pip-audit-ignore` existem com o formato (ID, motivo, prazo `exp:`)
mas **estão vazios**: nenhuma exceção foi necessária.

### 6.3 Pendentes (abaixo do gate, em arquivos de outras frentes)

Não reprovam o gate (nenhum é HIGH) e ficam em arquivos mantidos por outras frentes da
Fase 7; registrados para tratamento:

| Arquivo:linha | Regra | Sugestão |
|---|---|---|
| `src/agrosmart/pipeline.py:1304` | B310 (MEDIUM) `urlopen` | o esquema já é validado (`http`/`https`) na linha anterior: anotar `# nosec B310` com esse motivo |
| `src/agrosmart/mineracao/__init__.py:74` | B110 `try/except/pass` | registrar o erro em log (`logger.debug`) em vez de engolir |
| `src/agrosmart/mineracao/eventlog.py:441` | B405 import de `xml.etree` | se só *gera* XML (XES), `# nosec B405` com motivo; se ler XML externo, usar `defusedxml` |
| `src/agrosmart/mineracao/visual.py:16` | B406 import de `xml.sax` | usa só `saxutils.escape` (não faz parsing): `# nosec B406` com esse motivo |
| `ia/guardrails.py` (`validar`, 26), `orquestracao.py` (`executar`, 25), `mineracao/relatorio.py` (`gerar_insights`, 21) | C901 > 20 | candidatas a refatoração (o gate é 30) |

## 7. Como rodar localmente

Pré-requisitos: Python 3.11+ com o projeto instalado (`python -m pip install -e
".[dev,ia,app,iot]"`), `bandit` e `pip-audit` no mesmo Python, Docker com buildx.

```powershell
# Windows
.\scripts\pipeline_local.ps1                          # todos os estágios
.\scripts\pipeline_local.ps1 --estagios qualidade,testes,sast
.\scripts\pipeline_local.ps1 --sem-docker             # sem build/scan
```

```bash
# Linux/macOS/Git Bash
./scripts/pipeline_local.sh
```

Saída: `outputs/pipeline/<AAAAMMDDTHHMMSS>/` com um `NN-<estágio>.log` por estágio
(comandos, saída completa e código de saída), os artefatos e `resumo.json`/`resumo.md`;
o último resumo é copiado para `outputs/pipeline/ultimo/`. Código de saída 1 se algum
estágio falhar.

**Rede com inspeção TLS**: se existir a cadeia `~/.claude/certs/corporate-ca-chain.pem`
(ou `--ca-extra`/`AGROSMART_CA_EXTRA`), o runner monta `.certs/ca-bundle.pem`
(certifi + cadeia, pasta ignorada pelo git) e a usa só nos processos filhos
(`REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`, `PIP_CERT`), no build (`--secret id=ca_extra`) e
no container do Trivy (montada e apontada por `SSL_CERT_FILE`). O banco do Trivy fica
em `.cache/trivy` (ignorado pelo git e pelo Docker) para não ser baixado a cada execução.

Diferença em relação ao CI: localmente o Trivy lê a imagem pelo socket do Docker; no CI
ele lê o `imagem.tar` gerado pelo job de build (mesma imagem, mesmos critérios).

## 8. Como habilitar no GitHub

1. Criar o repositório no GitHub e enviar o código:
   `git remote add origin <url>` e `git push -u origin main`.
2. O workflow roda sozinho no push; em **Actions** cada job mostra o log, o *Job summary*
   (cobertura, pip-audit, funções complexas) e os artifacts (`testes-relatorios`,
   `sast-bandit`, `sca-pip-audit`, `imagem-trivy`).
3. Code Scanning (SARIF de bandit e Trivy): público ou com GitHub Advanced Security.
   Sem ele o upload falha de forma tolerada (`continue-on-error`) e os SARIF ficam nos
   artifacts.
4. Proteção da `main` (Settings → Branches): exigir os checks `qualidade`, `testes`,
   `sast`, `sca`, `build` e `imagem` antes do merge.

## 9. Como habilitar o SonarCloud (opcional)

1. Entrar em sonarcloud.io com a conta GitHub, importar o repositório e anotar a
   **organização** e a **project key** (ajustar `sonar.projectKey` em
   `sonar-project.properties` se for diferente de `agrosmart`).
2. Em *Administration → Analysis Method*, desligar a *Automatic Analysis* (a análise
   vem do CI, com cobertura).
3. Gerar um token (*My Account → Security*) e criar os secrets do repositório
   `SONAR_TOKEN` e `SONAR_ORGANIZATION`.
4. No próximo push o job `sonarcloud` deixa de ser pulado: baixa `coverage.xml`/`junit.xml`
   do job de testes e publica a análise. Sem `SONAR_TOKEN` o job é simplesmente
   ignorado (o job `sonar-habilitado` decide).
