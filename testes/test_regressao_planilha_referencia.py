"""Regressão contra a planilha de referência do projeto.

A referência atual é `planilha.xlsx.xlsx` (mesma estrutura de colunas e cores
descrita no handoff, recorte de junho/2026). Estes testes travam os números
observados nela: 13 títulos, R$ 200.770,10, junho sem julho e o FGTS bloqueado
pela mesma divergência de emissão (08/06 e 15/06).

Tarifa Bancária não é conta a pagar (é uma linha lançada fora do módulo de
contas a pagar) e por isso está em `ignorar_fornecedores` — as quatro linhas de
tarifa da planilha (R$ 189,00 + R$ 22,60 + R$ 135,60 + R$ 80,30 = R$ 427,50)
saem como exclusão, não como título.

Só rodam quando o arquivo estiver disponível na máquina. O caminho pode ser
informado em ONGSYS_PLANILHA_TESTE.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import apoio
import pytest

from ongsys import configuracao, planilha, regras

_INFORMADO = os.environ.get("ONGSYS_PLANILHA_TESTE", "").strip()
CANDIDATOS = [
    *([Path(_INFORMADO)] if _INFORMADO else []),
    apoio.RAIZ.parent / "planilha.xlsx.xlsx",
    apoio.RAIZ / "planilha.xlsx.xlsx",
    Path.home() / "Downloads" / "Nova pasta" / "planilha.xlsx.xlsx",
]
# `is_file()` e não `exists()`: um caminho vazio vira "." e passaria por existente.
REFERENCIA = next((c for c in CANDIDATOS if c.is_file()), None)

pytestmark = pytest.mark.skipif(
    REFERENCIA is None,
    reason="planilha.xlsx.xlsx nao encontrada; defina ONGSYS_PLANILHA_TESTE para rodar.",
)

INICIO, FIM = date(2026, 6, 1), date(2026, 7, 31)


@pytest.fixture(scope="module")
def resultado():
    config = configuracao.carregar(apoio.RAIZ / "config.example.json")
    linhas, _, _, _ = planilha.ler_linhas(REFERENCIA)
    return regras.montar_titulos(linhas, INICIO, FIM, config, "6")


class TestNumerosDeReferencia:
    def test_encontra_treze_titulos(self, resultado):
        assert len(resultado.titulos) == 13

    def test_total_confere_ao_centavo(self, resultado):
        assert resultado.total == Decimal("200770.10")

    def test_total_liberado(self, resultado):
        # O FGTS deixou de ser bloqueado (emissões divergentes agora usam a
        # data da primeira linha), então todo o total fica liberado.
        assert resultado.total_liberado == Decimal("200770.10")

    def test_julho_e_informado_como_ausente(self, resultado):
        assert resultado.meses_com_dados() == ["06/2026"]

    def test_fgts_usa_emissao_da_primeira_linha_com_alerta(self, resultado):
        # Emissões divergentes não bloqueiam mais: usa a data da 1a linha e
        # registra alerta para conferência.
        fgts = [t for t in resultado.titulos if "FGTS" in t.fornecedor.upper()]
        assert len(fgts) == 1
        assert not fgts[0].bloqueado
        assert fgts[0].emissao == date(2026, 6, 8)
        alerta = " ".join(fgts[0].alertas)
        assert "08/06/2026" in alerta and "15/06/2026" in alerta

    def test_nenhum_titulo_esta_bloqueado(self, resultado):
        assert len([t for t in resultado.titulos if t.bloqueado]) == 0

    def test_linhas_com_cor_pendente(self, resultado):
        assert resultado.linhas_por_cor_incluidas == 72
        assert resultado.linhas_por_cor_excluidas == 31

    def test_quantidade_de_exclusoes(self, resultado):
        assert len(resultado.exclusoes) == 52

    def test_tarifa_bancaria_nunca_vira_titulo(self, resultado):
        """Tarifa Bancária não é conta a pagar — fica só na exclusão."""
        assert not [t for t in resultado.titulos if "TARIFA" in t.fornecedor.upper()]
        excluidas = [e for e in resultado.exclusoes if "TARIFA" in e.fornecedor.upper()]
        assert len(excluidas) == 4
        assert all("TARIFA BANCARIA" in e.motivo for e in excluidas)


class TestIntegridadeDosTitulos:
    def test_todo_titulo_fecha_cem_por_cento(self, resultado):
        for titulo in resultado.titulos:
            soma = sum((r.percentual for r in titulo.rateios), Decimal("0"))
            assert soma == Decimal("100"), f"{titulo.fornecedor}: {soma}%"

    def test_soma_dos_rateios_bate_com_cada_titulo(self, resultado):
        for titulo in resultado.titulos:
            soma = sum((r.valor for r in titulo.rateios), Decimal("0"))
            assert soma == titulo.valor, f"{titulo.fornecedor}: {soma} != {titulo.valor}"

    def test_caju_e_um_titulo_com_muitos_rateios(self, resultado):
        caju = [t for t in resultado.titulos if "BENEFIX" in t.fornecedor.upper()]
        assert len(caju) == 1
        assert len(caju[0].rateios) == 35
        assert caju[0].valor == Decimal("139863.76")

    def test_chaves_sao_unicas(self, resultado):
        chaves = [t.chave for t in resultado.titulos]
        assert len(chaves) == len(set(chaves))

    def test_nenhum_valor_negativo_ou_zerado(self, resultado):
        for titulo in resultado.titulos:
            assert titulo.valor > 0
            assert all(r.valor > 0 for r in titulo.rateios)
