# Pipeline DevSecOps (runner local) — 20261117T114128

- Status geral: **FALHOU**
- Commit: `5a7745a` (árvore com alterações)
- Host: Windows 11 · Python 3.13.1 · duração 969.8 s

| Estágio | Status | Duração (s) | Números-chave | Log |
|---|---|---:|---|---|
| qualidade | ❌ falhou | 9.1 | funcoes_complexidade_acima_alerta=3; complexidade_alerta=20; complexidade_max_gate=30; falhas: ruff check (lint) | ruff format --check (formatação) | [01-qualidade.log](01-qualidade.log) |
| testes | ✅ passou | 874.8 | testes_total=252; testes_passados=251; testes_falhas=0; testes_erros=0; testes_pulados=1; testes_tempo_s=851.7; cobertura_linhas_pct=80.39; cobertura_ramos_pct=65.5; linhas_validas=7464; linhas_cobertas=6000; cobertura_minima_pct=60 | [02-testes.log](02-testes.log) |
| sast | ✅ passou | 12.0 | bandit_high=0; bandit_medium=4; bandit_low=8; bandit_nosec_justificados=0; bandit_linhas_analisadas=15945 | [03-sast.log](03-sast.log) |
| sca | ❌ falhou | 14.0 | pip_audit_vulnerabilidades=92; pip_audit_pacotes_vulneraveis=8; dependencias_auditadas=71; falhas: pip-audit | [04-sca.log](04-sca.log) |
| build | ✅ passou | 16.3 | imagem_bytes=291520379; imagem_mb=291.5; imagem_usuario=10001:10001 | [05-build.log](05-build.log) |
| imagem | ✅ passou | 43.0 | trivy_total=211; imagem_so=debian 13.7 | [06-imagem.log](06-imagem.log) |

## Detalhes

- **sast · bandit_por_regra**: B101: 3, B110: 1, B310: 1, B311: 2, B405: 1, B406: 1, B608: 3
- **sca · pip_audit_detalhe**: anyio: 2, certifi: 1, gitpython: 34, pillow: 42, pygments: 1, python-multipart: 6, requests: 1, urllib3: 5
- **imagem · trivy_por_severidade**: CRITICAL: 0, HIGH: 48, MEDIUM: 93, LOW: 65, UNKNOWN: 5
- **imagem · trivy_corrigiveis_por_severidade**: CRITICAL: 0, HIGH: 1, MEDIUM: 1, LOW: 0, UNKNOWN: 0
