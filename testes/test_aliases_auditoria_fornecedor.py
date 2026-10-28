"""Variações cadastrais confirmadas não são divergências financeiras."""

from apoio import config_de_teste

from ongsys import auditoria as A
from ongsys.navegador import OngsysBot


def test_razao_social_com_ltda_confere_com_alias_confirmado(tmp_path, monkeypatch):
    # O apelido real fica no config.json local; aqui o mecanismo é testado com
    # um nome fictício na lista de correspondências confirmadas.
    from ongsys import navegador

    monkeypatch.setitem(
        navegador.APELIDOS_FORNECEDORES_CONFIRMADOS,
        "CASA OFICINA SOLUCOES EM NEGOCIOS",
        "CASA OFICINA SOLUCOES EM NEGOCIOS LTDA",
    )
    config = config_de_teste()
    config["apelidos_fornecedores"] = {
        "CASA OFICINA SOLUCOES EM NEGOCIOS": "CASA OFICINA SOLUCOES EM NEGOCIOS"
    }
    bot = OngsysBot(config, tmp_path / "perfil", tmp_path / "evidencias")
    esperado = bot.resolver_apelido("CASA OFICINA SOLUCOES EM NEGOCIOS")

    assert esperado == "CASA OFICINA SOLUCOES EM NEGOCIOS LTDA"
    assert A.comparar_texto(
        "Fornecedor",
        esperado,
        "3974 - CASA OFICINA SOLUCOES EM NEGOCIOS LTDA",
        "Conferir.",
    ) is None


def test_iptu_confere_com_alias_confirmado(tmp_path):
    bot = OngsysBot(
        config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias"
    )
    esperado = bot.resolver_apelido("IPTU 2026 - 05/10")

    assert esperado == "IPTU"
    assert A.comparar_texto(
        "Fornecedor", esperado, "2116 - IPTU", "Conferir."
    ) is None
