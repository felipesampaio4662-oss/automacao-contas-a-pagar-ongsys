"""Guarda temporária: só o que passou do prazo, e nunca a memória do robô."""

import os
from datetime import datetime, timedelta

import apoio  # noqa: F401
import pytest

from ongsys import cli, limpeza

AGORA = datetime(2026, 10, 1, 12, 0, 0)


def _criar(caminho, dias, conteudo="x"):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(conteudo, encoding="utf-8")
    momento = (AGORA - timedelta(days=dias)).timestamp()
    os.utime(caminho, (momento, momento))
    return caminho


@pytest.fixture
def base(tmp_path):
    _criar(tmp_path / "checkpoint.json", 400)
    _criar(tmp_path / "config.json", 400)
    _criar(tmp_path / "perfil_ongsys" / "Cookies", 400)
    _criar(tmp_path / "relatorios" / "historico_execucoes.csv", 400)
    return tmp_path


def _nomes(itens):
    return sorted(item.caminho.name for item in itens)


class TestPrazos:
    def test_captura_sai_depois_de_30_dias(self, base):
        _criar(base / "evidencias" / "falha_velha.png", 31)
        _criar(base / "evidencias" / "falha_nova.png", 29)

        assert _nomes(limpeza.candidatos(base, AGORA)) == ["falha_velha.png"]

    def test_relatorio_sai_depois_de_90_dias(self, base):
        _criar(base / "relatorios" / "resultado_velho.csv", 91)
        _criar(base / "relatorios" / "conferencia_velha.xlsx", 120)
        _criar(base / "relatorios" / "auditoria_nova.csv", 89)

        assert _nomes(limpeza.candidatos(base, AGORA)) == [
            "conferencia_velha.xlsx", "resultado_velho.csv",
        ]

    def test_copias_do_checkpoint_mantem_as_cinco_mais_recentes(self, base):
        for dias in (100, 110, 120, 130, 140, 150, 160):
            _criar(base / "backups" / f"checkpoint_d{dias}.json", dias)
        _criar(base / "checkpoint_antes_aceite_antigo.json", 200)

        assert _nomes(limpeza.candidatos(base, AGORA)) == [
            "checkpoint_antes_aceite_antigo.json", "checkpoint_d150.json", "checkpoint_d160.json",
        ]

    def test_copia_recente_fica_mesmo_alem_das_cinco(self, base):
        for dias in range(1, 9):
            _criar(base / "backups" / f"checkpoint_d{dias}.json", dias)

        assert limpeza.candidatos(base, AGORA) == []


class TestNuncaApaga:
    def test_memoria_configuracao_sessao_e_historico_ficam(self, base):
        _criar(base / "evidencias" / "falha.png", 400)
        _criar(base / "relatorios" / "resultado.csv", 400)

        itens = limpeza.candidatos(base, AGORA)
        apagados, falhas = limpeza.apagar(itens, base)

        assert (apagados, falhas) == (2, [])
        assert (base / "checkpoint.json").exists()
        assert (base / "config.json").exists()
        assert (base / "perfil_ongsys" / "Cookies").exists()
        assert (base / "relatorios" / "historico_execucoes.csv").exists()
        assert not (base / "evidencias" / "falha.png").exists()
        assert not (base / "relatorios" / "resultado.csv").exists()

    def test_nao_entra_em_subpastas_nem_pega_outros_tipos(self, base):
        _criar(base / "evidencias" / "sub" / "dentro.png", 400)
        _criar(base / "evidencias" / "anotacao.txt", 400)
        _criar(base / "relatorios" / "planilha.xlsm", 400)
        _criar(base / "testes9.xlsx", 400)

        assert limpeza.candidatos(base, AGORA) == []

    @pytest.mark.parametrize("relativo", [
        "checkpoint.json", "config.json", "perfil_ongsys/Cookies",
        "relatorios/historico_execucoes.csv", "ongsys/cli.py",
    ])
    def test_apagar_recusa_qualquer_caminho_fora_da_lista(self, base, relativo):
        alvo = _criar(base / relativo, 400)
        falso = limpeza.Candidato(alvo, limpeza.GRUPO_RELATORIOS, AGORA, 1)

        apagados, falhas = limpeza.apagar([falso], base)

        assert apagados == 0 and len(falhas) == 1
        assert alvo.exists()


class TestComando:
    @pytest.fixture
    def projeto(self, base, monkeypatch):
        monkeypatch.setattr(cli, "BASE", base)
        monkeypatch.setattr(cli, "CHECKPOINT", base / "nao_existe.json")
        monkeypatch.setattr(cli, "SAIDA", base / "relatorios")
        monkeypatch.setattr(cli, "HISTORICO", base / "relatorios" / "historico_execucoes.csv")
        velho = _criar(base / "evidencias" / "falha.png", 400)
        os.utime(velho, (0, 0))
        return base

    def test_sem_a_palavra_apagar_nada_e_excluido(self, projeto, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _texto: "sim")

        with pytest.raises(cli.SaidaControlada, match="Cancelado"):
            cli._limpar()

        assert (projeto / "evidencias" / "falha.png").exists()

    def test_com_apagar_exclui_e_registra_no_historico(self, projeto, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda _texto: "apagar")

        cli._limpar()

        assert not (projeto / "evidencias" / "falha.png").exists()
        assert "1 arquivo(s) apagado(s)" in capsys.readouterr().out
        historico = (projeto / "relatorios" / "historico_execucoes.csv").read_text(encoding="utf-8-sig")
        assert "limpar" in historico and "arquivos apagados: 1" in historico

    def test_nada_fora_do_prazo_nem_pergunta(self, projeto, monkeypatch):
        (projeto / "evidencias" / "falha.png").unlink()
        monkeypatch.setattr("builtins.input", lambda _texto: pytest.fail("nao deveria perguntar"))

        with pytest.raises(cli.SaidaControlada, match="Nada foi apagado"):
            cli._limpar()
