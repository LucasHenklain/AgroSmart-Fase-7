#!/usr/bin/env sh
# Runner local do pipeline DevSecOps (Linux/macOS/Git Bash).
# Repassa todos os argumentos para scripts/pipeline_local.py, ex.:
#   ./scripts/pipeline_local.sh
#   ./scripts/pipeline_local.sh --estagios qualidade,testes,sast
#   ./scripts/pipeline_local.sh --sem-docker
# Evidência em outputs/pipeline/<AAAAMMDDTHHMMSS>/ e outputs/pipeline/ultimo/.
set -eu
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python
cd "$RAIZ"
exec "$PYTHON" scripts/pipeline_local.py "$@"
