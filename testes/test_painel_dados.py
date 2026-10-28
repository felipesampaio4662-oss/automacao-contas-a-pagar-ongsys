"""Leituras do painel (abas Resultado/Pendências, progresso, perguntas)."""

from datetime import date
from pathlib import Path

import apoio  # noqa: F401  (ajusta o sys.path)
import pytest

from ongsys import cli
from ongsys.checkpoint import Checkpoint, ItemCheckpoint
from ongsys.modelos import Estado
from ongsys.painel import OpcoesPainel, montar_comando
from ongsys.painel_dados import (
    GRUPO_DIVERGENCIA,
    GRUPO_FALHA,
    GRUPO_SEM_ID,
    classificar_linha,
    extrair_progresso,
    formatar_data,
    interpretar_data,
    ler_resultado,
    pendencias_do_checkpoint,
    pergunta_pendente,
    resumir_resultado,
)


class TestDatas:
    def test_aceita_formato_brasileiro_e_iso(self):
        assert interpretar_data("21/09/2026") == date(2026, 9, 21)
        assert interpretar_data("2026-09-21") == date(2026, 9, 21)
        assert interpretar_data("") is None

    def test_data_invalida_explica_o_formato(self):
        with pytest.raises(ValueError, match="DD/MM/AAAA"):
            interpretar_data("31/02/2026")

    def test_exibe_no_formato_brasileiro(self):
        assert formatar_data(date(2026, 9, 1)) == "01/09/2026"


class TestAcompanhamento:
    SAIDA = (
        "[1/5] IVONE FERRAZ ANDRADE SERVICOS EDUCACIONAIS - 09_26\n"
        "      FALHA: Linha 1: ...\n\n"
        "[2/5] HELENA ANA DA ROCHA - NF 382\n"
        "      salvo. ID 00013983\n"
    )

    def test_progresso_usa_o_ultimo_titulo(self):
        assert extrair_progresso(self.SAIDA) == (2, 5, "HELENA ANA DA ROCHA - NF 382")

    @pytest.mark.parametrize("linha, cor", [
        ("      FALHA: Linha 1: Categoria", "falha"),
        ("EXECUCAO INTERROMPIDA: erro", "falha"),
        ("      ATENCAO: 1 pendencia(s) para corrigir", "atencao"),
        ("        * Linha 4: o código 7.2.2.04 não existe", "atencao"),
        ("      ADIADO: Busca de projetado inconclusiva", "atencao"),
        ("      auditoria encontrou 1 divergencia(s).", "atencao"),
        ("      salvo. ID 00013961", "ok"),
        ("      auditoria conferida.", "ok"),
        ("      info: Tipo de pagamento do cadastro do fornecedor: 'PIX'.", "info"),
        ("[3/5] TATIANA - 09_26", "titulo"),
        ("      verificacao: nenhum titulo equivalente localizado.", None),
    ])
    def test_cores_das_linhas(self, linha, cor):
        assert classificar_linha(linha) == cor

    def test_pergunta_aberta_e_detectada(self):
        buffer = "FILA...\nDigite CONFIRMO para continuar: "
        assert pergunta_pendente(buffer) == "Digite CONFIRMO para continuar:"

    def test_pergunta_respondida_nao_e_mais_destacada(self):
        assert pergunta_pendente("Digite CONFIRMO para continuar: CONFIRMO\n") is None

    def test_linha_comum_em_andamento_nao_e_pergunta(self):
        assert pergunta_pendente("      verificacao: DOCUMENTO_REPETIDO") is None


def _escrever_resultado(caminho: Path) -> Path:
    caminho.write_text(
        "situacao;fornecedor;documento_original;documento_final;id_ongsys;valor;inicio;fim;"
        "tentativas;fornecedor_selecionado;campos_nao_localizados;avisos;mensagem;screenshot\n"
        "CONCLUIDO;DA VILA;NF 99545;NF 99545.1;00013961;41.767,04;;;1;;"
        "Linha 4: o código 7.2.2.04 não existe;Documento alterado | Tipo PIX;Titulo criado sem baixa.;\n"
        "CONCLUIDO;HELENA ANA;NF 382;NF 382;00013983;2.850,00;;;1;;;Tipo PIX;Titulo criado sem baixa.;\n"
        "FALHA;IVONE;09_26;09_26;;517,69;;;1;;;;Salvar desabilitado;C:/x.png\n"
        "ADIADO;ASSINAFACIL;NF 1;NF 1;;1,00;;;1;;;;Projetados empatados;\n",
        encoding="utf-8-sig",
    )
    return caminho


def test_resultado_separa_ok_pendencia_falha_e_adiado(tmp_path):
    linhas = ler_resultado(_escrever_resultado(tmp_path / "resultado_1.csv"))

    assert [l.cor for l in linhas] == ["atencao", "ok", "falha", "info"]
    assert linhas[0].pendencias == ["Linha 4: o código 7.2.2.04 não existe"]
    assert linhas[0].avisos == ["Documento alterado", "Tipo PIX"]
    assert linhas[0].documento == "NF 99545.1"
    assert resumir_resultado(linhas) == {"ok": 1, "atencao": 1, "falha": 1, "info": 1}


def _item(chave, estado, id_ongsys="", divergencias=(), fim="2026-09-18T10:00:00"):
    return ItemCheckpoint(
        chave=chave, hash_origem="h", estado=estado, id_ongsys=id_ongsys,
        fornecedor=f"FORN {chave}", documento_original="09_26", valor="10.00",
        divergencias=list(divergencias), fim=fim,
    )


def test_pendencias_agrupadas_por_acao():
    itens = [
        _item("a", Estado.CONCLUIDO, "1"),
        _item("b", Estado.SALVO_NAO_AUDITADO, "00013974", ["Reabertura do titulo"]),
        _item("c", Estado.SALVO_NAO_AUDITADO, "", ["ID do titulo"]),
        _item("d", Estado.EM_EXECUCAO),
        _item("e", Estado.FALHA),
    ]

    grupos = [(p.chave, p.grupo) for p in pendencias_do_checkpoint(itens)]

    assert ("a", GRUPO_SEM_ID) not in grupos
    assert {c for c, g in grupos if g == GRUPO_SEM_ID} == {"c", "d"}
    assert [c for c, g in grupos if g == GRUPO_DIVERGENCIA] == ["b"]
    assert [c for c, g in grupos if g == GRUPO_FALHA] == ["e"]
    assert grupos[0][1] == GRUPO_SEM_ID  # o mais urgente primeiro


class TestAceitarDivergencias:
    def _ponto(self, tmp_path):
        ponto = Checkpoint(tmp_path / "checkpoint.json")
        ponto.itens = {
            "com_id": _item("com_id", Estado.SALVO_NAO_AUDITADO, "00013963", ["Fornecedor"]),
            "sem_id": _item("sem_id", Estado.SALVO_NAO_AUDITADO, "", ["ID do titulo"]),
            "execucao": _item("execucao", Estado.EM_EXECUCAO),
        }
        ponto.salvar()
        return ponto

    def test_titulo_com_id_vai_para_concluido_e_guarda_historico(self, tmp_path):
        ponto = self._ponto(tmp_path)
        item = ponto.aceitar_divergencias("com_id", "revisado")
        assert item.estado == Estado.CONCLUIDO
        assert item.divergencias == ["Fornecedor"]
        assert "ACEITAS PELO USUARIO" in item.resultado_auditoria
        assert ponto.salvos_com_divergencia() == []

    @pytest.mark.parametrize("chave", ["sem_id", "execucao", "inexistente"])
    def test_nunca_encerra_titulo_sem_id(self, tmp_path, chave):
        ponto = self._ponto(tmp_path)
        with pytest.raises(ValueError):
            ponto.aceitar_divergencias(chave, "x")

    def test_contagens_separadas(self, tmp_path):
        ponto = self._ponto(tmp_path)
        assert {i.chave for i in ponto.sem_confirmacao_no_ongsys()} == {"sem_id", "execucao"}
        assert [i.chave for i in ponto.salvos_com_divergencia()] == ["com_id"]

    def test_comando_exige_aceito_e_faz_copia(self, tmp_path, monkeypatch):
        self._ponto(tmp_path)
        monkeypatch.setattr(cli, "CHECKPOINT", tmp_path / "checkpoint.json")
        monkeypatch.setattr("builtins.input", lambda _p: "ACEITO")

        cli._aceitar_divergencia(["13963"])

        assert Checkpoint(tmp_path / "checkpoint.json").itens["com_id"].estado == Estado.CONCLUIDO
        assert list(tmp_path.glob("checkpoint_antes_aceite_*.json"))

    def test_comando_cancelado_nao_altera(self, tmp_path, monkeypatch):
        self._ponto(tmp_path)
        monkeypatch.setattr(cli, "CHECKPOINT", tmp_path / "checkpoint.json")
        monkeypatch.setattr("builtins.input", lambda _p: "nao")

        with pytest.raises(cli.SaidaControlada):
            cli._aceitar_divergencia(["00013963"])

        estado = Checkpoint(tmp_path / "checkpoint.json").itens["com_id"].estado
        assert estado == Estado.SALVO_NAO_AUDITADO


class TestComandosDoPainel:
    def test_aceitar_divergencia_so_leva_ids(self):
        comando = montar_comando(
            OpcoesPainel(comando="aceitar-divergencia", planilha=Path("x.xlsx"), ids=("1", "2")),
            python="py",
        )
        assert comando[3:] == ["aceitar-divergencia", "--id", "1", "--id", "2"]
        assert "ACEITO" not in comando

    def test_conferir_id_usa_auditar_com_ids(self):
        comando = montar_comando(
            OpcoesPainel(comando="auditar", planilha=Path("x.xlsx"), conta="6", ids=("00013500",)),
            python="py",
        )
        assert comando[comando.index("--auditar-id") + 1] == "00013500"
        assert "x.xlsx" in comando

    def test_ids_nao_vazam_para_executar(self):
        comando = montar_comando(OpcoesPainel(comando="executar", ids=("1",)), python="py")
        assert "--auditar-id" not in comando and "--id" not in comando
