"""Testes de agrupamento, exclusão, percentuais e bloqueios."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import apoio
import pytest
from apoio import JA_LANCADA, PENDENTE, config_de_teste, linha, montar

from ongsys import regras as R
from ongsys.modelos import Estado, Rateio


class TestAgrupamento:
    """§5: linhas consecutivas do mesmo título formam vários rateios."""

    def test_quatro_linhas_do_mesmo_documento_viram_um_titulo(self, tmp_path):
        linhas = [
            linha("CAIXA DE DESIGN", 250.00, documento="NF 500", cc="ESCOLAS"),
            linha("CAIXA DE DESIGN", 250.00, documento="NF 500", cc="PLANEJAMENTO"),
            linha("CAIXA DE DESIGN", 250.00, documento="NF 500", cc="GESTAO ADM"),
            linha("CAIXA DE DESIGN", 250.00, documento="NF 500", cc="RELACOES"),
        ]
        resultado = montar(tmp_path / "a.xlsx", linhas)
        assert len(resultado.titulos) == 1
        assert len(resultado.titulos[0].rateios) == 4
        assert resultado.titulos[0].valor == Decimal("1000.00")

    def test_uma_linha_vira_titulo_com_um_rateio(self, tmp_path):
        resultado = montar(tmp_path / "b.xlsx", [linha("FORNECEDOR UNICO", 300.00)])
        assert len(resultado.titulos) == 1
        assert len(resultado.titulos[0].rateios) == 1

    def test_documentos_diferentes_nao_se_juntam(self, tmp_path):
        linhas = [
            linha("MESMO FORNECEDOR", 100.00, documento="NF 1"),
            linha("MESMO FORNECEDOR", 100.00, documento="NF 2"),
        ]
        assert len(montar(tmp_path / "c.xlsx", linhas).titulos) == 2

    def test_ocorrencias_distantes_iguais_viram_titulos_distintos(self, tmp_path):
        linhas = [
            linha(
                "AMAZON", 100.00, documento="10.07",
                emissao=date(2026, 7, 1),
            ),
            linha("OUTRO FORNECEDOR", 50.00, documento="10.07"),
            linha(
                "AMAZON", 200.00, documento="10.07",
                emissao=date(2026, 7, 2),
            ),
        ]
        titulos = montar(tmp_path / "amazon_blocos.xlsx", linhas).titulos
        amazon = [t for t in titulos if t.fornecedor == "AMAZON"]
        assert len(amazon) == 2
        assert [t.valor for t in amazon] == [Decimal("100.00"), Decimal("200.00")]
        assert [t.emissao for t in amazon] == [date(2026, 7, 1), date(2026, 7, 2)]
        assert len({t.chave for t in amazon}) == 2

    def test_vencimentos_diferentes_nao_se_juntam(self, tmp_path):
        linhas = [
            linha("FORNECEDOR", 100.00, vencimento=date(2026, 6, 10)),
            linha("FORNECEDOR", 100.00, vencimento=date(2026, 6, 20)),
        ]
        assert len(montar(tmp_path / "d.xlsx", linhas).titulos) == 2

    def test_mesma_categoria_nao_consolida_linhas_distintas(self, tmp_path):
        # §5: centros de custo diferentes precisam continuar como linhas separadas.
        linhas = [
            linha("FORNECEDOR", 100.00, cc="ESCOLAS", categoria="SERVICOS"),
            linha("FORNECEDOR", 200.00, cc="PLANEJAMENTO", categoria="SERVICOS"),
        ]
        titulos = montar(tmp_path / "e.xlsx", linhas).titulos
        assert len(titulos) == 1
        assert [r.centro_custo for r in titulos[0].rateios] == ["ESCOLAS", "PLANEJAMENTO"]

    def test_documento_vazio_usa_a_data(self, tmp_path):
        resultado = montar(
            tmp_path / "f.xlsx",
            [linha("FORNECEDOR SEM DOCUMENTO", 50.00, documento="", pagamento=date(2026, 6, 2))],
        )
        assert resultado.titulos[0].documento == "02.06_26"

    def test_iof_da_linha_abaixo_entra_no_mesmo_titulo_em_rateio_separado(self, tmp_path):
        linhas = [
            linha(
                "HOSTIFY.COM", 4802.61, documento="05.05",
                cc="PLANEJAMENTO", categoria="LICENCA DE USO",
            ),
            linha(
                "HOSTIFY.COM (IOF)", 168.09, documento="05.05",
                cc="PLANEJAMENTO", categoria="LICENCA DE USO",
            ),
        ]
        resultado = montar(tmp_path / "iof.xlsx", linhas, conta="63")
        assert len(resultado.titulos) == 1
        titulo = resultado.titulos[0]
        assert titulo.fornecedor == "HOSTIFY.COM"
        assert titulo.valor == Decimal("4970.70")
        assert [r.valor for r in titulo.rateios] == [
            Decimal("4802.61"), Decimal("168.09")
        ]
        assert any("IOF" in alerta for alerta in titulo.alertas)

    def test_iof_sem_linha_principal_nao_e_lancado_sozinho(self, tmp_path):
        resultado = montar(
            tmp_path / "iof_orfao.xlsx",
            [linha("HOSTIFY.COM (IOF)", 168.09, documento="05.05")],
            conta="63",
        )
        assert resultado.titulos == []
        assert any("IOF sem uma linha principal" in e.motivo for e in resultado.exclusoes)


class TestBloqueios:
    def test_emissoes_divergentes_usam_a_primeira_linha(self, tmp_path):
        """Título rateado com emissões diferentes: não bloqueia (orientação do
        usuário). Usa a data da PRIMEIRA linha e registra alerta."""
        linhas = [
            linha("GFR- FGTS", 5332.37, documento="06_26", emissao=date(2026, 6, 8)),
            linha("GFR- FGTS", 4855.06, documento="06_26", emissao=date(2026, 6, 15)),
        ]
        titulo = montar(tmp_path / "g.xlsx", linhas).titulos[0]
        assert not titulo.bloqueado
        assert titulo.emissao == date(2026, 6, 8)
        assert any(
            "08/06/2026" in a and "15/06/2026" in a for a in titulo.alertas
        )

    def test_emissoes_iguais_nao_bloqueiam(self, tmp_path):
        linhas = [
            linha("FORNECEDOR", 100.00, emissao=date(2026, 6, 8)),
            linha("FORNECEDOR", 200.00, emissao=date(2026, 6, 8)),
        ]
        assert not montar(tmp_path / "h.xlsx", linhas).titulos[0].bloqueado

    def test_sem_centro_de_custo_bloqueia(self, tmp_path):
        """§15: centro de custo é essencial."""
        titulo = montar(tmp_path / "i.xlsx", [linha("FORNECEDOR", 100.00, cc="")]).titulos[0]
        assert titulo.bloqueado
        assert "centro de custo" in " ".join(titulo.bloqueios)

    def test_sem_categoria_bloqueia(self, tmp_path):
        titulo = montar(tmp_path / "j.xlsx", [linha("FORNECEDOR", 100.00, categoria="")]).titulos[0]
        assert titulo.bloqueado

    def test_sem_codigo_alerta_mas_nao_bloqueia(self, tmp_path):
        """§13: código ausente deixa o Subprojeto vazio, sem travar."""
        titulo = montar(tmp_path / "k.xlsx", [linha("FORNECEDOR", 100.00, codigo="")]).titulos[0]
        assert not titulo.bloqueado
        assert any("código de despesa" in a for a in titulo.alertas)

    def test_atividade_vazia_nao_bloqueia(self, tmp_path):
        """§14."""
        titulo = montar(tmp_path / "l.xlsx", [linha("FORNECEDOR", 100.00, atividade="")]).titulos[0]
        assert not titulo.bloqueado


class TestExclusoes:
    """§6: cada descarte precisa aparecer com o motivo."""

    @pytest.mark.parametrize("fornecedor", [
        "TRANSFERENCIA ENTRE CONTAS", "RECARGA CARTAO PREPAGO", "APLICACAO CDB",
        "RESGATE APLICACAO", "RENDIMENTO APLICACAO", "RECEBIMENTO DE RECURSOS",
        "TARIFA BANCARIA",
    ])
    def test_movimentacoes_que_nao_sao_conta_a_pagar(self, tmp_path, fornecedor):
        resultado = montar(tmp_path / "m.xlsx", [linha(fornecedor, 1000.00)])
        assert resultado.titulos == []
        assert len(resultado.exclusoes) == 1

    def test_retencao_irrf_excluida_com_motivo(self, tmp_path):
        resultado = montar(
            tmp_path / "n.xlsx",
            [linha("IRRF FOLHA PGTO", 500.00, titulo_categoria="ENCARGOS_SOCIAIS")],
        )
        assert resultado.titulos == []
        assert "IRRF" in resultado.exclusoes[0].motivo

    def test_entrada_financeira_excluida(self, tmp_path):
        resultado = montar(tmp_path / "o.xlsx", [linha("DOADOR", None, entrada=5000.00)])
        assert resultado.titulos == []
        assert "Entrada financeira" in resultado.exclusoes[0].motivo

    @pytest.mark.parametrize("valor", [0, -100.0])
    def test_valor_zero_ou_negativo_excluido(self, tmp_path, valor):
        resultado = montar(tmp_path / "p.xlsx", [linha("FORNECEDOR", valor)])
        assert resultado.titulos == []

    def test_linha_sem_fornecedor_excluida(self, tmp_path):
        resultado = montar(tmp_path / "q.xlsx", [linha("", 100.00)])
        assert resultado.titulos == []

    def test_exclusao_registra_a_linha_da_planilha(self, tmp_path):
        resultado = montar(
            tmp_path / "r.xlsx",
            [linha("FORNECEDOR BOM", 100.00), linha("APLICACAO CDB", 900.00)],
        )
        assert len(resultado.titulos) == 1
        assert resultado.exclusoes[0].linha == 5  # cabeçalho na 3, dados a partir da 4


class TestCores:
    """§4: a cor decide o que já foi lançado."""

    def test_apenas_linhas_com_cor_pendente_entram(self, tmp_path):
        linhas = [linha("PENDENTE SA", 100.00), linha("JA LANCADO SA", 200.00)]
        resultado = montar(tmp_path / "s.xlsx", linhas, cores=[PENDENTE, JA_LANCADA])
        assert [t.fornecedor for t in resultado.titulos] == ["PENDENTE SA"]

    def test_linha_ja_lancada_aparece_na_exclusao(self, tmp_path):
        resultado = montar(
            tmp_path / "t.xlsx",
            [linha("PENDENTE SA", 100.00), linha("JA LANCADO SA", 200.00)],
            cores=[PENDENTE, JA_LANCADA],
        )
        motivos = [e.motivo for e in resultado.exclusoes]
        assert any("já lançada" in m for m in motivos)

    def test_arquivo_sem_a_cor_esperada_interrompe(self, tmp_path):
        """Nunca assumir que todas as linhas são pendentes."""
        with pytest.raises(R.ErroRegras) as erro:
            montar(tmp_path / "u.xlsx", [linha("FORNECEDOR", 100.00)], cores=[JA_LANCADA])
        assert "cor de pendente" in str(erro.value)

    def test_contagem_por_cor_no_resultado(self, tmp_path):
        resultado = montar(
            tmp_path / "v.xlsx",
            [linha("A", 100.0), linha("B", 100.0), linha("C", 100.0)],
            cores=[PENDENTE, PENDENTE, JA_LANCADA],
        )
        assert resultado.linhas_por_cor_incluidas == 2
        assert resultado.linhas_por_cor_excluidas == 1


class TestPercentuais:
    """§12: o rateio precisa fechar exatamente 100%."""

    @pytest.mark.parametrize("quantidade", [1, 2, 3, 7, 35, 60, 100])
    def test_soma_fecha_cem_por_cento(self, tmp_path, quantidade):
        linhas = [linha("FORNECEDOR", 33.33, documento="NF 9") for _ in range(quantidade)]
        titulo = montar(tmp_path / f"w{quantidade}.xlsx", linhas).titulos[0]
        assert sum((r.percentual for r in titulo.rateios), Decimal("0")) == Decimal("100")

    def test_soma_dos_valores_bate_com_o_titulo(self, tmp_path):
        valores = [10684.31, 5332.37, 4855.06, 5959.19, 2555.67, 99.01, 0.03]
        linhas = [linha("BENEFIX", v, documento="BL") for v in valores]
        titulo = montar(tmp_path / "x.xlsx", linhas).titulos[0]
        assert sum((r.valor for r in titulo.rateios), Decimal("0")) == titulo.valor

    def test_ultima_linha_absorve_o_arredondamento(self, tmp_path):
        linhas = [linha("FORNECEDOR", 1.0, documento="NF 3") for _ in range(3)]
        titulo = montar(tmp_path / "y.xlsx", linhas).titulos[0]
        assert titulo.rateios[0].percentual == Decimal("33.3333")
        assert titulo.rateios[-1].percentual == Decimal("33.3334")

    def test_conferencia_detecta_percentual_fora(self):
        from ongsys.modelos import Titulo

        titulo = Titulo(
            chave="x", linhas_origem=[1], fornecedor="F", documento="D", documento_original="D",
            emissao=date(2026, 6, 1), vencimento=date(2026, 6, 1), efetivacao=date(2026, 6, 1),
            valor=Decimal("100.00"), conta_pagadora_id="6", conta_pagadora_nome="ITAU",
            tipo_despesa="Outros", tipo_documento="Outros", meio_pagamento="Internet Banking",
            tipo_pagamento="Transferência Eletrônica", historico="h",
            rateios=[Rateio(1, Decimal("100.00"), "CC", "T", "C", "", "", Decimal("99"))],
        )
        problemas = R.conferir_rateio(titulo, Decimal("0.01"))
        assert any("100%" in p for p in problemas)

    def test_conferencia_detecta_soma_de_valores_errada(self):
        from ongsys.modelos import Titulo

        titulo = Titulo(
            chave="x", linhas_origem=[1], fornecedor="F", documento="D", documento_original="D",
            emissao=date(2026, 6, 1), vencimento=date(2026, 6, 1), efetivacao=date(2026, 6, 1),
            valor=Decimal("100.00"), conta_pagadora_id="6", conta_pagadora_nome="ITAU",
            tipo_despesa="Outros", tipo_documento="Outros", meio_pagamento="Internet Banking",
            tipo_pagamento="Transferência Eletrônica", historico="h",
            rateios=[Rateio(1, Decimal("90.00"), "CC", "T", "C", "", "", Decimal("100"))],
        )
        assert any("não bate" in p for p in R.conferir_rateio(titulo, Decimal("0.01")))


class TestTipoDespesa:
    """§9: a prioridade é o Título categoria, não o ramo do fornecedor."""

    def test_servicos_tomados_vira_contratacao(self, tmp_path):
        titulo = montar(tmp_path / "z1.xlsx", [
            linha("QUALQUER UM", 100.0, titulo_categoria="SERVICOS_TOMADOS_DE_TERCEIROS")
        ]).titulos[0]
        assert titulo.tipo_despesa == "Contratação de Serviço(s)"

    def test_servicos_tomados_vence_fornecedor_de_varejo(self, tmp_path):
        # Papelmais é varejo, mas o Título categoria manda.
        titulo = montar(tmp_path / "z2.xlsx", [
            linha("PAPELMAIS", 100.0, titulo_categoria="SERVICOS_TOMADOS_DE_TERCEIROS")
        ]).titulos[0]
        assert titulo.tipo_despesa == "Contratação de Serviço(s)"

    def test_encargos_viram_imposto(self, tmp_path):
        titulo = montar(tmp_path / "z3.xlsx", [
            linha("FGTS", 100.0, titulo_categoria="ENCARGOS_SOCIAIS")
        ]).titulos[0]
        assert titulo.tipo_despesa == "Pagamento de Impostos"

    def test_varejo_vira_compra_de_produto(self, tmp_path):
        titulo = montar(tmp_path / "z4.xlsx", [
            linha("PAPELMAIS COMERCIO", 100.0, titulo_categoria="DESPESAS_ADMINISTRATIVAS")
        ]).titulos[0]
        assert titulo.tipo_despesa == "Compra de Produto(s)"

    def test_demais_casos_viram_outros(self, tmp_path):
        titulo = montar(tmp_path / "z5.xlsx", [
            linha("CONDOMINIO X", 100.0, titulo_categoria="DESPESAS_COM_OCUPACAO")
        ]).titulos[0]
        assert titulo.tipo_despesa == "Outros"

    def test_prestador_de_servico_sem_a_categoria_nao_vira_contratacao(self, tmp_path):
        # §9: "não classificar como serviço só porque o fornecedor presta serviço".
        titulo = montar(tmp_path / "z6.xlsx", [
            linha("MEDPLUS SERVICOS DE SAUDE LTDA", 100.0, titulo_categoria="DESPESAS_COM_PESSOAL")
        ]).titulos[0]
        assert titulo.tipo_despesa == "Outros"


class TestPeriodoEConta:
    def test_periodo_filtra_pela_efetivacao(self, tmp_path):
        linhas = [
            linha("JUNHO SA", 100.0, pagamento=date(2026, 6, 15)),
            linha("AGOSTO SA", 100.0, pagamento=date(2026, 8, 15), documento="NF 2"),
        ]
        resultado = montar(tmp_path / "z7.xlsx", linhas,
                           inicio=date(2026, 6, 1), fim=date(2026, 6, 30))
        assert [t.fornecedor for t in resultado.titulos] == ["JUNHO SA"]

    def test_mes_sem_dados_nao_e_inventado(self, tmp_path):
        """Critério de aceite 5."""
        resultado = montar(tmp_path / "z8.xlsx", [linha("JUNHO SA", 100.0, pagamento=date(2026, 6, 15))],
                           inicio=date(2026, 6, 1), fim=date(2026, 7, 31))
        assert resultado.meses_com_dados() == ["06/2026"]

    def test_periodo_invertido_da_erro(self, tmp_path):
        with pytest.raises(R.ErroRegras):
            montar(tmp_path / "z9.xlsx", [linha("F", 100.0)],
                   inicio=date(2026, 7, 1), fim=date(2026, 6, 1))

    def test_conta_inexistente_da_erro(self, tmp_path):
        with pytest.raises(R.ErroRegras):
            montar(tmp_path / "za.xlsx", [linha("F", 100.0)], conta="999")

    def test_sem_conta_pagadora_e_permitido(self, tmp_path):
        resultado = montar(tmp_path / "za_sem_conta.xlsx", [linha("F", 100.0)], conta="")
        titulo = resultado.titulos[0]
        assert titulo.conta_pagadora_id == ""
        assert titulo.conta_pagadora_nome == ""

    def test_conta_entra_na_chave_do_titulo(self, tmp_path):
        resultado = montar(tmp_path / "zb.xlsx", [linha("F", 100.0)], conta="63")
        assert resultado.titulos[0].chave.endswith("|63")
        assert resultado.titulos[0].conta_pagadora_nome == "PREPAGO CARTAO 0001"


class TestHashOrigem:
    def test_hash_muda_quando_o_valor_muda(self, tmp_path):
        um = montar(tmp_path / "zc.xlsx", [linha("F", 100.0)]).titulos[0]
        dois = montar(tmp_path / "zd.xlsx", [linha("F", 200.0)]).titulos[0]
        assert um.hash_origem() != dois.hash_origem()

    def test_hash_estavel_para_os_mesmos_dados(self, tmp_path):
        um = montar(tmp_path / "ze.xlsx", [linha("F", 100.0)]).titulos[0]
        dois = montar(tmp_path / "zf.xlsx", [linha("F", 100.0)]).titulos[0]
        assert um.hash_origem() == dois.hash_origem()

    def test_codigo_de_barras_faz_parte_do_hash(self, tmp_path):
        um = montar(
            tmp_path / "hash_barra_1.xlsx",
            [linha("F", codigo_barras="123.456 789")],
        ).titulos[0]
        dois = montar(
            tmp_path / "hash_barra_2.xlsx",
            [linha("F", codigo_barras="987654321")],
        ).titulos[0]
        assert um.hash_origem() != dois.hash_origem()


class TestCodigoDeBarrasRateado:
    def test_codigo_pode_estar_em_qualquer_linha_do_rateio(self, tmp_path):
        resultado = montar(
            tmp_path / "barra_rateada.xlsx",
            [
                linha("F", 100.0, codigo_barras=""),
                linha("F", 50.0, codigo_barras="123.456 789"),
            ],
        )
        titulo = resultado.titulos[0]
        assert titulo.codigo_barras == "123456789"
        assert not titulo.bloqueado

    def test_codigos_diferentes_bloqueiam_o_titulo(self, tmp_path):
        resultado = montar(
            tmp_path / "barra_conflito.xlsx",
            [
                linha("F", 100.0, codigo_barras="111.111"),
                linha("F", 50.0, codigo_barras="222 222"),
            ],
        )
        titulo = resultado.titulos[0]
        assert titulo.bloqueado
        assert any("Codigos de barras diferentes" in x for x in titulo.bloqueios)


class TestVencimentoUsarPagamento:
    """Por padrão, o vencimento usa a data de pagamento (efetivação).

    Isso facilita a conferência na hora da baixa, porque na visualização
    prévia do Ongsys só aparece a data de vencimento.
    """

    def test_padrao_usa_pagamento(self, tmp_path):
        titulo = montar(tmp_path / "zg.xlsx", [
            linha("BENEFIX", 100.0, vencimento=date(2026, 6, 10), pagamento=date(2026, 6, 16))
        ]).titulos[0]
        assert titulo.vencimento == date(2026, 6, 16)
        assert any("data de pagamento" in a for a in titulo.alertas)

    def test_sem_alerta_quando_datas_iguais(self, tmp_path):
        titulo = montar(tmp_path / "zg2.xlsx", [
            linha("BENEFIX", 100.0, vencimento=date(2026, 6, 16), pagamento=date(2026, 6, 16))
        ]).titulos[0]
        assert titulo.vencimento == date(2026, 6, 16)
        assert not any("data de pagamento" in a for a in titulo.alertas)

    def test_desativado_usa_vencimento_original(self, tmp_path):
        config = config_de_teste(vencimento_usar_pagamento=False)
        titulo = montar(tmp_path / "zg3.xlsx", [
            linha("BENEFIX", 100.0, vencimento=date(2026, 6, 10), pagamento=date(2026, 6, 16))
        ], config=config).titulos[0]
        assert titulo.vencimento == date(2026, 6, 10)

    def test_desativado_com_regra_por_fornecedor(self, tmp_path):
        config = config_de_teste(
            vencimento_usar_pagamento=False,
            vencimento_igual_efetivacao=["BENEFIX"],
        )
        titulo = montar(tmp_path / "zh.xlsx", [
            linha("EMPRESA EXEMPLO DE BENEFICIOS - BENEFIX", 100.0,
                  vencimento=date(2026, 6, 10), pagamento=date(2026, 6, 16))
        ], config=config).titulos[0]
        assert titulo.vencimento == date(2026, 6, 16)
        assert any("vencimento igual" in a for a in titulo.alertas)
