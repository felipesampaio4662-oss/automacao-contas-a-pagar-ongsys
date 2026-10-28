"""Contas de cartão 65 - PREPAGO - 0003 e 66 - PRE PAGO - 0004 (2026-10-06).

No mesmo dia a conta 56 (PREPAGO CARTAO 0005) saiu do config: ela não aparece
mais na tela do Ongsys.

O nome precisa ser igual ao do Ongsys ("65 - PREPAGO - 0003 ()") para a
seleção e a auditoria, e ele não traz a palavra CARTAO, que era o único sinal
de cartão. Por isso existe ``contas_cartao`` no config.json. Estes testes usam
a configuração REAL para garantir que a conta nova funciona e que as outras
contas continuam como estavam.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import apoio  # noqa: F401
import pytest
from apoio import config_de_teste, linha, montar

from ongsys import cli, configuracao
from ongsys.auditoria import comparar_texto
from ongsys.navegador import ErroFatal, OngsysBot

RAIZ = Path(__file__).resolve().parent.parent
from apoio import CONFIG_PROJETO as CONFIG  # noqa: E402
OPCOES_DA_TELA = [
    "", f"6 - {json.loads(CONFIG.read_text(encoding='utf-8'))['contas_pagadoras']['6']} (Itaú Unibanco S.A.)",
    "55 - PREPAGO - PRINCIPAL ()",
    "63 - PREPAGO CARTÃO 0001 ()", "64 - PREPAGO CARTÃO 0002 ()",
    "65 - PREPAGO - 0003 ()", "66 - PRE PAGO - 0004 ()",
]


@pytest.fixture(scope="module")
def config():
    return configuracao.carregar(CONFIG)


def _titulo(tmp_path, config, conta, fornecedor="MONITORA", categoria="LICENCA DE USO"):
    resultado = montar(
        tmp_path / f"p{conta or 0}.xlsx",
        [linha(fornecedor, 533.73, documento="19.08", categoria=categoria,
               vencimento=date(2026, 8, 19), pagamento=date(2026, 8, 19))],
        config=config, conta=conta,
    )
    assert len(resultado.titulos) == 1
    return resultado.titulos[0]


class TestConfiguracao:
    def test_conta_65_esta_cadastrada_com_o_nome_do_ongsys(self, config):
        assert config["contas_pagadoras"]["65"] == "PREPAGO - 0003"
        assert config["contas_pagadoras"]["66"] == "PRE PAGO - 0004"  # nome escrito de outro jeito no Ongsys
        assert config["contas_cartao"] == ["65", "66"]
        assert "56" not in config["contas_pagadoras"]
        assert "contas_cartao" not in config.get("_campos_desconhecidos", [])

    def test_exemplo_de_configuracao_acompanha(self):
        exemplo = configuracao.carregar(RAIZ / "config.example.json")
        assert exemplo["contas_pagadoras"]["65"] == "PREPAGO - 0003"
        assert exemplo["contas_pagadoras"]["66"] == "PRE PAGO - 0004"
        assert exemplo["contas_cartao"] == ["65", "66"]
        assert "56" not in exemplo["contas_pagadoras"]

    @pytest.mark.parametrize("conta, cartao", [
        ("66", True), ("65", True), ("64", True), ("63", True),
        ("56", False), ("55", False), ("6", False), ("2", False), ("", False),
    ])
    def test_quais_contas_sao_cartao(self, config, conta, cartao):
        assert configuracao.conta_e_cartao(config, conta) is cartao

    def test_nenhuma_conta_antiga_mudou(self, config):
        if CONFIG.name != "config.json":
            pytest.skip("só faz sentido com o config.json real desta máquina")
        antes = json.loads(
            (RAIZ / "backups" / "config_20261006_antes_conta_65.json").read_text(encoding="utf-8")
        )["contas_pagadoras"] if (RAIZ / "backups" / "config_20261006_antes_conta_65.json").exists() else None
        if antes is None:
            pytest.skip("cópia de antes da mudança não está nesta máquina")
        depois = dict(config["contas_pagadoras"])
        assert depois.pop("65") == "PREPAGO - 0003"
        assert depois.pop("66") == "PRE PAGO - 0004"
        assert antes.pop("56") == "PREPAGO CARTAO 0005"
        assert depois == antes

    def test_conta_de_cartao_inexistente_e_recusada(self, tmp_path):
        bruto = json.loads(CONFIG.read_text(encoding="utf-8"))
        bruto["contas_cartao"] = ["65", "999"]
        destino = tmp_path / "config.json"
        destino.write_text(json.dumps(bruto), encoding="utf-8")

        with pytest.raises(configuracao.ErroConfiguracao, match="contas_cartao.*999"):
            configuracao.carregar(destino)

    def test_config_antigo_sem_a_lista_continua_valido(self, tmp_path):
        bruto = json.loads(CONFIG.read_text(encoding="utf-8"))
        bruto.pop("contas_cartao")
        bruto["contas_pagadoras"].pop("65")
        bruto["contas_pagadoras"].pop("66")
        destino = tmp_path / "config.json"
        destino.write_text(json.dumps(bruto), encoding="utf-8")

        antigo = configuracao.carregar(destino)

        assert antigo["contas_cartao"] == []
        assert configuracao.conta_e_cartao(antigo, "64") is True
        assert configuracao.conta_e_cartao(antigo, "6") is False


class TestPlanejamento:
    def test_titulo_na_conta_65_sai_como_cartao(self, tmp_path, config):
        titulo = _titulo(tmp_path, config, "65")

        assert titulo.conta_pagadora_id == "65"
        assert titulo.conta_pagadora_nome == "PREPAGO - 0003"
        assert titulo.conta_cartao is True
        assert titulo.chave.endswith("|65")
        assert not titulo.bloqueios

    @pytest.mark.parametrize("conta, cartao", [("6", False), ("64", True), ("", False)])
    def test_outras_contas_mantem_o_comportamento(self, tmp_path, config, conta, cartao):
        assert _titulo(tmp_path, config, conta).conta_cartao is cartao

    def test_conta_nao_cadastrada_continua_recusada(self, tmp_path, config):
        from ongsys import regras

        with pytest.raises(regras.ErroRegras, match="não está cadastrada"):
            _titulo(tmp_path, config, "67")

    def test_conta_56_removida_e_recusada_no_planejamento(self, tmp_path, config):
        from ongsys import regras

        with pytest.raises(regras.ErroRegras, match="não está cadastrada"):
            _titulo(tmp_path, config, "56")

    def test_titulo_na_conta_66_sai_como_cartao(self, tmp_path, config):
        titulo = _titulo(tmp_path, config, "66")

        assert titulo.conta_pagadora_nome == "PRE PAGO - 0004"
        assert titulo.conta_cartao is True
        assert titulo.chave.endswith("|66")

    def test_impressao_digital_do_titulo_nao_mudou(self, tmp_path):
        # O campo novo do título não entra no hash: checkpoints antigos valem.
        base = config_de_teste()
        com_lista = config_de_teste(contas_cartao=["6"])
        a = _titulo(tmp_path / "a", base, "6")
        b = _titulo(tmp_path / "b", com_lista, "6")

        assert (a.conta_cartao, b.conta_cartao) == (False, True)
        assert a.hash_origem() == b.hash_origem()
        assert a.chave == b.chave


class _Opcoes:
    def __init__(self, textos):
        self.textos = textos

    def all_inner_texts(self):
        return list(self.textos)


class _CampoConta:
    def __init__(self, textos):
        self.first = self
        self.textos = textos
        self.selecoes = []

    def input_value(self):
        return ""

    def locator(self, _seletor):
        return _Opcoes(self.textos)

    def select_option(self, **opcao):
        self.selecoes.append(opcao)


class TestTelaDoOngsys:
    @pytest.fixture
    def bot(self, tmp_path, config):
        return OngsysBot(config, tmp_path / "perfil", tmp_path / "evidencias")

    def test_seleciona_exatamente_a_opcao_65(self, bot, tmp_path, config, monkeypatch):
        campo = _CampoConta(OPCOES_DA_TELA)
        monkeypatch.setattr(bot, "_campo", lambda _seletor: campo)

        bot._selecionar_conta(_titulo(tmp_path, config, "65"))

        assert campo.selecoes == [{"label": "65 - PREPAGO - 0003 ()"}]

    @pytest.mark.parametrize("conta, rotulo", [
        ("66", "66 - PRE PAGO - 0004 ()"),
        ("64", "64 - PREPAGO CARTÃO 0002 ()"),
        ("55", "55 - PREPAGO - PRINCIPAL ()"),
        ("6", OPCOES_DA_TELA[1]),
    ])
    def test_as_outras_contas_continuam_escolhendo_a_propria_opcao(
        self, bot, tmp_path, config, monkeypatch, conta, rotulo
    ):
        campo = _CampoConta(OPCOES_DA_TELA)
        monkeypatch.setattr(bot, "_campo", lambda _seletor: campo)

        bot._selecionar_conta(_titulo(tmp_path, config, conta))

        assert campo.selecoes == [{"label": rotulo}]

    def test_se_a_conta_nao_estiver_na_tela_a_execucao_para(self, bot, tmp_path, config, monkeypatch):
        campo = _CampoConta([o for o in OPCOES_DA_TELA if "0003" not in o])
        monkeypatch.setattr(bot, "_campo", lambda _seletor: campo)

        with pytest.raises(ErroFatal, match="PREPAGO - 0003"):
            bot._selecionar_conta(_titulo(tmp_path, config, "65"))
        assert campo.selecoes == []

    def test_fornecedor_usa_o_cadastro_especial_de_cartao(self, bot, tmp_path, config, monkeypatch):
        titulo = _titulo(tmp_path, config, "65")
        opcao = "3819 - LICENCA DE USO - MONITORA"
        monkeypatch.setattr(bot, "_candidatos_autocomplete", lambda *_: [opcao])
        cliques = []
        monkeypatch.setattr(bot, "_clicar_opcao", lambda texto: cliques.append(texto))
        monkeypatch.setattr(bot, "_fechar_popups", lambda *a, **k: True)

        assert bot._conta_e_cartao(titulo) is True
        assert bot._categorias_fornecedor_cartao(titulo) == ["LICENCA DE USO"]
        assert bot.selecionar_fornecedor_titulo(titulo) == opcao
        assert cliques == [opcao]
        assert "LICENCA DE USO" in bot.fornecedores_esperados_na_tela(titulo)

    def test_conta_bancaria_nao_usa_o_cadastro_de_cartao(self, bot, tmp_path, config):
        titulo = _titulo(tmp_path, config, "6")

        assert bot._conta_e_cartao(titulo) is False
        assert bot._categorias_fornecedor_cartao(titulo) == []

    def test_documento_repetido_segue_a_regra_das_outras_contas(self, bot, tmp_path, config):
        titulo = _titulo(tmp_path, config, "65")

        assert bot.proximo_documento(titulo, set()) == "19.08.1"


class TestAuditoria:
    def test_conta_lida_da_tela_confere(self):
        assert comparar_texto("Conta pagadora", "PREPAGO - 0003", "65 - PREPAGO - 0003 ()", "") is None
        assert comparar_texto("Conta pagadora", "PRE PAGO - 0004", "66 - PRE PAGO - 0004 ()", "") is None
        assert comparar_texto("Conta pagadora", "PRE PAGO - 0004", "65 - PREPAGO - 0003 ()", "") is not None

    @pytest.mark.parametrize("outra", ["55 - PREPAGO - PRINCIPAL ()", "64 - PREPAGO CARTÃO 0002 ()", ""])
    def test_outra_conta_na_tela_e_divergencia(self, outra):
        assert comparar_texto("Conta pagadora", "PREPAGO - 0003", outra, "") is not None

    def test_recuperacao_de_id_aceita_o_nome_de_categoria_do_cartao(self, tmp_path, config):
        titulo = _titulo(tmp_path, config, "65")
        lido = {"fornecedor": "3819 - LICENCA DE USO", "documento": "19.08", "valor": "533,73",
                "emissao": "01/06/2026", "vencimento": "19/08/2026", "efetivacao": "19/08/2026"}

        assert cli._motivos_titulo_incompativel(titulo, lido, Decimal("0.01")) == []

    def test_recuperacao_de_id_em_conta_bancaria_continua_exigindo_o_nome(self, tmp_path, config):
        titulo = _titulo(tmp_path, config, "6")
        lido = {"fornecedor": "3819 - LICENCA DE USO", "documento": "19.08", "valor": "533,73",
                "emissao": "01/06/2026", "vencimento": "19/08/2026", "efetivacao": "19/08/2026"}

        assert any("fornecedor" in m for m in cli._motivos_titulo_incompativel(titulo, lido, Decimal("0.01")))


class TestPainelEPrompt:
    def test_prompt_de_conta_aceita_65(self, config, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda _texto: "65")

        assert cli._perguntar_conta(config) == "65"
        assert "65 = PREPAGO - 0003" in capsys.readouterr().out

    def test_painel_oferece_a_conta_65(self, config):
        from ongsys import painel

        rotulos = [f"{numero} — {nome}" for numero, nome in config["contas_pagadoras"].items()]
        assert "65 — PREPAGO - 0003" in rotulos
        assert hasattr(painel, "PainelOngsys")
