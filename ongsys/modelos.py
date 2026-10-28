"""Estruturas de dados compartilhadas por todas as camadas."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .normalizacao import (
    competencia_br,
    data_br,
    formatar_dinheiro_br,
    formatar_percentual_br,
    normalizar_documento,
    sem_acentos,
)


class Estado:
    """Estados possíveis de um título no checkpoint (§20)."""

    PLANEJADO = "PLANEJADO"
    BLOQUEADO = "BLOQUEADO"
    DUPLICIDADE_CONFIRMADA = "DUPLICIDADE_CONFIRMADA"
    EM_EXECUCAO = "EM_EXECUCAO"
    SALVO_NAO_AUDITADO = "SALVO_NAO_AUDITADO"
    CONCLUIDO = "CONCLUIDO"
    PENDENTE_DADOS = "PENDENTE_DADOS"
    FALHA = "FALHA"
    # Estado aplicado somente depois de uma conferência manual explícita de
    # que a tentativa incerta NÃO criou o título. Volta a permitir uma futura
    # execução, mas não cria nada por si só.
    LIBERADO_MANUALMENTE = "LIBERADO_MANUALMENTE"

    #: Estados em que o título já pode existir no Ongsys. Nenhum deles pode ser
    #: recriado automaticamente sem antes consultar o site.
    JA_PODE_EXISTIR = {EM_EXECUCAO, SALVO_NAO_AUDITADO, CONCLUIDO, DUPLICIDADE_CONFIRMADA}


class Duplicidade:
    """Classificação da pesquisa de duplicidade no Ongsys (§18)."""

    NOVO = "NOVO"
    EXATO_JA_EXISTE = "EXATO_JA_EXISTE"
    POSSIVEL_DUPLICIDADE = "POSSIVEL_DUPLICIDADE"
    DOCUMENTO_REPETIDO_OUTRO_TITULO = "DOCUMENTO_REPETIDO_OUTRO_TITULO"
    NAO_VERIFICADO = "NAO_VERIFICADO"


@dataclass
class LinhaPlanilha:
    """Uma linha bruta já lida e convertida, antes do agrupamento."""

    linha: int
    codigo_despesa: str
    atividade: str
    emissao: date | None
    vencimento: date | None
    efetivacao: date | None
    fornecedor: str
    documento: str
    centro_custo: str
    categoria_titulo: str
    categoria_detalhada: str
    entrada: Decimal | None
    saida: Decimal | None
    codigo_barras: str = ""
    cor: str = "SEM_COR"


@dataclass
class Exclusao:
    """Linha descartada, com o motivo, para aparecer no relatório (§6)."""

    linha: int
    fornecedor: str
    documento: str
    valor: str
    motivo: str


@dataclass
class Rateio:
    linha: int
    valor: Decimal
    centro_custo: str
    categoria_titulo: str
    categoria_detalhada: str
    subprojeto: str
    atividade: str
    percentual: Decimal = Decimal("0")

    @property
    def valor_br(self) -> str:
        return formatar_dinheiro_br(self.valor)

    @property
    def percentual_br(self) -> str:
        return formatar_percentual_br(self.percentual)


@dataclass
class Titulo:
    """Um título de contas a pagar pronto para conferência ou lançamento."""

    chave: str
    linhas_origem: list[int]
    fornecedor: str
    documento: str
    documento_original: str
    emissao: date
    vencimento: date
    efetivacao: date
    valor: Decimal
    conta_pagadora_id: str
    conta_pagadora_nome: str
    tipo_despesa: str
    tipo_documento: str
    meio_pagamento: str
    tipo_pagamento: str
    historico: str
    codigo_barras: str = ""
    rateios: list[Rateio] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)
    bloqueios: list[str] = field(default_factory=list)
    estado: str = Estado.PLANEJADO
    duplicidade: str = Duplicidade.NAO_VERIFICADO
    #: Conta pagadora é cartão de crédito (pelo nome ou por ``contas_cartao``).
    conta_cartao: bool = False

    @property
    def bloqueado(self) -> bool:
        """Título com bloqueio nunca é enviado ao Ongsys sem decisão do usuário."""
        return bool(self.bloqueios)

    @property
    def competencia(self) -> str:
        return competencia_br(self.efetivacao)

    @property
    def valor_br(self) -> str:
        return formatar_dinheiro_br(self.valor)

    def datas_br(self) -> dict[str, str]:
        return {
            "emissao": data_br(self.emissao),
            "vencimento": data_br(self.vencimento),
            "efetivacao": data_br(self.efetivacao),
        }

    def hash_origem(self) -> str:
        """Impressão digital dos dados de origem (§20).

        Se a planilha mudar, o hash muda e o checkpoint antigo deixa de valer
        para aquele título — evita reaproveitar um 'CONCLUIDO' de outros dados.
        """
        base = json.dumps(
            {
                "chave": self.chave,
                "valor": str(self.valor),
                "emissao": self.emissao.isoformat(),
                "vencimento": self.vencimento.isoformat(),
                "efetivacao": self.efetivacao.isoformat(),
                "conta": self.conta_pagadora_id,
                "fornecedor": self.fornecedor,
                "documento": self.documento_original,
                "tipo_despesa": self.tipo_despesa,
                "meio_pagamento": self.meio_pagamento,
                "historico": self.historico,
                "codigo_barras": re.sub(r"[.\s]", "", self.codigo_barras or ""),
                "rateios": [
                    [str(r.valor), r.centro_custo, r.categoria_detalhada, r.subprojeto, r.atividade]
                    for r in self.rateios
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]

    def assinatura_busca(self) -> dict[str, str]:
        """Dados usados para procurar o título já existente no Ongsys (§18)."""
        return {
            "fornecedor": sem_acentos(self.fornecedor),
            "documento": normalizar_documento(self.documento),
            "valor": str(self.valor),
            "vencimento": self.vencimento.isoformat(),
            "efetivacao": self.efetivacao.isoformat(),
            "conta": self.conta_pagadora_id,
        }


@dataclass
class ResultadoLeitura:
    """Tudo que a leitura da planilha produziu, inclusive o que foi descartado."""

    titulos: list[Titulo] = field(default_factory=list)
    exclusoes: list[Exclusao] = field(default_factory=list)
    cores_encontradas: dict[str, int] = field(default_factory=dict)
    linhas_por_cor_incluidas: int = 0
    linhas_por_cor_excluidas: int = 0
    colunas: dict[str, int] = field(default_factory=dict)
    linha_cabecalho: int = 0
    avisos: list[str] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((t.valor for t in self.titulos), Decimal("0"))

    @property
    def total_liberado(self) -> Decimal:
        return sum((t.valor for t in self.titulos if not t.bloqueado), Decimal("0"))

    def meses_com_dados(self) -> list[str]:
        return sorted({t.efetivacao.strftime("%m/%Y") for t in self.titulos})
