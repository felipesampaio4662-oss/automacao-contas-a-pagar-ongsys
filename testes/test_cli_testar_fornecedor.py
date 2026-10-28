"""Regressão: `testar-fornecedor` precisa pedir a planilha, nunca recusar de cara.

Bug relatado pelo usuário: ao arrastar a planilha, o modo respondia
"Informe pelo menos um nome com --nome, ou uma planilha..." mesmo com a
planilha em mãos. A causa era uma checagem que recusava antes de a função
chegar ao prompt interativo que `_planejar` já oferece.
"""

from __future__ import annotations

from types import SimpleNamespace

import apoio
import pytest
from apoio import config_de_teste, criar_planilha, linha

from ongsys import cli


class _PaginaFalsa:
    """Só precisa aceitar o goto() que o loop de teste de fornecedor chama."""

    def goto(self, *args, **kwargs):
        pass


class _BotFalso:
    """Substitui o OngsysBot: nenhum navegador é aberto de verdade."""

    instancias: list["_BotFalso"] = []

    def __init__(self, config, perfil, evidencias):
        self.config = config
        self.page = _PaginaFalsa()
        self.fornecedores_testados: list[str] = []
        _BotFalso.instancias.append(self)

    def abrir(self):
        return self

    def exigir_sessao(self):
        pass

    def fechar(self):
        pass

    def testar_fornecedor(self, nome):
        self.fornecedores_testados.append(nome)
        return [{"consulta": nome, "candidatos": [], "selecionaria": False}]


@pytest.fixture(autouse=True)
def _sem_navegador_de_verdade(monkeypatch, tmp_path):
    _BotFalso.instancias.clear()
    monkeypatch.setattr("ongsys.navegador.OngsysBot", _BotFalso)
    monkeypatch.setattr(cli, "_abrir_formulario_inclusao", lambda bot: None)
    # Evita gravar relatorios de teste dentro da pasta real do projeto.
    monkeypatch.setattr(cli, "SAIDA", tmp_path / "relatorios")
    yield


def _args(**over):
    base = dict(planilha=None, inicio=None, fim=None, conta=None, aba=None,
                coluna=None, somente_fornecedor=None, nome=None)
    base.update(over)
    return SimpleNamespace(**base)


class TestTestarFornecedorSemArgumentos:
    def test_sem_nome_e_sem_planilha_pede_a_planilha_interativamente(self, tmp_path, monkeypatch):
        destino = tmp_path / "a.xlsx"
        criar_planilha(destino, [linha("FORNECEDOR TESTE", 100.0)])

        # Ordem das perguntas dentro de _planejar: planilha, data inicial,
        # data final, conta. As duas datas ficam em branco para aceitar a
        # sugestão calculada a partir da própria planilha.
        entradas = iter([str(destino), "", "", "6"])
        monkeypatch.setattr("builtins.input", lambda *_: next(entradas))

        cli._testar_fornecedor(_args(), config_de_teste())

        assert _BotFalso.instancias[0].fornecedores_testados == ["FORNECEDOR TESTE"]

    def test_nao_recusa_antes_de_perguntar(self, tmp_path, monkeypatch):
        """O bug original: SaidaControlada disparava antes de qualquer prompt."""
        destino = tmp_path / "b.xlsx"
        criar_planilha(destino, [linha("OUTRO FORNECEDOR", 50.0)])
        entradas = iter([str(destino), "", "", "6"])
        monkeypatch.setattr("builtins.input", lambda *_: next(entradas))

        try:
            cli._testar_fornecedor(_args(), config_de_teste())
        except cli.SaidaControlada as erro:
            pytest.fail(f"nao deveria recusar antes de perguntar a planilha: {erro}")

    def test_planilha_entre_aspas_arrastada_e_aceita(self, tmp_path, monkeypatch):
        """§23: aspas do arrastar-e-soltar precisam ser removidas."""
        destino = tmp_path / "c.xlsx"
        criar_planilha(destino, [linha("FORNECEDOR ASPAS", 75.0)])
        entradas = iter([f'"{destino}"', "", "", "6"])
        monkeypatch.setattr("builtins.input", lambda *_: next(entradas))

        cli._testar_fornecedor(_args(), config_de_teste())
        assert _BotFalso.instancias[0].fornecedores_testados == ["FORNECEDOR ASPAS"]


class TestTestarFornecedorComNome:
    def test_nome_explicito_nao_precisa_de_planilha(self):
        """--nome dispensa qualquer prompt: nenhum input() deve ser chamado."""
        cli._testar_fornecedor(_args(nome=["ALGUM FORNECEDOR"]), config_de_teste())
        assert _BotFalso.instancias[0].fornecedores_testados == ["ALGUM FORNECEDOR"]

    def test_nomes_repetidos_sao_deduplicados(self):
        cli._testar_fornecedor(
            _args(nome=["FORNECEDOR X", "FORNECEDOR X"]), config_de_teste()
        )
        assert _BotFalso.instancias[0].fornecedores_testados == ["FORNECEDOR X"]


class TestResilienciaAFalhaIsolada:
    """Bug real: um timeout num fornecedor derrubava o teste inteiro (§22 não
    se aplica aqui — não é um erro estrutural do site, é um problema pontual
    de um fornecedor específico, então os demais precisam continuar)."""

    def test_falha_num_fornecedor_nao_impede_os_seguintes(self, monkeypatch):
        def testar_com_falha_no_meio(self, nome):
            self.fornecedores_testados.append(nome)
            if nome == "QUEBRA":
                raise TimeoutError("Locator.fill: Timeout 15000ms exceeded.")
            return [{"consulta": nome, "candidatos": [], "selecionaria": False}]

        monkeypatch.setattr(_BotFalso, "testar_fornecedor", testar_com_falha_no_meio)

        cli._testar_fornecedor(
            _args(nome=["ANTES", "QUEBRA", "DEPOIS"]), config_de_teste()
        )

        assert _BotFalso.instancias[0].fornecedores_testados == ["ANTES", "QUEBRA", "DEPOIS"]


class TestDuplicidadeExataIntencional:
    def test_opcao_fica_desligada_por_padrao(self):
        args = cli._construir_parser().parse_args(["executar"])
        assert args.permitir_duplicidade_exata is False

    def test_opcao_precisa_ser_pedida_explicitamente(self):
        args = cli._construir_parser().parse_args([
            "executar", "--somente-fornecedor", "BENEFIX", "--limite", "1",
            "--permitir-duplicidade-exata",
        ])
        assert args.permitir_duplicidade_exata is True

    @pytest.mark.parametrize(
        "fornecedor, limite, quantidade",
        [
            (None, 1, 1),
            ("BENEFIX", None, 1),
            ("BENEFIX", 2, 1),
            ("BENEFIX", 1, 2),
        ],
    )
    def test_modo_excepcional_recusa_escopo_amplo(
        self, fornecedor, limite, quantidade
    ):
        args = SimpleNamespace(
            permitir_duplicidade_exata=True,
            somente_fornecedor=fornecedor,
            limite=limite,
        )
        with pytest.raises(cli.SaidaControlada):
            cli._validar_modo_duplicidade_intencional(args, quantidade)

    def test_escopo_de_um_fornecedor_e_um_titulo_e_aceito(self):
        args = SimpleNamespace(
            permitir_duplicidade_exata=True,
            somente_fornecedor="BENEFIX",
            limite=1,
        )
        cli._validar_modo_duplicidade_intencional(args, 1)

    def test_exige_a_palavra_duplicar_na_confirmacao_final(self, monkeypatch):
        args = SimpleNamespace(permitir_duplicidade_exata=True)
        titulo = SimpleNamespace(
            fornecedor="EMPRESA EXEMPLO DE BENEFICIOS - BENEFIX",
            documento="BL",
            valor_br="139.863,76",
            rateios=[object()] * 35,
        )
        monkeypatch.setattr("builtins.input", lambda *_: "SIM")
        assert cli._confirmar_duplicidade_exata_intencional(args, titulo, "ID 1") is False

        monkeypatch.setattr("builtins.input", lambda *_: "DUPLICAR")
        assert cli._confirmar_duplicidade_exata_intencional(args, titulo, "ID 1") is True
