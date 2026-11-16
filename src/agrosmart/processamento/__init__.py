"""Processamento entre camadas: bronze → silver → gold, e a manutenção do lake.

    leitura      bronze → DataFrame todo texto, com proveniência do manifest
    tipagem      texto → tipos do contrato, guardando o que falhou (TIP-01)
    silver       normalização, qualidade, LGPD, Parquet particionado, reconciliação
    gold         modelo dimensional e indicadores em SQL (DuckDB) sobre o silver
    manutencao   compactação de arquivos pequenos e retenção por contrato

O silver é recomputado por inteiro a cada execução; o gold é derivado do
silver e também recomputado. Nada aqui lê a landing nem toca o bronze além
de leitura — o bronze é imutável por contrato.

Responsável: E1 (Engenharia de Dados) com E2 (Analytics) no gold.
"""
