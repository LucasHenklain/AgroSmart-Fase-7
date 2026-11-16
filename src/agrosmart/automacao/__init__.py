"""Automação inteligente baseada em dados: do gold à ação, sem ninguém no meio.

    motor        lê o gold, decide por talhão e monta o plano de ações (JSON + Markdown)
    atuadores    publica os comandos de irrigação via MQTT (QoS 1) — ou um dublê, nos testes

A Fase 5 parou na recomendação ("a IA recomenda, o produtor decide"). A Fase 6
fecha o ciclo: as mesmas regras e os mesmos limiares de ``agronomia.py`` que o
gold e o copiloto usam viram **decisões executáveis** — irrigar, alertar,
trocar bateria, inspecionar sensor — com motivo, evidência (tabela:chave:data)
e, quando há atuador, um comando MQTT. O copiloto entra como explicação em
linguagem natural, com os guardrails de sempre; o plano não depende dele.

Responsável: E3 (IA e automação).
"""

from agrosmart.automacao.motor import Acao, Plano, TabelasGold, executar, gerar_plano

__all__ = ["Acao", "Plano", "TabelasGold", "executar", "gerar_plano"]
