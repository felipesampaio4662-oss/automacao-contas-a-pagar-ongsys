"""A tabela só pode ser lida depois de o aviso Processando desaparecer."""

from apoio import config_de_teste

from ongsys import seletores as S
from ongsys.navegador import OngsysBot


class Indicador:
    def __init__(self):
        self.visivel = True
        self.esperas = []

    def is_visible(self):
        return self.visivel

    def wait_for(self, *, state, timeout):
        self.esperas.append((state, timeout))
        if state == "hidden":
            self.visivel = False


class Lista:
    def __init__(self, itens):
        self.itens = itens

    def count(self):
        return len(self.itens)

    def nth(self, indice):
        return self.itens[indice]


def test_aguarda_processando_ficar_oculto(tmp_path):
    bot = OngsysBot(
        config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias"
    )
    indicador = Indicador()

    class Pagina:
        def wait_for_timeout(self, _ms):
            pass

        def locator(self, _seletor):
            return Lista([indicador])

    bot.page = Pagina()

    assert bot._aguardar_lista_pronta() is True
    assert indicador.esperas == [("hidden", 60000)]


def test_ver_relatorio_recusa_ler_enquanto_processa(tmp_path, monkeypatch):
    bot = OngsysBot(
        config_de_teste(), tmp_path / "perfil", tmp_path / "evidencias"
    )

    class Icone:
        def is_visible(self):
            return True

        def click(self):
            pass

    class Pagina:
        def locator(self, seletor):
            assert seletor == S.ICONE_VER_RELATORIO
            return Lista([Icone()])

    bot.page = Pagina()
    monkeypatch.setattr(bot, "_aguardar_lista_pronta", lambda: False)

    assert bot._gerar_relatorio_lista() is False
