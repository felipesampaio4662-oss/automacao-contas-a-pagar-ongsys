"""Linha de comando. Os arquivos .bat só chamam os comandos daqui.

Modos disponíveis:

* `login`             abre o navegador para o login manual;
* `planejar`          lê a planilha e gera relatórios, sem tocar no Ongsys;
* `conferir-tela`     confere se os campos da tela ainda existem, sem preencher;
* `testar-fornecedor` digita fornecedores no campo de busca e mostra as
                      sugestões, sem clicar em nenhuma e sem salvar;
* `executar`          cadastra os títulos, depois de confirmação por escrito;
* `auditar`           reabre títulos já salvos e compara com a planilha;
* `estado`            mostra a situação do checkpoint.

O modo padrão, quando nada é informado, é `planejar`.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import platform
import shutil
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from . import auditoria as A
from . import configuracao, planilha, regras, relatorios
from .checkpoint import Checkpoint
from .modelos import Duplicidade, Estado, ResultadoLeitura, Titulo
from .seletores import AcaoProibida
from .normalizacao import (
    data_br,
    documento_equivalente,
    documento_permite_projetado,
    formatar_dinheiro_br,
    nomes_divergem,
    normalizar_documento,
    para_data,
    para_decimal,
    periodo_mensal,
    sem_acentos,
    semelhanca,
)

BASE = Path(__file__).resolve().parent.parent
CONFIG = BASE / "config.json"
PERFIL = BASE / "perfil_ongsys"
SAIDA = BASE / "relatorios"
EVIDENCIAS = BASE / "evidencias"
CHECKPOINT = BASE / "checkpoint.json"
BACKUPS = BASE / "backups"
HISTORICO = SAIDA / "historico_execucoes.csv"

CONFIRMACAO = "CONFIRMO"


class SaidaControlada(SystemExit):
    """Encerramento previsto, com mensagem já explicada ao usuário."""


# --------------------------------------------------------------------------
# Apoio de interface
# --------------------------------------------------------------------------

def _titulo_tela(texto: str) -> None:
    print()
    print("=" * 68)
    print(texto)
    print("=" * 68)


def _sem_sobrescrever(destino: Path) -> Path:
    """Devolve um nome livre: duas cópias no mesmo segundo não se apagam.

    Em 15/10/2026 duas liberações seguidas geraram o mesmo nome e a segunda
    cópia substituiu a primeira.
    """
    candidato = destino
    numero = 2
    while candidato.exists():
        candidato = destino.with_name(f"{destino.stem}_{numero}{destino.suffix}")
        numero += 1
    return candidato


def _backup_checkpoint(motivo: str) -> Path | None:
    """Copia o checkpoint para ``backups/`` antes de uma operação que o altera.

    O checkpoint é a memória que impede lançar um título duas vezes. Uma cópia
    datada a cada execução permite voltar ao estado anterior se algo sair do
    esperado. Nunca apaga nem substitui cópias antigas, e uma falha aqui não
    impede a operação (apenas avisa).
    """
    try:
        if not CHECKPOINT.exists():
            return None
        BACKUPS.mkdir(parents=True, exist_ok=True)
        destino = _sem_sobrescrever(
            BACKUPS / f"checkpoint_{datetime.now():%Y%m%d_%H%M%S}_{motivo}.json"
        )
        shutil.copy2(CHECKPOINT, destino)
        print(f"Copia de seguranca do checkpoint: {destino}")
        return destino
    except Exception as erro:  # nunca bloqueia a operação
        print(f"AVISO: nao foi possivel copiar o checkpoint ({erro}).")
        return None


def _sha256_arquivo(caminho: Path | None) -> str:
    try:
        return hashlib.sha256(Path(caminho).read_bytes()).hexdigest()[:16]
    except Exception:
        return ""


CAMPOS_HISTORICO = [
    "data_hora", "usuario", "computador", "comando", "planilha", "sha256_planilha",
    "periodo", "conta", "titulos", "valor_total", "resultado", "relatorio",
]


def _registrar_historico(
    comando: str, *, planilha_origem: Path | None = None, periodo: str = "",
    conta: str = "", titulos: int = 0, valor_total: str = "",
    resultado: str = "", relatorio: Path | str = "",
) -> None:
    """Acrescenta uma linha ao histórico de execuções (trilha de auditoria).

    Registra quem rodou, quando, em qual computador, com qual planilha (nome e
    impressão digital SHA-256) e o que resultou. O arquivo só cresce: nenhuma
    linha é reescrita. Uma falha aqui nunca interrompe a operação.
    """
    try:
        SAIDA.mkdir(parents=True, exist_ok=True)
        novo = not HISTORICO.exists()
        with HISTORICO.open("a", encoding="utf-8-sig", newline="") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=CAMPOS_HISTORICO, delimiter=";")
            if novo:
                escritor.writeheader()
            escritor.writerow({
                "data_hora": datetime.now().isoformat(timespec="seconds"),
                "usuario": getpass.getuser(),
                "computador": platform.node(),
                "comando": comando,
                "planilha": Path(planilha_origem).name if planilha_origem else "",
                "sha256_planilha": _sha256_arquivo(planilha_origem),
                "periodo": periodo,
                "conta": conta,
                "titulos": titulos,
                "valor_total": valor_total,
                "resultado": resultado,
                "relatorio": Path(relatorio).name if relatorio else "",
            })
    except Exception as erro:
        print(f"AVISO: nao foi possivel gravar o historico de execucoes ({erro}).")


def _mostrar_pendencias(pendencias: list[str], avisos: list[str] = ()) -> None:
    """Aponta no acompanhamento o que ficou para correção manual (§13).

    Avisos informativos aparecem separados: não exigem mexer no título.
    """
    for aviso in avisos:
        print(f"      info: {aviso}")
    if not pendencias:
        return
    print(f"      ATENCAO: {len(pendencias)} pendencia(s) para corrigir manualmente no Ongsys:")
    for pendencia in pendencias:
        print(f"        * {pendencia}")


def _mensagem_curta(texto: object, limite: int = 220) -> str:
    """Resumo legível na tela; o diagnóstico técnico integral fica no CSV."""
    bruto = str(texto).strip()
    if not bruto:
        return "sem detalhes"
    linhas = [linha.strip() for linha in bruto.splitlines() if linha.strip()]
    primeira = linhas[0]
    # Em erros do Playwright, a primeira linha diz apenas "Timeout". Inclui o
    # campo aguardado para o operador entender onde a execução parou, sem
    # despejar todo o call log no painel.
    alvo = next((linha for linha in linhas[1:] if "waiting for locator" in linha), "")
    resumo = f"{primeira} ({alvo})" if alvo else primeira
    return resumo if len(resumo) <= limite else resumo[: limite - 3] + "..."


def _limpar_caminho(texto: str) -> str:
    """Remove aspas e espaços de um caminho arrastado para a janela (§23)."""
    return texto.strip().strip('"').strip("'").strip()


def _perguntar_data(rotulo: str, padrao: date | None = None) -> date:
    sugestao = f" [{padrao.strftime('%d/%m/%Y')}]" if padrao else ""
    while True:
        resposta = input(f"{rotulo}{sugestao}: ").strip()
        if not resposta and padrao:
            return padrao
        for formato in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
            try:
                return datetime.strptime(resposta, formato).date()
            except ValueError:
                continue
        print("  Data invalida. Use o formato dia/mes/ano, por exemplo 01/06/2026.")


def _perguntar_planilha() -> Path:
    while True:
        bruto = input("Arraste a planilha para esta janela e pressione Enter:\n> ")
        caminho = Path(_limpar_caminho(bruto))
        if caminho.exists() and caminho.suffix.lower() in (".xlsx", ".xlsm"):
            return caminho
        if not caminho.exists():
            print(f"  Arquivo nao encontrado: {caminho}")
        else:
            print("  O arquivo precisa ser .xlsx. Salve a planilha nesse formato.")


def _perguntar_conta(config: dict) -> str:
    contas = config["contas_pagadoras"]
    padrao = config.get("conta_pagadora_padrao") or ""
    print("\nContas pagadoras disponiveis:")
    print("     0 = SEM CONTA PAGADORA (deixar o campo vazio)")
    for identificador, nome in sorted(contas.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
        marca = "  (padrao)" if identificador == padrao else ""
        print(f"  {identificador:>4} = {nome}{marca}")
    while True:
        # §8: a conta nunca é reaproveitada em silêncio da execução anterior.
        resposta = input("Digite o numero da conta pagadora desta execucao: ").strip()
        if resposta == "0":
            return ""
        if resposta in contas:
            return resposta
        print("  Numero invalido. Escolha uma conta ou digite 0 para deixar em branco.")


def _confirmar(mensagem: str) -> bool:
    return input(f"{mensagem} (digite SIM para continuar): ").strip().upper() == "SIM"


def _validar_modo_duplicidade_intencional(args, quantidade_fila: int) -> None:
    """Restringe a exceção de duplicidade a um único fornecedor e título."""
    if not getattr(args, "permitir_duplicidade_exata", False):
        return
    if not getattr(args, "somente_fornecedor", None):
        raise SaidaControlada(
            "\nMODO DE DUPLICIDADE INTENCIONAL RECUSADO\n\n"
            "Informe um fornecedor específico. Este modo nunca pode rodar sem filtro.\n"
        )
    if getattr(args, "limite", None) != 1 or quantidade_fila != 1:
        raise SaidaControlada(
            "\nMODO DE DUPLICIDADE INTENCIONAL RECUSADO\n\n"
            "Use limite 1 e confirme que somente um título entrou na fila.\n"
        )


def _confirmar_duplicidade_exata_intencional(args, titulo: Titulo, detalhe: str) -> bool:
    """Pede uma palavra mais forte que SIM antes de criar uma cópia de teste."""
    if not getattr(args, "permitir_duplicidade_exata", False):
        return False
    _titulo_tela("DUPLICIDADE EXATA INTENCIONAL - PILOTO")
    print(f"Fornecedor : {titulo.fornecedor}")
    print(f"Documento  : {titulo.documento}")
    print(f"Valor      : R$ {titulo.valor_br}")
    print(f"Rateios    : {len(titulo.rateios)}")
    print(f"Localizado : {detalhe}")
    print()
    print("Este modo criara uma COPIA DE TESTE com variacao do documento.")
    print("Nao dara baixa, nao pagara e nao enviara para aprovacao.")
    print("Audite todas as linhas antes de excluir a copia.")
    resposta = input("Digite DUPLICAR para autorizar esta copia de teste: ")
    return resposta.strip().upper() == "DUPLICAR"


# --------------------------------------------------------------------------
# Planejamento
# --------------------------------------------------------------------------

def _carregar_config() -> dict:
    try:
        return configuracao.carregar(CONFIG)
    except configuracao.ErroConfiguracao as erro:
        raise SaidaControlada(f"\nERRO DE CONFIGURACAO\n\n{erro}\n")


def _planejar(args, config: dict) -> tuple[ResultadoLeitura, dict[str, Path], date, date, str]:
    caminho = Path(_limpar_caminho(str(args.planilha))) if args.planilha else _perguntar_planilha()

    try:
        linhas, mapa, cabecalho, aba = planilha.ler_linhas(
            caminho, aba=args.aba, colunas_manuais=_colunas_manuais(args.coluna)
        )
    except planilha.ErroPlanilha as erro:
        raise SaidaControlada(f"\nERRO NA PLANILHA\n\n{erro}\n")

    primeiro, ultimo = planilha.periodo_das_linhas(linhas)
    inicio = args.inicio or _perguntar_data("Data inicial", primeiro)
    fim = args.fim or _perguntar_data("Data final", ultimo)
    conta_informada = args.conta if args.conta is not None else _perguntar_conta(config)
    conta = "" if conta_informada == "0" else conta_informada

    try:
        resultado = regras.montar_titulos(linhas, inicio, fim, config, conta)
    except regras.ErroRegras as erro:
        raise SaidaControlada(f"\nERRO NAS REGRAS\n\n{erro}\n")

    resultado.colunas = mapa
    resultado.linha_cabecalho = cabecalho
    try:
        resultado.planilha_origem = caminho
    except Exception:
        pass

    if args.somente_fornecedor:
        alvo = args.somente_fornecedor.upper()
        antes = len(resultado.titulos)
        resultado.titulos = [
            t for t in resultado.titulos if alvo in t.fornecedor.upper()
        ]
        print(f"\nFiltro --somente-fornecedor: {len(resultado.titulos)} de {antes} titulos.")

    SAIDA.mkdir(parents=True, exist_ok=True)
    marca = relatorios.carimbo()
    caminhos = {
        "pre_execucao": SAIDA / f"pre_execucao_{marca}.csv",
        "exclusoes": SAIDA / f"exclusoes_{marca}.csv",
    }
    relatorios.relatorio_pre_execucao(resultado, caminhos["pre_execucao"])
    relatorios.relatorio_exclusoes(resultado.exclusoes, caminhos["exclusoes"])

    xlsx = relatorios.exportar_xlsx(
        {"Titulos": caminhos["pre_execucao"], "Exclusoes": caminhos["exclusoes"]},
        SAIDA / f"conferencia_{marca}.xlsx",
    )
    if xlsx:
        caminhos["xlsx"] = xlsx

    modo_execucao = getattr(args, "comando", "") == "executar"
    print()
    print(f"Planilha: {caminho.name}  |  Aba: {aba}")
    if not modo_execucao:
        print(
            "Colunas : "
            + ", ".join(
                f"{k}={v}" for k, v in sorted(mapa.items(), key=lambda x: x[1])
            )
        )
    print()
    nome_conta = config["contas_pagadoras"].get(conta, "SEM CONTA PAGADORA")
    print(
        relatorios.resumo_texto(
            resultado,
            inicio,
            fim,
            nome_conta,
            compacto=modo_execucao,
        )
    )
    if modo_execucao:
        print(f"Conferencia completa: {caminhos.get('xlsx', caminhos['pre_execucao'])}")
    else:
        print("\nRelatorios gerados:")
        for nome, destino in caminhos.items():
            print(f"  {nome:12}: {destino}")

    return resultado, caminhos, inicio, fim, conta


def _colunas_manuais(valores: list[str] | None) -> dict[str, str]:
    manuais: dict[str, str] = {}
    for item in valores or []:
        if "=" not in item:
            raise SaidaControlada(
                f"\nERRO: --coluna precisa do formato campo=letra. Recebido: {item}\n"
            )
        campo, letra = item.split("=", 1)
        manuais[campo.strip()] = letra.strip()
    return manuais


# --------------------------------------------------------------------------
# Execução
# --------------------------------------------------------------------------

def _fila_de_execucao(resultado: ResultadoLeitura, ponto: Checkpoint,
                      config: dict, limite: int | None) -> tuple[list[Titulo], list[str]]:
    """Monta a fila e explica cada título deixado de fora."""
    fila: list[Titulo] = []
    ignorados: list[str] = []

    for titulo in resultado.titulos:
        rotulo = f"{titulo.fornecedor} / {titulo.documento} / R$ {titulo.valor_br}"
        if titulo.bloqueado:
            ignorados.append(f"{rotulo}: BLOQUEADO - {titulo.bloqueios[0]}")
            continue
        estado = ponto.estado_de(titulo)
        if estado in Estado.JA_PODE_EXISTIR:
            # §20: nada que possa já existir é recriado automaticamente.
            if ponto.dados_mudaram(titulo):
                ignorados.append(
                    f"{rotulo}: estado {estado}, mas os dados da planilha mudaram. "
                    "O titulo continua bloqueado contra novo lancamento; confira no Ongsys."
                )
            elif estado == Estado.CONCLUIDO:
                ignorados.append(
                    f"{rotulo}: ja concluido e auditado em execucao anterior."
                )
            else:
                ignorados.append(
                    f"{rotulo}: estado {estado}. Pode existir no Ongsys. "
                    "Use o modo auditar antes de tentar de novo."
                )
            continue
        if ponto.dados_mudaram(titulo) and estado != Estado.FALHA:
            ignorados.append(
                f"{rotulo}: os dados da planilha mudaram desde a ultima execucao. "
                "Confira manualmente antes de lancar."
            )
            continue
        fila.append(titulo)

    maximo = limite or config["limite_titulos_por_execucao"]
    if len(fila) > maximo:
        ignorados.append(
            f"Limite de {maximo} titulos por execucao atingido; "
            f"{len(fila) - maximo} ficaram para a proxima rodada."
        )
        fila = fila[:maximo]

    return fila, ignorados


def _executar(args, config: dict) -> None:
    from .navegador import ErroFatal, ErroNavegador, OngsysBot

    resultado, _, _, _, _ = _planejar(args, config)
    ponto = Checkpoint(CHECKPOINT)

    pendentes_de_conferencia = ponto.precisam_de_conferencia()
    if pendentes_de_conferencia:
        _titulo_tela("PENDENCIAS DE EXECUCOES ANTERIORES")
        # Separar os dois casos: "não sei se existe" pede auditoria/Conferir
        # ID; "existe, mas com diferença" pede revisão no Ongsys. Juntos, o
        # número (160 em 18/09/2026) não dizia o que fazer.
        sem_id = ponto.sem_confirmacao_no_ongsys()
        com_divergencia = ponto.salvos_com_divergencia()
        print(f"Existem {len(pendentes_de_conferencia)} titulo(s) de execucoes anteriores a revisar:")
        print(
            f"  sem ID confirmado no Ongsys : {len(sem_id)}  "
            "-> rode Auditar ou Conferir ID"
        )
        print(
            f"  salvos com divergencia      : {len(com_divergencia)}  "
            "-> revise no Ongsys e marque como conferido (aba Pendencias do painel)"
        )
        print("Eles nao serao recriados automaticamente.")
        if not _confirmar("Deseja continuar mesmo assim?"):
            raise SaidaControlada("\nExecucao cancelada.\n")

    fila, ignorados = _fila_de_execucao(resultado, ponto, config, args.limite)

    if ignorados:
        _titulo_tela("ITENS FORA DESTA FILA")
        grupos = {
            "ja concluidos": 0,
            "podem existir no Ongsys": 0,
            "bloqueados": 0,
            "dados alterados": 0,
            "outros": 0,
        }
        importantes: list[str] = []
        for motivo in ignorados:
            if "ja concluido e auditado" in motivo:
                grupos["ja concluidos"] += 1
            elif "Pode existir no Ongsys" in motivo:
                grupos["podem existir no Ongsys"] += 1
            elif "BLOQUEADO" in motivo:
                grupos["bloqueados"] += 1
                importantes.append(motivo)
            elif "dados da planilha mudaram" in motivo:
                grupos["dados alterados"] += 1
                importantes.append(motivo)
            else:
                grupos["outros"] += 1
                importantes.append(motivo)
        print("  " + " | ".join(
            f"{rotulo}: {quantidade}"
            for rotulo, quantidade in grupos.items() if quantidade
        ))
        for motivo in importantes:
            print(f"  ! {motivo}")

    if not fila:
        raise SaidaControlada("\nNao ha nenhum titulo liberado para lancar.\n")

    _validar_modo_duplicidade_intencional(args, len(fila))

    total = sum((t.valor for t in fila), Decimal("0"))
    if total > config["limite_valor_total"]:
        raise SaidaControlada(
            f"\nERRO DE SEGURANCA\n\nO total da fila (R$ {relatorios.formatar_dinheiro_br(total)}) "
            f"passa do limite configurado (R$ "
            f"{relatorios.formatar_dinheiro_br(config['limite_valor_total'])}).\n"
            "Reduza o periodo, use --limite, ou ajuste limite_valor_total no config.json.\n"
        )

    _titulo_tela("FILA DE LANCAMENTO")
    for numero, titulo in enumerate(fila, start=1):
        print(f"  {numero:>3}. {titulo.efetivacao.strftime('%d/%m/%Y')} "
              f"{titulo.fornecedor[:38]:40} {titulo.documento[:18]:20} "
              f"R$ {titulo.valor_br:>13}  ({len(titulo.rateios)} rateio(s))")
    print()
    print(f"Total: {len(fila)} titulo(s) | R$ {relatorios.formatar_dinheiro_br(total)}")
    print(f"Conta: {fila[0].conta_pagadora_nome or 'SEM CONTA PAGADORA'}")
    print("Acao : criar/atualizar Contas a Pagar; sem baixa, pagamento ou aprovacao.")
    print()
    if input(f"Digite {CONFIRMACAO} para continuar: ").strip() != CONFIRMACAO:
        raise SaidaControlada("\nExecucao cancelada. Nada foi enviado ao Ongsys.\n")

    _backup_checkpoint("antes_executar")
    registros: list[dict] = []
    linhas_auditoria: list[dict] = []
    bot = OngsysBot(config, PERFIL, EVIDENCIAS)

    try:
        bot.abrir()
        bot.exigir_sessao()

        for numero, titulo in enumerate(fila, start=1):
            print(f"\n[{numero}/{len(fila)}] {titulo.fornecedor} - {titulo.documento}")
            registro = {
                "situacao": "FALHA",
                "fornecedor": titulo.fornecedor,
                "documento_original": titulo.documento_original,
                "documento_final": titulo.documento,
                "id_ongsys": "",
                "valor": titulo.valor_br,
                "inicio": datetime.now().isoformat(timespec="seconds"),
                "fim": "",
                "tentativas": 1,
                "fornecedor_selecionado": "",
                "campos_nao_localizados": "",
                "avisos": "",
                "mensagem": "",
                "screenshot": "",
            }

            classificacao, detalhe = bot.pesquisar_duplicidade(titulo)
            titulo.duplicidade = classificacao
            if classificacao == Duplicidade.NOVO:
                print("      verificacao: nenhum titulo equivalente localizado.")
            elif classificacao == Duplicidade.EXATO_JA_EXISTE:
                print("      verificacao: titulo equivalente ja existe.")
            else:
                print(f"      verificacao: {classificacao}.")
            duplicidade_intencional = False

            if classificacao == Duplicidade.EXATO_JA_EXISTE:
                if not getattr(args, "permitir_duplicidade_exata", False):
                    ponto.marcar_estado(titulo, Estado.DUPLICIDADE_CONFIRMADA, detalhe)
                    registro.update(situacao="JA_EXISTE", mensagem=detalhe,
                                    fim=datetime.now().isoformat(timespec="seconds"))
                    registros.append(registro)
                    continue
                if not _confirmar_duplicidade_exata_intencional(args, titulo, detalhe):
                    registro.update(
                        situacao="ADIADO",
                        mensagem="Copia de teste cancelada; checkpoint mantido.",
                        fim=datetime.now().isoformat(timespec="seconds"),
                    )
                    registros.append(registro)
                    continue
                duplicidade_intencional = True
                print("      copia de teste autorizada somente para este titulo.")

            if classificacao in (Duplicidade.POSSIVEL_DUPLICIDADE, Duplicidade.NAO_VERIFICADO):
                if not config.get("confirmar_duplicidade_por_titulo", True):
                    mensagem = (
                        f"{detalhe} Verificacao inconclusiva; titulo adiado por seguranca."
                    )
                    ponto.marcar_estado(titulo, Estado.PENDENTE_DADOS, mensagem)
                    registro.update(
                        situacao="ADIADO",
                        mensagem=mensagem,
                        fim=datetime.now().isoformat(timespec="seconds"),
                    )
                    registros.append(registro)
                    print("      ADIADO: verificacao de duplicidade inconclusiva.")
                    continue
                if not _confirmar("      Verificacao inconclusiva. Lancar este titulo mesmo assim?"):
                    ponto.marcar_estado(titulo, Estado.PENDENTE_DADOS, detalhe)
                    registro.update(situacao="ADIADO", mensagem=detalhe,
                                    fim=datetime.now().isoformat(timespec="seconds"))
                    registros.append(registro)
                    continue

            # Antes de incluir um título novo, verifica se já existe um
            # lançamento "Projetado" para este fornecedor+valor. Se existir,
            # abre para edição, sobrescreve os dados e muda para Real.
            #
            # Só documentos de nota fiscal ("NF 45", "FATURA 79055") disputam um
            # projetado. Documentos de período/data ("09_26" de salários,
            # "01.07_26" de cartão) são recorrentes e seguem o lançamento
            # normal, sem procurar nem editar projetado.
            linha_projetado = None
            procurar_projetado = documento_permite_projetado(titulo.documento)
            if not procurar_projetado:
                print(
                    f"      documento '{titulo.documento}' nao e de nota fiscal: "
                    "lancamento normal, sem procurar projetado."
                )
            try:
                if procurar_projetado:
                    linha_projetado = bot.buscar_projetado(titulo)
            except ErroNavegador as erro:
                mensagem = f"Busca de projetado inconclusiva: {erro}"
                ponto.marcar_estado(titulo, Estado.PENDENTE_DADOS, mensagem)
                registro.update(
                    situacao="ADIADO",
                    mensagem=mensagem,
                    fim=datetime.now().isoformat(timespec="seconds"),
                )
                registros.append(registro)
                print(f"      ADIADO: {_mensagem_curta(mensagem)}")
                continue
            if linha_projetado is not None:
                aviso_projetado = getattr(bot, "ultimo_aviso_projetado", "")
                if aviso_projetado:
                    print(f"      aviso: {aviso_projetado}")
                if not config.get("editar_projetados_automaticamente", False):
                    mensagem = (
                        "Projetado correspondente localizado. A conversao automatica para Real "
                        "esta desativada ate validacao em piloto controlado."
                    )
                    ponto.marcar_estado(titulo, Estado.PENDENTE_DADOS, mensagem)
                    registro.update(
                        situacao="ADIADO",
                        mensagem=mensagem,
                        fim=datetime.now().isoformat(timespec="seconds"),
                    )
                    registros.append(registro)
                    print("      ADIADO: projetado localizado; conversao automatica desativada.")
                    continue
                print("      projetado encontrado — abrindo para edicao...")
                ponto.iniciar(titulo)
                try:
                    inclusao = bot.editar_projetado(titulo, linha_projetado)
                    if aviso_projetado:
                        # Registra no CSV qual projetado foi escolhido e por quê.
                        inclusao.avisos.insert(0, aviso_projetado)
                    ponto.marcar_salvo(titulo, inclusao.id_ongsys, inclusao.documento_final)
                    registro.update(
                        situacao="SALVO",
                        documento_final=inclusao.documento_final,
                        id_ongsys=inclusao.id_ongsys,
                        fornecedor_selecionado=inclusao.fornecedor_selecionado,
                        campos_nao_localizados=" | ".join(inclusao.pendencias),
                        avisos=" | ".join(inclusao.avisos),
                        mensagem="Projetado convertido para Real.",
                    )
                    print(f"      projetado salvo como Real. ID {inclusao.id_ongsys or 'nao localizado'}")
                    _mostrar_pendencias(inclusao.pendencias, inclusao.avisos)

                    divergencias = _auditar_um(bot, titulo, inclusao.id_ongsys, config)
                    linhas_auditoria.extend(
                        A.linhas_de_relatorio(titulo, inclusao.id_ongsys, divergencias)
                    )
                    ponto.marcar_concluido(titulo, [d.campo for d in divergencias])
                    if divergencias:
                        registro["situacao"] = "SALVO_COM_DIVERGENCIA"
                        print(f"      auditoria encontrou {len(divergencias)} divergencia(s).")
                    else:
                        registro["situacao"] = "CONCLUIDO"
                        print("      auditoria conferida.")
                except (ErroFatal, AcaoProibida) as erro:
                    registro["mensagem"] = str(erro)
                    registro["screenshot"] = bot.screenshot_falha(titulo.chave)
                    _registrar_erro_no_checkpoint(ponto, titulo, bot, str(erro))
                    registros.append(registro)
                    print(f"\nEXECUCAO INTERROMPIDA: {_mensagem_curta(erro)}")
                    break
                except (ErroNavegador, Exception) as erro:
                    registro["mensagem"] = str(erro)
                    registro["screenshot"] = bot.screenshot_falha(titulo.chave)
                    _registrar_erro_no_checkpoint(ponto, titulo, bot, str(erro))
                    print(f"      FALHA ao editar projetado: {_mensagem_curta(erro)}")
                registro["fim"] = datetime.now().isoformat(timespec="seconds")
                registros.append(registro)
                continue

            if procurar_projetado:
                # Combinado com o usuário: ausência de projetado nunca adia o
                # título — segue o lançamento normal, e isso fica visível no
                # acompanhamento para não parecer que a etapa foi ignorada.
                print(
                    "      nenhum projetado correspondente: seguindo com lancamento normal."
                )

            # Quando a pré-verificação detectou documento repetido em outro
            # título, pré-incrementa o documento ANTES de abrir o formulário.
            # Isso evita o aviso assíncrono do Ongsys, que pode fechar o
            # formulário inteiro ao tentar resolver a duplicidade em tempo real.
            documento_original = titulo.documento
            documentos_ja_tentados: set[str] = set()
            # O aviso de duplicado do Ongsys vale para qualquer data; a
            # pré-verificação só olha o dia do vencimento. Consulta o ano todo
            # para escolher um número LIVRE antes de abrir o formulário.
            try:
                ocupados = bot.documentos_ja_usados(titulo)
            except Exception as erro:
                ocupados = set()
                print(
                    "      aviso: nao foi possivel listar os documentos ja usados "
                    f"({_mensagem_curta(erro, limite=120)}); seguindo como antes."
                )
            if ocupados:
                print(
                    "      documentos ja usados por este fornecedor: "
                    + ", ".join(sorted(ocupados))
                )
            if (
                classificacao == Duplicidade.DOCUMENTO_REPETIDO_OUTRO_TITULO
                or titulo.documento in ocupados
            ):
                novo_doc = bot.proximo_documento(
                    titulo, documentos_ja_tentados | ocupados
                )
                documentos_ja_tentados.add(novo_doc)
                print(f"      documento pre-incrementado para '{novo_doc}' (evita aviso assincrono)")
                titulo.documento = novo_doc

            ponto.iniciar(titulo)
            try:
                # Nunca reabre o formulário depois de um resultado incerto.
                # O próprio navegador só varia o documento enquanto um aviso
                # explícito de duplicidade comprova que o salvamento não ocorreu.
                # Se o formulário fechar sem confirmação, a exceção chega ao
                # checkpoint e o título permanece EM_EXECUCAO até auditoria.
                inclusao = bot.incluir(titulo)

                titulo.documento = documento_original  # restaura para o checkpoint
                ponto.marcar_salvo(titulo, inclusao.id_ongsys, inclusao.documento_final)
                registro.update(
                    situacao="SALVO",
                    documento_final=inclusao.documento_final,
                    id_ongsys=inclusao.id_ongsys,
                    fornecedor_selecionado=inclusao.fornecedor_selecionado,
                    campos_nao_localizados=" | ".join(inclusao.pendencias),
                    avisos=" | ".join(inclusao.avisos),
                    mensagem=(
                        "Titulo criado sem baixa. DUPLICIDADE INTENCIONAL PARA TESTE."
                        if duplicidade_intencional else "Titulo criado sem baixa."
                    ),
                )
                print(f"      salvo. ID {inclusao.id_ongsys or 'nao localizado'}")
                _mostrar_pendencias(inclusao.pendencias, inclusao.avisos)

                divergencias = _auditar_um(bot, titulo, inclusao.id_ongsys, config)
                linhas_auditoria.extend(
                    A.linhas_de_relatorio(titulo, inclusao.id_ongsys, divergencias)
                )
                ponto.marcar_concluido(titulo, [d.campo for d in divergencias])
                if divergencias:
                    registro["situacao"] = "SALVO_COM_DIVERGENCIA"
                    print(f"      auditoria encontrou {len(divergencias)} divergencia(s).")
                else:
                    registro["situacao"] = "CONCLUIDO"
                    print("      auditoria conferida.")

            except (ErroFatal, AcaoProibida) as erro:
                registro["mensagem"] = str(erro)
                registro["screenshot"] = bot.screenshot_falha(titulo.chave)
                _registrar_erro_no_checkpoint(ponto, titulo, bot, str(erro))
                registros.append(registro)
                print(f"\nEXECUCAO INTERROMPIDA: {_mensagem_curta(erro)}")
                break
            except ErroNavegador as erro:
                registro["mensagem"] = str(erro)
                registro["screenshot"] = bot.screenshot_falha(titulo.chave)
                _registrar_erro_no_checkpoint(ponto, titulo, bot, str(erro))
                print(f"      FALHA: {_mensagem_curta(erro)}")
            except Exception as erro:  # falha inesperada: registra e segue
                registro["mensagem"] = f"Erro inesperado: {erro}"
                registro["screenshot"] = bot.screenshot_falha(titulo.chave)
                _registrar_erro_no_checkpoint(ponto, titulo, bot, str(erro))
                print(f"      FALHA INESPERADA: {_mensagem_curta(erro)}")

            registro["fim"] = datetime.now().isoformat(timespec="seconds")
            registros.append(registro)
    finally:
        bot.fechar()

    marca = relatorios.carimbo()
    caminho_resultado = relatorios.relatorio_resultado(registros, SAIDA / f"resultado_{marca}.csv")
    caminho_auditoria = relatorios.relatorio_auditoria(linhas_auditoria, SAIDA / f"auditoria_{marca}.csv")
    _titulo_tela("EXECUCAO ENCERRADA")
    por_situacao: dict[str, int] = {}
    for registro in registros:
        situacao = registro.get("situacao", "SEM_RESULTADO")
        por_situacao[situacao] = por_situacao.get(situacao, 0) + 1
    if por_situacao:
        print("Rodada: " + " | ".join(
            f"{situacao}: {quantidade}"
            for situacao, quantidade in sorted(por_situacao.items())
        ))

    # O acompanhamento item a item pode rolar para fora da tela ou ser
    # truncado pelo painel. Repete no encerramento todos os itens que não
    # terminaram e o motivo principal; assim uma falha como BOLETO.14 nunca
    # desaparece do histórico visível da rodada.
    nao_concluidos = [
        registro for registro in registros
        if registro.get("situacao") not in {
            "CONCLUIDO", "SALVO", "SALVO_COM_DIVERGENCIA", "JA_EXISTE"
        }
    ]
    if nao_concluidos:
        print("\nItens NAO concluidos nesta rodada:")
        for registro in nao_concluidos:
            motivo = _mensagem_curta(
                registro.get("mensagem") or "motivo nao registrado", limite=500
            )
            documento = registro.get("documento_final") or registro.get("documento_original")
            print(
                f"  - {registro.get('fornecedor', '[fornecedor]')} / "
                f"{documento or '[documento]'}: "
                f"{registro.get('situacao', 'SEM_RESULTADO')} — {motivo}"
            )

    # Campos que a automação deixou vazios ou escolheu por semelhança. O
    # título foi salvo, mas o usuário precisa corrigir à mão (regra §13).
    com_pendencia = [
        registro for registro in registros
        if registro.get("campos_nao_localizados")
    ]
    if com_pendencia:
        print("\nCORRIGIR MANUALMENTE NO ONGSYS (titulos salvos com pendencia):")
        for registro in com_pendencia:
            documento = registro.get("documento_final") or registro.get("documento_original")
            print(
                f"  - ID {registro.get('id_ongsys') or 'nao localizado'} | "
                f"{registro.get('fornecedor', '[fornecedor]')} / {documento or '[documento]'}"
            )
            for pendencia in str(registro["campos_nao_localizados"]).split(" | "):
                print(f"      * {pendencia}")

    com_divergencia = [
        registro for registro in registros
        if registro.get("situacao") == "SALVO_COM_DIVERGENCIA"
    ]
    if com_divergencia:
        print(
            f"\nCriados com alerta de auditoria: {len(com_divergencia)}. "
            "Os titulos existem; confira o relatorio de auditoria e NAO os lance novamente."
        )
    print("Checkpoint: " + " | ".join(
        f"{estado}: {quantidade}"
        for estado, quantidade in sorted(ponto.contagem_por_estado().items())
    ))
    print(f"\nResultado detalhado: {caminho_resultado}")
    print(f"Auditoria detalhada: {caminho_auditoria}")
    _registrar_historico(
        "executar",
        planilha_origem=getattr(resultado, "planilha_origem", None),
        conta=fila[0].conta_pagadora_nome or "SEM CONTA PAGADORA",
        titulos=len(fila),
        valor_total=relatorios.formatar_dinheiro_br(total),
        resultado=" | ".join(f"{k}: {v}" for k, v in sorted(por_situacao.items())),
        relatorio=caminho_resultado,
    )


def _registrar_erro_no_checkpoint(ponto: Checkpoint, titulo: Titulo, bot, erro: str) -> None:
    """Nunca libera retentativa quando o botão Salvar já foi clicado."""
    if getattr(bot, "salvar_foi_clicado", False):
        ponto.marcar_incerto_apos_salvar(titulo, erro)
    else:
        ponto.marcar_falha(titulo, erro)


def _auditar_um(bot, titulo: Titulo, id_ongsys: str, config: dict) -> list[A.Divergencia]:
    """Reabre o título salvo e compara com o planejado (§19)."""
    if not id_ongsys:
        return [A.Divergencia(
            "ID do titulo", "um ID numerico", "[nao localizado]",
            "Localizar o titulo manualmente no Ongsys e conferir os campos.",
        )]
    lido = A.ler_titulo_da_tela(
        bot, id_ongsys, titulo.vencimento, titulo.vencimento, titulo.fornecedor
    )
    if lido is None:
        return [A.Divergencia(
            "Reabertura do titulo", f"titulo {id_ongsys} acessivel", "[nao foi possivel abrir]",
            "Conferir manualmente. NAO lancar de novo: o titulo pode existir.",
        )]
    return A.auditar_dados(
        titulo, lido, config["tolerancia_conciliacao"],
        apelidos_atividades=config.get("apelidos_atividades"),
        fornecedor_esperado=bot.resolver_apelido(titulo.fornecedor),
        fornecedores_esperados=bot.fornecedores_esperados_na_tela(titulo),
    )


# --------------------------------------------------------------------------
# Modos auxiliares
# --------------------------------------------------------------------------

def _abrir_formulario_inclusao(bot) -> None:
    """Abre a tela de "Incluir conta a pagar", sem preencher nada."""
    from . import seletores as S

    bot.page.locator(S.BOTAO_INCLUIR).first.click()
    bot.page.wait_for_timeout(600)
    if bot.page.locator(S.MENU_INCLUIR_CONTAS_PAGAR).count():
        bot.page.locator(S.MENU_INCLUIR_CONTAS_PAGAR).first.click()
    bot.page.locator(S.CAMPO_FORNECEDOR).first.wait_for(state="visible")


def _conferir_tela(config: dict) -> None:
    """Confere se os seletores ainda existem, sem preencher nada."""
    from . import seletores as S
    from .navegador import OngsysBot

    bot = OngsysBot(config, PERFIL, EVIDENCIAS)
    try:
        bot.abrir()
        bot.exigir_sessao()
        _titulo_tela("CONFERENCIA DA TELA (nada e preenchido)")

        busca_lista = bot.page.locator(S.CAMPO_BUSCA_LISTA).count() > 0
        print(f"  {'OK  ' if busca_lista else 'FALTA'} Busca da lista (para checar duplicidade)  "
              f"{S.CAMPO_BUSCA_LISTA}")

        _abrir_formulario_inclusao(bot)

        problemas = 0 if busca_lista else 1
        for nome, seletor in S.CAMPOS_ESSENCIAIS.items():
            existe = bot.page.locator(seletor).count() > 0
            print(f"  {'OK  ' if existe else 'FALTA'} {nome:24} {seletor}")
            problemas += 0 if existe else 1

        # Campos da primeira linha de rateio (§12). Já vêm na tela por padrão,
        # sem precisar clicar em "Incluir Linha" — nada é preenchido aqui.
        print("\n  Primeira linha de rateio:")
        campos_rateio = {
            "Categoria/Conta": S.categoria_conta(1),
            "Projeto/Centro de Custos": S.projeto_cc(1),
            "Subprojeto": S.subprojeto(1),
            "Atividade": S.atividade(1),
            "Valor do rateio": S.valor_rateio(1),
            "Percentual do rateio": S.percentual_rateio(1),
            "Botão incluir linha": S.botao_incluir_linha(1),
        }
        for nome, seletor in campos_rateio.items():
            existe = bot.page.locator(seletor).count() > 0
            print(f"  {'OK  ' if existe else 'FALTA'} {nome:24} {seletor}")
            problemas += 0 if existe else 1

        for rotulo, seletor, chave in (
            ("Tipo de despesa", S.CAMPO_TIPO_DESPESA, "tipos_despesa"),
            ("Conta pagadora", S.CAMPO_CONTA_PAGADORA, "contas_pagadoras"),
        ):
            if bot.page.locator(seletor).count():
                opcoes = [t.strip() for t in bot.page.locator(f"{seletor} option").all_inner_texts()]
                print(f"\n  Opcoes de {rotulo}:")
                for opcao in opcoes:
                    if opcao:
                        print(f"      {opcao}")
                print(f"  Configurado em {chave}: {config[chave]}")

        print(f"\n{'Tela compativel.' if not problemas else f'{problemas} campo(s) nao encontrado(s).'}")
        print("\nNAO verificado por este modo (exige digitar em campos de busca):")
        print("  - autocomplete de fornecedor, categoria, centro de custo, subprojeto e atividade;")
        print("  - mensagem de documento duplicado;")
        print("  - popup de fornecedor nao parametrizado.")
        input("\nPressione Enter para fechar o navegador (nada sera salvo)... ")
    finally:
        bot.fechar()


def _testar_fornecedor(args, config: dict) -> None:
    """Digita fornecedores no campo de busca e mostra as sugestões vistas.

    Nenhuma sugestão é clicada e o formulário nunca é salvo (§10). Serve para
    validar o autocomplete de fornecedor antes de qualquer lançamento real.
    """
    from .navegador import OngsysBot

    nomes = list(dict.fromkeys(args.nome or []))
    if not nomes:
        # _planejar já pergunta a planilha interativamente quando ela não vem
        # por linha de comando (--nome também não foi informado aqui).
        resultado, _, _, _, _ = _planejar(args, config)
        nomes = list(dict.fromkeys(t.fornecedor for t in resultado.titulos))
        limite = 20
        if len(nomes) > limite:
            print(f"\n{len(nomes)} fornecedores encontrados; testando os primeiros {limite}.")
            nomes = nomes[:limite]

    if not nomes:
        raise SaidaControlada("\nNenhum fornecedor para testar.\n")

    minimo = config["semelhanca_minima_fornecedor"]
    bot = OngsysBot(config, PERFIL, EVIDENCIAS)
    try:
        bot.abrir()
        bot.exigir_sessao()
        _titulo_tela(f"TESTE DE BUSCA DE FORNECEDOR ({len(nomes)} nome(s))")
        print("Nenhuma sugestao sera clicada e nada sera salvo.")

        sem_correspondencia: list[str] = []
        com_falha: list[str] = []
        for nome in nomes:
            print(f"\n  '{nome}'")
            try:
                # Reabre o formulario do zero a cada fornecedor: um popup ou
                # campo escondido deixado pela busca anterior nao contamina a
                # proxima, e uma falha isolada nao derruba o teste inteiro.
                bot.page.goto(config["ongsys_url"], wait_until="domcontentloaded")
                _abrir_formulario_inclusao(bot)
                buscas = bot.testar_fornecedor(nome)
            except Exception as erro:
                print(f"    FALHA ao testar este fornecedor: {erro}")
                com_falha.append(nome)
                continue

            for resultado_busca in buscas:
                candidatos = resultado_busca["candidatos"]
                if not candidatos:
                    print(f"    busca '{resultado_busca['consulta']}': nenhum resultado")
                    continue
                print(f"    busca '{resultado_busca['consulta']}':")
                for candidato in candidatos[:5]:
                    marca = " <- selecionaria" if (
                        resultado_busca["selecionaria"] and candidato == candidatos[0]
                    ) else ""
                    print(f"        semelhanca {candidato['semelhanca']:>7}  {candidato['texto']}{marca}")
            if not any(r["selecionaria"] for r in buscas):
                sem_correspondencia.append(nome)

        if sem_correspondencia:
            print(f"\nFornecedores que NAO seriam selecionados automaticamente "
                  f"(semelhanca minima exigida: {minimo}):")
            for nome in sem_correspondencia:
                print(f"  - {nome}")
            print('Considere cadastrar um apelido em "apelidos_fornecedores" no config.json.')
        elif not com_falha:
            print("\nTodos os fornecedores testados teriam uma opcao selecionavel.")

        if com_falha:
            print(f"\nNao foi possivel testar {len(com_falha)} fornecedor(es) por erro tecnico "
                  "(isso nao quer dizer que o fornecedor nao existe no Ongsys):")
            for nome in com_falha:
                print(f"  - {nome}")
    finally:
        bot.fechar()
    print("\nNenhuma opcao foi clicada e nada foi salvo no Ongsys.")


def _ids_conhecidos_para_auditoria(
    ponto: Checkpoint, por_chave: dict[str, Titulo]
) -> list[str]:
    """Respeita o recorte da planilha e o filtro --somente-fornecedor."""
    return [
        item.id_ongsys
        for item in ponto.itens.values()
        if item.id_ongsys and item.chave in por_chave
    ]


def _auditar(args, config: dict) -> None:
    from .navegador import OngsysBot

    ponto = Checkpoint(CHECKPOINT)
    print("\nInforme a planilha de origem para a auditoria ter os valores esperados.")
    resultado, _, inicio, fim, _ = _planejar(args, config)

    # 1) o caminho normal: o checkpoint liga o ID ao título planejado.
    por_chave = {t.chave: t for t in resultado.titulos}
    por_id: dict[str, Titulo] = {}
    for item in ponto.itens.values():
        if item.id_ongsys and item.chave in por_chave:
            por_id[item.id_ongsys] = por_chave[item.chave]

    # 2) títulos criados antes deste programa existir (§26) não estão no
    #    checkpoint. Para eles a ligação é feita depois, pelo documento lido
    #    da tela, em _titulo_correspondente.
    por_documento: dict[str, list[Titulo]] = {}
    for titulo in resultado.titulos:
        por_documento.setdefault(normalizar_documento(titulo.documento), []).append(titulo)

    alvos = list(
        args.auditar_id
        or _ids_conhecidos_para_auditoria(ponto, por_chave)
    )
    recuperaveis = [
        item for item in ponto.itens.values()
        if not item.id_ongsys
        and item.chave in por_chave
        and item.estado in (Estado.EM_EXECUCAO, Estado.FALHA, Estado.SALVO_NAO_AUDITADO)
        and item.documento_final  # nunca salvou → nada a recuperar
    ]
    if not alvos and not recuperaveis:
        raise SaidaControlada(
            "\nNao ha titulo registrado para auditar.\n"
            "Use auditar.bat depois de uma execucao, ou informe --auditar-id 00033191.\n"
        )

    _backup_checkpoint("antes_auditar")
    linhas: list[dict] = []
    bot = OngsysBot(config, PERFIL, EVIDENCIAS)
    try:
        bot.abrir()
        bot.exigir_sessao()

        # Recupera o caso em que o Ongsys confirmou o salvamento, mas o
        # programa caiu antes de gravar o ID no checkpoint. É somente leitura
        # no Ongsys: filtra o fornecedor, abre candidatos e exige uma única
        # assinatura compatível antes de proteger o item como salvo.
        if not args.auditar_id:
            recuperados = _recuperar_ids_desconhecidos(
                bot, ponto, recuperaveis, por_chave, config
            )
            alvos.extend(recuperados)
            for id_ongsys in recuperados:
                item = next((x for x in ponto.itens.values() if x.id_ongsys == id_ongsys), None)
                if item and item.chave in por_chave:
                    por_id[id_ongsys] = por_chave[item.chave]

        alvos = list(dict.fromkeys(x for x in alvos if x))
        if not alvos:
            raise SaidaControlada(
                "\nNenhum ID foi localizado com seguranca. O checkpoint continua bloqueado.\n"
                "NAO execute novamente. Envie esta tela para analise.\n"
            )

        _titulo_tela(f"AUDITORIA DE {len(alvos)} TITULO(S)")
        for id_ongsys in alvos:
            print(f"\n  Titulo {id_ongsys}...")
            titulo_previsto = por_id.get(id_ongsys)
            lido = A.ler_titulo_da_tela(
                bot,
                id_ongsys,
                inicio,
                fim,
                titulo_previsto.fornecedor if titulo_previsto else "",
            )
            if lido is None:
                print("    nao foi possivel abrir.")
                linhas.append({
                    "situacao": "NAO_ENCONTRADO", "id_ongsys": id_ongsys,
                    "fornecedor": "", "documento": "", "campo": "abertura",
                    "esperado": "titulo acessivel", "encontrado": "nao localizado",
                    "confere": "NAO", "acao_recomendada": "Conferir manualmente no Ongsys.",
                })
                continue
            titulo = titulo_previsto or _titulo_correspondente(
                lido,
                por_documento,
                getattr(bot, "resolver_apelido", None),
                getattr(bot, "fornecedores_esperados_na_tela", None),
            )
            if titulo is None:
                print("    lido, mas sem correspondencia na planilha informada.")
                linhas.append({
                    "situacao": "SEM_REFERENCIA", "id_ongsys": id_ongsys,
                    "fornecedor": lido.get("fornecedor", ""), "documento": lido.get("documento", ""),
                    "campo": "planilha de origem", "esperado": "linha correspondente na planilha",
                    "encontrado": f"documento {lido.get('documento', '') or '[vazio]'}",
                    "confere": "NAO",
                    "acao_recomendada": "Conferir se a planilha e o periodo informados cobrem este titulo.",
                })
                continue
            divergencias = A.auditar_dados(
                titulo, lido, config["tolerancia_conciliacao"],
                apelidos_atividades=config.get("apelidos_atividades"),
                fornecedor_esperado=bot.resolver_apelido(titulo.fornecedor),
                fornecedores_esperados=bot.fornecedores_esperados_na_tela(titulo),
            )
            linhas.extend(A.linhas_de_relatorio(titulo, id_ongsys, divergencias))
            ponto.marcar_salvo(titulo, id_ongsys, lido.get("documento", titulo.documento))
            ponto.marcar_concluido(titulo, [d.campo for d in divergencias])
            print(f"    {len(divergencias)} divergencia(s)." if divergencias else "    conferido.")
    finally:
        bot.fechar()

    destino = relatorios.relatorio_auditoria(linhas, SAIDA / f"auditoria_{relatorios.carimbo()}.csv")
    print(f"\nRelatorio de auditoria: {destino}")
    _registrar_historico(
        "auditar",
        planilha_origem=getattr(resultado, "planilha_origem", None),
        titulos=len(alvos),
        resultado=f"linhas de auditoria: {len(linhas)}",
        relatorio=destino,
    )


def _lido_corresponde_ao_titulo(titulo: Titulo, lido: dict, tolerancia: Decimal) -> bool:
    """Assinatura mínima e rígida para recuperar um ID sem adivinhar."""
    return not _motivos_titulo_incompativel(titulo, lido, tolerancia)


def _motivos_titulo_incompativel(
    titulo: Titulo,
    lido: dict,
    tolerancia: Decimal,
    documento_final: str = "",
) -> list[str]:
    """Explica por que um candidato não pode receber o checkpoint."""
    motivos: list[str] = []
    fornecedores_validos = [titulo.fornecedor]
    if getattr(titulo, "conta_cartao", False) or "CARTAO" in sem_acentos(titulo.conta_pagadora_nome):
        fornecedores_validos.extend(
            r.categoria_detalhada for r in titulo.rateios if r.categoria_detalhada
        )
    # A semelhança sozinha (>=0,6) casava pessoas diferentes que compartilham
    # sobrenomes (JULIANA MOREIRA DA ROCHA x PATRICIA DA ROCHA DOS MOREIRA) —
    # perigoso ao recuperar o ID entre reembolsos de mesma assinatura. Exige
    # também que os nomes NÃO divirjam (2026-09-24).
    lido_fornecedor = lido.get("fornecedor", "")
    if not any(
        semelhanca(esperado, lido_fornecedor) >= Decimal("0.6")
        and not nomes_divergem(esperado, lido_fornecedor)
        for esperado in fornecedores_validos
    ):
        motivos.append(f"fornecedor '{lido_fornecedor or '[vazio]'}'")

    # Quando o salvamento já registrou o documento efetivamente usado pelo
    # Ongsys (por exemplo BL.1), ele identifica a cópia criada e precisa bater
    # exatamente. Aceitar também o documento-base BL faria a recuperação ficar
    # ambígua justamente nos pilotos de duplicidade intencional. Para estados
    # antigos em que o documento final ainda não era conhecido, preservamos a
    # regra anterior e aceitamos uma variação numérica controlada.
    documento_esperado = documento_final or titulo.documento
    if not documento_equivalente(
        documento_esperado,
        lido.get("documento", ""),
        aceitar_variacao_numerica=not bool(documento_final),
    ):
        motivos.append(
            f"documento '{lido.get('documento', '') or '[vazio]'}' "
            f"(esperado '{documento_esperado}')"
        )
    valor = para_decimal(lido.get("valor", ""))
    if valor is None or abs(valor - titulo.valor) > tolerancia:
        motivos.append(f"valor '{lido.get('valor', '') or '[vazio]'}'")
    for campo, esperado in (
        ("emissao", titulo.emissao),
        ("vencimento", titulo.vencimento),
        ("efetivacao", titulo.efetivacao),
    ):
        if para_data(lido.get(campo, "")) != esperado:
            motivos.append(f"{campo} '{lido.get(campo, '') or '[vazio]'}'")
    return motivos


def _motivos_linha_incompativel(titulo: Titulo, texto_linha: str) -> list[str]:
    """Usa apenas os campos seguros que aparecem na tabela principal.

    A coluna ``Dcto`` da tabela de Contas a Pagar mostra o *tipo* do título
    (por exemplo ``Projetado``) e **não** o número do documento (``BL``,
    ``NF 100`` etc.).  Por isso a pré-validação de linha não pode comparar
    o documento — essa verificação é feita depois, ao abrir o formulário do
    candidato, pela ``_motivos_titulo_incompativel``.

    Os campos úteis que a linha expõe são: valor, emissão e vencimento.
    """
    if not texto_linha:
        return []
    texto_n = sem_acentos(texto_linha)
    motivos: list[str] = []

    if formatar_dinheiro_br(titulo.valor) not in texto_linha:
        motivos.append("valor visivel diferente")

    meses_en = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )
    meses_pt = (
        "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
        "Jul", "Ago", "Set", "Out", "Nov", "Dez",
    )
    for rotulo, esperado in (
        ("emissao", titulo.emissao),
        ("vencimento", titulo.vencimento),
    ):
        variantes = (
            esperado.strftime("%d/%m/%Y"),
            esperado.strftime("%d-%m-%Y"),
            esperado.strftime("%Y-%m-%d"),
            f"{esperado.day:02d}-{meses_pt[esperado.month - 1]}-{esperado.year}",
            f"{esperado.day:02d}-{meses_en[esperado.month - 1]}-{esperado.year}",
        )
        if not any(sem_acentos(valor) in texto_n for valor in variantes):
            motivos.append(f"{rotulo} visivel diferente")
    return motivos


def _recuperar_ids_desconhecidos(
    bot, ponto: Checkpoint, itens, por_chave: dict[str, Titulo], config: dict
) -> list[str]:
    """Localiza IDs por leitura da tela e atualiza só correspondência única."""
    recuperados: list[str] = []
    if not itens:
        return recuperados

    _titulo_tela("RECUPERACAO SEGURA DE TITULOS SEM ID")
    for item in itens:
        titulo = por_chave[item.chave]
        print(f"\n  Procurando {titulo.fornecedor} / {titulo.documento} / R$ {titulo.valor_br}...")
        inicio_busca, fim_busca = periodo_mensal(titulo.vencimento)
        print(
            f"    filtro da lista: Vencimento de {data_br(inicio_busca)} "
            f"a {data_br(fim_busca)}."
        )
        try:
            ids: list[str] = []

            # O documento final conhecido (por exemplo BL.1) é uma chave de
            # busca melhor que o nome da planilha quando o cadastro do Ongsys
            # usa outra razão social. A pesquisa continua sendo seguida pela
            # validação rígida de fornecedor, valor e datas ao abrir a linha.
            buscar_documento = getattr(bot, "ids_resultado_busca_documento", None)
            if item.documento_final and callable(buscar_documento):
                try:
                    ids = buscar_documento(
                        item.documento_final, titulo.vencimento, titulo.vencimento
                    )
                    if ids:
                        print(
                            f"    {len(ids)} candidato(s) encontrado(s) pelo "
                            f"documento final {item.documento_final}."
                        )
                except Exception as erro_documento:
                    print(
                        f"    busca pelo documento final nao confirmou a lista: "
                        f"{erro_documento}"
                    )

            if not ids:
                resolver_apelido = getattr(bot, "resolver_apelido", lambda nome: nome)
                nome_busca = resolver_apelido(titulo.fornecedor)
                ids = bot.ids_resultado_busca(
                    nome_busca, titulo.vencimento, titulo.vencimento
                )
            if not ids:
                resolver_apelido = getattr(bot, "resolver_apelido", lambda nome: nome)
                nome_busca = resolver_apelido(titulo.fornecedor)
                primeiro_nome = nome_busca.split()[0] if nome_busca.split() else ""
                if primeiro_nome:
                    ids = bot.ids_resultado_busca(
                        primeiro_nome, titulo.vencimento, titulo.vencimento
                    )
            if ids:
                print(f"    {len(ids)} candidato(s) sera(ao) validado(s) pela assinatura completa.")
                for id_candidato in ids[:5]:
                    codigo = getattr(
                        bot, "codigo_lancamento_para_id", lambda _id: ""
                    )(id_candidato)
                    detalhe = f" / Cod. Lancamento {codigo}" if codigo else ""
                    print(f"      ID {id_candidato}{detalhe}")
        except Exception as erro:
            print(
                f"    PULANDO: filtro da lista nao pode ser confirmado: {erro}\n"
                "    O checkpoint deste titulo foi mantido."
            )
            continue

        correspondencias: list[tuple[str, dict]] = []
        candidatos_tabela: list[str] = []  # IDs que passaram na pré-validação
        ids_ja_avaliados: set[str] = set()
        for id_ongsys in ids[:40]:
            ids_ja_avaliados.add(id_ongsys)
            texto_linha = getattr(
                bot, "texto_linha_para_id", lambda _id: ""
            )(id_ongsys)
            motivos_linha = _motivos_linha_incompativel(titulo, texto_linha)
            if motivos_linha:
                print(
                    f"      ID {id_ongsys}: descartado pela tabela por "
                    + "; ".join(motivos_linha)
                    + "."
                )
                continue
            candidatos_tabela.append(id_ongsys)

        # Quando a busca por documento retornou candidatos mas todos foram
        # descartados (documento genérico como "08_26" compartilhado entre
        # muitos títulos), tentar novamente pelo fornecedor.
        if not candidatos_tabela and ids_ja_avaliados:
            print("    nenhum candidato pela busca por documento; tentando pelo fornecedor...")
            try:
                resolver_apelido = getattr(bot, "resolver_apelido", lambda nome: nome)
                nome_busca_forn = resolver_apelido(titulo.fornecedor)
                ids_forn = bot.ids_resultado_busca(
                    nome_busca_forn, titulo.vencimento, titulo.vencimento
                )
                if not ids_forn:
                    primeiro_nome = nome_busca_forn.split()[0] if nome_busca_forn.split() else ""
                    if primeiro_nome:
                        ids_forn = bot.ids_resultado_busca(
                            primeiro_nome, titulo.vencimento, titulo.vencimento
                        )
                novos = [i for i in ids_forn if i not in ids_ja_avaliados]
                if novos:
                    print(f"    {len(novos)} candidato(s) novo(s) pelo fornecedor.")
                    for id_ongsys in novos[:40]:
                        ids_ja_avaliados.add(id_ongsys)
                        texto_linha = getattr(
                            bot, "texto_linha_para_id", lambda _id: ""
                        )(id_ongsys)
                        motivos_linha = _motivos_linha_incompativel(titulo, texto_linha)
                        if motivos_linha:
                            print(
                                f"      ID {id_ongsys}: descartado pela tabela por "
                                + "; ".join(motivos_linha)
                                + "."
                            )
                            continue
                        candidatos_tabela.append(id_ongsys)
            except Exception as erro_forn:
                print(f"    busca pelo fornecedor falhou: {erro_forn}")

        for id_ongsys in candidatos_tabela:
            lido = A.ler_titulo_da_tela(
                bot,
                id_ongsys,
                titulo.vencimento,
                titulo.vencimento,
                titulo.fornecedor,
            )
            if not lido:
                detalhe = getattr(bot, "ultima_falha_auditoria", "")
                icones = getattr(bot, "_descricao_icones_auditoria", "")
                print(
                    f"      ID {id_ongsys}: nao foi possivel abrir os detalhes."
                    f"{f' {detalhe}' if detalhe else ''}"
                )
                if icones:
                    print(f"        icones encontrados na linha: {icones}")
                continue
            # Quando todos os campos do formulário voltaram vazios (modo
            # visualização que impede leitura), os motivos rejeitam tudo.
            # Neste caso, verificar se os dados da tabela (valor + datas)
            # já bastam para confirmar o candidato quando ele é o único.
            todos_vazios = all(
                not lido.get(c) for c in
                ("fornecedor", "documento", "valor", "emissao", "vencimento")
            )
            if todos_vazios:
                print(
                    f"      ID {id_ongsys}: formulario abriu em modo "
                    f"somente-leitura (todos os campos vazios)."
                )
                continue
            motivos = _motivos_titulo_incompativel(
                titulo,
                lido,
                config["tolerancia_conciliacao"],
                documento_final=item.documento_final,
            )
            if not motivos:
                correspondencias.append((id_ongsys, lido))
                print(
                    f"      ID {id_ongsys}: assinatura confere "
                    f"(documento {lido.get('documento', '') or '[vazio]'})."
                )
            else:
                print(f"      ID {id_ongsys}: descartado por " + "; ".join(motivos) + ".")

        if len(correspondencias) == 1:
            id_ongsys, lido = correspondencias[0]
            ponto.marcar_salvo(
                titulo, id_ongsys, lido.get("documento", titulo.documento)
            )
            recuperados.append(id_ongsys)
            print(f"    localizado com seguranca: ID {id_ongsys}.")
        elif len(correspondencias) > 1:
            print("    mais de um titulo corresponde. Nada foi alterado no checkpoint.")
        elif not correspondencias and len(candidatos_tabela) == 1:
            print(
                "    ha um candidato compativel na tabela, mas o formulario nao "
                "pode ser validado. ID nao gravado; checkpoint mantido por seguranca."
            )
        else:
            print("    nenhum titulo correspondente foi localizado. Checkpoint mantido.")
    return recuperados


def _titulo_correspondente(
    lido: dict,
    por_documento: dict[str, list[Titulo]],
    resolver_apelido=None,
    nomes_na_tela=None,
) -> Titulo | None:
    """Liga um título lido da tela à linha da planilha.

    Usado para auditar títulos criados fora deste programa (§26) e os IDs
    informados em Conferir ID. Só aceita quando a ligação é inequívoca. Na
    dúvida devolve None, e o relatório registra que faltou referência —
    auditar contra o título errado seria pior que não auditar.

    1) Pelo documento: um único candidato com aquele documento e o fornecedor
       compatível.
    2) Sem documento igual (título lançado à mão como "FAT 09.2026", ou salvo
       pelo Ongsys como "09_26.3"): um único título da planilha com fornecedor
       compatível, mesmo valor e vencimento no mesmo mês. A diferença de
       documento aparece depois como divergência na auditoria.
    """
    resolver = resolver_apelido or (lambda nome: nome)
    lido_fornecedor = lido.get("fornecedor", "")

    def confere(nomes) -> bool:
        return any(
            semelhanca(nome, lido_fornecedor) >= Decimal("0.6")
            and not nomes_divergem(nome, lido_fornecedor)
            for nome in dict.fromkeys(n for n in nomes if n)
        )

    def fornecedor_compativel(titulo: Titulo) -> bool:
        return confere((titulo.fornecedor, resolver(titulo.fornecedor)))

    def nome_de_cartao_compativel(titulo: Titulo) -> bool:
        # Cadastro especial de cartão: o campo Nome mostra a categoria
        # ("LICENCA DE USO") e o estabelecimento fica no CPF/CNPJ. Só vale
        # junto com o documento igual, nunca na ligação por valor.
        return bool(nomes_na_tela) and confere(nomes_na_tela(titulo))

    valor = para_decimal(lido.get("valor", ""))
    candidatos = por_documento.get(normalizar_documento(lido.get("documento", "")), [])
    if len(candidatos) > 1:
        # Documentos como "NF 13" se repetem entre fornecedores na mesma
        # planilha. O fornecedor lido da tela desempata, mas só quando sobra
        # exatamente um candidato compatível e sem nome divergente.
        pelo_nome = [t for t in candidatos if fornecedor_compativel(t)]
        if not pelo_nome and valor is not None:
            # Cartão: vários estabelecimentos usam a data como documento
            # ("08.06") e a tela mostra só a categoria. Aqui o desempate exige
            # também o mesmo valor.
            pelo_nome = [
                t for t in candidatos
                if t.valor == valor and nome_de_cartao_compativel(t)
            ]
        candidatos = pelo_nome
    if len(candidatos) == 1:
        unico = candidatos[0]
        if (
            semelhanca(unico.fornecedor, lido_fornecedor) >= Decimal("0.6")
            or fornecedor_compativel(unico)
            or nome_de_cartao_compativel(unico)
        ):
            return unico

    vencimento = para_data(lido.get("vencimento", ""))
    if valor is None or vencimento is None:
        return None
    mesmos = [
        titulo
        for lista in por_documento.values()
        for titulo in lista
        if titulo.valor == valor
        and (titulo.vencimento.year, titulo.vencimento.month)
        == (vencimento.year, vencimento.month)
        and fornecedor_compativel(titulo)
    ]
    return mesmos[0] if len(mesmos) == 1 else None


def _estado() -> None:
    ponto = Checkpoint(CHECKPOINT)
    _titulo_tela("SITUACAO DO CHECKPOINT")
    if not ponto.itens:
        print("Nenhum titulo registrado ainda.")
        return
    for estado, quantidade in sorted(ponto.contagem_por_estado().items()):
        print(f"  {estado:26} {quantidade}")
    sem_id = ponto.sem_confirmacao_no_ongsys()
    if sem_id:
        print("\nSem ID confirmado (podem existir no Ongsys) — rode Auditar ou Conferir ID:")
        for item in sem_id:
            print(f"  {item.fornecedor} / {item.documento_final or item.documento_original} "
                  f"R$ {item.valor} ({item.estado})")
    com_divergencia = ponto.salvos_com_divergencia()
    if com_divergencia:
        print("\nSalvos com divergencia — revise no Ongsys e marque como conferido:")
        for item in com_divergencia:
            print(f"  ID {item.id_ongsys} | {item.fornecedor} / "
                  f"{item.documento_final or item.documento_original}: "
                  f"{', '.join(item.divergencias) or 'divergencia nao detalhada'}")


def _aceitar_divergencia(ids: list[str]) -> None:
    """Encerra títulos salvos com ID cujas divergências o usuário revisou.

    Não abre o Ongsys. Mostra cada título e as divergências, exige ACEITO e
    guarda uma cópia do checkpoint antes de alterar. Os títulos vão para
    CONCLUIDO: continuam protegidos contra relançamento.
    """
    _titulo_tela("MARCAR DIVERGENCIAS COMO CONFERIDAS")
    print("Este modo NAO abre o Ongsys e NAO cria, altera, paga ou baixa titulos.")
    print("Use somente depois de conferir cada titulo no Ongsys.\n")
    ponto = Checkpoint(CHECKPOINT)
    alvo = {i.strip().lstrip("0") for i in ids if i.strip()}
    itens = [
        item for item in ponto.salvos_com_divergencia()
        if item.id_ongsys.lstrip("0") in alvo
    ]
    nao_encontrados = alvo - {item.id_ongsys.lstrip("0") for item in itens}
    if nao_encontrados:
        print(
            "IDs ignorados (nao estao entre os salvos com divergencia): "
            + ", ".join(sorted(nao_encontrados))
        )
    if not itens:
        raise SaidaControlada("\nNenhum titulo para marcar. O checkpoint nao foi alterado.\n")

    for item in itens:
        print(
            f"  ID {item.id_ongsys} | {item.fornecedor} / "
            f"{item.documento_final or item.documento_original} | R$ {item.valor}"
        )
        print(f"      divergencias: {', '.join(item.divergencias) or 'nao detalhadas'}")
    if input(
        f"\nDigite ACEITO para marcar {len(itens)} titulo(s) como conferido(s): "
    ).strip().upper() != "ACEITO":
        raise SaidaControlada("\nCancelado. O checkpoint nao foi alterado.\n")

    reserva = _sem_sobrescrever(CHECKPOINT.with_name(
        f"checkpoint_antes_aceite_{datetime.now():%Y%m%d_%H%M%S}.json"
    ))
    shutil.copy2(CHECKPOINT, reserva)
    observacao = f"Divergencias revisadas pelo usuario em {datetime.now():%d/%m/%Y %H:%M}."
    for item in itens:
        ponto.aceitar_divergencias(item.chave, observacao)
    print(f"\n{len(itens)} titulo(s) marcado(s) como conferido(s).")
    print(f"Copia de seguranca: {reserva}")
    print("Nada foi criado ou alterado no Ongsys.")


def _limpar() -> None:
    """Apaga arquivos que passaram do prazo de guarda, após confirmação.

    Não abre o Ongsys. Mostra o que seria apagado e exige APAGAR. Nunca toca
    no checkpoint, na configuração, na sessão nem no histórico de execuções.
    """
    from . import limpeza

    _titulo_tela("LIMPEZA DE ARQUIVOS ANTIGOS")
    print("Este modo NAO abre o Ongsys e NAO altera nenhum titulo.")
    print(f"Prazo de guarda: capturas de tela {limpeza.DIAS_EVIDENCIAS} dias; "
          f"relatorios e copias do checkpoint {limpeza.DIAS_RELATORIOS} dias.")
    print(f"Ficam sempre: checkpoint.json, config.json, a sessao, o historico de execucoes "
          f"e as {limpeza.COPIAS_MINIMAS} copias mais recentes do checkpoint.\n")

    itens = limpeza.candidatos(BASE)
    if not itens:
        raise SaidaControlada("Nenhum arquivo passou do prazo de guarda. Nada foi apagado.\n")

    for grupo, quantidade, tamanho, antigo, recente in limpeza.resumo(itens):
        print(f"  {grupo}: {quantidade} arquivo(s), {tamanho / 1_048_576:.1f} MB, "
              f"de {antigo:%d/%m/%Y} a {recente:%d/%m/%Y}")

    if CHECKPOINT.exists():
        ponto = Checkpoint(CHECKPOINT)
        abertas = len(ponto.sem_confirmacao_no_ongsys()) + len(ponto.salvos_com_divergencia())
        if abertas:
            print(f"\nATENCAO: ha {abertas} titulo(s) com pendencia em aberto. Os arquivos antigos")
            print("podem ajudar a resolve-los. Se tiver duvida, resolva as pendencias antes.")

    print("\nA exclusao e definitiva: os arquivos NAO vao para a Lixeira.")
    if input(
        f"Digite APAGAR para excluir {len(itens)} arquivo(s): "
    ).strip().upper() != "APAGAR":
        raise SaidaControlada("\nCancelado. Nenhum arquivo foi apagado.\n")

    apagados, falhas = limpeza.apagar(itens, BASE)
    print(f"\n{apagados} arquivo(s) apagado(s).")
    for falha in falhas:
        print(f"  nao apagado: {falha}")
    _registrar_historico(
        "limpar",
        resultado=f"arquivos apagados: {apagados}; nao apagados: {len(falhas)}",
    )
    print("Nada foi criado ou alterado no Ongsys.")


def _resolver_pendencia(args, config: dict) -> None:
    """Libera um único EM_EXECUCAO após confirmação manual de ausência.

    Não abre o navegador nem acessa o Ongsys. A planilha é solicitada apenas
    para reconstruir e mostrar todos os dados do título antes de alterar o
    checkpoint local.
    """
    _titulo_tela("LIBERAR PENDENCIA CONFIRMADA COMO NAO LANCADA")
    print("Este modo NAO abre o Ongsys e NAO cria, altera, paga ou baixa titulos.")
    print("Ele modifica somente o checkpoint local, depois de nova confirmacao.\n")

    ponto = Checkpoint(CHECKPOINT)
    print("Informe a mesma planilha para identificar exatamente a pendencia.")
    resultado, _, _, _, _ = _planejar(args, config)
    candidatos: list[tuple[Titulo, object]] = []
    for titulo in resultado.titulos:
        item = ponto.obter(titulo)
        if item and item.estado == Estado.EM_EXECUCAO and not item.id_ongsys:
            candidatos.append((titulo, item))

    if not candidatos:
        raise SaidaControlada(
            "\nNao existe pendencia EM_EXECUCAO sem ID correspondente a esta planilha.\n"
        )

    print("\nPendencias que podem ser liberadas somente apos conferencia manual:")
    for indice, (titulo, item) in enumerate(candidatos, start=1):
        print(
            f"  {indice}. {titulo.fornecedor} / {titulo.documento} / "
            f"R$ {titulo.valor_br} / emissao {data_br(titulo.emissao)} / "
            f"vencimento {data_br(titulo.vencimento)} / "
            f"efetivacao {data_br(titulo.efetivacao)} / "
            f"{item.tentativas} tentativa(s)"
        )

    if len(candidatos) == 1:
        escolhido = 0
    else:
        resposta = input("\nDigite o numero exato da pendencia: ").strip()
        if not resposta.isdigit() or not 1 <= int(resposta) <= len(candidatos):
            raise SaidaControlada("\nOpcao invalida. Nada foi alterado.\n")
        escolhido = int(resposta) - 1

    titulo, _item = candidatos[escolhido]
    print("\nATENCAO:")
    print("Voce deve ter conferido manualmente que este titulo NAO existe no Ongsys.")
    print("A liberacao apenas permite que ele entre em uma FUTURA fila de lancamento.")
    print("Nenhum lancamento sera criado agora.")
    if input("\nDigite LIBERAR para confirmar: ").strip() != "LIBERAR":
        raise SaidaControlada("\nCancelado. O checkpoint nao foi alterado.\n")

    if not CHECKPOINT.exists():
        raise SaidaControlada("\nCheckpoint nao encontrado. Nada foi alterado.\n")
    reserva = _sem_sobrescrever(CHECKPOINT.with_name(
        f"checkpoint_antes_liberacao_{datetime.now():%Y%m%d_%H%M%S}.json"
    ))
    shutil.copy2(CHECKPOINT, reserva)
    observacao = (
        f"Ausencia confirmada manualmente pelo usuario em {datetime.now():%d/%m/%Y}; "
        f"fornecedor={titulo.fornecedor}; documento={titulo.documento}; "
        f"vencimento={data_br(titulo.vencimento)}; valor={titulo.valor_br}."
    )
    try:
        ponto.liberar_apos_confirmacao_manual(titulo, observacao)
    except ValueError as erro:
        raise SaidaControlada(f"\nNao foi possivel liberar: {erro}\n") from erro

    print("\nPendencia liberada no checkpoint para uma FUTURA tentativa.")
    print(f"Copia de seguranca: {reserva}")
    print("Nada foi criado ou alterado no Ongsys.")


# --------------------------------------------------------------------------
# Argumentos
# --------------------------------------------------------------------------

def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="automacao_ongsys",
        description="Automacao local de contas a pagar no Ongsys. O modo padrao apenas confere.",
    )
    sub = parser.add_subparsers(dest="comando")

    def comuns(p):
        p.add_argument("planilha", nargs="?", type=Path, help="caminho do arquivo .xlsx")
        p.add_argument("--inicio", type=date.fromisoformat, help="data inicial AAAA-MM-DD")
        p.add_argument("--fim", type=date.fromisoformat, help="data final AAAA-MM-DD")
        p.add_argument("--conta", help="ID da conta pagadora")
        p.add_argument("--aba", help="nome da aba da planilha")
        p.add_argument("--coluna", action="append", metavar="campo=letra",
                       help="forca uma coluna, por exemplo --coluna saida=M")
        p.add_argument("--somente-fornecedor", metavar="NOME",
                       help="processa apenas titulos deste fornecedor")
        return p

    comuns(sub.add_parser("planejar", help="le a planilha e gera relatorios, sem tocar no Ongsys"))
    executar = comuns(sub.add_parser("executar", help="cadastra os titulos apos confirmacao"))
    executar.add_argument("--limite", type=int, help="maximo de titulos nesta execucao")
    executar.add_argument("--retomar", action="store_true",
                          help="continua de onde parou, usando o checkpoint")
    executar.add_argument(
        "--permitir-duplicidade-exata",
        action="store_true",
        help="modo excepcional de piloto; exige fornecedor, limite 1 e DUPLICAR",
    )

    auditar = comuns(sub.add_parser("auditar", help="reabre titulos salvos e compara com a planilha"))
    auditar.add_argument("--auditar-id", action="append", metavar="ID",
                         help="ID do titulo no Ongsys; pode repetir")

    sub.add_parser("login", help="abre o navegador para o login manual")
    sub.add_parser("conferir-tela", help="confere os campos da tela sem preencher nada")

    testar_forn = comuns(sub.add_parser(
        "testar-fornecedor",
        help="digita fornecedores no campo de busca e mostra as sugestoes, sem salvar nada",
    ))
    testar_forn.add_argument("--nome", action="append", metavar="NOME",
                             help="fornecedor a testar; pode repetir. Sem isso, usa a planilha")

    comuns(sub.add_parser(
        "resolver-pendencia",
        help="libera no checkpoint uma tentativa confirmada manualmente como nao lancada",
    ))
    aceitar = sub.add_parser(
        "aceitar-divergencia",
        help="marca como conferido um titulo salvo (com ID) cujas divergencias foram revisadas",
    )
    aceitar.add_argument("--id", action="append", dest="ids", metavar="ID",
                         help="ID do titulo no Ongsys; pode repetir")
    sub.add_parser("estado", help="mostra a situacao do checkpoint")
    sub.add_parser(
        "limpar",
        help="apaga capturas, relatorios e copias antigas, depois de mostrar e pedir confirmacao",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _construir_parser()
    args = parser.parse_args(argv)
    comando = args.comando or "planejar"

    if comando == "estado":
        _estado()
        return 0

    if comando == "aceitar-divergencia":
        _aceitar_divergencia(args.ids or [])
        return 0

    if comando == "limpar":
        _limpar()
        return 0

    config = _carregar_config()
    desconhecidos = [c for c in config.get("_campos_desconhecidos", []) if not c.startswith("_")]
    if desconhecidos:
        print(f"AVISO: campos ignorados no config.json: {', '.join(desconhecidos)}")

    if comando == "login":
        from .navegador import OngsysBot
        bot = OngsysBot(config, PERFIL, EVIDENCIAS)
        try:
            bot.abrir()
            bot.aguardar_login_manual()
        finally:
            bot.fechar()
        return 0

    if comando == "conferir-tela":
        _conferir_tela(config)
        return 0

    if comando == "testar-fornecedor":
        _testar_fornecedor(args, config)
        return 0

    if comando == "resolver-pendencia":
        _resolver_pendencia(args, config)
        return 0

    if comando == "auditar":
        _auditar(args, config)
        return 0

    if comando == "executar":
        _executar(args, config)
        return 0

    _planejar(args, config)
    print("\nModo conferencia: nada foi enviado ao Ongsys.")
    return 0


def executar_cli() -> int:
    try:
        return main()
    except SaidaControlada as saida:
        print(saida)
        return 1
    except AcaoProibida as erro:
        # Disjuntor de segurança: a automação tentou tocar em algo ligado a
        # baixa, pagamento, remessa ou aprovação. Para tudo e explica.
        print("\n" + "=" * 68)
        print("EXECUCAO INTERROMPIDA POR SEGURANCA")
        print("=" * 68)
        print(erro)
        print("Nenhuma baixa, pagamento, remessa ou aprovacao foi realizada.")
        print("Rode a auditoria antes de executar novamente e envie esta tela para analise.")
        _registrar_historico("bloqueio_de_seguranca", resultado=str(erro)[:200])
        return 2
    except KeyboardInterrupt:
        print("\n\nOperacao interrompida pelo usuario. Nada mais sera enviado.")
        return 130
    except Exception as erro:  # último recurso: mensagem legível, não traceback cru
        print(f"\nERRO INESPERADO: {erro}\n")
        print("Copie esta tela ao pedir ajuda. Nenhum dado foi enviado para fora do computador.")
        return 1


if __name__ == "__main__":
    sys.exit(executar_cli())
