"""Regressão da recuperação do piloto BENEFIX salvo como BL.1."""

from decimal import Decimal

import apoio
from apoio import config_de_teste, linha, montar

from ongsys import auditoria as A
from ongsys.checkpoint import Checkpoint
from ongsys.cli import _recuperar_ids_desconhecidos


def test_documento_final_bl1_distingue_copia_do_bl_original(tmp_path, monkeypatch):
    titulo = montar(
        tmp_path / "base.xlsx", [linha("FORNECEDOR TESTE", 1000.0)]
    ).titulos[0]
    ponto = Checkpoint(tmp_path / "checkpoint.json")
    ponto.iniciar(titulo)
    ponto.marcar_salvo(titulo, "", f"{titulo.documento}.1")
    item = ponto.obter(titulo)

    class Bot:
        def ids_resultado_busca(self, _termo, _inicio=None, _fim=None):
            return ["00011111", "00022222"]

    def ler(_bot, id_ongsys, *_args):
        documento = titulo.documento if id_ongsys == "00011111" else f"{titulo.documento}.1"
        return {
            "fornecedor": titulo.fornecedor,
            "documento": documento,
            "emissao": titulo.emissao.strftime("%d/%m/%Y"),
            "vencimento": titulo.vencimento.strftime("%d/%m/%Y"),
            "efetivacao": titulo.efetivacao.strftime("%d/%m/%Y"),
            "valor": titulo.valor_br,
        }

    monkeypatch.setattr(A, "ler_titulo_da_tela", ler)
    recuperados = _recuperar_ids_desconhecidos(
        Bot(), ponto, [item], {titulo.chave: titulo}, config_de_teste()
    )

    assert recuperados == ["00022222"]
    assert ponto.obter(titulo).id_ongsys == "00022222"
    assert ponto.obter(titulo).documento_final == f"{titulo.documento}.1"
