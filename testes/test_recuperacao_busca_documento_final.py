"""A recuperação prioriza o documento final quando a razão social diverge."""

from apoio import config_de_teste, linha, montar

from ongsys import auditoria as A
from ongsys.checkpoint import Checkpoint
from ongsys.cli import _recuperar_ids_desconhecidos


def test_busca_bl1_nao_depende_do_nome_do_fornecedor(tmp_path, monkeypatch):
    titulo = montar(
        tmp_path / "base.xlsx", [linha("EMPRESA EXEMPLO - BENEFIX", 1000.0)]
    ).titulos[0]
    ponto = Checkpoint(tmp_path / "checkpoint.json")
    ponto.iniciar(titulo)
    ponto.marcar_salvo(titulo, "", f"{titulo.documento}.1")
    item = ponto.obter(titulo)
    documentos_pesquisados = []

    class Bot:
        def ids_resultado_busca_documento(self, documento, _inicio=None, _fim=None):
            documentos_pesquisados.append(documento)
            return ["00011111", "00022222"]

        def ids_resultado_busca(self, *_args):
            raise AssertionError("a busca por fornecedor nao deveria ser necessaria")

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

    assert documentos_pesquisados == [f"{titulo.documento}.1"]
    assert recuperados == ["00022222"]
    assert ponto.obter(titulo).id_ongsys == "00022222"
