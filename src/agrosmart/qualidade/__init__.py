"""Motor de qualidade de dados.

Executa as regras declaradas em ``contratos/*.yaml`` (``qualidade[]``) sobre
os dados que estão entrando no silver. Cada regra produz, por registro, um
veredito; a severidade decide o destino:

    critica  →  registro vai para a quarentena, com o id da regra e o motivo
    alerta   →  registro segue para o silver, o id fica em ``_alertas``

Além das regras do contrato, duas regras de **sistema** existem sempre:
``TIP-01`` (valor incompatível com o tipo declarado) e a deduplicação pela
chave natural conforme ``dedup.estrategia`` — duplicata é reprovada pela
regra de unicidade do contrato; versão substituída é contada, não reprovada.

O resultado consolidado é ``outputs/dq_report.json``: por fonte e por regra,
quantos registros foram avaliados, aprovados e reprovados, mais os alertas de
frescor por entidade (sensor atrasado, lacuna na série). É o artefato de
"confiabilidade dos dados" da Entrega 2 — e a entrada de alertas do copiloto.

Responsável: E2 (Qualidade e Analytics).
"""

from agrosmart.qualidade.motor import (
    REGRA_DUPLICATA_PADRAO,
    AlertaTabela,
    ResultadoQualidade,
    ResultadoRegra,
    avaliar,
)

__all__ = [
    "REGRA_DUPLICATA_PADRAO",
    "AlertaTabela",
    "ResultadoQualidade",
    "ResultadoRegra",
    "avaliar",
]
