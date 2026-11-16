"""Copiloto de IA generativa para apoio à decisão no campo.

    contexto     Context Pack: tudo que o modelo pode saber sobre um talhão, lido do gold
    esquema      schema estruturado da saída (diagnóstico, recomendações, confiança)
    regras       motor determinístico — fallback e baseline de avaliação
    guardrails   trava agronômica, aterramento numérico, confiança, rastreabilidade
    prompts      prompts versionados (sistema + um por caso de uso)
    cliente_claude   chamada à API com saída estruturada via ferramenta
    agente       o copiloto: escolhe o motor, valida, cai no fallback, registra proveniência
    render       Markdown, PDF e JSON de proveniência
    avaliacao    casos × talhões × motores, com concordância entre regras e modelo

Princípio: **a IA lê o gold, não inventa**. Todo número de uma recomendação
existe no Context Pack; todo pacote sai das mesmas tabelas que o dashboard lê;
toda resposta diz que motor a produziu e por quê.

Responsável: E3 (IA).
"""

from agrosmart.ia.esquema import Caso, Recomendacao, SaidaCopiloto

__all__ = ["Caso", "Recomendacao", "SaidaCopiloto"]
