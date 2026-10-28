"""Texto apenas digitado num autocomplete não é seleção (BENEFIX, 11/09/2026).

A planilha traz "GESTAO ADM" e o Ongsys só encontra "GESTÃO ADM". A primeira
busca não abria lista e deixava "GESTAO ADM" no campo; a passada seguinte lia
esse texto, achava igual ao esperado e encerrava a busca sem clicar em opção
nenhuma. O Ongsys depois apagava o campo e recusava criar a linha 2.
"""

from types import SimpleNamespace

from apoio import config_de_teste

from ongsys import seletores as S
from ongsys.navegador import OngsysBot

SELETOR_CC = S.projeto_cc(1)
SELETOR_SUB = S.subprojeto(1)


class _Campo:
    def __init__(self):
        self.valor = ""
        self.habilitado = True

    @property
    def first(self):
        return self

    def is_enabled(self):
        return self.habilitado

    def input_value(self):
        return self.valor

    def fill(self, texto):
        self.valor = texto

    def type(self, texto, delay=0):
        self.valor += texto


class _Pagina:
    """Typeahead que só abre lista para as consultas cadastradas.

    Clicar uma opção faz o que o Ongsys real faz: grava o texto da opção e
    trava o campo.
    """

    def __init__(self, campo, respostas):
        self.campo = campo
        self.respostas = respostas
        self.opcoes: list[str] = []
        self.cliques: list[str] = []

    def wait_for_timeout(self, _ms):
        pass

    def wait_for_selector(self, _seletor, timeout=0):
        self.opcoes = list(self.respostas.get(self.campo.valor, []))
        if not self.opcoes:
            raise TimeoutError("nenhuma lista")

    def locator(self, _seletor):
        return self

    def count(self):
        return len(self.opcoes)

    def nth(self, indice):
        pagina = self
        texto = self.opcoes[indice]

        class _Opcao:
            def inner_text(self):
                return texto

            def click(self, **_kw):
                pagina.cliques.append(texto)
                pagina.campo.valor = texto
                pagina.campo.habilitado = False

        return _Opcao()


def _bot(tmp_path, campo, respostas):
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")
    bot.page = _Pagina(campo, respostas)
    bot._campo = lambda _seletor: campo
    return bot


def test_cc_sem_acento_clica_a_opcao_em_vez_de_aceitar_o_texto_digitado(tmp_path):
    campo = _Campo()
    bot = _bot(tmp_path, campo, {"ADM": ["GESTÃO ADM", "ADM ESCOLAS"]})

    escolhido = bot._selecionar_por_nome(
        SELETOR_CC, "GESTAO ADM", "Linha 1: Projeto/Centro de Custos",
        aceitar_prefixo=True,
    )

    assert escolhido == "GESTÃO ADM"
    assert bot.page.cliques == ["GESTÃO ADM"]
    assert campo.habilitado is False


def test_busca_sem_lista_nao_deixa_texto_no_campo(tmp_path):
    campo = _Campo()
    bot = _bot(tmp_path, campo, {})

    assert bot._candidatos_autocomplete(SELETOR_CC, "GESTAO ADM") == []
    assert campo.valor == ""


def test_texto_digitado_editavel_nao_passa_na_conferencia(tmp_path):
    campo = _Campo()
    bot = _bot(tmp_path, campo, {})
    bot._registrar_digitacao(SELETOR_CC, "GESTAO ADM")
    campo.valor = "GESTAO ADM"

    assert not bot._selecao_confere(
        SELETOR_CC, campo.valor, "GESTAO ADM", aceitar_prefixo=True
    )


def test_selecao_travada_com_mesmo_texto_continua_valida(tmp_path):
    """Opção cujo texto é igual ao digitado vale quando o Ongsys trava o campo."""
    campo = _Campo()
    bot = _bot(tmp_path, campo, {})
    bot._registrar_digitacao(SELETOR_CC, "GESTAO ADM")
    campo.valor = "GESTAO ADM"
    campo.habilitado = False

    assert bot._selecao_confere(
        SELETOR_CC, campo.valor, "GESTAO ADM", aceitar_prefixo=True
    )


SELETOR_CATEGORIA = S.categoria_conta(4)
OPCAO_TELEFONE = "2.5.1.08.004 - TELEFONE, INTERNET E CELULAR (A)"


def test_categoria_escolhida_por_semelhanca_nao_entra_em_ciclo(tmp_path):
    """BENEFIX linha 4: a opção clicada era rejeitada e o lápis era acionado."""
    campo = _Campo()
    bot = _bot(tmp_path, campo, {"TELEFONE, INTERNET": [OPCAO_TELEFONE]})

    escolhido = bot._selecionar_por_nome(
        SELETOR_CATEGORIA, "TELEFONE, INTERNET", "Linha 4: Categoria/Conta"
    )

    assert escolhido == OPCAO_TELEFONE
    assert bot.page.cliques == [OPCAO_TELEFONE]
    # A conferência da linha e a de antes de Salvar usam o nome da planilha.
    assert bot._selecao_confere(SELETOR_CATEGORIA, campo.valor, "TELEFONE, INTERNET")


def test_opcao_aceita_nao_vale_se_o_campo_ficou_com_outra_coisa(tmp_path):
    campo = _Campo()
    bot = _bot(tmp_path, campo, {})
    bot._registrar_opcao_aceita(SELETOR_CATEGORIA, OPCAO_TELEFONE)
    campo.valor = "2.5.1.08.001 - ENERGIA ELETRICA (A)"
    campo.habilitado = False

    assert not bot._selecao_confere(
        SELETOR_CATEGORIA, campo.valor, "TELEFONE, INTERNET"
    )


def _rateio_linha_4():
    from decimal import Decimal

    from ongsys.modelos import Rateio

    return Rateio(
        linha=18, valor=Decimal("1050.00"), centro_custo="GESTAO ADM",
        categoria_titulo="", categoria_detalhada="TELEFONE, INTERNET",
        subprojeto="2.1.3.10", atividade="", percentual=Decimal("0.9167"),
    )


def _bot_linha_sem_navegador(tmp_path, monkeypatch, subprojeto):
    campo = _Campo()
    bot = _bot(tmp_path, campo, {})
    escolhas = {
        S.categoria_conta(4): OPCAO_TELEFONE,
        S.projeto_cc(4): "10.2.01.01 - GESTÃO ADM",
    }
    monkeypatch.setattr(
        bot, "_selecionar_por_nome", lambda seletor, *_a, **_k: escolhas[seletor]
    )
    monkeypatch.setattr(bot, "_selecionar_subprojeto", subprojeto)
    monkeypatch.setattr(
        bot, "_ler_valor", lambda s: "" if s == S.subprojeto(4) else "1.050,00"
    )
    monkeypatch.setattr(bot, "_preencher_ou_validar_percentual_rateio", lambda *_a: None)
    monkeypatch.setattr(bot, "_garantir_campos_obrigatorios_rateio", lambda *_a: None)
    return bot


def test_erro_no_subprojeto_nao_derruba_a_linha(tmp_path, monkeypatch):
    def subprojeto_quebrado(*_args):
        raise TimeoutError("Locator.fill: Timeout 15000ms exceeded.")

    bot = _bot_linha_sem_navegador(tmp_path, monkeypatch, subprojeto_quebrado)

    pendencias = bot._preencher_rateio(4, _rateio_linha_4())

    assert any("Subprojeto 2.1.3.10" in p and "vazio" in p for p in pendencias)


def test_categoria_por_semelhanca_vira_pendencia_para_conferir(tmp_path, monkeypatch):
    bot = _bot_linha_sem_navegador(
        tmp_path, monkeypatch, lambda *_a: ("2.1.3.10 - TELEFONE", None)
    )

    pendencias = bot._preencher_rateio(4, _rateio_linha_4())

    assert pendencias == [
        "Linha 4: Categoria/Conta 'TELEFONE, INTERNET' não tem nome idêntico no "
        f"Ongsys; usada a opção '{OPCAO_TELEFONE}'. Confira e corrija "
        "manualmente se não for a correta."
    ]


def test_subprojeto_sem_confirmacao_vira_pendencia(tmp_path):
    campo = _Campo()

    class _PaginaSemEfeito(_Pagina):
        def nth(self, indice):
            opcao = super().nth(indice)
            opcao.click = lambda **_kw: None  # o Ongsys ignora o clique
            return opcao

    bot = _bot(tmp_path, campo, {})
    bot.page = _PaginaSemEfeito(campo, {"2.1.3.10": ["21 - 2.1.3.10 - Telefone"]})

    escolhido, pendencia = bot._selecionar_subprojeto(4, "2.1.3.10")

    assert escolhido == ""
    assert "não se confirmou" in pendencia
    assert campo.valor == ""


def test_subprojeto_preso_com_outro_codigo_segue_com_aviso(tmp_path):
    campo = _Campo()
    campo.valor = "22 - 2.1.3.11 - Outro"
    campo.habilitado = False
    bot = _bot(tmp_path, campo, {})

    aviso = bot._subprojeto_preso_com_outro_codigo(4, "2.1.3.10")

    assert aviso.startswith("Linha 4: CORRIGIR Subprojeto")
    assert "2.1.3.11" in aviso and "2.1.3.10" in aviso


OPCAO_REAL_CAJU = "2.5.1.08.004 - TELEFONE INTERNET (A)"


def test_virgula_nao_e_diferenca_de_nome():
    from ongsys.normalizacao import chave_comparacao_nome

    assert chave_comparacao_nome("TELEFONE, INTERNET") == chave_comparacao_nome(
        "TELEFONE INTERNET"
    )
    # Pontos continuam distinguindo códigos.
    assert chave_comparacao_nome("2.1.3.07") != chave_comparacao_nome("21.3.07")


def test_opcao_sem_virgula_e_selecao_exata_sem_pendencia(tmp_path, monkeypatch):
    """Rodada real das 16:38: 7 pendências só por causa da vírgula."""
    campo = _Campo()
    bot = _bot(tmp_path, campo, {"TELEFONE, INTERNET": [OPCAO_REAL_CAJU]})

    escolhido = bot._selecionar_por_nome(
        SELETOR_CATEGORIA, "TELEFONE, INTERNET", "Linha 4: Categoria/Conta"
    )
    pendencias: list[str] = []
    bot._anotar_escolha_por_semelhanca(
        pendencias, 4, "Categoria/Conta", "TELEFONE, INTERNET", escolhido
    )

    assert escolhido == OPCAO_REAL_CAJU
    assert pendencias == []
    assert getattr(bot, "_opcoes_aceitas", {}) == {}


def test_auditoria_ignora_virgula_na_categoria():
    from ongsys.auditoria import comparar_texto

    assert comparar_texto(
        "Linha 4: Categoria/Conta", "TELEFONE, INTERNET", OPCAO_REAL_CAJU, ""
    ) is None
    assert comparar_texto(
        "Linha 4: Categoria/Conta", "TELEFONE, INTERNET",
        "2.5.1.08.001 - ENERGIA ELETRICA (A)", "",
    ) is not None


def test_avisos_aparecem_separados_das_pendencias(capsys):
    from ongsys import cli

    cli._mostrar_pendencias(
        [], ["Tipo de pagamento do cadastro do fornecedor: 'Boleto Simples'."]
    )
    saida = capsys.readouterr().out

    assert "info: Tipo de pagamento" in saida
    assert "ATENCAO" not in saida


def test_aviso_de_duplicado_tardio_e_resolvido_entre_tentativas(tmp_path, monkeypatch):
    """IVONE/TATIANA 18/09: "documento já existe" cobria a tela no rateio."""
    campo = _Campo()
    opcao = "2.5.1.05.005 - CONDUCAO /LOCOMOÇÃO (A)"

    class _PaginaComAviso(_Pagina):
        aviso_na_tela = True

        def nth(self, indice):
            opcao_real = super().nth(indice)
            clicar = opcao_real.click
            pagina = self

            def click(**kw):
                if pagina.aviso_na_tela:
                    raise TimeoutError("Locator.click: Timeout 5000ms exceeded.")
                clicar(**kw)

            opcao_real.click = click
            opcao_real.dispatch_event = lambda _e: None
            opcao_real.evaluate = lambda _s: None
            return opcao_real

    bot = _bot(tmp_path, campo, {})
    bot.page = _PaginaComAviso(campo, {"CONDUCAO /LOCOMOÇÃO": [opcao]})
    resolvidos = []

    def resolver():
        resolvidos.append(True)
        bot.page.aviso_na_tela = False
        return True

    monkeypatch.setattr(bot, "_resolver_duplicado_pendente", resolver)

    escolhido = bot._selecionar_por_nome(
        S.categoria_conta(1), "CONDUCAO /LOCOMOÇÃO", "Linha 1: Categoria/Conta"
    )

    assert escolhido == opcao
    assert resolvidos == [True]


def test_codigo_de_barras_com_hifen_nao_e_divergencia():
    from ongsys.auditoria import comparar_codigo_barras

    assert comparar_codigo_barras(
        "3419109008065444187235167863000851576000010335-9",
        "34191090080654441872351678630008515760000103359",
    ) is None
    assert comparar_codigo_barras("34191-9", "34191-8") is not None


def _titulo_nice():
    from datetime import date
    from decimal import Decimal

    from ongsys.modelos import Titulo

    return Titulo(
        chave="k", linhas_origem=[31], fornecedor="IVONE FERRAZ ANDRADE SERVICOS EDUCACIONAIS",
        documento="09_26", documento_original="09_26", emissao=date(2026, 9, 22),
        vencimento=date(2026, 9, 22), efetivacao=date(2026, 9, 22), valor=Decimal("517.69"),
        conta_pagadora_id="6", conta_pagadora_nome="BANCO ITAU 12345-6 SEDE",
        tipo_despesa="Outros", tipo_documento="Outros", meio_pagamento="Internet Banking",
        tipo_pagamento="Transferência Eletrônica", historico="CONDUCAO /LOCOMOÇÃO",
        rateios=[],
    )


def test_documentos_ja_usados_no_ano_levam_ao_primeiro_numero_livre(tmp_path, monkeypatch):
    """IVONE/TATIANA/ORBITA 18/09: 09_26 e 09_26.1 já existiam em outras datas."""
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")
    linhas = {
        "1": "00033001 PRINC 09_26 IVONE FERRAZ ANDRADE SERVICOS EDUCACIONAIS - 39.359 REEMBOLSO 1-Set-2026 8-Set-2026 120,00",
        "2": "00033002 PRINC 09_26.1 IVONE FERRAZ ANDRADE SERVICOS EDUCACIONAIS - 39.359 REEMBOLSO 9-Set-2026 15-Set-2026 80,00",
        "3": "00033003 PRINC 09_26.4 TATIANA CARDOSO DOS PINTO RIBEIRO - 60.741 REEMBOLSO 9-Set-2026 15-Set-2026 50,00",
        "4": "00033004 PRINC 09_261 IVONE FERRAZ ANDRADE SERVICOS EDUCACIONAIS REEMBOLSO 70,00",
    }
    periodos = []

    def buscar(documento, inicio, fim):
        periodos.append((documento, inicio, fim))
        return list(linhas)

    monkeypatch.setattr(bot, "ids_resultado_busca_documento", buscar)
    monkeypatch.setattr(bot, "texto_linha_para_id", lambda i: linhas[i])
    titulo = _titulo_nice()

    usados = bot.documentos_ja_usados(titulo)

    assert usados == {"09_26", "09_26.1"}  # TATIANA e "09_261" não contam
    assert periodos[0][1].isoformat() == "2026-01-01"
    assert periodos[0][2].isoformat() == "2026-12-31"
    assert bot.proximo_documento(titulo, usados) == "09_26.2"


class _ModalFalso:
    def __init__(self):
        self.visivel = True

    def is_visible(self):
        return self.visivel


def _bot_salvando(tmp_path, monkeypatch, verde_apos_ms=None, fecha_apos_ms=None):
    """Simula o Ongsys processando o Salvar por alguns segundos."""
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")
    relogio = {"ms": 0}
    modal = _ModalFalso()

    class _Pagina:
        def wait_for_timeout(self, ms):
            relogio["ms"] += ms
            if fecha_apos_ms is not None and relogio["ms"] >= fecha_apos_ms:
                modal.visivel = False

        def locator(self, _seletor):
            # Campo Fornecedor escondido: estamos na aba de rateio.
            return SimpleNamespace(count=lambda: 0)

    bot.page = _Pagina()
    bot._modal_inclusao = modal
    monkeypatch.setattr(
        bot, "_inclusao_confirmada",
        lambda: verde_apos_ms is not None and relogio["ms"] >= verde_apos_ms,
    )
    monkeypatch.setattr(bot, "_texto_modal_erro", lambda: "")
    return bot, relogio


def test_formulario_na_aba_de_rateio_nao_e_dado_como_fechado(tmp_path, monkeypatch):
    bot, _ = _bot_salvando(tmp_path, monkeypatch)
    assert bot._formulario_fechou() is False


def test_salvar_lento_espera_a_confirmacao_verde(tmp_path, monkeypatch):
    """ORBITA/TATIANA 18/09: o Ongsys levou mais de 3 s; o robô desistia."""
    bot, relogio = _bot_salvando(tmp_path, monkeypatch, verde_apos_ms=9_000)
    assert bot._aguardar_resposta_do_salvar() is True
    assert relogio["ms"] >= 9_000


def test_formulario_que_fecha_sem_verde_termina_sem_esperar_o_limite(tmp_path, monkeypatch):
    bot, relogio = _bot_salvando(tmp_path, monkeypatch, fecha_apos_ms=4_000)
    assert bot._aguardar_resposta_do_salvar() is False
    assert relogio["ms"] < 10_000


def test_subprojeto_digitado_sem_lista_vira_pendencia_e_nao_selecao(tmp_path):
    campo = _Campo()
    bot = _bot(tmp_path, campo, {})

    escolhido, pendencia = bot._selecionar_subprojeto(1, "2.1.3.07")

    assert escolhido == ""
    assert "2.1.3.07" in pendencia
    assert campo.valor == ""
