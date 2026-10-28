"""Leitura e validação do config.json.

O arquivo é validado campo a campo com mensagens em português. Um erro de
configuração precisa parar o programa antes de abrir o navegador, e nunca
virar um valor padrão silencioso que lançaria dados errados.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .normalizacao import sem_acentos


class ErroConfiguracao(Exception):
    """Configuração inválida. A mensagem é mostrada direto ao usuário."""


#: Estrutura esperada: nome -> (tipo, obrigatório, padrão)
_ESQUEMA: dict[str, tuple[type | tuple[type, ...], bool, Any]] = {
    "ongsys_url": (str, True, None),
    "contas_pagadoras": (dict, True, None),
    "conta_pagadora_padrao": (str, False, ""),
    "contas_cartao": (list, False, []),
    "cores_pendentes": (list, True, None),
    "cores_ja_lancadas": (list, False, []),
    "ignorar_fornecedores": (list, False, []),
    "ignorar_retencoes": (list, False, []),
    "ignorar_categorias": (list, False, []),
    "fornecedores_varejo": (list, False, []),
    "apelidos_fornecedores": (dict, False, {}),
    "codigos_subprojeto_por_fornecedor": (dict, False, {}),
    "confirmar_duplicidade_por_titulo": (bool, False, True),
    "editar_projetados_automaticamente": (bool, False, True),
    "vencimento_usar_pagamento": (bool, False, True),
    "vencimento_igual_efetivacao": (list, False, []),
    "tipos_despesa": (dict, True, None),
    "tipos_documento": (dict, False, {}),
    "tipos_pagamento": (dict, False, {}),
    "apelidos_atividades": (dict, False, {}),
    "meio_pagamento_padrao": (str, False, "Internet Banking"),
    "limite_titulos_por_execucao": (int, False, 25),
    "limite_valor_total": ((str, int, float), False, "100000.00"),
    "alerta_valor_titulo": ((str, int, float), False, "50000.00"),
    "semelhanca_minima_fornecedor": ((str, int, float), False, "0.75"),
    "tolerancia_conciliacao": ((str, int, float), False, "0.01"),
    "timeout_ms": (int, False, 15000),
    "manter_navegador_visivel": (bool, False, True),
}

#: Sem esses tipos de despesa o programa não sabe classificar nada (§9).
_TIPOS_DESPESA_OBRIGATORIOS = ("servico", "imposto", "produto", "outros")


def _erro(mensagem: str) -> None:
    raise ErroConfiguracao(mensagem)


def _decimal(nome: str, valor: Any) -> Decimal:
    try:
        return Decimal(str(valor))
    except InvalidOperation:
        _erro(f'O campo "{nome}" precisa ser um número. Valor encontrado: {valor!r}.')
        raise  # inalcançável, mantém o tipo de retorno explícito


def carregar(caminho: Path) -> dict:
    """Lê e valida o config.json, devolvendo um dicionário já normalizado."""
    if not caminho.exists():
        _erro(
            f"O arquivo de configuração não existe: {caminho}\n"
            "Copie config.example.json para config.json e ajuste os dados."
        )
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8"))
    except json.JSONDecodeError as erro:
        _erro(
            f"O arquivo {caminho.name} não é um JSON válido.\n"
            f"Erro na linha {erro.lineno}, coluna {erro.colno}: {erro.msg}\n"
            "Verifique vírgulas sobrando e aspas não fechadas."
        )
        raise

    if not isinstance(bruto, dict):
        _erro(f"O arquivo {caminho.name} precisa conter um objeto JSON entre chaves.")

    config: dict[str, Any] = {}
    for nome, (tipo, obrigatorio, padrao) in _ESQUEMA.items():
        if nome not in bruto:
            if obrigatorio:
                _erro(f'Falta o campo obrigatório "{nome}" em {caminho.name}.')
            config[nome] = padrao
            continue
        valor = bruto[nome]
        if not isinstance(valor, tipo):
            esperado = tipo.__name__ if isinstance(tipo, type) else "número"
            _erro(f'O campo "{nome}" está com o tipo errado. Esperado: {esperado}.')
        config[nome] = valor

    desconhecidos = sorted(set(bruto) - set(_ESQUEMA))
    if desconhecidos:
        config["_campos_desconhecidos"] = desconhecidos

    _validar_contas(config)
    _validar_cores(config)
    _validar_tipos_despesa(config)

    for nome in ("limite_valor_total", "alerta_valor_titulo", "tolerancia_conciliacao"):
        config[nome] = _decimal(nome, config[nome])
    config["semelhanca_minima_fornecedor"] = _decimal(
        "semelhanca_minima_fornecedor", config["semelhanca_minima_fornecedor"]
    )
    if not Decimal("0") < config["semelhanca_minima_fornecedor"] <= Decimal("1"):
        _erro('O campo "semelhanca_minima_fornecedor" precisa ficar entre 0 e 1.')
    if config["limite_titulos_por_execucao"] < 1:
        _erro('O campo "limite_titulos_por_execucao" precisa ser pelo menos 1.')

    if not str(config["ongsys_url"]).startswith("https://"):
        _erro('O campo "ongsys_url" precisa começar com https://.')

    return config


def _validar_contas(config: dict) -> None:
    contas = config["contas_pagadoras"]
    if not contas:
        _erro('O campo "contas_pagadoras" está vazio. Cadastre pelo menos uma conta.')
    for identificador, nome in contas.items():
        if not isinstance(nome, str) or not nome.strip():
            _erro(f'A conta pagadora "{identificador}" precisa ter um nome em texto.')
    cartoes = [str(c).strip() for c in config.get("contas_cartao") or []]
    desconhecidas = [c for c in cartoes if c not in contas]
    if desconhecidas:
        _erro(
            f'O campo "contas_cartao" cita conta(s) que não estão em contas_pagadoras: '
            f"{', '.join(desconhecidas)}.\n"
            f"Contas disponíveis: {', '.join(sorted(contas))}."
        )
    config["contas_cartao"] = cartoes
    padrao = config.get("conta_pagadora_padrao") or ""
    if padrao and padrao not in contas:
        _erro(
            f'A "conta_pagadora_padrao" ({padrao}) não está na lista de contas_pagadoras.\n'
            f"Contas disponíveis: {', '.join(sorted(contas))}."
        )


def conta_e_cartao(config: dict, conta_id: object, nome: str = "") -> bool:
    """Diz se a conta pagadora é um cartão de crédito.

    Vale o nome com a palavra CARTAO (regra original: "PREPAGO CARTAO 0002")
    ou a presença do número da conta em ``contas_cartao``. A lista existe
    porque o nome precisa ser igual ao do Ongsys para a seleção e a auditoria,
    e nem todo cartão tem a palavra no nome ("65 - PREPAGO - 0003").
    """
    identificador = str(conta_id or "").strip()
    nome = nome or (config.get("contas_pagadoras") or {}).get(identificador, "")
    if "CARTAO" in sem_acentos(nome):
        return True
    return bool(identificador) and identificador in (config.get("contas_cartao") or [])


def _validar_cores(config: dict) -> None:
    pendentes = config["cores_pendentes"]
    if not pendentes:
        _erro(
            'O campo "cores_pendentes" está vazio.\n'
            "Sem a cor das linhas pendentes o programa não sabe o que já foi lançado."
        )
    for lista in ("cores_pendentes", "cores_ja_lancadas"):
        normalizadas = []
        for cor in config[lista]:
            if not isinstance(cor, str) or len(cor.strip()) not in (6, 8):
                _erro(
                    f'A cor "{cor}" em "{lista}" é inválida.\n'
                    "Use o formato ARGB de 8 dígitos, por exemplo FFFFFF99."
                )
            normalizadas.append(cor.strip().upper())
        config[lista] = normalizadas
    repetidas = set(config["cores_pendentes"]) & set(config["cores_ja_lancadas"])
    if repetidas:
        _erro(
            "As cores a seguir estão marcadas como pendentes e como já lançadas "
            f"ao mesmo tempo: {', '.join(sorted(repetidas))}."
        )


def _validar_tipos_despesa(config: dict) -> None:
    tipos = config["tipos_despesa"]
    faltando = [k for k in _TIPOS_DESPESA_OBRIGATORIOS if not tipos.get(k)]
    if faltando:
        _erro(
            'O campo "tipos_despesa" precisa conter as chaves: '
            f"{', '.join(_TIPOS_DESPESA_OBRIGATORIOS)}.\n"
            f"Faltando: {', '.join(faltando)}."
        )
