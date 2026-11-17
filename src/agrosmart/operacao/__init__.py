"""Operação contínua da plataforma (Fase 7).

* :mod:`agrosmart.operacao.worker` — o serviço que roda o ciclo coleta → ingestão →
  silver → gold → decisão → atuação em intervalo fixo (``agrosmart operar``);
* :mod:`agrosmart.operacao.metricas` — o estado da operação e a sua exposição no
  formato texto do Prometheus;
* :mod:`agrosmart.operacao.servidor` — ``/health``, ``/metrics`` e ``/status`` por HTTP,
  para o healthcheck do Docker, o Prometheus/Grafana e quem acompanha a operação.

Responsável: E4 (Plataforma e Operação).
"""
