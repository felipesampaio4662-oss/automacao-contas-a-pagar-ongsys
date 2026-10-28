"""Leitura do arquivo .xlsx.

Duas responsabilidades delicadas ficam aqui:

1. localizar as colunas pelo nome do cabeçalho, porque os índices mudam entre
   arquivos (§4);
2. classificar as linhas pela cor da célula do fornecedor, parando o programa
   quando o arquivo não tem nenhuma das cores esperadas — assumir que "tudo é
   pendente" relançaria a planilha inteira.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from .modelos import LinhaPlanilha
from .normalizacao import para_data, para_decimal, sem_acentos


class ErroPlanilha(Exception):
    """Problema estrutural na planilha, com mensagem pronta para o usuário."""


#: campo interno -> nomes aceitos no cabeçalho, já normalizados na comparação.
SINONIMOS: dict[str, tuple[str, ...]] = {
    "codigo_despesa": ("COD.ORC.", "CODIGO ORCAMENTO", "COD ORC", "CODIGO DE DESPESA",
                       "CODIGO DESPESA", "COD ORCAMENTO", "CODIGO"),
    "atividade": ("RATEIO", "ATIVIDADE", "RATEIO/ATIVIDADE"),
    "emissao": ("EMISSAO", "DATA EMISSAO", "DT EMISSAO", "DATA DE EMISSAO"),
    "vencimento": ("VENCIMENTO", "DATA VENCIMENTO", "DT VENCIMENTO", "DATA DE VENCIMENTO"),
    "efetivacao": ("PAGAMENTO", "EFETIVACAO", "DATA PAGAMENTO", "DATA EFETIVACAO",
                   "PREVISAO PAGAMENTO", "DATA DE PAGAMENTO"),
    "fornecedor": ("FORNECEDOR", "BENEFICIARIO", "CREDOR", "FAVORECIDO"),
    "documento": ("DOCUMENTO", "NUMERO DOCUMENTO", "N DOCUMENTO", "NF", "DOC"),
    "centro_custo": ("CC", "CENTRO DE CUSTO", "CENTRO CUSTO", "PROJETO", "PROJETO/CC"),
    "categoria_titulo": ("TITULO CATEGORIA", "TITULO DA CATEGORIA", "CATEGORIA TITULO",
                         "GRUPO CATEGORIA", "NATUREZA"),
    "categoria_detalhada": ("CATEGORIA DETALHADA", "CATEGORIA", "HISTORICO",
                            "DESCRICAO", "CATEGORIA DETALHE"),
    "entrada": ("ENTRADA", "CREDITO", "RECEITA"),
    "saida": ("SAIDA", "DEBITO", "VALOR", "DESPESA", "VALOR DA DESPESA"),
    "codigo_barras": ("CODIGO DE BARRAS", "COD BARRAS", "CODIGO BARRAS",
                      "COD DE BARRAS", "CODIGO DE BARRA", "COD BARRA"),
}

#: Sem estes campos não existe título de contas a pagar.
OBRIGATORIOS = ("emissao", "vencimento", "efetivacao", "fornecedor", "saida")

_MAX_LINHAS_CABECALHO = 15


def cor_da_celula(celula) -> str:
    """Cor de preenchimento em ARGB, ou um rótulo explicativo.

    Cores de tema e indexadas não têm valor ARGB estável no arquivo, então são
    devolvidas com um rótulo próprio em vez de virarem string vazia. Assim o
    relatório mostra "TEMA:4" e o usuário entende por que nada foi encontrado.
    """
    preenchimento = celula.fill
    if preenchimento is None or preenchimento.patternType is None:
        return "SEM_COR"
    cor = preenchimento.fgColor
    if cor is None:
        return "SEM_COR"
    if cor.type == "rgb" and cor.rgb:
        return str(cor.rgb).upper()
    if cor.type == "theme":
        return f"TEMA:{cor.theme}"
    if cor.type == "indexed":
        return f"INDEXADA:{cor.indexed}"
    return "SEM_COR"


def detectar_cabecalho(ws) -> tuple[int, dict[str, int]]:
    """Descobre a linha do cabeçalho e o índice de cada coluna conhecida.

    Percorre as primeiras linhas e fica com a que reconhecer mais campos. Não
    depende de o cabeçalho estar na linha 3.
    """
    melhor_linha = 0
    melhor_mapa: dict[str, int] = {}

    limite = min(_MAX_LINHAS_CABECALHO, ws.max_row)
    for linha in range(1, limite + 1):
        mapa: dict[str, int] = {}
        for coluna in range(1, ws.max_column + 1):
            titulo = sem_acentos(ws.cell(linha, coluna).value).rstrip(".:")
            if not titulo:
                continue
            for campo, nomes in SINONIMOS.items():
                if campo in mapa:
                    continue
                if any(titulo == sem_acentos(nome).rstrip(".:") for nome in nomes):
                    mapa[campo] = coluna
                    break
        if len(mapa) > len(melhor_mapa):
            melhor_linha, melhor_mapa = linha, mapa

    if not melhor_mapa:
        raise ErroPlanilha(
            "Não foi possível localizar o cabeçalho da planilha.\n"
            f"Foram examinadas as {limite} primeiras linhas da aba e nenhuma coluna "
            "conhecida (Fornecedor, Vencimento, Saida...) foi reconhecida.\n"
            "Confirme se a aba correta foi informada."
        )

    faltando = [c for c in OBRIGATORIOS if c not in melhor_mapa]
    if faltando:
        encontradas = ", ".join(sorted(melhor_mapa)) or "nenhuma"
        raise ErroPlanilha(
            f"O cabeçalho foi encontrado na linha {melhor_linha}, mas faltam colunas "
            f"essenciais: {', '.join(faltando)}.\n"
            f"Colunas reconhecidas: {encontradas}.\n"
            "Renomeie o cabeçalho na planilha ou informe as colunas manualmente "
            "com a opção --coluna campo=letra."
        )
    return melhor_linha, melhor_mapa


def aplicar_colunas_manuais(mapa: dict[str, int], manuais: dict[str, str]) -> dict[str, int]:
    """Sobrescreve colunas detectadas com as informadas pelo usuário (B, C, ...)."""
    mapa = dict(mapa)
    for campo, letra in manuais.items():
        if campo not in SINONIMOS:
            raise ErroPlanilha(
                f"Campo desconhecido em --coluna: {campo}.\n"
                f"Campos válidos: {', '.join(sorted(SINONIMOS))}."
            )
        letra = letra.strip().upper()
        if not letra.isalpha():
            raise ErroPlanilha(
                f"A coluna de {campo} precisa ser uma letra, como B. Recebido: {letra!r}."
            )
        indice = 0
        for caractere in letra:
            indice = indice * 26 + (ord(caractere) - ord("A") + 1)
        mapa[campo] = indice
    return mapa


def _texto(ws, linha: int, mapa: dict[str, int], campo: str) -> str:
    coluna = mapa.get(campo)
    if not coluna:
        return ""
    valor = ws.cell(linha, coluna).value
    return "" if valor is None else str(valor).strip()


def ler_linhas(
    caminho: Path,
    aba: str | None = None,
    colunas_manuais: dict[str, str] | None = None,
) -> tuple[list[LinhaPlanilha], dict[str, int], int, str]:
    """Lê a planilha inteira e devolve as linhas brutas convertidas.

    Não aplica filtro de período, de cor ou de exclusão: isso é trabalho do
    módulo de regras, para que o relatório possa explicar cada descarte.
    """
    if not caminho.exists():
        raise ErroPlanilha(f"A planilha não foi encontrada: {caminho}")
    if caminho.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ErroPlanilha(
            f"Formato não suportado: {caminho.suffix or 'sem extensão'}.\n"
            "Salve o arquivo como .xlsx antes de continuar."
        )

    livro = load_workbook(caminho, data_only=True)
    if aba:
        if aba not in livro.sheetnames:
            raise ErroPlanilha(
                f"A aba {aba} não existe neste arquivo.\n"
                f"Abas disponíveis: {', '.join(livro.sheetnames)}."
            )
        ws = livro[aba]
    else:
        ws = livro[livro.sheetnames[0]]

    linha_cabecalho, mapa = detectar_cabecalho(ws)
    if colunas_manuais:
        mapa = aplicar_colunas_manuais(mapa, colunas_manuais)

    coluna_fornecedor = mapa["fornecedor"]
    linhas: list[LinhaPlanilha] = []
    for numero in range(linha_cabecalho + 1, ws.max_row + 1):
        linhas.append(
            LinhaPlanilha(
                linha=numero,
                codigo_despesa=_texto(ws, numero, mapa, "codigo_despesa"),
                atividade=_texto(ws, numero, mapa, "atividade"),
                emissao=para_data(ws.cell(numero, mapa["emissao"]).value),
                vencimento=para_data(ws.cell(numero, mapa["vencimento"]).value),
                efetivacao=para_data(ws.cell(numero, mapa["efetivacao"]).value),
                fornecedor=_texto(ws, numero, mapa, "fornecedor"),
                documento=_texto(ws, numero, mapa, "documento"),
                centro_custo=_texto(ws, numero, mapa, "centro_custo"),
                categoria_titulo=_texto(ws, numero, mapa, "categoria_titulo"),
                categoria_detalhada=_texto(ws, numero, mapa, "categoria_detalhada"),
                entrada=para_decimal(ws.cell(numero, mapa["entrada"]).value) if mapa.get("entrada") else None,
                saida=para_decimal(ws.cell(numero, mapa["saida"]).value),
                codigo_barras=_texto(ws, numero, mapa, "codigo_barras"),
                cor=cor_da_celula(ws.cell(numero, coluna_fornecedor)),
            )
        )
    livro.close()
    return linhas, mapa, linha_cabecalho, ws.title


def periodo_das_linhas(linhas: list[LinhaPlanilha]) -> tuple[date | None, date | None]:
    """Menor e maior data de efetivação presentes, para orientar o usuário."""
    datas = [x.efetivacao for x in linhas if x.efetivacao]
    return (min(datas), max(datas)) if datas else (None, None)
