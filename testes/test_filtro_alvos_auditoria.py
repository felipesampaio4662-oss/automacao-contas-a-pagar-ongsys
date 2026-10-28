"""O filtro de fornecedor também restringe IDs antigos da auditoria."""

import pytest
from apoio import linha, montar

from ongsys.checkpoint import Checkpoint
from ongsys.cli import _ids_conhecidos_para_auditoria, _titulo_correspondente
from ongsys.normalizacao import normalizar_documento


def test_filtro_caju_nao_audita_outro_id_concluido(tmp_path):
    caju = montar(
        tmp_path / "caju.xlsx", [linha("EMPRESA EXEMPLO - BENEFIX", 1000.0)]
    ).titulos[0]
    outro = montar(
        tmp_path / "outro.xlsx", [linha("OUTRO FORNECEDOR", 500.0)]
    ).titulos[0]
    ponto = Checkpoint(tmp_path / "checkpoint.json")
    ponto.marcar_salvo(caju, "00022222", f"{caju.documento}.1")
    ponto.marcar_salvo(outro, "00011111", outro.documento)

    ids = _ids_conhecidos_para_auditoria(ponto, {caju.chave: caju})

    assert ids == ["00022222"]


class TestTituloCorrespondentePeloDocumento:
    """Liga um ID informado à linha da planilha (Conferir ID)."""

    def _por_documento(self, tmp_path, nomes):
        titulos = montar(
            tmp_path / "p.xlsx",
            [linha(nome, 1500.0, documento="NF 13") for nome in nomes],
        ).titulos
        por_documento = {}
        for titulo in titulos:
            por_documento.setdefault(normalizar_documento(titulo.documento), []).append(titulo)
        return por_documento

    def test_documento_repetido_e_desempatado_pelo_fornecedor_lido(self, tmp_path):
        # Tela real (2026-10-01): ANA CLARA NF 13 e VANESSA NF 13 na mesma
        # planilha faziam o Conferir ID responder "sem correspondencia".
        por_documento = self._por_documento(
            tmp_path, ["ANA CLARA MOREIRA ROCHA", "VANESSA FREITAS DE NUNES"]
        )
        lido = {"documento": "NF 13", "fornecedor": "4005 - ANA CLARA MOREIRA ROCHA"}

        achado = _titulo_correspondente(lido, por_documento)

        assert achado is not None
        assert achado.fornecedor == "ANA CLARA MOREIRA ROCHA"

    def test_documento_repetido_sem_fornecedor_compativel_fica_sem_ligacao(self, tmp_path):
        por_documento = self._por_documento(
            tmp_path, ["ANA CLARA MOREIRA ROCHA", "VANESSA FREITAS DE NUNES"]
        )
        lido = {"documento": "NF 13", "fornecedor": "3156 - ANA DO CARMO ROCHA MOREIRA"}

        assert _titulo_correspondente(lido, por_documento) is None

    def _lido(self, fornecedor, documento, valor="1.500,00", vencimento="10/06/2026"):
        return {"fornecedor": fornecedor, "documento": documento,
                "valor": valor, "vencimento": vencimento}

    def test_documento_diferente_liga_por_fornecedor_valor_e_mes(self, tmp_path):
        # Tela real (2026-10-01): TATIANA foi salva como "09_26.3" e a AGUASUL
        # lançada à mão como "FAT 09.2026"; o Conferir ID não achava a linha.
        por_documento = self._por_documento(
            tmp_path, ["TATIANA CARDOSO DOS PINTO RIBEIRO", "VANESSA FREITAS DE NUNES"]
        )
        lido = self._lido("60.741.682 TATIANA CARDOSO DOS PINTO RIBEIRO", "NF 13.3",
                          vencimento="09/06/2026")

        achado = _titulo_correspondente(lido, por_documento)

        assert achado is not None
        assert achado.fornecedor == "TATIANA CARDOSO DOS PINTO RIBEIRO"

    def test_documento_diferente_usa_o_apelido_do_fornecedor(self, tmp_path):
        por_documento = self._por_documento(tmp_path, ["AGUASUL"])
        lido = self._lido("CIA DE SANEAMENTO BASICO REGIONAL AGUASUL", "FAT 09.2026")
        apelidos = {"AGUASUL": "CIA DE SANEAMENTO BASICO REGIONAL AGUASUL"}

        achado = _titulo_correspondente(lido, por_documento, lambda n: apelidos.get(n, n))

        assert achado is not None and achado.fornecedor == "AGUASUL"

    def test_cartao_com_nome_de_categoria_liga_so_com_documento_igual(self, tmp_path):
        # Cartão: o Ongsys mostra "3819 - LICENCA DE USO" no fornecedor.
        por_documento = self._por_documento(tmp_path, ["MONITORA"])
        nomes = lambda titulo: [titulo.fornecedor, "LICENCA DE USO"]  # noqa: E731

        mesmo_doc = self._lido("3819 - LICENCA DE USO", "NF 13")
        outro_doc = self._lido("3819 - LICENCA DE USO", "19.08")

        assert _titulo_correspondente(mesmo_doc, por_documento) is None
        assert _titulo_correspondente(mesmo_doc, por_documento, None, nomes) is not None
        assert _titulo_correspondente(outro_doc, por_documento, None, nomes) is None

    def test_cartao_com_documento_repetido_desempata_pelo_valor(self, tmp_path):
        # "08.06" é o documento de CODEAI, CHATTEAM e PESQUISAJA no mesmo dia.
        titulos = montar(
            tmp_path / "p.xlsx",
            [linha("CODEAI", 883.08, documento="08.06"),
             linha("CHATTEAM", 20.32, documento="08.06")],
        ).titulos
        por_documento = {}
        for titulo in titulos:
            por_documento.setdefault(normalizar_documento(titulo.documento), []).append(titulo)
        nomes = lambda titulo: [titulo.fornecedor, "LICENCA DE USO"]  # noqa: E731

        lido = self._lido("3816 - LICENCA DE USO", "08.06", valor="883,08")
        outro_valor = self._lido("3816 - LICENCA DE USO", "08.06", valor="999,99")

        achado = _titulo_correspondente(lido, por_documento, None, nomes)
        assert achado is not None and achado.fornecedor == "CODEAI"
        assert _titulo_correspondente(outro_valor, por_documento, None, nomes) is None
        assert _titulo_correspondente(lido, por_documento) is None

    @pytest.mark.parametrize("valor,vencimento", [
        ("1.500,01", "10/06/2026"),   # valor diferente
        ("1.500,00", "10/07/2026"),   # outro mês
    ])
    def test_documento_diferente_sem_valor_ou_mes_igual_fica_sem_ligacao(
        self, tmp_path, valor, vencimento
    ):
        por_documento = self._por_documento(tmp_path, ["TATIANA CARDOSO DOS PINTO RIBEIRO"])
        lido = self._lido("TATIANA CARDOSO DOS PINTO RIBEIRO", "OUTRO 99", valor, vencimento)

        assert _titulo_correspondente(lido, por_documento) is None

    def test_documento_diferente_com_outro_fornecedor_fica_sem_ligacao(self, tmp_path):
        por_documento = self._por_documento(tmp_path, ["TATIANA CARDOSO DOS PINTO RIBEIRO"])
        lido = self._lido("ANA DO CARMO ROCHA MOREIRA", "OUTRO 99")

        assert _titulo_correspondente(lido, por_documento) is None

    def test_documento_diferente_e_dois_titulos_iguais_fica_sem_ligacao(self, tmp_path):
        titulos = montar(
            tmp_path / "p.xlsx",
            [linha("TATIANA CARDOSO DOS PINTO RIBEIRO", 1500.0, documento="NF 1"),
             linha("TATIANA CARDOSO DOS PINTO RIBEIRO", 1500.0, documento="NF 2")],
        ).titulos
        por_documento = {}
        for titulo in titulos:
            por_documento.setdefault(normalizar_documento(titulo.documento), []).append(titulo)
        lido = self._lido("TATIANA CARDOSO DOS PINTO RIBEIRO", "OUTRO 99")

        assert _titulo_correspondente(lido, por_documento) is None
