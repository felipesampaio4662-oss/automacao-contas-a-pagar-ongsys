"""Leituras do painel que não dependem de janela, para poderem ser testadas.

Nada aqui acessa o Ongsys nem altera arquivos: só interpreta a saída do CLI,
os relatórios CSV e o checkpoint.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .modelos import Estado

# -- datas ----------------------------------------------------------------


def interpretar_data(texto: str) -> date | None:
    """Aceita DD/MM/AAAA (como o usuário escreve) ou AAAA-MM-DD."""
    texto = (texto or "").strip()
    if not texto:
        return None
    for formato in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    raise ValueError(f"Data inválida: '{texto}'. Use DD/MM/AAAA.")


def formatar_data(valor: date | None) -> str:
    return valor.strftime("%d/%m/%Y") if valor else ""


# -- acompanhamento -------------------------------------------------------

_RE_PROGRESSO = re.compile(r"^\[(\d+)/(\d+)\]\s+(.+?)\s*$", re.MULTILINE)


def extrair_progresso(texto: str) -> tuple[int, int, str] | None:
    """Último "[3/26] FORNECEDOR - DOC" visto na saída."""
    encontrados = list(_RE_PROGRESSO.finditer(texto))
    if not encontrados:
        return None
    ultimo = encontrados[-1]
    return int(ultimo.group(1)), int(ultimo.group(2)), ultimo.group(3)


def classificar_linha(linha: str) -> str | None:
    """Cor da linha no acompanhamento: falha, atencao, ok, info ou titulo."""
    texto = linha.strip()
    if not texto:
        return None
    maiusculo = texto.upper()
    if _RE_PROGRESSO.match(texto):
        return "titulo"
    if maiusculo.startswith(("FALHA", "EXECUCAO INTERROMPIDA", "ERRO")) or " FALHA:" in maiusculo:
        return "falha"
    if (
        maiusculo.startswith(("ATENCAO", "ADIADO", "AVISO", "* ", "AUDITORIA ENCONTROU"))
        or "CORRIGIR MANUALMENTE" in maiusculo
    ):
        return "atencao"
    if maiusculo.startswith(("SALVO.", "AUDITORIA CONFERIDA", "PROJETADO SALVO")):
        return "ok"
    if maiusculo.startswith("INFO:"):
        return "info"
    return None


_RE_PERGUNTA = re.compile(
    r"([^\n]*(?:digite|continuar|confirmar|pressione)[^\n]*:[ \t]+)$", re.IGNORECASE
)


def pergunta_pendente(buffer: str) -> str | None:
    """Pergunta que o programa deixou aberta na última linha, sem resposta.

    O CLI usa ``input("...: ")``: o texto fica sem quebra de linha no fim até
    o usuário responder.
    """
    if not buffer or buffer.endswith("\n"):
        return None
    encontrado = _RE_PERGUNTA.search(buffer[-400:])
    return encontrado.group(1).strip() if encontrado else None


# -- resultado da execução ------------------------------------------------

#: Situação -> (grupo exibido, cor)
SITUACOES = {
    "CONCLUIDO": ("Concluído", "ok"),
    "SALVO": ("Salvo", "ok"),
    "JA_EXISTE": ("Já existia", "info"),
    "SALVO_COM_DIVERGENCIA": ("Salvo com divergência", "atencao"),
    "ADIADO": ("Adiado", "info"),
    "FALHA": ("Falha", "falha"),
}


@dataclass(frozen=True)
class LinhaResultado:
    situacao: str
    fornecedor: str
    documento: str
    id_ongsys: str
    valor: str
    pendencias: list[str]
    avisos: list[str]
    mensagem: str
    screenshot: str

    @property
    def cor(self) -> str:
        cor = SITUACOES.get(self.situacao, ("", "info"))[1]
        if cor == "ok" and self.pendencias:
            return "atencao"
        return cor


def _dividir(texto: str) -> list[str]:
    return [parte.strip() for parte in (texto or "").split(" | ") if parte.strip()]


def ler_resultado(caminho: Path) -> list[LinhaResultado]:
    with caminho.open(encoding="utf-8-sig", newline="") as arquivo:
        return [
            LinhaResultado(
                situacao=(linha.get("situacao") or "").strip(),
                fornecedor=(linha.get("fornecedor") or "").strip(),
                documento=(linha.get("documento_final") or linha.get("documento_original") or "").strip(),
                id_ongsys=(linha.get("id_ongsys") or "").strip(),
                valor=(linha.get("valor") or "").strip(),
                pendencias=_dividir(linha.get("campos_nao_localizados", "")),
                avisos=_dividir(linha.get("avisos", "")),
                mensagem=(linha.get("mensagem") or "").strip(),
                screenshot=(linha.get("screenshot") or "").strip(),
            )
            for linha in csv.DictReader(arquivo, delimiter=";")
        ]


def resultado_mais_recente(pasta: Path, desde: float | None = None) -> Path | None:
    """CSV de resultado mais novo; com ``desde``, só um criado depois disso."""
    candidatos = sorted(
        pasta.glob("resultado_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for caminho in candidatos:
        if desde is None or caminho.stat().st_mtime >= desde:
            return caminho
        break
    return None


def resumir_resultado(linhas: list[LinhaResultado]) -> dict[str, int]:
    resumo = {"ok": 0, "atencao": 0, "falha": 0, "info": 0}
    for linha in linhas:
        resumo[linha.cor] = resumo.get(linha.cor, 0) + 1
    return resumo


# -- pendências do checkpoint --------------------------------------------

GRUPO_SEM_ID = "Sem ID confirmado"
GRUPO_DIVERGENCIA = "Salvo com divergência"
GRUPO_FALHA = "Falha — pode relançar"

ACOES = {
    GRUPO_SEM_ID: (
        "Pode existir no Ongsys. Rode Auditar com a planilha; se você lançou à mão, "
        "use Conferir ID. Se confirmou que NÃO existe, use Liberar pendência."
    ),
    GRUPO_DIVERGENCIA: (
        "Existe no Ongsys, com diferença. Confira o título; corrija lá se preciso e "
        "depois use Marcar como conferido."
    ),
    GRUPO_FALHA: (
        "Não foi criado. Entra de novo na próxima execução da planilha; se você "
        "lançou à mão, use Conferir ID."
    ),
}


@dataclass(frozen=True)
class Pendencia:
    grupo: str
    chave: str
    fornecedor: str
    documento: str
    valor: str
    id_ongsys: str
    estado: str
    detalhe: str
    data: str


def pendencias_do_checkpoint(itens) -> list[Pendencia]:
    """Agrupa o que precisa de ação humana, do mais urgente ao menos."""
    ordem = {GRUPO_SEM_ID: 0, GRUPO_DIVERGENCIA: 1, GRUPO_FALHA: 2}
    saida: list[Pendencia] = []
    for item in itens:
        if item.estado == Estado.EM_EXECUCAO or (
            item.estado == Estado.SALVO_NAO_AUDITADO and not item.id_ongsys
        ):
            grupo, detalhe = GRUPO_SEM_ID, item.erro or ", ".join(item.divergencias)
        elif item.estado == Estado.SALVO_NAO_AUDITADO:
            grupo, detalhe = GRUPO_DIVERGENCIA, ", ".join(item.divergencias) or item.erro
        elif item.estado == Estado.FALHA:
            grupo, detalhe = GRUPO_FALHA, item.erro
        else:
            continue
        saida.append(Pendencia(
            grupo=grupo,
            chave=item.chave,
            fornecedor=item.fornecedor,
            documento=item.documento_final or item.documento_original,
            valor=item.valor,
            id_ongsys=item.id_ongsys,
            estado=item.estado,
            detalhe=(detalhe or "").replace("\n", " ")[:300],
            data=(item.fim or item.inicio or "")[:10],
        ))
    saida.sort(key=lambda p: p.data, reverse=True)  # mais recentes primeiro
    saida.sort(key=lambda p: ordem[p.grupo])  # estável: mantém a data no grupo
    return saida
