"""AgroSmart Fase 7 — plataforma de dados e IA para decisão no campo.

Mapa do pacote (um subpacote por responsabilidade, um dono por subpacote):

    config          onde o lake vive e como é endereçado
    storage         acesso ao lake (MinIO ou filesystem) por camada e chave
    contratos       schema, ingestão e regras de qualidade de cada fonte
    log             logging de console + trilha de eventos JSONL
    simuladores     geram dados sintéticos das fontes           (E1)
    ingestao        coletores: landing → bronze                 (E1)
    qualidade       regras dos contratos → aprovado/quarentena  (E2)
    processamento   bronze → silver → gold                      (E1/E2)
    catalogo        inventário de tabelas, schemas e linhagem   (E2)
    ia              copiloto: context pack → recomendação       (E3)
    automacao       motor de decisão → ações → comando MQTT     (E3)
    operacao        worker contínuo, métricas e saúde           (E4)
    mineracao       event log → mineração de processos          (E4)
    pipeline        CLI que amarra tudo
"""

__version__ = "7.0.0"
