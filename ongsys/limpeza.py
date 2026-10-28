"""Guarda temporária: lista e apaga capturas, relatórios e cópias antigas.

Capturas e relatórios contêm dados pessoais (nomes, CPF/CNPJ, valores). Eles
servem para conferir e auditar as execuções recentes; depois do prazo de guarda
não há motivo para mantê-los na máquina.

Este módulo só olha arquivos soltos dentro de pastas conhecidas e só aceita
extensões conhecidas. Nunca entra em subpastas, nunca toca no ``checkpoint.json``
(a memória que impede lançar em dobro), no ``config.json``, na sessão
(``perfil_ongsys/``) nem no histórico de execuções.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

DIAS_EVIDENCIAS = 30
DIAS_RELATORIOS = 90
# As cópias mais recentes do checkpoint ficam sempre, qualquer que seja a idade.
COPIAS_MINIMAS = 5

NUNCA_APAGAR = frozenset({"checkpoint.json", "config.json", "historico_execucoes.csv"})

GRUPO_EVIDENCIAS = "Capturas de tela"
GRUPO_RELATORIOS = "Relatórios"
GRUPO_COPIAS = "Cópias antigas do checkpoint"


@dataclass(frozen=True)
class Candidato:
    caminho: Path
    grupo: str
    modificado: datetime
    tamanho: int


def _soltos(pasta: Path, padroes: tuple[str, ...]) -> list[Path]:
    """Arquivos comuns diretamente dentro da pasta, pelos padrões aceitos."""
    if not pasta.is_dir():
        return []
    achados: dict[Path, None] = {}
    for padrao in padroes:
        for caminho in pasta.glob(padrao):
            if caminho.is_symlink() or not caminho.is_file():
                continue
            if caminho.name.lower() in NUNCA_APAGAR:
                continue
            achados[caminho] = None
    return list(achados)


def _candidato(caminho: Path, grupo: str) -> Candidato:
    dados = caminho.stat()
    return Candidato(caminho, grupo, datetime.fromtimestamp(dados.st_mtime), dados.st_size)


def candidatos(
    base: Path,
    agora: datetime | None = None,
    dias_evidencias: int = DIAS_EVIDENCIAS,
    dias_relatorios: int = DIAS_RELATORIOS,
    copias_minimas: int = COPIAS_MINIMAS,
) -> list[Candidato]:
    """Arquivos que passaram do prazo de guarda. Não apaga nada."""
    agora = agora or datetime.now()
    limite_evidencias = agora - timedelta(days=dias_evidencias)
    limite_relatorios = agora - timedelta(days=dias_relatorios)
    saida: list[Candidato] = []

    for caminho in _soltos(base / "evidencias", ("*.png",)):
        item = _candidato(caminho, GRUPO_EVIDENCIAS)
        if item.modificado < limite_evidencias:
            saida.append(item)

    for caminho in _soltos(base / "relatorios", ("*.csv", "*.xlsx")):
        item = _candidato(caminho, GRUPO_RELATORIOS)
        if item.modificado < limite_relatorios:
            saida.append(item)

    copias = [
        _candidato(caminho, GRUPO_COPIAS)
        for caminho in (
            _soltos(base / "backups", ("checkpoint_*.json",))
            + _soltos(base, ("checkpoint_antes_*.json",))
        )
    ]
    copias.sort(key=lambda item: item.modificado, reverse=True)
    for item in copias[max(copias_minimas, 0):]:
        if item.modificado < limite_relatorios:
            saida.append(item)

    return sorted(saida, key=lambda item: (item.grupo, item.modificado))


def _permitido(caminho: Path, base: Path) -> bool:
    """Última conferência antes de apagar: pasta, nome e tipo esperados."""
    if caminho.name.lower() in NUNCA_APAGAR or caminho.is_symlink() or not caminho.is_file():
        return False
    pai = caminho.resolve().parent
    raiz = base.resolve()
    nome = caminho.name.lower()
    if pai == raiz / "evidencias":
        return nome.endswith(".png")
    if pai == raiz / "relatorios":
        return nome.endswith((".csv", ".xlsx"))
    if pai == raiz / "backups":
        return nome.startswith("checkpoint_") and nome.endswith(".json")
    if pai == raiz:
        return nome.startswith("checkpoint_antes_") and nome.endswith(".json")
    return False


def apagar(itens: list[Candidato], base: Path) -> tuple[int, list[str]]:
    """Apaga em definitivo os candidatos. Devolve (apagados, falhas)."""
    apagados = 0
    falhas: list[str] = []
    for item in itens:
        if not _permitido(item.caminho, base):
            falhas.append(f"{item.caminho.name}: fora das pastas permitidas, mantido")
            continue
        try:
            item.caminho.unlink()
            apagados += 1
        except OSError as erro:
            falhas.append(f"{item.caminho.name}: {erro}")
    return apagados, falhas


def resumo(itens: list[Candidato]) -> list[tuple[str, int, int, datetime, datetime]]:
    """Por grupo: quantidade, bytes, mais antigo e mais recente."""
    grupos: dict[str, list[Candidato]] = {}
    for item in itens:
        grupos.setdefault(item.grupo, []).append(item)
    return [
        (
            grupo,
            len(lista),
            sum(x.tamanho for x in lista),
            min(x.modificado for x in lista),
            max(x.modificado for x in lista),
        )
        for grupo, lista in sorted(grupos.items())
    ]
