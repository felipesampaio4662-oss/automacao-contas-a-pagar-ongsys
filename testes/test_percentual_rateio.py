"""Regressão do percentual automático e desabilitado no rateio do Ongsys."""

from __future__ import annotations

from decimal import Decimal

import apoio  # noqa: F401  (ajusta o sys.path)
import pytest
from apoio import config_de_teste

from ongsys.navegador import ErroNavegador, OngsysBot


class _CampoPercentual:
    def __init__(self, valor: str, *, habilitado: bool):
        self.valor = valor
        self.habilitado = habilitado
        self.preenchimentos: list[str] = []

    def is_enabled(self):
        return self.habilitado

    def is_editable(self):
        return self.habilitado

    def fill(self, texto):
        if not self.habilitado:
            raise TimeoutError("elemento não está habilitado")
        self.preenchimentos.append(texto)
        self.valor = texto

    def input_value(self):
        return self.valor


class _Locator:
    def __init__(self, campo):
        self.first = campo


@pytest.fixture
def bot(tmp_path):
    return OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")


def _usar_campo(bot, monkeypatch, campo):
    monkeypatch.setattr(bot, "_campo", lambda _seletor: _Locator(campo))


class TestPercentualAutomaticoDoRateio:
    def test_campo_desabilitado_com_100_por_cento_e_aceito(self, bot, monkeypatch):
        campo = _CampoPercentual("100,00 %", habilitado=False)
        _usar_campo(bot, monkeypatch, campo)

        bot._preencher_ou_validar_percentual_rateio(1, Decimal("100.0000"))

        assert campo.preenchimentos == []

    def test_campo_desabilitado_com_valor_errado_interrompe_antes_de_salvar(
        self, bot, monkeypatch
    ):
        campo = _CampoPercentual("50,00 %", habilitado=False)
        _usar_campo(bot, monkeypatch, campo)

        with pytest.raises(ErroNavegador) as erro:
            bot._preencher_ou_validar_percentual_rateio(1, Decimal("100.0000"))

        assert "50,00 %" in str(erro.value)
        assert "NÃO foi salvo" in str(erro.value)

    def test_caso_real_caju_043_para_04350_e_aceito(self, bot, monkeypatch):
        campo = _CampoPercentual("0,43 %", habilitado=False)
        _usar_campo(bot, monkeypatch, campo)

        bot._preencher_ou_validar_percentual_rateio(12, Decimal("0.4350"))

        assert campo.preenchimentos == []

    def test_diferenca_maior_que_uma_casa_visual_continua_bloqueada(
        self, bot, monkeypatch
    ):
        campo = _CampoPercentual("0,42 %", habilitado=False)
        _usar_campo(bot, monkeypatch, campo)

        with pytest.raises(ErroNavegador) as erro:
            bot._preencher_ou_validar_percentual_rateio(12, Decimal("0.4350"))

        assert "0,42 %" in str(erro.value)
        assert "NÃO foi salvo" in str(erro.value)

    def test_campo_editavel_continua_sendo_preenchido_e_conferido(self, bot, monkeypatch):
        campo = _CampoPercentual("", habilitado=True)
        _usar_campo(bot, monkeypatch, campo)

        bot._preencher_ou_validar_percentual_rateio(1, Decimal("40.2500"))

        assert campo.preenchimentos == ["", "40,2500"]

    def test_campo_que_e_desabilitado_durante_o_fill_e_revalidado(self, bot, monkeypatch):
        class CampoQueViraAutomatico(_CampoPercentual):
            def fill(self, _texto):
                self.habilitado = False
                self.valor = "100,00 %"
                raise TimeoutError("o Ongsys desabilitou o campo durante o preenchimento")

        campo = CampoQueViraAutomatico("", habilitado=True)
        _usar_campo(bot, monkeypatch, campo)

        bot._preencher_ou_validar_percentual_rateio(1, Decimal("100.0000"))

    def test_campo_editavel_que_nao_grava_o_esperado_e_rejeitado(self, bot, monkeypatch):
        class CampoQueDistorce(_CampoPercentual):
            def fill(self, texto):
                super().fill(texto)
                if texto:
                    self.valor = "99,00 %"

        campo = CampoQueDistorce("", habilitado=True)
        _usar_campo(bot, monkeypatch, campo)

        with pytest.raises(ErroNavegador) as erro:
            bot._preencher_ou_validar_percentual_rateio(1, Decimal("40.2500"))

        assert "99,00 %" in str(erro.value)
        assert "NÃO foi salvo" in str(erro.value)
