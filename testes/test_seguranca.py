"""Travas de segurança da automação (endurecimento de 2026-10-01).

Cada teste prova uma barreira: o que a automação NÃO pode fazer, mesmo que a
tela do Ongsys mude ou apareça um aviso inesperado.
"""

import csv
import json
from types import SimpleNamespace

import apoio  # noqa: F401
import pytest
from apoio import config_de_teste

from ongsys import cli
from ongsys import seletores as S
from ongsys.navegador import ErroFatal, OngsysBot


# -- 1. Disjuntor de ações proibidas --------------------------------------

class TestDisjuntor:
    @pytest.mark.parametrize("alvo", [
        "#btn_baixar", "#btn_remessa", "#btn_autorizar", "#field_data_pagamento",
        "Dar baixa", "Confirmar pagamento", "Enviar para aprovação".upper().replace("Ç", "C").replace("Ã", "A"),
    ])
    def test_bloqueia_controles_de_baixa_pagamento_e_remessa(self, alvo):
        with pytest.raises(S.AcaoProibida):
            S.conferir_permitido(alvo)

    def test_campos_do_cadastro_continuam_permitidos(self):
        for seletor in (S.CAMPO_FORNECEDOR, S.CAMPO_VALOR, S.BOTAO_SALVAR, S.categoria_conta(3)):
            S.conferir_permitido(seletor)

    def test_disjuntor_nao_e_engolido_por_except_exception(self):
        """Um ``except Exception`` do preenchimento nunca pode esconder o bloqueio."""
        def rotina_com_tratamento_generico():
            try:
                S.conferir_permitido("#btn_baixar")
            except Exception:  # noqa: BLE001 - é exatamente o que se quer provar
                return "engolido"
            return "seguiu"

        with pytest.raises(S.AcaoProibida):
            rotina_com_tratamento_generico()

    def test_bloqueio_encerra_o_programa_com_codigo_2(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr(cli, "HISTORICO", tmp_path / "historico.csv")
        monkeypatch.setattr(cli, "SAIDA", tmp_path)

        def explode(_argv=None):
            raise S.AcaoProibida("tentou acionar #btn_baixar")

        monkeypatch.setattr(cli, "main", explode)
        assert cli.executar_cli() == 2
        saida = capsys.readouterr().out
        assert "INTERROMPIDA POR SEGURANCA" in saida
        assert "bloqueio_de_seguranca" in (tmp_path / "historico.csv").read_text(encoding="utf-8-sig")


# -- 2. Avisos sensíveis nunca são confirmados ----------------------------

class _Botao:
    def __init__(self, rotulo, cliques):
        self.rotulo, self.cliques = rotulo, cliques

    def is_visible(self):
        return True

    def inner_text(self):
        return self.rotulo

    def click(self):
        self.cliques.append(self.rotulo)


class _Popup:
    def __init__(self, texto, rotulos, cliques):
        self.texto = texto
        self.botoes = [_Botao(r, cliques) for r in rotulos]

    def inner_text(self):
        return self.texto

    def locator(self, _seletor):
        botoes = self.botoes
        return SimpleNamespace(count=lambda: len(botoes), nth=lambda i: botoes[i])


def _bot_com_popup(tmp_path, monkeypatch, popup):
    bot = OngsysBot(config_de_teste(), tmp_path / "p", tmp_path / "e")
    bot.page = SimpleNamespace(wait_for_timeout=lambda _ms: None)
    estado = {"chamadas": 0}

    def popups():
        estado["chamadas"] += 1
        return [popup] if estado["chamadas"] == 1 else []

    monkeypatch.setattr(bot, "_popups_genuinos", popups)
    return bot


class TestAvisosSensiveis:
    @pytest.mark.parametrize("texto", [
        "Deseja realmente excluir este título?",
        "Confirmar pagamento do título?",
        "Dar baixa neste lançamento?",
        "Enviar para aprovação agora?",
        "Gerar remessa com os títulos selecionados?",
    ])
    def test_reconhece_confirmacao_destrutiva_ou_financeira(self, texto):
        assert S.popup_exige_cautela(texto)

    @pytest.mark.parametrize("texto", [
        "O número do documento fiscal já existe para esse fornecedor.",
        "É necessário preencher a linha atual de rateio.",
        "Contas a pagar incluído com sucesso!",
        "Fornecedor sem serviço parametrizado.",
    ])
    def test_avisos_normais_do_cadastro_nao_sao_afetados(self, texto):
        assert not S.popup_exige_cautela(texto)

    def test_aviso_de_exclusao_nunca_recebe_confirmar(self, tmp_path, monkeypatch, capsys):
        cliques = []
        popup = _Popup("Deseja realmente excluir este título?", ["Confirmar", "OK", "Cancelar"], cliques)
        bot = _bot_com_popup(tmp_path, monkeypatch, popup)

        bot._fechar_popups(tentativas=2)

        assert cliques == ["Cancelar"]
        assert "aviso sensivel" in capsys.readouterr().out
        assert bot.avisos_seguranca

    def test_aviso_comum_continua_sendo_fechado_com_ok(self, tmp_path, monkeypatch):
        cliques = []
        popup = _Popup("Fornecedor sem serviço parametrizado.", ["OK"], cliques)
        bot = _bot_com_popup(tmp_path, monkeypatch, popup)

        assert bot._fechar_popups(tentativas=2) is True
        assert cliques == ["OK"]

    @pytest.mark.parametrize("rotulo, dispensa", [
        ("Fechar", True), ("Cancelar", True), ("Não", True), ("×", True), ("", True),
        ("OK", False), ("Confirmar", False), ("Sim", False), ("Continuar", False),
    ])
    def test_rotulos_que_apenas_dispensam(self, rotulo, dispensa):
        assert S.rotulo_so_dispensa(rotulo) is dispensa


# -- 3. Ícones de ação da linha -------------------------------------------

class TestIconesDaLinha:
    @pytest.mark.parametrize("descricao, proibido", [
        ("Excluir fa fa-trash", True),
        ("Baixar título fa fa-usd", True),
        ("Enviar remessa fa fa-paper-plane", True),
        ("Editar fa fa-pencil", False),
        ("Visualizar fa fa-eye", False),
    ])
    def test_classificacao_do_icone(self, descricao, proibido):
        assert S.acao_de_linha_proibida(descricao) is proibido

    def test_bot_recusa_icone_de_excluir(self, tmp_path):
        bot = OngsysBot(config_de_teste(), tmp_path / "p", tmp_path / "e")
        lixeira = SimpleNamespace(evaluate=lambda _js: "Excluir  fa fa-trash")
        lapis = SimpleNamespace(evaluate=lambda _js: "Editar  fa fa-pencil")
        assert bot._acao_de_linha_proibida(lixeira) is True
        assert bot._acao_de_linha_proibida(lapis) is False


# -- 4. Confinamento ao endereço do Ongsys --------------------------------

class TestDominio:
    def _bot(self, tmp_path, url):
        config = config_de_teste()
        config["ongsys_url"] = "https://www.ongsys.com.br/app/index.php/contas-pagar/"
        bot = OngsysBot(config, tmp_path / "p", tmp_path / "e")
        bot.page = SimpleNamespace(url=url)
        return bot

    def test_mesmo_servidor_segue(self, tmp_path):
        self._bot(tmp_path, "https://www.ongsys.com.br/app/index.php/contas-pagar/#")._exigir_dominio_ongsys("salvar")

    @pytest.mark.parametrize("url", [
        "https://ongsys.com.br/app/index.php/contas-pagar/",
        "https://app.ongsys.com.br/contas-pagar/",
    ])
    def test_com_ou_sem_www_e_subdominios_do_ongsys_seguem(self, tmp_path, url):
        self._bot(tmp_path, url)._exigir_dominio_ongsys("salvar")

    @pytest.mark.parametrize("url", [
        "https://site-falso.example.com/contas-pagar/",
        "https://ongsys.com.br.site-falso.com/app/",
        "https://falso-ongsys.com.br/app/",
    ])
    def test_outro_site_interrompe_antes_de_salvar(self, tmp_path, url):
        with pytest.raises(ErroFatal, match="nao no endereco configurado"):
            self._bot(tmp_path, url)._exigir_dominio_ongsys("salvar")

    def test_url_do_ongsys_precisa_ser_https(self, tmp_path):
        from ongsys import configuracao

        dados = json.loads((apoio.RAIZ / "config.example.json").read_text(encoding="utf-8"))
        dados["ongsys_url"] = "http://www.ongsys.com.br/app/"
        caminho = tmp_path / "config.json"
        caminho.write_text(json.dumps(dados), encoding="utf-8")
        with pytest.raises(configuracao.ErroConfiguracao, match="https"):
            configuracao.carregar(caminho)

    def test_navegador_invisivel_e_recusado(self, tmp_path):
        config = config_de_teste()
        config["manter_navegador_visivel"] = False
        with pytest.raises(ErroFatal, match="visível"):
            OngsysBot(config, tmp_path / "p", tmp_path / "e").abrir()


# -- 5. Cópia de segurança e trilha de auditoria --------------------------

class TestRastreabilidade:
    def test_backup_do_checkpoint_antes_de_operar(self, tmp_path, monkeypatch, capsys):
        checkpoint = tmp_path / "checkpoint.json"
        checkpoint.write_text('{"versao": 2, "itens": []}', encoding="utf-8")
        monkeypatch.setattr(cli, "CHECKPOINT", checkpoint)
        monkeypatch.setattr(cli, "BACKUPS", tmp_path / "backups")

        destino = cli._backup_checkpoint("antes_executar")

        assert destino is not None and destino.exists()
        assert destino.read_text(encoding="utf-8") == checkpoint.read_text(encoding="utf-8")
        assert "antes_executar" in destino.name

    def test_backup_sem_checkpoint_nao_quebra(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "CHECKPOINT", tmp_path / "nao_existe.json")
        monkeypatch.setattr(cli, "BACKUPS", tmp_path / "backups")
        assert cli._backup_checkpoint("x") is None

    def test_historico_registra_quem_quando_e_qual_planilha(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "SAIDA", tmp_path)
        monkeypatch.setattr(cli, "HISTORICO", tmp_path / "historico_execucoes.csv")
        planilha = tmp_path / "pagamentos.xlsx"
        planilha.write_bytes(b"conteudo da planilha")

        cli._registrar_historico(
            "executar", planilha_origem=planilha, conta="BANCO X", titulos=3,
            valor_total="1.500,00", resultado="CONCLUIDO: 3", relatorio="resultado_x.csv",
        )
        cli._registrar_historico("auditar", planilha_origem=planilha, titulos=3)

        with (tmp_path / "historico_execucoes.csv").open(encoding="utf-8-sig", newline="") as arquivo:
            linhas = list(csv.DictReader(arquivo, delimiter=";"))
        assert [l["comando"] for l in linhas] == ["executar", "auditar"]  # só cresce
        assert linhas[0]["usuario"] and linhas[0]["computador"]
        assert linhas[0]["planilha"] == "pagamentos.xlsx"
        assert len(linhas[0]["sha256_planilha"]) == 16
        assert linhas[0]["sha256_planilha"] == linhas[1]["sha256_planilha"]

    def test_falha_no_historico_nunca_interrompe(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(cli, "SAIDA", tmp_path / "arquivo_no_lugar_da_pasta")
        (tmp_path / "arquivo_no_lugar_da_pasta").write_text("x")
        monkeypatch.setattr(cli, "HISTORICO", tmp_path / "arquivo_no_lugar_da_pasta" / "h.csv")
        cli._registrar_historico("executar")  # não levanta
        assert "AVISO" in capsys.readouterr().out



class TestCopiasNaoSeSobrescrevem:
    def test_duas_copias_no_mesmo_segundo_ficam_separadas(self, tmp_path, monkeypatch):
        checkpoint = tmp_path / "checkpoint.json"
        checkpoint.write_text('{"versao": 2, "itens": []}', encoding="utf-8")
        monkeypatch.setattr(cli, "CHECKPOINT", checkpoint)
        monkeypatch.setattr(cli, "BACKUPS", tmp_path / "backups")

        class Relogio:
            @staticmethod
            def now():
                from datetime import datetime
                return datetime(2026, 10, 15, 13, 19, 8)

        monkeypatch.setattr(cli, "datetime", Relogio)
        primeira = cli._backup_checkpoint("antes_liberar")
        checkpoint.write_text('{"versao": 2, "itens": [1]}', encoding="utf-8")
        segunda = cli._backup_checkpoint("antes_liberar")

        assert primeira != segunda
        assert primeira.read_text(encoding="utf-8") == '{"versao": 2, "itens": []}'
        assert segunda.read_text(encoding="utf-8") == '{"versao": 2, "itens": [1]}'
        assert segunda.name.endswith("_2.json")

    def test_nome_livre_nao_muda_quando_nao_ha_conflito(self, tmp_path):
        destino = tmp_path / "checkpoint_antes_liberacao_20261015_131908.json"
        assert cli._sem_sobrescrever(destino) == destino
        destino.write_text("x", encoding="utf-8")
        assert cli._sem_sobrescrever(destino).name == "checkpoint_antes_liberacao_20261015_131908_2.json"
