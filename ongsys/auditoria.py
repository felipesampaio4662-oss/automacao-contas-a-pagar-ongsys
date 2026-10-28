"""Auditoria pós-salvamento (§19).

O relatório antigo chamado "auditoria" apenas repetia o que o programa achava
que tinha feito. Isso não é auditoria: se a seleção errou, o relatório erra
junto. Aqui o título é reaberto no Ongsys e cada campo é lido de volta da tela
e comparado com o esperado.

A comparação de campos é feita por funções puras, testáveis sem navegador.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from . import seletores as S
from .modelos import Titulo
from .normalizacao import (
    FORMAS_JURIDICAS,
    codigo_confere,
    documento_equivalente,
    formatar_dinheiro_br,
    nome_da_opcao,
    para_data,
    para_decimal,
    sem_acentos,
)


def mesmo_nome_sem_forma_juridica(esperado: str, encontrado: str) -> bool:
    """Mesmo nome quando só muda a forma jurídica (LTDA, ME, EPP, SA...).

    "INOVARE CONSULTORIA EDUCACIONAL LTDA ME" (planilha) e "4056 - INOVARE
    CONSULTORIA EDUCACIONAL LTDA" (Ongsys) são o mesmo cadastro; a auditoria
    acusava divergência de fornecedor (título 00014332, 2026-10-15). Todas as
    outras palavras, na mesma ordem, continuam obrigatórias.
    """
    def palavras(texto: str) -> list[str]:
        return [
            p for p in re.split(r"[^A-Z0-9]+", sem_acentos(nome_da_opcao(texto)))
            if p and p not in FORMAS_JURIDICAS
        ]

    esperadas = palavras(esperado)
    return bool(esperadas) and esperadas == palavras(encontrado)


@dataclass
class Divergencia:
    campo: str
    esperado: str
    encontrado: str
    acao: str

    @property
    def linha_relatorio(self) -> dict:
        return {
            "campo": self.campo,
            "esperado": self.esperado,
            "encontrado": self.encontrado,
            "confere": "NAO",
            "acao_recomendada": self.acao,
        }


def comparar_texto(campo: str, esperado: str, encontrado: str, acao: str) -> Divergencia | None:
    # O Ongsys devolve os valores completos das opções, por exemplo
    # ``3939 - DENISE...``, ``2.5.1.04.007 - LIMPEZA`` e ainda acrescenta o
    # banco entre parênteses na conta pagadora. Esses trechos são metadados da
    # opção, não divergências do título. A comparação continua exata depois de
    # retirar somente o prefixo numérico e o sufixo entre parênteses.
    # Espaços ao redor da "/" não são diferença real: o Ongsys cadastra
    # "CONDUCAO /LOCOMOÇÃO" (com espaço antes da barra) e a planilha escreve
    # "CONDUCAO/LOCOMOCAO". Normalizar isso evita um falso "categoria diferente"
    # quando a categoria selecionada está, de fato, correta.
    # Vírgula também não é diferença real: "TELEFONE, INTERNET" (planilha) e
    # "TELEFONE INTERNET" (lista do Ongsys) são a mesma categoria. Mesma
    # regra de `chave_comparacao_nome`, usada no preenchimento.
    def _norm(texto: str) -> str:
        texto = re.sub(r"\s*/\s*", "/", sem_acentos(nome_da_opcao(texto)))
        return re.sub(r"\s+", " ", re.sub(r"\s*[,;]\s*", " ", texto)).strip()

    esperado_limpo = _norm(esperado)
    encontrado_limpo = _norm(encontrado)
    if esperado_limpo == encontrado_limpo:
        return None
    # A planilha pode usar uma abreviação do nome cadastrado no Ongsys.
    # Exemplo: planilha diz "REDES", Ongsys mostra "REDES MUNICIPAIS".
    # Se o nome esperado for um prefixo completo de palavra do nome
    # encontrado, aceitar como equivalente.
    if esperado_limpo and encontrado_limpo.startswith(esperado_limpo + " "):
        return None
    return Divergencia(campo, esperado, encontrado or "[vazio]", acao)


def comparar_documento(esperado: str, encontrado: str) -> Divergencia | None:
    if documento_equivalente(
        esperado, encontrado, aceitar_variacao_numerica=True
    ):
        return None
    return Divergencia(
        "Número do documento", esperado, encontrado or "[vazio]",
        "Conferir manualmente se houve variação de documento não registrada.",
    )


def comparar_valor(campo: str, esperado: Decimal, encontrado_texto: str,
                   tolerancia: Decimal) -> Divergencia | None:
    encontrado = para_decimal(encontrado_texto)
    if encontrado is not None and abs(encontrado - esperado) <= tolerancia:
        return None
    return Divergencia(
        campo, formatar_dinheiro_br(esperado), encontrado_texto or "[vazio]",
        "Corrigir o valor no Ongsys antes de considerar o título conferido.",
    )


def comparar_data(campo: str, esperado, encontrado_texto: str) -> Divergencia | None:
    encontrado = para_data(encontrado_texto)
    if encontrado == esperado:
        return None
    return Divergencia(
        campo, esperado.strftime("%d/%m/%Y"), encontrado_texto or "[vazio]",
        "Ajustar a data no Ongsys.",
    )


def comparar_codigo_barras(esperado: str, encontrado: str) -> Divergencia | None:
    """Compara o boleto somente quando a planilha informou um código."""
    if not (esperado or "").strip():
        return None

    # Só dígitos: a planilha da RECRUTA trazia "...10335-9" e o Ongsys grava
    # "...103359"; o hífen não é diferença real (18/09/2026).
    def normalizar(valor: str) -> str:
        return re.sub(r"\D", "", valor or "")

    if normalizar(esperado) == normalizar(encontrado):
        return None
    return Divergencia(
        "Código de barras",
        normalizar(esperado),
        normalizar(encontrado) or "[vazio]",
        "Conferir e corrigir o código de barras no Ongsys antes do pagamento.",
    )


def comparar_subprojeto(linha: int, esperado: str, encontrado: str) -> Divergencia | None:
    """Compara `código esperado` × `código salvo`, exigência central do §13."""
    if not esperado:
        if encontrado.strip():
            return Divergencia(
                f"Linha {linha}: Subprojeto", "[vazio]", encontrado,
                "A planilha não tinha código, mas o Ongsys gravou um. Conferir manualmente.",
            )
        return None
    if codigo_confere(esperado, encontrado):
        return None
    if not encontrado.strip():
        return Divergencia(
            f"Linha {linha}: Subprojeto", esperado, "[vazio]",
            "Código não foi preenchido. Informar manualmente no Ongsys.",
        )
    return Divergencia(
        f"Linha {linha}: Subprojeto", esperado, encontrado,
        "CODIGO DIFERENTE DO ESPERADO. Corrigir imediatamente no Ongsys.",
    )


def auditar_dados(
    titulo: Titulo,
    lido: dict,
    tolerancia: Decimal,
    apelidos_atividades: dict[str, str] | None = None,
    fornecedor_esperado: str | None = None,
    fornecedores_esperados: list[str] | None = None,
) -> list[Divergencia]:
    """Compara o esperado com o que foi lido da tela. Função pura e testável.

    `lido` usa as chaves: fornecedor, documento, emissao, vencimento, efetivacao,
    valor, conta_pagadora, tipo_despesa, data_pagamento, situacao e a lista
    `rateios` com valor, percentual, categoria, centro_custo, subprojeto e
    atividade.
    """
    divergencias: list[Divergencia] = []
    datas = titulo.datas_br()

    alternativas_fornecedor = fornecedores_esperados or [
        fornecedor_esperado or titulo.fornecedor
    ]
    divergencia_fornecedor = next(
        (
            None
            for esperado in alternativas_fornecedor
            if comparar_texto(
                "Fornecedor", esperado, lido.get("fornecedor", ""), ""
            ) is None
            or mesmo_nome_sem_forma_juridica(esperado, lido.get("fornecedor", ""))
        ),
        False,
    )
    if divergencia_fornecedor is False:
        divergencia_fornecedor = Divergencia(
            "Fornecedor",
            " ou ".join(alternativas_fornecedor),
            lido.get("fornecedor", "") or "[vazio]",
            "Conferir se o fornecedor selecionado é o correto.",
        )

    verificacoes = [
        divergencia_fornecedor,
        comparar_documento(titulo.documento, lido.get("documento", "")),
        comparar_data("Data de emissão", titulo.emissao, lido.get("emissao", "")),
        comparar_data("Data de vencimento", titulo.vencimento, lido.get("vencimento", "")),
        comparar_data("Data de efetivação", titulo.efetivacao, lido.get("efetivacao", "")),
        comparar_valor("Valor bruto", titulo.valor, lido.get("valor", ""), tolerancia),
        comparar_texto("Conta pagadora", titulo.conta_pagadora_nome,
                       lido.get("conta_pagadora", ""), "Conferir a conta pagadora."),
        comparar_texto("Tipo de despesa", titulo.tipo_despesa, lido.get("tipo_despesa", ""),
                       "Conferir a classificação da despesa."),
        comparar_codigo_barras(titulo.codigo_barras, lido.get("codigo_barras", "")),
    ]
    divergencias.extend(d for d in verificacoes if d)

    # §17/§19: o título tem de estar sem baixa.
    if lido.get("data_pagamento", "").strip():
        divergencias.append(Divergencia(
            "Situação de baixa", "sem baixa",
            f"data de pagamento preenchida: {lido['data_pagamento']}",
            "ATENCAO: o titulo aparece com baixa. Verificar quem baixou.",
        ))
    situacao = sem_acentos(lido.get("situacao", ""))
    if situacao and any(m in situacao for m in ("PAGO", "BAIXADO", "LIQUIDADO")):
        divergencias.append(Divergencia(
            "Situação de baixa", "em aberto", lido.get("situacao", ""),
            "ATENCAO: o titulo consta como pago. Verificar imediatamente.",
        ))

    rateios_lidos = lido.get("rateios", [])
    if len(rateios_lidos) != len(titulo.rateios):
        divergencias.append(Divergencia(
            "Quantidade de linhas de rateio", str(len(titulo.rateios)), str(len(rateios_lidos)),
            "Conferir as linhas de rateio no Ongsys.",
        ))

    for indice, esperado in enumerate(titulo.rateios, start=1):
        if indice > len(rateios_lidos):
            divergencias.append(Divergencia(
                f"Linha {indice}", f"R$ {esperado.valor_br}", "[linha ausente]",
                "A linha de rateio não foi gravada. Incluir manualmente.",
            ))
            continue
        atual = rateios_lidos[indice - 1]
        verificacoes_linha = [
            comparar_valor(f"Linha {indice}: valor", esperado.valor,
                           atual.get("valor", ""), tolerancia),
            comparar_texto(f"Linha {indice}: Categoria/Conta", esperado.categoria_detalhada,
                           atual.get("categoria", ""), "Corrigir a categoria."),
            comparar_texto(f"Linha {indice}: Centro de custo", esperado.centro_custo,
                           atual.get("centro_custo", ""), "Corrigir o centro de custo."),
            comparar_subprojeto(indice, esperado.subprojeto, atual.get("subprojeto", "")),
        ]
        divergencias.extend(d for d in verificacoes_linha if d)

        percentual_lido = para_decimal(atual.get("percentual", "").replace("%", ""))
        if percentual_lido is not None and abs(percentual_lido - esperado.percentual) > Decimal("0.01"):
            divergencias.append(Divergencia(
                f"Linha {indice}: percentual", f"{esperado.percentual_br}%",
                atual.get("percentual", ""), "Conferir o rateio percentual.",
            ))

        # §14: atividade vazia é aceitável; atividade trocada não é.
        if esperado.atividade:
            # O apelido converte o nome da planilha (ex.: "SEDE") para o
            # nome real no Ongsys (ex.: "Despesas ADM"). A auditoria deve
            # comparar usando o nome que foi de fato preenchido.
            atividade_esperada = esperado.atividade
            if apelidos_atividades:
                atividade_esperada = apelidos_atividades.get(
                    esperado.atividade, esperado.atividade
                )
            divergencia = comparar_texto(
                f"Linha {indice}: Atividade", atividade_esperada, atual.get("atividade", ""),
                "Conferir a atividade.",
            )
            if divergencia and atual.get("atividade", "").strip():
                divergencias.append(divergencia)

    return divergencias


def ler_titulo_da_tela(
    bot, id_ongsys: str, inicio=None, fim=None, fornecedor: str = ""
) -> dict | None:
    """Reabre o título pelo ID e devolve os campos lidos da tela.

    Devolve None quando o título não pôde ser aberto — o chamador registra
    SALVO_NAO_AUDITADO e nunca tenta recriar o lançamento.
    """
    page = bot.page
    bot._modal_inclusao = None
    bot.ultima_falha_auditoria = ""
    try:
        page.goto(bot.config["ongsys_url"], wait_until="domcontentloaded")
        if inicio is not None and not bot._aplicar_periodo_lista(inicio, fim or inicio):
            return None
        alvo = bot.localizar_linha_titulo(id_ongsys, fornecedor)
        if alvo is None:
            raise RuntimeError("a linha do ID nao apareceu depois dos filtros")

        if not bot.abrir_linha_para_auditoria(alvo):
            raise RuntimeError(
                "nenhum icone da linha (lapis, olho ou link) abriu o formulario do titulo"
            )
        page.locator(f"{S.CAMPO_FORNECEDOR}:visible").first.wait_for(
            state="visible", timeout=bot.timeout
        )
        bot._modal_inclusao = bot._detectar_modal_formulario()
        # O formulário fica "visível" um instante antes de os campos
        # terminarem de carregar. Ler cedo demais devolve tudo em branco e
        # gera divergências FALSAS ("encontrado: [vazio]") — foi o que
        # aconteceu com AGUIAR NF 02, MARTA NF 40 e SILVIA 09_26. Espera o
        # fornecedor aparecer preenchido (prova de que o formulário terminou
        # de popular) antes de ler os campos, com teto de tempo para nunca
        # travar. Só leitura: nada é alterado no Ongsys.
        for _ in range(20):
            if (bot._ler_valor(S.CAMPO_FORNECEDOR) or "").strip():
                break
            page.wait_for_timeout(300)
    except Exception as erro:
        evidencia = ""
        try:
            evidencia = bot.screenshot_falha(f"auditoria_abertura_{id_ongsys}")
        except Exception:
            pass
        bot.ultima_falha_auditoria = str(erro)
        if evidencia:
            bot.ultima_falha_auditoria += f" Evidencia: {evidencia}."
        return None

    def valor(seletor: str) -> str:
        return bot._ler_valor(seletor)

    def selecionado(seletor: str) -> str:
        try:
            return bot._campo(f"{seletor} option:checked").first.inner_text().strip()
        except Exception:
            return ""

    lido = {
        "fornecedor": valor(S.CAMPO_FORNECEDOR),
        "documento": valor(S.CAMPO_NUMERO_DOCUMENTO),
        "emissao": valor(S.CAMPO_DATA_EMISSAO),
        "vencimento": valor(S.CAMPO_DATA_VENCIMENTO),
        "efetivacao": valor(S.CAMPO_DATA_EFETIVACAO),
        "valor": valor(S.CAMPO_VALOR),
        "conta_pagadora": selecionado(S.CAMPO_CONTA_PAGADORA),
        "tipo_despesa": selecionado(S.CAMPO_TIPO_DESPESA),
        "codigo_barras": (
            valor(S.CAMPO_CODIGO_BARRAS)
            if bot._campo(S.CAMPO_CODIGO_BARRAS).count()
            else ""
        ),
        "data_pagamento": "",
        "situacao": "",
        "rateios": [],
    }

    # A data de pagamento nunca é preenchida por este programa; se existir na
    # tela, é sinal de que alguém baixou o título e a auditoria precisa avisar.
    for seletor in ("#field_data_pagamento", "#field_data_baixa"):
        if bot._campo(seletor).count():
            lido["data_pagamento"] = valor(seletor)
            break

    indice = 1
    while bot._campo(S.categoria_conta(indice)).count():
        lido["rateios"].append({
            "categoria": valor(S.categoria_conta(indice)),
            "centro_custo": valor(S.projeto_cc(indice)),
            "subprojeto": valor(S.subprojeto(indice)),
            "atividade": valor(S.atividade(indice)),
            "valor": valor(S.valor_rateio(indice)),
            "percentual": valor(S.percentual_rateio(indice)),
        })
        indice += 1
        if indice > 200:
            break

    return lido


def linhas_de_relatorio(titulo: Titulo, id_ongsys: str,
                        divergencias: list[Divergencia]) -> list[dict]:
    """Converte o resultado da auditoria em linhas do CSV."""
    base = {
        "id_ongsys": id_ongsys or "[nao localizado]",
        "fornecedor": titulo.fornecedor,
        "documento": titulo.documento,
    }
    if not divergencias:
        return [{
            **base,
            "situacao": "CONFERIDO",
            "campo": "todos",
            "esperado": "conforme planejamento",
            "encontrado": "igual",
            "confere": "SIM",
            "acao_recomendada": "Nenhuma.",
        }]
    return [{**base, "situacao": "DIVERGENTE", **d.linha_relatorio} for d in divergencias]
