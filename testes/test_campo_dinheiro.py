"""Regressão do bug mais grave encontrado no piloto real.

Ao preencher "300,00" no campo Valor bruto da despesa, a máscara de moeda do
Ongsys gravou "300.000,00" — mil vezes o valor certo. Um título assim seria
criado com valor errado.

A correção não tenta adivinhar a regra da máscara: preenche, lê de volta e só
aceita quando o valor conferir. Estes testes exercitam essa lógica com um
campo falso que imita máscaras de moeda diferentes.
"""

from __future__ import annotations

from decimal import Decimal

import apoio
import pytest
from apoio import config_de_teste

from ongsys.navegador import ErroNavegador, OngsysBot


class _CampoComMascara:
    """Campo de texto que aplica uma transformação ao valor digitado."""

    def __init__(self, mascara):
        self._mascara = mascara
        self.valor = ""
        self.digitados: list[str] = []

    def fill(self, texto):
        if texto == "":
            self.valor = ""
            return
        self.valor = self._mascara(texto)

    def type(self, texto, delay=0):
        self.digitados.append(texto)
        self.valor = self._mascara(texto)

    def blur(self):
        pass

    def press(self, _tecla):
        pass  # o Tab não muda o valor nesta simulação; a máscara já reagiu no type()

    def input_value(self):
        return self.valor


class _LocatorDeCampo:
    def __init__(self, campo):
        self.first = campo

    def count(self):
        return 1


class _PaginaComCampo:
    def __init__(self, campo):
        self._campo = campo

    def locator(self, _seletor):
        return _LocatorDeCampo(self._campo)

    def wait_for_timeout(self, _ms):
        pass


@pytest.fixture
def bot(tmp_path):
    return OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")


def _montar(bot, mascara):
    campo = _CampoComMascara(mascara)
    bot.page = _PaginaComCampo(campo)
    return campo


# -- máscaras que imitam comportamentos reais de campos de moeda -------------

def mascara_transparente(texto):
    """Campo sem máscara: guarda o que foi digitado."""
    return texto


def mascara_multiplica_por_mil(texto):
    """Reproduz o bug observado: '300,00' vira '300.000,00'.

    Aceita corretamente o formato sem separador decimal (só dígitos, em
    centavos), que é o caminho de saída que a correção precisa encontrar.
    """
    if "," in texto or "." in texto:
        digitos = texto.replace(".", "").replace(",", "")
        return f"{int(digitos):,}".replace(",", ".") + ",00"
    centavos = int(texto)
    return f"{centavos // 100:,}".replace(",", ".") + f",{centavos % 100:02d}"


def mascara_so_aceita_ponto(texto):
    """Campo que só entende ponto como separador decimal."""
    if "," in texto:
        return "0,00"
    return texto


class TestPreencherDinheiro:
    def test_campo_sem_mascara_aceita_o_primeiro_formato(self, bot):
        campo = _montar(bot, mascara_transparente)
        bot._preencher_dinheiro("#valor", Decimal("300.00"), "Valor")
        assert campo.input_value() == "300,00"

    def test_bug_real_do_piloto_e_contornado(self, bot):
        """300,00 não pode virar 300.000,00 sob nenhuma hipótese."""
        campo = _montar(bot, mascara_multiplica_por_mil)
        bot._preencher_dinheiro("#valor", Decimal("300.00"), "Valor")
        from ongsys.normalizacao import para_decimal
        assert para_decimal(campo.input_value()) == Decimal("300.00")

    def test_campo_que_so_aceita_ponto(self, bot):
        campo = _montar(bot, mascara_so_aceita_ponto)
        bot._preencher_dinheiro("#valor", Decimal("1234.50"), "Valor")
        from ongsys.normalizacao import para_decimal
        assert para_decimal(campo.input_value()) == Decimal("1234.50")

    def test_valor_com_milhar_e_gravado_certo(self, bot):
        campo = _montar(bot, mascara_multiplica_por_mil)
        bot._preencher_dinheiro("#valor", Decimal("139863.76"), "Valor")
        from ongsys.normalizacao import para_decimal
        assert para_decimal(campo.input_value()) == Decimal("139863.76")

    def test_campo_que_nunca_aceita_gera_erro_em_vez_de_salvar_errado(self, bot):
        """Se nenhum formato funcionar, o título não pode ser salvo."""
        campo = _montar(bot, lambda _texto: "999,99")
        with pytest.raises(ErroNavegador) as erro:
            bot._preencher_dinheiro("#valor", Decimal("300.00"), "Valor bruto da despesa")
        assert "NÃO foi salvo" in str(erro.value)
        assert "Valor bruto da despesa" in str(erro.value)

    def test_mensagem_de_erro_mostra_o_que_foi_tentado(self, bot):
        _montar(bot, lambda _texto: "0,00")
        with pytest.raises(ErroNavegador) as erro:
            bot._preencher_dinheiro("#valor", Decimal("300.00"), "Valor")
        assert "300,00" in str(erro.value)  # lista as tentativas feitas

    @pytest.mark.parametrize("valor", ["0.01", "1.00", "99.99", "300.00",
                                       "1234.56", "139863.76", "1000000.00"])
    def test_varios_valores_com_a_mascara_problematica(self, bot, valor):
        campo = _montar(bot, mascara_multiplica_por_mil)
        esperado = Decimal(valor)
        bot._preencher_dinheiro("#valor", esperado, "Valor")
        from ongsys.normalizacao import para_decimal
        assert para_decimal(campo.input_value()) == esperado


class TestPopupInterferindoNoCampo:
    """Bug real observado no piloto: o popup de "serviço não parametrizado"

    apareceu com atraso e ficou aberto durante o preenchimento do Valor —
    o campo voltava para 0,00 em toda tentativa enquanto o popup existia.
    """

    def test_popup_pendente_e_fechado_antes_do_campo_ser_lido_como_zero(self, bot, monkeypatch):
        from ongsys import seletores as S

        campo = _CampoComMascara(mascara_transparente)
        popup_aberto = {"estado": True}

        class ListaVazia:
            def count(self):
                return 0

        class BotaoFechar:
            def is_visible(self):
                return True

            def inner_text(self):
                return "Fechar"

            def click(self):
                popup_aberto["estado"] = False  # fechar o popup de verdade (dentro do escopo dele)

        class ListaBotao:
            def count(self):
                return 1

            def nth(self, _i):
                return BotaoFechar()

        class ElementoPopup:
            def is_visible(self_inner):
                return popup_aberto["estado"]

            def locator(self_inner, seletor_interno):
                # A busca pelo botão de fechar agora é SEMPRE escopada dentro
                # do próprio popup — nunca mais na página inteira.
                if seletor_interno == S.POPUP_FECHAR:
                    return ListaBotao()
                return ListaVazia()  # não contém o campo fornecedor: é um popup de verdade

        class ListaPopup:
            def count(self):
                return 1

            def nth(self, _i):
                return ElementoPopup()

        class PaginaComPopupNoValor:
            def locator(self, seletor):
                if seletor == S.CONTEINERES_POPUP:
                    return ListaPopup()
                return _LocatorDeCampo(campo)

            def wait_for_timeout(self, _ms):
                pass

        bot.page = PaginaComPopupNoValor()
        bot._preencher_dinheiro("#valor", Decimal("300.00"), "Valor bruto da despesa")
        assert campo.input_value() == "300,00"
        assert popup_aberto["estado"] is False


class TestDocumentoDuplicado:
    """A mensagem real do Ongsys não era reconhecida pelos gatilhos antigos."""

    def test_mensagem_real_observada_no_piloto(self):
        assert OngsysBot._documento_duplicado(
            "O número do documento fiscal já existe para esse fornecedor."
        )

    @pytest.mark.parametrize("mensagem", [
        "Documento já existe para este fornecedor",
        "DOCUMENTO JA CADASTRADO",
        "Já existe um título com este número",
        "O número do documento fiscal já existe para esse fornecedor.",
        "Este documento já existe no sistema",
    ])
    def test_variacoes_de_duplicidade(self, mensagem):
        assert OngsysBot._documento_duplicado(mensagem)

    @pytest.mark.parametrize("mensagem", [
        "",
        "Campo obrigatório não preenchido",
        "Fornecedor não parametrizado para pagamento",
        "Saldo existente na conta",
        "Erro ao salvar o registro",
        "Já existe saldo disponível",  # 'ja existe' sem falar de documento
    ])
    def test_nao_confunde_outras_mensagens(self, mensagem):
        assert not OngsysBot._documento_duplicado(mensagem)
