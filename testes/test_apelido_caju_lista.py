"""A razão social confirmada da BENEFIX deve funcionar também na lista."""

from datetime import date

from apoio import config_de_teste

from ongsys.navegador import OngsysBot


def test_apelido_confirmado_da_caju(tmp_path):
    bot = OngsysBot(
        config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias"
    )

    assert bot.resolver_apelido("EMPRESA EXEMPLO DE BENEFICIOS - BENEFIX") == (
        "EMPRESA EXEMPLO DE BENEFICIOS E PAGAMENTOS LTDA"
    )


def test_busca_confere_linha_pela_razao_social_resolvida(tmp_path, monkeypatch):
    bot = OngsysBot(
        config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias"
    )
    termos_conferidos = []

    class Pagina:
        def goto(self, *_args, **_kwargs):
            pass

        def wait_for_timeout(self, _ms):
            pass

    class Campo:
        def count(self):
            return 1

    bot.page = Pagina()
    monkeypatch.setattr(bot, "_aplicar_periodo_lista", lambda *_args: True)
    monkeypatch.setattr(bot, "_campo_busca_fornecedor", lambda: Campo())
    monkeypatch.setattr(bot, "_selecionar_fornecedor_filtro", lambda _nome: "cadastro")
    monkeypatch.setattr(bot, "_gerar_relatorio_lista", lambda: True)

    def ids_visiveis(termo):
        termos_conferidos.append(termo)
        return ["00022222"], []

    monkeypatch.setattr(bot, "_ids_fornecedor_visiveis", ids_visiveis)

    ids = bot.ids_resultado_busca(
        "EMPRESA EXEMPLO DE BENEFICIOS - BENEFIX",
        date(2026, 6, 16),
        date(2026, 6, 16),
    )

    assert ids == ["00022222"]
    assert termos_conferidos == [
        "EMPRESA EXEMPLO DE BENEFICIOS E PAGAMENTOS LTDA"
    ]
