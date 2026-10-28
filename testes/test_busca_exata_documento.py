"""Pesquisa exata do documento final na lista de Contas a Pagar."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from apoio import config_de_teste

from ongsys.modelos import Duplicidade
from ongsys.navegador import OngsysBot


def test_linha_bate_assinatura_confere_valor_e_datas(tmp_path):
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")
    titulo = SimpleNamespace(
        valor=Decimal("433.13"), emissao=date(2026, 8, 20), vencimento=date(2026, 9, 1)
    )
    # Bate: valor + emissão + vencimento presentes (aceita grafias diferentes).
    assert bot._linha_bate_assinatura(
        titulo, "00040000 09_26 FULANO 433,13 20/08/2026 01-Sep-2026 CP031500"
    )
    # Valor diferente reprova.
    assert not bot._linha_bate_assinatura(
        titulo, "00040000 09_26 FULANO 433,14 20/08/2026 01/09/2026"
    )
    # Vencimento ausente reprova.
    assert not bot._linha_bate_assinatura(
        titulo, "00040000 09_26 FULANO 433,13 20/08/2026"
    )
    assert not bot._linha_bate_assinatura(titulo, "")


def test_descobrir_id_desambigua_por_valor_no_documento_compartilhado(tmp_path, monkeypatch):
    """Documento '09_26' é compartilhado; o valor isola o título certo."""
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")

    class Pagina:
        def wait_for_timeout(self, _ms):
            pass

    bot.page = Pagina()
    titulo = SimpleNamespace(
        valor=Decimal("339.09"), emissao=date(2026, 8, 20),
        vencimento=date(2026, 9, 1), fornecedor="EDUARDO WISNIEWSKI",
    )
    monkeypatch.setattr(
        bot, "ids_resultado_busca_documento", lambda *_a, **_k: ["00040001", "00040002"]
    )
    textos = {
        "00040001": "00040001 09_26 EDUARDO 339,09 20/08/2026 01/09/2026 CP031501",
        "00040002": "00040002 09_26 OUTRO 500,00 20/08/2026 01/09/2026 CP031502",
    }
    monkeypatch.setattr(bot, "texto_linha_para_id", lambda i: textos.get(i, ""))

    assert bot._descobrir_id(titulo, "09_26") == "00040001"


def test_descobrir_id_desambigua_por_fornecedor_quando_assinatura_colide(tmp_path, monkeypatch):
    """7 reembolsos '09_26' de R$ 1.710,00: só o fornecedor distingue (2026-09-24)."""
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")

    class Pagina:
        def wait_for_timeout(self, _ms):
            pass

    bot.page = Pagina()
    titulo = SimpleNamespace(
        valor=Decimal("1710.00"), emissao=date(2026, 9, 9),
        vencimento=date(2026, 9, 15), fornecedor="JULIANA MOREIRA DA ROCHA",
    )
    # Todos batem a assinatura (valor + datas). Um deles (PATRICIA) compartilha
    # MOREIRA + ROCHA com a JULIANA — não pode confundir.
    textos = {
        "00034097": "00034097 09_26 JULIANA MOREIRA DA ROCHA 1.710,00 09/09/2026 15/09/2026 CP0331",
        "00014098": "00014098 09_26 LUCIA VASCONCELOS DE ARAUJO 1.710,00 09/09/2026 15/09/2026 CP0332",
        "00014099": "00014099 09_26 PATRICIA DA ROCHA DOS MOREIRA 1.710,00 09/09/2026 15/09/2026 CP0333",
    }
    monkeypatch.setattr(
        bot, "ids_resultado_busca_documento", lambda *_a, **_k: list(textos)
    )
    monkeypatch.setattr(bot, "texto_linha_para_id", lambda i: textos.get(i, ""))

    assert bot._descobrir_id(titulo, "09_26") == "00034097"


def test_descobrir_id_nao_chuta_quando_fornecedor_tambem_colide(tmp_path, monkeypatch):
    """Dois títulos do MESMO fornecedor com mesma assinatura: devolve vazio."""
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")

    class Pagina:
        def wait_for_timeout(self, _ms):
            pass

    bot.page = Pagina()
    titulo = SimpleNamespace(
        valor=Decimal("1710.00"), emissao=date(2026, 9, 9),
        vencimento=date(2026, 9, 15), fornecedor="JULIANA MOREIRA DA ROCHA",
    )
    textos = {
        "00034097": "00034097 09_26 JULIANA MOREIRA DA ROCHA 1.710,00 09/09/2026 15/09/2026 CP0331",
        "00034200": "00034200 09_26 JULIANA MOREIRA DA ROCHA 1.710,00 09/09/2026 15/09/2026 CP0400",
    }
    monkeypatch.setattr(
        bot, "ids_resultado_busca_documento", lambda *_a, **_k: list(textos)
    )
    monkeypatch.setattr(bot, "texto_linha_para_id", lambda i: textos.get(i, ""))

    assert bot._descobrir_id(titulo, "09_26") == ""


def test_descobrir_id_devolve_vazio_quando_nao_e_unico(tmp_path, monkeypatch):
    """Na dúvida (dois candidatos com a mesma assinatura) devolve vazio."""
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")

    class Pagina:
        def wait_for_timeout(self, _ms):
            pass

    bot.page = Pagina()
    titulo = SimpleNamespace(
        valor=Decimal("100.00"), emissao=date(2026, 8, 20),
        vencimento=date(2026, 9, 1), fornecedor="X",
    )
    monkeypatch.setattr(
        bot, "ids_resultado_busca_documento", lambda *_a, **_k: ["00040003", "00040004"]
    )
    textos = {
        "00040003": "00040003 09_26 A 100,00 20/08/2026 01/09/2026",
        "00040004": "00040004 09_26 B 100,00 20/08/2026 01/09/2026",
    }
    monkeypatch.setattr(bot, "texto_linha_para_id", lambda i: textos.get(i, ""))

    assert bot._descobrir_id(titulo, "09_26") == ""


def test_descobrir_id_prioriza_linha_recente_sem_indice(tmp_path, monkeypatch):
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")

    class Pagina:
        def wait_for_timeout(self, _ms):
            pass

    bot.page = Pagina()
    titulo = SimpleNamespace(
        valor=Decimal("20.82"), emissao=date(2026, 7, 14),
        vencimento=date(2026, 7, 15), fornecedor="NORTE VIDA",
    )
    monkeypatch.setattr(bot, "_ids_recentes_sem_indice", lambda *_a: ["00050001"])
    monkeypatch.setattr(
        bot, "ids_resultado_busca_documento",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("fallback indevido")),
    )

    assert bot._descobrir_id(titulo, "BOLETO.11") == "00050001"


def test_duplicidade_sem_documento_encontrado_e_novo_sem_confirmacao(tmp_path, monkeypatch):
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")
    titulo = SimpleNamespace(
        documento="BOLETO.11", fornecedor="NORTE VIDA COMPANHIA DE SEGUROS",
        valor=Decimal("20.82"), vencimento=date(2026, 7, 15),
    )
    monkeypatch.setattr(bot, "ids_resultado_busca_documento", lambda *_a, **_k: [])
    monkeypatch.setattr(bot, "_ids_recentes_sem_indice", lambda *_a, **_k: [])

    classificacao, detalhe = bot.pesquisar_duplicidade(titulo)

    assert classificacao == Duplicidade.NOVO
    assert "documento" in detalhe.lower()


def test_duplicidade_reconhece_lancamento_manual_ainda_fora_do_indice(tmp_path, monkeypatch):
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")
    titulo = SimpleNamespace(
        documento="BOLETO.14", fornecedor="NORTE VIDA COMPANHIA DE SEGUROS",
        valor=Decimal("20.82"), emissao=date(2026, 7, 14),
        vencimento=date(2026, 7, 15),
    )
    monkeypatch.setattr(bot, "ids_resultado_busca_documento", lambda *_a, **_k: [])
    monkeypatch.setattr(
        bot, "_ids_recentes_sem_indice", lambda *_a, **_k: ["00033700"]
    )

    classificacao, detalhe = bot.pesquisar_duplicidade(titulo)

    assert classificacao == Duplicidade.EXATO_JA_EXISTE
    assert "00033700" in detalhe


def test_duplicidade_falha_tecnica_permanece_nao_verificada(tmp_path, monkeypatch):
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")
    titulo = SimpleNamespace(
        documento="BOLETO.11", fornecedor="NORTE VIDA",
        valor=Decimal("20.82"), vencimento=date(2026, 7, 15),
    )
    monkeypatch.setattr(
        bot, "ids_resultado_busca_documento",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("tabela indisponivel")),
    )

    classificacao, _ = bot.pesquisar_duplicidade(titulo)

    assert classificacao == Duplicidade.NAO_VERIFICADO


def test_mesmo_documento_com_outro_valor_exige_variacao(tmp_path, monkeypatch):
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")
    titulo = SimpleNamespace(
        documento="BOLETO.11", fornecedor="NORTE VIDA COMPANHIA DE SEGUROS",
        valor=Decimal("20.82"), vencimento=date(2026, 7, 15),
    )
    texto = (
        "00050000 BOLETO.11 NORTE VIDA COMPANHIA DE SEGUROS "
        "99,00 15/07/2026 CP032000"
    )
    monkeypatch.setattr(bot, "ids_resultado_busca_documento", lambda *_a, **_k: ["00050000"])
    monkeypatch.setattr(bot, "texto_linha_para_id", lambda _id: texto)

    classificacao, _ = bot.pesquisar_duplicidade(titulo)

    assert classificacao == Duplicidade.DOCUMENTO_REPETIDO_OUTRO_TITULO


def test_mesmo_documento_em_outro_fornecedor_nao_e_conflito(tmp_path, monkeypatch):
    """NF 15 de OUTRO fornecedor não bloqueia nem incrementa (2026-09-21).

    A trava do Ongsys é por fornecedor. Antes, "NF 15" de qualquer fornecedor
    virava DOCUMENTO_REPETIDO e o documento era incrementado para "NF 15.1"
    sem necessidade (RENATA, ERICO, JOANA, JONAS...).
    """
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")
    titulo = SimpleNamespace(
        documento="NF 15", fornecedor="RENATA FONSECA PRADO",
        valor=Decimal("6344.00"), vencimento=date(2026, 9, 29),
    )
    # A busca encontra um "NF 15" com o mesmo vencimento, porém de OUTRO
    # fornecedor (VANESSA). Não é conflito para a RENATA.
    texto = "00040100 NF 15 VANESSA CAMPOS TEIXEIRA 7.560,00 29/09/2026 CP033000"
    monkeypatch.setattr(bot, "ids_resultado_busca_documento", lambda *_a, **_k: ["00040100"])
    monkeypatch.setattr(bot, "texto_linha_para_id", lambda _id: texto)

    classificacao, detalhe = bot.pesquisar_duplicidade(titulo)

    assert classificacao == Duplicidade.NOVO
    assert "outro fornecedor" in detalhe.lower()


def test_mesmo_documento_mesmo_fornecedor_valor_diferente_ainda_incrementa(tmp_path, monkeypatch):
    """Reforço: mesmo fornecedor + mesmo documento + valor diferente = repetido."""
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")
    titulo = SimpleNamespace(
        documento="NF 15", fornecedor="RENATA FONSECA PRADO",
        valor=Decimal("6344.00"), vencimento=date(2026, 9, 29),
    )
    texto = "00040100 NF 15 RENATA FONSECA PRADO 999,00 29/09/2026 CP033000"
    monkeypatch.setattr(bot, "ids_resultado_busca_documento", lambda *_a, **_k: ["00040100"])
    monkeypatch.setattr(bot, "texto_linha_para_id", lambda _id: texto)

    classificacao, _ = bot.pesquisar_duplicidade(titulo)

    assert classificacao == Duplicidade.DOCUMENTO_REPETIDO_OUTRO_TITULO


def test_mesmo_titulo_com_data_em_ingles_e_reconhecido_como_existente(tmp_path, monkeypatch):
    bot = OngsysBot(config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias")
    titulo = SimpleNamespace(
        documento="BOLETO.11", fornecedor="NORTE VIDA COMPANHIA DE SEGUROS",
        valor=Decimal("20.82"), vencimento=date(2026, 7, 15),
    )
    texto = (
        "00050000 BOLETO.11 NORTE VIDA COMPANHIA DE SEGUROS "
        "14-Jul-2026 15-Jul-2026 20,82 CP032000"
    )
    monkeypatch.setattr(bot, "ids_resultado_busca_documento", lambda *_a, **_k: ["00050000"])
    monkeypatch.setattr(bot, "texto_linha_para_id", lambda _id: texto)

    classificacao, _ = bot.pesquisar_duplicidade(titulo)

    assert classificacao == Duplicidade.EXATO_JA_EXISTE


def test_bl1_nao_confunde_bl_original_nem_bl10(tmp_path, monkeypatch):
    bot = OngsysBot(
        config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias"
    )
    buscas = []

    class Pagina:
        def goto(self, *_args, **_kwargs):
            pass

        def wait_for_timeout(self, _ms):
            pass

    class Linha:
        def __init__(self, texto):
            self.texto = texto

        def inner_text(self):
            return self.texto

    bot.page = Pagina()
    monkeypatch.setattr(bot, "_aplicar_periodo_lista", lambda *_args: True)
    monkeypatch.setattr(bot, "_gerar_relatorio_lista", lambda: True)
    monkeypatch.setattr(
        bot, "_buscar_na_tabela", lambda termo: buscas.append(termo) or True
    )
    monkeypatch.setattr(
        bot,
        "_linhas_visiveis_lista",
        lambda: [
            Linha("00011111 BL EMPRESA EXEMPLO 139.863,76 CP031001"),
            Linha("00022222 BL.1 EMPRESA EXEMPLO 139.863,76 CP031002"),
            Linha("00033333 BL.10 EMPRESA EXEMPLO 139.863,76 CP031003"),
        ],
    )

    ids = bot.ids_resultado_busca_documento(
        "BL.1", date(2026, 6, 16), date(2026, 6, 16)
    )

    assert buscas == ["BL.1"]
    assert ids == ["00022222"]
    assert bot.codigo_lancamento_para_id("00022222") == "CP031002"
