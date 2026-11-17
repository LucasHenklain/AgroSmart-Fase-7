# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# AgroSmart Fase 7 — imagem única da aplicação (CLI, worker, gateway, dashboard)
#
#   docker build -t agrosmart:7.0.0 .
#   docker run --rm agrosmart:7.0.0 versao
#
# Multi-stage: o estágio `builder` instala as dependências num venv; o `runtime`
# copia só o venv e os arquivos de dados do projeto — sem compilador, sem pip,
# sem cache, rodando como usuário sem privilégio (UID 10001).
#
# Rede corporativa com inspeção TLS: passe a cadeia de CA como *build secret*
# (nunca entra numa camada da imagem):
#   docker build --secret id=ca_extra,src=/caminho/corporate-ca-chain.pem -t agrosmart:7.0.0 .
# Sem o secret, o build usa só as CAs públicas da imagem base.
# ---------------------------------------------------------------------------
ARG PYTHON_VERSION=3.13

FROM python:${PYTHON_VERSION}-slim AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=secret,id=ca_extra,required=false \
    set -eu; \
    if [ -s /run/secrets/ca_extra ]; then \
        cat /etc/ssl/certs/ca-certificates.crt /run/secrets/ca_extra > /tmp/ca.pem; \
        export PIP_CERT=/tmp/ca.pem; \
    fi; \
    pip install --upgrade pip; \
    pip install ".[app,iot]"; \
    pip uninstall -y pip; \
    rm -f /tmp/ca.pem

FROM python:${PYTHON_VERSION}-slim AS runtime
ARG VERSAO=7.0.0
LABEL org.opencontainers.image.title="agrosmart" \
      org.opencontainers.image.description="AgroSmart — IoT, Data Lake, automação e operação contínua" \
      org.opencontainers.image.version="${VERSAO}" \
      org.opencontainers.image.licenses="MIT"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGROSMART_RAIZ=/app \
    AGROSMART_METRICAS_HOST=0.0.0.0 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true
# Correções de segurança do Debian já publicadas entram na imagem (o scan de imagem do
# pipeline DevSecOps apontou zlib1g desatualizada na base). Sem pacotes novos, sem cache.
# O pip do Python base não é usado em runtime: sai junto com a superfície de ataque.
RUN set -eu; \
    apt-get update; \
    apt-get upgrade -y --no-install-recommends; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*; \
    python -m pip uninstall -y pip setuptools wheel 2>/dev/null || true; \
    groupadd --system --gid 10001 agro; \
    useradd --system --uid 10001 --gid agro --home-dir /app --shell /usr/sbin/nologin agro
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=agro:agro contratos ./contratos
COPY --chown=agro:agro app ./app
RUN mkdir -p landing outputs/logs outputs/status outputs/acoes outputs/relatorios \
             data/exemplos docs \
    && chown -R agro:agro /app
USER 10001:10001
EXPOSE 8000 8501
# Saúde do worker (/health); os demais serviços definem o próprio healthcheck no compose.
ENTRYPOINT ["python", "-m", "agrosmart"]
CMD ["versao"]
