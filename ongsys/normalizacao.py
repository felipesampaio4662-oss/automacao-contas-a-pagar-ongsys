"""Normalização de texto, dinheiro, datas e códigos de despesa.

Tudo que compara nomes, códigos ou valores passa por aqui. Centralizar evita
que uma comparação frouxa apareça em um canto do código e cause seleção
errada no Ongsys.
"""

from __future__ import annotations

import re
import unicodedata
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

CENTAVO = Decimal("0.01")
QUATRO_CASAS = Decimal("0.0001")

# Um código de despesa é uma sequência de blocos numéricos separados por ponto:
# 2.2.1.05, 6.2.5.01, 3.3.1.08. Os zeros à esquerda são significativos, então o
# código é tratado como texto e nunca convertido para número.
_RE_CODIGO = re.compile(r"\d+(?:\.\d+)+")
_RE_ESPACOS = re.compile(r"\s+")
_RE_ESPACO_OU_VIRGULA = re.compile(r"[\s,;]+")

# Opções de autocomplete do Ongsys costumam vir como "2.5.1.04.007 - LIMPEZA
# (A)": um código, um nome e um sufixo entre parênteses no final.
_RE_PREFIXO_CODIGO_OPCAO = re.compile(r"^\s*[\d.]+\s*[-–]\s*")
_RE_SUFIXO_PARENTESES_OPCAO = re.compile(r"\s*\([ABCabc]\)\s*$")
# CPF (11 dígitos) ou CNPJ (14 dígitos) que o Ongsys às vezes exibe junto com
# o nome do fornecedor — com ou sem pontuação: "99999999999",
# "66.666.666/0001-66", "035.925.832/0001-01". Removê-los antes de comparar
# evita divergências falsas na auditoria.
_RE_CPF_CNPJ = re.compile(r"\b\d{2,3}[.\d]{6,}[-/\d]*\b")


def sem_acentos(valor: object) -> str:
    """Maiúsculas, sem acentos, sem espaços duplicados. Só para comparar."""
    texto = "" if valor is None else str(valor)
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return _RE_ESPACOS.sub(" ", texto).upper().strip()


def chave_comparacao_nome(valor: object) -> str:
    """Chave para comparar nomes cuja grafia difere só por espaço/pontuação.

    Além de tirar acentos e maiúsculas (via `sem_acentos`), remove todos os
    espaços. Necessário porque o cadastro do Ongsys pode ter espaçamento
    diferente da planilha — por exemplo "CONDUCAO /LOCOMOÇÃO" (com espaço antes
    da barra) contra "CONDUCAO/LOCOMOCAO" na planilha. Só para igualdade
    exata; não afeta a medida de semelhança.

    Vírgula e ponto e vírgula também são ignorados: a lista de sugestões do
    Ongsys mostra "TELEFONE INTERNET", enquanto o cadastro gravado e a planilha
    trazem "TELEFONE, INTERNET" (BENEFIX, 11/09/2026). Pontos e barras continuam
    valendo, porque separam códigos e nomes compostos.
    """
    return _RE_ESPACO_OU_VIRGULA.sub("", sem_acentos(valor))


def tokens(valor: object) -> set[str]:
    """Palavras significativas de um nome, para medir semelhança."""
    return {t for t in re.split(r"[^A-Z0-9]+", sem_acentos(valor)) if t}


#: Formas jurídicas que não distinguem um fornecedor de outro.
FORMAS_JURIDICAS = {"LTDA", "ME", "EPP", "EIRELI", "MEI", "SA", "SS"}


def tokens_distintivos_nome(nome: object) -> set[str]:
    """Tokens que identificam a entidade (ignora forma jurídica e siglas curtas)."""
    from_nome = nome_da_opcao(nome)
    return {t for t in tokens(from_nome) if len(t) > 2 and t not in FORMAS_JURIDICAS}


def siglas_de_iniciais(nome: object) -> set[str]:
    """Siglas formadas por letras soltas seguidas: "P. R. P." vira {"PRP"}.

    Essas letras identificam a empresa tanto quanto uma palavra, mas somem das
    comparações que ignoram tokens curtos. Uma letra isolada ("JOSE A ROCHA")
    e as formas jurídicas escritas com barra ("S/A", "S/S") não contam.
    """
    partes = [t for t in re.split(r"[^A-Z0-9]+", sem_acentos(nome_da_opcao(nome))) if t]
    siglas: set[str] = set()
    atual = ""
    for parte in [*partes, ""]:
        if len(parte) == 1 and parte.isalpha():
            atual += parte
            continue
        if len(atual) >= 2 and atual not in FORMAS_JURIDICAS:
            siglas.add(atual)
        atual = ""
    return siglas


def nomes_divergem(esperado: object, candidato: object) -> bool:
    """True quando os nomes indicam pessoas ou empresas diferentes.

    1) CADA nome tem um token distintivo que o outro não tem. Ex.: "ANA CLARA
       MOREIRA ROCHA" x "ANA DO CARMO ROCHA MOREIRA": 'CLARA' só está no
       primeiro e 'CARMO' só no segundo — pessoas diferentes que apenas
       compartilham sobrenomes. Um nome que é subconjunto do outro (abreviação)
       NÃO diverge. Usado para não criar/associar título ao fornecedor errado
       (2026-09-22 e 2026-09-24).
    2) O nome esperado traz uma sigla de iniciais ("P. R. P. APRENDIZAGENS") e
       o candidato não a tem, nem separada nem junta ("PRP"). Sem isso o nome
       esperado se reduzia a "APRENDIZAGENS" e casava com "COLABORATIVA
       APRENDIZAGENS LTDA", outra empresa (título 00034227, 2026-10-02).
    """
    siglas = siglas_de_iniciais(esperado)
    if siglas and not siglas <= (siglas_de_iniciais(candidato) | tokens(nome_da_opcao(candidato))):
        return True
    esp = tokens_distintivos_nome(esperado)
    cand = tokens_distintivos_nome(candidato)
    if not esp or not cand:
        return False
    return bool(esp - cand) and bool(cand - esp)


def normalizar_documento(valor: object) -> str:
    """Chave de comparação de documento: sem acentos e sem pontuação solta."""
    return re.sub(r"[^A-Z0-9]+", "", sem_acentos(valor))


def documento_permite_projetado(valor: object) -> bool:
    """Diz se o documento é de nota fiscal e pode virar conversão de projetado.

    Regra definida pelo usuário: só documentos identificados por um tipo
    (``NF 45``, ``FATURA 79055``, ``BOLETO.14``, ``RECIBO``) podem substituir um
    lançamento ``Projetado`` já existente. Documentos de período ou data —
    ``09_26`` (mês_ano de salários), ``01.07_26`` e ``31.08`` (cartão de
    crédito) — são recorrentes e não representam a nota que o projetado
    estimava; esses seguem o lançamento normal, sem editar nenhum projetado.

    O critério é simples e verificável: documentos de nota começam por letra;
    os de período/data começam por dígito. Documento vazio também não permite
    (o programa gera um documento de data a partir da efetivação).
    """
    texto = sem_acentos(valor).strip()
    if not texto:
        return False
    return texto[0].isalpha()


def documento_equivalente(
    esperado: object, encontrado: object, *, aceitar_variacao_numerica: bool = False
) -> bool:
    """Compara documentos e, opcionalmente, aceita o sufixo ``.N``.

    Quando o Ongsys avisa que um documento já existe, esta própria automação
    troca, por exemplo, ``RPA`` por ``RPA.1``. Se o salvamento for confirmado
    mas o processo cair antes de atualizar o checkpoint, a recuperação precisa
    reconhecer somente essa variação controlada — nunca um documento livre.
    """
    if normalizar_documento(esperado) == normalizar_documento(encontrado):
        return True
    if not aceitar_variacao_numerica:
        return False
    base = sem_acentos(esperado)
    atual = sem_acentos(encontrado)
    return bool(base and re.fullmatch(re.escape(base) + r"\.\d+", atual))


def nome_da_opcao(texto: object) -> str:
    """Extrai o nome de uma opção "CODIGO - NOME (SUFIXO)", sem o código

    inicial nem o sufixo entre parênteses no final. Uma opção como
    "2.5.1.04.007 - LIMPEZA (A)" vira "LIMPEZA".

    Também remove CPF/CNPJ embutidos no nome (ex.: "MIRELA MOURA SALOMAO
    99999999999" → "MIRELA MOURA SALOMAO") e pontos finais soltos (ex.:
    "LTDA." → "LTDA"), que o Ongsys às vezes inclui no cadastro.

    Sem isso, comparar pelo texto completo da opção nunca bate exatamente
    (o código e o sufixo sempre atrapalham), e cai para uma comparação por
    substring que empata entre opções parecidas — por exemplo "LIMPEZA" e
    "MATERIAL DE LIMPEZA" contêm as duas a palavra "limpeza".
    """
    s = str(texto or "").strip()
    s = _RE_PREFIXO_CODIGO_OPCAO.sub("", s)
    s = _RE_SUFIXO_PARENTESES_OPCAO.sub("", s)
    s = _RE_CPF_CNPJ.sub("", s)
    s = s.strip()
    # Remove um ponto final solto (LTDA. → LTDA), mas não reticências
    # ("Selecione..." deve permanecer intacto).
    if s.endswith(".") and not s.endswith(".."):
        s = s[:-1]
    return s.strip()


def dinheiro(valor: Decimal) -> Decimal:
    """Arredonda para centavos. Toda entrada de dinheiro passa por aqui.

    A planilha guarda floats com ruído (2555.6699999999996). Quantizar na
    leitura garante que as somas do título fechem exatamente com as somas dos
    rateios, em vez de fecharem "quase".
    """
    return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def para_decimal(valor: object) -> Decimal | None:
    """Converte célula de planilha em Decimal de centavos, ou None."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, str):
        texto = valor.strip()
        if not texto:
            return None
        # Aceita "1.234,56" e "1234.56".
        texto = re.sub(r"[^\d,.\-]", "", texto)
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        try:
            return dinheiro(Decimal(texto))
        except InvalidOperation:
            return None
    try:
        return dinheiro(Decimal(str(valor)))
    except (InvalidOperation, ValueError):
        return None


def para_percentual(valor: object) -> Decimal | None:
    """Converte o percentual exibido pelo Ongsys sem perder quatro casas.

    ``para_decimal`` é próprio para dinheiro e, por segurança, arredonda para
    centavos. Percentuais de rateio podem ter até quatro casas na regra de
    negócio, enquanto a tela também pode devolvê-los com o sufixo ``%``.
    """
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, str):
        texto = valor.strip()
        if not texto:
            return None
        texto = re.sub(r"[^\d,.\-]", "", texto)
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
    else:
        texto = str(valor)
    try:
        return Decimal(texto).quantize(QUATRO_CASAS, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


_RE_DINHEIRO_BR = re.compile(r"(?<![\d.,])\d{1,3}(?:\.\d{3})*,\d{2}(?![\d,])")


def valor_unico_em_linha(texto: object) -> Decimal | None:
    """Valor de uma linha da lista de Contas a Pagar, ou None se ambíguo.

    A linha tem um único número no formato de dinheiro (coluna Valor); CNPJ,
    datas e códigos não têm vírgula decimal. Zero ou vários valores distintos
    devolvem None, para o chamador não decidir com base em leitura incerta.
    """
    valores = {
        para_decimal(m.group(0).replace(".", "").replace(",", "."))
        for m in _RE_DINHEIRO_BR.finditer(str(texto or ""))
    }
    valores.discard(None)
    return valores.pop() if len(valores) == 1 else None


def formatar_dinheiro_br(valor: Decimal) -> str:
    """1234.5 -> '1.234,50', no formato que o formulário do Ongsys espera."""
    inteiro, _, centavos = f"{dinheiro(valor):.2f}".partition(".")
    negativo = inteiro.startswith("-")
    inteiro = inteiro.lstrip("-")
    grupos = []
    while len(inteiro) > 3:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    grupos.insert(0, inteiro)
    return ("-" if negativo else "") + ".".join(grupos) + "," + centavos


def formatar_percentual_br(valor: Decimal) -> str:
    return f"{valor.quantize(QUATRO_CASAS)}".replace(".", ",")


def para_data(valor: object) -> date | None:
    """Converte célula em date. Devolve None em vez de estourar exceção."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str):
        texto = valor.strip()
        for formato in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d.%m.%Y"):
            try:
                return datetime.strptime(texto, formato).date()
            except ValueError:
                continue
    return None


def data_br(valor: date) -> str:
    """Formato de exibição/relatório: dd/mm/aaaa. NÃO usar para preencher

    campos `<input type="date">` via automação — use `data_iso` para isso.
    """
    return valor.strftime("%d/%m/%Y")


def periodo_mensal(inicio: date, fim: date | None = None) -> tuple[date, date]:
    """Expande qualquer intervalo para meses completos.

    A lista do Ongsys responde de forma mais estável quando o filtro De/Até
    cobre o mês inteiro. Para 16/06/2026, por exemplo, devolve
    01/06/2026–30/06/2026.
    """
    fim_base = fim or inicio
    primeiro = inicio.replace(day=1)
    ultimo = fim_base.replace(day=monthrange(fim_base.year, fim_base.month)[1])
    return primeiro, ultimo


def competencia_br(valor: date) -> str:
    """Competência no formato de exibição/relatório: mm/aaaa.

    NÃO usar para preencher o campo Competência via automação — o Ongsys
    exibe "agosto de 2026" na tela, mas é um `<input type="month">`; use
    `competencia_iso` para preencher.
    """
    return valor.strftime("%m/%Y")


def data_iso(valor: date) -> str:
    """Formato exigido por `<input type="date">` ao preencher via automação.

    O navegador MOSTRA a data no formato local (dd/mm/aaaa), mas o valor
    interno do elemento — o que precisa ser enviado ao preencher o campo — é
    sempre AAAA-MM-DD (padrão HTML5), não o formato exibido na tela. Preencher
    com dd/mm/aaaa faz o navegador rejeitar como "valor malformado".
    """
    return valor.isoformat()


def competencia_iso(valor: date) -> str:
    """Formato exigido por `<input type="month">` (Competência) ao preencher.

    Mesmo caso de `data_iso`: a tela mostra "agosto de 2026", mas o valor
    interno esperado é AAAA-MM.
    """
    return valor.strftime("%Y-%m")


def codigos_em(texto: object) -> list[str]:
    """Extrai todos os códigos de despesa presentes em um texto de opção.

    '2.2.1.05 - COPA E COZINHA' -> ['2.2.1.05']
    """
    return _RE_CODIGO.findall(str(texto or ""))


def codigo_confere(esperado: str, texto_opcao: str) -> bool:
    """Regra §13: a opção só serve se contiver EXATAMENTE o código esperado.

    O teste ingênuo `esperado in texto_opcao` foi a causa do incidente relatado
    no handoff: '2.2.1.05' está contido em '12.2.1.05 - ESTACIONAMENTO' e em
    '2.2.1.050 - OUTRO'. Comparar a lista de códigos extraídos elimina os dois
    casos, porque '12.2.1.05' != '2.2.1.05' e '2.2.1.050' != '2.2.1.05'.
    """
    esperado = str(esperado or "").strip()
    if not esperado:
        return False
    return esperado in codigos_em(texto_opcao)


def semelhanca(esperado: object, candidato: object) -> float:
    """Cobertura dos tokens esperados dentro do candidato, entre 0 e 1.

    Palavras de até 2 letras são ignoradas porque 'DE', 'DA' e 'E' aparecem em
    quase todo nome de fornecedor e inflariam a semelhança.
    """
    esperados = {t for t in tokens(esperado) if len(t) > 2}
    if not esperados:
        return 0.0
    encontrados = tokens(candidato)
    if sem_acentos(esperado) and sem_acentos(esperado) in sem_acentos(candidato):
        return 1.0
    return len(esperados & encontrados) / len(esperados)


def contem_palavra(agulha: str, palheiro: str) -> bool:
    """Substring com fronteira de palavra, para regras de exclusão.

    Evita que a retenção 'PCC' exclua um fornecedor chamado 'PCCOMEX'.
    """
    agulha_n = sem_acentos(agulha)
    if not agulha_n:
        return False
    padrao = r"(?<![A-Z0-9])" + re.escape(agulha_n) + r"(?![A-Z0-9])"
    return re.search(padrao, sem_acentos(palheiro)) is not None
