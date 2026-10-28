"""Testes que rodam os seletores contra um Playwright de verdade.

Bug real que motivou este arquivo: `POPUP_FECHAR` misturava `text='Fechar'`
(motor de texto do Playwright) com seletores CSS separados por vírgula —
sintaxe inválida. O Playwright lançava exceção ao avaliar o seletor, mas como
todo uso estava protegido por `try/except`, a falha nunca apareceu: o popup
de documento duplicado simplesmente nunca fechava, silenciosamente, ao longo
de muitas tentativas de correção diferentes.

Os outros testes do projeto usam objetos falsos (sem Playwright) de
propósito, para rodar rápido e sem precisar de navegador. Isso tem um limite:
uma string de seletor sintaticamente inválida passa por qualquer objeto falso
sem erro, porque o objeto falso não interpreta a string — só um motor de
seletor de verdade pega esse tipo de bug. Por isso estes testes usam o
Playwright real (Chromium headless, sem rede, sem Ongsys).
"""

from __future__ import annotations

import apoio
import pytest

playwright_sync_api = pytest.importorskip("playwright.sync_api")

from ongsys import seletores as S


@pytest.fixture(scope="module")
def pagina():
    with playwright_sync_api.sync_playwright() as p:
        navegador = p.chromium.launch()
        pg = navegador.new_page()
        yield pg
        navegador.close()


TODOS_OS_SELETORES = [
    S.MENSAGENS,
    S.CONTEINERES_POPUP,
    S.POPUP_FECHAR,
    S.OPCOES_AUTOCOMPLETE,
    S.ABA_DADOS_PAGAMENTO,
    S.ABA_RATEIO_PARCELAMENTO,
    S.CAMPO_BUSCA_LISTA,
    S.CAMPO_BUSCA_FORNECEDOR_FALLBACK,
    S.CAMPO_BUSCA_TABELA,
    S.ICONE_VER_RELATORIO,
    S.ACAO_EDITAR_TITULO,
    S.BOTAO_INCLUIR_LINHA_RATEIO,
    S.FILTROS_DATA_LISTA,
    S.FILTRO_TIPO_DATA_LISTA,
    S.MENU_INCLUIR_CONTAS_PAGAR,
]


class TestTodosOsSeletoresSaoValidos:
    """Se algum destes lançar uma exceção de sintaxe, o bug volta a existir

    — mesmo que fique escondido atrás de um `try/except` em produção."""

    @pytest.mark.parametrize("seletor", TODOS_OS_SELETORES)
    def test_seletor_nao_lanca_erro_de_sintaxe(self, pagina, seletor):
        pagina.set_content("<div></div>")
        pagina.locator(seletor).count()  # não deve levantar


class TestPopupFecharEncontraBotaoDeVerdade:
    """O caso real: o botão "Fechar" do #modalErro não é <button>, <a> nem

    tem role="button" — é um elemento genérico com o texto certo."""

    def test_encontra_span_com_o_texto_fechar(self, pagina):
        pagina.set_content(
            "<div id='modalErro'>"
            "<p class='erro-title'>O número do documento fiscal já existe.</p>"
            "<span class='btn-custom'>Fechar</span>"
            "</div>"
        )
        botao = pagina.locator("#modalErro").locator(S.POPUP_FECHAR)
        assert botao.count() == 1
        assert botao.first.inner_text() == "Fechar"

    def test_nao_alcanca_o_fechar_do_formulario_principal(self, pagina):
        """A busca escopada ao popup não pode enxergar o Fechar de fora dele."""
        pagina.set_content(
            "<div id='modalErro'><span>Fechar</span></div>"
            "<div id='outroLugar'><button>Fechar</button></div>"
        )
        dentro_do_popup = pagina.locator("#modalErro").locator(S.POPUP_FECHAR)
        assert dentro_do_popup.count() == 1

        fora_do_popup = pagina.locator("#outroLugar").locator(S.POPUP_FECHAR)
        assert fora_do_popup.count() == 1  # existe, mas só é alcançado se alguém buscar lá

    def test_botao_com_tag_button_continua_funcionando(self, pagina):
        pagina.set_content("<div id='modalErro'><button>OK</button></div>")
        assert pagina.locator("#modalErro").locator(S.POPUP_FECHAR).count() == 1


class TestMensagensLeModalErro:
    def test_le_o_texto_de_dentro_do_modal_erro(self, pagina):
        pagina.set_content(
            "<div id='modalErro'>"
            "<p class='erro-title'>O número do documento fiscal já existe para esse fornecedor.</p>"
            "</div>"
        )
        textos = pagina.locator(S.MENSAGENS).all_inner_texts()
        assert any("existe" in t for t in textos)
