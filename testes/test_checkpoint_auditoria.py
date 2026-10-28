"""Testes do checkpoint estruturado e da comparação de auditoria."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import apoio
import pytest
from apoio import config_de_teste, linha, montar

from ongsys import auditoria as A
from ongsys.checkpoint import Checkpoint
from ongsys.cli import (
    _fila_de_execucao,
    _motivos_linha_incompativel,
    _recuperar_ids_desconhecidos,
)
from ongsys.modelos import Estado, Rateio, Titulo


@pytest.fixture
def titulo(tmp_path):
    return montar(tmp_path / "base.xlsx", [linha("FORNECEDOR TESTE", 1000.0)]).titulos[0]


class TestCheckpoint:
    def test_comeca_vazio(self, tmp_path):
        assert Checkpoint(tmp_path / "c.json").itens == {}

    def test_iniciar_grava_em_execucao_antes_de_salvar(self, tmp_path, titulo):
        """§20: se o programa morrer no meio, o estado já está no disco."""
        caminho = tmp_path / "c.json"
        ponto = Checkpoint(caminho)
        ponto.iniciar(titulo)
        assert Checkpoint(caminho).estado_de(titulo) == Estado.EM_EXECUCAO

    def test_fluxo_completo_ate_concluido(self, tmp_path, titulo):
        ponto = Checkpoint(tmp_path / "c.json")
        ponto.iniciar(titulo)
        ponto.marcar_salvo(titulo, "00033191", "NF 1")
        assert ponto.estado_de(titulo) == Estado.SALVO_NAO_AUDITADO
        ponto.marcar_concluido(titulo, [])
        assert ponto.estado_de(titulo) == Estado.CONCLUIDO
        assert ponto.obter(titulo).id_ongsys == "00033191"

    def test_auditoria_com_divergencia_nao_conclui(self, tmp_path, titulo):
        """§19: só entra em CONCLUIDO depois de auditoria sem divergência."""
        ponto = Checkpoint(tmp_path / "c.json")
        ponto.iniciar(titulo)
        ponto.marcar_salvo(titulo, "00033191", "NF 1")
        ponto.marcar_concluido(titulo, ["Linha 1: Subprojeto"])
        assert ponto.estado_de(titulo) == Estado.SALVO_NAO_AUDITADO
        assert ponto.obter(titulo).divergencias == ["Linha 1: Subprojeto"]

    def test_falha_apos_salvar_nao_volta_para_falha(self, tmp_path, titulo):
        """Voltar para FALHA liberaria nova tentativa e criaria título duplicado."""
        ponto = Checkpoint(tmp_path / "c.json")
        ponto.iniciar(titulo)
        ponto.marcar_salvo(titulo, "00033191", "NF 1")
        ponto.marcar_falha(titulo, "erro na auditoria")
        assert ponto.estado_de(titulo) == Estado.SALVO_NAO_AUDITADO

    def test_falha_antes_de_salvar_marca_falha(self, tmp_path, titulo):
        ponto = Checkpoint(tmp_path / "c.json")
        ponto.iniciar(titulo)
        ponto.marcar_falha(titulo, "fornecedor nao localizado")
        assert ponto.estado_de(titulo) == Estado.FALHA

    def test_erro_depois_do_clique_salvar_permanece_bloqueado(self, tmp_path, titulo):
        ponto = Checkpoint(tmp_path / "c.json")
        ponto.iniciar(titulo)
        ponto.marcar_incerto_apos_salvar(titulo, "timeout na confirmacao")
        assert ponto.estado_de(titulo) == Estado.EM_EXECUCAO
        assert "depois do clique em Salvar" in ponto.obter(titulo).erro

    def test_confirmacao_manual_libera_somente_em_execucao_sem_id(self, tmp_path, titulo):
        ponto = Checkpoint(tmp_path / "c.json")
        ponto.iniciar(titulo)

        ponto.liberar_apos_confirmacao_manual(titulo, "ausencia conferida")

        item = ponto.obter(titulo)
        assert item.estado == Estado.LIBERADO_MANUALMENTE
        assert item.id_ongsys == ""
        assert ponto.precisam_de_conferencia() == []

    def test_item_com_id_nunca_pode_ser_liberado_manualmente(self, tmp_path, titulo):
        ponto = Checkpoint(tmp_path / "c.json")
        ponto.iniciar(titulo)
        ponto.marcar_salvo(titulo, "00013242", "RPA.11")

        with pytest.raises(ValueError, match="EM_EXECUCAO e sem ID"):
            ponto.liberar_apos_confirmacao_manual(titulo, "nao deveria")

    def test_liberado_manualmente_volta_a_fila_futura(self, tmp_path, titulo):
        ponto = Checkpoint(tmp_path / "c.json")
        ponto.iniciar(titulo)
        ponto.liberar_apos_confirmacao_manual(titulo, "ausencia conferida")
        resultado = montar(tmp_path / "fila.xlsx", [linha("FORNECEDOR TESTE", 1000.0)])

        fila, ignorados = _fila_de_execucao(
            resultado, ponto, config_de_teste(), limite=1
        )

        assert [item.chave for item in fila] == [titulo.chave]
        assert ignorados == []

    def test_tentativas_sao_contadas(self, tmp_path, titulo):
        ponto = Checkpoint(tmp_path / "c.json")
        ponto.iniciar(titulo)
        ponto.marcar_falha(titulo, "erro")
        ponto.iniciar(titulo)
        assert ponto.obter(titulo).tentativas == 2

    def test_itens_que_precisam_de_conferencia(self, tmp_path, titulo):
        ponto = Checkpoint(tmp_path / "c.json")
        ponto.iniciar(titulo)
        ponto.marcar_salvo(titulo, "00033191", "NF 1")
        assert len(ponto.precisam_de_conferencia()) == 1

    def test_detecta_mudanca_nos_dados_de_origem(self, tmp_path):
        ponto = Checkpoint(tmp_path / "c.json")
        antes = montar(tmp_path / "a.xlsx", [linha("F", 100.0)]).titulos[0]
        ponto.iniciar(antes)
        depois = montar(tmp_path / "b.xlsx", [linha("F", 250.0)]).titulos[0]
        assert ponto.dados_mudaram(depois)

    def test_dados_iguais_nao_acusam_mudanca(self, tmp_path):
        ponto = Checkpoint(tmp_path / "c.json")
        antes = montar(tmp_path / "a.xlsx", [linha("F", 100.0)]).titulos[0]
        ponto.iniciar(antes)
        igual = montar(tmp_path / "b.xlsx", [linha("F", 100.0)]).titulos[0]
        assert not ponto.dados_mudaram(igual)

    def test_importa_checkpoint_antigo_como_nao_auditado(self, tmp_path, titulo):
        """Lista simples do formato antigo não prova auditoria nenhuma."""
        caminho = tmp_path / "c.json"
        caminho.write_text(json.dumps([titulo.chave]), encoding="utf-8")
        assert Checkpoint(caminho).estado_de(titulo) == Estado.SALVO_NAO_AUDITADO

    def test_arquivo_corrompido_e_preservado(self, tmp_path):
        caminho = tmp_path / "c.json"
        caminho.write_text("{isso nao e json", encoding="utf-8")
        with pytest.raises(RuntimeError) as erro:
            Checkpoint(caminho)
        assert "corrompido" in str(erro.value)
        assert list(tmp_path.glob("*.corrompido_*.json"))

    def test_contagem_por_estado(self, tmp_path):
        ponto = Checkpoint(tmp_path / "c.json")
        um = montar(tmp_path / "a.xlsx", [linha("A", 100.0)]).titulos[0]
        dois = montar(tmp_path / "b.xlsx", [linha("B", 200.0)]).titulos[0]
        ponto.iniciar(um)
        ponto.marcar_salvo(um, "1", "NF 1")
        ponto.marcar_concluido(um, [])
        ponto.iniciar(dois)
        ponto.marcar_falha(dois, "erro")
        assert ponto.contagem_por_estado() == {Estado.CONCLUIDO: 1, Estado.FALHA: 1}


class TestRecuperacaoDeIdAposSucesso:
    def test_correspondencia_unica_protege_checkpoint_como_salvo(
        self, tmp_path, titulo, monkeypatch
    ):
        ponto = Checkpoint(tmp_path / "c.json")
        ponto.iniciar(titulo)
        ponto.marcar_falha(titulo, "timeout depois da tela verde")
        item = ponto.obter(titulo)

        class Bot:
            def ids_resultado_busca(self, _termo, _inicio=None, _fim=None):
                return ["00045678"]

        lido = {
            "fornecedor": titulo.fornecedor,
            "documento": titulo.documento,
            "emissao": titulo.emissao.strftime("%d/%m/%Y"),
            "vencimento": titulo.vencimento.strftime("%d/%m/%Y"),
            "efetivacao": titulo.efetivacao.strftime("%d/%m/%Y"),
            "valor": titulo.valor_br,
        }
        monkeypatch.setattr(A, "ler_titulo_da_tela", lambda *_a: lido)

        recuperados = _recuperar_ids_desconhecidos(
            Bot(), ponto, [item], {titulo.chave: titulo}, config_de_teste()
        )

        assert recuperados == ["00045678"]
        assert ponto.estado_de(titulo) == Estado.SALVO_NAO_AUDITADO
        assert ponto.obter(titulo).id_ongsys == "00045678"

    def test_duas_correspondencias_nao_adivinham_o_id(self, tmp_path, titulo, monkeypatch):
        ponto = Checkpoint(tmp_path / "c.json")
        ponto.iniciar(titulo)
        ponto.marcar_falha(titulo, "incerto")
        item = ponto.obter(titulo)

        class Bot:
            def ids_resultado_busca(self, _termo, _inicio=None, _fim=None):
                return ["00011111", "00022222"]

        lido = {
            "fornecedor": titulo.fornecedor,
            "documento": titulo.documento,
            "emissao": titulo.emissao.strftime("%d/%m/%Y"),
            "vencimento": titulo.vencimento.strftime("%d/%m/%Y"),
            "efetivacao": titulo.efetivacao.strftime("%d/%m/%Y"),
            "valor": titulo.valor_br,
        }
        monkeypatch.setattr(A, "ler_titulo_da_tela", lambda *_a: lido)

        recuperados = _recuperar_ids_desconhecidos(
            Bot(), ponto, [item], {titulo.chave: titulo}, config_de_teste()
        )

        assert recuperados == []
        assert ponto.estado_de(titulo) == Estado.FALHA
        assert ponto.obter(titulo).id_ongsys == ""

    def test_documento_com_sufixo_criado_pela_automacao_recupera_id(
        self, tmp_path, titulo, monkeypatch
    ):
        ponto = Checkpoint(tmp_path / "c.json")
        ponto.iniciar(titulo)
        ponto.marcar_falha(titulo, "timeout depois do salvamento")
        item = ponto.obter(titulo)

        class Bot:
            def ids_resultado_busca(self, _termo, _inicio=None, _fim=None):
                return ["00045678"]

        lido = {
            "fornecedor": titulo.fornecedor,
            "documento": f"{titulo.documento}.1",
            "emissao": titulo.emissao.strftime("%d/%m/%Y"),
            "vencimento": titulo.vencimento.strftime("%d/%m/%Y"),
            "efetivacao": titulo.efetivacao.strftime("%d/%m/%Y"),
            "valor": titulo.valor_br,
        }
        monkeypatch.setattr(A, "ler_titulo_da_tela", lambda *_a: lido)

        recuperados = _recuperar_ids_desconhecidos(
            Bot(), ponto, [item], {titulo.chave: titulo}, config_de_teste()
        )

        assert recuperados == ["00045678"]
        assert ponto.obter(titulo).documento_final == f"{titulo.documento}.1"


def _titulo_simples(**ajustes) -> Titulo:
    base = dict(
        chave="k", linhas_origem=[4], fornecedor="PAPELMAIS COMERCIO LTDA",
        documento="NF 100", documento_original="NF 100",
        emissao=date(2026, 6, 1), vencimento=date(2026, 6, 10), efetivacao=date(2026, 6, 10),
        valor=Decimal("1000.00"), conta_pagadora_id="6", conta_pagadora_nome="BANCO ITAU 12345-6 SEDE",
        tipo_despesa="Compra de Produto(s)", tipo_documento="Nota Fiscal",
        meio_pagamento="Internet Banking", tipo_pagamento="Boleto Simples de Outros Bancos",
        historico="MATERIAL DE ESCRITORIO",
        rateios=[Rateio(4, Decimal("1000.00"), "GESTAO ADM", "DESPESAS_ADMINISTRATIVAS",
                        "MATERIAL DE ESCRITORIO", "2.2.7.02", "Despesas ADM", Decimal("100"))],
    )
    base.update(ajustes)
    return Titulo(**base)


def _tela_correta() -> dict:
    return {
        "fornecedor": "PAPELMAIS COMERCIO LTDA",
        "documento": "NF 100",
        "emissao": "01/06/2026",
        "vencimento": "10/06/2026",
        "efetivacao": "10/06/2026",
        "valor": "1.000,00",
        "conta_pagadora": "BANCO ITAU 12345-6 SEDE",
        "tipo_despesa": "Compra de Produto(s)",
        "data_pagamento": "",
        "situacao": "EM ABERTO",
        "rateios": [{
            "categoria": "MATERIAL DE ESCRITORIO",
            "centro_custo": "GESTAO ADM",
            "subprojeto": "2.2.7.02 - MATERIAL DE ESCRITORIO",
            "atividade": "Despesas ADM",
            "valor": "1.000,00",
            "percentual": "100,0000",
        }],
    }


TOLERANCIA = Decimal("0.01")


class TestAuditoria:
    def test_titulo_correto_nao_gera_divergencia(self):
        assert A.auditar_dados(_titulo_simples(), _tela_correta(), TOLERANCIA) == []

    def test_codigo_de_barras_informado_e_conferido(self):
        titulo = _titulo_simples(codigo_barras="123.456 789")
        tela = _tela_correta() | {"codigo_barras": "123456789"}
        assert A.auditar_dados(titulo, tela, TOLERANCIA) == []

    def test_codigo_de_barras_ausente_e_apontado(self):
        titulo = _titulo_simples(codigo_barras="123.456 789")
        tela = _tela_correta() | {"codigo_barras": ""}
        divergencias = A.auditar_dados(titulo, tela, TOLERANCIA)
        assert any(d.campo == "Código de barras" for d in divergencias)

    def test_codigo_vazio_na_planilha_nao_e_auditado(self):
        tela = _tela_correta() | {"codigo_barras": "qualquer valor antigo"}
        assert A.auditar_dados(_titulo_simples(), tela, TOLERANCIA) == []

    def test_detecta_valor_diferente(self):
        tela = _tela_correta() | {"valor": "900,00"}
        divergencias = A.auditar_dados(_titulo_simples(), tela, TOLERANCIA)
        assert any(d.campo == "Valor bruto" for d in divergencias)

    def test_detecta_data_diferente(self):
        tela = _tela_correta() | {"vencimento": "15/06/2026"}
        assert any(d.campo == "Data de vencimento"
                   for d in A.auditar_dados(_titulo_simples(), tela, TOLERANCIA))

    def test_codigo_de_subprojeto_errado_e_apontado(self):
        """O caso do hortifruti: Copa e Cozinha virou Estacionamento."""
        tela = _tela_correta()
        tela["rateios"][0]["subprojeto"] = "12.2.7.02 - ESTACIONAMENTO"
        divergencias = A.auditar_dados(_titulo_simples(), tela, TOLERANCIA)
        alvo = [d for d in divergencias if "Subprojeto" in d.campo]
        assert alvo and "CODIGO DIFERENTE" in alvo[0].acao

    def test_subprojeto_vazio_e_apontado_como_pendencia(self):
        tela = _tela_correta()
        tela["rateios"][0]["subprojeto"] = ""
        alvo = [d for d in A.auditar_dados(_titulo_simples(), tela, TOLERANCIA)
                if "Subprojeto" in d.campo]
        assert alvo and alvo[0].encontrado == "[vazio]"

    def test_subprojeto_nao_esperado_e_vazio_confere(self):
        titulo = _titulo_simples()
        titulo.rateios[0].subprojeto = ""
        tela = _tela_correta()
        tela["rateios"][0]["subprojeto"] = ""
        assert A.auditar_dados(titulo, tela, TOLERANCIA) == []

    def test_baixa_detectada_pela_data_de_pagamento(self):
        """§17/§19: título com baixa é alarme."""
        tela = _tela_correta() | {"data_pagamento": "20/06/2026"}
        alvo = [d for d in A.auditar_dados(_titulo_simples(), tela, TOLERANCIA)
                if d.campo == "Situação de baixa"]
        assert alvo and "ATENCAO" in alvo[0].acao

    def test_baixa_detectada_pela_situacao(self):
        tela = _tela_correta() | {"situacao": "PAGO"}
        assert any(d.campo == "Situação de baixa"
                   for d in A.auditar_dados(_titulo_simples(), tela, TOLERANCIA))

    def test_linha_de_rateio_faltando(self):
        titulo = _titulo_simples()
        titulo.rateios.append(Rateio(5, Decimal("500.00"), "ESCOLAS", "T", "C", "3.3.1.08", "", Decimal("50")))
        divergencias = A.auditar_dados(titulo, _tela_correta(), TOLERANCIA)
        assert any("Quantidade de linhas" in d.campo for d in divergencias)
        assert any(d.encontrado == "[linha ausente]" for d in divergencias)

    def test_centro_de_custo_trocado(self):
        tela = _tela_correta()
        tela["rateios"][0]["centro_custo"] = "ESCOLAS"
        assert any("Centro de custo" in d.campo
                   for d in A.auditar_dados(_titulo_simples(), tela, TOLERANCIA))

    def test_documento_com_variacao_numerica_controlada_e_aceito(self):
        tela = _tela_correta() | {"documento": "NF 100.1"}
        assert not any(d.campo == "Número do documento"
                       for d in A.auditar_dados(_titulo_simples(), tela, TOLERANCIA))

    def test_documento_com_variacao_livre_e_apontado(self):
        tela = _tela_correta() | {"documento": "OUTRO"}
        assert any(d.campo == "Número do documento"
                   for d in A.auditar_dados(_titulo_simples(), tela, TOLERANCIA))

    def test_prefixos_de_codigo_do_ongsys_nao_sao_divergencias(self):
        tela = _tela_correta() | {
            "fornecedor": "3939 - PAPELMAIS COMERCIO LTDA",
            "conta_pagadora": "6 - BANCO ITAU 12345-6 SEDE (Itaú Unibanco S.A.)",
        }
        tela["rateios"][0] |= {
            "categoria": "2.5.1.06.006 - MATERIAL DE ESCRITORIO",
            "centro_custo": "10.2.01.01 - GESTÃO ADM",
        }
        assert A.auditar_dados(_titulo_simples(), tela, TOLERANCIA) == []

    def test_opcao_parecida_mas_diferente_continua_sendo_divergencia(self):
        tela = _tela_correta()
        tela["rateios"][0]["categoria"] = "2.5.1.06.006 - OUTRO MATERIAL"
        assert any("Categoria/Conta" in d.campo
                   for d in A.auditar_dados(_titulo_simples(), tela, TOLERANCIA))


class TestFiltroPrevioDaLinha:
    def test_linha_do_rpa_correto_passa_para_abertura(self):
        titulo = _titulo_simples(
            fornecedor="DENISE CRISTINA DA ROCHA BARBOSA",
            documento="RPA",
            emissao=date(2026, 6, 16),
            vencimento=date(2026, 6, 16),
            efetivacao=date(2026, 6, 16),
            valor=Decimal("300.00"),
        )
        linha = (
            "00013242 RPA.11 DENISE CRISTINA DA ROCHA BARBOSA "
            "20-Aug-2026 16-Jun-2026 16-Jun-2026 300,00 CP031204"
        )
        assert _motivos_linha_incompativel(titulo, linha) == []

    def test_linha_baixada_de_outro_documento_e_descartada(self):
        """A coluna Dcto mostra o tipo do título, não o número do documento.
        A pré-validação de linha não compara documento — essa verificação
        é feita ao abrir o formulário. A linha ainda é descartada pelas
        datas diferentes (emissão e vencimento).
        """
        titulo = _titulo_simples(
            fornecedor="DENISE CRISTINA DA ROCHA BARBOSA",
            documento="RPA",
            emissao=date(2026, 6, 2),
            vencimento=date(2026, 6, 2),
            efetivacao=date(2026, 6, 2),
            valor=Decimal("300.00"),
        )
        linha = (
            "00032201 06_26 DENISE CRISTINA DA ROCHA BARBOSA LIMPEZA "
            "25-Jun-2026 24-Jun-2026 29-Jun-2026 300,00 CP030193 "
            "Enviado Sim BANCO ITAU 29-Jun-2026"
        )
        motivos = _motivos_linha_incompativel(titulo, linha)
        assert "documento visivel diferente" not in motivos
        assert "emissao visivel diferente" in motivos
        assert "vencimento visivel diferente" in motivos

    def test_regressao_caju_coluna_dcto_mostra_projetado(self):
        """Regressão BENEFIX: a coluna Dcto mostra 'Projetado', não 'BL.1'.

        A linha com valor, emissão e vencimento corretos deve passar a
        pré-validação, permitindo que o formulário seja aberto para a
        validação documental completa.
        """
        titulo = _titulo_simples(
            fornecedor="EMPRESA EXEMPLO DE BENEFICIOS - BENEFIX",
            documento="BL",
            emissao=date(2026, 6, 2),
            vencimento=date(2026, 6, 16),
            efetivacao=date(2026, 6, 16),
            valor=Decimal("139863.76"),
        )
        linha_tabela = (
            "00013048 Projetado EMPRESA EXEMPLO DE BENEFICIOS E PAGAMENTOS LTDA "
            "02/06/2026 16/06/2026 16/06/2026 139.863,76 CP030994"
        )
        assert _motivos_linha_incompativel(titulo, linha_tabela) == []

    def test_regressao_caju_formulario_bl_rejeitado(self):
        """Regressão BENEFIX: formulário com documento 'BL' é o original e
        deve ser rejeitado quando documento_final='BL.1'.
        """
        from ongsys.cli import _motivos_titulo_incompativel

        titulo = _titulo_simples(
            fornecedor="EMPRESA EXEMPLO DE BENEFICIOS E PAGAMENTOS LTDA",
            documento="BL",
            emissao=date(2026, 6, 2),
            vencimento=date(2026, 6, 16),
            efetivacao=date(2026, 6, 16),
            valor=Decimal("139863.76"),
        )
        lido = {
            "fornecedor": "EMPRESA EXEMPLO DE BENEFICIOS E PAGAMENTOS LTDA",
            "documento": "BL",
            "emissao": "02/06/2026",
            "vencimento": "16/06/2026",
            "efetivacao": "16/06/2026",
            "valor": "139.863,76",
        }
        motivos = _motivos_titulo_incompativel(
            titulo, lido, TOLERANCIA, documento_final="BL.1"
        )
        assert any("documento" in m for m in motivos)

    def test_regressao_caju_formulario_bl1_aceito(self):
        """Regressão BENEFIX: formulário com documento 'BL.1' e demais campos
        corretos deve ser aceito quando documento_final='BL.1'.
        """
        from ongsys.cli import _motivos_titulo_incompativel

        titulo = _titulo_simples(
            fornecedor="EMPRESA EXEMPLO DE BENEFICIOS E PAGAMENTOS LTDA",
            documento="BL",
            emissao=date(2026, 6, 2),
            vencimento=date(2026, 6, 16),
            efetivacao=date(2026, 6, 16),
            valor=Decimal("139863.76"),
        )
        lido = {
            "fornecedor": "EMPRESA EXEMPLO DE BENEFICIOS E PAGAMENTOS LTDA",
            "documento": "BL.1",
            "emissao": "02/06/2026",
            "vencimento": "16/06/2026",
            "efetivacao": "16/06/2026",
            "valor": "139.863,76",
        }
        motivos = _motivos_titulo_incompativel(
            titulo, lido, TOLERANCIA, documento_final="BL.1"
        )
        assert motivos == []

    def test_acento_nao_conta_como_divergencia(self):
        tela = _tela_correta() | {"fornecedor": "PAPELMAIS COMÉRCIO LTDA"}
        assert not any(d.campo == "Fornecedor"
                       for d in A.auditar_dados(_titulo_simples(), tela, TOLERANCIA))

    def test_atividade_vazia_na_tela_nao_e_divergencia(self):
        """§14: atividade em branco é aceitável."""
        tela = _tela_correta()
        tela["rateios"][0]["atividade"] = ""
        assert A.auditar_dados(_titulo_simples(), tela, TOLERANCIA) == []

    def test_atividade_trocada_e_divergencia(self):
        tela = _tela_correta()
        tela["rateios"][0]["atividade"] = "OUTRA COISA"
        assert any("Atividade" in d.campo
                   for d in A.auditar_dados(_titulo_simples(), tela, TOLERANCIA))

    def test_relatorio_de_titulo_conferido(self):
        linhas = A.linhas_de_relatorio(_titulo_simples(), "00033191", [])
        assert linhas[0]["situacao"] == "CONFERIDO" and linhas[0]["confere"] == "SIM"

    def test_relatorio_lista_cada_divergencia(self):
        tela = _tela_correta() | {"valor": "1,00", "documento": "OUTRO"}
        divergencias = A.auditar_dados(_titulo_simples(), tela, TOLERANCIA)
        linhas = A.linhas_de_relatorio(_titulo_simples(), "00033191", divergencias)
        assert len(linhas) == len(divergencias)
        assert all(l["confere"] == "NAO" for l in linhas)


class TestAcoesProibidas:
    """§17: nada de baixa, pagamento, remessa ou autorização."""

    from ongsys import seletores as S

    @pytest.mark.parametrize("alvo", [
        "#btn_informar_pagamento", "#btn_baixar", "#field_data_pagamento",
        "#btn_remessa", "#btn_autorizar",
    ])
    def test_seletores_proibidos_sao_recusados(self, alvo):
        from ongsys.seletores import AcaoProibida, conferir_permitido
        with pytest.raises(AcaoProibida):
            conferir_permitido(alvo)

    @pytest.mark.parametrize("alvo", [
        "Informar dados de pagamento", "Dar baixa no titulo", "Enviar para autorizacao",
        "marcar como pago", "REMESSA bancaria",
    ])
    def test_textos_proibidos_sao_recusados(self, alvo):
        from ongsys.seletores import AcaoProibida, conferir_permitido
        with pytest.raises(AcaoProibida):
            conferir_permitido(alvo)

    @pytest.mark.parametrize("alvo", [
        "#btn_salvar", "#field_fornecedor", "#field_valor_despesa", "salvar titulo",
        "#field_data_vencimento", "#field_data_efetivacao",
    ])
    def test_acoes_legitimas_passam(self, alvo):
        from ongsys.seletores import conferir_permitido
        conferir_permitido(alvo)  # não deve levantar
