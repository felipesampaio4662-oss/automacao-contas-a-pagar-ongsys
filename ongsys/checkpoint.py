"""Checkpoint estruturado (§20).

A versão anterior guardava apenas uma lista de chaves concluídas. Isso não
distingue "salvei e conferi" de "salvei e o programa caiu antes de conferir",
e é exatamente essa diferença que evita lançar o mesmo título duas vezes.

Cada item guarda estado, ID no Ongsys, hash dos dados de origem, tentativas e
horários. Um título em estado que já pode existir no Ongsys nunca é recriado
automaticamente: ele volta como pendência para consulta.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .modelos import Estado, Titulo

_VERSAO_ARQUIVO = 2


@dataclass
class ItemCheckpoint:
    chave: str
    hash_origem: str
    estado: str = Estado.PLANEJADO
    id_ongsys: str = ""
    documento_original: str = ""
    documento_final: str = ""
    fornecedor: str = ""
    valor: str = ""
    inicio: str = ""
    fim: str = ""
    tentativas: int = 0
    erro: str = ""
    resultado_auditoria: str = ""
    divergencias: list[str] = field(default_factory=list)

    @property
    def pode_existir_no_ongsys(self) -> bool:
        return self.estado in Estado.JA_PODE_EXISTIR


class Checkpoint:
    """Coleção de itens persistida em JSON, gravada de forma atômica."""

    def __init__(self, caminho: Path):
        self.caminho = caminho
        self.itens: dict[str, ItemCheckpoint] = {}
        self._carregar()

    # -- persistência ------------------------------------------------------

    def _carregar(self) -> None:
        if not self.caminho.exists():
            return
        try:
            bruto = json.loads(self.caminho.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            reserva = self.caminho.with_suffix(f".corrompido_{datetime.now():%Y%m%d_%H%M%S}.json")
            self.caminho.rename(reserva)
            raise RuntimeError(
                f"O arquivo de checkpoint estava corrompido e foi movido para {reserva.name}.\n"
                "Rode o modo planejar e confira no Ongsys o que já existe antes de executar."
            )

        if isinstance(bruto, list):
            # Formato antigo: apenas chaves concluídas, sem auditoria. Elas
            # entram como SALVO_NAO_AUDITADO porque não há prova de conferência.
            for chave in bruto:
                self.itens[chave] = ItemCheckpoint(
                    chave=chave,
                    hash_origem="",
                    estado=Estado.SALVO_NAO_AUDITADO,
                    erro="Importado de checkpoint antigo, sem auditoria registrada.",
                )
            return

        for dados in bruto.get("itens", []):
            item = ItemCheckpoint(**{k: v for k, v in dados.items() if k in ItemCheckpoint.__annotations__})
            self.itens[item.chave] = item

    def salvar(self) -> None:
        """Grava em arquivo temporário e substitui, para não perder o histórico."""
        conteudo = json.dumps(
            {
                "versao": _VERSAO_ARQUIVO,
                "atualizado_em": datetime.now().isoformat(timespec="seconds"),
                "itens": [asdict(x) for x in self.itens.values()],
            },
            ensure_ascii=False,
            indent=2,
        )
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.caminho.parent, delete=False, suffix=".tmp"
        ) as temporario:
            temporario.write(conteudo)
            caminho_temporario = Path(temporario.name)
        os.replace(caminho_temporario, self.caminho)

    # -- consultas ---------------------------------------------------------

    def obter(self, titulo: Titulo) -> ItemCheckpoint | None:
        return self.itens.get(titulo.chave)

    def estado_de(self, titulo: Titulo) -> str:
        item = self.itens.get(titulo.chave)
        return item.estado if item else ""

    def dados_mudaram(self, titulo: Titulo) -> bool:
        """True se a planilha mudou desde o registro anterior daquele título."""
        item = self.itens.get(titulo.chave)
        if item is None or not item.hash_origem:
            return False
        return item.hash_origem != titulo.hash_origem()

    def precisam_de_conferencia(self) -> list[ItemCheckpoint]:
        """Itens que podem ter ficado salvos sem auditoria (§20)."""
        return [
            x for x in self.itens.values()
            if x.estado in (Estado.EM_EXECUCAO, Estado.SALVO_NAO_AUDITADO)
        ]

    def sem_confirmacao_no_ongsys(self) -> list[ItemCheckpoint]:
        """Títulos que podem existir, mas sem ID confirmado.

        Só a auditoria (ou "Conferir ID") resolve: sem ID não se sabe se o
        título foi criado, e ele não pode ser relançado nem dado como pronto.
        """
        return [
            x for x in self.itens.values()
            if x.estado == Estado.EM_EXECUCAO
            or (x.estado == Estado.SALVO_NAO_AUDITADO and not x.id_ongsys)
        ]

    def salvos_com_divergencia(self) -> list[ItemCheckpoint]:
        """Títulos com ID que a auditoria abriu e achou alguma diferença.

        Em 21/09/2026 eram 60 dos 144 "sem auditoria": fornecedor com razão
        social diferente, título já pago, subprojeto inexistente etc. Precisam
        de revisão humana, não de nova auditoria.
        """
        return [
            x for x in self.itens.values()
            if x.estado == Estado.SALVO_NAO_AUDITADO and x.id_ongsys
        ]

    def contagem_por_estado(self) -> dict[str, int]:
        contagem: dict[str, int] = {}
        for item in self.itens.values():
            contagem[item.estado] = contagem.get(item.estado, 0) + 1
        return contagem

    # -- transições --------------------------------------------------------

    def registrar_planejado(self, titulo: Titulo) -> ItemCheckpoint:
        item = self.itens.get(titulo.chave)
        if item is None:
            item = ItemCheckpoint(
                chave=titulo.chave,
                hash_origem=titulo.hash_origem(),
                fornecedor=titulo.fornecedor,
                documento_original=titulo.documento_original,
                valor=str(titulo.valor),
            )
            self.itens[titulo.chave] = item
        return item

    def iniciar(self, titulo: Titulo) -> ItemCheckpoint:
        """Marca EM_EXECUCAO ANTES de tocar no formulário.

        Se o programa morrer no meio do salvamento, o item fica registrado como
        possivelmente existente e a próxima execução obriga uma consulta.
        """
        item = self.registrar_planejado(titulo)
        item.estado = Estado.EM_EXECUCAO
        item.hash_origem = titulo.hash_origem()
        item.fornecedor = titulo.fornecedor
        item.documento_original = titulo.documento_original
        item.valor = str(titulo.valor)
        item.inicio = datetime.now().isoformat(timespec="seconds")
        item.fim = ""
        item.erro = ""
        item.tentativas += 1
        self.salvar()
        return item

    def marcar_salvo(self, titulo: Titulo, id_ongsys: str, documento_final: str) -> ItemCheckpoint:
        item = self.registrar_planejado(titulo)
        item.estado = Estado.SALVO_NAO_AUDITADO
        item.id_ongsys = id_ongsys
        item.documento_final = documento_final
        item.fim = datetime.now().isoformat(timespec="seconds")
        self.salvar()
        return item

    def marcar_concluido(self, titulo: Titulo, divergencias: list[str]) -> ItemCheckpoint:
        """Só entra em CONCLUIDO quando a auditoria passou sem divergência (§19)."""
        item = self.registrar_planejado(titulo)
        if divergencias:
            item.estado = Estado.SALVO_NAO_AUDITADO
            item.resultado_auditoria = "AUDITORIA COM DIVERGENCIAS"
            item.divergencias = divergencias
        else:
            item.estado = Estado.CONCLUIDO
            item.resultado_auditoria = "AUDITORIA OK"
            item.divergencias = []
        item.fim = datetime.now().isoformat(timespec="seconds")
        self.salvar()
        return item

    def marcar_falha(self, titulo: Titulo, erro: str) -> ItemCheckpoint:
        item = self.registrar_planejado(titulo)
        # Um item que já pode existir no Ongsys nunca volta para FALHA: voltar
        # abriria caminho para uma nova tentativa e um título duplicado.
        if item.estado not in (Estado.SALVO_NAO_AUDITADO, Estado.CONCLUIDO):
            item.estado = Estado.FALHA
        item.erro = erro[:1000]
        item.fim = datetime.now().isoformat(timespec="seconds")
        self.salvar()
        return item

    def marcar_incerto_apos_salvar(self, titulo: Titulo, erro: str) -> ItemCheckpoint:
        """Mantém bloqueado um título quando houve erro após clicar Salvar.

        Sem confirmação de sucesso não se pode afirmar que foi salvo; porém,
        voltar para FALHA permitiria uma nova tentativa e poderia duplicá-lo.
        EM_EXECUCAO é o estado seguro até o modo auditar localizar o registro.
        """
        item = self.registrar_planejado(titulo)
        if item.estado not in (Estado.SALVO_NAO_AUDITADO, Estado.CONCLUIDO):
            item.estado = Estado.EM_EXECUCAO
        item.erro = ("Falha depois do clique em Salvar: " + erro)[:1000]
        item.fim = datetime.now().isoformat(timespec="seconds")
        self.salvar()
        return item

    def liberar_apos_confirmacao_manual(
        self, titulo: Titulo, observacao: str
    ) -> ItemCheckpoint:
        """Libera somente um EM_EXECUCAO sem ID após conferência humana.

        Esta transição é deliberadamente estreita: um item com ID, já salvo,
        concluído ou em qualquer outro estado nunca pode ser liberado por este
        caminho. O comando que a chama também cria uma cópia de segurança do
        checkpoint e exige confirmação digitada.
        """
        item = self.obter(titulo)
        if item is None:
            raise ValueError("O titulo nao existe no checkpoint.")
        if item.estado != Estado.EM_EXECUCAO or item.id_ongsys:
            raise ValueError(
                "Somente um titulo EM_EXECUCAO e sem ID pode ser liberado manualmente."
            )
        item.estado = Estado.LIBERADO_MANUALMENTE
        item.id_ongsys = ""
        item.documento_final = ""
        item.resultado_auditoria = "NAO LOCALIZADO; AUSENCIA CONFIRMADA MANUALMENTE"
        item.divergencias = []
        item.erro = observacao[:1000]
        item.fim = datetime.now().isoformat(timespec="seconds")
        self.salvar()
        return item

    def aceitar_divergencias(self, chave: str, observacao: str) -> ItemCheckpoint:
        """Encerra um título salvo COM ID cujas divergências o usuário revisou.

        Transição estreita, como a liberação manual: só vale para
        SALVO_NAO_AUDITADO com ID. O item vai para CONCLUIDO — continua
        protegido contra relançamento — e a lista de divergências é mantida
        como histórico. Nunca libera nada para uma nova tentativa.
        """
        item = self.itens.get(chave)
        if item is None:
            raise ValueError("O titulo nao existe no checkpoint.")
        if item.estado != Estado.SALVO_NAO_AUDITADO or not item.id_ongsys:
            raise ValueError(
                "Somente um titulo salvo, com ID no Ongsys e ainda pendente de "
                "revisao pode ter as divergencias aceitas."
            )
        item.estado = Estado.CONCLUIDO
        item.resultado_auditoria = "DIVERGENCIAS REVISADAS E ACEITAS PELO USUARIO"
        item.erro = observacao[:1000]
        item.fim = datetime.now().isoformat(timespec="seconds")
        self.salvar()
        return item

    def marcar_estado(self, titulo: Titulo, estado: str, observacao: str = "") -> ItemCheckpoint:
        item = self.registrar_planejado(titulo)
        item.estado = estado
        if observacao:
            item.erro = observacao[:1000]
        self.salvar()
        return item
