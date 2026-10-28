"""Camada de automação do navegador.

Regras que valem para todo este módulo:

* nenhuma seleção é feita "no chute": toda opção escolhida é conferida contra o
  valor esperado antes de seguir;
* qualquer dúvida vira pendência ou falha, nunca um palpite;
* nada aqui clica em baixa, pagamento, remessa ou autorização (§17);
* o navegador fica visível, para o usuário acompanhar.

O módulo é importado sem Playwright instalado sem quebrar: a importação real
acontece dentro de `abrir()`, para que o modo planejar funcione em máquinas
onde o navegador ainda não foi instalado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from . import seletores as S
from .configuracao import conta_e_cartao
from .modelos import Duplicidade, Rateio, Titulo
from .normalizacao import (
    chave_comparacao_nome,
    codigo_confere,
    codigos_em,
    competencia_iso,
    contem_palavra,
    data_br,
    data_iso,
    dinheiro,
    formatar_dinheiro_br,
    formatar_percentual_br,
    nome_da_opcao,
    normalizar_documento,
    para_data,
    para_decimal,
    para_percentual,
    periodo_mensal,
    nomes_divergem,
    semelhanca,
    valor_unico_em_linha,
    sem_acentos,
)


# Correspondências comprovadas em uso real. O config.json continua tendo
# prioridade; estes padrões só evitam que uma razão social conhecida volte a
# bloquear uma recuperação depois de o próprio resultado do salvamento já ter
# confirmado o cadastro escolhido. Só empresas públicas ficam aqui: apelidos de
# fornecedores pessoa física ou pequenos ficam no config.json, que não vai para
# o GitHub.
APELIDOS_FORNECEDORES_CONFIRMADOS = {
    "EMPRESA EXEMPLO DE BENEFICIOS - BENEFIX": (
        "EMPRESA EXEMPLO DE BENEFICIOS E PAGAMENTOS LTDA"
    ),
    "IPTU 2026 - 05/10": "IPTU",
}


class ErroNavegador(Exception):
    """Falha recuperável: o título é abandonado, a execução continua."""


class ErroFatal(Exception):
    """Falha que exige parar tudo (§22): sessão, seletores, conta, CAPTCHA."""


@dataclass
class ResultadoInclusao:
    id_ongsys: str = ""
    documento_final: str = ""
    fornecedor_selecionado: str = ""
    # Campos que o usuário precisa corrigir à mão no Ongsys (§13).
    pendencias: list[str] = field(default_factory=list)
    # Informações da execução que não pedem correção de campo (tipo de
    # pagamento vindo do cadastro, documento variado para ".1" etc.).
    avisos: list[str] = field(default_factory=list)
    salvo: bool = False


class OngsysBot:
    """Sessão de navegador sobre a tela de Contas a Pagar."""

    def __init__(self, config: dict, pasta_perfil: Path, pasta_evidencias: Path):
        self.config = config
        self.pasta_perfil = pasta_perfil
        self.pasta_evidencias = pasta_evidencias
        self.timeout = int(config.get("timeout_ms", 15000))
        self.playwright = None
        self.contexto = None
        self.page = None
        # Escopo do modal "Inclusão de Contas a Pagar" ativo, quando aberto.
        # A tela tem campos com o mesmo ID escondidos em outros modais; uma
        # busca sem escopo pode atingir o elemento errado, silenciosamente
        # (comportamento observado em uso real do Ongsys). Ver `_campo`.
        self._modal_inclusao = None
        # Contexto do título sendo preenchido, para que QUALQUER clique no
        # formulário (não só os pontos onde já era esperado) possa resolver
        # um aviso de documento duplicado que apareça no meio do caminho —
        # observado interceptando desde opções de autocomplete até a própria
        # aba "Rateio e Parcelamento". Ver `_resolver_duplicado_pendente`.
        self._duplicado_titulo = None
        self._duplicado_resultado = None
        self._duplicado_tentados: set[str] = set()
        # Fica True assim que o botão Salvar aceita o clique. A camada de
        # checkpoint usa essa informação para nunca transformar uma falha
        # pós-clique em FALHA retentável — o título pode já ter sido criado.
        self.salvar_foi_clicado = False
        # Relação observada nas linhas da lista: ID numérico -> Cód. Lançamento
        # (CP...). O segundo é aceito pelo campo "Buscar" da tabela e serve
        # para reabrir o título sem colocar um ID no filtro de fornecedor.
        self._codigos_lancamento_por_id: dict[str, str] = {}
        # Texto completo observado em cada linha da consulta. A recuperação
        # usa documento, valor, emissão e vencimento visíveis para descartar
        # um título incompatível antes de tentar abri-lo — importante para
        # registros já baixados, que não oferecem o ícone de lápis.
        self._texto_linha_por_id: dict[str, str] = {}
        # Explica ao operador por que um projetado foi escolhido quando o
        # valor antigo estiver divergente e for sobrescrito pela planilha.
        self.ultimo_aviso_projetado = ""

    # -- ciclo de vida -----------------------------------------------------

    def abrir(self) -> "OngsysBot":
        # Esta trava independe da instalação do Playwright. Conferi-la antes
        # da importação garante que uma configuração insegura seja recusada
        # sempre, inclusive numa máquina ainda não preparada.
        if not self.config.get("manter_navegador_visivel", True):
            raise ErroFatal(
                "Esta versão só roda com o navegador visível.\n"
                'Deixe "manter_navegador_visivel": true no config.json.'
            )

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as erro:
            raise ErroFatal(
                "O Playwright não está instalado nesta pasta.\n"
                "Feche esta janela e execute instalar.bat novamente."
            ) from erro

        self.pasta_perfil.mkdir(parents=True, exist_ok=True)
        self.pasta_evidencias.mkdir(parents=True, exist_ok=True)
        self.playwright = sync_playwright().start()
        self.contexto = self.playwright.chromium.launch_persistent_context(
            str(self.pasta_perfil),
            headless=False,
            viewport={"width": 1440, "height": 1000},
        )
        self.contexto.set_default_timeout(self.timeout)
        self.page = self.contexto.pages[0] if self.contexto.pages else self.contexto.new_page()
        self.page.goto(self.config["ongsys_url"], wait_until="domcontentloaded")
        return self

    def fechar(self) -> None:
        try:
            if self.contexto:
                self.contexto.close()
        finally:
            if self.playwright:
                self.playwright.stop()

    def __enter__(self) -> "OngsysBot":
        return self.abrir()

    def __exit__(self, *_) -> None:
        self.fechar()

    # -- sessão ------------------------------------------------------------

    def aguardar_login_manual(self) -> None:
        print("\nO navegador foi aberto.")
        print("Faca o login manualmente e deixe a tela de Contas a Pagar aberta.")
        input("Quando terminar, volte aqui e pressione Enter... ")
        if self.sessao_ativa():
            print("Sessao confirmada. Ela fica salva apenas neste computador.")
        else:
            print("AVISO: nao foi possivel confirmar a tela de Contas a Pagar.")
            print("Confira se o login foi concluido antes de planejar ou executar.")

    def sessao_ativa(self) -> bool:
        """Confere se a tela de contas a pagar respondeu (§28)."""
        try:
            self.page.goto(self.config["ongsys_url"], wait_until="domcontentloaded")
            self.page.wait_for_timeout(1200)
            texto = sem_acentos(self.page.locator("body").inner_text())
        except Exception:
            return False
        if self._parece_captcha(texto):
            raise ErroFatal(
                "A tela apresentou verificação de segurança ou CAPTCHA.\n"
                "Resolva manualmente no navegador e rode o programa de novo."
            )
        if any(marca in texto for marca in ("ACESSAR", "ENTRAR", "SENHA", "LOGIN")) and \
                "CONTAS A PAGAR" not in texto:
            return False
        return "CONTAS A PAGAR" in texto

    def exigir_sessao(self) -> None:
        if not self.sessao_ativa():
            raise ErroFatal(
                "A sessão do Ongsys não está ativa ou expirou.\n"
                "Execute login.bat, faça o login manualmente e tente de novo."
            )

    @staticmethod
    def _parece_captcha(texto: str) -> bool:
        return any(m in texto for m in ("CAPTCHA", "NAO SOU UM ROBO", "VERIFICACAO DE SEGURANCA"))

    # -- primitivas seguras ------------------------------------------------

    def _detectar_modal_inclusao(self):
        """Localiza o modal "Inclusão de Contas a Pagar" visível.

        Usado para restringir todas as buscas de campo a ele — a página tem
        campos com o mesmo ID escondidos em outros modais/formulários, e uma
        busca sem escopo (`page.locator("#field_...")`) pode atingir o
        elemento errado, escondido, sem erro nenhum. Devolve None se não
        encontrar (a busca cai para a página inteira, como antes).
        """
        for seletor in (".modal:visible", "[role='dialog']:visible"):
            try:
                modal = self.page.locator(seletor).filter(has_text="Inclusão de Contas a Pagar")
                if modal.count() >= 1:
                    return modal.first
            except Exception:
                continue
        return self._detectar_modal_formulario()

    def _detectar_modal_formulario(self):
        """Localiza um formulário visível de inclusão ou consulta pelo campo fornecedor."""
        for seletor in (".modal:visible", "[role='dialog']:visible"):
            try:
                candidatos = self.page.locator(seletor)
                for indice in range(min(candidatos.count(), 5)):
                    candidato = candidatos.nth(indice)
                    if candidato.is_visible() and candidato.locator(S.CAMPO_FORNECEDOR).count():
                        return candidato
            except Exception:
                continue
        return None

    def _campo(self, seletor: str):
        """Locator de um campo, restrito ao modal de inclusão quando há um

        aberto. Fora do formulário (lista, menu Incluir), não há modal ainda
        e a busca cai para a página inteira, como sempre foi.
        """
        escopo = self._modal_inclusao if self._modal_inclusao is not None else self.page
        return escopo.locator(seletor)

    def _resolver_duplicado_pendente(self) -> bool:
        """Resolve o aviso de documento duplicado, se houver um pendente.

        Usa o contexto do título atualmente em preenchimento (guardado por
        `incluir()`). Diferente de só fechar o popup, isto de fato renomeia
        o documento — necessário porque fechar sem renomear faz o mesmo
        aviso reaparecer mais tarde, em qualquer novo clique.
        """
        if self._duplicado_titulo is None or self._duplicado_resultado is None:
            return False
        return self._resolver_documento_duplicado(
            self._duplicado_titulo, self._duplicado_resultado, self._duplicado_tentados
        )

    def _clicar_resiliente(self, obter_alvo, descricao: str):
        """Clica um elemento, tentando de novo se um aviso interceptar.

        `obter_alvo` é uma função sem argumentos que devolve o Locator a
        clicar — chamada de novo a cada tentativa, porque o elemento pode ter
        deixado de existir (por exemplo, uma lista de sugestões que fechou
        junto com o popup) e precisa ser buscado de novo, não só reclicado.
        Entre as tentativas, primeiro tenta resolver um documento duplicado
        pendente (não só dispensar o aviso) e, se não for esse o caso, fecha
        qualquer outro popup genérico.
        """
        ultimo_erro: Exception | None = None
        diagnostico: list[str] = []
        for tentativa in range(1, 5):
            try:
                alvo = obter_alvo()
                alvo.click(timeout=4000)
                self.page.wait_for_timeout(150)
                return
            except Exception as erro:
                ultimo_erro = erro
                resolveu = self._resolver_duplicado_pendente()
                if resolveu:
                    doc_atual = self._duplicado_resultado.documento_final if self._duplicado_resultado else "?"
                    ainda_la = False
                    try:
                        ainda_la = self.page.locator("#modalErro").count() > 0
                    except Exception:
                        pass
                    diagnostico.append(
                        f"#{tentativa}: duplicidade resolvida (documento agora '{doc_atual}'), "
                        f"#modalErro ainda na tela depois de fechar={ainda_la}"
                    )
                else:
                    # Nenhuma duplicidade NOVA para resolver — mas um
                    # #modalErro de uma duplicidade já resolvida antes pode
                    # ter ficado sem fechar (confirmado: ele não fecha
                    # sozinho). Tenta fechá-lo direto antes do genérico.
                    fechou_stale = self._fechar_modal_erro_confirmado()
                    if not fechou_stale:
                        self._fechar_popups(tentativas=2)
                    # Diagnóstico: registra exatamente o que foi visto (ou não
                    # visto) na tela nesta tentativa, para não precisar
                    # adivinhar de novo se isto voltar a falhar.
                    vista = (self._mensagens_da_tela() or self._texto_modal_erro())[:150]
                    modal_existe = False
                    try:
                        modal_existe = self.page.locator("#modalErro").count() > 0
                    except Exception:
                        pass
                    diagnostico.append(
                        f"#{tentativa}: nada resolvido (modalErro fechado agora={fechou_stale}, "
                        f"#modalErro ainda existe={modal_existe}, mensagem lida='{vista or '[vazia]'}')"
                    )
        raise ErroNavegador(
            f"Não foi possível clicar em {descricao} — um aviso pode ter ficado "
            f"cobrindo a tela mesmo após tentar fechá-lo. "
            f"Diagnóstico: {' | '.join(diagnostico)}. Último erro: {ultimo_erro}"
        )

    def _clicar(self, seletor: str, descricao: str) -> None:
        S.conferir_permitido(seletor)
        S.conferir_permitido(descricao)
        if not self._resolver_duplicado_pendente():
            self._fechar_popups(tentativas=1)
        self._clicar_resiliente(lambda: self._campo(seletor).first, descricao)

    def _preencher(self, seletor: str, valor: str) -> None:
        S.conferir_permitido(seletor)
        campo = self._campo(seletor).first
        campo.fill("")
        campo.fill(valor)

    def _preencher_dinheiro(self, seletor: str, valor: Decimal, descricao: str) -> None:
        """Preenche um campo de moeda e CONFERE o que ficou gravado na tela.

        Campos de valor no Ongsys têm máscara de moeda, que reinterpreta o
        texto digitado: em uso real, enviar "300,00" resultou em "300.000,00"
        na tela — mil vezes o valor certo. Como a regra exata da máscara não é
        conhecida (e pode mudar), aqui não se tenta adivinhá-la: cada formato
        candidato é preenchido, lido de volta e comparado com o valor
        esperado. O primeiro que conferir é aceito.

        Se nenhum formato produzir o valor correto, levanta erro em vez de
        seguir — um valor errado aqui viraria um título com o valor errado.
        """
        S.conferir_permitido(seletor)
        # Um popup pendente (como o de serviço não parametrizado, que pode
        # aparecer com atraso) rouba o foco no blur e o campo volta a 0,00
        # mesmo com o texto certo digitado. Fecha qualquer aviso solto antes
        # de tentar, para não gastar as tentativas de formato à toa. O
        # detector genérico nunca reconheceu o container desse aviso
        # específico em uso real, então a rota por proximidade de texto
        # entra como reforço, não como substituta.
        self._fechar_popups()
        self._fechar_por_proximidade_do_texto("não tem parametrizado")

        esperado = dinheiro(valor)
        inteiro_centavos = int(esperado.scaleb(2))
        candidatos = [
            f"{esperado:.2f}".replace(".", ","),   # 300,00  (sem separador de milhar)
            formatar_dinheiro_br(esperado),        # 1.234,50
            f"{esperado:.2f}",                     # 300.00
            str(inteiro_centavos),                 # 30000  (máscaras que montam a partir dos centavos)
        ]

        tentado: list[str] = []
        for texto in dict.fromkeys(candidatos):
            self._fechar_popups()  # defesa extra: um popup pode surgir entre tentativas
            self._fechar_por_proximidade_do_texto("não tem parametrizado")
            campo = self._campo(seletor).first
            campo.fill("")
            campo.type(texto, delay=20)
            # Tab, não blur(): a máscara de moeda do Ongsys reage à tecla Tab
            # de verdade para reformatar/validar — blur() programático nem
            # sempre dispara o mesmo processamento (relatado em uso real).
            campo.press("Tab")
            self.page.wait_for_timeout(200)
            na_tela = self._ler_valor(seletor)
            tentado.append(f"'{texto}' -> '{na_tela}'")
            if para_decimal(na_tela) == esperado:
                return

        raise ErroNavegador(
            f"{descricao}: não foi possível gravar o valor R$ {formatar_dinheiro_br(esperado)} "
            f"corretamente. Tentativas: {'; '.join(tentado)}. O título NÃO foi salvo."
        )

    def _ler_valor(self, seletor: str) -> str:
        try:
            return (self._campo(seletor).first.input_value() or "").strip()
        except Exception:
            try:
                return (self._campo(seletor).first.inner_text() or "").strip()
            except Exception:
                return ""

    def _ler_tipo_pagamento(self) -> str:
        """Lê o valor atual do campo Tipo de Pagamento, sem alterá-lo.

        O Ongsys preenche este campo automaticamente ao selecionar o fornecedor.
        O programa apenas registra o que foi escolhido, para informação.
        """
        try:
            campo = self._campo(S.CAMPO_TIPO_PAGAMENTO).first
            selecionado = campo.locator("option:checked").first.inner_text().strip()
            return selecionado
        except Exception:
            return self._ler_valor(S.CAMPO_TIPO_PAGAMENTO)

    def _selecionar_lista(self, seletor: str, rotulo: str, descricao: str) -> str:
        """Escolhe uma opção de <select> pelo texto visível e confere o resultado.

        A versão anterior usava códigos numéricos fixos ("2", "4", "6") que nunca
        foram confirmados na tela. Selecionar pelo rótulo e reler o campo evita
        gravar um tipo de despesa diferente do pretendido.
        """
        S.conferir_permitido(seletor)
        S.conferir_permitido(rotulo)
        campo = self._campo(seletor).first
        opcoes = [t.strip() for t in campo.locator("option").all_inner_texts()]

        exato = [o for o in opcoes if sem_acentos(o) == sem_acentos(rotulo)]
        parcial = [o for o in opcoes if sem_acentos(rotulo) and sem_acentos(rotulo) in sem_acentos(o)]
        escolhido = (exato or parcial or [None])[0]
        if escolhido is None:
            raise ErroNavegador(
                f"{descricao}: a opção '{rotulo}' não existe na tela.\n"
                f"Opções disponíveis: {', '.join(o for o in opcoes if o) or 'nenhuma'}.\n"
                "Ajuste o config.json para usar exatamente um dos nomes acima."
            )
        campo.select_option(label=escolhido)
        confirmado = campo.locator("option:checked").first.inner_text().strip()
        if sem_acentos(confirmado) != sem_acentos(escolhido):
            raise ErroNavegador(
                f"{descricao}: foi selecionado '{escolhido}', mas a tela ficou com "
                f"'{confirmado}'. A execução parou para não gravar dado errado."
            )
        return confirmado

    def _popups_genuinos(self) -> list:
        """Lista os avisos secundários visíveis — nunca o formulário principal.

        O formulário "Inclusão de Contas a Pagar" também é implementado como
        um modal, então um seletor genérico de modal captura os dois. Um
        candidato só conta como popup se NÃO contiver o campo de fornecedor
        do formulário principal dentro dele — assim o próprio formulário
        nunca é confundido com um aviso a ser fechado.
        """
        genuinos = []
        try:
            elementos = self.page.locator(S.CONTEINERES_POPUP)
            for indice in range(min(elementos.count(), 5)):
                candidato = elementos.nth(indice)
                if not candidato.is_visible():
                    continue
                if candidato.locator(S.CAMPO_FORNECEDOR).count() > 0:
                    continue  # é o próprio formulário, não um popup
                genuinos.append(candidato)
        except Exception:
            pass
        return genuinos

    def _algum_popup_visivel(self) -> bool:
        return bool(self._popups_genuinos())

    def _fechar_popups(self, tentativas: int = 4) -> bool:
        """Fecha avisos como o de fornecedor não parametrizado (§10).

        Tenta algumas vezes porque o popup pode demorar a renderizar depois
        do clique que o disparou (a falha original acontecia por checar só
        uma vez, cedo demais). Devolve True quando, ao final, nenhum popup
        conhecido continua visível — o chamador deve tratar False como um
        motivo para parar em vez de seguir preenchendo o formulário.

        O botão de fechar é procurado SEMPRE dentro do popup encontrado, nunca
        na página inteira: o formulário principal também tem um botão
        "Fechar" (o dele mesmo, legítimo), e uma busca sem escopo correria o
        risco de clicar nesse botão errado e fechar o cadastro inteiro em vez
        do aviso.

        Não usa a tecla Esc como recurso: Esc é um evento de página, não
        escopado ao popup, e pode fechar o formulário principal inteiro em
        vez do aviso (comportamento observado em uso real). Sem um botão
        reconhecido dentro do popup, a tentativa simplesmente falha — mais
        seguro que arriscar perder tudo o que já foi preenchido.

        A primeira checagem é imediata, sem espera nenhuma: esta função é
        chamada de forma defensiva antes de QUALQUER clique em opção de
        autocomplete (mesmo sem popup nenhum na tela, o caso mais comum), e
        uma pausa de 300ms toda vez atrasa o clique o suficiente para a
        lista de sugestões do Ongsys fechar sozinha antes de ser clicada
        (comportamento observado em uso real). A pausa só entra a partir da
        segunda tentativa, quando já se sabe que existe um popup de verdade
        para esperar.
        """
        for tentativa in range(tentativas):
            if tentativa > 0:
                self.page.wait_for_timeout(300)
            popups = self._popups_genuinos()
            if not popups:
                return True
            for popup in popups:
                try:
                    # Segurança: um aviso que fala de exclusão, baixa, pagamento
                    # ou remessa nunca recebe OK/Confirmar/Continuar. Só pode
                    # ser dispensado (Fechar, Cancelar, Não, ×). Se não houver
                    # botão de dispensa, o aviso fica na tela e o chamador para.
                    cautela = self._popup_exige_cautela(popup)
                    botoes = popup.locator(S.POPUP_FECHAR)
                    for indice in range(min(botoes.count(), 3)):
                        botao = botoes.nth(indice)
                        if not botao.is_visible():
                            continue
                        rotulo = sem_acentos(botao.inner_text())
                        if any(p in rotulo for p in S.TEXTOS_PROIBIDOS):
                            continue
                        if cautela and not S.rotulo_so_dispensa(rotulo):
                            continue
                        botao.click()
                        self.page.wait_for_timeout(250)
                except Exception:
                    pass
        return not self._algum_popup_visivel()

    def _popup_exige_cautela(self, popup) -> bool:
        """Lê o texto do aviso e diz se ele pede confirmação sensível.

        Registra o caso em ``avisos_seguranca`` e no acompanhamento, para que
        um aviso inesperado nunca passe despercebido.
        """
        try:
            texto = popup.inner_text()
        except Exception:
            return False
        if not isinstance(texto, str) or not S.popup_exige_cautela(texto):
            return False
        resumo = " ".join(texto.split())[:160]
        if not hasattr(self, "avisos_seguranca"):
            self.avisos_seguranca = []
        if resumo not in self.avisos_seguranca:
            self.avisos_seguranca.append(resumo)
            print(
                "      [seguranca] aviso sensivel na tela NAO foi confirmado "
                f"pela automacao: '{resumo}'"
            )
        return True

    def _acao_de_linha_proibida(self, elemento) -> bool:
        """True se o ícone da linha for de excluir, baixar/pagar ou remeter.

        Defesa em profundidade: os seletores já pedem só lápis/olho, mas a
        conferência do próprio elemento impede o clique mesmo se a tela mudar.
        Lê apenas título, aria-label e classes (do ícone e do link/botão pai).
        """
        try:
            descricao = elemento.evaluate(
                """e => {
                    const pai = e.closest('a,button') || e;
                    const filhos = Array.from(pai.querySelectorAll('i,span'))
                        .map(f => f.className || '').join(' ');
                    return [e.title, e.getAttribute('aria-label'), e.className,
                            pai.title, pai.getAttribute('aria-label'),
                            pai.className, filhos].join(' ');
                }"""
            )
        except Exception:
            return False
        if not isinstance(descricao, str):
            return False
        return S.acao_de_linha_proibida(descricao)

    def _exigir_dominio_ongsys(self, momento: str) -> None:
        """Para tudo se a página não estiver no endereço configurado do Ongsys.

        Antes de gravar qualquer coisa, confere que o navegador continua no
        mesmo servidor do ``ongsys_url``. Um redirecionamento inesperado para
        outro site interrompe a execução sem salvar.
        """
        from urllib.parse import urlparse

        atual = getattr(self.page, "url", "")
        if not isinstance(atual, str) or not atual or atual.startswith("about:"):
            return
        esperado = (urlparse(str(self.config.get("ongsys_url", ""))).hostname or "").lower()
        encontrado = (urlparse(atual).hostname or "").lower()
        # Aceita o mesmo domínio com ou sem "www" e seus subdomínios; qualquer
        # outro site é recusado.
        base = esperado[4:] if esperado.startswith("www.") else esperado
        mesmo_site = encontrado == base or encontrado.endswith("." + base)
        if esperado and encontrado and not mesmo_site:
            raise ErroFatal(
                f"Seguranca: ao {momento}, o navegador estava em '{encontrado}', "
                f"e nao no endereco configurado do Ongsys ('{esperado}').\n"
                "A execução parou sem salvar. Confira o navegador e o config.json."
            )

    def _candidatos_autocomplete(self, seletor: str, consulta: str) -> list[str]:
        S.conferir_permitido(seletor)
        # O campo de busca é escopado ao modal (evita atingir um campo igual
        # escondido em outro modal); a lista de sugestões que aparece NÃO é
        # escopada de propósito — muitos widgets de autocomplete anexam a
        # lista direto no <body>, fora da árvore do modal.
        campo = self._campo(seletor).first
        # Alguns campos do rateio (Projeto/Centro de Custos) só ficam
        # habilitados depois que a Categoria/Conta é selecionada e o onchange
        # dela termina de processar. Se o campo ainda estiver desabilitado,
        # espera habilitar antes de digitar — evita o erro "element is not
        # enabled" visto na MONICA (Projeto/Centro de Custos "GESTÃO FRENTE PP").
        for _ in range(20):
            try:
                if campo.is_enabled():
                    break
            except Exception:
                break
            self.page.wait_for_timeout(300)
        campo.fill("")
        campo.type(consulta, delay=30)
        self._registrar_digitacao(seletor, consulta)
        try:
            # 3s bastava para o fornecedor, mas Centro de Custos/Categoria
            # não mostraram nenhuma lista dentro desse tempo em uso real,
            # mesmo sem popup nenhum na tela — a resposta é mais lenta.
            self.page.wait_for_selector(S.OPCOES_AUTOCOMPLETE, timeout=6000)
        except Exception:
            # Sem lista, nada foi selecionado. O texto digitado não pode
            # ficar no campo: ele é igual ao nome esperado e era lido na
            # passada seguinte como se fosse uma seleção (BENEFIX, "GESTAO ADM",
            # que o Ongsys só encontra como "GESTÃO ADM").
            self._limpar_digitacao(seletor)
            return []
        opcoes = self.page.locator(S.OPCOES_AUTOCOMPLETE)
        return [opcoes.nth(i).inner_text().strip() for i in range(opcoes.count())]

    def _registrar_digitacao(self, seletor: str, texto: str) -> None:
        if not hasattr(self, "_texto_digitado"):
            self._texto_digitado = {}
        self._texto_digitado[seletor] = texto

    def _limpar_digitacao(self, seletor: str) -> None:
        """Apaga do campo um texto de busca que não virou seleção."""
        try:
            campo = self._campo(seletor).first
            if campo.is_enabled():
                campo.fill("")
        except Exception:
            pass

    def _valor_e_so_digitacao(self, seletor: str, atual: str) -> bool:
        """True quando o campo mostra apenas o texto que a automação digitou.

        No typeahead do Ongsys, digitar não seleciona nada: a opção precisa
        ser clicada. Com o nome da planilha igual ao nome esperado, o texto
        digitado passava na conferência e o Ongsys apagava o campo depois,
        recusando a próxima linha do rateio. Uma seleção real trava o campo
        ou troca o texto pelo da opção; campo editável com o mesmo texto
        digitado não é seleção.
        """
        digitado = getattr(self, "_texto_digitado", {}).get(seletor)
        if not digitado or not atual:
            return False
        if chave_comparacao_nome(atual) != chave_comparacao_nome(digitado):
            return False
        if self.page is None:
            return True
        try:
            return bool(self._campo(seletor).first.is_enabled())
        except Exception:
            return True

    def _selecao_confere(
        self,
        seletor: str,
        atual: str,
        esperado: str,
        *,
        aceitar_prefixo: bool = False,
        codigo: bool = False,
    ) -> bool:
        """Conferência de autocomplete que recusa texto apenas digitado.

        Aceita o nome da planilha ou, no mesmo campo, a opção que a própria
        automação escolheu por semelhança neste título.
        """
        if codigo:
            confere = codigo_confere(esperado, atual)
        else:
            confere = self._nome_selecionado_confere(
                atual, esperado, aceitar_prefixo=aceitar_prefixo
            )
            aceita = getattr(self, "_opcoes_aceitas", {}).get(seletor)
            if not confere and aceita:
                confere = self._nome_selecionado_confere(atual, aceita)
        return confere and not self._valor_e_so_digitacao(seletor, atual)

    def _registrar_opcao_aceita(self, seletor: str, opcao: str) -> None:
        if not hasattr(self, "_opcoes_aceitas"):
            self._opcoes_aceitas = {}
        self._opcoes_aceitas[seletor] = opcao

    def _achar_opcao(self, texto: str):
        opcoes = self.page.locator(S.OPCOES_AUTOCOMPLETE)
        for indice in range(opcoes.count()):
            if opcoes.nth(indice).inner_text().strip() == texto:
                return opcoes.nth(indice)
        raise ErroNavegador(f"A opção '{texto}' sumiu da lista antes do clique.")

    def _clicar_opcao(self, texto: str) -> None:
        # Nenhuma verificação de popup ANTES do clique: opções de autocomplete
        # são efêmeras — a lista suspensa do Ongsys fecha sozinha em ~300-500ms
        # se qualquer coisa tirar o foco ou demorar demais. As consultas ao DOM
        # que `_resolver_duplicado_pendente` e `_fechar_popups` fazem (~400ms
        # somadas) eram suficientes para a lista fechar antes do clique, causando
        # "não foi localizado com segurança" em TODAS as tentativas. Se um popup
        # estiver de fato no caminho, `_clicar_resiliente` trata na retentativa
        # (resolve duplicidade, fecha popup e refaz a busca da opção).
        self._clicar_resiliente(lambda: self._achar_opcao(texto), f"a opção '{texto}'")

    # -- seleções de negócio ----------------------------------------------

    def resolver_apelido(self, nome: str) -> str:
        """Nome de busca, trocado pelo apelido configurado quando existir (§10)."""
        apelidos = self.config.get("apelidos_fornecedores", {})
        configurado = apelidos.get(nome) or apelidos.get(sem_acentos(nome))
        # Um mapeamento identidade antigo (NOME -> o mesmo NOME) não deve
        # esconder uma razão social posteriormente confirmada na tela real.
        if configurado and sem_acentos(configurado) != sem_acentos(nome):
            return configurado
        return (
            APELIDOS_FORNECEDORES_CONFIRMADOS.get(sem_acentos(nome))
            or configurado
            or nome
        )

    def _resolver_apelido_atividade(self, nome: str) -> str:
        """Troca o nome da atividade pelo apelido configurado, se existir.

        Confirmado em uso real: a planilha usa "SEDE" como atividade, mas o
        Ongsys tem "Despesas ADM". O mapeamento fica em `apelidos_atividades`
        no config.json, sem exigir edição do código.
        """
        apelidos = self.config.get("apelidos_atividades", {})
        return apelidos.get(nome) or apelidos.get(sem_acentos(nome)) or nome

    @staticmethod
    def construir_consultas_fornecedor(alvo: str) -> list[str]:
        """Ordem de tentativas de busca: nome completo, sem acentos, primeiro
        nome e, por fim, tokens distintivos (siglas/códigos).

        Quando o nome começa com uma palavra genérica ("INSTITUTO",
        "ASSOCIACAO") o cadastro certo pode não vir entre os primeiros
        resultados dessa palavra — foi o caso de "INSTITUTO SABER PARA
        TODOS - INSTITUTO Q9Z", que não aparecia buscando "INSTITUTO". Por isso
        também se busca por tokens distintivos: primeiro os que contêm dígito
        (como "Q9Z"), depois as demais palavras significativas. A conferência
        por semelhança (>= mínimo) e a exigência de opção única continuam
        protegendo contra escolher o fornecedor errado.

        Função pura, sem depender do navegador, para poder ser testada sozinha.
        """
        palavras = [p for p in alvo.split() if len(p) > 2]
        consultas = [alvo, sem_acentos(alvo)]
        if palavras:
            consultas += [palavras[0], sem_acentos(palavras[0])]
            distintivos = [p for p in palavras if any(c.isdigit() for c in p)]
            outras = [p for p in palavras[1:] if p not in distintivos]
            for palavra in distintivos + outras:
                consultas += [palavra, sem_acentos(palavra)]
        return list(dict.fromkeys(c for c in consultas if c))

    #: Trava contra escolher outra pessoa/empresa por semelhança. Delegada à
    #: função pura em normalizacao (reusada pela auditoria em cli.py).
    _nomes_divergem = staticmethod(nomes_divergem)

    def selecionar_fornecedor(self, nome: str) -> str:
        """Busca em várias formas, mas só aceita candidato semelhante (§10).

        Tenta primeiro o apelido configurado. Se nada seguro aparecer, tenta o
        nome exatamente como está na planilha: um apelido pode ficar velho
        quando o cadastro é renomeado no Ongsys (NORTE VIDA, 2026-10-02 — o
        cadastro passou a ter o nome da planilha e o apelido antigo impedia a
        seleção). As mesmas travas de semelhança e de nome divergente valem
        para as duas tentativas.
        """
        minimo = self.config["semelhanca_minima_fornecedor"]
        alvos = list(dict.fromkeys(a for a in (self.resolver_apelido(nome), nome) if a))

        tentativas: list[str] = []
        melhor_geral = (Decimal("0"), "")

        for alvo, consulta in (
            (alvo, consulta)
            for alvo in alvos
            for consulta in self.construir_consultas_fornecedor(alvo)
        ):
            candidatos = self._candidatos_autocomplete(S.CAMPO_FORNECEDOR, consulta)
            tentativas.append(f"'{consulta}' -> {len(candidatos)} resultado(s)")
            if not candidatos:
                continue
            # Descarta candidatos que DIVERGEM no nome (pessoa/empresa diferente
            # que só compartilha alguns tokens). Sem isso, "ANA CLARA MOREIRA
            # ROCHA" era criada no cadastro de "ANA DO CARMO ROCHA MOREIRA".
            candidatos = [c for c in candidatos if not self._nomes_divergem(alvo, c)]
            if not candidatos:
                continue
            pontuados = sorted(
                ((Decimal(str(semelhanca(alvo, c))), c) for c in candidatos),
                key=lambda x: x[0],
                reverse=True,
            )
            if pontuados[0] > melhor_geral:
                melhor_geral = pontuados[0]
            nota, texto = pontuados[0]
            if nota < minimo:
                continue
            # Empate no topo significa ambiguidade real: não dá para escolher.
            empatados = [c for n, c in pontuados if n == nota]
            # Exceção: um único cadastro com o nome IDÊNTICO ao esperado vence
            # os que apenas contêm esse nome. "EE HERMINIA CASTANHEIRA FONTES"
            # empatava com "CAIXA CUST ESC EEPSG DONA HERMINIA CASTANHEIRA
            # FONTES" e o título falhava (2026-10-02).
            identicos = [
                c for c in empatados
                if normalizar_documento(nome_da_opcao(c)) == normalizar_documento(alvo)
            ]
            if len(empatados) > 1 and len(identicos) == 1:
                texto = identicos[0]
            elif len(empatados) > 1:
                raise ErroNavegador(
                    f"Fornecedor ambíguo para '{nome}'. Candidatos igualmente parecidos: "
                    f"{'; '.join(empatados[:4])}.\n"
                    'Defina o nome exato em "apelidos_fornecedores" no config.json.'
                )
            self._clicar_opcao(texto)
            if not self._fechar_popups():
                raise ErroNavegador(
                    f"Um aviso apareceu depois de selecionar o fornecedor '{texto}' "
                    "(comum quando o fornecedor não está parametrizado no Ongsys) e "
                    "não fechou automaticamente. O título não foi preenchido nem salvo."
                )
            return texto

        raise ErroNavegador(
            f"Fornecedor '{nome}' não foi localizado com segurança.\n"
            f"Buscas feitas: {'; '.join(tentativas) or 'nenhuma'}.\n"
            f"Melhor candidato: {melhor_geral[1] or 'nenhum'} "
            f"(semelhança {melhor_geral[0]}, mínimo exigido {minimo}).\n"
            'Se o cadastro tiver outro nome, informe-o em "apelidos_fornecedores".'
        )

    def _conta_e_cartao(self, titulo: Titulo) -> bool:
        """Identifica cartão pelo nome da conta pagadora configurada."""
        nome = self.config.get("contas_pagadoras", {}).get(
            titulo.conta_pagadora_id, titulo.conta_pagadora_nome
        )
        return conta_e_cartao(self.config, titulo.conta_pagadora_id, nome)

    def _categorias_fornecedor_cartao(self, titulo: Titulo) -> list[str]:
        """Nomes que podem ocupar o campo Nome no cadastro especial ``Outros``.

        Esses cadastros também são usados quando a planilha é executada sem
        conta pagadora. Portanto, a presença de ``CARTAO`` no nome da conta
        não pode ser o único sinal. A seleção continua segura porque o caminho
        especial só aceita uma sugestão que contenha, ao mesmo tempo, o
        estabelecimento e uma categoria detalhada do próprio título.
        """
        if titulo.conta_pagadora_id and not self._conta_e_cartao(titulo):
            return []
        return list(dict.fromkeys(
            r.categoria_detalhada.strip()
            for r in titulo.rateios
            if r.categoria_detalhada.strip()
        ))

    def fornecedores_esperados_na_tela(self, titulo: Titulo) -> list[str]:
        """Alternativas legítimas exibidas no campo Fornecedor do Ongsys.

        Em alguns cadastros de cartão, o estabelecimento fica no campo
        CPF/CNPJ (tipo Outros) e o campo Nome recebe a categoria detalhada.
        A tela de leitura pode devolver somente esse Nome.
        """
        return list(dict.fromkeys([
            self.resolver_apelido(titulo.fornecedor),
            # O nome da planilha também é legítimo: o cadastro pode ter sido
            # renomeado no Ongsys depois de o apelido ser criado.
            titulo.fornecedor,
            *self._categorias_fornecedor_cartao(titulo),
        ]))

    def selecionar_fornecedor_titulo(self, titulo: Titulo) -> str:
        """Seleciona fornecedor, reconhecendo o cadastro especial de cartão.

        Para cartão, primeiro procura uma opção que contenha AO MESMO TEMPO o
        estabelecimento da planilha e uma categoria detalhada do título. Isso
        cobre opções como ``LICENÇ(C)A DE USO - ESTABELECIMENTO``. Na ausência
        dessa estrutura, conserva o fluxo normal de fornecedor.
        """
        categorias = self._categorias_fornecedor_cartao(titulo)
        if not categorias:
            return self.selecionar_fornecedor(titulo.fornecedor)

        estabelecimento = self.resolver_apelido(titulo.fornecedor)
        minimo = self.config["semelhanca_minima_fornecedor"]
        consultas: list[str] = self.construir_consultas_fornecedor(estabelecimento)
        for categoria in categorias:
            consultas.extend(self.construir_consultas_fornecedor(categoria))
        consultas = list(dict.fromkeys(consultas))

        correspondencias: dict[str, tuple[Decimal, str]] = {}
        for consulta in consultas:
            candidatos = self._candidatos_autocomplete(S.CAMPO_FORNECEDOR, consulta)
            for candidato in candidatos:
                nota_estabelecimento = Decimal(str(semelhanca(estabelecimento, candidato)))
                categorias_compativeis = [
                    categoria for categoria in categorias
                    if Decimal(str(semelhanca(categoria, candidato))) >= minimo
                ]
                if nota_estabelecimento < minimo or not categorias_compativeis:
                    continue
                melhor_categoria = max(
                    categorias_compativeis,
                    key=lambda categoria: semelhanca(categoria, candidato),
                )
                correspondencias[candidato] = (nota_estabelecimento, melhor_categoria)

        if len(correspondencias) > 1:
            raise ErroNavegador(
                "Fornecedor de cartão ambíguo. Mais de um cadastro contém "
                f"o estabelecimento '{titulo.fornecedor}' e a categoria detalhada: "
                + "; ".join(list(correspondencias)[:4])
                + ". O título não foi salvo."
            )
        if len(correspondencias) == 1:
            texto = next(iter(correspondencias))
            try:
                self._clicar_opcao(texto)
            except ErroNavegador:
                # A lista é efêmera: o Ongsys pode aplicar a opção e fechar o
                # typeahead antes de o Playwright concluir o clique (IAPRO
                # 31.08). Nesse caso, a tela já contém a categoria correta.
                atual = self._ler_valor(S.CAMPO_FORNECEDOR)
                if not any(
                    self._nome_selecionado_confere(atual, categoria)
                    for categoria in categorias
                ):
                    raise
            atual = self._ler_valor(S.CAMPO_FORNECEDOR)
            if self.page is None:  # testes puros sem navegador
                atual = categorias[0]
            if not any(
                self._nome_selecionado_confere(atual, categoria)
                or self._nome_selecionado_confere(atual, texto)
                for categoria in categorias
            ):
                raise ErroNavegador(
                    "A opção do fornecedor especial foi acionada, mas o campo "
                    f"Fornecedor não confirmou a categoria esperada. Encontrado: "
                    f"'{atual or '[vazio]'}'. O título não foi salvo."
                )
            if not self._fechar_popups():
                raise ErroNavegador(
                    f"Um aviso apareceu depois de selecionar o fornecedor '{texto}' "
                    "e não fechou automaticamente. O título não foi salvo."
                )
            return texto

        # Nem todo fornecedor de cartão usa o cadastro especial. Mantém o
        # comportamento normal para cadastros cujo Nome é o estabelecimento.
        return self.selecionar_fornecedor(titulo.fornecedor)

    def _fechar_por_proximidade_do_texto(self, frase_marcador: str) -> bool:
        """Fecha um aviso localizando o botão pela POSIÇÃO no DOM, não pela

        estrutura do container. Em uso real, o aviso de "serviço não
        parametrizado" nunca bateu com nenhum seletor de container conhecido
        (`.modal`, `[role=dialog]`, SweetAlert...) — o container real usa
        outra marcação, desconhecida. Em vez de continuar adivinhando classes
        CSS, localiza o texto do próprio aviso e pega o botão "Fechar" mais
        próximo *depois* dele no documento — o botão "Fechar" do formulário
        principal vem bem depois, após todos os campos, então normalmente não
        é alcançado por engano.
        """
        try:
            marcador = self.page.get_by_text(frase_marcador, exact=False)
            if marcador.count() == 0:
                return True  # esse aviso específico não está na tela
            botao = marcador.first.locator(
                "xpath=following::*[self::button or self::a or @role='button']"
                "[contains(normalize-space(.), 'Fechar')][1]"
            )
            if botao.count() and botao.first.is_visible():
                botao.first.click()
                self.page.wait_for_timeout(300)
        except Exception:
            pass
        try:
            return self.page.get_by_text(frase_marcador, exact=False).count() == 0
        except Exception:
            return False

    def _fechar_popup_de_servico_nao_parametrizado(self, tipo_despesa: str) -> None:
        """Fecha o aviso de "serviço não parametrizado" (§10, confirmado em uso real).

        O Ongsys mostra esse aviso especificamente ao selecionar Tipo de
        despesa "Contratação de Serviço(s)" quando o fornecedor não tem
        serviço cadastrado — não ao selecionar o fornecedor em si. Por isso a
        checagem fica aqui, logo após o Tipo de despesa, e só roda quando o
        tipo selecionado é mesmo o de serviço (evita uma espera desnecessária
        nos demais casos, que são a maioria dos títulos).
        """
        if sem_acentos(tipo_despesa) != sem_acentos(self.config["tipos_despesa"]["servico"]):
            return
        # Confirmado em uso real: essa validação é assíncrona e pode demorar
        # mais que o padrão de _fechar_popups (~1,2s) para aparecer. Dar menos
        # tempo aqui faz o programa concluir "sem popup" cedo demais e seguir
        # preenchendo com o aviso ainda a caminho — por isso mais tentativas.
        self.page.wait_for_timeout(1500)
        if self._fechar_popups(tentativas=8):
            return
        # O detector genérico de popup nunca bateu com este aviso em uso
        # real; tenta a rota específica por texto antes de desistir.
        if not self._fechar_por_proximidade_do_texto("não tem parametrizado"):
            raise ErroNavegador(
                f"Um aviso apareceu depois de selecionar Tipo de despesa '{tipo_despesa}' "
                "(comum quando o fornecedor não tem serviço parametrizado no Ongsys) "
                "e não fechou automaticamente. O título não foi salvo."
            )

    def testar_fornecedor(self, nome: str) -> list[dict]:
        """Digita o fornecedor no campo de busca e devolve as sugestões vistas.

        Modo de conferência: nenhuma sugestão é clicada e nada é salvo.

        Não tenta fechar popups aqui de propósito: como nenhuma opção é
        selecionada, o Ongsys pode reagir ao texto digitado com um aviso (por
        exemplo "fornecedor não parametrizado"), e clicar em qualquer botão às
        cegas nesse momento arrisca fechar o formulário inteiro em vez do
        aviso. Quem chama este método deve reabrir o formulário do zero antes
        do próximo fornecedor.
        """
        alvo = self.resolver_apelido(nome)
        minimo = self.config["semelhanca_minima_fornecedor"]
        resultados: list[dict] = []

        for consulta in self.construir_consultas_fornecedor(alvo):
            candidatos = self._candidatos_autocomplete(S.CAMPO_FORNECEDOR, consulta)
            pontuados = sorted(
                ((Decimal(str(semelhanca(alvo, c))), c) for c in candidatos),
                key=lambda x: x[0],
                reverse=True,
            )
            resultados.append({
                "consulta": consulta,
                "candidatos": [{"texto": c, "semelhanca": str(n)} for n, c in pontuados],
                "selecionaria": bool(pontuados) and pontuados[0][0] >= minimo
                and len([c for n, c in pontuados if n == pontuados[0][0]]) == 1,
            })
        return resultados

    def _selecionar_por_nome(
        self,
        seletor: str,
        esperado: str,
        descricao: str,
        tentativas: int = 5,
        *,
        aceitar_prefixo: bool = False,
    ) -> str:
        """Autocomplete de categoria/centro de custo, com conferência (§15).

        Se o clique na opção falhar (um aviso pode ter fechado a lista de
        sugestões junto com ele — observado em uso real), a busca inteira é
        refeita do zero: reclicar numa lista que já fechou nunca funciona,
        é preciso digitar de novo para abri-la de novo.

        `tentativas` permite reduzir a insistência em campos OPCIONAIS (a
        atividade, §14): com o campo travado, cada tentativa custa vários
        timeouts de 15s e a execução parece pendurada.
        """
        minimo = self.config["semelhanca_minima_fornecedor"]
        for _ in range(max(1, tentativas)):
            # O typeahead do Ongsys desabilita o input assim que uma opção é
            # selecionada. Em algumas rodadas o clique é efetivado, mas o
            # Playwright estoura o timeout antes de receber o retorno. Nesse
            # caso, repetir fill("") tenta escrever num campo já preenchido e
            # disabled. Antes de cada nova busca, aceite somente um valor que
            # confira EXATAMENTE com o nome esperado.
            atual = self._ler_valor(seletor)
            if self._selecao_confere(
                seletor, atual, esperado, aceitar_prefixo=aceitar_prefixo
            ):
                return atual
            self._exigir_autocomplete_editavel(seletor, esperado)
            escolhido = self._buscar_e_clicar_por_nome(
                seletor,
                esperado,
                minimo,
                aceitar_prefixo=aceitar_prefixo,
            )
            if escolhido is not None:
                return escolhido
            self._liberar_tela_para_autocomplete()
        raise ErroNavegador(
            f"{descricao} '{esperado}' não foi localizado com segurança. "
            "O título não foi salvo, para não gravar classificação errada."
        )

    def _liberar_tela_para_autocomplete(self) -> None:
        """Trata um aviso tardio que cobre o formulário entre duas tentativas.

        O clique rápido nas opções não consulta avisos (a lista fecha em
        ~300 ms). Em 18/09/2026, IVONE e TATIANA falharam na Categoria porque o
        aviso "documento fiscal já existe" apareceu durante o rateio — o
        "09_26.1" pré-incrementado também já existia — e engoliu todos os
        cliques. Aqui o duplicado é resolvido de verdade (próximo sufixo) ou,
        se não houver duplicado novo, um #modalErro remanescente é fechado.
        """
        if self.page is None:
            return
        try:
            if not self._resolver_duplicado_pendente():
                self._fechar_modal_erro_confirmado()
        except ErroFatal:
            raise
        except Exception:
            pass

    def _exigir_autocomplete_editavel(self, seletor: str, esperado: str) -> None:
        self._habilitar_campo_de_rateio(seletor, esperado)

    def _habilitar_campo_de_rateio(self, seletor: str, descricao: str) -> None:
        """Usa o lápis do Ongsys para editar um campo já salvo no rateio.

        Em títulos projetados os campos existentes chegam desabilitados. Tirar
        o atributo ``disabled`` diretamente contorna a interface, mas não
        executa as rotinas ``liberaCampo*`` do Ongsys. O formulário real oferece
        um lápis ao lado de cada campo; acioná-lo mantém o estado interno da
        edição consistente.
        """
        if self.page is None:
            return
        campo = self._campo(seletor).first
        try:
            if campo.is_enabled():
                return
        except Exception:
            return
        try:
            acionou = campo.evaluate(
                """el => {
                    const grupo = el.closest('.input-group');
                    const lapis = grupo && grupo.querySelector('.input-group-addon a');
                    if (!lapis) return false;
                    lapis.click();
                    return true;
                }"""
            )
            if acionou:
                for _ in range(10):
                    if campo.is_enabled():
                        return
                    self.page.wait_for_timeout(100)
        except Exception:
            pass
        raise ErroNavegador(
            f"Campo {seletor} bloqueado com '{self._ler_valor(seletor)}'; "
            f"esperado '{descricao}'. O lápis de edição não habilitou o campo."
        )

    def _aguardar_selecao(
        self,
        seletor: str,
        esperado: str,
        *,
        codigo: bool = False,
        aceitar_prefixo: bool = False,
    ) -> bool:
        for tentativa in range(8):
            if self._selecao_confere(
                seletor,
                self._ler_valor(seletor),
                esperado,
                aceitar_prefixo=aceitar_prefixo,
                codigo=codigo,
            ):
                return True
            if self.page is None:
                break
            self.page.wait_for_timeout(150)
        return False

    @staticmethod
    def _nome_selecionado_confere(
        atual: str, esperado: str, *, aceitar_prefixo: bool = False
    ) -> bool:
        """Confirma um autocomplete já selecionado sem usar semelhança frouxa."""
        if not atual or not esperado:
            return False
        alvo = chave_comparacao_nome(esperado)
        atual_completo = chave_comparacao_nome(atual)
        atual_nome = chave_comparacao_nome(nome_da_opcao(atual))
        esperado_nome = chave_comparacao_nome(nome_da_opcao(esperado))
        if (
            atual_completo == alvo
            or atual_nome == alvo
            or atual_nome == esperado_nome
        ):
            return True
        if not aceitar_prefixo:
            return False

        # Abreviação confirmada no cadastro real: a planilha pode trazer
        # ``REDES`` enquanto a opção do Ongsys é ``REDES MUNICIPAIS``. Esse
        # relaxamento é usado SOMENTE em Projeto/Centro de Custos e somente
        # quando o esperado termina exatamente numa fronteira de palavra.
        # Assim, ``GESTAO`` não aceita ``GESTAO FRENTE PP`` por acidente caso
        # exista mais de uma opção compatível (a seleção abaixo exige unicidade).
        atual_sem_espacos = sem_acentos(nome_da_opcao(atual))
        esperado_sem_espacos = sem_acentos(nome_da_opcao(esperado))
        return bool(
            esperado_sem_espacos
            and atual_sem_espacos.startswith(esperado_sem_espacos + " ")
        )

    @staticmethod
    def _consultas_prefixo_sem_acento(esperado: str) -> list[str]:
        """Prefixos sem acento para driblar o typeahead sensível a acento.

        Quebra o nome esperado em blocos alfanuméricos (separando também em
        "/", "-", espaço) e, para cada bloco com 4+ letras, gera prefixos sem
        acento de comprimento decrescente (6, 5, 4). O maior vem primeiro: é a
        busca mais específica e a que retorna a lista mais curta. Para
        "CONDUCAO/LOCOMOCAO" gera, entre outros, "CONDU" (que casa com
        "CONDUÇÃO"); para "GESTAO FRENTE PP" gera "GEST" (casa com "GESTÃO").

        Função pura (sem navegador) para poder ser testada isoladamente.
        """
        blocos = [b for b in re.split(r"[^0-9A-Za-zÀ-ÿ]+", esperado) if len(b) >= 4]
        prefixos: list[str] = []
        for bloco in blocos:
            bloco_sa = sem_acentos(bloco)
            for tamanho in (6, 5, 4):
                if len(bloco_sa) > tamanho:
                    prefixos.append(bloco_sa[:tamanho])
        return list(dict.fromkeys(prefixos))

    def _buscar_e_clicar_por_nome(
        self,
        seletor: str,
        esperado: str,
        minimo: Decimal,
        *,
        aceitar_prefixo: bool = False,
    ) -> str | None:
        """Uma passada de busca+clique. None significa "tentar de novo do zero"."""
        palavras = [p for p in esperado.split() if len(p) > 2]
        consultas = [esperado, sem_acentos(esperado)]
        if palavras:
            # Buscar pelo nome inteiro pode não achar nada se o cadastro
            # tiver uma grafia um pouco diferente (abreviação, hífen, espaço
            # a mais); a primeira palavra tende a bater mesmo assim, e a
            # conferência abaixo continua exigindo bater com o nome esperado.
            #
            # Todas as palavras entram como consulta, não só a primeira:
            # confirmado em uso real que o typeahead do Ongsys NÃO faz busca
            # sem acento — "GESTAO" não encontra "GESTÃO". A segunda palavra
            # ("ADM", no caso) pode não ter acento e achar o resultado.
            consultas.extend(palavras)

        # Consultas por PREFIXO sem acento. O typeahead do Ongsys é sensível a
        # acento (exige "CONDUÇÃO"), mas a planilha vem sem acento
        # ("CONDUCAO/LOCOMOCAO") — então nenhuma das consultas acima encontra a
        # categoria. Um prefixo curto, antes da primeira letra acentuada, é
        # igual nos dois ("CONDU" casa com "CONDUÇÃO", "GEST" casa com
        # "GESTÃO"). Essas consultas são marcadas para serem aceitas SOMENTE
        # por casamento exato de nome (nunca por semelhança), então uma busca
        # ampla como "GEST" não corre o risco de gravar a categoria errada:
        # ou existe uma opção com exatamente aquele nome, ou o título falha.
        consultas_prefixo = self._consultas_prefixo_sem_acento(esperado)
        consultas_prefixo = [c for c in consultas_prefixo if c not in set(consultas)]

        for consulta in dict.fromkeys(consultas + consultas_prefixo):
            atual = self._ler_valor(seletor)
            if self._selecao_confere(
                seletor, atual, esperado, aceitar_prefixo=aceitar_prefixo
            ):
                return atual
            self._exigir_autocomplete_editavel(seletor, esperado)
            so_exato = consulta in consultas_prefixo
            candidatos = self._candidatos_autocomplete(seletor, consulta)
            if not candidatos:
                continue

            # Desduplicar candidatos: o seletor OPCOES_AUTOCOMPLETE captura
            # opções de TODAS as typeahead visíveis na página. Quando a linha 1
            # do rateio já está preenchida, sua typeahead pode permanecer visível
            # e produzir entradas duplicadas (confirmado com CARTORIO no fornecedor de exemplo).
            # Textos idênticos são a mesma opção, não uma ambiguidade real.
            candidatos_unicos = list(dict.fromkeys(candidatos))

            # A comparação ignora acentos E espaços: o cadastro do Ongsys pode
            # ter espaçamento diferente da planilha — "CONDUCAO /LOCOMOÇÃO"
            # (com espaço antes da barra) contra "CONDUCAO/LOCOMOCAO". Sem isso,
            # a categoria era encontrada mas rejeitada na conferência.
            alvo_cmp = chave_comparacao_nome(esperado)
            exatos = [c for c in candidatos_unicos if chave_comparacao_nome(c) == alvo_cmp]
            if not exatos:
                # As opções do Ongsys vêm como "2.5.1.04.007 - LIMPEZA (A)":
                # o texto completo nunca bate exatamente. Comparar só pelo
                # nome (sem código nem sufixo) resolve empates como "LIMPEZA"
                # x "MATERIAL DE LIMPEZA", que a pontuação por substring abaixo
                # não consegue distinguir — as duas contêm a palavra inteira.
                exatos = [c for c in candidatos_unicos if chave_comparacao_nome(nome_da_opcao(c)) == alvo_cmp]

            if not exatos and aceitar_prefixo:
                esperado_nome = sem_acentos(nome_da_opcao(esperado))
                exatos = [
                    c
                    for c in candidatos_unicos
                    if sem_acentos(nome_da_opcao(c)).startswith(
                        esperado_nome + " "
                    )
                ]

            alvo = None
            if len(exatos) == 1:
                alvo = exatos[0]
            elif len(exatos) > 1:
                # Uma abreviação como ``REDES`` só é segura quando aponta
                # para uma única opção. Não use a pontuação de semelhança para
                # desempatar centros de custo reais.
                continue
            elif not so_exato:
                pontuados = sorted(
                    ((Decimal(str(semelhanca(esperado, c))), c) for c in candidatos_unicos),
                    key=lambda x: x[0],
                    reverse=True,
                )
                nota, texto = pontuados[0]
                if nota >= minimo and len([c for n, c in pontuados if n == nota]) == 1:
                    alvo = texto

            if alvo is not None:
                # Caminho rápido: clicar por ÍNDICE na lista que
                # _candidatos_autocomplete acabou de ler, sem re-ler
                # textos via _achar_opcao.  A lista suspensa é efêmera
                # (~300-500ms).  O fluxo antigo fazia count + inner_text
                # para cada opção duas vezes (uma em _candidatos_autocomplete,
                # outra em _achar_opcao) antes de clicar — o dobro de
                # roundtrips ao DOM.  Clicar pelo índice conhecido faz
                # 1 chamada ao DOM em vez de N+1.
                # Confirmado necessário para a linha 2 do rateio (RODA).
                # Usar o ÚLTIMO índice: quando há duplicatas de typeaheads
                # de linhas anteriores, o último elemento pertence à linha
                # atual (a que acabou de ser digitada).
                idx = len(candidatos) - 1 - candidatos[::-1].index(alvo)
                if not self._nome_selecionado_confere(
                    alvo, esperado, aceitar_prefixo=aceitar_prefixo
                ):
                    # Opção escolhida pela semelhança (melhor nota, única e
                    # acima do mínimo). As conferências seguintes comparam com
                    # o nome da planilha e rejeitavam a própria opção clicada:
                    # a BENEFIX (linha 4, "TELEFONE, INTERNET") entrava num ciclo
                    # de clicar, destravar pelo lápis e buscar de novo.
                    self._registrar_opcao_aceita(seletor, alvo)
                    print(
                        f"      [autocomplete] '{esperado}' sem opção de nome "
                        f"idêntico; escolhida por semelhança: '{alvo}'."
                    )
                try:
                    if self._clicar_opcao_do_typeahead(
                        idx,
                        seletor,
                        esperado,
                        aceitar_prefixo=aceitar_prefixo,
                    ):
                        return alvo
                    return None  # a lista fechou — refaz a busca do zero
                except AttributeError:
                    # Ambiente de teste (self.page é None) — fallback.
                    pass
                # Fallback: caminho original via _clicar_opcao
                try:
                    self._clicar_opcao(alvo)
                    if self.page is None:  # testes puros sem navegador
                        return alvo
                    if self._selecao_confere(
                        seletor,
                        self._ler_valor(seletor),
                        esperado,
                        aceitar_prefixo=aceitar_prefixo,
                    ):
                        return alvo
                    return None
                except ErroNavegador:
                    # O clique pode ter sido efetivado antes de a lista sumir.
                    if self._selecao_confere(
                        seletor,
                        self._ler_valor(seletor),
                        esperado,
                        aceitar_prefixo=aceitar_prefixo,
                    ):
                        return alvo
                    return None
        return None

    def _clicar_opcao_do_typeahead(
        self,
        idx: int,
        seletor: str,
        esperado: str,
        *,
        aceitar_prefixo: bool = False,
    ) -> bool:
        """Clica a opção do typeahead e confirma o valor que ficou no campo.

        O clique comum expira com frequência mesmo o Playwright reportando o
        elemento como *visible, enabled and stable*: algo invisível (backdrop ou
        ``#modalErro`` remanescente) fica por cima e engole o ponteiro.

        A versão anterior só tentava o JavaScript DEPOIS de esperar o timeout
        inteiro — e a lista suspensa do Ongsys fecha sozinha em ~300-500ms, ou
        seja, quando a alternativa entrava a opção já não existia mais (por isso
        a mensagem de recuperação nunca aparecia no log). Agora a alternativa é
        imediata e sem pausa nenhuma antes dela.

        ``dispatch_event`` e ``evaluate`` não fazem hit-testing, então acionam o
        handler mesmo com um overlay por cima. Nada é aceito no escuro: o campo
        é lido depois e só o valor exatamente igual ao esperado vale.
        """
        opcao = self.page.locator(S.OPCOES_AUTOCOMPLETE).nth(idx)
        try:
            opcao.click(timeout=5000)
            self.page.wait_for_timeout(150)
            # Clique sem erro não é prova de seleção: em MONITORA e GUARDABOX a
            # lista recebeu o clique, mas o campo obrigatório continuou vazio.
            return self._aguardar_selecao(
                seletor, esperado, aceitar_prefixo=aceitar_prefixo
            )
        except AttributeError:
            raise
        except Exception as erro:
            primeira_linha = str(erro).splitlines()[0] if str(erro) else type(erro).__name__
            print(f"      [clique autocomplete] indice {idx}: {primeira_linha}")

        for descricao, acionar in (
            ("dispatch_event", lambda: opcao.dispatch_event("click")),
            ("JavaScript", lambda: opcao.evaluate("el => el.click()")),
        ):
            if self._aguardar_selecao(
                seletor, esperado, aceitar_prefixo=aceitar_prefixo
            ):
                return True
            try:
                acionar()
            except Exception:
                continue
            self.page.wait_for_timeout(250)
            if self._selecao_confere(
                seletor,
                self._ler_valor(seletor),
                esperado,
                aceitar_prefixo=aceitar_prefixo,
            ):
                print(f"      [clique autocomplete] recuperado por {descricao}.")
                return True

        # Última chance: o Ongsys pode ter processado o clique original e só não
        # ter devolvido a confirmação a tempo do Playwright.
        self.page.wait_for_timeout(250)
        return self._selecao_confere(
            seletor,
            self._ler_valor(seletor),
            esperado,
            aceitar_prefixo=aceitar_prefixo,
        )

    def _selecionar_subprojeto(self, indice: int, codigo: str) -> tuple[str, str | None]:
        """Regra mais importante (§13): o código escolhido tem de ser idêntico.

        Devolve (texto escolhido, pendência). Código inexistente deixa o campo
        vazio e vira pendência; código parecido nunca é aceito.
        """
        seletor = S.subprojeto(indice)
        vistos: list[str] = []
        for consulta in dict.fromkeys([codigo, codigo.replace(".", " "), codigo.split(".")[0]]):
            atual = self._ler_valor(seletor)
            if self._selecao_confere(seletor, atual, codigo, codigo=True):
                return atual, None
            self._exigir_autocomplete_editavel(seletor, codigo)
            candidatos = self._candidatos_autocomplete(seletor, consulta)
            vistos.extend(candidatos)
            # Desduplicar: o seletor OPCOES_AUTOCOMPLETE pode capturar
            # opções de typeaheads de linhas anteriores do rateio,
            # produzindo entradas idênticas (confirmado com RODA, linha 2).
            corretos = list(dict.fromkeys(c for c in candidatos if codigo_confere(codigo, c)))
            if len(corretos) == 1:
                # Clicar no ÚLTIMO elemento com esse texto (pertence à
                # linha atual, não à typeahead de uma linha anterior).
                idx = len(candidatos) - 1 - candidatos[::-1].index(corretos[0])
                try:
                    self.page.locator(S.OPCOES_AUTOCOMPLETE).nth(idx).click(timeout=2000)
                    self.page.wait_for_timeout(150)
                except AttributeError:
                    self._clicar_opcao(corretos[0])
                except Exception:
                    self._clicar_opcao(corretos[0])
                if not self._aguardar_selecao(seletor, codigo, codigo=True):
                    # Regra do usuário (§13): código que não se confirma não
                    # derruba o título. O campo fica vazio e vira pendência.
                    retornou = self._ler_valor(seletor)
                    self._limpar_digitacao(seletor)
                    return "", self._subprojeto_preso_com_outro_codigo(
                        indice, codigo
                    ) or (
                        f"Linha {indice}: seleção do Subprojeto {codigo} não se "
                        f"confirmou (campo retornou '{retornou or '[vazio]'}'). "
                        "Campo deixado vazio; informe manualmente no Ongsys."
                    )
                return corretos[0], None
            if len(corretos) > 1:
                self._preencher(seletor, "")
                return "", (
                    f"Linha {indice}: há mais de uma opção com o código {codigo} "
                    f"({'; '.join(corretos[:3])}). Campo deixado vazio para decisão manual."
                )

        parecidos = [c for c in dict.fromkeys(vistos) if codigos_em(c)][:5]
        self._preencher(seletor, "")
        return "", self._subprojeto_preso_com_outro_codigo(indice, codigo) or (
            f"Linha {indice}: o código {codigo} não existe no Ongsys. "
            f"Campo Subprojeto deixado vazio. Opções vistas: "
            f"{'; '.join(parecidos) if parecidos else 'nenhuma'}."
        )

    def _subprojeto_preso_com_outro_codigo(self, indice: int, codigo: str) -> str | None:
        """Aviso quando o campo ficou travado com um código diferente.

        Decisão do usuário (11/09/2026): subprojeto NUNCA impede o lançamento.
        Mesmo nesse caso o título segue, mas a pendência diz exatamente o que
        corrigir no Ongsys.
        """
        seletor = S.subprojeto(indice)
        atual = self._ler_valor(seletor)
        if not atual or self._valor_e_so_digitacao(seletor, atual):
            return None
        if codigo_confere(codigo, atual):
            return None
        return (
            f"Linha {indice}: CORRIGIR Subprojeto — o campo ficou com '{atual}' "
            f"e não pôde ser esvaziado; o código da planilha é {codigo}."
        )

    # -- duplicidade -------------------------------------------------------

    def _selecionar_filtro_vencimento(self) -> bool:
        """Troca o critério De/Até de Emissão para Vencimento e confirma."""
        try:
            selects = self.page.locator(S.FILTRO_TIPO_DATA_LISTA)
            for indice in range(selects.count()):
                select = selects.nth(indice)
                opcoes = select.locator("option").all_inner_texts()
                normalizadas = [sem_acentos(texto) for texto in opcoes]
                if "EMISSAO" not in normalizadas or "VENCIMENTO" not in normalizadas:
                    continue
                rotulo = opcoes[normalizadas.index("VENCIMENTO")]
                select.select_option(label=rotulo)
                self.page.wait_for_timeout(300)
                atual = select.locator("option:checked").first.inner_text()
                return sem_acentos(atual) == "VENCIMENTO"
        except Exception:
            pass
        return False

    def _campo_busca_fornecedor(self):
        """Prioriza o input cujo placeholder diz fornecedor.

        Usar uma lista CSS com fallbacks e ``.first`` não garante prioridade:
        o Playwright devolve os elementos na ordem do DOM. Na tela real isso
        podia selecionar outro ``#field_busca`` antes do campo correto.
        """
        principal = self.page.locator(S.CAMPO_BUSCA_FORNECEDOR).first
        if principal.count():
            return principal
        return self.page.locator(S.CAMPO_BUSCA_FORNECEDOR_FALLBACK).first

    def _selecionar_fornecedor_filtro(self, nome: str) -> str:
        """Digita, escolhe e confirma o fornecedor no autocomplete da lista."""
        alvo = self.resolver_apelido(nome)
        minimo = self.config["semelhanca_minima_fornecedor"]
        tentativas: list[str] = []

        for consulta in self.construir_consultas_fornecedor(alvo):
            campo = self._campo_busca_fornecedor()
            campo.fill("")
            campo.type(consulta, delay=30)
            try:
                self.page.wait_for_selector(S.OPCOES_AUTOCOMPLETE, timeout=6000)
            except Exception:
                tentativas.append(f"'{consulta}' -> nenhuma opcao")
                continue

            opcoes = self.page.locator(S.OPCOES_AUTOCOMPLETE)
            candidatos = [
                opcoes.nth(indice).inner_text().strip()
                for indice in range(opcoes.count())
            ]
            tentativas.append(f"'{consulta}' -> {len(candidatos)} opcao(oes)")
            if not candidatos:
                continue

            exatos = [
                texto for texto in candidatos
                if sem_acentos(alvo) in sem_acentos(texto)
                or sem_acentos(texto) in sem_acentos(alvo)
            ]
            if len(exatos) == 1:
                escolhido = exatos[0]
            else:
                pontuados = sorted(
                    ((Decimal(str(semelhanca(alvo, texto))), texto) for texto in candidatos),
                    reverse=True,
                )
                nota, escolhido = pontuados[0]
                empatados = [texto for valor, texto in pontuados if valor == nota]
                if nota < minimo or len(empatados) != 1:
                    continue

            opcao = self._achar_opcao(escolhido)
            opcao.click()
            esperar = getattr(self.page, "wait_for_timeout", None)
            if callable(esperar):
                esperar(400)
            confirmado = campo.input_value().strip()
            if semelhanca(alvo, confirmado) < minimo:
                raise ErroNavegador(
                    f"A opcao '{escolhido}' foi clicada no filtro, mas o campo ficou com "
                    f"'{confirmado or '[vazio]' }'. A busca foi interrompida."
                )
            return confirmado

        raise ErroNavegador(
            f"Fornecedor '{nome}' nao foi selecionado na lista suspensa do filtro. "
            f"Tentativas: {'; '.join(tentativas) or 'nenhuma'}."
        )

    def _gerar_relatorio_lista(self) -> bool:
        """Aciona o comando ``Ver relatório`` da lista principal.

        Selecionar o fornecedor no autocomplete apenas preenche o filtro. A
        tabela só é atualizada depois do clique no ícone ``fa-file-text`` —
        comportamento confirmado na tela real. Esta ação apenas consulta a
        lista e não altera qualquer título.
        """
        try:
            icones = self.page.locator(S.ICONE_VER_RELATORIO)
            for indice in range(min(icones.count(), 4)):
                icone = icones.nth(indice)
                if not icone.is_visible():
                    continue
                icone.click()
                if not self._aguardar_lista_pronta():
                    raise ErroNavegador(
                        "A lista permaneceu em 'Processando...' e nao pode ser conferida."
                    )
                # Aumenta o nº de linhas por página: sem isso, buscas por
                # documentos compartilhados (ex.: "09_26" dos salários) só
                # enxergam a primeira página de 10 linhas, e um título além
                # dela não é encontrado (foi o caso da TALITA).
                self._ampliar_pagina_lista()
                return True
        except Exception:
            pass
        return False

    def _ampliar_pagina_lista(self) -> None:
        """Seleciona a maior opção de "linhas por página" do DataTables.

        A lista de Contas a Pagar vem paginada de 10 em 10 por padrão. Ao varrer
        os resultados de uma busca (recuperação de ID, captura de ID no
        salvamento, duplicidade), só as 10 primeiras linhas ficam no DOM, então
        um título na 2ª página some da varredura. Trocar a página para 100 (ou
        "Todos") resolve. Só muda a exibição da lista — não altera título algum.
        """
        try:
            selects = self.page.locator(
                "div.dataTables_length select, select[name$='_length']"
            )
            total = selects.count() if hasattr(selects, "count") else 0
            for indice in range(min(total, 3)):
                sel = selects.nth(indice)
                try:
                    if not sel.is_visible():
                        continue
                except Exception:
                    continue
                opcoes = sel.locator("option")
                melhor_valor = None
                melhor_num = -2
                for j in range(opcoes.count()):
                    val = (opcoes.nth(j).get_attribute("value") or "").strip()
                    try:
                        num = int(val)
                    except ValueError:
                        continue
                    # -1 é a opção "Todos" no DataTables; tratamos como a maior.
                    efetivo = 10 ** 9 if num == -1 else num
                    if efetivo > melhor_num:
                        melhor_num = efetivo
                        melhor_valor = val
                if melhor_valor is not None and melhor_num > 10:
                    sel.select_option(value=melhor_valor)
                    self._aguardar_lista_pronta()
                return
        except Exception:
            pass

    def _aguardar_lista_pronta(self) -> bool:
        """Espera o processamento assíncrono da tabela terminar de verdade.

        O Ongsys pode levar dezenas de segundos para substituir os 24 mil
        registros pela consulta filtrada. Esperas fixas de 1-2 segundos liam
        as linhas antigas enquanto o aviso azul ``Processando...`` ainda
        estava visível. Na dúvida, esta função falha sem aceitar nenhum ID.
        """
        seletor = (
            ".dataTables_processing, [id$='_processing'], "
            ".blockUI.blockMsg, .loading:has-text('Processando')"
        )
        try:
            # Dá tempo para o indicador aparecer depois do clique e verifica
            # novamente após uma pequena janela, cobrindo respostas rápidas e
            # consultas que só iniciam no próximo ciclo do JavaScript.
            self.page.wait_for_timeout(350)
            indicadores = self.page.locator(seletor)
            limite = max(int(self.timeout) * 4, 60000)
            obter_indicador = getattr(indicadores, "nth", None)
            if not callable(obter_indicador):
                # Compatibilidade com páginas simuladas dos testes. Um
                # Locator real do Playwright sempre oferece ``nth``.
                self.page.wait_for_timeout(500)
                return True

            visiveis = []
            for indice in range(min(indicadores.count(), 5)):
                indicador = obter_indicador(indice)
                try:
                    if indicador.is_visible():
                        visiveis.append(indicador)
                except Exception:
                    continue

            if not visiveis:
                self.page.wait_for_timeout(1200)
                for indice in range(min(indicadores.count(), 5)):
                    indicador = obter_indicador(indice)
                    try:
                        if indicador.is_visible():
                            visiveis.append(indicador)
                    except Exception:
                        continue

            for indicador in visiveis:
                indicador.wait_for(state="hidden", timeout=limite)

            self.page.wait_for_timeout(500)
            for indice in range(min(indicadores.count(), 5)):
                indicador = obter_indicador(indice)
                verificar = getattr(indicador, "is_visible", None)
                if not callable(verificar):
                    continue
                try:
                    if verificar():
                        return False
                except Exception:
                    continue
            return True
        except Exception:
            return False

    def _buscar_na_tabela(self, termo: str) -> bool:
        """Filtra imediatamente a tabela pelo campo geral ``Buscar``.

        Diferentemente do filtro de fornecedor, este campo não exige o clique
        em ``Ver relatório``. É a segunda via segura quando o relatório não
        reflete o fornecedor selecionado e também aceita Cód. Lançamento CP.
        """
        try:
            campo = self._campo_busca_tabela()
            if not campo.count():
                return False
            campo.fill(termo)
            return self._aguardar_lista_pronta()
        except Exception:
            return False

    def _campo_busca_tabela(self):
        """Prioriza o ``Buscar`` do DataTable antes de inputs genéricos."""
        for seletor in (
            S.CAMPO_BUSCA_TABELA_DATATABLES,
            S.CAMPO_BUSCA_TABELA_ROTULO,
            S.CAMPO_BUSCA_TABELA_FALLBACK,
        ):
            campo = self.page.locator(seletor).first
            if campo.count():
                return campo
        return self.page.locator(S.CAMPO_BUSCA_TABELA).first

    def _aplicar_periodo_lista(self, inicio, fim) -> bool:
        """Seleciona Vencimento e confirma os meses completos em De/Até.

        Na tela real os campos exibem ``01/08/2026`` e ``31/08/2026``, mas
        são inputs de texto com máscara — não ``input[type=date]``. Por isso
        eles são identificados pelo valor atual, que precisa ser uma data.
        """
        try:
            inicio, fim = periodo_mensal(inicio, fim)
            if not self._selecionar_filtro_vencimento():
                return False

            entradas = self.page.locator(S.FILTROS_DATA_LISTA)
            campos = []
            for indice in range(entradas.count()):
                campo = entradas.nth(indice)
                tipo = (campo.get_attribute("type") or "text").lower()
                valor = campo.input_value().strip()
                if tipo == "date" or para_data(valor) is not None:
                    campos.append(campo)

            if len(campos) < 2:
                return False

            for campo, esperado in zip(campos[:2], (inicio, fim)):
                tipo = (campo.get_attribute("type") or "text").lower()
                valor = data_iso(esperado) if tipo == "date" else data_br(esperado)
                try:
                    campo.fill(valor)
                except Exception:
                    # Alguns datepickers deixam o input readonly. Alterar o
                    # valor e disparar os mesmos eventos só filtra a lista;
                    # não modifica qualquer título do Ongsys.
                    campo.evaluate(
                        """(el, valor) => {
                            el.value = valor;
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                        }""",
                        valor,
                    )
                try:
                    campo.dispatch_event("change")
                except Exception:
                    pass

            campos[1].press("Enter")
            self.page.wait_for_timeout(700)

            # Sem esta validação, uma falha silenciosa deixa o mês anterior
            # (agosto no piloto) ativo e produz um falso "não localizado".
            return (
                para_data(campos[0].input_value()) == inicio
                and para_data(campos[1].input_value()) == fim
            )
        except Exception:
            return False

    def pesquisar_duplicidade(self, titulo: Titulo) -> tuple[str, str]:
        """Procura primeiro o documento exato antes de abrir o formulário.

        A busca antiga começava pelo nome completo do fornecedor. Na tabela
        real isso frequentemente deixava linhas de outros fornecedores na
        tela e transformava praticamente todo título novo em NAO_VERIFICADO,
        exigindo um ``SIM`` individual. O documento é o identificador mais
        objetivo disponível e o campo geral Buscar o consulta sem incluir
        fornecedores inativos.

        Zero resultados de uma consulta de documento concluída é um resultado
        válido (NOVO). Falha técnica, período não confirmado ou campo ausente
        continuam devolvendo NAO_VERIFICADO e nunca viram sinal verde.
        """
        try:
            ids = self.ids_resultado_busca_documento(
                titulo.documento, titulo.vencimento, titulo.vencimento
            )
        except Exception as erro:
            return Duplicidade.NAO_VERIFICADO, f"Falha ao pesquisar: {erro}"

        if not ids:
            # O índice textual do DataTables pode demorar para enxergar um
            # lançamento recém-criado — exatamente o que ocorreu depois dos
            # lançamentos manuais dos BOLETO.2/.3/.7/.8/.14. Antes de declarar
            # NOVO, examina os IDs mais recentes sem depender desse índice e
            # exige a assinatura completa (documento, valor, emissão e
            # vencimento). Isso evita recriar um título manual recente.
            try:
                recentes = self._ids_recentes_sem_indice(titulo, titulo.documento)
            except Exception as erro:
                return (
                    Duplicidade.NAO_VERIFICADO,
                    f"A busca por documento terminou vazia, mas a conferência dos "
                    f"IDs recentes falhou: {erro}",
                )
            if recentes:
                return (
                    Duplicidade.EXATO_JA_EXISTE,
                    "Título equivalente localizado entre os IDs mais recentes: "
                    + ", ".join(recentes[:3]),
                )
            return Duplicidade.NOVO, "Nenhum título com este documento foi localizado."

        textos = [self.texto_linha_para_id(id_linha) for id_linha in ids]
        textos = [texto for texto in textos if texto]
        if not textos:
            return (
                Duplicidade.NAO_VERIFICADO,
                "A busca encontrou IDs, mas não foi possível ler as linhas correspondentes.",
            )

        doc_alvo = normalizar_documento(titulo.documento)
        valor_alvo = formatar_dinheiro_br(titulo.valor)

        exatos, mesmo_documento, outro_fornecedor = [], [], []
        for texto in textos:
            bate_doc = bool(doc_alvo) and doc_alvo in normalizar_documento(texto)
            if not bate_doc:
                continue
            # A trava de duplicidade do Ongsys é POR FORNECEDOR ("o número do
            # documento fiscal já existe para esse fornecedor"). Um mesmo número
            # de documento em OUTRO fornecedor não é conflito. Sem este filtro,
            # "NF 15" de um fornecedor bloqueava o "NF 15" de outro e o documento
            # era incrementado por engano — confirmado em 2026-09-21 (RENATA,
            # ERICO, JOANA, JONAS... viraram NF x.1 sem necessidade).
            if not self._linha_e_do_fornecedor(titulo, texto):
                outro_fornecedor.append(texto.strip()[:200])
                continue
            bate_valor = valor_alvo in texto
            bate_venc = self._texto_contem_data(texto, titulo.vencimento)
            if bate_valor and bate_venc:
                exatos.append(texto.strip()[:200])
            else:
                mesmo_documento.append(texto.strip()[:200])

        if exatos:
            return Duplicidade.EXATO_JA_EXISTE, f"Já existe no Ongsys: {exatos[0]}"
        if mesmo_documento:
            return (
                Duplicidade.DOCUMENTO_REPETIDO_OUTRO_TITULO,
                f"Documento repetido no mesmo fornecedor: {mesmo_documento[0]}",
            )
        if outro_fornecedor:
            # O documento existe, mas em outro fornecedor: para ESTE fornecedor
            # é novo. Lança com o documento original; se por acaso houver um
            # duplicado real do mesmo fornecedor que a busca não pegou, o aviso
            # do próprio formulário ainda trata no momento de salvar.
            return (
                Duplicidade.NOVO,
                f"Documento existe apenas em outro fornecedor (não é conflito): {outro_fornecedor[0]}",
            )
        return (
            Duplicidade.NAO_VERIFICADO,
            "A busca retornou linhas, mas nenhuma pôde ser associada ao documento com segurança.",
        )

    def _linha_e_do_fornecedor(self, titulo: Titulo, texto: str) -> bool:
        texto_n = sem_acentos(texto)
        apelido = sem_acentos(self.resolver_apelido(titulo.fornecedor))
        return (
            semelhanca(titulo.fornecedor, texto) >= Decimal("0.6")
            or sem_acentos(titulo.fornecedor) in texto_n
            or bool(apelido and apelido in texto_n)
        )

    def documentos_ja_usados(self, titulo: Titulo) -> set[str]:
        """Variações do documento já gravadas para o fornecedor no ano.

        O aviso do Ongsys "documento fiscal já existe para esse fornecedor"
        vale para QUALQUER data, mas a pré-verificação só olha o dia do
        vencimento. Em 18/09/2026 IVONE, TATIANA e ORBITA já tinham "09_26" e
        "09_26.1" em outras datas: o robô pré-incrementava para ".1", o aviso
        surgia no meio do formulário e o Salvar travava. Aqui a lista é
        consultada no ano inteiro do vencimento, para escolher um número livre
        ANTES de abrir o formulário. O resultado também alimenta a resolução
        de duplicado dentro do formulário, caso o aviso apareça mesmo assim.
        """
        self._documentos_ocupados = set()
        raiz = (titulo.documento_original or titulo.documento).strip()
        if not raiz:
            return set()
        ano = titulo.vencimento.year
        ids = self.ids_resultado_busca_documento(
            raiz, datetime(ano, 1, 1).date(), datetime(ano, 12, 31).date()
        )
        padrao = re.compile(
            rf"(?<![A-Z0-9]){re.escape(sem_acentos(raiz))}(?:\.(\d+))?(?![A-Z0-9])"
        )
        usados: set[str] = set()
        for id_linha in ids:
            texto = self.texto_linha_para_id(id_linha)
            if not texto or not self._linha_e_do_fornecedor(titulo, texto):
                continue
            for encontrado in padrao.finditer(sem_acentos(texto)):
                sufixo = encontrado.group(1)
                usados.add(f"{raiz}.{sufixo}" if sufixo else raiz)
        self._documentos_ocupados = usados
        return usados

    def proximo_documento(self, titulo: Titulo, ja_tentados: set[str]) -> str:
        """Variação do documento quando ele já existe (§11).

        Todas as contas usam o sufixo ".1", ".2"… — padrão validado pelo usuário,
        inclusive para despesas de cartão de crédito (ex.: "01.07_26" -> "01.07_26.1").
        A raiz é sempre o documento original da planilha. Isso é essencial
        porque ``03.08`` e ``BOLETO.14`` são documentos completos, não a
        variação 8 ou 14 de ``03``/``BOLETO``. Somente sufixos criados nesta
        execução são incrementados por meio de ``ja_tentados``.
        """
        raiz = (titulo.documento_original or titulo.documento).strip()
        if not raiz:
            raise ErroNavegador("Não foi possível variar um documento vazio.")
        numero = 1
        while f"{raiz}.{numero}" in ja_tentados or f"{raiz}.{numero}" == titulo.documento:
            numero += 1
            if numero > 50:
                raise ErroNavegador(
                    f"Não foi possível criar uma variação de documento para '{raiz}' "
                    "depois de 50 tentativas. Verifique manualmente no Ongsys."
                )
        return f"{raiz}.{numero}"

    # -- preenchimento -----------------------------------------------------

    def _conferir_tela_do_formulario(self) -> None:
        faltando = [
            nome for nome, seletor in S.CAMPOS_ESSENCIAIS.items()
            if not self._campo(seletor).count()
        ]
        if faltando:
            raise ErroFatal(
                "A tela do Ongsys mudou: campos essenciais não foram encontrados.\n"
                f"Faltando: {', '.join(faltando)}.\n"
                "A execução parou. Ajuste ongsys/seletores.py antes de continuar."
            )

    def _abrir_formulario_inclusao(self) -> None:
        """Abre o submenu de inclusão e confirma que o formulário apareceu.

        O botão ``Incluir`` é um dropdown. Em uso real, o clique às vezes não
        deixa o submenu aberto, embora não levante erro. Clicar imediatamente
        no item oculto gerava um diagnóstico longo e pouco claro. Aqui cada
        tentativa confirma primeiro a visibilidade do submenu e, depois, a do
        campo Fornecedor do formulário.
        """
        detalhes: list[str] = []
        for tentativa in range(1, 4):
            try:
                self._clicar(S.BOTAO_INCLUIR, "abrir menu de inclusão")
                menu = self.page.locator(S.MENU_INCLUIR_CONTAS_PAGAR).first
                menu.wait_for(state="visible", timeout=1800)
                menu.click(timeout=4000)
                self.page.locator(S.CAMPO_FORNECEDOR + ":visible").first.wait_for(
                    state="visible", timeout=5000
                )
                return
            except Exception as erro:
                # Só repete falhas recuperáveis de clique/espera. Um erro de
                # programação inesperado deve subir com o traceback original,
                # em vez de ser mascarado como simples dropdown fechado.
                if not isinstance(erro, ErroNavegador) and type(erro).__name__ not in {
                    "TimeoutError", "Error"
                }:
                    raise
                detalhes.append(f"tentativa {tentativa}: {type(erro).__name__}")
                # Ainda não há formulário aberto neste ponto. Limpar apenas
                # avisos/modais órfãos é seguro e evita que uma sobra da
                # tentativa anterior intercepte o próximo clique.
                self._purgar_modais_orfaos()
                self.page.wait_for_timeout(300)

        raise ErroNavegador(
            "O formulário de inclusão não abriu: o submenu 'Contas a Pagar' "
            "não permaneceu visível após três tentativas. O título NÃO foi "
            f"preenchido nem salvo. ({'; '.join(detalhes)})"
        )

    def _preencher_rateio(self, indice: int, rateio: Rateio) -> list[str]:
        """Preenche uma linha inteira. A próxima só é criada depois desta (§12)."""
        pendencias: list[str] = []

        categoria = self._selecionar_por_nome(
            S.categoria_conta(indice), rateio.categoria_detalhada, f"Linha {indice}: Categoria/Conta"
        )
        self._anotar_escolha_por_semelhanca(
            pendencias, indice, "Categoria/Conta", rateio.categoria_detalhada, categoria
        )
        # A seleção da categoria dispara um onchange assíncrono que prepara
        # Projeto/CC e Subprojeto. Na execução do BENEFIX, o campo de CC chegou a
        # aceitar a opção e foi limpo logo depois pelo término desse onchange;
        # o erro só apareceu ao pedir a linha seguinte. Aguarda a dependência
        # estabilizar antes de selecionar o CC.
        if self.page is not None:
            self.page.wait_for_timeout(400)
        projeto = self._selecionar_por_nome(
            S.projeto_cc(indice),
            rateio.centro_custo,
            f"Linha {indice}: Projeto/Centro de Custos",
            aceitar_prefixo=True,
        )
        self._anotar_escolha_por_semelhanca(
            pendencias, indice, "Projeto/Centro de Custos", rateio.centro_custo,
            projeto, aceitar_prefixo=True,
        )

        if rateio.subprojeto:
            try:
                _, pendencia = self._selecionar_subprojeto(indice, rateio.subprojeto)
            except ErroFatal:
                raise
            except Exception as erro:
                # Regra do usuário (§13, reforçada em 11/09/2026): problema
                # com o subprojeto NUNCA interrompe a linha nem o título. O
                # campo fica vazio (ou apontado para correção) e vira pendência.
                self._limpar_digitacao(S.subprojeto(indice))
                pendencia = self._subprojeto_preso_com_outro_codigo(
                    indice, rateio.subprojeto
                ) or (
                    f"Linha {indice}: Subprojeto {rateio.subprojeto} não pôde ser "
                    f"selecionado ({type(erro).__name__}). Campo deixado vazio; "
                    "informe manualmente no Ongsys."
                )
            if pendencia:
                pendencias.append(pendencia)

        if rateio.atividade:
            atividade_buscar = self._resolver_apelido_atividade(rateio.atividade)
            try:
                # Menos tentativas que os campos obrigatórios: a atividade é
                # opcional (§14) e, quando o campo está travado, cada tentativa
                # custa vários timeouts de 15s. Insistir só fazia a execução
                # parecer travada — foi o que levou o usuário a fechar o
                # navegador na linha 2 do rateio do LEANDRO ("Despesas ADM").
                self._selecionar_por_nome(
                    S.atividade(indice), atividade_buscar,
                    f"Linha {indice}: Atividade", tentativas=2,
                )
            except ErroFatal:
                raise
            except Exception as erro:
                # §14: atividade ausente NUNCA trava o título. Antes só
                # ErroNavegador era tratado; com o campo desabilitado o
                # Playwright levanta TimeoutError em fill(), que escapava e
                # derrubava o título inteiro. Limpar o campo também pode
                # falhar pelo mesmo motivo, então vai protegido.
                try:
                    self._preencher(S.atividade(indice), "")
                except Exception:
                    pass
                pendencias.append(
                    f"Linha {indice}: atividade '{rateio.atividade}' nao pôde ser "
                    f"preenchida ({type(erro).__name__}). Campo deixado vazio; "
                    "informe manualmente no Ongsys se necessario."
                )

        # Mesmo cuidado do valor do título: a máscara de moeda pode distorcer
        # o número, então o preenchimento é conferido lendo o campo de volta.
        seletor_valor = S.valor_rateio(indice)
        if para_decimal(self._ler_valor(seletor_valor)) != rateio.valor:
            self._habilitar_campo_de_rateio(
                seletor_valor, f"Linha {indice}: valor do rateio"
            )
            self._preencher_dinheiro(
                seletor_valor, rateio.valor, f"Linha {indice}: valor do rateio"
            )
        self._preencher_ou_validar_percentual_rateio(indice, rateio.percentual)

        valor_na_tela = para_decimal(self._ler_valor(S.valor_rateio(indice)))
        if valor_na_tela is not None and valor_na_tela != rateio.valor:
            raise ErroNavegador(
                f"Linha {indice}: o valor gravado na tela ({valor_na_tela}) não é o "
                f"esperado ({rateio.valor}). O título não foi salvo."
            )

        # O Ongsys pode limpar um campo dependente alguns instantes depois de
        # a opção ter sido clicada. Releia os dois campos obrigatórios no fim
        # da linha e faça uma única recuperação objetiva antes de prosseguir.
        # Isso evita tanto o falso sucesso visto no BENEFIX quanto dezenas de
        # variações de busca sem utilidade.
        self._garantir_campos_obrigatorios_rateio(indice, rateio)
        return pendencias

    def _anotar_escolha_por_semelhanca(
        self,
        pendencias: list[str],
        indice: int,
        campo: str,
        esperado: str,
        escolhido: str,
        *,
        aceitar_prefixo: bool = False,
    ) -> None:
        """Opção sem nome idêntico ao da planilha segue, mas fica apontada."""
        if not esperado or not escolhido:
            return
        if self._nome_selecionado_confere(
            escolhido, esperado, aceitar_prefixo=aceitar_prefixo
        ):
            return
        pendencias.append(
            f"Linha {indice}: {campo} '{esperado}' não tem nome idêntico no "
            f"Ongsys; usada a opção '{escolhido}'. Confira e corrija "
            "manualmente se não for a correta."
        )

    def _garantir_campos_obrigatorios_rateio(
        self, indice: int, rateio: Rateio
    ) -> None:
        """Confirma Categoria e Projeto/CC após os onchanges do rateio.

        A recuperação é limitada a uma nova seleção por campo. Se o Ongsys
        tornar a limpar o valor, a execução para antes de criar a próxima
        linha ou salvar o título.
        """
        if self.page is not None:
            self.page.wait_for_timeout(350)

        categoria = self._ler_valor(S.categoria_conta(indice))
        if not self._selecao_confere(
            S.categoria_conta(indice), categoria, rateio.categoria_detalhada
        ):
            self._selecionar_por_nome(
                S.categoria_conta(indice),
                rateio.categoria_detalhada,
                f"Linha {indice}: Categoria/Conta",
                tentativas=1,
            )
            if self.page is not None:
                self.page.wait_for_timeout(400)

        projeto = self._ler_valor(S.projeto_cc(indice))
        if rateio.centro_custo and not self._selecao_confere(
            S.projeto_cc(indice), projeto, rateio.centro_custo, aceitar_prefixo=True
        ):
            self._selecionar_por_nome(
                S.projeto_cc(indice),
                rateio.centro_custo,
                f"Linha {indice}: Projeto/Centro de Custos",
                tentativas=1,
                aceitar_prefixo=True,
            )
            if self.page is not None:
                self.page.wait_for_timeout(350)

        categoria = self._ler_valor(S.categoria_conta(indice))
        projeto = self._ler_valor(S.projeto_cc(indice))
        faltando: list[str] = []
        if not self._selecao_confere(
            S.categoria_conta(indice), categoria, rateio.categoria_detalhada
        ):
            faltando.append(
                f"Categoria/Conta='{categoria or '[vazio]'}' "
                f"(esperado '{rateio.categoria_detalhada}')"
            )
        if rateio.centro_custo and not self._selecao_confere(
            S.projeto_cc(indice), projeto, rateio.centro_custo, aceitar_prefixo=True
        ):
            faltando.append(
                f"Projeto/CC='{projeto or '[vazio]'}' "
                f"(esperado '{rateio.centro_custo}')"
            )
        if faltando:
            raise ErroNavegador(
                f"Linha {indice} não ficou pronta para continuar: "
                + "; ".join(faltando)
                + ". O título NÃO foi salvo."
            )

    def _preencher_ou_validar_percentual_rateio(
        self, indice: int, percentual_esperado: Decimal
    ) -> None:
        """Preenche o percentual ou valida o valor calculado pelo Ongsys.

        No piloto real com uma única linha, ao sair do campo Valor o Ongsys
        calculou 100,00% automaticamente e desabilitou o campo Percentual.
        Tentar ``fill("")`` nesse input desabilitado fez o Playwright esperar
        15 segundos e abortar antes de Salvar. Campo automático não deve ser
        forçado: ele é aceito somente quando o valor exibido confere.

        A releitura também cobre uma transição assíncrona em que o campo ainda
        parece editável na primeira consulta, mas é desabilitado no instante
        em que a automação tenta preenchê-lo.
        """
        seletor = S.percentual_rateio(indice)
        S.conferir_permitido(seletor)
        esperado = para_percentual(percentual_esperado)
        campo = self._campo(seletor).first

        def esta_editavel(elemento) -> bool:
            try:
                return elemento.is_enabled() and elemento.is_editable()
            except Exception:
                try:
                    return elemento.is_enabled()
                except Exception:
                    # Mantém compatibilidade defensiva com elementos que não
                    # expõem esses métodos; a tentativa de fill dará o erro
                    # real e será tratada abaixo.
                    return True

        encontrado_inicial = para_percentual(self._ler_valor(seletor))
        if (
            esperado is not None
            and encontrado_inicial is not None
            and abs(encontrado_inicial - esperado) <= Decimal("0.01")
        ):
            return

        if not esta_editavel(campo):
            self._habilitar_campo_de_rateio(
                seletor, f"Linha {indice}: percentual do rateio"
            )
            campo = self._campo(seletor).first

        if esta_editavel(campo):
            percentual_texto = formatar_percentual_br(percentual_esperado)
            try:
                self._preencher(seletor, percentual_texto)
            except Exception as erro:
                campo = self._campo(seletor).first
                if esta_editavel(campo):
                    raise ErroNavegador(
                        f"Linha {indice}: não foi possível preencher o percentual "
                        f"{percentual_texto}%. O título NÃO foi salvo. Detalhe: {erro}"
                    ) from erro

        encontrado_texto = self._ler_valor(seletor)
        encontrado = para_percentual(encontrado_texto)
        # O campo do Ongsys tem data-precision="2". Em uso real ele exibiu
        # tanto arredondamento normal (52,5388% -> 52,54%) quanto o caso de
        # fronteira 0,4350% -> 0,43%, provavelmente por representação interna
        # ligeiramente abaixo de 0,435. A auditoria já aceita diferença de até
        # 0,01 ponto percentual; a validação antes de salvar usa o mesmo limite.
        # Isso cobre somente a precisão visual de duas casas. Diferenças maiores
        # continuam bloqueando o título, e cada valor monetário ainda é conferido
        # exatamente antes do salvamento.
        if esperado is not None and encontrado is not None:
            if abs(encontrado - esperado) <= Decimal("0.01"):
                return
        if esperado is None or encontrado != esperado:
            esperado_texto = formatar_percentual_br(percentual_esperado)
            raise ErroNavegador(
                f"Linha {indice}: o percentual exibido na tela é "
                f"'{encontrado_texto or '[ilegível]'}', mas o esperado é "
                f"{esperado_texto}%. O título NÃO foi salvo."
            )

    def _preencher_documento_trocando_de_aba_se_preciso(self, novo: str) -> None:
        """Preenche o Número do Documento, voltando para "Dados do pagamento"

        primeiro se for preciso. Esse campo vive nessa aba; se o aviso de
        documento duplicado aparecer só durante o preenchimento do rateio
        (confirmado em uso real), a aba ativa já é "Rateio e Parcelamento" e
        o campo fica inacessível até voltar para a aba certa. Volta para a
        aba de rateio depois, se foi de lá que veio — sem usar `_clicar`
        (que re-entraria na resolução de duplicidade) porque já estamos
        dentro dela.
        """
        try:
            self._preencher(S.CAMPO_NUMERO_DOCUMENTO, novo)
            self._campo(S.CAMPO_NUMERO_DOCUMENTO).first.press("Tab")
            return
        except Exception:
            pass

        estava_no_rateio = False
        try:
            estava_no_rateio = self._campo(S.categoria_conta(1)).first.is_visible()
        except Exception:
            pass

        self._campo(S.ABA_DADOS_PAGAMENTO).first.click()
        self.page.wait_for_timeout(300)
        self._preencher(S.CAMPO_NUMERO_DOCUMENTO, novo)
        self._campo(S.CAMPO_NUMERO_DOCUMENTO).first.press("Tab")

        if estava_no_rateio:
            self._campo(S.ABA_RATEIO_PARCELAMENTO).first.click()
            self.page.wait_for_timeout(300)

    def _resolver_documento_duplicado(
        self, titulo: Titulo, resultado: ResultadoInclusao, documentos_tentados: set[str]
    ) -> bool:
        """Se a tela indicar que o documento já existe, aplica uma variação (§11).

        Chamado em dois pontos: logo após preencher o Número do Documento
        (a validação pode ser assíncrona e aparecer sozinha, sem precisar de
        Salvar) e depois de tentar salvar (caso o aviso só apareça no envio).
        Devolve True quando tratou uma duplicidade — o chamador deve conferir
        de novo antes de seguir — e False quando não havia nada a tratar.
        """
        mensagem = self._mensagens_da_tela() or self._texto_modal_erro()
        if not self._documento_duplicado(mensagem):
            return False

        novo = self.proximo_documento(titulo, documentos_tentados)
        documentos_tentados.add(novo)
        resultado.avisos.append(
            f"Documento '{resultado.documento_final}' já existia; alterado para '{novo}'."
        )
        # Confirmado pelo usuário: o popup NÃO fecha sozinho ao trocar o
        # documento, mesmo que o novo valor seja aceito — ele fica ali,
        # aguardando para ser fechado, e não atrapalha o preenchimento do
        # resto do formulário nesse meio-tempo. Por isso o documento é
        # preenchido primeiro (garantido não bloqueado) e o fechamento vem
        # depois, com confirmação de que realmente sumiu — não só uma
        # tentativa às cegas.
        self._preencher_documento_trocando_de_aba_se_preciso(novo)
        resultado.documento_final = novo
        # §11: o Ongsys costuma limpar o fornecedor nesse aviso.
        if not self._ler_valor(S.CAMPO_FORNECEDOR):
            resultado.fornecedor_selecionado = self.selecionar_fornecedor_titulo(titulo)
        if not self._fechar_modal_erro_confirmado():
            resultado.avisos.append(
                "Aviso de documento duplicado ficou na tela mesmo depois de tentar fechar."
            )
        return True

    def _purgar_modais_orfaos(self) -> None:
        """Remove todo modal/backdrop órfão — só quando estamos na LISTA.

        Chamado no começo de ``incluir()``, antes de abrir o formulário: nesse
        instante nenhum modal de inclusão deveria existir, então qualquer
        ``.modal`` visível ou ``.modal-backdrop`` é resto de uma operação
        anterior. O ``#modalErro`` (aviso de documento duplicado) e seu backdrop
        às vezes sobrevivem à navegação e ficam por cima da tela, bloqueando o
        clique em "Incluir" (falha vista em NC AVENIDA e NUVEMCO CLOUD). Como não
        há formulário aberto aqui, é seguro remover tudo, inclusive o backdrop
        (que é o que de fato intercepta o clique).
        """
        try:
            self.page.evaluate("""
                (function() {
                    var backdrops = document.querySelectorAll('.modal-backdrop');
                    for (var i = 0; i < backdrops.length; i++) backdrops[i].remove();
                    var modais = document.querySelectorAll('.modal');
                    for (var j = 0; j < modais.length; j++) {
                        modais[j].classList.remove('in');
                        modais[j].style.display = 'none';
                        modais[j].setAttribute('aria-hidden', 'true');
                    }
                    document.body.classList.remove('modal-open');
                    document.body.style.overflow = '';
                    document.body.style.paddingRight = '';
                })()
            """)
            self.page.wait_for_timeout(150)
        except Exception:
            pass

    def _fechar_modal_erro_confirmado(self) -> bool:
        """Fecha #modalErro e CONFIRMA que ele sumiu — não só tenta uma vez.

        IMPORTANTE: usa JavaScript direto para esconder SOMENTE o #modalErro,
        sem passar pelo data-dismiss="modal" do Bootstrap 3, que em modais
        aninhados pode fechar também o modal-pai (o formulário de inclusão).
        O JS remove a classe "in", esconde o modal e remove o backdrop extra,
        sem disparar eventos que afetem outros modais abertos.
        """
        js_fechar = """
        (function() {
            var m = document.getElementById('modalErro');
            if (!m) return true;
            m.classList.remove('in');
            m.style.display = 'none';
            m.setAttribute('aria-hidden', 'true');
            // Remove apenas backdrops extras (mantém o do formulário)
            var backdrops = document.querySelectorAll('.modal-backdrop');
            if (backdrops.length > 1) {
                backdrops[backdrops.length - 1].remove();
            }
            // Garante que o body mantém a classe 'modal-open' para o
            // formulário de inclusão que ainda está ativo
            document.body.classList.add('modal-open');
            return true;
        })()
        """
        for _ in range(6):
            try:
                modal = self.page.locator("#modalErro")
                if modal.count() == 0:
                    return True
                if not modal.first.is_visible():
                    return True
                # Abordagem JS direta: esconde só o #modalErro sem efeito
                # cascata no Bootstrap
                try:
                    self.page.evaluate(js_fechar)
                    self.page.wait_for_timeout(300)
                    if self.page.locator("#modalErro:visible").count() == 0:
                        return True
                except Exception:
                    pass
                # Fallback: tenta clicar o botão do próprio #modalErro
                for seletor_botao in ("[data-dismiss='modal']", S.POPUP_FECHAR):
                    botao = modal.first.locator(seletor_botao)
                    if not botao.count():
                        continue
                    for indice in range(min(botao.count(), 3)):
                        candidato = botao.nth(indice)
                        try:
                            if candidato.is_visible():
                                candidato.click(timeout=2000)
                        except Exception:
                            try:
                                candidato.evaluate("el => el.click()")
                            except Exception:
                                continue
                    self.page.wait_for_timeout(300)
                    if self.page.locator("#modalErro").count() == 0:
                        return True
            except Exception:
                pass
            esperar = getattr(self.page, "wait_for_timeout", None)
            if callable(esperar):
                esperar(400)
        try:
            return self.page.locator("#modalErro").count() == 0
        except Exception:
            return False

    # -- projetados --------------------------------------------------------

    def buscar_projetado(self, titulo: Titulo) -> object | None:
        """Procura um lançamento projetado inequívoco para o fornecedor.

        Retorna o elemento ``<tr>`` da linha correspondente, ou ``None``.
        A busca usa o campo "Buscar" da tabela (DataTables) em vez do filtro
        de fornecedor com lupa, porque este último exibe também fornecedores
        inativos e pode causar confusão. O texto "Projetado" deve aparecer na
        coluna Dcto da linha. Um único projetado do fornecedor é aceito mesmo
        com valor estimado divergente; havendo vários, o valor exato é usado
        como desempate e qualquer ambiguidade interrompe o título.
        """
        self.ultimo_aviso_projetado = ""
        try:
            self.page.goto(self.config["ongsys_url"], wait_until="domcontentloaded")
            # Regra definida pelo usuário: o projetado candidato é o do MÊS DO
            # PAGAMENTO da despesa (uma despesa de setembro só disputa
            # projetados de setembro). Antes a busca cobria o ano inteiro, o que
            # juntava projetados de vários meses do mesmo fornecedor e criava
            # ambiguidade — vários candidatos idênticos adiavam o título.
            # Consequência aceita: um projetado cuja data estimada caiu em outro
            # mês não é convertido; o título é lançado normalmente e aquele
            # projetado permanece para tratamento manual.
            if not self._aplicar_periodo_lista(titulo.efetivacao, titulo.efetivacao):
                raise ErroNavegador(
                    "nao foi possivel aplicar e confirmar o periodo da lista"
                )
            if not self._gerar_relatorio_lista():
                raise ErroNavegador("o comando Ver relatorio nao foi localizado")
            self.page.wait_for_timeout(500)

            # Usa a barra "Buscar" da tabela para filtrar pelo fornecedor
            nome_busca = self.resolver_apelido(titulo.fornecedor)
            if not self._buscar_na_tabela(nome_busca):
                raise ErroNavegador("a barra Buscar nao confirmou o processamento")

            valor_alvo = formatar_dinheiro_br(titulo.valor)
            fornecedores_alvo = [
                sem_acentos(nome)
                for nome in self.fornecedores_esperados_na_tela(titulo)
                if nome
            ]
            padrao_valor = re.compile(
                rf"(?<![\d.]){re.escape(valor_alvo)}(?!\d)"
            )

            projetados_fornecedor = []
            projetados_valor_exato = []
            for linha in self._linhas_visiveis_lista():
                texto = linha.inner_text()
                texto_n = sem_acentos(texto)
                # Deve ter "Projetado" na coluna Dcto
                if "PROJETADO" not in texto_n.upper():
                    continue
                # Deve ser do mesmo fornecedor
                bate_forn = any(
                    fornecedor in texto_n
                    or semelhanca(fornecedor, texto_n) >= Decimal("0.6")
                    for fornecedor in fornecedores_alvo
                )
                # Deve ter o mesmo valor
                bate_valor = bool(padrao_valor.search(texto))
                if not bate_forn:
                    continue
                projetados_fornecedor.append(linha)
                if bate_valor:
                    projetados_valor_exato.append(linha)

            # Um único projetado do fornecedor é inequívoco mesmo se o valor
            # estimado estiver errado: os dados reais da planilha o substituirão.
            if len(projetados_fornecedor) == 1:
                if not projetados_valor_exato:
                    self.ultimo_aviso_projetado = (
                        "Projetado unico do fornecedor localizado com valor diferente; "
                        "o valor sera substituido pelo valor da planilha."
                    )
                return projetados_fornecedor[0]

            # Com vários projetados, o valor é o desempate autorizado pelo
            # usuário. Só uma correspondência exata permite escolher.
            if len(projetados_valor_exato) == 1:
                return projetados_valor_exato[0]
            if len(projetados_valor_exato) > 1:
                raise ErroNavegador(
                    f"foram encontrados {len(projetados_valor_exato)} projetados com o "
                    "mesmo fornecedor e valor; selecao manual necessaria"
                )
            if len(projetados_fornecedor) > 1:
                return self._projetado_de_valor_mais_proximo(
                    titulo, projetados_fornecedor
                )
            return None
        except ErroNavegador:
            raise
        except Exception as erro:
            raise ErroNavegador(f"falha tecnica ao procurar projetado: {erro}") from erro

    def _projetado_de_valor_mais_proximo(self, titulo: Titulo, linhas: list) -> object:
        """Desempate entre vários projetados quando nenhum tem o valor exato.

        Decisão do usuário (18/09/2026, ASSINAFACIL NF 1520037 e 1520039): os
        valores dos projetados ficam desatualizados; vale o valor da planilha.
        Escolhe o projetado de valor mais próximo, desde que seja o ÚNICO mais
        próximo e que todos os valores da lista tenham sido lidos com
        segurança. Empate ou valor ilegível continuam adiando o título. Como
        cada conversão transforma o projetado em real, o título seguinte do
        mesmo fornecedor só disputa os projetados que sobraram.
        """
        candidatos: list[tuple[Decimal, Decimal, object]] = []
        for linha in linhas:
            valor = valor_unico_em_linha(linha.inner_text())
            if valor is None:
                raise ErroNavegador(
                    f"foram encontrados {len(linhas)} projetados do fornecedor e o "
                    "valor de um deles nao pode ser lido com seguranca; "
                    "selecao manual necessaria"
                )
            candidatos.append((abs(valor - titulo.valor), valor, linha))
        candidatos.sort(key=lambda c: c[0])
        if len(candidatos) > 1 and candidatos[0][0] == candidatos[1][0]:
            raise ErroNavegador(
                f"foram encontrados {len(linhas)} projetados do fornecedor, nenhum "
                "com o valor exato, e dois igualmente proximos do valor da "
                "planilha; selecao manual necessaria"
            )
        _, valor, linha = candidatos[0]
        outros = ", ".join(f"R$ {formatar_dinheiro_br(v)}" for _, v, _ in candidatos[1:])
        self.ultimo_aviso_projetado = (
            f"{len(linhas)} projetados do fornecedor sem valor exato; escolhido o de "
            f"valor mais proximo (R$ {formatar_dinheiro_br(valor)}; outros: {outros}). "
            f"O valor sera substituido pelo da planilha (R$ {titulo.valor_br})."
        )
        return linha

    def _habilitar_campos_formulario(self) -> None:
        """Habilita somente campos conhecidos do título dentro do modal ativo.

        O fluxo antigo removia ``disabled`` e ``hidden`` de todos os elementos
        da página. Aqui o escopo é o formulário já identificado e a lista é
        restrita aos campos que a automação efetivamente confere e preenche.
        """
        if self._modal_inclusao is None:
            raise ErroNavegador("Formulario de edicao nao foi identificado com seguranca.")
        # Os seletores vêm das constantes de `seletores.py`, nunca digitados à
        # mão aqui. A lista antiga tinha '#field_historico', '#field_valor' e
        # '#field_competencia', que NÃO existem na tela — os IDs reais são
        # '#field_descricao_despesa', '#field_valor_despesa' e
        # '#field_inicio_competencia'. Com isso, Histórico, Valor e Competência
        # nunca eram habilitados na conversão de projetado, e o Ongsys recusava
        # o salvamento com "é obrigatório preencher ou selecionar um histórico".
        campos = [
            S.CAMPO_HISTORICO,
            S.CAMPO_TIPO_DESPESA,
            S.CAMPO_DATA_EMISSAO,
            S.CAMPO_DATA_VENCIMENTO,
            S.CAMPO_DATA_EFETIVACAO,
            S.CAMPO_VALOR,
            S.CAMPO_COMPETENCIA,
            S.CAMPO_TIPO_DOCUMENTO,
            S.CAMPO_NUMERO_DOCUMENTO,
            S.CAMPO_CODIGO_BARRAS,
            S.CAMPO_MEIO_PAGAMENTO,
            S.CAMPO_CONTA_PAGADORA,
        ]
        self._modal_inclusao.evaluate(
            """
            (root, seletores) => {
                root.querySelectorAll(seletores.join(',')).forEach((el) => {
                    el.removeAttribute('disabled');
                    el.removeAttribute('readonly');
                });
            }
            """,
            campos,
        )

    def _sincronizar_linhas_rateio(self, titulo: Titulo, resultado: ResultadoInclusao) -> None:
        """Ajusta as linhas de rateio do projetado às linhas da planilha.

        Reutiliza as linhas que o projetado já tem, cria as que faltam e nunca
        remove sobras — um projetado com mais linhas que a planilha bloqueia a
        edição e exige ajuste manual.

        Cada linha nova é criada IMEDIATAMENTE ANTES de ser preenchida, igual ao
        fluxo já comprovado em ``incluir()``. O fluxo anterior criava todas as
        linhas de uma vez e só depois preenchia: como o Ongsys clona a linha
        anterior ao criar a próxima, clonar linhas ainda vazias deixava a
        seguinte inutilizável (LEANDRO NF 48 expirou em ``#field_valor_rateio_2``).
        """
        linhas_existentes = 0
        for indice in range(1, 201):
            if not self._campo(S.categoria_conta(indice)).count():
                break
            linhas_existentes = indice

        if linhas_existentes > len(titulo.rateios):
            raise ErroNavegador(
                f"O projetado possui {linhas_existentes} linhas de rateio, mas a planilha "
                f"possui {len(titulo.rateios)}. Nada foi removido; ajuste manual necessario."
            )

        for indice, rateio in enumerate(titulo.rateios, start=1):
            if indice > linhas_existentes:
                resultado.pendencias.extend(
                    self._incluir_linha_rateio(
                        indice - 1, titulo.rateios[indice - 2]
                    )
                )
                linhas_existentes = indice
            self._habilitar_campos_formulario()
            resultado.pendencias.extend(self._preencher_rateio(indice, rateio))

    def _garantir_historico_preenchido(self, titulo: Titulo) -> None:
        """Relê o Histórico e repreenche se a tela o devolveu vazio.

        Na conversão de projetado o campo pode voltar em branco (por estar
        desabilitado ou por um onchange do próprio Ongsys). Como ele é
        obrigatório, o salvamento seria recusado. Aqui o valor é conferido e,
        se preciso, reescrito; se ainda assim não gravar, o título falha antes
        de clicar em Salvar, sem deixar o lançamento em estado incerto.
        """
        esperado = (titulo.historico or "").strip()
        if not esperado:
            raise ErroNavegador(
                "A planilha nao forneceu historico para a despesa e o Ongsys "
                "exige esse campo. O projetado nao foi alterado."
            )
        for _ in range(3):
            if self._ler_valor(S.CAMPO_HISTORICO).strip():
                return
            try:
                self._preencher(S.CAMPO_HISTORICO, esperado)
            except Exception:
                self.page.wait_for_timeout(300)
        if not self._ler_valor(S.CAMPO_HISTORICO).strip():
            raise ErroNavegador(
                f"O campo Historico continuou vazio depois de tentar preencher "
                f"'{esperado}'. O Ongsys exige esse campo, entao o projetado NAO "
                "foi salvo. Confira o lancamento manualmente."
            )

    def editar_projetado(self, titulo: Titulo, linha_projetado) -> ResultadoInclusao:
        """Abre um lançamento projetado, sobrescreve com os dados da planilha e salva.

        O formulário de edição é o mesmo usado na inclusão: mesmos campos,
        mesmos IDs. A diferença é que os campos já vêm preenchidos com os
        dados do projetado, e o bot sobrescreve com os dados da planilha.
        Campos desabilitados são habilitados via JavaScript.
        """
        if titulo.bloqueado:
            raise ErroNavegador(
                "Título bloqueado no planejamento: " + " | ".join(titulo.bloqueios)
            )

        texto_linha = linha_projetado.inner_text()
        id_projetado, codigo_projetado = self._identificadores_da_linha(texto_linha)
        resultado = ResultadoInclusao(
            id_ongsys=id_projetado,
            documento_final=titulo.documento,
        )
        if id_projetado and codigo_projetado:
            self._codigos_lancamento_por_id[
                self._normalizar_id(id_projetado)
            ] = codigo_projetado
        self.salvar_foi_clicado = False
        self._modal_inclusao = None
        self._texto_digitado = {}
        self._opcoes_aceitas = {}
        self._duplicado_titulo = titulo
        self._duplicado_resultado = resultado
        self._duplicado_tentados = {titulo.documento}

        # Abre o projetado para edição (lápis)
        if not self.abrir_linha_para_auditoria(linha_projetado):
            raise ErroNavegador(
                "Não foi possível abrir o projetado para edição (lápis não encontrado)."
            )

        self._modal_inclusao = self._detectar_modal_inclusao()
        self.page.wait_for_timeout(300)

        # Confirma novamente o fornecedor dentro do formulário antes de tocar
        # no projetado. A linha da tabela é apenas a primeira barreira.
        for _ in range(15):
            fornecedor_lido = self._ler_valor(S.CAMPO_FORNECEDOR)
            if fornecedor_lido:
                break
            self.page.wait_for_timeout(200)
        else:
            raise ErroNavegador("O fornecedor do projetado nao carregou no formulario.")

        fornecedores_validos = self.fornecedores_esperados_na_tela(titulo)
        if not any(
            sem_acentos(esperado) in sem_acentos(fornecedor_lido)
            or semelhanca(esperado, fornecedor_lido) >= Decimal("0.6")
            for esperado in fornecedores_validos
        ):
            raise ErroNavegador(
                f"O projetado aberto pertence a '{fornecedor_lido}', nao a "
                f"'{titulo.fornecedor}'. Nada foi alterado."
            )
        resultado.fornecedor_selecionado = fornecedor_lido

        # Muda para Real ANTES de preencher. O onchange setaProjetado() pode
        # limpar ou reconfigurar campos; preenchê-los antes perderia dados.
        self._selecionar_lista(
            S.CAMPO_TIPO_LANCAMENTO, "Real", "Tipo de lançamento"
        )
        self.page.wait_for_timeout(500)
        self._modal_inclusao = self._detectar_modal_formulario()
        if self._modal_inclusao is None:
            raise ErroNavegador(
                "O formulario desapareceu ao mudar o projetado para Real. "
                "A alteracao nao foi confirmada; audite antes de tentar novamente."
            )
        self._habilitar_campos_formulario()

        # Sobrescreve os campos com os dados da planilha
        # O fornecedor do projetado já deve estar correto; não reseleciona
        # para evitar problemas com autocomplete em modo edição.
        self._preencher(S.CAMPO_HISTORICO, titulo.historico)
        self._selecionar_lista(S.CAMPO_TIPO_DESPESA, titulo.tipo_despesa, "Tipo de despesa")
        self._fechar_popup_de_servico_nao_parametrizado(titulo.tipo_despesa)
        self._preencher(S.CAMPO_DATA_EMISSAO, data_iso(titulo.emissao))
        self._preencher(S.CAMPO_DATA_VENCIMENTO, data_iso(titulo.vencimento))
        self._preencher(S.CAMPO_DATA_EFETIVACAO, data_iso(titulo.efetivacao))
        self._preencher_dinheiro(S.CAMPO_VALOR, titulo.valor, "Valor bruto da despesa")
        self._preencher(S.CAMPO_COMPETENCIA, competencia_iso(titulo.efetivacao))

        self._selecionar_lista(S.CAMPO_TIPO_DOCUMENTO, titulo.tipo_documento, "Tipo de documento")
        self._preencher(S.CAMPO_NUMERO_DOCUMENTO, titulo.documento)
        self._campo(S.CAMPO_NUMERO_DOCUMENTO).first.press("Tab")

        # Código de barras
        if titulo.codigo_barras:
            barras_limpo = titulo.codigo_barras.replace(" ", "").replace(".", "")
            self._preencher(S.CAMPO_CODIGO_BARRAS, barras_limpo)

        # Espera para resolução de duplicidade assíncrona
        for rodada_dup in range(15):
            self.page.wait_for_timeout(800 if rodada_dup < 2 else 500)
            if not self._resolver_duplicado_pendente():
                break

        self._selecionar_lista(S.CAMPO_MEIO_PAGAMENTO, titulo.meio_pagamento, "Meio de pagamento")
        self._selecionar_conta(titulo)
        self._resolver_duplicado_pendente()

        # Aba de rateio: reutiliza as linhas existentes do projetado. Nunca
        # cria linhas novas sem antes contar as atuais e nunca remove extras.
        self._abrir_aba_rateio()
        self._habilitar_campos_formulario()

        self._sincronizar_linhas_rateio(titulo, resultado)

        self._conferir_totais_na_tela(titulo)

        # Volta para a aba de dados antes de salvar.
        try:
            self._clicar(S.ABA_DADOS_PAGAMENTO, "voltar para aba Dados do pagamento")
            self.page.wait_for_timeout(500)
        except Exception as erro:
            raise ErroNavegador(
                f"Nao foi possivel voltar para Dados do pagamento: {erro}"
            ) from erro

        # Conferência final dos campos obrigatórios que a tela de edição costuma
        # devolver vazios. O Ongsys recusa o salvamento com "é obrigatório
        # preencher ou selecionar um histórico para a despesa"; melhor descobrir
        # isso ANTES de clicar em Salvar do que depois, com o título em estado
        # incerto. Se o campo não aceitar o valor, o título falha sem salvar.
        self._habilitar_campos_formulario()
        self._garantir_historico_preenchido(titulo)

        # Salvar
        for tentativa in range(6):
            self._habilitar_campos_formulario()
            self._exigir_dominio_ongsys("salvar a edição do projetado")
            self._clicar(S.BOTAO_SALVAR_EDICAO, "salvar edição do projetado")
            self.salvar_foi_clicado = True
            self.page.wait_for_timeout(1200)

            try:
                if self._finalizar_sucesso_confirmado(titulo, resultado):
                    return resultado
            except ErroNavegador:
                # A edição real da 99 Tecnologia comprovou que o Ongsys pode
                # fechar o formulário sem manter o aviso verde tempo suficiente
                # para o Playwright enxergá-lo. Diferentemente de uma inclusão,
                # a edição preserva o Cód. Lançamento CP do projetado. Portanto,
                # só aceitamos o salvamento sem toast quando a lista mostra uma
                # única linha com o MESMO CP, já não projetada, e com documento,
                # fornecedor, valor, emissão e vencimento da planilha.
                id_convertido = self._confirmar_conversao_projetado_na_lista(
                    titulo, codigo_projetado
                )
                if id_convertido:
                    resultado.salvo = True
                    resultado.id_ongsys = id_convertido
                    resultado.avisos.append(
                        "Conversão do projetado confirmada pela lista do Ongsys "
                        f"(Cód. Lançamento {codigo_projetado}); a mensagem verde não foi capturada."
                    )
                    self._modal_inclusao = None
                    self._duplicado_titulo = None
                    self._duplicado_resultado = None
                    self._duplicado_tentados = set()
                    return resultado
                raise

            mensagem = self._mensagens_da_tela()
            if self._resolver_duplicado_pendente():
                continue
            raise ErroNavegador(
                "Nao foi possivel confirmar a alteracao do projetado. "
                f"Mensagens da tela: {mensagem or 'nenhuma'}. "
                "Nao clique em Salvar novamente antes de auditar."
            )

        raise ErroNavegador(
            "Não foi possível confirmar o salvamento do projetado após várias tentativas."
        )

    def _confirmar_conversao_projetado_na_lista(
        self, titulo: Titulo, codigo_projetado: str
    ) -> str:
        """Confirma uma edição sem toast pelo identificador imutável do projetado.

        Esta recuperação é exclusiva do fluxo ``Projetado -> Real``. Ela nunca
        é usada na inclusão normal, pois ali não existe um Cód. Lançamento já
        conhecido que vincule com segurança o antes e o depois.

        Retorna o novo ID apenas se houver exatamente uma linha que reúna todas
        as evidências: mesmo código CP, ausência de ``Projetado``, documento,
        fornecedor, valor, emissão e vencimento esperados.
        """
        codigo_alvo = re.sub(r"\s+", "", sem_acentos(codigo_projetado or ""))
        if not codigo_alvo:
            return ""

        try:
            self._modal_inclusao = None
            self.page.goto(self.config["ongsys_url"], wait_until="domcontentloaded")
            if not self._aplicar_periodo_lista(titulo.vencimento, titulo.vencimento):
                return ""
            if not self._gerar_relatorio_lista():
                return ""
            if not self._buscar_na_tabela(codigo_alvo):
                return ""

            documento_alvo = sem_acentos(titulo.documento)
            padrao_documento = re.compile(
                rf"(?<![A-Z0-9]){re.escape(documento_alvo)}(?![A-Z0-9])"
            )
            fornecedores = [
                re.sub(r"[^A-Z0-9]+", "", sem_acentos(nome))
                for nome in self.fornecedores_esperados_na_tela(titulo)
                if nome
            ]
            encontrados: list[str] = []

            for linha in self._linhas_visiveis_lista():
                texto = (linha.inner_text() or "").strip()
                id_linha, codigo_linha = self._identificadores_da_linha(texto)
                if not id_linha or codigo_linha != codigo_alvo:
                    continue
                texto_sem_acentos = sem_acentos(texto)
                if "PROJETADO" in texto_sem_acentos:
                    continue
                if not padrao_documento.search(texto_sem_acentos):
                    continue
                if formatar_dinheiro_br(titulo.valor) not in texto:
                    continue
                if not all(
                    self._texto_contem_data(texto, esperado)
                    for esperado in (titulo.emissao, titulo.vencimento)
                ):
                    continue
                texto_compacto = re.sub(r"[^A-Z0-9]+", "", texto_sem_acentos)
                if fornecedores and not any(
                    fornecedor in texto_compacto for fornecedor in fornecedores
                ):
                    continue
                encontrados.append(id_linha)
                self._texto_linha_por_id[self._normalizar_id(id_linha)] = texto
                self._codigos_lancamento_por_id[
                    self._normalizar_id(id_linha)
                ] = codigo_linha

            unicos = list(dict.fromkeys(encontrados))
            return unicos[0] if len(unicos) == 1 else ""
        except Exception:
            return ""

    def incluir(self, titulo: Titulo) -> ResultadoInclusao:
        """Abre o formulário, preenche, salva e devolve o ID. Nunca dá baixa."""
        if titulo.bloqueado:
            raise ErroNavegador(
                "Título bloqueado no planejamento: " + " | ".join(titulo.bloqueios)
            )

        resultado = ResultadoInclusao(documento_final=titulo.documento)
        self.salvar_foi_clicado = False
        self._modal_inclusao = None  # descarta o escopo de um título anterior, se houver
        self._texto_digitado = {}
        self._opcoes_aceitas = {}
        # Contexto para _resolver_duplicado_pendente: permite que qualquer
        # clique, em qualquer ponto do preenchimento, resolva um aviso de
        # documento duplicado que apareça no meio do caminho.
        self._duplicado_titulo = titulo
        self._duplicado_resultado = resultado
        # Números já ocupados no Ongsys (documentos_ja_usados) nunca são
        # oferecidos de novo se o aviso de duplicado aparecer no formulário.
        self._duplicado_tentados = {titulo.documento} | set(
            getattr(self, "_documentos_ocupados", set())
        )

        self.page.goto(self.config["ongsys_url"], wait_until="domcontentloaded")
        # Limpa QUALQUER modal/backdrop órfão remanescente de um título anterior.
        # Neste ponto ainda estamos na lista (nenhum formulário de inclusão
        # aberto), então é seguro remover tudo — foi um #modalErro/backdrop
        # sobrando que travou o clique em "Incluir" em NC AVENIDA e NUVEMCO CLOUD.
        self._purgar_modais_orfaos()
        self._fechar_modal_erro_confirmado()
        self._abrir_formulario_inclusao()
        # Fixa o escopo do modal ANTES de qualquer preenchimento: a página tem
        # campos com o mesmo ID escondidos em outros modais/formulários (§2 do
        # guia de operação real), e uma busca sem escopo pode atingir o
        # elemento errado sem erro nenhum — foi o caso do campo de Valor.
        self._modal_inclusao = self._detectar_modal_inclusao()
        self._campo(S.CAMPO_FORNECEDOR).first.wait_for(state="visible")
        self._conferir_tela_do_formulario()

        resultado.fornecedor_selecionado = self.selecionar_fornecedor_titulo(titulo)

        self._preencher(S.CAMPO_HISTORICO, titulo.historico)
        self._selecionar_lista(S.CAMPO_TIPO_DESPESA, titulo.tipo_despesa, "Tipo de despesa")
        self._fechar_popup_de_servico_nao_parametrizado(titulo.tipo_despesa)
        # Campos <input type="date"> e <input type="month">: a tela MOSTRA a
        # data em formato brasileiro, mas o valor interno que precisa ser
        # enviado ao preencher é sempre AAAA-MM-DD / AAAA-MM (padrão HTML5).
        # Preencher com dd/mm/aaaa é rejeitado como "valor malformado".
        self._preencher(S.CAMPO_DATA_EMISSAO, data_iso(titulo.emissao))
        self._preencher(S.CAMPO_DATA_VENCIMENTO, data_iso(titulo.vencimento))
        self._preencher(S.CAMPO_DATA_EFETIVACAO, data_iso(titulo.efetivacao))
        self._preencher_dinheiro(S.CAMPO_VALOR, titulo.valor, "Valor bruto da despesa")
        self._preencher(S.CAMPO_COMPETENCIA, competencia_iso(titulo.efetivacao))
        self._selecionar_lista(S.CAMPO_TIPO_DOCUMENTO, titulo.tipo_documento, "Tipo de documento")
        self._preencher(S.CAMPO_NUMERO_DOCUMENTO, titulo.documento)
        # Tab de verdade, não só fill(): a validação de documento duplicado
        # parece depender de um evento de blur/troca de foco real para
        # disparar — o mesmo problema já visto na máscara de moeda (§ Valor).
        # Sem isso, o aviso chega tarde e de forma imprevisível, às vezes só
        # no meio do preenchimento do rateio.
        self._campo(S.CAMPO_NUMERO_DOCUMENTO).first.press("Tab")

        # Código de barras: preenche apenas quando presente, só com dígitos
        # (espaços, pontos e hífens da planilha não fazem parte do código).
        if titulo.codigo_barras:
            barras_limpo = re.sub(r"\D", "", titulo.codigo_barras)
            self._preencher(S.CAMPO_CODIGO_BARRAS, barras_limpo)

        # Confirmado em uso real: o aviso de documento já existente pode
        # aparecer sozinho, de forma assíncrona, ao sair do campo — não só
        # depois de clicar em Salvar. Sem tratar aqui, o popup fica coberto
        # o resto do preenchimento, e os campos seguintes "somem" da tela.
        # (self._duplicado_tentados, não uma variável local: qualquer clique
        # daqui pra frente também pode resolver o mesmo aviso, via
        # `_resolver_duplicado_pendente`, e precisa enxergar as mesmas
        # variações já tentadas.)
        for rodada_dup in range(15):
            # Espera mais na primeira rodada (Ongsys pode demorar para validar
            # o documento original) e um pouco menos nas seguintes (o novo
            # valor já foi preenchido e Tab pressionado por _resolver_documento_duplicado).
            self.page.wait_for_timeout(800 if rodada_dup < 2 else 500)
            if not self._resolver_duplicado_pendente():
                break

        self._selecionar_lista(S.CAMPO_MEIO_PAGAMENTO, titulo.meio_pagamento, "Meio de pagamento")
        # O campo Tipo de Pagamento (#field_tipo_pagamento_eletronico) é
        # preenchido AUTOMATICAMENTE pelo Ongsys depois de selecionar o
        # fornecedor — confirmado pelo usuário em uso real. Tentar selecioná-lo
        # causava falha porque o rótulo configurado ("Boleto Simples de Outros
        # Bancos") não bate com o rótulo real ("Boleto Simples (Outros Bancos)").
        # A correção não é trocar o rótulo: é NÃO preencher este campo.
        # Registra o que o Ongsys escolheu para informação no relatório.
        # Confirmado pelo usuário (11/09/2026): o Ongsys puxa o tipo do
        # cadastro do fornecedor e esse é o correto. É só informação.
        tipo_pagamento_tela = self._ler_tipo_pagamento()
        if tipo_pagamento_tela and tipo_pagamento_tela != titulo.tipo_pagamento:
            resultado.avisos.append(
                f"Tipo de pagamento do cadastro do fornecedor: '{tipo_pagamento_tela}'."
            )
        self._selecionar_conta(titulo)

        # Segunda checagem, por segurança: em uso real o aviso de documento
        # duplicado chegou a aparecer só depois de todos os campos acima
        # preenchidos, tarde demais para a primeira janela de espera.
        self._resolver_duplicado_pendente()

        # O formulário é um assistente com abas. Os campos de rateio existem no
        # DOM desde o início, mas só ficam visíveis depois de abrir esta aba —
        # sem isso o preenchimento falha por "element is not visible".
        self._abrir_aba_rateio()

        for indice, rateio in enumerate(titulo.rateios, start=1):
            if indice > 1:
                resultado.pendencias.extend(
                    self._incluir_linha_rateio(
                        indice - 1, titulo.rateios[indice - 2]
                    )
                )
            resultado.pendencias.extend(self._preencher_rateio(indice, rateio))

        self._conferir_totais_na_tela(titulo)

        for tentativa in range(6):
            self._exigir_dominio_ongsys("salvar o título")
            self._clicar(S.BOTAO_SALVAR, "salvar título")
            self.salvar_foi_clicado = True
            self.page.wait_for_timeout(1200)

            # A confirmação verde é prova mais forte que qualquer popup de
            # erro antigo ainda escondido no DOM. Precisa ser verificada ANTES
            # de tentar resolver duplicidade: no piloto real o título foi
            # criado, mas um #modalErro invisível disparou uma troca de aba no
            # formulário que já tinha fechado e o checkpoint virou FALHA.
            if self._finalizar_sucesso_confirmado(titulo, resultado):
                return resultado

            mensagem = self._mensagens_da_tela()

            if self._resolver_duplicado_pendente():
                # Nesse caso o clique em Salvar só exibiu a duplicidade; ainda
                # não há confirmação de criação e uma nova variação será tentada.
                self.salvar_foi_clicado = False
                self._conferir_totais_na_tela(titulo)
                continue

            if self._finalizar_sucesso_confirmado(titulo, resultado):
                return resultado

            recusa = self._recusa_definitiva_do_ongsys(mensagem)
            if recusa and not self._formulario_fechou():
                # O Ongsys respondeu com um erro conhecido e o formulário
                # continua aberto, sem confirmação de sucesso: o título NÃO
                # foi gravado. Volta a ser uma falha comum (pode ser lançado
                # depois de corrigir a causa), e não uma incerteza bloqueada.
                self.salvar_foi_clicado = False
                raise ErroNavegador(recusa.format(
                    emissao=data_br(titulo.emissao),
                    vencimento=data_br(titulo.vencimento),
                    efetivacao=data_br(titulo.efetivacao),
                ))

            raise ErroNavegador(
                f"O formulário continuou aberto depois de salvar (tentativa {tentativa + 1}). "
                f"Mensagens da tela: {mensagem or 'nenhuma'}"
            )

        raise ErroNavegador(
            "Não foi possível salvar após seis tentativas de variação de documento. "
            f"Documentos testados: {', '.join(sorted(self._duplicado_tentados))}."
        )

    def _selecionar_conta(self, titulo: Titulo) -> None:
        campo = self._campo(S.CAMPO_CONTA_PAGADORA).first
        if not titulo.conta_pagadora_id and not titulo.conta_pagadora_nome:
            # A opção 0 da interface significa manter Conta pagadora vazia.
            # Nunca escolhe a primeira conta como fallback: se o campo já
            # vier preenchido, procura exclusivamente uma option de value="".
            if not (campo.input_value() or "").strip():
                return
            opcoes_locator = campo.locator("option")
            for indice in range(opcoes_locator.count()):
                opcao = opcoes_locator.nth(indice)
                if opcao.get_attribute("value") == "":
                    campo.select_option(value="")
                    if (campo.input_value() or "").strip():
                        raise ErroFatal(
                            "O Ongsys não manteve a Conta pagadora vazia. "
                            "A execução parou sem salvar este título."
                        )
                    return
            raise ErroFatal(
                "A tela abriu com uma Conta pagadora preenchida e não oferece "
                "uma opção vazia. A execução parou para não usar a conta errada."
            )

        opcoes = [t.strip() for t in campo.locator("option").all_inner_texts()]
        alvo = [o for o in opcoes if sem_acentos(titulo.conta_pagadora_nome) in sem_acentos(o)]
        if not alvo:
            raise ErroFatal(
                f"A conta pagadora '{titulo.conta_pagadora_nome}' não está disponível na tela.\n"
                f"Contas oferecidas: {', '.join(o for o in opcoes if o) or 'nenhuma'}.\n"
                "A execução parou (§22)."
            )
        campo.select_option(label=alvo[0])

    def _abrir_aba_rateio(self) -> None:
        """Abre a aba "Rateio e Parcelamento" do assistente de inclusão.

        Se a primeira linha já estiver visível, não faz nada — a aba pode já
        estar aberta dependendo de como o formulário foi carregado.
        """
        primeiro_campo = self._campo(S.categoria_conta(1)).first
        try:
            if primeiro_campo.is_visible():
                return
        except Exception:
            pass

        try:
            self._clicar(S.ABA_RATEIO_PARCELAMENTO, "abrir aba de rateio")
        except Exception as erro:
            raise ErroNavegador(
                "Não foi possível abrir a aba 'Rateio e Parcelamento' do formulário. "
                f"O título não foi salvo. Detalhe: {erro}"
            ) from erro

        try:
            primeiro_campo.wait_for(state="visible", timeout=self.timeout)
        except Exception as erro:
            raise ErroNavegador(
                "A aba 'Rateio e Parcelamento' foi acionada, mas os campos de rateio "
                "não ficaram visíveis. O título não foi salvo."
            ) from erro
        # O campo pode ficar "visível" para o Playwright um instante antes de
        # a transição da aba (CSS) realmente terminar e ele aceitar digitação
        # — observado em uso real como um autocomplete que nunca recebeu o
        # texto digitado. Uma pequena pausa aqui é mais barata que arriscar.
        self.page.wait_for_timeout(300)

    def _linha_rateio_visivel(self, indice: int) -> bool:
        """Confirma se a linha pedida já existe e está pronta para uso."""
        try:
            campo = self._campo(S.categoria_conta(indice)).first
            return bool(campo.count()) and campo.is_visible()
        except Exception:
            return False

    def _incluir_linha_rateio(
        self, indice_anterior: int, rateio_anterior: Rateio | None = None
    ) -> list[str]:
        """Cria a próxima linha pelo único botão real ``#btn_clone_1``.

        O número do botão não acompanha a quantidade de linhas. A tela real
        mantém sempre um único botão superior com esse ID; ``#btn_clone_2`` e
        seguintes não existem. Depois do clique, a nova linha é confirmada pelo
        ID de seu primeiro campo antes de continuar.
        """
        proximo_indice = indice_anterior + 1
        pendencias: list[str] = []
        recuperou_linha_anterior = False
        if self._linha_rateio_visivel(proximo_indice):
            return pendencias
        falhas: list[str] = []
        for modo in ("clique", "JavaScript"):
            if self._linha_rateio_visivel(proximo_indice):
                return pendencias
            try:
                botao = self._campo(S.BOTAO_INCLUIR_LINHA_RATEIO).first
                if modo == "clique":
                    botao.click(timeout=4000)
                else:
                    botao.evaluate("el => el.click()")
                for _ in range(30):
                    if self._linha_rateio_visivel(proximo_indice):
                        self.page.wait_for_timeout(150)
                        return pendencias
                    mensagem = self._texto_modal_erro()
                    if "PREENCHER A LINHA ATUAL DE RATEIO" in sem_acentos(mensagem):
                        diagnostico = self._diagnostico_linha_rateio(indice_anterior)
                        self._fechar_modal_erro_confirmado()
                        # O onchange da Categoria/Conta pode terminar tarde e
                        # limpar o Projeto/CC depois de ele já ter sido lido como
                        # correto. Isso aconteceu no BENEFIX: a linha 1 parecia
                        # pronta, mas o próprio Ongsys a recusou ao criar a linha
                        # 2. A recusa é a evidência mais recente; restaura a linha
                        # anterior uma única vez e só então repete o clone.
                        if rateio_anterior is not None and not recuperou_linha_anterior:
                            recuperou_linha_anterior = True
                            for rodada_recuperacao in range(2):
                                self._garantir_campos_obrigatorios_rateio(
                                    indice_anterior, rateio_anterior
                                )
                                # Reescrever o CC pode limpar o Subprojeto. Por
                                # isso ele é conferido depois do CC em cada
                                # passada, nunca somente antes da recuperação.
                                if rateio_anterior.subprojeto and not self._selecao_confere(
                                    S.subprojeto(indice_anterior),
                                    self._ler_valor(S.subprojeto(indice_anterior)),
                                    rateio_anterior.subprojeto,
                                    codigo=True,
                                ):
                                    try:
                                        _, pendencia = self._selecionar_subprojeto(
                                            indice_anterior, rateio_anterior.subprojeto
                                        )
                                    except ErroFatal:
                                        raise
                                    except Exception as erro:
                                        # Subprojeto nunca derruba o título.
                                        self._limpar_digitacao(
                                            S.subprojeto(indice_anterior)
                                        )
                                        pendencia = self._subprojeto_preso_com_outro_codigo(
                                            indice_anterior, rateio_anterior.subprojeto
                                        ) or (
                                            f"Linha {indice_anterior}: Subprojeto "
                                            f"{rateio_anterior.subprojeto} não pôde ser "
                                            f"selecionado ({type(erro).__name__}). Campo "
                                            "deixado vazio; informe manualmente no Ongsys."
                                        )
                                    if pendencia and pendencia not in pendencias:
                                        pendencias.append(pendencia)
                                if rodada_recuperacao == 0:
                                    # Deixa os onchanges dependentes terminarem
                                    # antes da confirmação final da linha.
                                    self.page.wait_for_timeout(600)
                            break
                        raise ErroNavegador(
                            f"O Ongsys recusou criar a linha {proximo_indice} porque "
                            f"a linha {indice_anterior} não está completa. {diagnostico} "
                            "O título NÃO foi salvo."
                        )
                    self.page.wait_for_timeout(100)
            except ErroNavegador:
                raise
            except Exception as erro:
                falhas.append(f"{modo}: {erro}")

        # Sem este resumo, a falha das 15:30 de 11/09 (BENEFIX) terminou com
        # "Detalhes:" vazio e só a imagem mostrou o Projeto/CC apagado.
        detalhes = " | ".join(falhas) or "nenhum erro técnico no clique"
        raise ErroNavegador(
            f"A linha de rateio {proximo_indice} não foi criada. "
            "O botão superior #btn_clone_1 foi acionado por clique e JavaScript. "
            f"Detalhes: {detalhes}. Linha {indice_anterior}: "
            f"{self._diagnostico_linha_rateio(indice_anterior)} "
            "O título NÃO foi salvo."
        )

    def _diagnostico_linha_rateio(self, indice: int) -> str:
        """Resumo dos campos da linha para tornar uma recusa do Ongsys útil."""
        campos = (
            ("Categoria/Conta", S.categoria_conta(indice)),
            ("Projeto/CC", S.projeto_cc(indice)),
            ("Subprojeto", S.subprojeto(indice)),
            ("Atividade", S.atividade(indice)),
            ("Percentual", S.percentual_rateio(indice)),
            ("Valor", S.valor_rateio(indice)),
        )
        valores = [
            f"{nome}='{self._ler_valor(seletor) or '[vazio]'}'"
            for nome, seletor in campos
        ]
        return "Campos vistos: " + ", ".join(valores) + "."

    def _conferir_totais_na_tela(self, titulo: Titulo) -> None:
        """Última conferência antes de salvar: campos, soma e percentual (§12)."""
        tolerancia = self.config["tolerancia_conciliacao"]

        # O valor do cabeçalho é relido da tela: a máscara de moeda já
        # transformou 300,00 em 300.000,00 em uso real, e um valor errado aqui
        # criaria um título mil vezes maior.
        valor_cabecalho = para_decimal(self._ler_valor(S.CAMPO_VALOR))
        if valor_cabecalho is None or abs(valor_cabecalho - titulo.valor) > tolerancia:
            raise ErroNavegador(
                f"O valor bruto na tela é R$ "
                f"{formatar_dinheiro_br(valor_cabecalho) if valor_cabecalho is not None else '[ilegível]'}, "
                f"mas o título vale R$ {titulo.valor_br}. O título NÃO foi salvo."
            )

        soma = Decimal("0")
        soma_percentual_tela = Decimal("0")
        for indice, rateio in enumerate(titulo.rateios, start=1):
            categoria_lida = self._ler_valor(S.categoria_conta(indice))
            if not self._selecao_confere(
                S.categoria_conta(indice), categoria_lida, rateio.categoria_detalhada
            ):
                raise ErroNavegador(
                    f"Linha {indice}: Categoria/Conta obrigatória não ficou "
                    f"confirmada na tela. Esperado '{rateio.categoria_detalhada}', "
                    f"encontrado '{categoria_lida or '[vazio]'}'. O título NÃO foi salvo."
                )

            projeto_lido = self._ler_valor(S.projeto_cc(indice))
            if rateio.centro_custo and not self._selecao_confere(
                S.projeto_cc(indice),
                projeto_lido,
                rateio.centro_custo,
                aceitar_prefixo=True,
            ):
                raise ErroNavegador(
                    f"Linha {indice}: Projeto/Centro de Custos obrigatório não "
                    f"ficou confirmado na tela. Esperado '{rateio.centro_custo}', "
                    f"encontrado '{projeto_lido or '[vazio]'}'. O título NÃO foi salvo."
                )

            valor = para_decimal(self._ler_valor(S.valor_rateio(indice)))
            if valor is None:
                raise ErroNavegador(f"Não foi possível ler o valor da linha de rateio {indice}.")
            soma += valor

            percentual = para_decimal(
                self._ler_valor(S.percentual_rateio(indice))
            )
            if percentual is None:
                raise ErroNavegador(
                    f"Não foi possível ler o percentual da linha de rateio {indice}."
                )
            soma_percentual_tela += percentual
        if abs(soma - titulo.valor) > tolerancia:
            raise ErroNavegador(
                f"A soma das linhas na tela é R$ {formatar_dinheiro_br(soma)}, mas o título "
                f"vale R$ {titulo.valor_br}. O título NÃO foi salvo."
            )
        if abs(soma_percentual_tela - Decimal("100")) > Decimal("0.02"):
            raise ErroNavegador(
                f"Os percentuais visíveis na tela somam {soma_percentual_tela}% "
                "em vez de 100%. O título NÃO foi salvo."
            )

        if self._linha_rateio_visivel(len(titulo.rateios) + 1):
            raise ErroNavegador(
                "O Ongsys exibiu mais linhas de rateio do que a planilha. "
                "O título NÃO foi salvo para evitar rateio adicional indevido."
            )

    def _texto_modal_erro(self) -> str:
        """Lê `#modalErro` direto pelo ID, sem passar por seletor combinado,

        `:visible` ou lógica de exclusão nenhuma. `_mensagens_da_tela` já
        deveria enxergar esse popup por outros dois caminhos, mas em uso real
        a detecção falhou de forma persistente mesmo com o popup claramente
        na tela (bloqueando cliques) — este é o caminho mais direto possível,
        para eliminar qualquer ponto de falha da combinação de seletores.
        """
        try:
            modal = self.page.locator("#modalErro")
            if modal.count() == 0:
                return ""
            if not modal.first.is_visible():
                return ""
            return modal.first.inner_text().strip()
        except Exception:
            return ""

    def _mensagens_da_tela(self) -> str:
        """Texto de avisos visíveis: tanto mensagens inline quanto popups.

        Um erro real observado em uso ("O número do documento fiscal já
        existe para esse fornecedor.") aparece num popup próprio, não em
        `.help-block`/`.alert`. Por isso também lemos qualquer popup genuíno
        (excluindo sempre o formulário principal, como em `_algum_popup_visivel`).
        """
        textos: list[str] = []
        try:
            textos.extend(t.strip() for t in self.page.locator(S.MENSAGENS).all_inner_texts() if t.strip())
        except Exception:
            pass
        try:
            popups = self.page.locator(S.CONTEINERES_POPUP)
            for indice in range(min(popups.count(), 3)):
                candidato = popups.nth(indice)
                if not candidato.is_visible():
                    continue
                if candidato.locator(S.CAMPO_FORNECEDOR).count() > 0:
                    continue  # é o próprio formulário, não um popup
                texto = candidato.inner_text().strip()
                if texto:
                    textos.append(texto)
        except Exception:
            pass
        return " | ".join(dict.fromkeys(textos))[:800]

    #: Respostas do Ongsys ao Salvar que provam que nada foi gravado.
    RECUSAS_DEFINITIVAS = {
        "PERIODO FECHADO": (
            "O Ongsys recusou o título: PERÍODO FECHADO. Alguma data dele cai "
            "em um mês já fechado no Ongsys (emissão {emissao}, vencimento "
            "{vencimento}, efetivação {efetivacao}). Nada foi salvo. Reabra o "
            "período no Ongsys ou ajuste a data na planilha e rode de novo."
        ),
    }

    @classmethod
    def _recusa_definitiva_do_ongsys(cls, mensagem: str) -> str:
        """Explicação para uma recusa conhecida do Ongsys, ou "" se não houver."""
        texto = sem_acentos(mensagem or "")
        for marca, explicacao in cls.RECUSAS_DEFINITIVAS.items():
            if marca in texto:
                return explicacao
        return ""

    @staticmethod
    def _documento_duplicado(mensagem: str) -> bool:
        """Só reage à mensagem específica, não a qualquer texto da página.

        A versão anterior procurava 'EXISTENTE' no corpo inteiro da página, o
        que casaria com qualquer texto solto e dispararia troca de documento sem
        necessidade.
        """
        texto = sem_acentos(mensagem)
        if not texto:
            return False
        gatilhos = (
            "DOCUMENTO JA EXISTE",
            "DOCUMENTO JA CADASTRADO",
            "NUMERO DE DOCUMENTO JA",
            "NUMERO DO DOCUMENTO",  # cobre "...do documento fiscal já existe...", confirmado em uso real
            "JA EXISTE UM TITULO",
            "DOCUMENTO EXISTENTE",
        )
        if any(g in texto for g in gatilhos):
            return True
        # Rede de segurança ampla: qualquer aviso que junte os dois conceitos.
        return "JA EXISTE" in texto and "DOCUMENTO" in texto

    def _formulario_fechou(self) -> bool:
        """True somente quando a JANELA do formulário não está mais visível.

        A versão antiga olhava o campo Fornecedor, que fica escondido na aba
        "Rateio e Parcelamento" — justamente onde o Salvar é clicado. Com o
        Ongsys lento (18/09/2026), o formulário ainda aberto e processando era
        dado como "fechado"; o robô desistia e navegava para outra tela no
        meio do salvamento (ORBITA, TATIANA, AGUASUL, ASSINAFACIL NF 1520039).
        """
        modal = self._modal_inclusao
        if modal is not None:
            try:
                return not modal.is_visible()
            except Exception:
                return False
        try:
            campo = self.page.locator(f"{S.CAMPO_FORNECEDOR}:visible")
            return campo.count() == 0
        except Exception:
            return False

    #: Quanto esperar pela resposta do Salvar enquanto o formulário continua
    #: aberto e sem aviso. O Ongsys chegou a passar de 3 s em 18/09/2026.
    ESPERA_MAXIMA_SALVAR_MS = 60_000

    def _aguardar_resposta_do_salvar(self) -> bool:
        """Espera a confirmação verde, o fechamento do formulário ou um aviso.

        Devolve True somente quando a confirmação verde foi vista. Nunca sai
        enquanto o formulário estiver aberto e processando, até o limite.
        """
        passo = 500
        for _ in range(self.ESPERA_MAXIMA_SALVAR_MS // passo):
            if self._inclusao_confirmada():
                return True
            if self._formulario_fechou():
                # A janela fechou: o toast verde pode surgir logo em seguida.
                for _ in range(6):
                    self.page.wait_for_timeout(400)
                    if self._inclusao_confirmada():
                        return True
                return False
            try:
                if self._texto_modal_erro():
                    return False  # aviso (ex.: duplicado) — o chamador trata
            except Exception:
                pass
            self.page.wait_for_timeout(passo)
        return self._inclusao_confirmada()

    #: Mensagens de confirmação verde aceitas como prova de salvamento.
    #: A inclusão mostra "incluído com sucesso"; a edição pode mostrar
    #: "alterado", "atualizado" ou "editado" — todas são aceitas.
    _MENSAGENS_SUCESSO = (
        "Contas a pagar incluído com sucesso",
        "Contas a pagar incluido com sucesso",
        "Contas a pagar alterado com sucesso",
        "Contas a pagar atualizado com sucesso",
        "Contas a pagar editado com sucesso",
        "alterado com sucesso",
        "atualizado com sucesso",
        "salvo com sucesso",
    )

    def _inclusao_confirmada(self) -> bool:
        """Reconhece a confirmação verde mostrada após a criação ou edição.

        A mensagem observada no piloto de inclusão foi "Contas a pagar
        incluído com sucesso!". Na edição de projetado a mensagem pode ser
        "alterado com sucesso" ou variação. Ambas são aceitas.
        """
        for texto in self._MENSAGENS_SUCESSO:
            try:
                encontrados = self.page.get_by_text(texto, exact=False)
                for indice in range(min(encontrados.count(), 3)):
                    if encontrados.nth(indice).is_visible():
                        return True
            except Exception:
                continue
        return False

    def _fechar_confirmacao_sucesso(self) -> bool:
        """Fecha apenas o botão próximo da mensagem de sucesso já confirmada."""
        for texto in self._MENSAGENS_SUCESSO:
            try:
                mensagens = self.page.get_by_text(texto, exact=False)
                for indice in range(min(mensagens.count(), 3)):
                    mensagem = mensagens.nth(indice)
                    if not mensagem.is_visible():
                        continue
                    botao = mensagem.locator(
                        "xpath=following::*[self::button or self::a or @role='button']"
                        "[contains(normalize-space(.), 'Fechar')][1]"
                    )
                    if botao.count() and botao.first.is_visible():
                        botao.first.click(timeout=2000)
                        self.page.wait_for_timeout(300)
                        return True
            except Exception:
                continue
        return False

    def _finalizar_sucesso_confirmado(
        self, titulo: Titulo, resultado: ResultadoInclusao
    ) -> bool:
        """Finaliza sem lançar exceção depois que a criação já foi provada.

        Depois da confirmação, qualquer dificuldade para fechar o aviso ou
        localizar o ID vira pendência de auditoria — nunca uma FALHA que
        permitiria tentar criar o mesmo título novamente.

        IMPORTANTE: somente a mensagem verde "Contas a pagar incluído com
        sucesso" é prova real de salvamento. O formulário fechar sozinho
        pode acontecer durante a resolução de documento duplicado e NÃO
        significa que o título foi salvo — tratar como falha nesses casos.
        """
        # A confirmação verde do Ongsys é um "toast" que some sozinho depois de
        # alguns segundos. Uma única checagem, no instante errado, pode perdê-lo
        # mesmo com o título JÁ salvo — foi o que gerou a duplicata da Debora
        # NF 34 (a confirmação foi perdida, o formulário já tinha fechado, e o
        # retry criou a NF 34.1). Por isso a checagem é repetida algumas vezes,
        # em janelas curtas, antes de concluir que houve falha.
        confirmou_mensagem = self._aguardar_resposta_do_salvar()

        if not confirmou_mensagem:
            if self._formulario_fechou():
                # Antes de deixar o chamador criar uma variação de documento
                # (o que duplicaria um título que talvez JÁ tenha sido salvo),
                # guarda uma evidência da tela nesse instante, para conferência.
                self.screenshot_falha(f"sem_confirmacao_{titulo.documento}")
                raise ErroNavegador(
                    "O formulário fechou sem a mensagem de confirmação verde. "
                    "O título provavelmente NÃO foi salvo. Isso pode acontecer "
                    "quando a resolução de documento duplicado fecha o formulário "
                    "acidentalmente. Verifique manualmente no Ongsys."
                )
            return False

        resultado.salvo = True
        self._modal_inclusao = None
        self._duplicado_titulo = None
        self._duplicado_resultado = None
        self._duplicado_tentados = set()

        if confirmou_mensagem and not self._fechar_confirmacao_sucesso():
            resultado.avisos.append(
                "Título salvo; a janela verde de confirmação não fechou automaticamente."
            )

        resultado.id_ongsys = self._descobrir_id(titulo, resultado.documento_final)
        if not resultado.id_ongsys:
            resultado.avisos.append(
                "Título salvo, mas o ID não foi localizado automaticamente. Rode auditar.bat."
            )
        return True

    def ids_resultado_busca(self, termo: str, inicio=None, fim=None) -> list[str]:
        """Lista IDs retornados pelo filtro de fornecedor, sem alterar títulos."""
        try:
            alvo_lista = self.resolver_apelido(termo)
            self.page.goto(self.config["ongsys_url"], wait_until="domcontentloaded")
            if inicio is not None and not self._aplicar_periodo_lista(inicio, fim or inicio):
                raise ErroNavegador(
                    "Nao foi possivel aplicar e confirmar o filtro Vencimento e os campos "
                    "De/Ate da lista. "
                    "A busca foi interrompida para nao informar falsamente que o titulo nao existe."
                )
            busca = self._campo_busca_fornecedor()
            if not busca.count():
                raise ErroNavegador(
                    "O campo 'Filtrar por fornecedor' nao foi encontrado na lista."
                )
            self._selecionar_fornecedor_filtro(termo)
            if not self._gerar_relatorio_lista():
                raise ErroNavegador(
                    "O comando Ver relatorio (icone fa-file-text) nao foi encontrado."
                )
            self.page.wait_for_timeout(500)
            ids, ids_nao_relacionados = self._ids_fornecedor_visiveis(alvo_lista)
            busca_geral_usada = False
            if not ids:
                busca_geral_usada = self._buscar_na_tabela(alvo_lista)
                if busca_geral_usada:
                    ids, ids_nao_relacionados = self._ids_fornecedor_visiveis(alvo_lista)
            if not ids and ids_nao_relacionados:
                evidencia = self.screenshot_falha("filtros_lista_fornecedor")
                estado_busca = (
                    "e o fallback Buscar foi usado"
                    if busca_geral_usada else "mas o campo Buscar nao foi localizado"
                )
                detalhe_evidencia = f" Evidencia: {evidencia}." if evidencia else ""
                raise ErroNavegador(
                    "A tabela continuou exibindo linhas de outros fornecedores depois da busca "
                    f"por '{alvo_lista}'. Ver relatorio foi acionado {estado_busca}. "
                    "Nenhum desses IDs foi aceito."
                    f"{detalhe_evidencia}"
                )
            return list(dict.fromkeys(ids))
        except ErroNavegador:
            raise
        except Exception as erro:
            raise ErroNavegador(f"Falha ao pesquisar a lista do Ongsys: {erro}") from erro

    def ids_resultado_busca_documento(
        self, documento: str, inicio=None, fim=None
    ) -> list[str]:
        """Pesquisa um documento final exato no campo geral da tabela.

        Esta rota é usada quando o checkpoint já conhece a variação gravada
        pelo Ongsys, como ``BL.1``. Ela evita depender do nome do fornecedor
        da planilha, que pode ser diferente da razão social cadastrada. Os IDs
        retornados ainda são abertos e validados por fornecedor, valor e datas
        antes de qualquer atualização do checkpoint.
        """
        try:
            documento = str(documento or "").strip()
            if not documento:
                return []
            self.page.goto(self.config["ongsys_url"], wait_until="domcontentloaded")
            if inicio is not None and not self._aplicar_periodo_lista(inicio, fim or inicio):
                raise ErroNavegador(
                    "Nao foi possivel aplicar e confirmar o periodo de Vencimento da lista."
                )
            if not self._gerar_relatorio_lista():
                raise ErroNavegador(
                    "O comando Ver relatorio (icone fa-file-text) nao foi encontrado."
                )
            self.page.wait_for_timeout(500)
            if not self._buscar_na_tabela(documento):
                raise ErroNavegador("O campo geral Buscar nao foi localizado na tabela.")

            return self._ids_documento_na_lista_atual(documento)
        except ErroNavegador:
            raise
        except Exception as erro:
            raise ErroNavegador(
                f"Falha ao pesquisar o documento final na lista do Ongsys: {erro}"
            ) from erro

    def _ids_documento_na_lista_atual(self, documento: str) -> list[str]:
        """Extrai IDs cujo documento aparece inteiro nas linhas atuais."""
        padrao = re.compile(
            rf"(?<![A-Z0-9]){re.escape(sem_acentos(documento))}(?![A-Z0-9])"
        )
        ids: list[str] = []
        for linha in self._linhas_visiveis_lista():
            texto = linha.inner_text()
            if not padrao.search(sem_acentos(texto)):
                continue
            id_linha, codigo = self._identificadores_da_linha(texto)
            if not id_linha:
                continue
            ids.append(id_linha)
            self._texto_linha_por_id[self._normalizar_id(id_linha)] = texto
            if codigo:
                self._codigos_lancamento_por_id[
                    self._normalizar_id(id_linha)
                ] = codigo
        return list(dict.fromkeys(ids))

    def _ordenar_lista_por_id_decrescente(self) -> bool:
        """Coloca os títulos recém-criados no topo sem depender do índice Buscar."""
        try:
            cabecalhos = self.page.locator("table thead th")
            alvo = None
            for indice in range(min(cabecalhos.count(), 30)):
                candidato = cabecalhos.nth(indice)
                if sem_acentos(candidato.inner_text()).strip() == "ID":
                    alvo = candidato
                    break
            if alvo is None:
                return False
            classe = (alvo.get_attribute("class") or "").lower()
            aria = (alvo.get_attribute("aria-sort") or "").lower()
            if "sorting_desc" in classe or aria == "descending":
                return True
            alvo.click()
            if not self._aguardar_lista_pronta():
                return False
            classe = (alvo.get_attribute("class") or "").lower()
            aria = (alvo.get_attribute("aria-sort") or "").lower()
            if "sorting_desc" in classe or aria == "descending":
                return True
            alvo.click()
            if not self._aguardar_lista_pronta():
                return False
            classe = (alvo.get_attribute("class") or "").lower()
            aria = (alvo.get_attribute("aria-sort") or "").lower()
            return "sorting_desc" in classe or aria == "descending"
        except Exception:
            return False

    def _ids_recentes_sem_indice(self, titulo: Titulo, documento: str) -> list[str]:
        """Busca o recém-criado entre os maiores IDs, sem pesquisar texto.

        O total de registros da lista é atualizado imediatamente após o
        salvamento, mas o índice do campo Buscar demora para reconhecer o novo
        documento. Limpar a busca e ordenar por ID decrescente expõe a linha
        recém-criada. Ainda exigimos documento + valor + emissão + vencimento e
        só aceitamos um candidato único.
        """
        try:
            self.page.goto(self.config["ongsys_url"], wait_until="domcontentloaded")
            if not self._aplicar_periodo_lista(titulo.vencimento, titulo.vencimento):
                return []
            if not self._gerar_relatorio_lista():
                return []
            if not self._buscar_na_tabela(""):
                return []
            if not self._ordenar_lista_por_id_decrescente():
                return []

            ids_documento = self._ids_documento_na_lista_atual(documento)
            return [
                id_linha
                for id_linha in ids_documento
                if self._linha_bate_assinatura(
                    titulo, self.texto_linha_para_id(id_linha)
                )
            ]
        except Exception:
            return []

    @staticmethod
    def _normalizar_id(id_ongsys: str) -> str:
        return str(id_ongsys or "").strip().lstrip("0") or "0"

    @staticmethod
    def _identificadores_da_linha(texto: str) -> tuple[str, str]:
        """Extrai o ID numérico e o Cód. Lançamento CP... de uma linha."""
        ids = re.findall(r"\b0*\d{6,8}\b", texto or "")
        codigo = re.search(r"\bCP\s*\d+\b", sem_acentos(texto or ""))
        return (ids[0] if ids else "", re.sub(r"\s+", "", codigo.group(0)) if codigo else "")

    def codigo_lancamento_para_id(self, id_ongsys: str) -> str:
        return self._codigos_lancamento_por_id.get(self._normalizar_id(id_ongsys), "")

    def texto_linha_para_id(self, id_ongsys: str) -> str:
        return self._texto_linha_por_id.get(self._normalizar_id(id_ongsys), "")

    def _linhas_visiveis_lista(self, limite: int = 60) -> list:
        """Devolve apenas linhas realmente visíveis da tabela principal.

        O DataTables mantém no DOM linhas de consultas anteriores. Ler todas
        elas produzia ``NAO_VERIFICADO`` mesmo quando o relatório atual estava
        correto. Objetos falsos dos testes antigos não expõem ``is_visible``;
        nesses casos a linha é tratada como visível para compatibilidade.
        """
        linhas = self.page.locator(S.LINHAS_DA_LISTA)
        visiveis = []
        for indice in range(min(linhas.count(), limite)):
            linha = linhas.nth(indice)
            verificar = getattr(linha, "is_visible", None)
            if callable(verificar):
                try:
                    if not verificar():
                        continue
                except Exception:
                    continue
            visiveis.append(linha)
        return visiveis

    def _textos_linhas_visiveis_lista(self, limite: int = 60) -> list[str]:
        textos: list[str] = []
        for linha in self._linhas_visiveis_lista(limite):
            try:
                texto = (linha.inner_text() or "").strip()
            except Exception:
                continue
            if texto:
                textos.append(texto)
        return textos

    def _ids_fornecedor_visiveis(self, termo: str) -> tuple[list[str], list[str]]:
        """Separa IDs do fornecedor pedido de qualquer linha antiga da tela."""
        ids: list[str] = []
        ids_nao_relacionados: list[str] = []
        termo_n = sem_acentos(termo)
        for linha in self._linhas_visiveis_lista():
            texto = linha.inner_text()
            id_linha, codigo = self._identificadores_da_linha(texto)
            if not id_linha:
                continue
            if termo_n and termo_n in sem_acentos(texto):
                ids.append(id_linha)
                self._texto_linha_por_id[self._normalizar_id(id_linha)] = texto
                if codigo:
                    self._codigos_lancamento_por_id[
                        self._normalizar_id(id_linha)
                    ] = codigo
            else:
                ids_nao_relacionados.append(id_linha)
        return list(dict.fromkeys(ids)), list(dict.fromkeys(ids_nao_relacionados))

    def _encontrar_linha_por_id_ou_codigo(self, id_ongsys: str, codigo: str = ""):
        id_alvo = self._normalizar_id(id_ongsys)
        codigo_alvo = sem_acentos(codigo).replace(" ", "")
        for linha in self._linhas_visiveis_lista():
            texto = linha.inner_text()
            id_linha, codigo_linha = self._identificadores_da_linha(texto)
            bate_id = bool(id_linha) and self._normalizar_id(id_linha) == id_alvo
            bate_codigo = bool(codigo_alvo) and codigo_linha == codigo_alvo
            if bate_id or bate_codigo:
                if id_linha and codigo_linha:
                    self._codigos_lancamento_por_id[
                        self._normalizar_id(id_linha)
                    ] = codigo_linha
                return linha
        return None

    def localizar_linha_titulo(self, id_ongsys: str, fornecedor: str = ""):
        """Localiza a linha por fornecedor; se necessário, usa o código CP.

        O ID nunca é digitado em "Filtrar por fornecedor". A busca geral da
        tabela recebe primeiro o Cód. Lançamento capturado na linha e só usa o
        ID como último recurso para títulos antigos sem código em memória.
        """
        busca_fornecedor = self._campo_busca_fornecedor()
        if fornecedor and busca_fornecedor.count():
            try:
                self._selecionar_fornecedor_filtro(fornecedor)
                self._gerar_relatorio_lista()
                self.page.wait_for_timeout(500)
                alvo = self._encontrar_linha_por_id_ou_codigo(id_ongsys)
                if alvo is not None:
                    return alvo
            except Exception:
                pass
            # O cadastro pode usar outra razão social, ou o autocomplete pode
            # ter escolhido um cadastro duplicado do mesmo fornecedor (visto em
            # 2026-10-01: a lista filtrada voltava vazia e a busca pelo ID,
            # feita com o filtro ainda aplicado, nunca achava o título). O
            # filtro é sempre limpo antes de procurar pelo código CP ou pelo
            # próprio ID, nunca por aproximação. Só consulta a lista.
            try:
                busca_fornecedor.fill("")
                self._gerar_relatorio_lista()
                self.page.wait_for_timeout(500)
            except Exception:
                pass

        codigo = self.codigo_lancamento_para_id(id_ongsys)
        termos = [x for x in (codigo, id_ongsys) if x]
        for termo in dict.fromkeys(termos):
            if not self._buscar_na_tabela(termo):
                return None
            alvo = self._encontrar_linha_por_id_ou_codigo(id_ongsys, codigo)
            if alvo is not None:
                return alvo
        return None

    def abrir_linha_para_auditoria(self, linha) -> bool:
        """Abre o formulário existente pelo lápis ou olho, sem salvar.

        Clicar na linha ``<tr>`` não abre o título na tela real. A ação de
        edição, identificada pelo ícone de lápis, abre os mesmos campos usados
        na inclusão e permite apenas lê-los. Quando o lápis não está
        disponível (por exemplo, títulos com baixa informada), tenta o ícone
        de visualização (olho / ``fa-eye``).

        Após cada tentativa de clique, verifica se o formulário correto
        abriu — deve conter o campo de fornecedor **dentro de um modal de
        Inclusão/Edição/Visualização**, não em outra página. Se um popup
        inesperado abriu (como "Arquivos"), fecha-o e tenta o próximo ícone.

        Não tenta links genéricos ``<a>`` da linha porque esses podem
        navegar para páginas como detalhes de pagamento, cujos campos
        possuem os mesmos IDs ``#field_*`` mas com conteúdo diferente.
        """
        S.conferir_permitido("abrir edicao do titulo somente para leitura")

        # Coletar ícones candidatos (lápis e depois olho/visualização)
        candidatos: list = []
        for seletor in (S.ACAO_EDITAR_TITULO, S.ACAO_VISUALIZAR_TITULO):
            try:
                acoes = linha.locator(seletor)
                for i in range(min(acoes.count(), 6)):
                    el = acoes.nth(i)
                    if not el.is_visible():
                        continue
                    # Nunca clica em ícone de excluir/baixar/remeter, mesmo
                    # que um seletor passe a casar com ele por mudança na tela.
                    if self._acao_de_linha_proibida(el):
                        continue
                    candidatos.append(el)
            except Exception:
                pass

        if not candidatos:
            # Registrar todos os elementos clicáveis da linha para
            # facilitar a análise quando nenhum seletor conhecido funcionar
            try:
                todos = linha.locator(
                    "a:visible, button:visible, i.fa:visible"
                )
                descricoes = []
                for i in range(min(todos.count(), 12)):
                    el = todos.nth(i)
                    try:
                        tag = el.evaluate("e => e.tagName")
                        cls = el.evaluate("e => e.className || ''")
                        titulo_el = el.evaluate(
                            "e => e.title || e.getAttribute('title') || ''"
                        )
                        desc = f"{tag}.{cls}"
                        if titulo_el:
                            desc += f" title='{titulo_el}'"
                        descricoes.append(desc)
                    except Exception:
                        descricoes.append("?")
                self._descricao_icones_auditoria = (
                    "; ".join(descricoes) if descricoes else "[nenhum]"
                )
            except Exception:
                self._descricao_icones_auditoria = "[erro ao inspecionar]"
            return False

        url_antes = self.page.url
        for acao in candidatos:
            try:
                acao.click(timeout=5000)
                self.page.wait_for_timeout(700)

                # Se o clique navegou para outra página, não é o formulário
                # modal que esperamos — voltar e tentar o próximo ícone.
                if self.page.url != url_antes:
                    self.page.go_back(wait_until="domcontentloaded")
                    self.page.wait_for_timeout(500)
                    continue

                # Verificar se o formulário correto abriu (modal)
                campo_fornecedor = self.page.locator(
                    f"{S.CAMPO_FORNECEDOR}:visible"
                )
                if campo_fornecedor.count():
                    return True

                # Algo errado abriu (popup de arquivos, etc.) — fechar
                self._fechar_popups(tentativas=2)
                self.page.wait_for_timeout(300)
            except Exception:
                try:
                    self._fechar_popups(tentativas=1)
                except Exception:
                    pass

        return False

    def _descobrir_id(self, titulo: Titulo, documento: str) -> str:
        """Procura o ID do título recém-criado buscando pelo DOCUMENTO.

        A versão antiga filtrava a lista pelo FORNECEDOR (a lupa), rota que na
        prática quase nunca filtrava ("a tabela permaneceu com outros
        fornecedores") e devolvia sempre vazio — por isso todo título saía com
        "ID nao localizado". Esta versão usa a MESMA estratégia que a
        recuperação do modo auditar já faz com sucesso: busca pelo documento no
        campo geral "Buscar" e confere cada candidato por valor + emissão +
        vencimento. Não compara o fornecedor de propósito — o cadastro do
        Ongsys pode ter razão social diferente da planilha (ex.: Q9Z). Só
        devolve um ID quando exatamente um candidato bate; na dúvida, devolve
        vazio e o modo auditar recupera depois.
        """
        diag: dict = {
            "documento": documento,
            "fornecedor": getattr(titulo, "fornecedor", ""),
            "esperado": {
                "valor": formatar_dinheiro_br(titulo.valor),
                "emissao": titulo.emissao.strftime("%d/%m/%Y"),
                "vencimento": titulo.vencimento.strftime("%d/%m/%Y"),
            },
            "erro_busca": "",
            "erro_geral": "",
            "ids": [],
            "candidatos": [],
        }
        try:
            # A tabela atualiza o total de registros imediatamente, mas o campo
            # Buscar ainda retorna "No matching records found" para o documento
            # recém-criado. Primeiro ordenamos por ID decrescente e validamos a
            # linha sem depender desse índice atrasado.
            self.page.wait_for_timeout(800)
            recentes = self._ids_recentes_sem_indice(titulo, documento)
            diag["tentativas"] = []
            diag["tentativas"].append(
                {"rota": "ids_recentes", "espera_ms": 800, "ids_encontrados": len(recentes)}
            )
            if len(set(recentes)) == 1:
                return recentes[0]
            # Vários títulos com a MESMA assinatura (mesmo documento, valor e
            # datas) — comum em reembolsos "09_26" de R$ 1.710,00 para pessoas
            # diferentes (2026-09-24). O fornecedor é o único diferenciador:
            # entre os candidatos que já batem a assinatura, isola o desta
            # pessoa. Se isolar exatamente um, é o nosso.
            if len(set(recentes)) > 1:
                unico = self._isolar_por_fornecedor(titulo, recentes)
                if len(unico) == 1:
                    diag.setdefault("tentativas", []).append(
                        {"rota": "ids_recentes+fornecedor", "ids_encontrados": 1}
                    )
                    return unico[0]

            # Fallback único pelo documento. Não repetimos três consultas
            # lentas ao mesmo índice que acabou de demonstrar atraso.
            ids: list[str] = []
            self.page.wait_for_timeout(1200)
            try:
                ids = self.ids_resultado_busca_documento(
                    documento, titulo.vencimento, titulo.vencimento
                )
            except ErroNavegador as erro:
                ids = []
                diag["erro_busca"] = str(erro)
            diag["tentativas"].append(
                {"rota": "buscar_documento", "espera_ms": 1200, "ids_encontrados": len(ids)}
            )

            candidatos = []
            for id_linha in ids:
                texto = self.texto_linha_para_id(id_linha)
                bate = self._linha_bate_assinatura(titulo, texto)
                diag["ids"].append({"id": id_linha, "bate_assinatura": bate, "texto": (texto or "")[:250]})
                if bate:
                    candidatos.append(id_linha)
            diag["candidatos"] = list(dict.fromkeys(candidatos))

            if len(set(candidatos)) == 1:
                return candidatos[0]
            if len(set(candidatos)) > 1:
                unico = self._isolar_por_fornecedor(titulo, candidatos)
                if len(unico) == 1:
                    diag["candidato_por_fornecedor"] = unico[0]
                    return unico[0]

            # Ainda sem ID único: registra as linhas que a lista mostra AGORA
            # (após a última busca), para revelar se o título aparece ou não.
            try:
                diag["linhas_visiveis"] = self._textos_linhas_visiveis_lista(limite=40)
            except Exception as erro_linhas:
                diag["linhas_visiveis_erro"] = str(erro_linhas)
        except Exception as erro:
            diag["erro_geral"] = str(erro)

        # Não achou um ID único: grava um diagnóstico na pasta de evidências
        # com o que a lista realmente devolveu, para eu corrigir com dados reais.
        self._salvar_diagnostico_id(documento, diag)
        return ""

    def _salvar_diagnostico_id(self, documento: str, diag: dict) -> None:
        """Salva em evidencias/ o que a busca por documento devolveu (temporário)."""
        try:
            import json
            nome = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(documento))[:40]
            carimbo = f"{datetime.now():%Y%m%d_%H%M%S}"
            destino = self.pasta_evidencias / f"diag_id_{carimbo}_{nome}.json"
            destino.write_text(
                json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            try:
                self.page.screenshot(
                    path=str(self.pasta_evidencias / f"diag_id_{carimbo}_{nome}.png"),
                    full_page=False,
                )
            except Exception:
                pass
        except Exception:
            pass

    def _isolar_por_fornecedor(self, titulo: Titulo, ids: list[str]) -> list[str]:
        """Entre candidatos que já batem a assinatura, mantém os do fornecedor.

        Desempate para quando vários títulos têm mesmo documento, valor e datas
        e só o fornecedor difere. Usa a mesma correspondência tolerante da
        pré-verificação (`_linha_e_do_fornecedor`), que reconhece razão social
        diferente da planilha. Se restar exatamente um, o chamador o aceita; na
        dúvida (nenhum ou vários), devolve a lista e o título continua sem ID
        para o modo auditar recuperar — nunca chuta.
        """
        alvo = self.resolver_apelido(titulo.fornecedor)
        do_fornecedor: list[str] = []
        for id_linha in ids:
            texto = self.texto_linha_para_id(id_linha)
            if not texto:
                continue
            # Rejeita a linha de OUTRA pessoa que só compartilha sobrenomes
            # (JULIANA MOREIRA DA ROCHA x PATRICIA DA ROCHA DOS MOREIRA): a
            # correspondência frouxa (0,6) casaria as duas, mas os nomes
            # divergem. Exige o casamento positivo E a ausência de divergência.
            if self._nomes_divergem(alvo, texto):
                continue
            if self._linha_e_do_fornecedor(titulo, texto):
                do_fornecedor.append(id_linha)
        return list(dict.fromkeys(do_fornecedor))

    def _linha_bate_assinatura(self, titulo: Titulo, texto: str) -> bool:
        """True se a linha da lista confere em valor, emissão e vencimento.

        Mesma assinatura usada pela recuperação do modo auditar
        (`_motivos_linha_incompativel`): não compara fornecedor (cadastro pode
        ter outra razão social) nem documento (a coluna Dcto mostra o tipo, não
        o número). Aceita as várias grafias de data que a tabela pode exibir.
        """
        if not texto:
            return False
        if formatar_dinheiro_br(titulo.valor) not in texto:
            return False
        return all(
            self._texto_contem_data(texto, esperado)
            for esperado in (titulo.emissao, titulo.vencimento)
        )

    @staticmethod
    def _texto_contem_data(texto: str, esperado) -> bool:
        """Aceita as grafias de data observadas na lista do Ongsys."""
        texto_n = sem_acentos(texto)
        meses_en = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        meses_pt = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                    "Jul", "Ago", "Set", "Out", "Nov", "Dez")
        variantes = (
            esperado.strftime("%d/%m/%Y"),
            esperado.strftime("%d-%m-%Y"),
            esperado.strftime("%Y-%m-%d"),
            f"{esperado.day:02d}-{meses_pt[esperado.month - 1]}-{esperado.year}",
            f"{esperado.day:02d}-{meses_en[esperado.month - 1]}-{esperado.year}",
        )
        return any(sem_acentos(valor) in texto_n for valor in variantes)

    # -- evidências --------------------------------------------------------

    def screenshot_falha(self, identificador: str) -> str:
        """Captura local da tela em caso de falha (§21). Nada sai da máquina."""
        try:
            nome = re.sub(r"[^A-Za-z0-9_.-]+", "_", identificador)[:60]
            destino = self.pasta_evidencias / f"falha_{datetime.now():%Y%m%d_%H%M%S}_{nome}.png"
            self.page.screenshot(path=str(destino), full_page=False)
            return str(destino)
        except Exception:
            return ""

    def _diagnosticar_tipo_lancamento(self, identificador: str) -> str:
        """Salva o estado real do #field_tipo_lancamento na pasta de evidências.

        Serve para descobrir POR QUE o campo aparece como "não visível" no
        formulário de edição do projetado: sobe a árvore de pais e registra,
        para cada nível, o display/visibility computados e a classe/id — assim
        dá para ver exatamente qual container está escondendo o campo (aba
        inativa, seção colapsada, modal errado etc.). Nada sai da máquina.
        """
        try:
            info = self.page.evaluate("""
                (function() {
                    var sel = document.getElementById('field_tipo_lancamento');
                    if (!sel) return {erro: 'campo nao encontrado no DOM'};
                    var linhas = [];
                    var el = sel;
                    var nivel = 0;
                    while (el && el !== document.body && nivel < 15) {
                        var cs = window.getComputedStyle(el);
                        linhas.push({
                            nivel: nivel,
                            tag: el.tagName,
                            id: el.id || '',
                            classe: el.className || '',
                            display: cs.display,
                            visibility: cs.visibility,
                            opacity: cs.opacity,
                            hidden_attr: el.hasAttribute('hidden'),
                            aria_hidden: el.getAttribute('aria-hidden') || ''
                        });
                        el = el.parentElement;
                        nivel++;
                    }
                    return {
                        offsetParent_null: sel.offsetParent === null,
                        disabled: sel.disabled,
                        valor_atual: sel.value,
                        opcoes: Array.from(sel.options).map(function(o){
                            return o.text.trim() + '=' + o.value;
                        }),
                        cadeia_pais: linhas
                    };
                })()
            """)
        except Exception as erro:
            info = {"erro_captura": str(erro)}

        try:
            import json
            nome = re.sub(r"[^A-Za-z0-9_.-]+", "_", identificador)[:60]
            carimbo = f"{datetime.now():%Y%m%d_%H%M%S}"
            destino_json = self.pasta_evidencias / f"diag_{carimbo}_{nome}.json"
            destino_json.write_text(
                json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # Screenshot junto, para conferência visual da tela no mesmo instante.
            try:
                destino_png = self.pasta_evidencias / f"diag_{carimbo}_{nome}.png"
                self.page.screenshot(path=str(destino_png), full_page=False)
            except Exception:
                pass
            return str(destino_json)
        except Exception:
            return ""
