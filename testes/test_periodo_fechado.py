"""Recusa "PERÍODO FECHADO!" ao salvar (BELLE CARIOCA, 2026-10-15).

Emissão 30/07/2026 em mês fechado: o Ongsys mostra "Erro! Ocorreu um erro ao
tentar executar essa ação... PERÍODO FECHADO!" e o formulário continua aberto.
Nada foi gravado, então o título tem de virar FALHA (pode ser lançado depois de
corrigir a data ou reabrir o período), e não EM_EXECUCAO, que o bloqueia como
se pudesse existir no Ongsys.
"""

from __future__ import annotations

from datetime import date

import apoio  # noqa: F401
import pytest
from apoio import config_de_teste, linha, montar

from ongsys import cli
from ongsys.checkpoint import Checkpoint
from ongsys.modelos import Estado
from ongsys.navegador import ErroNavegador, OngsysBot

MENSAGEM_REAL = (
    "×\nErro!\n\nOcorreu um erro ao tentar executar essa ação. Por favor tente "
    "novamente.\n\nPERÍODO FECHADO!\n\nFechar"
)


@pytest.fixture
def titulo(tmp_path):
    return montar(
        tmp_path / "p.xlsx",
        [linha("BELLE CARIOCA RESTAURANTE", 133.77, documento="NF 14717",
               emissao=date(2026, 7, 30), vencimento=date(2026, 10, 15),
               pagamento=date(2026, 10, 15))],
    ).titulos[0]


class _Campo:
    def __init__(self):
        self.first = self

    def wait_for(self, **_):
        pass

    def press(self, _tecla):
        pass


class _Pagina:
    def goto(self, *_a, **_k):
        pass

    def wait_for_timeout(self, _ms):
        pass


def _bot_que_chega_ao_salvar(tmp_path, monkeypatch, mensagem, formulario_fechou=False):
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "ev")
    bot.page = _Pagina()
    cliques = []
    nada = lambda *a, **k: None  # noqa: E731
    for nome in ("_purgar_modais_orfaos", "_fechar_modal_erro_confirmado",
                 "_abrir_formulario_inclusao", "_conferir_tela_do_formulario",
                 "_preencher", "_preencher_dinheiro", "_selecionar_lista",
                 "_selecionar_conta", "_abrir_aba_rateio", "_conferir_totais_na_tela",
                 "_exigir_dominio_ongsys"):
        monkeypatch.setattr(bot, nome, nada)
    monkeypatch.setattr(bot, "_detectar_modal_inclusao", lambda: None)
    monkeypatch.setattr(bot, "_campo", lambda _s: _Campo())
    monkeypatch.setattr(bot, "selecionar_fornecedor_titulo", lambda t: t.fornecedor)
    monkeypatch.setattr(bot, "_resolver_duplicado_pendente", lambda: False)
    monkeypatch.setattr(bot, "_ler_tipo_pagamento", lambda: "")
    monkeypatch.setattr(bot, "_preencher_rateio", lambda *_: [])
    monkeypatch.setattr(bot, "_incluir_linha_rateio", lambda *_: [])
    monkeypatch.setattr(bot, "_clicar", lambda _s, descricao: cliques.append(descricao))
    monkeypatch.setattr(bot, "_finalizar_sucesso_confirmado", lambda *_: False)
    monkeypatch.setattr(bot, "_mensagens_da_tela", lambda: mensagem)
    monkeypatch.setattr(bot, "_formulario_fechou", lambda: formulario_fechou)
    return bot, cliques


def test_periodo_fechado_vira_falha_comum_com_explicacao(tmp_path, monkeypatch, titulo):
    bot, cliques = _bot_que_chega_ao_salvar(tmp_path, monkeypatch, MENSAGEM_REAL)

    with pytest.raises(ErroNavegador) as erro:
        bot.incluir(titulo)

    texto = str(erro.value)
    assert "PERÍODO FECHADO" in texto and "Nada foi salvo" in texto
    assert "30/07/2026" in texto
    assert cliques == ["salvar título"]  # uma tentativa só, sem insistir
    assert bot.salvar_foi_clicado is False

    ponto = Checkpoint(tmp_path / "checkpoint.json")
    cli._registrar_erro_no_checkpoint(ponto, titulo, bot, texto)
    assert ponto.estado_de(titulo) == Estado.FALHA


def test_formulario_fechado_continua_incerto(tmp_path, monkeypatch, titulo):
    # Se a janela sumiu, não dá para afirmar que nada foi gravado.
    bot, _ = _bot_que_chega_ao_salvar(tmp_path, monkeypatch, MENSAGEM_REAL, formulario_fechou=True)

    with pytest.raises(ErroNavegador, match="continuou aberto"):
        bot.incluir(titulo)

    assert bot.salvar_foi_clicado is True
    ponto = Checkpoint(tmp_path / "checkpoint.json")
    cli._registrar_erro_no_checkpoint(ponto, titulo, bot, "x")
    assert ponto.estado_de(titulo) == Estado.EM_EXECUCAO


@pytest.mark.parametrize("mensagem", ["", "×", "Erro! Ocorreu um erro ao tentar executar essa ação."])
def test_erro_desconhecido_depois_de_salvar_continua_incerto(tmp_path, monkeypatch, titulo, mensagem):
    bot, _ = _bot_que_chega_ao_salvar(tmp_path, monkeypatch, mensagem)

    with pytest.raises(ErroNavegador, match="continuou aberto"):
        bot.incluir(titulo)

    assert bot.salvar_foi_clicado is True


@pytest.mark.parametrize("mensagem, reconhece", [
    (MENSAGEM_REAL, True),
    ("PERIODO FECHADO", True),
    ("periodo fechado para lancamentos", True),
    ("O número do documento fiscal já existe para esse fornecedor.", False),
    ("Contas a pagar incluído com sucesso!", False),
    ("", False),
])
def test_reconhecimento_da_mensagem(mensagem, reconhece):
    assert bool(OngsysBot._recusa_definitiva_do_ongsys(mensagem)) is reconhece
