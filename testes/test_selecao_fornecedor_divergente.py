"""Seleção de fornecedor não pode escolher outra pessoa/empresa (2026-09-22).

"ANA CLARA MOREIRA ROCHA" foi criada no cadastro de "ANA DO CARMO ROCHA
MOREIRA" porque compartilham 3 de 4 tokens (semelhança 0,75 = mínimo). A trava:
quando cada nome tem um token distintivo que o outro não tem, são entidades
diferentes e o título deve FALHAR (seguro), não ser criado no errado.
"""

import tempfile
from decimal import Decimal
from pathlib import Path

import apoio  # noqa: F401
import pytest

from ongsys import configuracao
from ongsys.navegador import ErroNavegador, OngsysBot

from apoio import CONFIG_PROJETO as CONFIG  # noqa: E402


@pytest.mark.parametrize("esperado, candidato, divergem", [
    ("ANA CLARA MOREIRA ROCHA", "ANA DO CARMO ROCHA MOREIRA", True),
    ("ANA GERALDA PEIXOTO DE BRANDAO", "ANA GERALDA PEIXOTO", False),
    ("CINCO EDUCACAO E CULTURA LTDA", "CINCO - EDUCACAO E CULTURA LTDA", False),
    ("RENATA FONSECA PRADO", "RENATA FONSECA PRADO", False),
    ("X CONSULTORIA", "X CONSULTORIA LTDA", False),
    ("JOAO ROCHA", "JOAO ROCHA MOREIRA", False),
    ("ANA ROCHA ME", "ANA ROCHA LTDA", False),
    # Sigla de iniciais (2026-10-02): "P. R. P." identifica a empresa.
    ("P. R. P. APRENDIZAGENS LTDA", "2695 - COLABORATIVA APRENDIZAGENS LTDA - 44.444.444/0001-44", True),
    ("P. R. P. APRENDIZAGENS LTDA", "4100 - P. R. P. APRENDIZAGENS LTDA - 11.111.111/0001-11", False),
    ("P. R. P. APRENDIZAGENS LTDA", "PRP APRENDIZAGENS LTDA", False),
    ("P. R. P. APRENDIZAGENS LTDA", "P R P APRENDIZAGENS", False),
    ("M.C. ZANETTI BARRETO", "3900 - M C ZANETTI BARRETO", False),
    ("M.C. ZANETTI BARRETO", "ZANETTI BARRETO CONSULTORIA", True),
    # Forma jurídica com barra e letra isolada não são sigla.
    ("EX-AEQUO S/S", "1234 - EX-AEQUO SS", False),
    ("BOA FONTE SERVIÇOS S.A", "BOA FONTE SERVICOS SA", False),
    ("JOSE A ROCHA", "JOSE ROCHA", False),
])
def test_nomes_divergem(esperado, candidato, divergem):
    assert OngsysBot._nomes_divergem(esperado, candidato) is divergem


def test_selecao_recusa_empresa_que_so_compartilha_a_palavra_generica(tmp_path):
    # Título 00034227: sem o cadastro certo no Ongsys, a única sugestão era
    # outra empresa com "APRENDIZAGENS" no nome. Tem de falhar, não lançar.
    bot = _bot(tmp_path, "2695 - COLABORATIVA APRENDIZAGENS LTDA - 44.444.444/0001-44")
    with pytest.raises(ErroNavegador, match="não foi localizado com segurança"):
        bot.selecionar_fornecedor("P. R. P. APRENDIZAGENS LTDA")


def _bot(tmp_path, candidato):  # noqa: E302
    bot = OngsysBot(configuracao.carregar(CONFIG), tmp_path / "p", tmp_path / "e")
    bot._candidatos_autocomplete = lambda _sel, _consulta: [candidato]
    bot._clicar_opcao = lambda _t: (_ for _ in ()).throw(
        AssertionError("não deveria clicar num fornecedor divergente")
    )
    return bot


def test_selecao_recusa_pessoa_diferente(tmp_path):
    bot = _bot(tmp_path, "3156 - ANA DO CARMO ROCHA MOREIRA - 111.111.111-11")
    with pytest.raises(ErroNavegador, match="não foi localizado com segurança"):
        bot.selecionar_fornecedor("ANA CLARA MOREIRA ROCHA")


def test_selecao_aceita_fornecedor_com_apelido_legitimo(tmp_path):
    # MACRO LOJABILL -> MACRO COMPUTADORES LTDA (apelido). Não pode ser
    # barrado pela trava de divergência.
    config = configuracao.carregar(CONFIG)
    config["apelidos_fornecedores"] = {"MACRO LOJABILL": "MACRO COMPUTADORES LTDA"}
    bot = OngsysBot(config, tmp_path / "p", tmp_path / "e")
    bot._candidatos_autocomplete = lambda _s, _c: ["3844 - MACRO COMPUTADORES LTDA - 11.111.111/0001-11"]
    cliques = []
    bot._clicar_opcao = lambda t: cliques.append(t)
    bot._fechar_popups = lambda *a, **k: True

    escolhido = bot.selecionar_fornecedor("MACRO LOJABILL")

    assert "MACRO COMPUTADORES LTDA" in escolhido
    assert cliques


class TestApelidoDesatualizado:
    """O cadastro foi renomeado no Ongsys e o apelido antigo ficou para trás."""

    NOME = "NORTE VIDA COMPANHIA DE SEGUROS"
    ANTIGO = "3947 - NORTE VIDA COMPANHIA DE SEGURO SAUDE - 22.222.222/0001-22"
    ATUAL = "3947 - NORTE VIDA COMPANHIA DE SEGUROS - 22.222.222/0001-22"

    def _bot(self, tmp_path, opcoes):
        config = configuracao.carregar(CONFIG)
        config["apelidos_fornecedores"] = {self.NOME: "NORTE VIDA COMPANHIA DE SEGURO SAUDE"}
        bot = OngsysBot(config, tmp_path / "p", tmp_path / "e")
        bot._candidatos_autocomplete = lambda _s, _c: list(opcoes)
        self.cliques = []
        bot._clicar_opcao = lambda texto: self.cliques.append(texto)
        bot._fechar_popups = lambda *a, **k: True
        return bot

    def test_sem_o_nome_do_apelido_usa_o_nome_da_planilha(self, tmp_path):
        bot = self._bot(tmp_path, [self.ATUAL])

        assert bot.selecionar_fornecedor(self.NOME) == self.ATUAL
        assert self.cliques == [self.ATUAL]

    def test_apelido_continua_tendo_preferencia_quando_existe(self, tmp_path):
        bot = self._bot(tmp_path, [self.ATUAL, self.ANTIGO])

        assert bot.selecionar_fornecedor(self.NOME) == self.ANTIGO

    def test_nome_da_planilha_nao_afrouxa_a_trava_de_divergencia(self, tmp_path):
        bot = self._bot(tmp_path, ["3999 - NORTE VIDA CAPITALIZACAO SA - 33.333.333/0001-33"])

        with pytest.raises(ErroNavegador, match="não foi localizado com segurança"):
            bot.selecionar_fornecedor(self.NOME)
        assert self.cliques == []

    def test_auditoria_aceita_o_nome_da_planilha_e_o_do_apelido(self, tmp_path):
        from types import SimpleNamespace

        bot = self._bot(tmp_path, [])
        titulo = SimpleNamespace(fornecedor=self.NOME, conta_pagadora_id="6",
                                 conta_pagadora_nome="BANCO ITAU", rateios=[])

        assert bot.fornecedores_esperados_na_tela(titulo) == [
            "NORTE VIDA COMPANHIA DE SEGURO SAUDE", self.NOME,
        ]


class TestEmpateComNomeIdentico:
    CAIXA = "3476 - CAIXA CUST ESC EEPSG DONA HERMINIA CASTANHEIRA FONTES - 77.777.777/0001-77"
    ESCOLA = "3919 - EE HERMINIA CASTANHEIRA FONTES - 55.555.555/0001-55"

    def _bot(self, tmp_path, opcoes):
        bot = OngsysBot(configuracao.carregar(CONFIG), tmp_path / "p", tmp_path / "e")
        bot._candidatos_autocomplete = lambda _s, _c: list(opcoes)
        self.cliques = []
        bot._clicar_opcao = lambda texto: self.cliques.append(texto)
        bot._fechar_popups = lambda *a, **k: True
        return bot

    def test_nome_identico_vence_o_que_apenas_contem_o_nome(self, tmp_path):
        bot = self._bot(tmp_path, [self.CAIXA, self.ESCOLA])

        assert bot.selecionar_fornecedor("EE HERMINIA CASTANHEIRA FONTES") == self.ESCOLA
        assert self.cliques == [self.ESCOLA]

    def test_dois_cadastros_de_nome_identico_continuam_ambiguos(self, tmp_path):
        outra = "4001 - EE HERMINIA CASTANHEIRA FONTES - 11.111.111/0001-11"
        bot = self._bot(tmp_path, [self.ESCOLA, outra])

        with pytest.raises(ErroNavegador, match="ambíguo"):
            bot.selecionar_fornecedor("EE HERMINIA CASTANHEIRA FONTES")
        assert self.cliques == []

    def test_sem_nome_identico_o_empate_continua_ambiguo(self, tmp_path):
        bot = self._bot(tmp_path, [self.CAIXA, "3920 - APM EE HERMINIA CASTANHEIRA FONTES - 22.222.222/0001-22"])

        with pytest.raises(ErroNavegador, match="ambíguo"):
            bot.selecionar_fornecedor("EE HERMINIA CASTANHEIRA FONTES")



@pytest.mark.parametrize("planilha, tela, mesmo", [
    ("INOVARE CONSULTORIA EDUCACIONAL LTDA ME", "4056 - INOVARE CONSULTORIA EDUCACIONAL LTDA", True),
    ("X CONSULTORIA LTDA", "1 - X CONSULTORIA SA - 11.111.111/0001-11", True),
    ("TELNET TELECOM S/A", "3529 - TELNET TELECOM SA", False),  # "S/A" não é removido: segue a regra antiga
    ("P. R. P. APRENDIZAGENS LTDA", "2695 - COLABORATIVA APRENDIZAGENS LTDA", False),
    ("ANA CLARA MOREIRA ROCHA", "3156 - ANA DO CARMO ROCHA MOREIRA", False),
    ("INOVARE CONSULTORIA EDUCACIONAL LTDA ME", "4057 - INOVARE EDUCACIONAL LTDA", False),
    ("LTDA", "ME", False),
])
def test_auditoria_ignora_so_a_forma_juridica(planilha, tela, mesmo):
    from ongsys.auditoria import mesmo_nome_sem_forma_juridica

    assert mesmo_nome_sem_forma_juridica(planilha, tela) is mesmo


def test_auditoria_do_titulo_innovate_nao_acusa_fornecedor(tmp_path):
    from ongsys.auditoria import auditar_dados
    from apoio import linha, montar

    titulo = montar(tmp_path / "p.xlsx", [linha("INOVARE CONSULTORIA EDUCACIONAL LTDA ME", 1000.0)]).titulos[0]
    lido = {"fornecedor": "4056 - INOVARE CONSULTORIA EDUCACIONAL LTDA"}

    divergencias = auditar_dados(titulo, lido, Decimal("0.01"))

    assert not any(d.campo == "Fornecedor" for d in divergencias)
