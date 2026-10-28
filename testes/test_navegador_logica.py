"""Testes das partes do navegador que não dependem de uma tela aberta.

A variação de documento e o reconhecimento da mensagem de duplicidade são
regras de negócio; testá-las sem browser evita depender do Ongsys para saber
se estão certas.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import apoio
import pytest
from apoio import config_de_teste, linha, montar

from ongsys.modelos import Rateio, Titulo
from ongsys.navegador import OngsysBot
from ongsys.normalizacao import sem_acentos


@pytest.fixture
def bot(tmp_path):
    """Bot sem navegador: só as regras puras são exercitadas."""
    return OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")


def _titulo(conta="6", documento="BOLETO", efetivacao=date(2026, 7, 1)) -> Titulo:
    return Titulo(
        chave="k", linhas_origem=[4], fornecedor="FORNECEDOR", documento=documento,
        documento_original=documento, emissao=date(2026, 6, 1),
        vencimento=date(2026, 6, 10), efetivacao=efetivacao, valor=Decimal("100.00"),
        conta_pagadora_id=conta, conta_pagadora_nome="X", tipo_despesa="Outros",
        tipo_documento="Outros", meio_pagamento="Internet Banking",
        tipo_pagamento="Transferência Eletrônica", historico="h",
        rateios=[Rateio(4, Decimal("100.00"), "CC", "T", "C", "", "", Decimal("100"))],
    )


class TestVariacaoDeDocumento:
    """§11: todas as contas usam ".1, .2, .3" — inclusive cartão de crédito."""

    def test_cartao_usa_sufixo_ponto(self, bot):
        # Documento "01.07_26" (data.mês_ano, cartão): repete como "01.07_26.1".
        titulo = _titulo(conta="63", documento="01.07_26")
        assert bot.proximo_documento(titulo, set()) == "01.07_26.1"

    def test_documento_com_data_nao_e_quebrado(self, bot):
        """Os pontos internos de "01.07_26" não viram sufixo (nada de '01.1')."""
        titulo = _titulo(conta="63", documento="01.07_26")
        assert bot.proximo_documento(titulo, {"01.07_26.1"}) == "01.07_26.2"

    def test_documento_original_terminado_em_ponto_numero_e_preservado(self, bot):
        """Um sufixo vindo da planilha faz parte do documento original."""
        titulo = _titulo(conta="63", documento="01.07_26.1")
        assert bot.proximo_documento(titulo, {"01.07_26.1"}) == "01.07_26.1.1"

    def test_conta_bancaria_usa_ponto(self, bot):
        titulo = _titulo(conta="6", documento="BOLETO")
        assert bot.proximo_documento(titulo, set()) == "BOLETO.1"

    def test_sequencia_incremental(self, bot):
        titulo = _titulo(documento="BOLETO")
        assert bot.proximo_documento(titulo, {"BOLETO.1"}) == "BOLETO.2"
        assert bot.proximo_documento(titulo, {"BOLETO.1", "BOLETO.2"}) == "BOLETO.3"

    def test_boleto_numerado_na_planilha_nao_perde_o_numero(self, bot):
        """'BOLETO.14' é um documento real, não a 14ª tentativa de BOLETO."""
        titulo = _titulo(documento="BOLETO.14")
        assert bot.proximo_documento(titulo, {"BOLETO.14"}) == "BOLETO.14.1"

    def test_incrementa_apenas_variacoes_criadas_nesta_execucao(self, bot):
        titulo = _titulo(documento="03.08")
        assert bot.proximo_documento(titulo, {"03.08.1"}) == "03.08.2"

    def test_desiste_depois_de_muitas_tentativas(self, bot):
        from ongsys.navegador import ErroNavegador

        titulo = _titulo(documento="BOLETO")
        usados = {f"BOLETO.{n}" for n in range(1, 60)}
        with pytest.raises(ErroNavegador):
            bot.proximo_documento(titulo, usados)


class TestConsultasDeFornecedor:
    """Ordem de tentativas de busca usada tanto na seleção real quanto no dry-run."""

    def test_nome_completo_e_sem_acentos_primeiro(self):
        consultas = OngsysBot.construir_consultas_fornecedor("Conceição Ferreira Lima")
        assert consultas[0] == "Conceição Ferreira Lima"
        assert consultas[1] == "CONCEICAO FERREIRA LIMA"

    def test_inclui_o_primeiro_nome(self):
        consultas = OngsysBot.construir_consultas_fornecedor("Papelmais Comercio Ltda")
        assert "Papelmais" in consultas

    def test_palavras_curtas_nao_viram_consulta_isolada(self):
        # "De" sozinho devolveria candidatos demais para servir de busca.
        consultas = OngsysBot.construir_consultas_fornecedor("De Paula Assessoria")
        assert "De" not in consultas

    def test_nome_de_uma_palavra_nao_duplica(self):
        consultas = OngsysBot.construir_consultas_fornecedor("PAPELMAIS")
        assert consultas.count("PAPELMAIS") == 1


class TestFornecedorEspecialDeCartao:
    def test_funciona_tambem_sem_conta_pagadora(self, bot, monkeypatch):
        titulo = _titulo(conta="")
        titulo.conta_pagadora_nome = ""
        titulo.fornecedor = "NUVEMCO WEB SERVICES"
        titulo.rateios[0].categoria_detalhada = "LICENCA DE USO"
        opcao = "3991 - LICENCA DE USO - NUVEMCO WEB SERVICES"
        monkeypatch.setattr(bot, "_candidatos_autocomplete", lambda *_: [opcao])
        monkeypatch.setattr(bot, "_clicar_opcao", lambda _texto: None)
        monkeypatch.setattr(bot, "_fechar_popups", lambda: True)

        assert bot.selecionar_fornecedor_titulo(titulo) == opcao
        assert "LICENCA DE USO" in bot.fornecedores_esperados_na_tela(titulo)

    def test_exige_estabelecimento_e_categoria_na_mesma_opcao(self, bot, monkeypatch):
        titulo = _titulo(conta="63")
        titulo.fornecedor = "SOFTCORP"
        titulo.rateios[0].categoria_detalhada = "LICENÇ(C)A DE USO"
        opcao = "9000 - LICENÇ(C)A DE USO - SOFTCORP"
        monkeypatch.setattr(
            bot, "_candidatos_autocomplete", lambda _campo, _consulta: [opcao]
        )
        clicados = []
        monkeypatch.setattr(bot, "_clicar_opcao", lambda texto: clicados.append(texto))
        monkeypatch.setattr(bot, "_fechar_popups", lambda: True)

        assert bot.selecionar_fornecedor_titulo(titulo) == opcao
        assert clicados == [opcao]

    def test_busca_tambem_pela_categoria_quando_cpf_cnpj_nao_e_indexado(self, bot, monkeypatch):
        titulo = _titulo(conta="63")
        titulo.fornecedor = "ESTABELECIMENTO XPTO"
        titulo.rateios[0].categoria_detalhada = "LICENÇ(C)A DE USO"
        opcao = "9000 - LICENÇ(C)A DE USO - ESTABELECIMENTO XPTO"

        def candidatos(_campo, consulta):
            return [opcao] if "LICEN" in sem_acentos(consulta) else []

        monkeypatch.setattr(bot, "_candidatos_autocomplete", candidatos)
        monkeypatch.setattr(bot, "_clicar_opcao", lambda _texto: None)
        monkeypatch.setattr(bot, "_fechar_popups", lambda: True)

        assert bot.selecionar_fornecedor_titulo(titulo) == opcao

    def test_sem_cadastro_especial_mantem_fluxo_normal(self, bot, monkeypatch):
        titulo = _titulo(conta="63")
        titulo.fornecedor = "FORNECEDOR NORMAL"
        titulo.rateios[0].categoria_detalhada = "INFORMATICA"
        monkeypatch.setattr(bot, "_candidatos_autocomplete", lambda *_: [])
        chamados = []
        monkeypatch.setattr(
            bot,
            "selecionar_fornecedor",
            lambda nome: chamados.append(nome) or "FORNECEDOR NORMAL",
        )

        assert bot.selecionar_fornecedor_titulo(titulo) == "FORNECEDOR NORMAL"
        assert chamados == ["FORNECEDOR NORMAL"]


class TestContaPagadoraVazia:
    def test_campo_ja_vazio_permanece_vazio(self, bot, monkeypatch):
        titulo = _titulo(conta="")
        titulo.conta_pagadora_nome = ""

        class Campo:
            first = None

            def __init__(self):
                self.first = self
                self.selecoes = []

            def input_value(self):
                return ""

            def select_option(self, **opcao):
                self.selecoes.append(opcao)

        campo = Campo()
        monkeypatch.setattr(bot, "_campo", lambda _seletor: campo)

        bot._selecionar_conta(titulo)
        assert campo.selecoes == []

    def test_remove_conta_preselecionada_usando_option_vazia(self, bot, monkeypatch):
        titulo = _titulo(conta="")
        titulo.conta_pagadora_nome = ""

        class Opcao:
            def __init__(self, valor):
                self.valor = valor

            def get_attribute(self, _nome):
                return self.valor

        class Opcoes:
            def __init__(self):
                self.itens = [Opcao(""), Opcao("6")]

            def count(self):
                return len(self.itens)

            def nth(self, indice):
                return self.itens[indice]

        class Campo:
            first = None

            def __init__(self):
                self.first = self
                self.valor = "6"

            def input_value(self):
                return self.valor

            def locator(self, _seletor):
                return Opcoes()

            def select_option(self, **opcao):
                self.valor = opcao["value"]

        campo = Campo()
        monkeypatch.setattr(bot, "_campo", lambda _seletor: campo)

        bot._selecionar_conta(titulo)
        assert campo.valor == ""

    def test_sem_repeticao_quando_ja_esta_sem_acento(self):
        consultas = OngsysBot.construir_consultas_fornecedor("TARIFA BANCARIA")
        assert len(consultas) == len(set(consultas))


class TestMensagemDeDuplicidade:
    """A troca de documento só pode ser disparada pela mensagem certa."""

    @pytest.mark.parametrize("mensagem", [
        "Documento já existe para este fornecedor",
        "DOCUMENTO JA CADASTRADO",
        "Já existe um título com este número",
        "Número de documento já utilizado",
        "Documento existente",
    ])
    def test_reconhece_avisos_de_duplicidade(self, mensagem):
        assert OngsysBot._documento_duplicado(mensagem)

    @pytest.mark.parametrize("mensagem", [
        "",
        "Campo obrigatório não preenchido",
        "Fornecedor não parametrizado para pagamento",
        "Saldo existente na conta",          # contém 'EXISTENTE', mas não é duplicidade
        "Verifique o cliente existente",
        "Erro ao salvar o registro",
    ])
    def test_ignora_outras_mensagens(self, mensagem):
        # A versão anterior procurava 'EXISTENTE' em toda a página e trocaria o
        # documento sem necessidade nestes casos.
        assert not OngsysBot._documento_duplicado(mensagem)


class TestSegurancaDeAbertura:
    def test_recusa_modo_invisivel(self, tmp_path):
        """§24: nada de headless enquanto a automação não estiver validada."""
        from ongsys.navegador import ErroFatal

        config = config_de_teste(manter_navegador_visivel=False)
        bot = OngsysBot(config, tmp_path / "p", tmp_path / "e")
        with pytest.raises(ErroFatal) as erro:
            bot.abrir()
        assert "visível" in str(erro.value)


class TestLinhasVisiveisDaLista:
    def test_ignora_linhas_ocultas_mantidas_pelo_datatables(self, bot):
        class Linha:
            def __init__(self, texto, visivel):
                self.texto = texto
                self.visivel = visivel

            def is_visible(self):
                return self.visivel

            def inner_text(self):
                return self.texto

        class Linhas:
            def __init__(self):
                self.itens = [
                    Linha("FORNECEDOR ANTIGO", False),
                    Linha("FORNECEDOR ATUAL", True),
                ]

            def count(self):
                return len(self.itens)

            def nth(self, indice):
                return self.itens[indice]

        bot.page = type("Pagina", (), {"locator": lambda self, _s: Linhas()})()

        assert bot._textos_linhas_visiveis_lista() == ["FORNECEDOR ATUAL"]


class TestCaptcha:
    @pytest.mark.parametrize("texto", ["CAPTCHA DETECTADO", "NAO SOU UM ROBO",
                                       "VERIFICACAO DE SEGURANCA"])
    def test_reconhece_bloqueio_de_seguranca(self, texto):
        assert OngsysBot._parece_captcha(texto)

    def test_texto_normal_nao_e_captcha(self):
        assert not OngsysBot._parece_captcha("CONTAS A PAGAR - LISTAGEM")


class TestTituloBloqueadoNaoEEnviado:
    def test_incluir_recusa_titulo_bloqueado(self, bot, tmp_path):
        """Defesa em profundidade: mesmo chamado por engano, não passa."""
        from ongsys.navegador import ErroNavegador

        titulo = montar(tmp_path / "b.xlsx", [
            linha("GFR- FGTS", 100.0, documento="06_26", cc=""),
        ]).titulos[0]
        assert titulo.bloqueado
        with pytest.raises(ErroNavegador) as erro:
            bot.incluir(titulo)
        assert "bloqueado" in str(erro.value).lower()
