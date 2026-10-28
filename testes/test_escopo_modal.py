"""Testes do escopo de campo ao modal "Inclusão de Contas a Pagar".

Relatado em guia de operação real contra o Ongsys: a página tem campos com o
mesmo ID escondidos em outros modais/formulários. Uma busca sem escopo
(`page.locator("#field_valor_despesa")`) pode atingir o elemento errado,
escondido, sem erro nenhum — explicação mais provável do bug em que o campo
de Valor sempre lia "0,00" de volta, não importa o que fosse digitado.
"""

from __future__ import annotations

import apoio
import pytest
from apoio import config_de_teste

from ongsys.navegador import OngsysBot


class _CampoFalso:
    def __init__(self, origem: str):
        self.origem = origem  # de onde este campo "veio" (página ou modal)


class _LocatorFalso:
    def __init__(self, origem: str):
        self._origem = origem

    @property
    def first(self):
        return _CampoFalso(self._origem)

    def count(self):
        return 1


class _ModalFalso:
    """Simula o Locator do modal "Inclusão de Contas a Pagar" já filtrado."""

    def locator(self, _seletor):
        return _LocatorFalso("modal")


class _PaginaFalsa:
    def locator(self, _seletor):
        return _LocatorFalso("pagina")


@pytest.fixture
def bot(tmp_path):
    return OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")


class TestEscopoDoCampo:
    def test_sem_modal_ativo_busca_na_pagina_inteira(self, bot):
        """Antes do formulário abrir (ou se não achar o modal), o
        comportamento é o de sempre: busca na página inteira."""
        bot.page = _PaginaFalsa()
        bot._modal_inclusao = None
        campo = bot._campo("#field_valor_despesa").first
        assert campo.origem == "pagina"

    def test_com_modal_ativo_busca_fica_restrita_a_ele(self, bot):
        """Bug real: com o modal identificado, a busca não pode mais cair
        num campo de mesmo ID escondido em outro lugar da página."""
        bot.page = _PaginaFalsa()
        bot._modal_inclusao = _ModalFalso()
        campo = bot._campo("#field_valor_despesa").first
        assert campo.origem == "modal"

    def test_modal_e_zerado_a_cada_novo_titulo(self, bot, monkeypatch):
        """Reaproveitar o bot entre títulos não pode arrastar o escopo do
        modal do título anterior, já fechado."""
        from datetime import date
        from decimal import Decimal

        from ongsys.modelos import Rateio, Titulo

        bot._modal_inclusao = _ModalFalso()  # escopo "esquecido" de uma rodada anterior

        titulo = Titulo(
            chave="k", linhas_origem=[1], fornecedor="F", documento="D",
            documento_original="D", emissao=date(2026, 1, 1), vencimento=date(2026, 1, 1),
            efetivacao=date(2026, 1, 1), valor=Decimal("10.00"), conta_pagadora_id="6",
            conta_pagadora_nome="X", tipo_despesa="Outros", tipo_documento="Outros",
            meio_pagamento="Internet Banking", tipo_pagamento="Transferência Eletrônica",
            historico="h", rateios=[Rateio(1, Decimal("10.00"), "CC", "T", "C", "", "", Decimal("100"))],
        )

        capturado = {}

        def parar_logo(*_a, **_k):
            # Captura o estado do escopo assim que incluir() o reseta, e
            # interrompe a execução antes de precisar de um Playwright real.
            capturado["modal_no_inicio"] = bot._modal_inclusao
            raise RuntimeError("parar aqui de propósito")

        bot.page = type("PaginaMinima", (), {
            "goto": lambda *_a, **_k: None,
        })()
        monkeypatch.setattr(bot, "_clicar", parar_logo)

        with pytest.raises(RuntimeError, match="parar aqui de proposito|parar aqui de propósito"):
            bot.incluir(titulo)

        assert capturado["modal_no_inicio"] is None
