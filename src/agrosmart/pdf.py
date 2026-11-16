"""Escritor mínimo de PDF, sem dependências.

Gera um documento de texto em Helvetica, codificação WinAnsi (acentos do
português incluídos), com quebra automática de página. É o suficiente para um
laudo laboratorial simulado ou um relatório do copiloto — e é
**determinístico**: mesmo texto, mesmos bytes, sempre. As bibliotecas usuais
de PDF carimbam data de criação e identificadores aleatórios, o que quebraria
a reprodutibilidade das evidências.
"""

from __future__ import annotations

LARGURA_A4, ALTURA_A4 = 595, 842
MARGEM_X, TOPO, RODAPE = 56, 790, 60


def _escapar(texto: str) -> bytes:
    """Escapa o que o PDF exige dentro de ``( … )`` e codifica em WinAnsi."""
    codificado = texto.encode("cp1252", errors="replace")
    return codificado.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _quebrar(linha: str, largura_max: int) -> list[str]:
    """Quebra por palavras para caber em ``largura_max`` caracteres (aprox. Helvetica 10)."""
    if len(linha) <= largura_max:
        return [linha]
    palavras, atual, saida = linha.split(" "), "", []
    for palavra in palavras:
        candidata = f"{atual} {palavra}".strip()
        if len(candidata) > largura_max and atual:
            saida.append(atual)
            atual = palavra
        else:
            atual = candidata
    if atual:
        saida.append(atual)
    return saida


def _pagina(conteudo: bytes, numero_fonte: int, numero_pagina: int) -> tuple[bytes, bytes]:
    return (
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {LARGURA_A4} {ALTURA_A4}] "
            f"/Resources << /Font << /F1 {numero_fonte} 0 R >> >> /Contents {numero_pagina} 0 R >>"
        ).encode(),
        b"<< /Length " + str(len(conteudo)).encode() + b" >>\nstream\n" + conteudo + b"endstream",
    )


def gerar_pdf(
    linhas: list[str],
    *,
    titulo: str,
    autor: str = "AgroSmart",
    tamanho_fonte: int = 10,
    fonte_titulo: int = 15,
    largura_max: int = 92,
) -> bytes:
    """PDF A4 de uma ou mais páginas com título e linhas de texto."""
    passo = tamanho_fonte + 4
    linhas_por_pagina = (TOPO - RODAPE - fonte_titulo - 10) // passo

    fluxo: list[str] = []
    for linha in linhas:
        fluxo.extend(_quebrar(linha, largura_max) if linha else [""])

    paginas: list[list[str]] = [
        fluxo[i : i + linhas_por_pagina] for i in range(0, max(len(fluxo), 1), linhas_por_pagina)
    ]
    if not paginas:
        paginas = [[]]

    # Objetos: 1 catálogo, 2 páginas, 3 fonte, 4 info, depois pares (página, conteúdo).
    objetos: list[bytes] = [
        b"",  # 1 catálogo (preenchido depois)
        b"",  # 2 pages
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Title ("
        + _escapar(titulo)
        + b") /Author ("
        + _escapar(autor)
        + b") /Producer (agrosmart-pdf) >>",
    ]
    numeros_paginas: list[int] = []
    for indice, pagina in enumerate(paginas):
        conteudo = bytearray(b"BT\n")
        conteudo += f"/F1 {fonte_titulo} Tf\n{MARGEM_X} {TOPO} Td\n".encode()
        cabecalho = titulo if indice == 0 else f"{titulo} (cont.)"
        conteudo += b"(" + _escapar(cabecalho) + b") Tj\n"
        conteudo += f"/F1 {tamanho_fonte} Tf\n0 -{fonte_titulo + 10} Td\n".encode()
        for linha in pagina:
            conteudo += b"(" + _escapar(linha) + b") Tj\n"
            conteudo += f"0 -{passo} Td\n".encode()
        conteudo += f"/F1 8 Tf\n0 -{passo} Td\n".encode()
        conteudo += b"(" + _escapar(f"página {indice + 1} de {len(paginas)}") + b") Tj\n"
        conteudo += b"ET\n"
        numero_pagina = len(objetos) + 1
        numero_conteudo = numero_pagina + 1
        pagina_obj, conteudo_obj = _pagina(bytes(conteudo), 3, numero_conteudo)
        objetos.extend([pagina_obj, conteudo_obj])
        numeros_paginas.append(numero_pagina)

    objetos[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{n} 0 R" for n in numeros_paginas)
    objetos[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(numeros_paginas)} >>".encode()

    saida = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    deslocamentos: list[int] = []
    for indice, corpo in enumerate(objetos, start=1):
        deslocamentos.append(len(saida))
        saida += f"{indice} 0 obj\n".encode() + corpo + b"\nendobj\n"

    inicio_xref = len(saida)
    saida += f"xref\n0 {len(objetos) + 1}\n".encode()
    saida += b"0000000000 65535 f \n"
    for deslocamento in deslocamentos:
        saida += f"{deslocamento:010d} 00000 n \n".encode()
    saida += (
        f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R /Info 4 0 R >>\n"
        f"startxref\n{inicio_xref}\n%%EOF\n"
    ).encode()
    return bytes(saida)
