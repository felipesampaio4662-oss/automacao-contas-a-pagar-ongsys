"""Regras de negócio: o que entra, como agrupa e o que bloqueia.

Este módulo é puro: recebe linhas já lidas e devolve títulos. Não conhece
navegador nem arquivos, o que permite testá-lo inteiro com planilhas
sintéticas.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from . import configuracao
from .modelos import (
    Estado,
    Exclusao,
    LinhaPlanilha,
    Rateio,
    ResultadoLeitura,
    Titulo,
)
from .normalizacao import (
    QUATRO_CASAS,
    contem_palavra,
    dinheiro,
    normalizar_documento,
    sem_acentos,
)


class ErroRegras(Exception):
    """Condição que impede qualquer planejamento seguro."""


# --------------------------------------------------------------------------
# Classificação
# --------------------------------------------------------------------------

def tipo_despesa(linha: LinhaPlanilha, config: dict) -> str:
    """Prioridade do §9. O critério principal é o Título categoria.

    Regras especiais têm prioridade sobre a classificação pela
    categoria-título:
      - Categoria detalhada "Salarios a pagar" → Pagamento de folha
      - Coluna rateio/atividade "Reembolsos"   → Reembolso de despesas
    """
    tipos = config["tipos_despesa"]
    detalhada = sem_acentos(linha.categoria_detalhada)
    atividade = sem_acentos(linha.atividade)

    if "SALARIOS A PAGAR" in detalhada and "folha" in tipos:
        return tipos["folha"]
    if "REEMBOLSOS" in atividade and "reembolso" in tipos:
        return tipos["reembolso"]

    titulo = sem_acentos(linha.categoria_titulo)

    if "SERVICOS TOMADOS DE TERCEIROS" in titulo.replace("_", " "):
        return tipos["servico"]
    if any(
        marca in titulo.replace("_", " ")
        for marca in ("DESPESAS TRIBUTARIAS", "ENCARGOS SOCIAIS", "IMPOSTOS", "TRIBUTOS")
    ):
        return tipos["imposto"]
    if any(contem_palavra(nome, linha.fornecedor) or sem_acentos(nome) in sem_acentos(linha.fornecedor)
           for nome in config["fornecedores_varejo"]):
        return tipos["produto"]
    return tipos["outros"]


def tipo_documento(documento: str, config: dict) -> str:
    """Mapeamento do §16, com sobreposição pela configuração."""
    doc = sem_acentos(documento)
    for prefixo, rotulo in config.get("tipos_documento", {}).items():
        if doc.startswith(sem_acentos(prefixo)):
            return rotulo
    if doc.startswith("NF"):
        return "Nota Fiscal"
    if "BOLETO" in doc:
        return "Boleto"
    if "RECIBO" in doc:
        return "Recibo"
    if "FATURA" in doc:
        return "Fatura"
    return "Outros"


def tipo_pagamento(linha: LinhaPlanilha, documento: str, config: dict) -> str:
    """Mapeamento do §16 para o tipo de pagamento eletrônico."""
    texto = " ".join(
        sem_acentos(x) for x in (documento, linha.categoria_titulo,
                                 linha.categoria_detalhada, linha.fornecedor)
    )
    for chave, rotulo in config.get("tipos_pagamento", {}).items():
        if contem_palavra(chave, texto):
            return rotulo
    if contem_palavra("FGTS", texto):
        return "FGTS"
    if contem_palavra("IPTU", texto):
        return "Tributos Municipais"
    if contem_palavra("PIX", texto):
        return "PIX"
    if contem_palavra("BOLETO", texto):
        return "Boleto Simples de Outros Bancos"
    return "Transferência Eletrônica"


# --------------------------------------------------------------------------
# Exclusões
# --------------------------------------------------------------------------

def motivo_de_exclusao(linha: LinhaPlanilha, config: dict) -> str | None:
    """Devolve o motivo do descarte, ou None se a linha deve ser considerada.

    A ordem importa: motivos estruturais vêm antes dos motivos de negócio, para
    que o relatório mostre a causa mais concreta.
    """
    if not linha.fornecedor and linha.saida is None and linha.entrada is None:
        return "Linha vazia"

    alvo = f"{linha.fornecedor} {linha.categoria_titulo} {linha.categoria_detalhada}"

    for nome in config.get("ignorar_fornecedores", []):
        if sem_acentos(nome) in sem_acentos(linha.fornecedor):
            return f"Movimentação que não é conta a pagar: {nome}"
    for nome in config.get("ignorar_categorias", []):
        if sem_acentos(nome) in sem_acentos(linha.categoria_titulo):
            return f"Categoria excluída pela configuração: {nome}"
    for nome in config.get("ignorar_retencoes", []):
        if contem_palavra(nome, alvo):
            return f"Retenção lançada separadamente: {nome}"

    if not linha.fornecedor:
        return "Sem fornecedor"
    if linha.saida is None:
        if linha.entrada is not None:
            return "Entrada financeira, não é conta a pagar"
        return "Sem valor de saída"
    if linha.saida <= 0:
        return f"Valor de saída menor ou igual a zero ({linha.saida})"
    if linha.efetivacao is None:
        return "Sem data de pagamento/efetivação válida"
    if linha.vencimento is None:
        return "Sem data de vencimento válida"
    if linha.emissao is None:
        return "Sem data de emissão válida"
    return None


# --------------------------------------------------------------------------
# Rateio
# --------------------------------------------------------------------------

def distribuir_percentuais(rateios: list[Rateio], total: Decimal) -> None:
    """Percentual de cada linha, fechando exatamente 100% (§12).

    Cada linha recebe o percentual arredondado a 4 casas e a última recebe o
    resto. Com `Decimal` a soma fecha exata; com `float` sobraria diferença de
    arredondamento em títulos com muitas linhas, como o da Benefix.
    """
    if total <= 0:
        raise ErroRegras(f"Total do título precisa ser maior que zero. Recebido: {total}.")

    acumulado = Decimal("0")
    for indice, rateio in enumerate(rateios):
        if indice == len(rateios) - 1:
            rateio.percentual = Decimal("100") - acumulado
        else:
            rateio.percentual = (rateio.valor / total * 100).quantize(
                QUATRO_CASAS, rounding=ROUND_HALF_UP
            )
            acumulado += rateio.percentual


def conferir_rateio(titulo: Titulo, tolerancia: Decimal) -> list[str]:
    """Confere soma de valores e de percentuais. Devolve os problemas (§12)."""
    problemas: list[str] = []

    soma_valores = sum((r.valor for r in titulo.rateios), Decimal("0"))
    if abs(soma_valores - titulo.valor) > tolerancia:
        problemas.append(
            f"A soma dos rateios ({soma_valores}) não bate com o valor do título "
            f"({titulo.valor}). Diferença de {soma_valores - titulo.valor}."
        )

    soma_percentuais = sum((r.percentual for r in titulo.rateios), Decimal("0"))
    if soma_percentuais != Decimal("100"):
        problemas.append(f"A soma dos percentuais é {soma_percentuais}%, e precisa ser 100%.")

    for rateio in titulo.rateios:
        if rateio.percentual <= 0:
            problemas.append(
                f"Linha {rateio.linha} ficou com percentual {rateio.percentual}%, "
                "o que indica valor zerado ou negativo."
            )
    return problemas


# --------------------------------------------------------------------------
# Montagem dos títulos
# --------------------------------------------------------------------------

def _lista(numeros: list[int], maximo: int = 12) -> str:
    """Enumera linhas, resumindo quando forem muitas."""
    if len(numeros) <= maximo:
        return ", ".join(str(n) for n in numeros)
    mostrados = ", ".join(str(n) for n in numeros[:maximo])
    return f"{mostrados} e mais {len(numeros) - maximo}"


def _chave_agrupamento(linha: LinhaPlanilha, conta_id: str) -> tuple:
    """Assinatura do título dentro de um bloco contínuo da planilha."""
    return (
        sem_acentos(linha.fornecedor),
        normalizar_documento(linha.documento),
        linha.vencimento.isoformat(),
        linha.efetivacao.isoformat(),
        conta_id,
    )


def _e_linha_iof(linha: LinhaPlanilha) -> bool:
    """IOF de cartão vem em uma linha própria logo abaixo da compra."""
    return "(IOF)" in sem_acentos(linha.fornecedor)


def _documento_final(linhas: list[LinhaPlanilha]) -> str:
    """Documento do título; quando ausente, usa a data no formato DD.MM_AA (§11)."""
    documento = linhas[0].documento.strip()
    if documento:
        return documento
    return linhas[0].efetivacao.strftime("%d.%m_%y")


def montar_titulos(
    linhas: list[LinhaPlanilha],
    inicio: date,
    fim: date,
    config: dict,
    conta_id: str,
) -> ResultadoLeitura:
    """Aplica período, cor, exclusões e agrupamento, registrando cada descarte."""
    if inicio > fim:
        raise ErroRegras(
            f"O período está invertido: início {inicio.strftime('%d/%m/%Y')} é depois "
            f"do fim {fim.strftime('%d/%m/%Y')}."
        )

    contas = config["contas_pagadoras"]
    if conta_id and conta_id not in contas:
        raise ErroRegras(
            f"A conta pagadora {conta_id} não está cadastrada.\n"
            f"Contas disponíveis: {', '.join(f'{k} = {v}' for k, v in sorted(contas.items()))}."
        )

    resultado = ResultadoLeitura()
    pendentes = set(config["cores_pendentes"])
    ja_lancadas = set(config.get("cores_ja_lancadas", []))
    tolerancia = config["tolerancia_conciliacao"]

    for linha in linhas:
        resultado.cores_encontradas[linha.cor] = resultado.cores_encontradas.get(linha.cor, 0) + 1

    _validar_cores_do_arquivo(resultado.cores_encontradas, pendentes, ja_lancadas)

    grupos: dict[tuple, list[LinhaPlanilha]] = {}
    base_por_grupo: dict[tuple, tuple] = {}
    ocorrencias_por_base: dict[tuple, int] = {}
    # Liga cada número de linha já aceito à chave do seu título. Assim uma
    # linha "FORNECEDOR (IOF)" usa exatamente o grupo da linha física anterior,
    # mesmo que o nome do fornecedor seja diferente.
    chave_por_linha: dict[int, tuple] = {}
    for linha in linhas:
        if linha.efetivacao is None or not (inicio <= linha.efetivacao <= fim):
            # Fora do período: não é descarte, é outro recorte. Não polui o relatório.
            continue

        if linha.cor not in pendentes:
            resultado.linhas_por_cor_excluidas += 1
            if linha.fornecedor or linha.saida is not None:
                rotulo = "já lançada" if linha.cor in ja_lancadas else "não é a cor de pendente"
                resultado.exclusoes.append(
                    Exclusao(
                        linha=linha.linha,
                        fornecedor=linha.fornecedor,
                        documento=linha.documento,
                        valor=str(linha.saida or ""),
                        motivo=f"Cor {linha.cor}: {rotulo}",
                    )
                )
            continue

        resultado.linhas_por_cor_incluidas += 1
        motivo = motivo_de_exclusao(linha, config)
        if motivo:
            if motivo != "Linha vazia":
                resultado.exclusoes.append(
                    Exclusao(
                        linha=linha.linha,
                        fornecedor=linha.fornecedor,
                        documento=linha.documento,
                        valor=str(linha.saida or linha.entrada or ""),
                        motivo=motivo,
                    )
                )
            continue

        if _e_linha_iof(linha):
            chave_principal = chave_por_linha.get(linha.linha - 1)
            if chave_principal is None:
                resultado.exclusoes.append(
                    Exclusao(
                        linha=linha.linha,
                        fornecedor=linha.fornecedor,
                        documento=linha.documento,
                        valor=str(linha.saida or ""),
                        motivo=(
                            "Linha de IOF sem uma linha principal válida "
                            "imediatamente acima. Não foi lançada sozinha."
                        ),
                    )
                )
                continue
            grupos[chave_principal].append(linha)
            chave_por_linha[linha.linha] = chave_principal
            continue

        base = _chave_agrupamento(linha, conta_id)
        grupo_anterior = chave_por_linha.get(linha.linha - 1)

        # Rateios só existem dentro de um bloco contínuo. Uma ocorrência igual
        # mais abaixo na ordem de pagamentos é outro título, mesmo que
        # fornecedor, documento, vencimento e efetivação sejam idênticos.
        if grupo_anterior is not None and base_por_grupo.get(grupo_anterior) == base:
            chave_grupo = grupo_anterior
        else:
            ocorrencia = ocorrencias_por_base.get(base, 0) + 1
            ocorrencias_por_base[base] = ocorrencia
            # Preserva a chave histórica da primeira ocorrência. Somente os
            # blocos repetidos recebem um identificador pela linha inicial,
            # evitando colisão no checkpoint sem invalidar títulos antigos.
            chave_grupo = base if ocorrencia == 1 else (*base, f"BLOCO_LINHA_{linha.linha}")
            grupos[chave_grupo] = []
            base_por_grupo[chave_grupo] = base

        grupos[chave_grupo].append(linha)
        chave_por_linha[linha.linha] = chave_grupo

    for chave_tupla, linhas_grupo in grupos.items():
        resultado.titulos.append(
            _montar_titulo(
                chave_tupla,
                linhas_grupo,
                config,
                conta_id,
                contas.get(conta_id, ""),
                tolerancia,
            )
        )

    resultado.titulos.sort(key=lambda t: (t.efetivacao, t.fornecedor, t.documento))
    return resultado


def _validar_cores_do_arquivo(
    encontradas: dict[str, int], pendentes: set[str], ja_lancadas: set[str]
) -> None:
    """Para o programa se o arquivo não tiver nenhuma das cores esperadas (§4)."""
    if any(cor in encontradas for cor in pendentes):
        return

    resumo = ", ".join(
        f"{cor} ({quantidade} linhas)"
        for cor, quantidade in sorted(encontradas.items(), key=lambda x: -x[1])
        if cor != "SEM_COR"
    ) or "nenhuma cor de preenchimento"

    raise ErroRegras(
        "Nenhuma linha da planilha está com a cor de pendente configurada "
        f"({', '.join(sorted(pendentes))}).\n\n"
        f"Cores realmente encontradas na coluna Fornecedor: {resumo}.\n\n"
        "O programa parou de propósito. Se ele continuasse, trataria todas as "
        "linhas como pendentes e poderia relançar a planilha inteira.\n"
        "Ajuste cores_pendentes no config.json com uma das cores acima, ou "
        "confirme se a planilha correta foi informada."
    )


def _montar_titulo(
    chave_tupla: tuple,
    linhas: list[LinhaPlanilha],
    config: dict,
    conta_id: str,
    conta_nome: str,
    tolerancia: Decimal,
) -> Titulo:
    primeira = linhas[0]
    total = dinheiro(sum((x.saida for x in linhas), Decimal("0")))
    documento = _documento_final(linhas)

    alertas: list[str] = []
    bloqueios: list[str] = []

    linhas_iof = [x.linha for x in linhas if _e_linha_iof(x)]
    if linhas_iof:
        alertas.append(
            f"IOF da(s) linha(s) {_lista(linhas_iof)} incorporado ao título principal "
            "em rateio(s) separado(s)."
        )

    # Emissões divergentes entre as linhas de um título rateado NÃO bloqueiam
    # mais. Por orientação do usuário (títulos rateados, como reembolsos),
    # usa-se a data de emissão da PRIMEIRA linha e registra-se um alerta para
    # conferência — em vez de exigir correção manual da planilha.
    emissoes_distintas = sorted({x.emissao for x in linhas})
    if len(emissoes_distintas) > 1:
        formatadas = ", ".join(d.strftime("%d/%m/%Y") for d in emissoes_distintas)
        alertas.append(
            f"Datas de emissão diferentes entre as linhas ({formatadas}). "
            f"Usada a data da primeira linha: {primeira.emissao.strftime('%d/%m/%Y')}."
        )

    rateios = [
        Rateio(
            linha=x.linha,
            valor=x.saida,
            centro_custo=x.centro_custo,
            categoria_titulo=x.categoria_titulo,
            categoria_detalhada=x.categoria_detalhada,
            subprojeto=x.codigo_despesa,
            atividade=x.atividade,
        )
        for x in linhas
    ]
    distribuir_percentuais(rateios, total)

    sem_cc = [r.linha for r in rateios if not r.centro_custo]
    sem_categoria = [r.linha for r in rateios if not r.categoria_detalhada]
    sem_codigo = [r.linha for r in rateios if not r.subprojeto]

    # Um título como o da Benefix tem dezenas de linhas. Agrupar mantém o resumo
    # legível sem esconder nenhuma linha: todas aparecem enumeradas.
    if sem_cc:
        bloqueios.append(f"Sem centro de custo nas linhas: {_lista(sem_cc)}.")
    if sem_categoria:
        bloqueios.append(f"Sem categoria detalhada nas linhas: {_lista(sem_categoria)}.")
    if sem_codigo:
        alertas.append(
            f"Sem código de despesa em {len(sem_codigo)} linha(s): {_lista(sem_codigo)}. "
            "O Subprojeto ficará vazio e será relatado na auditoria."
        )

    # O campo Vencimento no Ongsys é o que aparece na visualização prévia e na
    # hora da baixa. A planilha de pagamentos já realizados informa a data do
    # pagamento real (efetivação), que é mais útil para conferência do que a
    # data de vencimento original. Por padrão, usa-se a efetivação como
    # vencimento; o comportamento antigo fica disponível via config
    # ``vencimento_usar_pagamento: false``.
    usar_pagamento = config.get("vencimento_usar_pagamento", True)
    if usar_pagamento:
        vencimento = primeira.efetivacao
        if primeira.vencimento != primeira.efetivacao:
            alertas.append(
                f"Vencimento ajustado para a data de pagamento: "
                f"{primeira.vencimento.strftime('%d/%m/%Y')} passou a "
                f"{primeira.efetivacao.strftime('%d/%m/%Y')}."
            )
    else:
        vencimento = primeira.vencimento
        if any(sem_acentos(nome) in sem_acentos(primeira.fornecedor)
               for nome in config.get("vencimento_igual_efetivacao", [])):
            if vencimento != primeira.efetivacao:
                alertas.append(
                    f"Fornecedor configurado para vencimento igual à efetivação: "
                    f"{vencimento.strftime('%d/%m/%Y')} passou a "
                    f"{primeira.efetivacao.strftime('%d/%m/%Y')}."
                )
                vencimento = primeira.efetivacao

    codigos_barras = {
        re.sub(r"[.\s]", "", getattr(x, "codigo_barras", "") or "")
        for x in linhas
        if (getattr(x, "codigo_barras", "") or "").strip()
    }
    if len(codigos_barras) > 1:
        bloqueios.append(
            "Codigos de barras diferentes nas linhas do mesmo titulo: "
            + ", ".join(sorted(codigos_barras))
            + "."
        )
    codigo_barras = next(iter(codigos_barras), "")

    titulo = Titulo(
        chave="|".join(chave_tupla),
        linhas_origem=[x.linha for x in linhas],
        fornecedor=primeira.fornecedor,
        documento=documento,
        documento_original=documento,
        emissao=primeira.emissao,
        vencimento=vencimento,
        efetivacao=primeira.efetivacao,
        valor=total,
        conta_pagadora_id=conta_id,
        conta_pagadora_nome=conta_nome,
        conta_cartao=configuracao.conta_e_cartao(config, conta_id, conta_nome),
        tipo_despesa=tipo_despesa(primeira, config),
        tipo_documento=tipo_documento(documento, config),
        meio_pagamento=config.get("meio_pagamento_padrao", "Internet Banking"),
        tipo_pagamento=tipo_pagamento(primeira, documento, config),
        historico=primeira.categoria_detalhada,
        codigo_barras=codigo_barras,
        rateios=rateios,
        alertas=alertas,
        bloqueios=bloqueios,
    )

    problemas = conferir_rateio(titulo, tolerancia)
    titulo.bloqueios.extend(problemas)

    if titulo.valor >= config["alerta_valor_titulo"]:
        titulo.alertas.append(
            f"Título de valor alto: R$ {titulo.valor_br}. Confira antes de autorizar."
        )

    titulo.estado = Estado.BLOQUEADO if titulo.bloqueios else Estado.PLANEJADO
    return titulo
