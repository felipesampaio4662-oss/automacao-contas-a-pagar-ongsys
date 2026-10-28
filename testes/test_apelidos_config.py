"""Apelidos de fornecedor cadastrados em config.json (2026-09-21).

Fornecedor normal com nome diferente no Ongsys ganha apelido (ajuda busca e
auditoria). Fornecedor de CARTÃO nunca ganha apelido: o Nome no Ongsys é o
genérico "LICENÇA DE USO" e o estabelecimento real fica no campo CPF/CNPJ, então
a busca precisa continuar usando o nome do estabelecimento.
"""

import tempfile
from pathlib import Path

import apoio  # noqa: F401
import pytest

from ongsys import configuracao
from ongsys.auditoria import comparar_texto
from ongsys.navegador import OngsysBot

from apoio import CONFIG_PROJETO as CONFIG  # noqa: E402
from apoio import config_de_teste  # noqa: E402

#: Apelidos fictícios no mesmo formato dos reais (os reais ficam só no
#: config.json local, que não vai para o GitHub).
APELIDOS_EXEMPLO = {
    "EE OSVALDO MACEDO": "APM EE OSVALDO MACEDO",
    "CINCO EDUCACAO E CULTURA LTDA": "CINCO - EDUCACAO E CULTURA LTDA",
    "AGUASUL": "CIA DE SANEAMENTO BASICO REGIONAL AGUASUL",
    "EE BENEDITO PACHECO DA ROCHA": "APM EE PROF BENEDITO PACHECO DA ROCHA",
}


@pytest.fixture(scope="module")
def bot():
    cfg = configuracao.carregar(CONFIG)
    return OngsysBot(cfg, Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()))


@pytest.fixture(scope="module")
def bot_exemplo():
    cfg = config_de_teste(apelidos_fornecedores=APELIDOS_EXEMPLO)
    return OngsysBot(cfg, Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()))


@pytest.mark.parametrize("planilha, ongsys_bruto", [
    ("EE OSVALDO MACEDO", "3458 - APM EE OSVALDO MACEDO"),
    ("CINCO EDUCACAO E CULTURA LTDA", "2495 - CINCO - EDUCACAO E CULTURA LTDA"),
    ("AGUASUL", "2089 - CIA DE SANEAMENTO BASICO REGIONAL AGUASUL"),
    ("EE BENEDITO PACHECO DA ROCHA", "3090 - APM EE PROF BENEDITO PACHECO DA ROCHA"),
])
def test_fornecedor_normal_com_apelido_passa_na_auditoria(bot_exemplo, planilha, ongsys_bruto):
    bot = bot_exemplo
    alvo = bot.resolver_apelido(planilha)
    assert alvo != planilha, "esperava um apelido cadastrado"
    assert comparar_texto("Fornecedor", alvo, ongsys_bruto, "") is None
    # A busca ainda gera uma consulta com o nome resolvido.
    assert bot.construir_consultas_fornecedor(alvo)[0] == alvo


# Estabelecimentos de cartão registrados como "LICENÇA DE USO": um apelido
# apontando para esse nome genérico tornaria a busca ambígua (dezenas de
# cadastros). Devem permanecer SEM apelido.
CARTAO_SEM_APELIDO = [
    "CODEAI", "CODEAI USAGE", "IAPRO", "SITEFACIL",
    "APITOOL TEAM", "HOSTIFY.COM", "NUVEMCO WEB SERVICES",
]


@pytest.mark.parametrize("estabelecimento", CARTAO_SEM_APELIDO)
def test_fornecedor_cartao_nunca_vira_licenca_de_uso(bot, estabelecimento):
    resolvido = bot.resolver_apelido(estabelecimento)
    assert "LICEN" not in resolvido.upper(), (
        f"{estabelecimento} nao pode resolver para 'LICENCA DE USO' — "
        "quebraria a busca pelo estabelecimento."
    )


def test_config_json_continua_valido():
    cfg = configuracao.carregar(CONFIG)
    assert cfg["apelidos_fornecedores"]  # carregou sem erro


def test_norte_vida_nao_tem_mais_apelido(bot):
    # O cadastro 3947 foi renomeado no Ongsys para o nome da planilha; o
    # apelido antigo ("...SEGURO SAUDE") impedia a seleção (2026-10-02).
    nome = "NORTE VIDA COMPANHIA DE SEGUROS"
    assert bot.resolver_apelido(nome) == nome
