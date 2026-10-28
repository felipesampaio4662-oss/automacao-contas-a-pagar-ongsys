"""Testes da aba de rateio e do resolvedor de documento duplicado.

Ambos vieram de bugs reais observados no piloto:

* os campos de rateio existem no DOM mas ficam escondidos até a aba
  "Rateio e Parcelamento" ser aberta — preencher sem abrir a aba falha
  silenciosamente com "elemento não visível";
* o aviso de documento duplicado pode aparecer sozinho, de forma
  assíncrona, ao sair do campo Número do Documento — não só depois de
  clicar em Salvar.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import apoio
import pytest
from apoio import config_de_teste

from ongsys import seletores as S
from ongsys.modelos import Rateio, Titulo
from ongsys.navegador import ErroNavegador, OngsysBot, ResultadoInclusao


@pytest.fixture
def bot(tmp_path):
    return OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")


class _CampoFalsoParaTab:
    """Substitui `_campo(...)` nos testes que só precisam do `.press("Tab")`

    depois de renomear o documento — não interessa o que ele faz, só que
    não quebre por falta de `bot.page`."""

    first = type("Elemento", (), {"press": lambda self, _tecla: None})()


class TestConfirmacaoPosSalvamento:
    """Regressão do primeiro título realmente criado no piloto.

    O Ongsys mostrou a confirmação verde, mas um #modalErro antigo e invisível
    fez o fluxo tentar voltar à aba Dados do pagamento do formulário fechado.
    """

    def test_mensagem_verde_visivel_confirma_a_inclusao(self, bot):
        class Mensagem:
            def is_visible(self):
                return True

        class Lista:
            def count(self):
                return 1

            def nth(self, _indice):
                return Mensagem()

        bot.page = type("Pagina", (), {"get_by_text": lambda self, *_a, **_k: Lista()})()
        assert bot._inclusao_confirmada() is True

    def test_modal_erro_antigo_e_invisivel_e_ignorado(self, bot):
        class ModalOculto:
            def is_visible(self):
                return False

            def inner_text(self):
                return "O número do documento fiscal já existe para esse fornecedor."

        class ListaModal:
            first = ModalOculto()

            def count(self):
                return 1

        bot.page = type("Pagina", (), {"locator": lambda self, _s: ListaModal()})()
        assert bot._texto_modal_erro() == ""

    def test_sucesso_confirmado_retorna_salvo_mesmo_sem_nova_interacao(self, bot, monkeypatch):
        resultado = ResultadoInclusao(documento_final="RPA")
        titulo = _titulo_simples()
        bot._modal_inclusao = object()
        bot._duplicado_titulo = titulo
        bot._duplicado_resultado = resultado

        monkeypatch.setattr(bot, "_inclusao_confirmada", lambda: True)
        monkeypatch.setattr(bot, "_fechar_confirmacao_sucesso", lambda: True)
        monkeypatch.setattr(bot, "_descobrir_id", lambda *_a: "00045678")

        assert bot._finalizar_sucesso_confirmado(titulo, resultado) is True
        assert resultado.salvo is True
        assert resultado.id_ongsys == "00045678"
        assert bot._modal_inclusao is None
        assert bot._duplicado_titulo is None


class TestConfirmacaoDaConversaoSemToast:
    """A edição da 99 salvou, mas o aviso verde sumiu antes da leitura."""

    class Pagina:
        def goto(self, *_a, **_k):
            pass

    class Linha:
        def __init__(self, texto):
            self.texto = texto

        def inner_text(self):
            return self.texto

    def _preparar(self, bot, monkeypatch, linhas):
        bot.page = self.Pagina()
        monkeypatch.setattr(bot, "_aplicar_periodo_lista", lambda *_a: True)
        monkeypatch.setattr(bot, "_gerar_relatorio_lista", lambda: True)
        monkeypatch.setattr(bot, "_buscar_na_tabela", lambda termo: termo == "CP029990")
        monkeypatch.setattr(bot, "_linhas_visiveis_lista", lambda: linhas)

    def test_mesmo_cp_real_e_assinatura_completa_confirma(self, bot, monkeypatch):
        titulo = _titulo_simples("NF 390829")
        titulo.fornecedor = "99 TECNOLOGIA"
        titulo.emissao = date(2026, 9, 2)
        titulo.vencimento = date(2026, 9, 14)
        titulo.efetivacao = date(2026, 9, 14)
        titulo.valor = Decimal("2855.52")
        linha = self.Linha(
            "00013933 PRINC NF 390829 99 TECNOLOGIA - 88888888000188 "
            "LOCOMOÇÃO 24-Jun-2026 02-Set-2026 14-Set-2026 2.855,52 "
            "CP029990 Não N/D"
        )
        self._preparar(bot, monkeypatch, [linha])

        assert bot._confirmar_conversao_projetado_na_lista(
            titulo, "CP029990"
        ) == "00013933"

    @pytest.mark.parametrize(
        "texto",
        [
            "00031998 PRINC Projetado 99 TECNOLOGIA 02-Set-2026 "
            "14-Set-2026 2.855,52 CP029990",
            "00013933 PRINC NF 390829 99 TECNOLOGIA 02-Set-2026 "
            "14-Set-2026 9.999,99 CP029990",
            "00013933 PRINC NF 999999 99 TECNOLOGIA 02-Set-2026 "
            "14-Set-2026 2.855,52 CP029990",
        ],
    )
    def test_nao_confirma_projetado_ou_assinatura_divergente(
        self, bot, monkeypatch, texto
    ):
        titulo = _titulo_simples("NF 390829")
        titulo.fornecedor = "99 TECNOLOGIA"
        titulo.emissao = date(2026, 9, 2)
        titulo.vencimento = date(2026, 9, 14)
        titulo.valor = Decimal("2855.52")
        self._preparar(bot, monkeypatch, [self.Linha(texto)])

        assert bot._confirmar_conversao_projetado_na_lista(
            titulo, "CP029990"
        ) == ""


class _InputListaFalso:
    def __init__(self, valor, tipo="text"):
        self.valor = valor
        self.tipo = tipo
        self.teclas = []

    def get_attribute(self, nome):
        return self.tipo if nome == "type" else None

    def input_value(self):
        return self.valor

    def fill(self, valor):
        self.valor = valor

    def dispatch_event(self, _evento):
        pass

    def press(self, tecla):
        self.teclas.append(tecla)


class _ListaInputsFalsa:
    def __init__(self, itens):
        self.itens = itens

    def count(self):
        return len(self.itens)

    def nth(self, indice):
        return self.itens[indice]


class _SelectTipoDataFalso:
    def __init__(self, opcoes=("Emissão", "Vencimento"), selecionado="Emissão"):
        self.opcoes = list(opcoes)
        self.selecionado = selecionado

    def locator(self, _seletor):
        return self

    def all_inner_texts(self):
        return self.opcoes

    def select_option(self, *, label):
        if label not in self.opcoes:
            raise ValueError(label)
        self.selecionado = label

    @property
    def first(self):
        return self

    def inner_text(self):
        return self.selecionado


def _pagina_lista_falsa(inputs, select=None):
    lista_inputs = _ListaInputsFalsa(inputs)
    lista_selects = _ListaInputsFalsa([select or _SelectTipoDataFalso()])

    class Pagina:
        def locator(self, seletor):
            return lista_selects if "select" in seletor else lista_inputs

        def wait_for_timeout(self, _ms):
            pass

    return Pagina()


class TestPeriodoDaLista:
    """Regressão da auditoria que procurou junho com a lista presa em agosto."""

    def test_campos_de_texto_mascarados_sao_preenchidos_em_formato_br(self, bot):
        busca_fornecedor = _InputListaFalso("")
        inicio = _InputListaFalso("01/08/2026")
        fim = _InputListaFalso("31/08/2026")
        busca_tabela = _InputListaFalso("")
        tipo_data = _SelectTipoDataFalso()
        bot.page = _pagina_lista_falsa(
            [busca_fornecedor, inicio, fim, busca_tabela], tipo_data
        )
        assert bot._aplicar_periodo_lista(date(2026, 6, 16), date(2026, 6, 16)) is True
        assert tipo_data.selecionado == "Vencimento"
        assert inicio.valor == "01/06/2026"
        assert fim.valor == "30/06/2026"
        assert fim.teclas == ["Enter"]

    def test_input_date_nativo_continua_usando_formato_iso(self, bot):
        inicio = _InputListaFalso("2026-08-01", tipo="date")
        fim = _InputListaFalso("2026-08-31", tipo="date")
        bot.page = _pagina_lista_falsa([inicio, fim])
        assert bot._aplicar_periodo_lista(date(2026, 6, 2), date(2026, 6, 29)) is True
        assert inicio.valor == "2026-06-01"
        assert fim.valor == "2026-06-30"

    def test_sem_dois_campos_de_data_recusa_a_busca(self, bot):
        bot.page = _pagina_lista_falsa(
            [_InputListaFalso("01/08/2026"), _InputListaFalso("")]
        )
        assert bot._aplicar_periodo_lista(date(2026, 6, 16), date(2026, 6, 16)) is False

    def test_sem_opcao_vencimento_recusa_a_busca(self, bot):
        bot.page = _pagina_lista_falsa(
            [_InputListaFalso("01/08/2026"), _InputListaFalso("31/08/2026")],
            _SelectTipoDataFalso(opcoes=("Emissão", "Cadastro")),
        )
        assert bot._aplicar_periodo_lista(date(2026, 6, 16), date(2026, 6, 16)) is False

    def test_busca_de_ids_nao_converte_falha_de_periodo_em_lista_vazia(self, bot, monkeypatch):
        class Pagina:
            def goto(self, *_a, **_k):
                pass

        bot.page = Pagina()
        monkeypatch.setattr(bot, "_aplicar_periodo_lista", lambda *_a: False)

        with pytest.raises(ErroNavegador, match="campos De/Ate"):
            bot.ids_resultado_busca("DENISE", date(2026, 6, 16), date(2026, 6, 16))


class TestBuscaSeguraDeProjetado:
    class Pagina:
        def goto(self, *_a, **_k):
            pass

        def wait_for_timeout(self, _ms):
            pass

    class Linha:
        def __init__(self, texto):
            self.texto = texto

        def inner_text(self):
            return self.texto

    def preparar(self, bot, monkeypatch, linhas, periodos=None):
        bot.page = self.Pagina()

        def _periodo(inicio, fim):
            if periodos is not None:
                periodos.append((inicio, fim))
            return True

        bot.page = self.Pagina()
        monkeypatch.setattr(bot, "_aplicar_periodo_lista", _periodo)
        monkeypatch.setattr(bot, "_gerar_relatorio_lista", lambda: True)
        monkeypatch.setattr(bot, "_buscar_na_tabela", lambda _termo: True)
        monkeypatch.setattr(bot, "_linhas_visiveis_lista", lambda: linhas)

    def test_busca_usa_o_mes_do_pagamento_e_nao_o_ano(self, bot, monkeypatch):
        """Regra do usuário: despesa de setembro só disputa projetado de setembro."""
        titulo = _titulo_simples()
        periodos: list = []
        self.preparar(bot, monkeypatch, [], periodos=periodos)

        bot.buscar_projetado(titulo)

        assert periodos, "o periodo da lista precisa ser aplicado"
        inicio, fim = periodos[0]
        # O filtro parte da data de pagamento (efetivação), não de 01/01 a 31/12.
        assert inicio == titulo.efetivacao
        assert fim == titulo.efetivacao

    def test_um_projetado_exato_e_retornado(self, bot, monkeypatch):
        titulo = _titulo_simples()
        linha_projetado = self.Linha(
            "000123 Projetado DENISE 300,00"
        )
        self.preparar(bot, monkeypatch, [linha_projetado])
        assert bot.buscar_projetado(titulo) is linha_projetado

    def test_dois_projetados_iguais_param_por_ambiguidade(self, bot, monkeypatch):
        titulo = _titulo_simples()
        linhas = [
            self.Linha("000123 Projetado DENISE 300,00"),
            self.Linha("000124 Projetado DENISE 300,00"),
        ]
        self.preparar(bot, monkeypatch, linhas)
        with pytest.raises(ErroNavegador, match="2 projetados"):
            bot.buscar_projetado(titulo)

    def test_projetado_unico_e_usado_mesmo_com_valor_diferente(self, bot, monkeypatch):
        titulo = _titulo_simples()
        linha = self.Linha("000123 Projetado DENISE 1.300,00")
        self.preparar(bot, monkeypatch, [linha])
        assert bot.buscar_projetado(titulo) is linha
        assert "valor diferente" in bot.ultimo_aviso_projetado

    def test_varios_projetados_usam_o_unico_valor_exato(self, bot, monkeypatch):
        titulo = _titulo_simples()
        diferente = self.Linha("000123 Projetado DENISE 1.300,00")
        exato = self.Linha("000124 Projetado DENISE 300,00")
        self.preparar(bot, monkeypatch, [diferente, exato])
        assert bot.buscar_projetado(titulo) is exato

    def test_varios_sem_valor_exato_usam_o_mais_proximo(self, bot, monkeypatch):
        """Regra do usuário (18/09/2026, ASSINAFACIL): vale o valor da planilha."""
        titulo = _titulo_simples()  # R$ 300,00
        longe = self.Linha("000123 Projetado DENISE 1.300,00")
        perto = self.Linha("000124 Projetado DENISE 400,00")
        self.preparar(bot, monkeypatch, [longe, perto])
        assert bot.buscar_projetado(titulo) is perto
        assert "mais proximo" in bot.ultimo_aviso_projetado
        assert "R$ 400,00" in bot.ultimo_aviso_projetado
        assert "R$ 300,00" in bot.ultimo_aviso_projetado

    def test_varios_sem_valor_exato_e_empatados_param(self, bot, monkeypatch):
        titulo = _titulo_simples()  # R$ 300,00: 200 e 400 estão à mesma distância
        linhas = [
            self.Linha("000123 Projetado DENISE 200,00"),
            self.Linha("000124 Projetado DENISE 400,00"),
        ]
        self.preparar(bot, monkeypatch, linhas)
        with pytest.raises(ErroNavegador, match="igualmente proximos"):
            bot.buscar_projetado(titulo)

    def test_varios_sem_valor_exato_com_valor_ilegivel_param(self, bot, monkeypatch):
        titulo = _titulo_simples()
        linhas = [
            self.Linha("000123 Projetado DENISE 1.300,00"),
            self.Linha("000124 Projetado DENISE sem valor"),
        ]
        self.preparar(bot, monkeypatch, linhas)
        with pytest.raises(ErroNavegador, match="nao pode ser lido"):
            bot.buscar_projetado(titulo)

    def test_falha_de_periodo_nao_vira_ausencia_de_projetado(self, bot, monkeypatch):
        bot.page = self.Pagina()
        monkeypatch.setattr(bot, "_aplicar_periodo_lista", lambda *_a: False)
        with pytest.raises(ErroNavegador, match="periodo"):
            bot.buscar_projetado(_titulo_simples())


class _CampoBuscaFalso:
    def __init__(self, pagina, tipo):
        self.pagina = pagina
        self.tipo = tipo
        self.preenchimentos = []
        self.digitacoes = []
        self.valor = ""

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def fill(self, valor):
        self.preenchimentos.append(valor)
        self.valor = valor
        if self.tipo == "fornecedor":
            self.pagina.busca_fornecedor = valor
        else:
            self.pagina.busca_tabela = valor

    def type(self, valor, delay=None):
        self.digitacoes.append(valor)
        self.valor = valor
        if self.tipo == "fornecedor":
            self.pagina.busca_fornecedor = valor
        else:
            self.pagina.busca_tabela = valor

    def input_value(self):
        return self.valor

    def press(self, _tecla):
        pass


class _LinhaBuscaFalsa:
    def __init__(self, texto):
        self.texto = texto

    def inner_text(self):
        return self.texto


class _OpcaoFornecedorFalsa:
    def __init__(self, pagina, texto):
        self.pagina = pagina
        self.texto = texto
        self.cliques = 0

    def inner_text(self):
        return self.texto

    def click(self):
        self.cliques += 1
        self.pagina.busca_fornecedor = self.texto
        self.pagina.campo_fornecedor.valor = self.texto


class _LinhasBuscaFalsas:
    def __init__(self, pagina):
        self.pagina = pagina

    def _atuais(self):
        if (
            self.pagina.busca_geral_funciona
            and "DENISE" in self.pagina.busca_tabela.upper()
        ):
            return [self.pagina.linha]
        if self.pagina.fornecedor_funciona and "DENISE" in self.pagina.busca_fornecedor.upper():
            return [self.pagina.linha]
        if self.pagina.linhas_antigas and self.pagina.busca_fornecedor:
            return [self.pagina.linha_antiga]
        if self.pagina.busca_tabela.upper() in ("CP029999", "00012345", "12345"):
            return [self.pagina.linha]
        return []

    def count(self):
        return len(self._atuais())

    def nth(self, indice):
        return self._atuais()[indice]


class _PaginaBuscaTituloFalsa:
    def __init__(
        self,
        fornecedor_funciona=True,
        linhas_antigas=False,
        busca_geral_funciona=True,
    ):
        self.fornecedor_funciona = fornecedor_funciona
        self.linhas_antigas = linhas_antigas
        self.busca_geral_funciona = busca_geral_funciona
        self.busca_fornecedor = ""
        self.busca_tabela = ""
        self.linha = _LinhaBuscaFalsa(
            "00012345 DENISE CRISTINA DA ROCHA BARBOSA RPA 300,00 CP029999"
        )
        self.linha_antiga = _LinhaBuscaFalsa(
            "00031746 ROSANA QUEIROZ ASSESSORIA 8.802,53 CP029738"
        )
        self.campo_fornecedor = _CampoBuscaFalso(self, "fornecedor")
        self.campo_tabela = _CampoBuscaFalso(self, "tabela")
        self.linhas = _LinhasBuscaFalsas(self)
        self.opcao_fornecedor = _OpcaoFornecedorFalsa(
            self, "DENISE CRISTINA DA ROCHA BARBOSA"
        )
        self.opcoes_fornecedor = _ListaInputsFalsa([self.opcao_fornecedor])
        self.icone_relatorio = type(
            "IconeRelatorio",
            (),
            {
                "cliques": 0,
                "is_visible": lambda self: True,
                "click": lambda self: setattr(self, "cliques", self.cliques + 1),
            },
        )()
        self.icones_relatorio = _ListaInputsFalsa([self.icone_relatorio])

    def locator(self, seletor):
        if seletor == "table tbody tr":
            return self.linhas
        if seletor == ".typeahead__list:visible a":
            return self.opcoes_fornecedor
        if "fa-file-text" in seletor:
            return self.icones_relatorio
        if "dataTables_filter" in seletor or "type='search'" in seletor:
            return self.campo_tabela
        return self.campo_fornecedor

    def wait_for_timeout(self, _ms):
        pass

    def goto(self, *_a, **_k):
        pass

    def wait_for_selector(self, _seletor, timeout=None):
        if not self.busca_fornecedor:
            raise TimeoutError


class TestBuscaSeparadaDaLista:
    def test_extrai_id_e_codigo_de_lancamento_da_mesma_linha(self, bot):
        assert bot._identificadores_da_linha(
            "00012345 DENISE RPA CP029999"
        ) == ("00012345", "CP029999")

    def test_abre_titulo_pelo_lapis_da_linha_sem_salvar(self, bot):
        class AcaoEditar:
            def __init__(self):
                self.cliques = 0

            def is_visible(self):
                return True

            def click(self, timeout=None):
                self.cliques += 1

        acao = AcaoEditar()

        class Linha:
            def locator(self, seletor):
                if "pencil" in seletor or "fa-edit" in seletor:
                    return _ListaInputsFalsa([acao])
                return _ListaInputsFalsa([])

        # Simula a página com o campo fornecedor visível após o clique
        campo_fornecedor = _ListaInputsFalsa(["campo"])

        class Pagina:
            url = "https://ongsys.test/contas-pagar"

            def wait_for_timeout(self, _ms):
                pass

            def locator(self, seletor):
                if "field_fornecedor" in seletor:
                    return campo_fornecedor
                return _ListaInputsFalsa([])

        bot.page = Pagina()

        assert bot.abrir_linha_para_auditoria(Linha()) is True
        assert acao.cliques == 1

    def test_reabre_pelo_nome_sem_digitar_id_no_campo_fornecedor(self, bot):
        pagina = _PaginaBuscaTituloFalsa(fornecedor_funciona=True)
        bot.page = pagina

        alvo = bot.localizar_linha_titulo("00012345", "DENISE CRISTINA")

        assert alvo is pagina.linha
        assert pagina.campo_fornecedor.digitacoes == ["DENISE CRISTINA"]
        assert pagina.opcao_fornecedor.cliques == 1
        assert pagina.campo_tabela.preenchimentos == []
        assert bot.codigo_lancamento_para_id("00012345") == "CP029999"
        assert pagina.icone_relatorio.cliques == 1

    def test_se_nome_nao_localizar_usa_codigo_cp_no_buscar_da_tabela(self, bot):
        pagina = _PaginaBuscaTituloFalsa(fornecedor_funciona=False)
        bot.page = pagina
        bot._codigos_lancamento_por_id["12345"] = "CP029999"

        alvo = bot.localizar_linha_titulo("00012345", "DENISE CRISTINA")

        assert alvo is pagina.linha
        assert pagina.campo_fornecedor.digitacoes == ["DENISE CRISTINA"]
        assert pagina.campo_tabela.preenchimentos[0] == "CP029999"
        assert "00012345" not in pagina.campo_fornecedor.preenchimentos

    def test_filtro_de_fornecedor_vazio_e_limpo_antes_de_buscar_pelo_id(self, bot):
        # Tela real (2026-10-01): o autocomplete escolheu um cadastro duplicado
        # do fornecedor, a lista filtrada voltou vazia e a busca pelo ID, feita
        # com o filtro ainda aplicado, não achava o título.
        pagina = _PaginaBuscaTituloFalsa(fornecedor_funciona=False)
        bot.page = pagina

        alvo = bot.localizar_linha_titulo("00012345", "DENISE CRISTINA")

        assert alvo is pagina.linha
        assert pagina.busca_fornecedor == ""
        assert pagina.campo_fornecedor.preenchimentos[-1] == ""
        assert pagina.icone_relatorio.cliques == 2
        assert pagina.campo_tabela.preenchimentos == ["00012345"]

    def test_buscar_geral_por_nome_e_fallback_sem_novo_clique(self, bot, monkeypatch):
        pagina = _PaginaBuscaTituloFalsa(
            fornecedor_funciona=False,
            linhas_antigas=True,
            busca_geral_funciona=True,
        )
        bot.page = pagina
        monkeypatch.setattr(bot, "_aplicar_periodo_lista", lambda *_a: True)

        ids = bot.ids_resultado_busca(
            "DENISE", date(2026, 6, 16), date(2026, 6, 16)
        )

        assert ids == ["00012345"]
        assert pagina.campo_tabela.preenchimentos == ["DENISE"]
        assert pagina.icone_relatorio.cliques == 1

    def test_lista_antiga_de_outros_fornecedores_nunca_vira_candidata(self, bot, monkeypatch):
        pagina = _PaginaBuscaTituloFalsa(
            fornecedor_funciona=False,
            linhas_antigas=True,
            busca_geral_funciona=False,
        )
        bot.page = pagina
        monkeypatch.setattr(bot, "_aplicar_periodo_lista", lambda *_a: True)
        monkeypatch.setattr(bot, "screenshot_falha", lambda *_a: "evidencia.png")

        with pytest.raises(ErroNavegador, match=r"Ver relatorio.*Buscar.*evidencia.png"):
            bot.ids_resultado_busca(
                "DENISE", date(2026, 6, 16), date(2026, 6, 16)
            )


class TestIncluirNovaLinhaRateio:
    """Regressão da BENEFIX: a terceira linha deve ter uma rota alternativa."""

    def test_prioriza_botao_superior_visivel(self, bot, monkeypatch):
        chamadas = []
        linha_criada = {3: False}

        monkeypatch.setattr(
            bot, "_linha_rateio_visivel", lambda indice: linha_criada.get(indice, False)
        )

        class Botao:
            first = None
            def __init__(self): self.first = self
            def click(self, **_kw):
                chamadas.append("clique")
                linha_criada[3] = True
        monkeypatch.setattr(bot, "_campo", lambda _seletor: Botao())
        bot.page = type("Pagina", (), {"wait_for_timeout": lambda self, _ms: None})()

        bot._incluir_linha_rateio(2)

        assert chamadas == ["clique"]

    def test_usa_javascript_no_mesmo_botao_como_fallback(self, bot, monkeypatch):
        chamadas = []
        linha_criada = {3: False}

        monkeypatch.setattr(
            bot, "_linha_rateio_visivel", lambda indice: linha_criada.get(indice, False)
        )

        class Botao:
            first = None
            def __init__(self): self.first = self
            def click(self, **_kw):
                chamadas.append("clique")
                raise TimeoutError("overlay")
            def evaluate(self, _script):
                chamadas.append("JavaScript")
                linha_criada[3] = True
        monkeypatch.setattr(bot, "_campo", lambda _seletor: Botao())
        bot.page = type("Pagina", (), {"wait_for_timeout": lambda self, _ms: None})()

        bot._incluir_linha_rateio(2)

        assert chamadas == ["clique", "JavaScript"]

    def test_nao_cria_outra_linha_se_a_proxima_ja_existe(self, bot, monkeypatch):
        monkeypatch.setattr(bot, "_linha_rateio_visivel", lambda indice: indice == 3)
        chamadas = []
        monkeypatch.setattr(bot, "_clicar", lambda *args: chamadas.append(args))

        bot._incluir_linha_rateio(2)

        assert chamadas == []

    def test_clique_superior_via_javascript_recupera_overlay_invisivel(
        self, bot, monkeypatch
    ):
        linha_criada = {2: False}

        class Elemento:
            first = None

            def __init__(self, seletor):
                self.first = self
                self.seletor = seletor

            def count(self):
                return 1

            def evaluate(self, _script):
                if self.seletor == S.BOTAO_INCLUIR_LINHA_RATEIO:
                    linha_criada[2] = True

            def wait_for(self, **_kwargs):
                if not linha_criada[2]:
                    raise TimeoutError("linha ainda ausente")

        monkeypatch.setattr(
            bot, "_linha_rateio_visivel", lambda indice: linha_criada.get(indice, False)
        )
        monkeypatch.setattr(
            bot, "_clicar", lambda *_a: (_ for _ in ()).throw(TimeoutError("overlay"))
        )
        monkeypatch.setattr(bot, "_campo", lambda seletor: Elemento(seletor))
        bot.page = type("Pagina", (), {"wait_for_timeout": lambda self, _ms: None})()

        bot._incluir_linha_rateio(1)

        assert linha_criada[2] is True

    def test_recusa_do_ongsys_revalida_linha_anterior_e_tenta_uma_vez(
        self, bot, monkeypatch
    ):
        estado = {"modal": True, "linha_2": False}
        revalidacoes = []

        class Botao:
            first = None

            def __init__(self):
                self.first = self

            def click(self, **_kw):
                pass

            def evaluate(self, _script):
                estado["linha_2"] = True

        bot.page = type("Pagina", (), {"wait_for_timeout": lambda self, _ms: None})()
        monkeypatch.setattr(
            bot, "_linha_rateio_visivel", lambda indice: indice == 2 and estado["linha_2"]
        )
        monkeypatch.setattr(bot, "_campo", lambda _seletor: Botao())
        monkeypatch.setattr(
            bot,
            "_texto_modal_erro",
            lambda: "É necessário preencher a linha atual de rateio" if estado["modal"] else "",
        )
        monkeypatch.setattr(bot, "_diagnostico_linha_rateio", lambda _i: "diagnostico")
        monkeypatch.setattr(
            bot, "_fechar_modal_erro_confirmado", lambda: estado.update(modal=False) or True
        )
        monkeypatch.setattr(
            bot,
            "_garantir_campos_obrigatorios_rateio",
            lambda indice, _rateio: revalidacoes.append(indice),
        )
        rateio = Rateio(
            4, Decimal("100.00"), "GESTAO ADM", "DESPESAS_ADMINISTRATIVAS",
            "VALE REFEICAO/REFEITORIO", "", "Despesas RH", Decimal("100")
        )

        assert bot._incluir_linha_rateio(1, rateio) == []
        assert revalidacoes == [1, 1]
        assert estado["linha_2"] is True


class TestClicarOpcaoFechaPopupPendente:
    """Bug real do piloto: um aviso assíncrono (documento duplicado) chegou

    tarde, depois de vários campos já preenchidos, e bloqueou o clique numa
    opção de autocomplete completamente diferente (Categoria/Conta) com
    "elemento intercepta eventos de clique". `_clicar_opcao` precisa
    defender esse caso para qualquer campo com autocomplete, não só os
    pontos onde o aviso já era esperado.
    """

    def test_clica_imediatamente_sem_verificacao_previa(self, bot, monkeypatch):
        """Opções de autocomplete são efêmeras: a lista suspensa do Ongsys
        fecha sozinha em ~300-500ms. Verificar popups ANTES do clique gastava
        ~400ms em consultas DOM e a opção sumia. O clique deve ser imediato;
        se um popup estiver no caminho, `_clicar_resiliente` trata na
        retentativa."""

        class Opcao:
            def __init__(self, texto):
                self._texto = texto
                self.cliques = 0

            def inner_text(self):
                return self._texto

            def click(self, timeout=None):
                self.cliques += 1

        alvo = Opcao("2.5.1.04.007 - LIMPEZA (A)")

        class ListaOpcoes:
            def count(self):
                return 1

            def nth(self, _i):
                return alvo

        class PaginaMinima:
            def locator(self, _seletor):
                return ListaOpcoes()

            def wait_for_timeout(self, _ms):
                pass

        bot.page = PaginaMinima()
        chamadas_popup = []
        monkeypatch.setattr(bot, "_fechar_popups", lambda **kw: chamadas_popup.append(kw) or True)

        bot._clicar_opcao("2.5.1.04.007 - LIMPEZA (A)")

        assert not chamadas_popup, "não deveria checar popup antes de clicar (opção efêmera)"
        assert alvo.cliques == 1

    def test_reclica_quando_o_clique_e_interceptado_por_um_aviso_tardio(self, bot, monkeypatch):
        """O caso real: o popup passa a existir só NO INSTANTE do clique —
        checar antes não é suficiente, é preciso reagir a uma falha do
        próprio clique e tentar de novo."""

        class OpcaoInterceptadaDuasVezes:
            def __init__(self, texto):
                self._texto = texto
                self.tentativas = 0

            def inner_text(self):
                return self._texto

            def click(self, timeout=None):
                self.tentativas += 1
                if self.tentativas <= 2:
                    raise Exception("intercepts pointer events: #modalErro")
                # na terceira tentativa, o aviso já fechou e o clique funciona

        alvo = OpcaoInterceptadaDuasVezes("GESTAO ADM")

        class ListaOpcoes:
            def count(self):
                return 1

            def nth(self, _i):
                return alvo

        class PaginaMinima:
            def locator(self, _seletor):
                return ListaOpcoes()

            def wait_for_timeout(self, _ms):
                pass

        bot.page = PaginaMinima()
        monkeypatch.setattr(bot, "_fechar_popups", lambda **_: True)

        bot._clicar_opcao("GESTAO ADM")  # não deve levantar

        assert alvo.tentativas == 3

    def test_desiste_com_erro_claro_se_nunca_conseguir_clicar(self, bot, monkeypatch):
        from ongsys.navegador import ErroNavegador

        class OpcaoSempreInterceptada:
            def inner_text(self):
                return "GESTAO ADM"

            def click(self, timeout=None):
                raise Exception("intercepts pointer events: #modalErro")

        alvo = OpcaoSempreInterceptada()

        class ListaOpcoes:
            def count(self):
                return 1

            def nth(self, _i):
                return alvo

        class PaginaMinima:
            def locator(self, _seletor):
                return ListaOpcoes()

            def wait_for_timeout(self, _ms):
                pass

        bot.page = PaginaMinima()
        monkeypatch.setattr(bot, "_fechar_popups", lambda **_: False)

        with pytest.raises(ErroNavegador) as erro:
            bot._clicar_opcao("GESTAO ADM")
        assert "aviso" in str(erro.value).lower()


class TestSelecionarPorNomeComOpcoesPrefixadas:
    """Bug real do piloto: 'LIMPEZA' empatava com semelhança 1.0 entre as

    opções 'LIMPEZA' e 'MATERIAL DE LIMPEZA' (as duas contêm a palavra
    inteira como substring), e o programa corretamente recusava escolher —
    mas deveria ter reconhecido a correspondência exata pelo nome.
    """

    def test_resolve_o_empate_real_da_categoria_limpeza(self, bot, monkeypatch):
        monkeypatch.setattr(
            bot, "_candidatos_autocomplete",
            lambda *_: ["2.5.1.04.007 - LIMPEZA (A)", "2.5.1.06.006 - MATERIAL DE LIMPEZA (A)"],
        )
        cliques = []
        monkeypatch.setattr(bot, "_clicar_opcao", lambda texto: cliques.append(texto))

        escolhido = bot._selecionar_por_nome("#field_categoria_conta_1", "LIMPEZA", "Categoria/Conta")

        assert escolhido == "2.5.1.04.007 - LIMPEZA (A)"
        assert cliques == ["2.5.1.04.007 - LIMPEZA (A)"]

    def test_ainda_recusa_quando_o_nome_tambem_empata(self, bot, monkeypatch):
        """Se duas opções tiverem o MESMO nome (não só a mesma substring),
        continua sem escolher — a segurança não pode depender só desse
        atalho."""
        monkeypatch.setattr(
            bot, "_candidatos_autocomplete",
            lambda *_: ["2.5.1.04.007 - LIMPEZA (A)", "9.9.9.999 - LIMPEZA (B)"],
        )
        monkeypatch.setattr(bot, "_clicar_opcao", lambda texto: pytest.fail("não deveria clicar"))

        with pytest.raises(ErroNavegador):
            bot._selecionar_por_nome("#field_categoria_conta_1", "LIMPEZA", "Categoria/Conta")

    def test_texto_completo_exato_continua_funcionando(self, bot, monkeypatch):
        """Uma opção sem código/sufixo, batendo exatamente, não muda de
        comportamento."""
        monkeypatch.setattr(bot, "_candidatos_autocomplete", lambda *_: ["GESTAO ADM"])
        cliques = []
        monkeypatch.setattr(bot, "_clicar_opcao", lambda texto: cliques.append(texto))

        escolhido = bot._selecionar_por_nome("#field_projeto_cc_1", "GESTAO ADM", "Centro de Custos")
        assert escolhido == "GESTAO ADM"
        assert cliques == ["GESTAO ADM"]

    def test_campo_ja_selecionado_e_bloqueado_nao_e_preenchido_de_novo(
        self, bot, monkeypatch
    ):
        """Regressão real: o clique concluiu, mas o Playwright deu timeout.

        O Ongsys já exibia o valor correto e havia desabilitado o input. Uma
        nova busca tentava fill("") nesse campo disabled e abortava o título.
        """
        monkeypatch.setattr(bot, "_ler_valor", lambda _s: "5 - DESPESAS ADM")
        monkeypatch.setattr(
            bot,
            "_buscar_e_clicar_por_nome",
            lambda *_: pytest.fail("não deve tentar preencher um campo já confirmado"),
        )

        escolhido = bot._selecionar_por_nome(
            "#field_atividade_1", "DESPESAS ADM", "Atividade"
        )

        assert escolhido == "5 - DESPESAS ADM"

    def test_campo_bloqueado_so_e_aceito_com_nome_exato(self, bot, monkeypatch):
        leituras = iter(["5 - DESPESAS OPERACIONAIS", ""])
        monkeypatch.setattr(bot, "_ler_valor", lambda _s: next(leituras, ""))
        tentativas = []

        def buscar(*_args, **_kwargs):
            tentativas.append(1)
            return "5 - DESPESAS ADM"

        monkeypatch.setattr(bot, "_buscar_e_clicar_por_nome", buscar)

        escolhido = bot._selecionar_por_nome(
            "#field_atividade_1", "DESPESAS ADM", "Atividade"
        )

        assert escolhido == "5 - DESPESAS ADM"
        assert tentativas == [1]

    def test_timeout_do_clique_aceita_valor_exato_que_a_tela_ja_aplicou(
        self, bot, monkeypatch
    ):
        class Opcao:
            def click(self, **_kwargs):
                raise RuntimeError("timeout depois de o Ongsys processar o clique")

        class Lista:
            @property
            def first(self):
                return self

            def is_enabled(self):
                return True

            def nth(self, _indice):
                return Opcao()

        class Pagina:
            def locator(self, _seletor):
                return Lista()

            def wait_for_timeout(self, _ms):
                pass

        bot.page = Pagina()
        monkeypatch.setattr(
            bot, "_candidatos_autocomplete", lambda *_: ["5 - DESPESAS ADM"]
        )
        leituras = iter(["", "5 - DESPESAS ADM"])
        monkeypatch.setattr(bot, "_ler_valor", lambda _s: next(leituras, ""))

        escolhido = bot._selecionar_por_nome(
            "#field_atividade_1", "DESPESAS ADM", "Atividade"
        )

        assert escolhido == "5 - DESPESAS ADM"

    def test_clique_falho_refaz_a_busca_em_vez_de_reclicar_na_lista_fechada(self, bot, monkeypatch):
        """Bug real do piloto: o popup interceptou o clique; fechá-lo também

        fechou a lista de sugestões junto. Reclicar na mesma opção "fantasma"
        nunca funciona — é preciso digitar de novo para reabrir a lista.
        """
        buscas = []

        def buscar_de_novo(_seletor, _consulta):
            buscas.append(1)
            return ["2.5.1.04.007 - LIMPEZA (A)", "2.5.1.06.006 - MATERIAL DE LIMPEZA (A)"]

        monkeypatch.setattr(bot, "_candidatos_autocomplete", buscar_de_novo)

        cliques = []

        def clicar(texto):
            cliques.append(texto)
            if len(cliques) == 1:
                raise ErroNavegador("A opção sumiu da lista antes do clique.")

        monkeypatch.setattr(bot, "_clicar_opcao", clicar)

        escolhido = bot._selecionar_por_nome("#field_categoria_conta_1", "LIMPEZA", "Categoria/Conta")

        assert escolhido == "2.5.1.04.007 - LIMPEZA (A)"
        assert len(cliques) == 2  # tentou de novo, não desistiu na primeira falha
        assert len(buscas) >= 2  # e refez a busca, não só o clique

    def test_cai_para_a_primeira_palavra_quando_o_nome_completo_nao_acha_nada(self, bot, monkeypatch):
        """Bug real do piloto: buscar 'GESTAO ADM' não trouxe nenhuma

        sugestão. A primeira palavra sozinha ('GESTAO') pode bater mesmo
        quando o nome completo não bate por causa de alguma diferença de
        grafia no cadastro."""
        consultas_feitas = []

        def busca(_seletor, consulta):
            consultas_feitas.append(consulta)
            if consulta == "GESTAO":
                return ["GESTAO ADM"]
            return []

        monkeypatch.setattr(bot, "_candidatos_autocomplete", busca)
        cliques = []
        monkeypatch.setattr(bot, "_clicar_opcao", lambda texto: cliques.append(texto))

        escolhido = bot._selecionar_por_nome("#field_projeto_cc_1", "GESTAO ADM", "Centro de Custos")

        assert escolhido == "GESTAO ADM"
        assert "GESTAO" in consultas_feitas
        assert cliques == ["GESTAO ADM"]


def _titulo_simples(documento="RPA") -> Titulo:
    return Titulo(
        chave="k", linhas_origem=[4], fornecedor="DENISE", documento=documento,
        documento_original=documento, emissao=date(2026, 6, 1),
        vencimento=date(2026, 6, 2), efetivacao=date(2026, 6, 2), valor=Decimal("300.00"),
        conta_pagadora_id="6", conta_pagadora_nome="BANCO ITAU 12345-6 SEDE",
        tipo_despesa="Contratação de Serviço(s)", tipo_documento="Outros",
        meio_pagamento="Internet Banking", tipo_pagamento="Transferência Eletrônica",
        historico="LIMPEZA",
        rateios=[Rateio(4, Decimal("300.00"), "GESTAO ADM", "SERVICOS_TOMADOS_DE_TERCEIROS",
                        "LIMPEZA", "", "", Decimal("100"))],
    )


class _ElementoFalso:
    def __init__(self, visivel):
        self._visivel = visivel

    def is_visible(self):
        return self._visivel

    def wait_for(self, state="visible", timeout=None):
        if not self._visivel:
            raise TimeoutError("nunca ficou visível")


class TestAbrirAbaRateio:
    def test_ja_visivel_nao_clica_em_nada(self, bot, monkeypatch):
        campo = _ElementoFalso(visivel=True)

        class Pagina:
            def locator(self, seletor):
                class L:
                    first = campo
                return L()

        bot.page = Pagina()
        cliques = []
        monkeypatch.setattr(bot, "_clicar", lambda *a: cliques.append(a))
        bot._abrir_aba_rateio()
        assert cliques == []

    def test_nao_visivel_clica_na_aba_e_espera_aparecer(self, bot, monkeypatch):
        campo = _ElementoFalso(visivel=False)

        class Pagina:
            def locator(self, seletor):
                class L:
                    first = campo
                return L()

            def wait_for_timeout(self, _ms):
                pass

        bot.page = Pagina()
        cliques = []

        def clicar_e_revelar(seletor, descricao):
            cliques.append((seletor, descricao))
            campo._visivel = True  # simula a aba abrindo e revelando o campo

        monkeypatch.setattr(bot, "_clicar", clicar_e_revelar)
        bot._abrir_aba_rateio()
        assert len(cliques) == 1
        assert "rateio" in cliques[0][1].lower()

    def test_aba_nao_existe_gera_erro_claro(self, bot, monkeypatch):
        campo = _ElementoFalso(visivel=False)

        class Pagina:
            def locator(self, seletor):
                class L:
                    first = campo
                return L()

        bot.page = Pagina()

        def clicar_falha(*_a):
            raise Exception("seletor não encontrado")

        monkeypatch.setattr(bot, "_clicar", clicar_falha)
        with pytest.raises(ErroNavegador) as erro:
            bot._abrir_aba_rateio()
        assert "Rateio e Parcelamento" in str(erro.value)

    def test_aba_clicada_mas_campo_nunca_aparece(self, bot, monkeypatch):
        campo = _ElementoFalso(visivel=False)

        class Pagina:
            def locator(self, seletor):
                class L:
                    first = campo
                return L()

        bot.page = Pagina()
        monkeypatch.setattr(bot, "_clicar", lambda *_a: None)  # clica, mas nada muda
        with pytest.raises(ErroNavegador) as erro:
            bot._abrir_aba_rateio()
        assert "não ficaram visíveis" in str(erro.value)


class TestPreencherDocumentoTrocandoDeAba:
    """Bug real do piloto: o aviso de documento duplicado apareceu só

    depois de já estar na aba "Rateio e Parcelamento" — nesse momento, o
    campo Número do Documento (que vive em "Dados do pagamento") está
    escondido, e preencher falha com "elemento não visível".
    """

    def test_preenche_direto_quando_o_campo_ja_esta_acessivel(self, bot, monkeypatch):
        """Caso comum: ainda na aba certa, não precisa trocar de aba."""
        preenchidos = []
        monkeypatch.setattr(bot, "_preencher", lambda seletor, valor: preenchidos.append((seletor, valor)))
        monkeypatch.setattr(bot, "_campo", lambda _s: _CampoFalsoParaTab())

        bot._preencher_documento_trocando_de_aba_se_preciso("RPA.1")

        assert ("#field_numero_documento", "RPA.1") in preenchidos

    def test_volta_para_dados_do_pagamento_quando_o_campo_esta_escondido(self, bot, monkeypatch):
        from ongsys import seletores as S

        cliques_aba: list[str] = []
        tentativas_preencher = {"n": 0}

        def preencher_falha_na_primeira(seletor, valor):
            tentativas_preencher["n"] += 1
            if tentativas_preencher["n"] == 1:
                raise Exception("element is not visible")

        class ElementoClicavel:
            def __init__(self, nome):
                self._nome = nome

            def click(self):
                cliques_aba.append(self._nome)

            def is_visible(self):
                return True

            def press(self, _tecla):
                pass

        def campo_falso(seletor):
            if seletor == S.ABA_DADOS_PAGAMENTO:
                nome = "dados_pagamento"
            elif seletor == S.ABA_RATEIO_PARCELAMENTO:
                nome = "rateio"
            else:
                nome = seletor
            elemento = ElementoClicavel(nome)
            return type("L", (), {"first": elemento})()

        monkeypatch.setattr(bot, "_preencher", preencher_falha_na_primeira)
        monkeypatch.setattr(bot, "_campo", campo_falso)
        bot.page = type("Pagina", (), {"wait_for_timeout": lambda self, _ms: None})()

        bot._preencher_documento_trocando_de_aba_se_preciso("RPA.1")

        assert "dados_pagamento" in cliques_aba
        assert "rateio" in cliques_aba  # voltou para onde estava
        assert tentativas_preencher["n"] == 2  # tentou, falhou, tentou de novo depois de trocar


class TestFecharModalErroConfirmado:
    """Confirmado pelo usuário: o popup de documento duplicado NÃO fecha

    sozinho ao trocar o documento, mesmo que o novo valor seja aceito — ele
    fica ali, aguardando ser fechado, sem impedir o resto do preenchimento
    nesse meio-tempo. `_fechar_modal_erro_confirmado` precisa confirmar que
    ele realmente sumiu, não só tentar uma vez.
    """

    def test_modal_inexistente_conta_como_fechado(self, bot):
        class Locator:
            def count(self):
                return 0

        bot.page = type("Pagina", (), {
            "locator": lambda self, _s: Locator(),
            "wait_for_timeout": lambda self, _ms: None,
        })()
        assert bot._fechar_modal_erro_confirmado() is True

    def test_clica_fechar_e_confirma_que_sumiu(self, bot):
        class BotaoFechar:
            def is_visible(self):
                return True

            def click(self, timeout=None):
                estado["existe"] = False

        class ListaBotoes:
            def count(self):
                return 1

            def nth(self, _i):
                return BotaoFechar()

        estado = {"existe": True}

        class Modal:
            def is_visible(self):
                return estado["existe"]

            def locator(self, _s):
                return ListaBotoes()

        class ListaModal:
            def count(self):
                return 1 if estado["existe"] else 0

            @property
            def first(self):
                return Modal()

        class Pagina:
            def locator(self, seletor):
                if seletor == "#modalErro":
                    return ListaModal()
                return ListaModal()

            def wait_for_timeout(self, _ms):
                pass

        bot.page = Pagina()
        assert bot._fechar_modal_erro_confirmado() is True

    def test_nunca_fecha_devolve_false(self, bot):
        class BotaoQueNaoFuncionaDeVerdade:
            def is_visible(self):
                return True

            def click(self, timeout=None):
                pass  # clica, mas o modal continua lá (situação real observada)

        class ListaBotoes:
            def count(self):
                return 1

            def nth(self, _i):
                return BotaoQueNaoFuncionaDeVerdade()

        class Modal:
            def is_visible(self):
                return True

            def locator(self, _s):
                return ListaBotoes()

        class ListaModal:
            def count(self):
                return 1

            @property
            def first(self):
                return Modal()

        bot.page = type("Pagina", (), {
            "locator": lambda self, _s: ListaModal(),
            "wait_for_timeout": lambda self, _ms: None,
        })()
        assert bot._fechar_modal_erro_confirmado() is False

    def test_prioriza_data_dismiss_modal_padrao_do_bootstrap(self, bot):
        """A classe "modal fade ... in" é Bootstrap 3: data-dismiss="modal"

        é como o próprio framework escuta o fechamento — mais confiável que
        adivinhar texto/tag de botão."""
        chamadas = {"data_dismiss": 0, "popup_fechar": 0}

        class Botao:
            def __init__(self, tipo):
                self._tipo = tipo

            def is_visible(self):
                return True

            def click(self, timeout=None):
                chamadas[self._tipo] += 1
                estado["existe"] = False

        class ListaBotoes:
            def __init__(self, tipo):
                self._tipo = tipo

            def count(self):
                return 1

            def nth(self, _i):
                return Botao(self._tipo)

        estado = {"existe": True}

        class Modal:
            def is_visible(self):
                return estado["existe"]

            def locator(self, seletor):
                if seletor == "[data-dismiss='modal']":
                    return ListaBotoes("data_dismiss")
                return ListaBotoes("popup_fechar")

        class ListaModal:
            def count(self):
                return 1 if estado["existe"] else 0

            @property
            def first(self):
                return Modal()

        bot.page = type("Pagina", (), {
            "locator": lambda self, _s: ListaModal(),
            "wait_for_timeout": lambda self, _ms: None,
        })()

        assert bot._fechar_modal_erro_confirmado() is True
        assert chamadas["data_dismiss"] == 1
        assert chamadas["popup_fechar"] == 0  # nem chegou a tentar o outro: o primeiro já fechou

    def test_recorre_a_clique_via_javascript_se_o_clique_simulado_falhar(self, bot):
        """Se o clique "de verdade" do Playwright não registrar (ex.: algum

        problema de posição/z-index), tenta disparar o evento direto no
        elemento via JS antes de desistir dessa tentativa."""

        class BotaoQueSoFechaViaJs:
            def is_visible(self):
                return True

            def click(self, timeout=None):
                raise Exception("elemento não clicável nesta posição")

            def evaluate(self, _script):
                estado["existe"] = False

        class ListaBotoes:
            def count(self):
                return 1

            def nth(self, _i):
                return BotaoQueSoFechaViaJs()

        estado = {"existe": True}

        class Modal:
            def is_visible(self):
                return estado["existe"]

            def locator(self, _s):
                return ListaBotoes()

        class ListaModal:
            def count(self):
                return 1 if estado["existe"] else 0

            @property
            def first(self):
                return Modal()

        bot.page = type("Pagina", (), {
            "locator": lambda self, _s: ListaModal(),
            "wait_for_timeout": lambda self, _ms: None,
        })()

        assert bot._fechar_modal_erro_confirmado() is True


class TestTextoModalErro:
    """Bug real do piloto: a mensagem do #modalErro ficava sem ser

    detectada de forma persistente, mesmo com o popup claramente bloqueando
    cliques. Este é o caminho mais direto possível de leitura — sem
    seletores combinados, sem `:visible`, sem lógica de exclusão — usado
    como reforço quando `_mensagens_da_tela` não encontra nada.
    """

    def test_le_o_texto_quando_o_modal_existe(self, bot):
        class Modal:
            def is_visible(self):
                return True

            def inner_text(self):
                return "O número do documento fiscal já existe para esse fornecedor."

        class Locator:
            def count(self):
                return 1

            @property
            def first(self):
                return Modal()

        bot.page = type("Pagina", (), {"locator": lambda self, _s: Locator()})()
        assert "ja existe" in bot._texto_modal_erro().lower() or "já existe" in bot._texto_modal_erro()

    def test_vazio_quando_o_modal_nao_existe(self, bot):
        class Locator:
            def count(self):
                return 0

        bot.page = type("Pagina", (), {"locator": lambda self, _s: Locator()})()
        assert bot._texto_modal_erro() == ""

    def test_vazio_em_vez_de_estourar_sem_pagina(self, bot):
        bot.page = None
        assert bot._texto_modal_erro() == ""

    def test_resolver_usa_o_modal_erro_quando_mensagens_da_tela_nao_acha_nada(self, bot, monkeypatch):
        """A detecção principal falhou de forma persistente em uso real; o

        resolvedor precisa continuar funcionando mesmo assim, via este
        caminho alternativo."""
        titulo = _titulo_simples()
        from ongsys.navegador import ResultadoInclusao

        resultado = ResultadoInclusao(documento_final=titulo.documento)
        monkeypatch.setattr(bot, "_mensagens_da_tela", lambda: "")  # o caminho principal falha
        monkeypatch.setattr(
            bot, "_texto_modal_erro",
            lambda: "O número do documento fiscal já existe para esse fornecedor.",
        )
        monkeypatch.setattr(bot, "_fechar_popups", lambda **_: True)
        monkeypatch.setattr(bot, "_campo", lambda _s: _CampoFalsoParaTab())
        monkeypatch.setattr(bot, "_fechar_modal_erro_confirmado", lambda: True)
        monkeypatch.setattr(bot, "_preencher", lambda *_: None)
        monkeypatch.setattr(bot, "_ler_valor", lambda _s: "DENISE")

        tratou = bot._resolver_documento_duplicado(titulo, resultado, {titulo.documento})

        assert tratou is True
        assert resultado.documento_final == "RPA.1"


class TestResolverDocumentoDuplicado:
    def test_sem_duplicidade_nao_faz_nada(self, bot, monkeypatch):
        titulo = _titulo_simples()
        from ongsys.navegador import ResultadoInclusao
        resultado = ResultadoInclusao(documento_final=titulo.documento)

        monkeypatch.setattr(bot, "_mensagens_da_tela", lambda: "")
        tratou = bot._resolver_documento_duplicado(titulo, resultado, {titulo.documento})
        assert tratou is False
        assert resultado.documento_final == "RPA"
        assert resultado.pendencias == []

    def test_duplicidade_aplica_variacao_e_registra_pendencia(self, bot, monkeypatch):
        titulo = _titulo_simples()
        from ongsys.navegador import ResultadoInclusao
        resultado = ResultadoInclusao(documento_final=titulo.documento)
        documentos_tentados = {titulo.documento}

        monkeypatch.setattr(
            bot, "_mensagens_da_tela",
            lambda: "O número do documento fiscal já existe para esse fornecedor.",
        )
        monkeypatch.setattr(bot, "_fechar_popups", lambda: True)
        monkeypatch.setattr(bot, "_campo", lambda _s: _CampoFalsoParaTab())
        monkeypatch.setattr(bot, "_fechar_modal_erro_confirmado", lambda: True)
        preenchidos = []
        monkeypatch.setattr(bot, "_preencher", lambda seletor, valor: preenchidos.append((seletor, valor)))
        monkeypatch.setattr(bot, "_ler_valor", lambda _seletor: "DENISE")  # fornecedor não foi limpo

        tratou = bot._resolver_documento_duplicado(titulo, resultado, documentos_tentados)

        assert tratou is True
        assert resultado.documento_final == "RPA.1"
        # Variação do documento é informação, não correção de campo.
        assert resultado.pendencias == []
        assert "RPA" in resultado.avisos[0] and "RPA.1" in resultado.avisos[0]
        assert ("#field_numero_documento", "RPA.1") in preenchidos
        assert "RPA.1" in documentos_tentados

    def test_reseleciona_fornecedor_quando_o_campo_foi_limpo(self, bot, monkeypatch):
        """§11: o Ongsys costuma limpar o fornecedor nesse aviso."""
        titulo = _titulo_simples()
        from ongsys.navegador import ResultadoInclusao
        resultado = ResultadoInclusao(documento_final=titulo.documento)

        monkeypatch.setattr(
            bot, "_mensagens_da_tela", lambda: "Documento já existe para este fornecedor"
        )
        monkeypatch.setattr(bot, "_fechar_popups", lambda: True)
        monkeypatch.setattr(bot, "_campo", lambda _s: _CampoFalsoParaTab())
        monkeypatch.setattr(bot, "_fechar_modal_erro_confirmado", lambda: True)
        monkeypatch.setattr(bot, "_preencher", lambda *_: None)
        monkeypatch.setattr(bot, "_ler_valor", lambda _seletor: "")  # fornecedor foi limpo
        reselecionados = []
        monkeypatch.setattr(
            bot, "selecionar_fornecedor",
            lambda nome: reselecionados.append(nome) or nome,
        )

        bot._resolver_documento_duplicado(titulo, resultado, {titulo.documento})
        assert reselecionados == ["DENISE"]

    def test_nao_reseleciona_fornecedor_quando_o_campo_continua_preenchido(self, bot, monkeypatch):
        titulo = _titulo_simples()
        from ongsys.navegador import ResultadoInclusao
        resultado = ResultadoInclusao(documento_final=titulo.documento)

        monkeypatch.setattr(bot, "_mensagens_da_tela", lambda: "Documento já existe")
        monkeypatch.setattr(bot, "_fechar_popups", lambda: True)
        monkeypatch.setattr(bot, "_campo", lambda _s: _CampoFalsoParaTab())
        monkeypatch.setattr(bot, "_fechar_modal_erro_confirmado", lambda: True)
        monkeypatch.setattr(bot, "_preencher", lambda *_: None)
        monkeypatch.setattr(bot, "_ler_valor", lambda _seletor: "DENISE")
        chamado = []
        monkeypatch.setattr(bot, "selecionar_fornecedor", lambda nome: chamado.append(nome))

        bot._resolver_documento_duplicado(titulo, resultado, {titulo.documento})
        assert chamado == []

    def test_segunda_duplicidade_incrementa_a_variacao(self, bot, monkeypatch):
        """Se '.1' também já existir, precisa tentar '.2'."""
        titulo = _titulo_simples()
        from ongsys.navegador import ResultadoInclusao
        resultado = ResultadoInclusao(documento_final=titulo.documento)
        documentos_tentados = {"RPA", "RPA.1"}

        monkeypatch.setattr(bot, "_mensagens_da_tela", lambda: "Documento já existe")
        monkeypatch.setattr(bot, "_fechar_popups", lambda: True)
        monkeypatch.setattr(bot, "_campo", lambda _s: _CampoFalsoParaTab())
        monkeypatch.setattr(bot, "_fechar_modal_erro_confirmado", lambda: True)
        monkeypatch.setattr(bot, "_preencher", lambda *_: None)
        monkeypatch.setattr(bot, "_ler_valor", lambda _s: "DENISE")

        bot._resolver_documento_duplicado(titulo, resultado, documentos_tentados)
        assert resultado.documento_final == "RPA.2"


class TestCliqueGenericoResolveDuplicidadePendente:
    """Bug real do piloto: o popup de documento duplicado interceptou o

    clique na ABA "Rateio e Parcelamento" — um clique genérico, nada a ver
    com autocomplete. Fechar sem renomear faz o aviso reaparecer no próximo
    clique; `_clicar` (usado em qualquer botão/aba do formulário) precisa
    resolver de verdade, usando o contexto do título atual.
    """

    def test_clicar_generico_renomeia_o_documento_ao_ser_interceptado(self, bot, monkeypatch):
        titulo = _titulo_simples()
        from ongsys.navegador import ResultadoInclusao

        resultado = ResultadoInclusao(documento_final=titulo.documento)
        bot._duplicado_titulo = titulo
        bot._duplicado_resultado = resultado
        bot._duplicado_tentados = {titulo.documento}

        mensagens = iter([
            "O número do documento fiscal já existe para esse fornecedor.",
            "",  # depois de renomear, a segunda tentativa não acha mais o aviso
        ])
        monkeypatch.setattr(bot, "_mensagens_da_tela", lambda: next(mensagens, ""))
        monkeypatch.setattr(bot, "_fechar_popups", lambda **_: True)
        monkeypatch.setattr(bot, "_campo", lambda _s: _CampoFalsoParaTab())
        monkeypatch.setattr(bot, "_fechar_modal_erro_confirmado", lambda: True)
        preenchidos = []
        monkeypatch.setattr(bot, "_preencher", lambda seletor, valor: preenchidos.append((seletor, valor)))
        monkeypatch.setattr(bot, "_ler_valor", lambda _s: "DENISE")

        class AbaQueInterceptaUmaVez:
            def __init__(self):
                self.cliques = 0

            def click(self, timeout=None):
                self.cliques += 1
                if self.cliques == 1:
                    raise Exception("intercepts pointer events: #modalErro")

        aba = AbaQueInterceptaUmaVez()
        bot.page = type("PaginaMinima", (), {"wait_for_timeout": lambda *_a: None})()

        bot._clicar_resiliente(lambda: aba, "aba Rateio e Parcelamento")

        assert aba.cliques == 2
        assert resultado.documento_final == "RPA.1"
        assert ("#field_numero_documento", "RPA.1") in preenchidos


class TestHistoricoObrigatorioNoProjetado:
    """O Ongsys recusa o projetado sem historico; conferir ANTES de salvar."""

    class Pagina:
        def wait_for_timeout(self, _ms):
            pass

    def _bot(self, bot):
        bot.page = self.Pagina()
        return bot

    def test_historico_ja_preenchido_nao_reescreve(self, bot, monkeypatch):
        self._bot(bot)
        escritas: list = []
        monkeypatch.setattr(bot, "_ler_valor", lambda _s: "LIMPEZA")
        monkeypatch.setattr(bot, "_preencher", lambda s, v: escritas.append((s, v)))

        bot._garantir_historico_preenchido(_titulo_simples())

        assert escritas == []

    def test_historico_vazio_e_repreenchido(self, bot, monkeypatch):
        self._bot(bot)
        lidos = iter(["", "LIMPEZA", "LIMPEZA"])
        escritas: list = []
        monkeypatch.setattr(bot, "_ler_valor", lambda _s: next(lidos, "LIMPEZA"))
        monkeypatch.setattr(bot, "_preencher", lambda s, v: escritas.append((s, v)))

        bot._garantir_historico_preenchido(_titulo_simples())

        assert escritas and escritas[0][1] == "LIMPEZA"

    def test_historico_que_nao_grava_falha_antes_de_salvar(self, bot, monkeypatch):
        self._bot(bot)
        monkeypatch.setattr(bot, "_ler_valor", lambda _s: "")
        monkeypatch.setattr(bot, "_preencher", lambda _s, _v: None)

        with pytest.raises(ErroNavegador, match="Historico continuou vazio"):
            bot._garantir_historico_preenchido(_titulo_simples())

    def test_planilha_sem_historico_falha_sem_salvar(self, bot, monkeypatch):
        self._bot(bot)
        monkeypatch.setattr(bot, "_ler_valor", lambda _s: "")
        titulo = _titulo_simples()
        titulo.historico = "   "

        with pytest.raises(ErroNavegador, match="nao forneceu historico"):
            bot._garantir_historico_preenchido(titulo)


class TestCliqueAutocompleteViaJavaScript:
    """NORTE VIDA e LOJAX: o clique expirava com algo invisivel por cima."""

    class _Opcao:
        def __init__(self, ao_clicar):
            self.ao_clicar = ao_clicar
            self.js = []

        def click(self, **_kw):
            raise TimeoutError("Locator.click: Timeout 2000ms exceeded.")

        def evaluate(self, script):
            self.js.append(script)
            self.ao_clicar()

    class _Pagina:
        def __init__(self, opcao):
            self.opcao = opcao

        @property
        def first(self):
            return self

        def is_enabled(self):
            return True

        def locator(self, _seletor):
            return self

        def nth(self, _indice):
            return self.opcao

        def wait_for_timeout(self, _ms):
            pass

    def _montar(self, bot, monkeypatch, valor_apos_js):
        estado = {"valor": ""}

        def _js_clicou():
            estado["valor"] = valor_apos_js

        opcao = self._Opcao(_js_clicou)
        bot.page = self._Pagina(opcao)
        monkeypatch.setattr(
            bot, "_candidatos_autocomplete", lambda *_a: ["3.3.1.01 - Assessoria de Imprensa"]
        )
        monkeypatch.setattr(bot, "_ler_valor", lambda _s: estado["valor"])
        return opcao

    def test_clique_expirado_e_recuperado_por_javascript(self, bot, monkeypatch):
        opcao = self._montar(
            bot, monkeypatch, valor_apos_js="3.3.1.01 - Assessoria de Imprensa"
        )

        escolhido = bot._buscar_e_clicar_por_nome(
            "#field_categoria_conta_1", "Assessoria de Imprensa", Decimal("0.75")
        )

        assert escolhido == "3.3.1.01 - Assessoria de Imprensa"
        assert opcao.js == ["el => el.click()"], "o clique via JavaScript deve ser tentado"

    def test_javascript_que_nao_seleciona_nao_e_aceito(self, bot, monkeypatch):
        """Se o campo nao ficar com o valor exato, nada e aceito."""
        opcao = self._montar(bot, monkeypatch, valor_apos_js="OUTRA CATEGORIA")

        assert bot._buscar_e_clicar_por_nome(
            "#field_categoria_conta_1", "Assessoria de Imprensa", Decimal("0.75")
        ) is None
        assert opcao.js == ["el => el.click()"]


class TestSincronizarLinhasRateioDoProjetado:
    """LEANDRO NF 48: a linha 2 do rateio expirava na conversao de projetado."""

    def _preparar(self, bot, monkeypatch, linhas_no_projetado):
        eventos: list = []

        class Campo:
            def __init__(self, existe):
                self.existe = existe

            def count(self):
                return 1 if self.existe else 0

        def _campo(seletor):
            # "#field_categoria_conta_N" -> N
            numero = int(seletor.rsplit("_", 1)[-1])
            return Campo(numero <= linhas_no_projetado)

        monkeypatch.setattr(bot, "_campo", _campo)
        monkeypatch.setattr(
            bot, "_incluir_linha_rateio",
            lambda anterior, _rateio=None: eventos.append(f"criar_linha_{anterior + 1}") or [],
        )
        monkeypatch.setattr(bot, "_habilitar_campos_formulario", lambda: None)
        monkeypatch.setattr(
            bot, "_preencher_rateio",
            lambda indice, _r: eventos.append(f"preencher_{indice}") or [],
        )
        return eventos

    def test_cria_cada_linha_imediatamente_antes_de_preencher(self, bot, monkeypatch):
        eventos = self._preparar(bot, monkeypatch, linhas_no_projetado=1)
        titulo = _titulo_simples()
        titulo.rateios = [titulo.rateios[0]] * 3

        bot._sincronizar_linhas_rateio(titulo, ResultadoInclusao())

        # A linha 2 so e criada depois que a 1 foi preenchida (o Ongsys clona
        # a linha anterior); o mesmo vale para a 3.
        assert eventos == [
            "preencher_1",
            "criar_linha_2", "preencher_2",
            "criar_linha_3", "preencher_3",
        ]

    def test_reutiliza_linhas_existentes_sem_criar(self, bot, monkeypatch):
        eventos = self._preparar(bot, monkeypatch, linhas_no_projetado=2)
        titulo = _titulo_simples()
        titulo.rateios = [titulo.rateios[0]] * 2

        bot._sincronizar_linhas_rateio(titulo, ResultadoInclusao())

        assert eventos == ["preencher_1", "preencher_2"]

    def test_projetado_com_linhas_a_mais_bloqueia(self, bot, monkeypatch):
        self._preparar(bot, monkeypatch, linhas_no_projetado=3)
        titulo = _titulo_simples()
        titulo.rateios = [titulo.rateios[0]]

        with pytest.raises(ErroNavegador, match="ajuste manual"):
            bot._sincronizar_linhas_rateio(titulo, ResultadoInclusao())


class TestAtividadeNuncaDerrubaOTitulo:
    """LEANDRO NF 48: o campo Atividade travado matava o titulo inteiro.

    O `except ErroNavegador` nao pegava o TimeoutError do Playwright, entao a
    excecao escapava de _preencher_rateio. A regra §14 diz que atividade
    ausente vira pendencia, nunca falha.
    """

    def _rateio_com_atividade(self):
        return Rateio(4, Decimal("100.00"), "GESTAO ADM",
                      "SERVICOS_TOMADOS_DE_TERCEIROS", "LIMPEZA", "", "SEDE",
                      Decimal("100"))

    def _preparar(self, bot, monkeypatch, erro):
        limpezas: list = []

        def _selecionar(seletor, esperado, descricao, tentativas=5, **_kwargs):
            if "atividade" in seletor:
                raise erro
            return esperado

        def _preencher(seletor, valor):
            if "atividade" in seletor:
                limpezas.append(valor)
                if isinstance(erro, TimeoutError):
                    raise TimeoutError("Locator.fill: Timeout 15000ms exceeded.")

        monkeypatch.setattr(bot, "_selecionar_por_nome", _selecionar)
        monkeypatch.setattr(bot, "_preencher", _preencher)
        monkeypatch.setattr(bot, "_selecionar_subprojeto", lambda *_a: ("", None))
        monkeypatch.setattr(bot, "_preencher_dinheiro", lambda *_a: None)
        monkeypatch.setattr(bot, "_preencher_ou_validar_percentual_rateio", lambda *_a: None)
        monkeypatch.setattr(bot, "_garantir_campos_obrigatorios_rateio", lambda *_a: None)
        monkeypatch.setattr(bot, "_ler_valor", lambda _s: "100,00")
        return limpezas

    def test_timeout_do_playwright_vira_pendencia(self, bot, monkeypatch):
        erro = TimeoutError("Locator.fill: Timeout 15000ms exceeded.")
        self._preparar(bot, monkeypatch, erro)

        pendencias = bot._preencher_rateio(2, self._rateio_com_atividade())

        assert any("atividade" in p and "SEDE" in p for p in pendencias)

    def test_erro_navegador_continua_virando_pendencia(self, bot, monkeypatch):
        self._preparar(bot, monkeypatch, ErroNavegador("nao localizado"))

        pendencias = bot._preencher_rateio(2, self._rateio_com_atividade())

        assert any("atividade" in p for p in pendencias)

    def test_erro_fatal_nao_e_engolido(self, bot, monkeypatch):
        from ongsys.navegador import ErroFatal

        self._preparar(bot, monkeypatch, ErroFatal("sessao caiu"))

        with pytest.raises(ErroFatal):
            bot._preencher_rateio(2, self._rateio_com_atividade())


class TestConferenciaFinalDosRateios:
    """Regressões da execução teste7: campo vazio nunca pode chegar ao Salvar."""

    def _valores_corretos(self, titulo):
        return {
            S.CAMPO_VALOR: "300,00",
            S.categoria_conta(1): "2.5.1.04.007 - LIMPEZA (A)",
            S.projeto_cc(1): "GESTAO ADM",
            S.valor_rateio(1): "300,00",
            S.percentual_rateio(1): "100,00",
        }

    def test_categoria_vazia_bloqueia_antes_de_salvar(self, bot, monkeypatch):
        titulo = _titulo_simples()
        valores = self._valores_corretos(titulo)
        valores[S.categoria_conta(1)] = ""
        monkeypatch.setattr(bot, "_ler_valor", lambda seletor: valores.get(seletor, ""))
        monkeypatch.setattr(bot, "_linha_rateio_visivel", lambda _indice: False)

        with pytest.raises(ErroNavegador, match="Categoria/Conta obrigatória"):
            bot._conferir_totais_na_tela(titulo)

    def test_projeto_vazio_bloqueia_antes_de_salvar(self, bot, monkeypatch):
        titulo = _titulo_simples()
        valores = self._valores_corretos(titulo)
        valores[S.projeto_cc(1)] = ""
        monkeypatch.setattr(bot, "_ler_valor", lambda seletor: valores.get(seletor, ""))
        monkeypatch.setattr(bot, "_linha_rateio_visivel", lambda _indice: False)

        with pytest.raises(ErroNavegador, match="Projeto/Centro de Custos obrigatório"):
            bot._conferir_totais_na_tela(titulo)

    def test_percentual_visivel_incompleto_bloqueia(self, bot, monkeypatch):
        titulo = _titulo_simples()
        valores = self._valores_corretos(titulo)
        valores[S.percentual_rateio(1)] = "96,62"
        monkeypatch.setattr(bot, "_ler_valor", lambda seletor: valores.get(seletor, ""))
        monkeypatch.setattr(bot, "_linha_rateio_visivel", lambda _indice: False)

        with pytest.raises(ErroNavegador, match="percentuais visíveis"):
            bot._conferir_totais_na_tela(titulo)

    def test_linha_extra_bloqueia(self, bot, monkeypatch):
        titulo = _titulo_simples()
        valores = self._valores_corretos(titulo)
        monkeypatch.setattr(bot, "_ler_valor", lambda seletor: valores.get(seletor, ""))
        monkeypatch.setattr(bot, "_linha_rateio_visivel", lambda indice: indice == 2)

        with pytest.raises(ErroNavegador, match="mais linhas de rateio"):
            bot._conferir_totais_na_tela(titulo)

    def test_rateio_completo_e_exato_e_aceito(self, bot, monkeypatch):
        titulo = _titulo_simples()
        valores = self._valores_corretos(titulo)
        monkeypatch.setattr(bot, "_ler_valor", lambda seletor: valores.get(seletor, ""))
        monkeypatch.setattr(bot, "_linha_rateio_visivel", lambda _indice: False)

        bot._conferir_totais_na_tela(titulo)
