"""Seletores da tela do Ongsys, reunidos em um único lugar.

Quando o site mudar, este é o único arquivo que precisa ser ajustado.

IMPORTANTE: os seletores abaixo vieram do código anterior e da observação
manual descrita no handoff. Eles ainda NÃO foram confirmados contra a tela real
por esta versão do programa. O modo `conferir-tela` existe justamente para
checar cada um antes de qualquer lançamento.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Lista e navegação
# --------------------------------------------------------------------------
BOTAO_INCLUIR = "button:has-text('Incluir'):visible, a:has-text('Incluir'):visible"
MENU_INCLUIR_CONTAS_PAGAR = ".include-contas-pagar"
CAMPO_BUSCA_FORNECEDOR = "input[placeholder*='fornecedor']:visible"
CAMPO_BUSCA_FORNECEDOR_FALLBACK = "#field_busca:visible, input[name='busca']:visible"
# Campo "Buscar:" da própria tabela. É diferente de "Filtrar por fornecedor"
# e aceita o Cód. Lançamento (CP...), usado para reabrir uma linha específica.
CAMPO_BUSCA_TABELA_DATATABLES = ".dataTables_filter input[type='search']:visible"
CAMPO_BUSCA_TABELA_ROTULO = "label:has-text('Buscar') input:visible"
CAMPO_BUSCA_TABELA_FALLBACK = "input[type='search']:visible"
CAMPO_BUSCA_TABELA = ", ".join(
    (
        CAMPO_BUSCA_TABELA_DATATABLES,
        CAMPO_BUSCA_TABELA_ROTULO,
        CAMPO_BUSCA_TABELA_FALLBACK,
    )
)
# Nome antigo mantido como alias: todas as pesquisas de duplicidade e
# descoberta inicial devem continuar buscando o NOME do fornecedor.
CAMPO_BUSCA_LISTA = CAMPO_BUSCA_FORNECEDOR
# Depois de selecionar o fornecedor no autocomplete, a tela só gera a lista
# filtrada quando o comando "Ver relatório" é acionado. O ícone foi confirmado
# na tela real como ``<i class="fa fa-file-text">``.
ICONE_VER_RELATORIO = "i.fa.fa-file-text:visible, i.fa-file-text:visible"
# A auditoria abre o formulário existente pelo lápis da própria linha, mas
# nunca aciona Salvar. O seletor fica restrito à linha já identificada pelo ID.
ACAO_EDITAR_TITULO = (
    "a[title*='Editar']:visible, button[title*='Editar']:visible, "
    "a:has(i[class*='pencil']):visible, button:has(i[class*='pencil']):visible, "
    "a:has(i.fa-edit):visible, button:has(i.fa-edit):visible, "
    "i[class*='pencil']:visible, i.fa-edit:visible"
)
# Fallback para títulos cujo status impede o lápis de edição mas permite
# visualização. Evita seletores amplos como ``title*='Ver'`` porque eles
# casam com "Ver arquivos" e abrem o popup de anexos em vez do formulário.
# A cada tentativa o chamador deve verificar se o formulário correto abriu
# e fechar popups inesperados antes de tentar o próximo ícone.
ACAO_VISUALIZAR_TITULO = (
    "a[title*='Visualizar']:visible, button[title*='Visualizar']:visible, "
    "a[title*='Detalh']:visible, button[title*='Detalh']:visible, "
    "a:has(i.fa-eye):visible, button:has(i.fa-eye):visible, "
    "a:has(i.fa-search-plus):visible, button:has(i.fa-search-plus):visible, "
    "i.fa-eye:visible, i.fa-search-plus:visible"
)
LINHAS_DA_LISTA = "table tbody tr"
# Na lista principal há dois campos visíveis De/Até. O navegador mantém o
# último mês consultado (no piloto, agosto), então buscas por títulos de junho
# precisam ajustar esse período antes de filtrar o fornecedor. Na tela real,
# apesar do ícone de calendário, esses campos são inputs de texto mascarados
# em dd/mm/aaaa (não input[type=date]). O código filtra esta coleção pelo valor
# de data para distinguir os dois campos dos demais inputs da lista.
FILTROS_DATA_LISTA = "input:visible"
# O primeiro seletor da barra define qual data orienta De/Até. A automação
# identifica o controle pelas opções Emissão/Vencimento e confirma a seleção.
FILTRO_TIPO_DATA_LISTA = "select:visible"

# --------------------------------------------------------------------------
# Cabeçalho do título
# --------------------------------------------------------------------------
CAMPO_FORNECEDOR = "#field_fornecedor"
CAMPO_HISTORICO = "#field_descricao_despesa"
CAMPO_TIPO_DESPESA = "#field_tipo_despesa"
CAMPO_DATA_EMISSAO = "#field_data_emissao"
CAMPO_DATA_VENCIMENTO = "#field_data_vencimento"
CAMPO_DATA_EFETIVACAO = "#field_data_efetivacao"
CAMPO_VALOR = "#field_valor_despesa"
CAMPO_COMPETENCIA = "#field_inicio_competencia"
CAMPO_TIPO_LANCAMENTO = "#field_tipo_lancamento"
CAMPO_TIPO_DOCUMENTO = "#field_tipo_documento"
CAMPO_NUMERO_DOCUMENTO = "#field_numero_documento"
CAMPO_MEIO_PAGAMENTO = "#field_meio_pagamento"
CAMPO_TIPO_PAGAMENTO = "#field_tipo_pagamento_eletronico"
CAMPO_CONTA_PAGADORA = "#field_conta_pagadora"
CAMPO_CODIGO_BARRAS = "#field_codigo_barras"

BOTAO_SALVAR = "#btn_salvar"
BOTAO_SALVAR_EDICAO = "#btn_editar:visible"

#: Lista de sugestões dos campos com autocomplete.
OPCOES_AUTOCOMPLETE = ".typeahead__list:visible a"

#: `#modalErro` é o popup de erro real do Ongsys (confirmado em uso real,
#: classe "modal-danger modal fade erro-modal-sm in") — usado tanto para o
#: aviso de documento duplicado quanto, possivelmente, outros erros. Nenhuma
#: classe genérica de alerta (.help-block, .alert, .alert-danger) captura o
#: texto de dentro dele, por isso o ID entra explícito na lista.
MENSAGENS = (
    "#modalErro:visible, .help-block:visible, .alert:visible, "
    ".alert-danger:visible, .swal2-html-container:visible"
)

#: Contêineres de popup conhecidos. Cobre Bootstrap 3 (.modal.in) e 4/5
#: (.modal.show), SweetAlert2, diálogos genéricos com role="dialog" e o
#: popup de erro real do Ongsys (#modalErro) — porque os avisos observados em
#: uso real (fornecedor sem serviço parametrizado, documento duplicado) vêm
#: sempre por ele, com classes que não batem de forma confiável com nenhum
#: seletor genérico isolado.
CONTEINERES_POPUP = (
    "#modalErro, .modal.show, .modal.in, .modal.fade.in, .swal2-container, "
    ".swal2-popup, .ui-dialog, [role='dialog']"
)

#: Botões que fecham um popup, sem precisar saber o rótulo exato usado.
#: Usado sempre com a busca já restrita ao próprio popup (nunca à página
#: inteira — o formulário principal tem seu próprio botão "Fechar", legítimo
#: e perigoso de clicar por engano).
#:
#: BUG REAL CORRIGIDO: a versão anterior misturava `text='Fechar'` (motor de
#: texto do Playwright) direto num seletor CSS separado por vírgula — isso é
#: sintaxe INVÁLIDA e o Playwright lançava exceção ao avaliar o seletor.
#: Como toda chamada que usa isto está protegida por `try/except`, a exceção
#: era engolida silenciosamente: a busca pelo botão nunca funcionou, nenhuma
#: vez, sem nenhum erro visível — essa foi a causa real de o popup nunca
#: fechar em uso real, por trás de várias tentativas de correção anteriores.
#: A forma certa de casar por texto sem depender da tag é o pseudo-seletor
#: CSS `:text(...)` do Playwright (não `text=...`), que combina normalmente
#: com o resto via vírgula — confirmado com um teste direto do Playwright,
#: não apenas por leitura da documentação.
POPUP_FECHAR = (
    ":text('OK'), :text('Ok'), :text('Fechar'), :text('Entendi'), "
    ":text('Continuar'), :text('Confirmar'), "
    ".swal2-confirm, [data-dismiss='modal'], [aria-label='Close']"
)

#: Abas do formulário de inclusão (Dados do pagamento / Rateio e
#: Parcelamento / Impostos Retidos). Cada aba esconde os campos das outras —
#: por exemplo, o Número do Documento (aba "Dados do pagamento") fica
#: inacessível enquanto "Rateio e Parcelamento" estiver ativa, confirmado em
#: uso real: o aviso de documento duplicado pode aparecer só depois de já
#: estar na aba de rateio, e reescrever o documento exige voltar para a
#: primeira aba antes de preencher.
ABA_DADOS_PAGAMENTO = "text='Dados do pagamento'"
ABA_RATEIO_PARCELAMENTO = "text='Rateio e Parcelamento'"

# --------------------------------------------------------------------------
# Linhas de rateio (§12). O índice começa em 1.
# --------------------------------------------------------------------------
def categoria_conta(indice: int) -> str:
    return f"#field_categoria_conta_{indice}"


def projeto_cc(indice: int) -> str:
    return f"#field_projeto_cc_{indice}"


def subprojeto(indice: int) -> str:
    return f"#field_subprojeto_{indice}"


def atividade(indice: int) -> str:
    return f"#field_atividade_{indice}"


def valor_rateio(indice: int) -> str:
    return f"#field_valor_rateio_{indice}"


def percentual_rateio(indice: int) -> str:
    return f"#field_percentual_rateio_{indice}"


def botao_incluir_linha(indice: int) -> str:
    # Compatibilidade com diagnósticos antigos. Na tela real existe somente o
    # botão superior #btn_clone_1; o índice não acompanha a linha anterior.
    return "#btn_clone_1:visible"


# Na tela real existe um botão superior estável com o texto "Incluir Linha".
# Os ícones de clonagem de cada linha continuam como fallback, pois podem ter
# IDs dinâmicos ou ficar temporariamente indisponíveis em rateios longos.
BOTAO_INCLUIR_LINHA_RATEIO = "#btn_clone_1:visible"


#: Campos obrigatórios do cabeçalho, conferidos antes de qualquer preenchimento.
CAMPOS_ESSENCIAIS = {
    "Fornecedor": CAMPO_FORNECEDOR,
    "Histórico da despesa": CAMPO_HISTORICO,
    "Tipo de despesa": CAMPO_TIPO_DESPESA,
    "Data de emissão": CAMPO_DATA_EMISSAO,
    "Data de vencimento": CAMPO_DATA_VENCIMENTO,
    "Data de efetivação": CAMPO_DATA_EFETIVACAO,
    "Valor da despesa": CAMPO_VALOR,
    "Número do documento": CAMPO_NUMERO_DOCUMENTO,
    "Conta pagadora": CAMPO_CONTA_PAGADORA,
    "Botão salvar": BOTAO_SALVAR,
}

# --------------------------------------------------------------------------
# Ações proibidas (§17)
# --------------------------------------------------------------------------
#: Trechos de texto que identificam controles de baixa, pagamento, remessa e
#: autorização. Nenhum clique do programa pode cair em um destes.
TEXTOS_PROIBIDOS = (
    "INFORMAR DADOS DE PAGAMENTO",
    "DADOS DE PAGAMENTO",
    "DAR BAIXA",
    "BAIXAR",
    "BAIXA",
    "MARCAR COMO PAGO",
    "CONFIRMAR PAGAMENTO",
    "EFETUAR PAGAMENTO",
    "REALIZAR PAGAMENTO",
    "REMESSA",
    "ENVIAR PARA AUTORIZACAO",
    "ENVIAR PARA APROVACAO",
    "AUTORIZAR PAGAMENTO",
    "APROVAR PAGAMENTO",
)

#: Seletores que o programa se recusa a acionar, mesmo se pedidos por engano.
SELETORES_PROIBIDOS = (
    "#btn_informar_pagamento",
    "#btn_baixar",
    "#btn_baixa",
    "#btn_remessa",
    "#btn_autorizar",
    "#btn_aprovar",
    "#field_data_pagamento",
    "#field_data_baixa",
)


#: Trechos que, no TEXTO de um aviso, indicam uma confirmação destrutiva ou
#: financeira ("Deseja excluir?", "Confirmar pagamento?"). Um aviso assim nunca
#: recebe OK/Confirmar/Continuar da automação — só pode ser dispensado.
TEXTOS_CONFIRMACAO_SENSIVEL = (
    "EXCLU", "APAGAR", "DELET", "ESTORN",
    "DAR BAIXA", "BAIXAR",
    "CONFIRMAR PAGAMENTO", "EFETUAR PAGAMENTO", "REALIZAR PAGAMENTO",
    "MARCAR COMO PAGO", "REMESSA",
    "ENVIAR PARA AUTORIZACAO", "ENVIAR PARA APROVACAO",
)

#: Rótulos que apenas dispensam um aviso, sem confirmar nada.
ROTULOS_SO_DISPENSA = ("FECHAR", "CANCELAR", "NAO", "VOLTAR")

#: Marcas (título/classe do ícone) de ações de linha que a automação nunca
#: aciona: excluir, baixar/pagar, remeter, estornar, aprovar.
MARCAS_ACAO_DE_LINHA_PROIBIDA = (
    "TRASH", "EXCLU", "DELET", "REMOV", "BAIX", "ESTORN", "REMESSA",
    "FA-USD", "FA-MONEY", "FA-DOLLAR", "PAPER-PLANE", "APROV", "AUTORIZ",
)


def _maiusculas_sem_acento(texto: object) -> str:
    import unicodedata

    bruto = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in bruto if not unicodedata.combining(c)).upper()


def popup_exige_cautela(texto: object) -> bool:
    """True quando o aviso fala de exclusão, baixa, pagamento ou remessa."""
    normalizado = _maiusculas_sem_acento(texto)
    return any(marca in normalizado for marca in TEXTOS_CONFIRMACAO_SENSIVEL)


def rotulo_so_dispensa(rotulo: object) -> bool:
    """True para botões que só fecham o aviso (Fechar, Cancelar, Não, ×)."""
    normalizado = " ".join(_maiusculas_sem_acento(rotulo).split())
    if normalizado in ("", "X", "×"):
        return True
    return normalizado.split()[0] in ROTULOS_SO_DISPENSA


def acao_de_linha_proibida(descricao: object) -> bool:
    """True quando título/classe do ícone indica excluir, baixar ou remeter."""
    normalizado = _maiusculas_sem_acento(descricao)
    return any(marca in normalizado for marca in MARCAS_ACAO_DE_LINHA_PROIBIDA)


class AcaoProibida(BaseException):
    """Tentativa de acionar algo relacionado a baixa ou pagamento.

    Deriva de ``BaseException`` de propósito: é um disjuntor de segurança, e
    nenhum ``except Exception`` do preenchimento pode engoli-lo ou tratá-lo
    como falha comum de um título. Quando dispara, a execução inteira para.
    """


def conferir_permitido(alvo: str) -> None:
    """Trava de segurança do §17, chamada antes de todo clique e preenchimento.

    É uma verificação redundante de propósito: o código não deveria chegar a
    esses controles, e se chegar por causa de uma mudança no site, a execução
    para em vez de dar baixa em um título.
    """
    alvo_normalizado = alvo.upper()
    for proibido in SELETORES_PROIBIDOS:
        if proibido.upper() in alvo_normalizado:
            raise AcaoProibida(
                f"O programa tentou acionar um controle de baixa/pagamento: {alvo}.\n"
                "A execução foi interrompida. Esta automação só cadastra títulos."
            )
    for texto in TEXTOS_PROIBIDOS:
        if texto in alvo_normalizado:
            raise AcaoProibida(
                f"O programa tentou acionar algo relacionado a pagamento: {alvo}.\n"
                "A execução foi interrompida. Esta automação só cadastra títulos."
            )
