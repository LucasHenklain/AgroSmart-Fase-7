# Pipeline DevSecOps (runner local) — 20261117T134927

- Status geral: **PASSOU**
- Commit: `e2aaa74` (árvore com alterações)
- Host: Windows 11 · Python 3.13.1 · duração 1620.7 s

| Estágio | Status | Duração (s) | Números-chave | Log |
|---|---|---:|---|---|
| qualidade | ✅ passou | 11.0 | funcoes_complexidade_acima_alerta=3; complexidade_alerta=20; complexidade_max_gate=30 | [01-qualidade.log](01-qualidade.log) |
| testes | ✅ passou | 684.9 | testes_total=262; testes_passados=262; testes_falhas=0; testes_erros=0; testes_pulados=0; testes_tempo_s=641.8; cobertura_linhas_pct=85.1; cobertura_ramos_pct=74.75; linhas_validas=7484; linhas_cobertas=6369; cobertura_minima_pct=70 | [02-testes.log](02-testes.log) |
| sast | ✅ passou | 167.5 | bandit_high=0; bandit_medium=1; bandit_low=3; bandit_nosec_justificados=5; bandit_linhas_analisadas=15980 | [03-sast.log](03-sast.log) |
| sca | ✅ passou | 50.0 | pip_audit_vulnerabilidades=0; pip_audit_pacotes_vulneraveis=0; dependencias_auditadas=66 | [04-sca.log](04-sca.log) |
| build | ✅ passou | 464.4 | imagem_bytes=292450509; imagem_mb=292.5; imagem_usuario=10001:10001 | [05-build.log](05-build.log) |
| imagem | ✅ passou | 241.3 | trivy_total=209; imagem_so=debian 13.7 | [06-imagem.log](06-imagem.log) |

## Detalhes

- **sast · bandit_por_regra**: B110: 1, B310: 1, B405: 1, B406: 1
- **imagem · trivy_por_severidade**: CRITICAL: 0, HIGH: 47, MEDIUM: 92, LOW: 65, UNKNOWN: 5
- **imagem · trivy_corrigiveis_por_severidade**: CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0, UNKNOWN: 0
