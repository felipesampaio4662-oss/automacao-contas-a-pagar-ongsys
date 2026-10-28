"""Regressão: o piloto real travou porque o popup de fornecedor não

parametrizado (§10) ficou aberto e escondeu o campo seguinte do formulário.
A causa era checar a presença do popup só uma vez, cedo demais, e nunca
confirmar se ele realmente fechou antes de seguir preenchendo.

Estes testes usam uma página falsa (sem Playwright de verdade) para exercitar
`_fechar_popups` e `_algum_popup_visivel` isoladamente.
"""

from __future__ import annotations

import apoio
import pytest
from apoio import config_de_teste

from ongsys.navegador import OngsysBot


class _ElementoFalso:
    def __init__(self, visivel: bool, texto: str = "OK", contem_fornecedor: bool = False):
        self._visivel = visivel
        self._texto = texto
        self._contem_fornecedor = contem_fornecedor
        self.cliques = 0

    def is_visible(self):
        return self._visivel

    def inner_text(self):
        return self._texto

    def click(self):
        self.cliques += 1

    def locator(self, seletor):
        from ongsys import seletores as S

        if seletor == S.CAMPO_FORNECEDOR and self._contem_fornecedor:
            return _LocatorFalso([_ElementoFalso(True)])
        if seletor == S.POPUP_FECHAR:
            # A busca do botão de fechar agora é escopada dentro do próprio
            # popup — devolve o botão deste elemento, com o texto configurado.
            return _LocatorFalso([_ElementoFalso(True, self._texto)])
        return _LocatorFalso([])


class _LocatorFalso:
    """Simula `page.locator(seletor)`: uma lista fixa de elementos falsos."""

    def __init__(self, elementos: list[_ElementoFalso]):
        self._elementos = elementos

    def count(self):
        return len(self._elementos)

    def nth(self, indice):
        return self._elementos[indice]

    @property
    def first(self):
        return self._elementos[0]


class _TecladoFalso:
    def __init__(self):
        self.teclas_pressionadas: list[str] = []

    def press(self, tecla):
        self.teclas_pressionadas.append(tecla)


class _PaginaFalsa:
    """Controla, chamada a chamada, se o popup ainda está visível.

    `estados_popup` é consumida em ordem a cada verificação de
    `_algum_popup_visivel`; o último valor repete quando a lista acaba.
    """

    def __init__(self, estados_popup: list[bool], fecha_ao_clicar: bool = True,
                fecha_no_escape: bool = False, texto_botao: str = "OK"):
        self._estados = list(estados_popup)
        self._fecha_ao_clicar = fecha_ao_clicar
        self._fecha_no_escape = fecha_no_escape
        self._texto_botao = texto_botao
        self._fechado = False
        self.teclado = _TecladoFalso()

    def _popup_visivel_agora(self) -> bool:
        if self._fechado:
            return False
        if self._estados:
            return self._estados.pop(0)
        return True  # sem mais estados programados: continua visível

    def locator(self, seletor):
        from ongsys import seletores as S

        if seletor == S.CONTEINERES_POPUP:
            # O texto do botão de fechar precisa viajar com o próprio popup:
            # a busca por ele agora é feita dentro do elemento do popup, não
            # mais na página inteira.
            return _LocatorFalso([_ElementoFalso(self._popup_visivel_agora(), self._texto_botao)])
        return _LocatorFalso([])

    def wait_for_timeout(self, _ms):
        pass


@pytest.fixture
def bot(tmp_path):
    return OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")


class TestSeletorDeBotaoFechar:
    """Bug real do piloto: o botão "Fechar" do #modalErro nunca foi clicado,

    mesmo depois de mais de dez tentativas com correções diferentes. Causa
    real: `POPUP_FECHAR` misturava `text='Fechar'` com CSS via vírgula —
    sintaxe inválida que o Playwright rejeitava com uma exceção,
    silenciosamente engolida por um `try/except`. A verificação de sintaxe
    de verdade está em `test_seletores_playwright.py` (usa Playwright real);
    aqui só confere que a versão quebrada não volta a aparecer.
    """

    def test_nao_mistura_text_igual_com_css_por_virgula(self):
        """`text='X'` ou `text=X` misturado via vírgula com CSS é sintaxe

        inválida no Playwright — nunca deve voltar a aparecer aqui."""
        from ongsys import seletores as S

        for parte in S.POPUP_FECHAR.split(","):
            parte = parte.strip()
            assert not parte.startswith("text="), (
                f"'{parte}' usa o motor de texto misturado com CSS via vírgula — "
                "isso quebra a sintaxe do seletor. Use :text('...') no lugar."
            )

    def test_usa_o_pseudo_seletor_text_para_nao_depender_da_tag(self):
        from ongsys import seletores as S

        assert ":text('Fechar')" in S.POPUP_FECHAR
        assert ":text('OK')" in S.POPUP_FECHAR

    def test_modal_erro_esta_nos_conteineres_de_popup(self):
        from ongsys import seletores as S

        assert "#modalErro" in S.CONTEINERES_POPUP

    def test_modal_erro_esta_nas_mensagens(self):
        from ongsys import seletores as S

        assert "#modalErro" in S.MENSAGENS


class TestFecharPopups:
    def test_sem_popup_nao_faz_nada_e_confirma_fechado(self, bot):
        bot.page = _PaginaFalsa(estados_popup=[False])
        assert bot._fechar_popups() is True

    def test_primeira_checagem_e_imediata_sem_espera(self, bot):
        """Bug real do piloto: uma pausa de 300ms ANTES até da primeira

        checagem atrasava o clique numa opção de autocomplete o suficiente
        para a lista de sugestões do Ongsys fechar sozinha — mesmo quando
        não havia popup nenhum, o caso mais comum. A primeira tentativa não
        pode esperar; só as seguintes, quando já se sabe que há um popup.
        """
        chamadas_de_espera = []

        class PaginaComContador(_PaginaFalsa):
            def wait_for_timeout(self, ms):
                chamadas_de_espera.append(ms)

        bot.page = PaginaComContador(estados_popup=[False])
        assert bot._fechar_popups(tentativas=1) is True
        assert chamadas_de_espera == []  # nenhuma espera, popup nunca existiu

    def test_popup_aparece_um_pouco_depois_e_e_fechado(self, bot):
        """A causa raiz do bug: checar só uma vez, cedo demais."""
        bot.page = _PaginaFalsa(estados_popup=[True, True, False])
        assert bot._fechar_popups() is True

    def test_popup_que_nunca_fecha_devolve_false_em_vez_de_travar(self, bot):
        pagina = _PaginaFalsa(estados_popup=[True] * 10)
        bot.page = pagina
        assert bot._fechar_popups() is False

    def test_botao_proibido_nao_e_clicado(self, bot):
        """Um botão de fechar cujo texto bate com uma ação proibida (§17)
        nunca deve ser clicado, mesmo que isso deixe o popup aberto."""
        pagina = _PaginaFalsa(estados_popup=[True] * 5, texto_botao="Dar baixa")
        bot.page = pagina
        resultado = bot._fechar_popups()
        # Como o único botão encontrado é proibido, o popup nunca fecha.
        assert resultado is False

    def test_sem_botao_reconhecido_nunca_usa_escape(self, bot):
        """Guia de operação real (§16): Esc é um evento de página, não

        escopado ao popup, e pode fechar o formulário principal inteiro em
        vez do aviso — nunca deve ser usado como recurso, mesmo sem achar
        nenhum botão de fechar dentro do popup.
        """
        from ongsys import seletores as S

        class _PopupSemBotao:
            """Popup que nunca tem um botão de fechar dentro dele."""

            def is_visible(self):
                return True  # nunca fecha sozinho

            def locator(self, _seletor):
                return _LocatorFalso([])  # nenhum botão de fechar dentro deste popup

        class PaginaSemBotao:
            def __init__(self):
                self.teclado = _TecladoFalso()
                self.keyboard = self.teclado

            def locator(self, seletor):
                if seletor == S.CONTEINERES_POPUP:
                    return _LocatorFalso([_PopupSemBotao()])
                return _LocatorFalso([])

            def wait_for_timeout(self, _ms):
                pass

        pagina = PaginaSemBotao()
        bot.page = pagina
        resultado = bot._fechar_popups()
        assert resultado is False  # sem botão, o popup genuinamente não fecha
        assert pagina.teclado.teclas_pressionadas == []  # e Escape nunca foi tentado


class TestFormularioPrincipalNuncaVirapopup:
    """Bug real do piloto: o próprio formulário 'Inclusão de Contas a Pagar'

    também é um modal. O detector de popup capturava o formulário inteiro e
    tentava 'fechá-lo', arriscando clicar no botão Fechar real do formulário
    em vez de um aviso — o que fecharia o cadastro que estava sendo
    preenchido."""

    def test_modal_com_o_campo_fornecedor_dentro_nao_e_tratado_como_popup(self, bot):
        class PaginaComFormularioAberto:
            def locator(self, seletor):
                from ongsys import seletores as S

                if seletor == S.CONTEINERES_POPUP:
                    # O "modal" encontrado é o próprio formulário principal:
                    # contém o campo de fornecedor dentro dele.
                    return _LocatorFalso([_ElementoFalso(True, contem_fornecedor=True)])
                return _LocatorFalso([])

            def wait_for_timeout(self, _ms):
                pass

        bot.page = PaginaComFormularioAberto()
        assert bot._algum_popup_visivel() is False
        # Sem popup de verdade, fecha_popups nem tenta clicar em nada.
        assert bot._fechar_popups() is True

    def test_popup_de_verdade_ao_lado_do_formulario_ainda_e_detectado(self, bot):
        class PaginaComFormularioEPopup:
            def locator(self, seletor):
                from ongsys import seletores as S

                if seletor == S.CONTEINERES_POPUP:
                    return _LocatorFalso([
                        _ElementoFalso(True, contem_fornecedor=True),   # o formulário
                        _ElementoFalso(True, contem_fornecedor=False),  # o aviso de verdade
                    ])
                if seletor == S.POPUP_FECHAR:
                    return _LocatorFalso([_ElementoFalso(True, "OK")])
                return _LocatorFalso([])

            def wait_for_timeout(self, _ms):
                pass

        bot.page = PaginaComFormularioEPopup()
        assert bot._algum_popup_visivel() is True


class TestSelecionarFornecedorComPopup:
    def test_popup_que_nao_fecha_gera_erro_claro_em_vez_de_travar_depois(self, bot, monkeypatch):
        """Antes: o formulário travava num campo sem relação nenhuma com o
        fornecedor, minutos depois, com um erro genérico do Playwright."""
        from ongsys.navegador import ErroNavegador

        monkeypatch.setattr(bot, "_candidatos_autocomplete", lambda *_: ["FORNECEDOR X"])
        monkeypatch.setattr(bot, "_clicar_opcao", lambda *_: None)
        monkeypatch.setattr(bot, "_fechar_popups", lambda **_: False)

        with pytest.raises(ErroNavegador) as erro:
            bot.selecionar_fornecedor("FORNECEDOR X")
        assert "aviso" in str(erro.value).lower()
        assert "não fechou" in str(erro.value)

    def test_popup_que_fecha_devolve_o_fornecedor_normalmente(self, bot, monkeypatch):
        monkeypatch.setattr(bot, "_candidatos_autocomplete", lambda *_: ["FORNECEDOR X"])
        monkeypatch.setattr(bot, "_clicar_opcao", lambda *_: None)
        monkeypatch.setattr(bot, "_fechar_popups", lambda **_: True)
        assert bot.selecionar_fornecedor("FORNECEDOR X") == "FORNECEDOR X"


class _ElementoDeTexto:
    def __init__(self, botao_fechar_disponivel: bool):
        self._botao_fechar_disponivel = botao_fechar_disponivel
        self.cliques = 0

    def is_visible(self):
        return True

    def locator(self, seletor):
        if "Fechar" in seletor and self._botao_fechar_disponivel:
            return _LocatorFalso([self])
        return _LocatorFalso([])

    def click(self):
        self.cliques += 1


class TestFecharPorProximidadeDeTexto:
    """Segunda linha de defesa: quando o detector genérico de popup nunca

    reconhece o container do aviso (caso real observado no piloto — três
    tentativas estruturais diferentes falharam), localizar o botão pela
    posição no documento em vez de por classe CSS."""

    def test_frase_ausente_conta_como_fechado(self, bot):
        class PaginaSemAFrase:
            def get_by_text(self, _frase, exact=False):
                return _LocatorFalso([])

        bot.page = PaginaSemAFrase()
        assert bot._fechar_por_proximidade_do_texto("não tem parametrizado") is True

    def test_encontra_e_clica_o_botao_seguinte(self, bot):
        marcador = _ElementoDeTexto(botao_fechar_disponivel=True)

        class PaginaComAFrase:
            def __init__(self):
                self._sumiu = False

            def get_by_text(self_inner, frase, exact=False):
                if self_inner._sumiu:
                    return _LocatorFalso([])
                return _LocatorFalso([marcador])

            def wait_for_timeout(self_inner, _ms):
                self_inner._sumiu = True  # simula o aviso sumindo após o clique

        bot.page = PaginaComAFrase()
        assert bot._fechar_por_proximidade_do_texto("não tem parametrizado") is True
        assert marcador.cliques == 1

    def test_sem_botao_seguinte_permanece_aberto(self, bot):
        marcador = _ElementoDeTexto(botao_fechar_disponivel=False)

        class PaginaSemBotaoSeguinte:
            def get_by_text(self, _frase, exact=False):
                return _LocatorFalso([marcador])

            def wait_for_timeout(self, _ms):
                pass

        bot.page = PaginaSemBotaoSeguinte()
        assert bot._fechar_por_proximidade_do_texto("não tem parametrizado") is False
        assert marcador.cliques == 0


class TestPopupDeServicoNaoParametrizado:
    """Confirmado pelo usuário em uso real: o aviso aparece ao selecionar

    Tipo de despesa = Contratação de Serviço(s), não ao selecionar o
    fornecedor. A checagem só deve rodar para esse tipo específico."""

    def test_tipo_servico_com_popup_que_fecha_nao_gera_erro(self, bot, monkeypatch):
        bot.page = type("PaginaMinima", (), {"wait_for_timeout": lambda *_a: None})()
        monkeypatch.setattr(bot, "_fechar_popups", lambda **_: True)
        bot._fechar_popup_de_servico_nao_parametrizado("Contratação de Serviço(s)")

    def test_tipo_servico_com_popup_que_nao_fecha_gera_erro_claro(self, bot, monkeypatch):
        from ongsys.navegador import ErroNavegador

        bot.page = type("PaginaMinima", (), {"wait_for_timeout": lambda *_a: None})()
        monkeypatch.setattr(bot, "_fechar_popups", lambda **_: False)
        monkeypatch.setattr(bot, "_fechar_por_proximidade_do_texto", lambda *_a: False)
        with pytest.raises(ErroNavegador) as erro:
            bot._fechar_popup_de_servico_nao_parametrizado("Contratação de Serviço(s)")
        assert "Tipo de despesa" in str(erro.value)
        assert "não fechou" in str(erro.value)

    def test_rota_por_proximidade_de_texto_evita_o_erro(self, bot, monkeypatch):
        """Quando o detector genérico falha, mas a rota por texto consegue
        fechar o aviso, a execução não deve parar."""
        bot.page = type("PaginaMinima", (), {"wait_for_timeout": lambda *_a: None})()
        monkeypatch.setattr(bot, "_fechar_popups", lambda **_: False)
        monkeypatch.setattr(bot, "_fechar_por_proximidade_do_texto", lambda *_a: True)
        bot._fechar_popup_de_servico_nao_parametrizado("Contratação de Serviço(s)")  # não levanta

    def test_ignora_acentuacao_ao_comparar_o_tipo(self, bot, monkeypatch):
        from ongsys.navegador import ErroNavegador

        bot.page = type("PaginaMinima", (), {"wait_for_timeout": lambda *_a: None})()
        monkeypatch.setattr(bot, "_fechar_popups", lambda **_: False)
        monkeypatch.setattr(bot, "_fechar_por_proximidade_do_texto", lambda *_a: False)
        with pytest.raises(ErroNavegador):
            bot._fechar_popup_de_servico_nao_parametrizado("CONTRATACAO DE SERVICO(S)")

    @pytest.mark.parametrize("tipo", ["Outros", "Pagamento de Impostos", "Compra de Produto(s)"])
    def test_outros_tipos_nunca_checam_popup(self, bot, monkeypatch, tipo):
        """A maioria dos títulos não é serviço: não pode ter espera extra."""
        chamado = []
        monkeypatch.setattr(bot, "_fechar_popups", lambda: chamado.append(1) or True)
        bot._fechar_popup_de_servico_nao_parametrizado(tipo)
        assert chamado == []
