"""Testes das comparações de texto, dinheiro e códigos."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import apoio  # noqa: F401  (ajusta o sys.path)
import pytest

from ongsys.normalizacao import (
    codigo_confere,
    codigos_em,
    competencia_br,
    competencia_iso,
    contem_palavra,
    data_br,
    data_iso,
    dinheiro,
    documento_equivalente,
    documento_permite_projetado,
    formatar_dinheiro_br,
    formatar_percentual_br,
    nome_da_opcao,
    normalizar_documento,
    para_data,
    para_decimal,
    para_percentual,
    periodo_mensal,
    semelhanca,
    sem_acentos,
)


class TestNomeDaOpcao:
    """Bug real do piloto: 'LIMPEZA' empatava entre 'LIMPEZA' e 'MATERIAL DE

    LIMPEZA' porque as duas contêm a palavra inteira. Extrair só o nome da
    opção (sem código nem sufixo) permite comparação exata.
    """

    def test_remove_codigo_e_sufixo(self):
        assert nome_da_opcao("2.5.1.04.007 - LIMPEZA (A)") == "LIMPEZA"

    def test_resolve_o_empate_real_do_piloto(self):
        assert nome_da_opcao("2.5.1.04.007 - LIMPEZA (A)") == "LIMPEZA"
        assert nome_da_opcao("2.5.1.06.006 - MATERIAL DE LIMPEZA (A)") == "MATERIAL DE LIMPEZA"

    def test_nome_com_varias_palavras(self):
        assert nome_da_opcao("2.2.1.05 - COPA E COZINHA (A)") == "COPA E COZINHA"

    def test_sem_sufixo_entre_parenteses(self):
        assert nome_da_opcao("6 - BANCO ITAU 12345-6 SEDE") == "BANCO ITAU 12345-6 SEDE"

    def test_sem_codigo_inicial(self):
        assert nome_da_opcao("Selecione...") == "Selecione..."

    def test_texto_vazio(self):
        assert nome_da_opcao("") == ""
        assert nome_da_opcao(None) == ""


class TestDocumentoEquivalente:
    def test_documento_identico_ignora_formatacao(self):
        assert documento_equivalente("NF 100", "nf-100")

    def test_variacao_numerica_controlada_e_aceita_quando_autorizada(self):
        assert documento_equivalente(
            "RPA", "RPA.1", aceitar_variacao_numerica=True
        )

    def test_variacao_nao_e_aceita_sem_autorizacao_explicita(self):
        assert not documento_equivalente("RPA", "RPA.1")

    def test_sufixo_livre_nunca_e_aceito_como_variacao(self):
        assert not documento_equivalente(
            "RPA", "RPA.OUTRO", aceitar_variacao_numerica=True
        )


class TestCodigoSubprojeto:
    """§13: o código selecionado precisa ser exatamente o esperado."""

    def test_aceita_codigo_identico(self):
        assert codigo_confere("2.2.1.05", "2.2.1.05 - COPA E COZINHA")

    def test_rejeita_codigo_com_prefixo_diferente(self):
        # O incidente relatado no handoff: 'Copa e Cozinha' virou 'Estacionamento'
        # porque '2.2.1.05' está contido em '12.2.1.05'.
        assert not codigo_confere("2.2.1.05", "12.2.1.05 - ESTACIONAMENTO")

    def test_rejeita_codigo_com_digito_a_mais(self):
        assert not codigo_confere("2.2.1.05", "2.2.1.050 - OUTRO")

    def test_rejeita_codigo_truncado(self):
        assert not codigo_confere("2.2.1.05", "2.2.1.0 - INCOMPLETO")

    def test_rejeita_descricao_parecida_sem_o_codigo(self):
        assert not codigo_confere("2.2.4.02", "COPA E COZINHA")

    def test_codigo_vazio_nunca_confere(self):
        assert not codigo_confere("", "2.2.1.05 - QUALQUER")

    @pytest.mark.parametrize("codigo", ["2.2.1.05", "6.2.5.01", "3.3.1.08", "2.2.4.02", "2.2.7.02"])
    def test_codigos_conhecidos_do_handoff(self, codigo):
        assert codigo_confere(codigo, f"{codigo} - DESCRICAO QUALQUER")

    def test_extrai_todos_os_codigos_da_opcao(self):
        assert codigos_em("2.2.1.05 - SALA 3.1 ANEXO") == ["2.2.1.05", "3.1"]

    def test_zeros_a_esquerda_sao_significativos(self):
        assert not codigo_confere("2.2.1.5", "2.2.1.05 - COPA E COZINHA")


class TestDinheiro:
    def test_remove_ruido_de_float_da_planilha(self):
        assert dinheiro(Decimal(str(2555.6699999999996))) == Decimal("2555.67")

    def test_soma_de_valores_com_ruido_fecha_exata(self):
        brutos = [5332.37, 4855.06, 5959.19, 2555.6699999999996]
        total = sum((dinheiro(Decimal(str(v))) for v in brutos), Decimal("0"))
        assert total == Decimal("18702.29")

    @pytest.mark.parametrize("entrada,esperado", [
        (Decimal("139863.76"), "139.863,76"),
        (Decimal("1000000"), "1.000.000,00"),
        (Decimal("0.5"), "0,50"),
        (Decimal("100"), "100,00"),
        (Decimal("-45.9"), "-45,90"),
    ])
    def test_formato_brasileiro(self, entrada, esperado):
        assert formatar_dinheiro_br(entrada) == esperado

    def test_percentual_usa_virgula(self):
        assert formatar_percentual_br(Decimal("5.1105")) == "5,1105"

    @pytest.mark.parametrize("entrada,esperado", [
        ("1.234,56", Decimal("1234.56")),
        ("1234.56", Decimal("1234.56")),
        ("R$ 1.234,56", Decimal("1234.56")),
        (1234.5, Decimal("1234.50")),
        ("", None),
        (None, None),
        ("texto", None),
    ])
    def test_leitura_de_valores(self, entrada, esperado):
        assert para_decimal(entrada) == esperado

    def test_booleano_nao_vira_valor(self):
        # True viraria Decimal('1') e criaria um título de R$ 1,00 do nada.
        assert para_decimal(True) is None

    @pytest.mark.parametrize("entrada,esperado", [
        ("100,00 %", Decimal("100.0000")),
        ("33,3333%", Decimal("33.3333")),
        ("12.5", Decimal("12.5000")),
        ("", None),
        (None, None),
        (True, None),
    ])
    def test_leitura_de_percentual_preserva_quatro_casas(self, entrada, esperado):
        assert para_percentual(entrada) == esperado


class TestDatas:
    @pytest.mark.parametrize("entrada", ["01/06/2026", "2026-06-01", "01.06.2026"])
    def test_formatos_aceitos(self, entrada):
        assert para_data(entrada).isoformat() == "2026-06-01"

    def test_texto_invalido_nao_estoura(self):
        assert para_data("sem data") is None
        assert para_data(None) is None

    def test_periodo_mensal_expande_um_dia_para_o_mes_inteiro(self):
        assert periodo_mensal(date(2026, 6, 16)) == (
            date(2026, 6, 1), date(2026, 6, 30)
        )

    def test_periodo_mensal_respeita_fevereiro_bissexto(self):
        assert periodo_mensal(date(2028, 2, 10)) == (
            date(2028, 2, 1), date(2028, 2, 29)
        )


class TestFormatoDeDataParaPreenchimento:
    """Bug real do piloto: <input type="date"> rejeita dd/mm/aaaa como

    "valor malformado" — o valor interno sempre precisa ser AAAA-MM-DD,
    mesmo a tela mostrando a data em formato brasileiro."""

    def test_data_iso_usa_traco_e_ano_primeiro(self):
        assert data_iso(date(2026, 6, 1)) == "2026-06-01"

    def test_data_br_continua_com_barra_para_relatorios(self):
        """data_br é para exibição/relatório, não para preencher o formulário."""
        assert data_br(date(2026, 6, 1)) == "01/06/2026"

    def test_competencia_iso_e_ano_mes_sem_dia(self):
        assert competencia_iso(date(2026, 8, 15)) == "2026-08"

    def test_competencia_br_continua_mes_barra_ano(self):
        assert competencia_br(date(2026, 8, 15)) == "08/2026"

    def test_data_iso_bate_com_o_que_type_date_aceita(self):
        """Um <input type="date"> só aceita exatamente 10 caracteres AAAA-MM-DD."""
        import re
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", data_iso(date(2026, 12, 31)))

    def test_competencia_iso_bate_com_o_que_type_month_aceita(self):
        import re
        assert re.fullmatch(r"\d{4}-\d{2}", competencia_iso(date(2026, 1, 5)))


class TestComparacaoDeNomes:
    def test_ignora_acentos_e_caixa(self):
        assert sem_acentos("Relações Institucionais") == "RELACOES INSTITUCIONAIS"

    def test_cedilha_vira_c(self):
        assert sem_acentos("Conceição") == "CONCEICAO"

    def test_nome_contido_tem_semelhanca_total(self):
        assert semelhanca("PAPELMAIS", "PAPELMAIS COMERCIO E INDUSTRIA LTDA") == 1.0

    def test_nome_totalmente_diferente_tem_semelhanca_baixa(self):
        assert semelhanca("APITOOL TEAM", "PADARIA CENTRAL LTDA") < 0.5

    def test_palavras_curtas_nao_inflam_a_semelhanca(self):
        # 'DE' e 'DA' não podem valer como coincidência.
        assert semelhanca("CASA DE FERRAMENTAS", "LOJA DA ESQUINA") == 0.0

    def test_documento_normalizado_ignora_pontuacao(self):
        assert normalizar_documento("NF 1.912-307") == normalizar_documento("nf1912307")


class TestFronteiraDePalavra:
    def test_retencao_pcc_nao_pega_fornecedor_parecido(self):
        assert not contem_palavra("PCC", "PCCOMEX SERVICOS LTDA")

    def test_retencao_pcc_pega_o_termo_isolado(self):
        assert contem_palavra("PCC", "RETENCAO PCC SOBRE NF")

    def test_irrf_isolado(self):
        assert contem_palavra("IRRF", "IRRF FOLHA PGTO 2026")
        assert not contem_palavra("IRRF", "IRRFX CONSULTORIA")


class TestDocumentoPermiteProjetado:
    """Só documento de nota fiscal disputa um lançamento Projetado."""

    @pytest.mark.parametrize(
        "documento",
        ["NF 45", "NF 3942", "FATURA 79055", "RECIBO", "BOLETO.14", "nf 45", "  NF 45 "],
    )
    def test_documento_de_nota_permite(self, documento):
        assert documento_permite_projetado(documento)

    @pytest.mark.parametrize(
        "documento",
        ["09_26", "08_26.2", "01.07_26", "30.07_26.1", "31.08", "05.1", ""],
    )
    def test_documento_de_periodo_ou_data_nao_permite(self, documento):
        assert not documento_permite_projetado(documento)

    def test_documento_ausente_nao_permite(self):
        assert not documento_permite_projetado(None)
        assert not documento_permite_projetado("   ")
