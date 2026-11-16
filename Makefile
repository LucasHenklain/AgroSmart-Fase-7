# AgroSmart Fase 5 — atalhos de desenvolvimento.
# A interface principal é a CLI `agrosmart`; este Makefile só encurta o caminho.
# No Windows sem `make`, use os comandos equivalentes descritos no README.

PY ?= python

.PHONY: help venv install infra-up infra-down infra-logs doctor init run dashboard empacotar test lint fmt clean

help:
	@echo "make install     instala o pacote em modo editável com extras de dev"
	@echo "make infra-up    sobe MinIO e cria os buckets das camadas"
	@echo "make infra-down  derruba a infraestrutura (mantém os dados)"
	@echo "make doctor      verifica ambiente, contratos e lake"
	@echo "make init        cria diretórios e camadas do lake"
	@echo "make run         pipeline completo: simular → ingerir → silver → gold → catálogo → copiloto"
	@echo "make dashboard   abre a camada de consumo (Streamlit) sobre o gold"
	@echo "make empacotar   NOME='Nome Completo' RM=RM12345 TURMA=1TDSA → zip da entrega"
	@echo "make test        roda a suíte de testes"
	@echo "make lint        ruff check"
	@echo "make fmt         ruff format"

run:
	agrosmart run --all

dashboard:
	streamlit run app/dashboard.py

empacotar:
	agrosmart empacotar --nome "$(NOME)" --rm "$(RM)" --turma "$(TURMA)"

install:
	$(PY) -m pip install -e ".[dev,ia,app]"

infra-up:
	docker compose -f infra/docker-compose.yml up -d
	docker compose -f infra/docker-compose.yml logs bootstrap

infra-down:
	docker compose -f infra/docker-compose.yml down

infra-logs:
	docker compose -f infra/docker-compose.yml logs -f minio

doctor:
	agrosmart doctor

init:
	agrosmart init

test:
	$(PY) -m pytest

lint:
	ruff check src tests app scripts

fmt:
	ruff format src tests app scripts

clean:
	rm -rf .pytest_cache .ruff_cache build dist src/*.egg-info
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
