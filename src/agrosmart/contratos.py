"""Contratos de dados: leitura, validação e consulta.

Um contrato (``contratos/<fonte>.<versao>.yaml``) é a descrição completa de
uma origem de dados — schema, forma de ingestão, particionamento, retenção e
regras de qualidade. Este módulo transforma o YAML em objetos tipados e
**rejeita contratos internamente inconsistentes** antes que qualquer dado seja
tocado: regra que cita campo inexistente, chave natural fora do schema,
identificador de regra duplicado, parâmetro obrigatório ausente.

Quem consome:

* coletores — sabem o formato bruto, a janela e a chave natural;
* motor de qualidade — executa ``qualidade[]`` sobre o silver;
* catálogo e dicionário de dados — são gerados daqui, nunca escritos à mão;
* processamento — usa ``campos[]`` para tipar o Parquet.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agrosmart.config import carregar_config

# ---------------------------------------------------------------------------
# Vocabulário controlado
# ---------------------------------------------------------------------------


class TipoDado(StrEnum):
    ESTRUTURADO = "estruturado"
    SEMIESTRUTURADO = "semiestruturado"
    NAO_ESTRUTURADO = "nao_estruturado"


class ClassificacaoLGPD(StrEnum):
    """Sensibilidade do dado. Governa mascaramento e o que pode ir para o gold."""

    PUBLICO = "publico"
    INTERNO = "interno"
    PESSOAL = "pessoal"
    SENSIVEL = "sensivel"


class Severidade(StrEnum):
    CRITICA = "critica"  # registro reprovado -> quarentena
    ALERTA = "alerta"  # registro passa, mas o problema é registrado


class TipoCampo(StrEnum):
    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    DATE = "date"
    TIMESTAMP = "timestamp"


class ModoIngestao(StrEnum):
    STREAMING = "streaming"
    BATCH = "batch"
    API_PULL = "api_pull"
    WATCH_FOLDER = "watch_folder"


TipoRegra = Literal[
    "nao_nulo",
    "unicidade",
    "intervalo",
    "dominio",
    "regex",
    "nao_futuro",
    "referencia",
    "comparacao",
    "freshness",
]

OperadorComparacao = Literal["<", "<=", "==", "!=", ">=", ">"]

# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------


ClassificacaoCampoLGPD = Literal["identificador", "localizacao", "pessoal"]
TratamentoLGPD = Literal["pseudonimizar", "remover"]


class Campo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    nome: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    tipo: TipoCampo
    unidade: str | None = None
    obrigatorio: bool = False
    descricao: str = Field(min_length=1)
    exemplo: Any = None
    # Marcação LGPD no nível do campo. ``lgpd`` diz o que o campo revela;
    # ``tratamento`` diz o que o silver faz com ele. Campos marcados nunca
    # entram em produto de dados agregado (benchmark), com ou sem tratamento.
    lgpd: ClassificacaoCampoLGPD | None = None
    tratamento: TratamentoLGPD | None = None

    @model_validator(mode="after")
    def _tratamento_exige_classificacao(self) -> Campo:
        if self.tratamento and not self.lgpd:
            raise ValueError(f"campo {self.nome}: 'tratamento' exige 'lgpd'")
        return self


class Dedup(BaseModel):
    """Como interpretar a repetição da chave natural.

    * ``evento`` — registros são fatos imutáveis (leitura de sensor, dia de
      clima, cena, laudo). Repetir a chave é **duplicata**: viola a regra de
      unicidade e vai para a quarentena; fica a primeira ingestão.
    * ``versao`` — a origem reenvia registros atualizados (snapshot do ERP,
      correção retroativa). Repetir a chave **entre lotes** é nova versão:
      fica a mais recente por ``campo_ordem``, as anteriores são
      *substituídas* (contadas, não quarentenadas). Repetir a chave **no
      mesmo lote** continua sendo duplicata.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    estrategia: Literal["evento", "versao"]
    campo_ordem: str | None = None

    @model_validator(mode="after")
    def _versao_exige_ordem(self) -> Dedup:
        if self.estrategia == "versao" and not self.campo_ordem:
            raise ValueError("dedup.estrategia=versao exige 'campo_ordem'")
        return self


class Ingestao(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    modo: ModoIngestao
    origem: str
    formato_bruto: str
    janela: str
    sla_freshness_min: int = Field(gt=0)
    observacao: str | None = None
    # Campos do schema que o coletor calcula ao gravar (ponteiro para o binário,
    # checksum, tamanho) e que, portanto, não vêm no dado de origem.
    campos_derivados: list[str] = Field(default_factory=list)


class Particionamento(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    estrategia: str
    coluna_evento: str
    colunas: list[str] = Field(min_length=1)


class Destino(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    silver_tabela: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    gold_contribui: list[str] = Field(default_factory=list)


class Retencao(BaseModel):
    """Dias de retenção por camada. ``None`` significa permanente."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bronze_dias: int | None = Field(default=None, gt=0)
    silver_dias: int | None = Field(default=None, gt=0)
    gold_dias: int | None = Field(default=None, gt=0)


class RegraQualidade(BaseModel):
    """Uma expectativa sobre o dado, com a razão de ela existir.

    Os parâmetros variam por tipo de regra; o validador garante que cada tipo
    recebeu o que precisa. A ``justificativa`` é obrigatória de propósito:
    regra sem motivo documentado vira ruído que ninguém ousa remover.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Z]{3}-\d{2}$")
    regra: TipoRegra
    severidade: Severidade
    justificativa: str = Field(min_length=10)

    campos: list[str] = Field(default_factory=list)

    # intervalo
    min: float | None = None
    max: float | None = None
    # dominio
    valores: list[str] | None = None
    # regex
    padrao: str | None = None
    # nao_futuro — ``agora`` compara com o instante de referência do pipeline;
    # ``lote`` compara com o fim da janela do lote (detecta relógio adiantado
    # em streaming, onde o "agora" da ingestão é o corte do micro-batch).
    tolerancia_min: int | None = Field(default=None, ge=0)
    referencia: Literal["agora", "lote"] | None = None
    # referencia
    tabela_referencia: str | None = None
    campo_referencia: str | None = None
    # comparacao
    campo_a: str | None = None
    operador: OperadorComparacao | None = None
    campo_b: str | None = None
    # freshness — ``detectar_lacunas`` só faz sentido em séries periódicas (sensor a
    # cada 15 min, estação uma vez por dia); em fontes de eventos esparsos (uma
    # operação quando acontece) a ausência de registro não é lacuna.
    max_atraso_min: int | None = Field(default=None, gt=0)
    detectar_lacunas: bool = False

    @model_validator(mode="after")
    def _parametros_por_tipo(self) -> RegraQualidade:
        exige_campos = {
            "nao_nulo",
            "unicidade",
            "intervalo",
            "dominio",
            "regex",
            "nao_futuro",
            "referencia",
        }
        if self.regra in exige_campos and not self.campos:
            raise ValueError(f"regra {self.id} ({self.regra}) exige a lista 'campos'")

        if self.regra == "intervalo":
            if self.min is None and self.max is None:
                raise ValueError(f"regra {self.id} (intervalo) exige 'min' e/ou 'max'")
            if self.min is not None and self.max is not None and self.min > self.max:
                raise ValueError(f"regra {self.id} (intervalo) tem min > max")
        if self.regra == "dominio" and not self.valores:
            raise ValueError(f"regra {self.id} (dominio) exige 'valores'")
        if self.regra == "regex" and not self.padrao:
            raise ValueError(f"regra {self.id} (regex) exige 'padrao'")
        if self.regra == "referencia" and not (self.tabela_referencia and self.campo_referencia):
            raise ValueError(
                f"regra {self.id} (referencia) exige 'tabela_referencia' e 'campo_referencia'"
            )
        if self.regra == "comparacao" and not (self.campo_a and self.operador and self.campo_b):
            raise ValueError(
                f"regra {self.id} (comparacao) exige 'campo_a', 'operador' e 'campo_b'"
            )
        if self.regra == "freshness" and self.max_atraso_min is None:
            raise ValueError(f"regra {self.id} (freshness) exige 'max_atraso_min'")
        return self

    @property
    def campos_envolvidos(self) -> list[str]:
        """Todos os campos que a regra toca — para validar contra o schema."""
        extras = [c for c in (self.campo_a, self.campo_b) if c]
        return [*self.campos, *extras]

    @property
    def parametros(self) -> dict[str, Any]:
        """Só os parâmetros específicos preenchidos — para o relatório de qualidade."""
        candidatos = {
            "min": self.min,
            "max": self.max,
            "valores": self.valores,
            "padrao": self.padrao,
            "tolerancia_min": self.tolerancia_min,
            "referencia": self.referencia,
            "tabela_referencia": self.tabela_referencia,
            "campo_referencia": self.campo_referencia,
            "campo_a": self.campo_a,
            "operador": self.operador,
            "campo_b": self.campo_b,
            "max_atraso_min": self.max_atraso_min,
            "detectar_lacunas": self.detectar_lacunas if self.regra == "freshness" else None,
        }
        return {k: v for k, v in candidatos.items() if v is not None}


class Contrato(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fonte: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    versao: str = Field(pattern=r"^v\d+$")
    titulo: str
    descricao: str
    responsavel: str
    tipo_dado: TipoDado
    classificacao_lgpd: ClassificacaoLGPD
    ingestao: Ingestao
    particionamento: Particionamento
    chave_natural: list[str] = Field(min_length=1)
    dedup: Dedup
    destino: Destino
    retencao: Retencao
    campos: list[Campo] = Field(min_length=1)
    qualidade: list[RegraQualidade] = Field(default_factory=list)

    # --- validação cruzada --------------------------------------------------

    @field_validator("campos")
    @classmethod
    def _nomes_unicos(cls, campos: list[Campo]) -> list[Campo]:
        nomes = [c.nome for c in campos]
        duplicados = sorted({n for n in nomes if nomes.count(n) > 1})
        if duplicados:
            raise ValueError(f"campos duplicados: {duplicados}")
        return campos

    @model_validator(mode="after")
    def _consistencia(self) -> Contrato:
        nomes = {c.nome for c in self.campos}

        faltando = [c for c in self.chave_natural if c not in nomes]
        if faltando:
            raise ValueError(f"chave_natural cita campos inexistentes: {faltando}")

        if self.particionamento.coluna_evento not in nomes:
            coluna = self.particionamento.coluna_evento
            raise ValueError(f"particionamento.coluna_evento={coluna!r} não está no schema")

        derivados_fora = [c for c in self.ingestao.campos_derivados if c not in nomes]
        if derivados_fora:
            raise ValueError(
                f"ingestao.campos_derivados cita campos inexistentes: {derivados_fora}"
            )

        if self.dedup.campo_ordem and self.dedup.campo_ordem not in nomes:
            raise ValueError(f"dedup.campo_ordem={self.dedup.campo_ordem!r} não está no schema")

        ids = [r.id for r in self.qualidade]
        repetidos = sorted({i for i in ids if ids.count(i) > 1})
        if repetidos:
            raise ValueError(f"ids de regra duplicados: {repetidos}")

        for regra in self.qualidade:
            desconhecidos = [c for c in regra.campos_envolvidos if c not in nomes]
            if desconhecidos:
                raise ValueError(f"regra {regra.id} cita campos inexistentes: {desconhecidos}")
        return self

    # --- consultas ----------------------------------------------------------

    @property
    def nome_completo(self) -> str:
        return f"{self.fonte}.{self.versao}"

    @property
    def campos_obrigatorios(self) -> list[Campo]:
        return [c for c in self.campos if c.obrigatorio]

    @property
    def campos_lgpd(self) -> list[Campo]:
        """Campos que revelam pessoa ou propriedade — nunca entram em agregado externo."""
        return [c for c in self.campos if c.lgpd]

    @property
    def regra_unicidade(self) -> RegraQualidade | None:
        return next((r for r in self.qualidade if r.regra == "unicidade"), None)

    @property
    def campos_de_origem(self) -> list[str]:
        """O que o dado bruto precisa trazer — schema menos os campos derivados."""
        derivados = set(self.ingestao.campos_derivados)
        return [c.nome for c in self.campos if c.nome not in derivados]

    @property
    def obrigatorios_de_origem(self) -> list[str]:
        derivados = set(self.ingestao.campos_derivados)
        return [c.nome for c in self.campos if c.obrigatorio and c.nome not in derivados]

    @property
    def regras_criticas(self) -> list[RegraQualidade]:
        return [r for r in self.qualidade if r.severidade is Severidade.CRITICA]

    @property
    def regras_alerta(self) -> list[RegraQualidade]:
        return [r for r in self.qualidade if r.severidade is Severidade.ALERTA]

    @property
    def dependencias(self) -> set[str]:
        """Tabelas silver de outras fontes que este contrato referencia."""
        return {r.tabela_referencia for r in self.qualidade if r.tabela_referencia}

    def campo(self, nome: str) -> Campo:
        for c in self.campos:
            if c.nome == nome:
                return c
        raise KeyError(f"campo {nome!r} não existe em {self.nome_completo}")

    def mapa_tipos(self) -> dict[str, TipoCampo]:
        return {c.nome: c.tipo for c in self.campos}


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------


class ErroContrato(Exception):
    """Contrato malformado ou inconsistente."""


def carregar_contrato(caminho: Path) -> Contrato:
    try:
        bruto = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    except yaml.YAMLError as erro:
        raise ErroContrato(f"{caminho.name}: YAML inválido — {erro}") from erro
    if not isinstance(bruto, dict):
        raise ErroContrato(f"{caminho.name}: o contrato precisa ser um mapeamento YAML")
    try:
        contrato = Contrato.model_validate(bruto)
    except Exception as erro:  # pydantic.ValidationError, mas queremos a mensagem limpa
        raise ErroContrato(f"{caminho.name}: {erro}") from erro

    esperado = f"{contrato.nome_completo}.yaml"
    if caminho.name != esperado:
        raise ErroContrato(
            f"{caminho.name}: o nome do arquivo deve ser {esperado} (fonte + versão declaradas)"
        )
    return contrato


def carregar_contratos(diretorio: Path | None = None) -> dict[str, Contrato]:
    """Carrega todos os contratos, indexados por ``fonte``.

    Falha cedo: um único contrato inválido interrompe a carga. É melhor o
    pipeline não subir do que subir com uma fonte sem regras.
    """
    pasta = diretorio or carregar_config().dir_contratos
    arquivos = sorted(pasta.glob("*.yaml"))
    if not arquivos:
        raise ErroContrato(f"nenhum contrato encontrado em {pasta}")

    contratos: dict[str, Contrato] = {}
    for arquivo in arquivos:
        contrato = carregar_contrato(arquivo)
        if contrato.fonte in contratos:
            raise ErroContrato(
                f"fonte {contrato.fonte!r} declarada em mais de um arquivo — "
                "só uma versão pode estar ativa por vez"
            )
        contratos[contrato.fonte] = contrato

    _validar_referencias_cruzadas(contratos)
    return contratos


def _validar_referencias_cruzadas(contratos: dict[str, Contrato]) -> None:
    """Toda regra ``referencia`` precisa apontar para uma tabela silver que existe."""
    tabelas = {c.destino.silver_tabela: c for c in contratos.values()}
    for contrato in contratos.values():
        for regra in contrato.qualidade:
            if regra.regra != "referencia":
                continue
            alvo = tabelas.get(regra.tabela_referencia or "")
            if alvo is None:
                raise ErroContrato(
                    f"{contrato.nome_completo} regra {regra.id}: tabela_referencia "
                    f"{regra.tabela_referencia!r} não é a silver_tabela de nenhum contrato"
                )
            if regra.campo_referencia not in alvo.mapa_tipos():
                raise ErroContrato(
                    f"{contrato.nome_completo} regra {regra.id}: campo_referencia "
                    f"{regra.campo_referencia!r} não existe em {alvo.nome_completo}"
                )


def ordenar_por_dependencia(contratos: dict[str, Contrato]) -> list[Contrato]:
    """Ordem de processamento: quem é referenciado vem antes de quem referencia.

    Ordenação topológica com desempate por "quantas fontes dependem de mim":
    entre contratos igualmente prontos, o dado mestre (o mais referenciado)
    vem primeiro. Na prática ``erp_talhoes`` abre o pipeline — todas as outras
    fontes checam integridade referencial contra ele. Ciclo é erro de
    contrato, não caso a tratar.
    """
    por_tabela = {c.destino.silver_tabela: c.fonte for c in contratos.values()}
    dependentes: dict[str, int] = {fonte: 0 for fonte in contratos}
    for c in contratos.values():
        for dep in c.dependencias:
            fonte_alvo = por_tabela.get(dep)
            if fonte_alvo is not None:
                dependentes[fonte_alvo] += 1

    pendentes = dict(contratos)
    ordenados: list[Contrato] = []

    while pendentes:
        prontos = [
            c
            for c in pendentes.values()
            if all(por_tabela.get(dep) not in pendentes for dep in c.dependencias)
        ]
        if not prontos:
            raise ErroContrato(f"ciclo de dependência entre contratos: {sorted(pendentes)}")
        for c in sorted(prontos, key=lambda x: (-dependentes[x.fonte], x.fonte)):
            ordenados.append(c)
            del pendentes[c.fonte]
    return ordenados


@lru_cache(maxsize=1)
def contratos_ativos() -> dict[str, Contrato]:
    """Contratos do projeto, carregados uma vez por processo."""
    return carregar_contratos()
