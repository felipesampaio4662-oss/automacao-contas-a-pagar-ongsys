import apoio
import pytest
from decimal import Decimal
from ongsys.normalizacao import codigo_confere, nome_da_opcao
from ongsys.navegador import OngsysBot, ErroNavegador
from ongsys import seletores as S
from types import SimpleNamespace


def test_parenteses_significativos_preservados():
    assert nome_da_opcao('6 - Despesas RH (Beneficios)') == 'Despesas RH (Beneficios)'
    assert nome_da_opcao('2.1 - LIMPEZA (A)') == 'LIMPEZA'
    assert OngsysBot._nome_selecionado_confere(
        '2.5.1.08.003 - CONDUÇÃO /LOCOMOÇÃO (A)',
        'CONDUCAO /LOCOMOÇÃO (A)',
    )


@pytest.mark.parametrize('prefixo', ['93', '107'])
def test_codigo_com_identificador_cadastral(prefixo):
    assert codigo_confere('2.2.3.11', f'{prefixo} - 2.2.3.11 - Descricao')
    assert not codigo_confere('2.2.3.11', f'{prefixo} - 12.2.3.11 - Descricao')


def test_selecao_vazia_nao_confirma():
    bot = object.__new__(OngsysBot)
    bot.page = None
    bot._ler_valor = lambda _: ''
    assert not bot._aguardar_selecao('#field_subprojeto_1', '2.2.3.11', codigo=True)


def test_campo_bloqueado_incorreto_interrompe():
    bot = object.__new__(OngsysBot)
    bot.page = object()
    bot._campo = lambda _: SimpleNamespace(first=SimpleNamespace(is_enabled=lambda: False))
    bot._ler_valor = lambda _: 'Outra atividade'
    with pytest.raises(ErroNavegador, match='bloqueado'):
        bot._exigir_autocomplete_editavel('#field_atividade_1', 'Despesas ADM')


def test_lapis_nativo_habilita_campo_de_rateio():
    estado = {'habilitado': False, 'cliques': 0}

    class Campo:
        def is_enabled(self):
            return estado['habilitado']
        def evaluate(self, _script):
            estado['cliques'] += 1
            estado['habilitado'] = True
            return True

    bot = object.__new__(OngsysBot)
    bot.page = SimpleNamespace(wait_for_timeout=lambda _ms: None)
    bot._campo = lambda _: SimpleNamespace(first=Campo())
    bot._habilitar_campo_de_rateio('#field_valor_rateio_1', 'valor')
    assert estado == {'habilitado': True, 'cliques': 1}


def test_seletores_reais_da_edicao_mapeados():
    assert S.BOTAO_SALVAR_EDICAO == '#btn_editar:visible'
    assert S.BOTAO_INCLUIR_LINHA_RATEIO == '#btn_clone_1:visible'


def test_redes_municipais_equivale_a_redes_somente_quando_autorizado():
    atual = '10.2.01.09 - REDES MUNICIPAIS'
    assert not OngsysBot._nome_selecionado_confere(atual, 'REDES')
    assert OngsysBot._nome_selecionado_confere(
        atual, 'REDES', aceitar_prefixo=True
    )


def test_busca_escolhe_unica_expansao_segura_de_redes():
    bot = object.__new__(OngsysBot)
    bot.page = object()
    bot._ler_valor = lambda _seletor: ''
    bot._exigir_autocomplete_editavel = lambda *_args: None
    bot._candidatos_autocomplete = lambda *_args: [
        '10.2.01.09 - REDES MUNICIPAIS'
    ]
    cliques = []

    def clicar(idx, seletor, esperado, **kwargs):
        cliques.append((idx, seletor, esperado, kwargs))
        return True

    bot._clicar_opcao_do_typeahead = clicar
    selecionado = bot._buscar_e_clicar_por_nome(
        S.projeto_cc(5),
        'REDES',
        minimo=Decimal('0.75'),
        aceitar_prefixo=True,
    )

    assert selecionado == '10.2.01.09 - REDES MUNICIPAIS'
    assert cliques == [(
        0,
        S.projeto_cc(5),
        'REDES',
        {'aceitar_prefixo': True},
    )]


def test_rateio_recupera_centro_de_custo_limpo_por_onchange():
    valores = {
        S.categoria_conta(1): '2.5.1.02.003 - VALE REFEICAO/REFEITORIO',
        S.projeto_cc(1): '',
    }
    chamadas = []

    bot = object.__new__(OngsysBot)
    bot.page = SimpleNamespace(wait_for_timeout=lambda _ms: None)
    bot._ler_valor = lambda seletor: valores.get(seletor, '')

    def selecionar(seletor, esperado, descricao, tentativas=5, **kwargs):
        chamadas.append((seletor, esperado, tentativas, kwargs))
        if seletor == S.projeto_cc(1):
            valores[seletor] = '10.2.01.01 - GESTAO ADM'
        return valores.get(seletor, esperado)

    bot._selecionar_por_nome = selecionar
    rateio = SimpleNamespace(
        categoria_detalhada='VALE REFEICAO/REFEITORIO',
        centro_custo='GESTAO ADM',
    )

    bot._garantir_campos_obrigatorios_rateio(1, rateio)

    assert chamadas == [(
        S.projeto_cc(1),
        'GESTAO ADM',
        1,
        {'aceitar_prefixo': True},
    )]
