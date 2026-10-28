"""Painel local para operar os mesmos comandos seguros da automação.

O painel não implementa regras financeiras e não acessa o Ongsys diretamente.
Ele apenas prepara os argumentos, inicia ``automacao_ongsys.py`` e mantém a
entrada interativa visível para que nenhuma confirmação seja respondida em
nome do usuário. As abas "Resultado" e "Pendências" só LEEM os relatórios e o
checkpoint; qualquer alteração passa pelo CLI, que pede confirmação digitada.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import configuracao, planilha
from .painel_dados import (
    ACOES,
    GRUPO_DIVERGENCIA,
    GRUPO_FALHA,
    GRUPO_SEM_ID,
    SITUACOES,
    LinhaResultado,
    classificar_linha,
    extrair_progresso,
    formatar_data,
    interpretar_data,
    ler_resultado,
    pendencias_do_checkpoint,
    pergunta_pendente,
    resultado_mais_recente,
    resumir_resultado,
)


BASE = Path(__file__).resolve().parent.parent
CONFIG = BASE / "config.json"
PROGRAMA = BASE / "automacao_ongsys.py"
RELATORIOS = BASE / "relatorios"
CHECKPOINT = BASE / "checkpoint.json"


@dataclass(frozen=True)
class OpcoesPainel:
    comando: str
    planilha: Path | None = None
    inicio: date | None = None
    fim: date | None = None
    conta: str | None = None
    fornecedor: str = ""
    limite: int | None = None
    ids: tuple[str, ...] = ()


def montar_comando(opcoes: OpcoesPainel, python: str | Path | None = None) -> list[str]:
    """Monta a chamada ao CLI sem passar respostas de confirmação."""
    executavel = str(python or sys.executable)
    comando = [executavel, "-u", str(PROGRAMA), opcoes.comando]
    if opcoes.comando == "aceitar-divergencia":
        # Só os IDs: o próprio CLI mostra os títulos e exige ACEITO.
        for identificador in opcoes.ids:
            comando.extend(["--id", identificador])
        return comando
    if opcoes.planilha is not None:
        comando.append(str(opcoes.planilha))
    if opcoes.inicio is not None:
        comando.extend(["--inicio", opcoes.inicio.isoformat()])
    if opcoes.fim is not None:
        comando.extend(["--fim", opcoes.fim.isoformat()])
    if opcoes.conta is not None:
        comando.extend(["--conta", opcoes.conta])
    if opcoes.fornecedor.strip():
        comando.extend(["--somente-fornecedor", opcoes.fornecedor.strip()])
    if opcoes.comando == "executar" and opcoes.limite is not None:
        comando.extend(["--limite", str(opcoes.limite)])
    if opcoes.comando == "auditar":
        for identificador in opcoes.ids:
            comando.extend(["--auditar-id", identificador])
    return comando


def extrair_resumo(texto: str) -> dict[str, str]:
    """Extrai indicadores conhecidos sem depender deles para executar.

    Cobre o resumo do **executar** ("Fila : N titulo(s) | R$ ...") e o do
    **planejar** ("Titulos encontrados: N" + "Valor total : R$ ..."). Sem o
    segundo, os cartões ficavam vazios logo após o Planejar — confirmado no
    teste real com testes8.xlsx (2026-09-21).
    """
    resumo: dict[str, str] = {}
    fila = re.search(
        r"(?:Fila\s*:|Total:)\s*(\d+)\s+titulo\(s\)\s*\|\s*R\$\s*([\d.,]+)",
        texto, flags=re.IGNORECASE,
    )
    if fila:
        resumo["titulos"] = fila.group(1)
        resumo["valor"] = f"R$ {fila.group(2)}"
    else:
        encontrados = re.search(r"Titulos?\s+encontrados\s*:\s*(\d+)", texto, flags=re.IGNORECASE)
        if encontrados:
            resumo["titulos"] = encontrados.group(1)
        total = re.search(r"Valor\s+total\s*:\s*R\$\s*([\d.,]+)", texto, flags=re.IGNORECASE)
        if total:
            resumo["valor"] = f"R$ {total.group(1)}"

    avisos = re.search(r"Avisos\s*:\s*(\d+)", texto, flags=re.IGNORECASE)
    if avisos:
        resumo["avisos"] = avisos.group(1)
    else:
        # No planejar os avisos vêm como uma lista com marcadores "  - ..."
        # abaixo do cabeçalho "AVISOS (...)". Conta esses marcadores.
        bloco = re.search(
            r"AVISOS\b[^\n]*\n(.*?)(?:\n=|\n\s*\n|\Z)", texto, flags=re.IGNORECASE | re.DOTALL
        )
        if bloco:
            marcadores = re.findall(r"^\s+-\s+\S", bloco.group(1), flags=re.MULTILINE)
            if marcadores:
                resumo["avisos"] = str(len(marcadores))
    return resumo


class PainelOngsys(tk.Tk):
    COR_FUNDO = "#edf2f7"
    COR_PAINEL = "#ffffff"
    COR_SUAVE = "#f6f8fb"
    COR_TEXTO = "#172334"
    COR_MUTED = "#66758a"
    COR_AZUL = "#175fbd"
    COR_AZUL_ESCURO = "#104b98"
    COR_VERDE = "#16734b"
    COR_VERDE_FUNDO = "#e9f7f0"
    COR_LINHA = "#d8e1eb"
    COR_ALERTA = "#9a6500"
    COR_ALERTA_FUNDO = "#fff4dc"
    COR_ERRO = "#b3261e"
    COR_ERRO_FUNDO = "#fdecea"

    #: Cores das linhas do acompanhamento (fundo escuro).
    CORES_CONSOLE = {
        "falha": "#ff8a80",
        "atencao": "#ffd166",
        "ok": "#7ee2a8",
        "info": "#8fa3b8",
        "titulo": "#9cc9ff",
    }
    #: Fundo das linhas das tabelas por cor lógica.
    FUNDOS_TABELA = {
        "ok": "#e9f7f0",
        "atencao": "#fff4dc",
        "falha": "#fdecea",
        "info": "#eef2f7",
    }

    def __init__(self) -> None:
        super().__init__()
        self.title("Automação Ongsys — Contas a Pagar")
        self.geometry("1240x820")
        self.minsize(960, 700)
        self.configure(bg=self.COR_FUNDO)
        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)

        self._processo: subprocess.Popen[str] | None = None
        self._comando_em_execucao = ""
        self._inicio_execucao: float | None = None
        self._fila_saida: queue.Queue[str | None] = queue.Queue()
        self._buffer_saida = ""
        self._pergunta_avisada = ""
        self._contas_por_rotulo: dict[str, str] = {}
        self._botoes_operacao: list[ttk.Button] = []
        self._dados_recolhidos = False
        self._resultado: list[LinhaResultado] = []
        self._pendencias_por_item: dict[str, object] = {}

        self._configurar_estilos()
        self._criar_variaveis()
        self._carregar_configuracao()
        self._montar_tela()
        self.after(80, self._consumir_saida)
        self.after(300, self._carregar_ultimo_resultado)
        self.after(300, self._carregar_pendencias)

    # -- aparência ---------------------------------------------------------

    def _configurar_estilos(self) -> None:
        estilo = ttk.Style(self)
        if "clam" in estilo.theme_names():
            estilo.theme_use("clam")
        estilo.configure("App.TFrame", background=self.COR_FUNDO)
        estilo.configure("Surface.TFrame", background=self.COR_PAINEL)
        estilo.configure("Soft.TFrame", background=self.COR_SUAVE)
        estilo.configure(
            "Title.TLabel", background=self.COR_PAINEL, foreground=self.COR_TEXTO,
            font=("Segoe UI Semibold", 15),
        )
        estilo.configure(
            "Subtitle.TLabel", background=self.COR_PAINEL, foreground=self.COR_MUTED,
            font=("Segoe UI", 9),
        )
        estilo.configure(
            "Section.TLabel", background=self.COR_PAINEL, foreground=self.COR_TEXTO,
            font=("Segoe UI Semibold", 11),
        )
        estilo.configure(
            "Body.TLabel", background=self.COR_PAINEL, foreground=self.COR_MUTED,
            font=("Segoe UI", 9),
        )
        for nome, fundo, frente in (
            ("Status.TLabel", self.COR_VERDE_FUNDO, self.COR_VERDE),
            ("StatusPergunta.TLabel", self.COR_ALERTA_FUNDO, self.COR_ALERTA),
            ("StatusErro.TLabel", self.COR_ERRO_FUNDO, self.COR_ERRO),
        ):
            estilo.configure(
                nome, background=fundo, foreground=frente,
                font=("Segoe UI Semibold", 10), padding=(12, 6),
            )
        estilo.configure(
            "MetricTitle.TLabel", background=self.COR_SUAVE, foreground=self.COR_MUTED,
            font=("Segoe UI", 8),
        )
        estilo.configure(
            "MetricValue.TLabel", background=self.COR_SUAVE, foreground=self.COR_TEXTO,
            font=("Segoe UI Semibold", 13),
        )
        estilo.configure(
            "Primary.TButton", background=self.COR_AZUL, foreground="#ffffff",
            borderwidth=0, padding=(14, 9), font=("Segoe UI Semibold", 9),
        )
        estilo.map(
            "Primary.TButton",
            background=[("active", self.COR_AZUL_ESCURO), ("disabled", "#91a6bf")],
        )
        estilo.configure(
            "Secondary.TButton", background=self.COR_PAINEL, foreground=self.COR_TEXTO,
            bordercolor=self.COR_LINHA, padding=(12, 8), font=("Segoe UI Semibold", 9),
        )
        estilo.map("Secondary.TButton", background=[("active", self.COR_SUAVE)])
        estilo.configure("TEntry", padding=7, fieldbackground=self.COR_PAINEL)
        estilo.configure("TCombobox", padding=6, fieldbackground=self.COR_PAINEL)
        estilo.configure("TNotebook", background=self.COR_PAINEL, borderwidth=0)
        estilo.configure(
            "TNotebook.Tab", padding=(14, 6), font=("Segoe UI Semibold", 9),
        )
        estilo.configure(
            "Treeview", rowheight=24, font=("Segoe UI", 9),
            fieldbackground=self.COR_PAINEL,
        )
        estilo.configure("Treeview.Heading", font=("Segoe UI Semibold", 9))
        estilo.configure(
            "Progresso.Horizontal.TProgressbar", troughcolor=self.COR_SUAVE,
            background=self.COR_AZUL, thickness=8,
        )

    def _criar_variaveis(self) -> None:
        self.var_planilha = tk.StringVar()
        self.var_inicio = tk.StringVar()
        self.var_fim = tk.StringVar()
        self.var_conta = tk.StringVar(value="Selecione uma conta ou 'Sem conta pagadora'")
        self.var_fornecedor = tk.StringVar()
        self.var_limite = tk.StringVar()
        self.var_status = tk.StringVar(value="Pronto para selecionar uma planilha")
        self.var_titulos = tk.StringVar(value="—")
        self.var_valor = tk.StringVar(value="—")
        self.var_avisos = tk.StringVar(value="—")
        self.var_pendencias = tk.StringVar(value="—")
        self.var_resposta = tk.StringVar()
        self.var_progresso = tk.StringVar(value="")
        self.var_resultado_resumo = tk.StringVar(value="Nenhuma execução carregada.")
        self.var_resultado_detalhe = tk.StringVar(value="")
        self.var_pendencia_acao = tk.StringVar(
            value="Selecione uma linha para ver o que fazer."
        )

    def _carregar_configuracao(self) -> None:
        try:
            config = configuracao.carregar(CONFIG)
        except Exception as erro:
            messagebox.showerror("Configuração inválida", str(erro), parent=self)
            self.destroy()
            raise
        rotulo_sem_conta = "0 — Sem conta pagadora"
        self._contas_por_rotulo[rotulo_sem_conta] = "0"
        for identificador, nome in sorted(
            config["contas_pagadoras"].items(),
            key=lambda item: int(item[0]) if item[0].isdigit() else 9999,
        ):
            self._contas_por_rotulo[f"{identificador} — {nome}"] = identificador

    # -- montagem ----------------------------------------------------------

    def _montar_tela(self) -> None:
        exterior = ttk.Frame(self, style="App.TFrame", padding=18)
        exterior.pack(fill="both", expand=True)
        janela = ttk.Frame(exterior, style="Surface.TFrame", padding=0)
        janela.pack(fill="both", expand=True)

        self._montar_cabecalho(janela)
        self._montar_operacoes(janela)
        conteudo = ttk.Frame(janela, style="Surface.TFrame", padding=(22, 10, 22, 12))
        conteudo.pack(fill="both", expand=True)
        conteudo.columnconfigure(0, weight=1)
        conteudo.rowconfigure(1, weight=1)

        self.rotulo_status = ttk.Label(
            conteudo, textvariable=self.var_status, style="Status.TLabel"
        )
        self.rotulo_status.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        # Divisória vertical arrastável: o operador pode dedicar mais espaço
        # aos dados ou às abas sem perder conteúdo. Durante uma operação, a
        # parte superior continua recolhida automaticamente.
        self.divisor_vertical = tk.PanedWindow(
            conteudo, orient=tk.VERTICAL, bg=self.COR_LINHA, borderwidth=0,
            relief="flat", sashwidth=9, sashrelief="raised", showhandle=True,
            opaqueresize=True,
        )
        self.divisor_vertical.grid(row=1, column=0, columnspan=2, sticky="nsew")

        self.bloco_superior = ttk.Frame(self.divisor_vertical, style="Surface.TFrame")
        self.bloco_superior.columnconfigure(0, weight=1)
        self.bloco_superior.columnconfigure(1, weight=0)
        self._montar_dados(self.bloco_superior)
        self._montar_seguranca(self.bloco_superior)
        self._montar_metricas(self.bloco_superior)

        self.bloco_acompanhamento = ttk.Frame(
            self.divisor_vertical, style="Surface.TFrame", padding=(0, 8, 0, 0)
        )
        self.bloco_acompanhamento.columnconfigure(0, weight=1)
        self.bloco_acompanhamento.rowconfigure(0, weight=1)
        self._montar_abas(self.bloco_acompanhamento)
        self._montar_resposta(self.bloco_acompanhamento)

        self.divisor_vertical.add(self.bloco_superior, minsize=120, stretch="never")
        self.divisor_vertical.add(self.bloco_acompanhamento, minsize=160, stretch="always")
        self.after_idle(self._posicionar_divisoria_inicial)

    def _posicionar_divisoria_inicial(self) -> None:
        try:
            altura = self.divisor_vertical.winfo_height()
            necessario = self.bloco_superior.winfo_reqheight()
            # Em notebook (janela ~750 px) os dados ocupavam quase tudo e as
            # abas ficavam sem linhas visíveis. Mostra os dados inteiros quando
            # couberem, mas sempre deixa pelo menos 235 px para as abas.
            posicao = max(120, min(necessario, altura - 235))
            self.divisor_vertical.sash_place(0, 1, posicao)
        except (tk.TclError, AttributeError):
            pass

    def _recolher_dados(self) -> None:
        try:
            self.divisor_vertical.paneconfigure(self.bloco_superior, minsize=0)
            self.divisor_vertical.sash_place(0, 1, 0)
        except (tk.TclError, AttributeError):
            pass
        self.bloco_dados.grid_remove()
        self.bloco_seguranca.grid_remove()
        self.faixa_metricas.grid_remove()
        self._dados_recolhidos = True
        self.botao_recolher.configure(text="▼ Mostrar dados")

    def _mostrar_dados(self) -> None:
        self.bloco_dados.grid()
        self.bloco_seguranca.grid()
        self.faixa_metricas.grid()
        self._dados_recolhidos = False
        self.botao_recolher.configure(text="▲ Recolher dados")
        try:
            self.divisor_vertical.paneconfigure(self.bloco_superior, minsize=120)
            self.after_idle(self._posicionar_divisoria_inicial)
        except (tk.TclError, AttributeError):
            pass

    def _alternar_dados(self) -> None:
        if getattr(self, "_dados_recolhidos", False):
            self._mostrar_dados()
        else:
            self._recolher_dados()

    def _montar_cabecalho(self, pai: ttk.Frame) -> None:
        cabecalho = ttk.Frame(pai, style="Surface.TFrame", padding=(22, 10))
        cabecalho.pack(fill="x")
        marca = tk.Label(
            cabecalho, text="O", bg=self.COR_AZUL, fg="white",
            font=("Segoe UI Semibold", 16), width=3, height=1,
        )
        marca.pack(side="left", padx=(0, 12))
        textos = ttk.Frame(cabecalho, style="Surface.TFrame")
        textos.pack(side="left")
        ttk.Label(textos, text="Automação Ongsys", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            textos, text="Contas a pagar · planejar → executar → auditar → revisar pendências",
            style="Subtitle.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            cabecalho, text="●  Proteções ativas", style="Subtitle.TLabel",
            foreground=self.COR_VERDE,
        ).pack(side="right")

    def _montar_operacoes(self, pai: ttk.Frame) -> None:
        faixa = ttk.Frame(pai, style="Soft.TFrame", padding=(22, 8))
        faixa.pack(fill="x")
        operacoes = (
            ("1. Planejar", self._planejar),
            ("2. Executar", self._executar),
            ("3. Auditar", self._auditar),
            ("Conferir ID", self._conferir_id),
            ("Liberar pendência", self._liberar_pendencia),
            ("Situação", self._situacao),
            ("Login", self._login),
        )
        for texto, acao in operacoes:
            botao = ttk.Button(faixa, text=texto, command=acao, style="Secondary.TButton")
            botao.pack(side="left", padx=(0, 6))
            self._botoes_operacao.append(botao)
        ttk.Button(
            faixa, text="Abrir relatórios", command=self._abrir_relatorios,
            style="Secondary.TButton",
        ).pack(side="right")
        self.botao_recolher = ttk.Button(
            faixa, text="▲ Recolher dados", command=self._alternar_dados,
            style="Secondary.TButton",
        )
        self.botao_recolher.pack(side="right", padx=(0, 6))

    def _montar_dados(self, pai: ttk.Frame) -> None:
        self.bloco_dados = ttk.Frame(pai, style="Surface.TFrame", padding=(15, 8))
        self.bloco_dados.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        self.bloco_dados.columnconfigure(1, weight=1)
        ttk.Label(self.bloco_dados, text="Dados da execução", style="Section.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 6)
        )
        self._rotulo_campo(self.bloco_dados, "Planilha", 1, 0)
        ttk.Entry(self.bloco_dados, textvariable=self.var_planilha, state="readonly").grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=(8, 8)
        )
        ttk.Button(
            self.bloco_dados, text="Selecionar…", command=self._selecionar_planilha,
            style="Secondary.TButton",
        ).grid(row=1, column=3, sticky="ew")

        self._rotulo_campo(self.bloco_dados, "Data inicial (DD/MM/AAAA)", 2, 0)
        ttk.Entry(self.bloco_dados, textvariable=self.var_inicio, width=15).grid(
            row=2, column=1, sticky="ew", padx=(8, 18), pady=(6, 0)
        )
        self._rotulo_campo(self.bloco_dados, "Data final", 2, 2)
        ttk.Entry(self.bloco_dados, textvariable=self.var_fim, width=15).grid(
            row=2, column=3, sticky="ew", padx=(8, 0), pady=(6, 0)
        )

        self._rotulo_campo(self.bloco_dados, "Conta", 3, 0)
        self.combo_conta = ttk.Combobox(
            self.bloco_dados, textvariable=self.var_conta, state="readonly",
            values=list(self._contas_por_rotulo),
        )
        self.combo_conta.grid(row=3, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(6, 0))

        self._rotulo_campo(self.bloco_dados, "Fornecedor (opcional)", 4, 0)
        ttk.Entry(self.bloco_dados, textvariable=self.var_fornecedor).grid(
            row=4, column=1, sticky="ew", padx=(8, 18), pady=(6, 0)
        )
        self._rotulo_campo(self.bloco_dados, "Limite", 4, 2)
        ttk.Entry(self.bloco_dados, textvariable=self.var_limite, width=8).grid(
            row=4, column=3, sticky="ew", padx=(8, 0), pady=(6, 0)
        )

    def _rotulo_campo(self, pai: ttk.Frame, texto: str, linha: int, coluna: int) -> None:
        ttk.Label(pai, text=texto, style="Body.TLabel").grid(
            row=linha, column=coluna, sticky="w", pady=(6 if linha > 1 else 0, 0)
        )

    def _montar_seguranca(self, pai: ttk.Frame) -> None:
        self.bloco_seguranca = ttk.Frame(pai, style="Soft.TFrame", padding=(15, 10), width=275)
        self.bloco_seguranca.grid(row=0, column=1, sticky="nsew")
        self.bloco_seguranca.grid_propagate(False)
        ttk.Label(
            self.bloco_seguranca, text="Segurança desta execução", style="Section.TLabel",
            background=self.COR_SUAVE,
        ).pack(anchor="w", pady=(0, 4))
        itens = (
            "Não baixa nem efetua pagamentos",
            "Não remete ou envia para aprovação",
            "Checkpoint evita recriação automática",
            "Duplicidades incertas exigem decisão",
            "Auditoria permanece somente leitura",
            "SIM, CONFIRMO e ACEITO são digitados por você",
        )
        for item in itens:
            linha = ttk.Frame(self.bloco_seguranca, style="Soft.TFrame")
            linha.pack(fill="x", pady=1)
            ttk.Label(
                linha, text="✓", foreground=self.COR_VERDE,
                background=self.COR_SUAVE, font=("Segoe UI Semibold", 10),
            ).pack(side="left", padx=(0, 7))
            ttk.Label(
                linha, text=item, foreground=self.COR_MUTED,
                background=self.COR_SUAVE, font=("Segoe UI", 8), wraplength=220,
            ).pack(side="left", anchor="w")

    def _montar_metricas(self, pai: ttk.Frame) -> None:
        self.faixa_metricas = ttk.Frame(pai, style="Surface.TFrame")
        self.faixa_metricas.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        self.faixa_metricas.columnconfigure((0, 1, 2, 3), weight=1)
        dados = (
            ("TÍTULOS NA FILA", self.var_titulos),
            ("VALOR TOTAL", self.var_valor),
            ("AVISOS DA PLANILHA", self.var_avisos),
            ("PENDÊNCIAS A REVISAR", self.var_pendencias),
        )
        for indice, (titulo, variavel) in enumerate(dados):
            quadro = ttk.Frame(self.faixa_metricas, style="Soft.TFrame", padding=(14, 6))
            quadro.grid(row=0, column=indice, sticky="ew", padx=(0 if indice == 0 else 7, 0))
            ttk.Label(quadro, text=titulo, style="MetricTitle.TLabel").pack(anchor="w")
            ttk.Label(quadro, textvariable=variavel, style="MetricValue.TLabel").pack(anchor="w", pady=(3, 0))

    def _montar_abas(self, pai: ttk.Frame) -> None:
        self.abas = ttk.Notebook(pai)
        self.abas.grid(row=0, column=0, sticky="nsew")
        self.aba_console = ttk.Frame(self.abas, style="Surface.TFrame", padding=(0, 8, 0, 0))
        self.aba_resultado = ttk.Frame(self.abas, style="Surface.TFrame", padding=(0, 8, 0, 0))
        self.aba_pendencias = ttk.Frame(self.abas, style="Surface.TFrame", padding=(0, 8, 0, 0))
        self.abas.add(self.aba_console, text="Acompanhamento")
        self.abas.add(self.aba_resultado, text="Resultado da execução")
        self.abas.add(self.aba_pendencias, text="Pendências")
        self._montar_console(self.aba_console)
        self._montar_resultado(self.aba_resultado)
        self._montar_pendencias(self.aba_pendencias)

    def _montar_console(self, pai: ttk.Frame) -> None:
        pai.columnconfigure(0, weight=1)
        pai.rowconfigure(2, weight=1, minsize=150)
        titulo = ttk.Frame(pai, style="Surface.TFrame")
        titulo.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(titulo, textvariable=self.var_progresso, style="Section.TLabel").pack(side="left")
        ttk.Button(
            titulo, text="Limpar visualização", command=self._limpar_console,
            style="Secondary.TButton",
        ).pack(side="right")
        self.barra_progresso = ttk.Progressbar(
            pai, style="Progresso.Horizontal.TProgressbar", mode="determinate", maximum=1,
        )
        self.barra_progresso.grid(row=1, column=0, sticky="ew", pady=(0, 6))

        quadro_texto = tk.Frame(pai, bg="#111b28", highlightthickness=0)
        quadro_texto.grid(row=2, column=0, sticky="nsew")
        quadro_texto.columnconfigure(0, weight=1)
        quadro_texto.rowconfigure(0, weight=1)
        self.console = tk.Text(
            quadro_texto, bg="#111b28", fg="#dce7f3", insertbackground="#ffffff",
            selectbackground="#31577f", relief="flat", padx=12, pady=10,
            font=("Cascadia Mono", 9), wrap="word", state="disabled", height=8,
        )
        for tag, cor in self.CORES_CONSOLE.items():
            self.console.tag_configure(tag, foreground=cor)
        self.console.tag_configure("titulo", font=("Cascadia Mono", 9, "bold"))
        barra = ttk.Scrollbar(quadro_texto, orient="vertical", command=self.console.yview)
        self.console.configure(yscrollcommand=barra.set)
        self.console.grid(row=0, column=0, sticky="nsew")
        barra.grid(row=0, column=1, sticky="ns")

    def _montar_resposta(self, pai: ttk.Frame) -> None:
        resposta = ttk.Frame(pai, style="Surface.TFrame")
        resposta.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        resposta.columnconfigure(1, weight=1)
        self.rotulo_resposta = ttk.Label(resposta, text="Responder ao programa:", style="Body.TLabel")
        self.rotulo_resposta.grid(row=0, column=0, padx=(0, 8))
        self.entrada_resposta = ttk.Entry(resposta, textvariable=self.var_resposta, state="disabled")
        self.entrada_resposta.grid(row=0, column=1, sticky="ew")
        self.entrada_resposta.bind("<Return>", lambda _evento: self._enviar_resposta())
        self.botao_responder = ttk.Button(
            resposta, text="Enviar", command=self._enviar_resposta,
            style="Primary.TButton", state="disabled",
        )
        self.botao_responder.grid(row=0, column=2, padx=(8, 0))

    def _nova_tabela(self, pai, colunas: tuple[tuple[str, str, int], ...]) -> ttk.Treeview:
        quadro = ttk.Frame(pai, style="Surface.TFrame")
        quadro.grid(row=1, column=0, sticky="nsew")
        quadro.columnconfigure(0, weight=1)
        quadro.rowconfigure(0, weight=1)
        tabela = ttk.Treeview(
            quadro, columns=[c[0] for c in colunas], show="tree headings",
            selectmode="extended",
        )
        tabela.column("#0", width=22, stretch=False)
        for chave, titulo, largura in colunas:
            tabela.heading(chave, text=titulo, anchor="w")
            tabela.column(chave, width=largura, anchor="w", stretch=chave in {"fornecedor", "detalhe"})
        for cor, fundo in self.FUNDOS_TABELA.items():
            tabela.tag_configure(cor, background=fundo)
        tabela.tag_configure("grupo", font=("Segoe UI Semibold", 9), background=self.COR_SUAVE)
        rolagem = ttk.Scrollbar(quadro, orient="vertical", command=tabela.yview)
        tabela.configure(yscrollcommand=rolagem.set)
        tabela.grid(row=0, column=0, sticky="nsew")
        rolagem.grid(row=0, column=1, sticky="ns")
        return tabela

    def _montar_resultado(self, pai: ttk.Frame) -> None:
        pai.columnconfigure(0, weight=1)
        pai.rowconfigure(1, weight=1)
        topo = ttk.Frame(pai, style="Surface.TFrame")
        topo.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(topo, textvariable=self.var_resultado_resumo, style="Section.TLabel").pack(side="left")
        ttk.Button(
            topo, text="Carregar outro…", command=self._escolher_resultado,
            style="Secondary.TButton",
        ).pack(side="right")
        self.botao_imagem = ttk.Button(
            topo, text="Abrir imagem da falha", command=self._abrir_imagem_resultado,
            style="Secondary.TButton", state="disabled",
        )
        self.botao_imagem.pack(side="right", padx=(0, 6))
        self.tabela_resultado = self._nova_tabela(pai, (
            ("situacao", "Situação", 150),
            ("fornecedor", "Fornecedor", 280),
            ("documento", "Documento", 110),
            ("id", "ID Ongsys", 90),
            ("valor", "Valor", 95),
            ("detalhe", "O que aconteceu", 360),
        ))
        self.tabela_resultado.bind("<<TreeviewSelect>>", lambda _e: self._mostrar_detalhe_resultado())
        self.tabela_resultado.bind("<Double-1>", lambda _e: self._abrir_imagem_resultado())
        ttk.Label(
            pai, textvariable=self.var_resultado_detalhe, style="Body.TLabel",
            wraplength=1100, justify="left",
        ).grid(row=2, column=0, sticky="ew", pady=(6, 0))

    def _montar_pendencias(self, pai: ttk.Frame) -> None:
        pai.columnconfigure(0, weight=1)
        pai.rowconfigure(1, weight=1)
        topo = ttk.Frame(pai, style="Surface.TFrame")
        topo.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(
            topo, text="Títulos de execuções anteriores que pedem sua ação",
            style="Section.TLabel",
        ).pack(side="left")
        ttk.Button(
            topo, text="Atualizar", command=self._carregar_pendencias,
            style="Secondary.TButton",
        ).pack(side="right")
        self.botao_aceitar = ttk.Button(
            topo, text="Marcar como conferido", command=self._aceitar_divergencias,
            style="Primary.TButton", state="disabled",
        )
        self.botao_aceitar.pack(side="right", padx=(0, 6))
        self.tabela_pendencias = self._nova_tabela(pai, (
            ("fornecedor", "Fornecedor", 280),
            ("documento", "Documento", 110),
            ("valor", "Valor", 95),
            ("id", "ID Ongsys", 90),
            ("data", "Data", 90),
            ("detalhe", "Detalhe", 380),
        ))
        self.tabela_pendencias.bind(
            "<<TreeviewSelect>>", lambda _e: self._atualizar_acao_pendencia()
        )
        ttk.Label(
            pai, textvariable=self.var_pendencia_acao, style="Body.TLabel",
            wraplength=1100, justify="left",
        ).grid(row=2, column=0, sticky="ew", pady=(6, 0))

    # -- dados da execução ---------------------------------------------------

    def _selecionar_planilha(self) -> None:
        caminho = filedialog.askopenfilename(
            parent=self,
            title="Selecione a planilha de pagamentos",
            filetypes=[("Planilhas Excel", "*.xlsx *.xlsm"), ("Todos os arquivos", "*.*")],
        )
        if not caminho:
            return
        self.var_planilha.set(caminho)
        try:
            linhas, _mapa, _cabecalho, _aba = planilha.ler_linhas(Path(caminho))
            inicio, fim = planilha.periodo_das_linhas(linhas)
            self.var_inicio.set(formatar_data(inicio))
            self.var_fim.set(formatar_data(fim))
            self.var_status.set("Planilha carregada. Escolha explicitamente a conta pagadora.")
        except Exception as erro:
            self.var_inicio.set("")
            self.var_fim.set("")
            messagebox.showerror("Não foi possível ler a planilha", str(erro), parent=self)

    def _opcoes(self, comando: str, ids: tuple[str, ...] = ()) -> OpcoesPainel | None:
        caminho_texto = self.var_planilha.get().strip()
        if comando in {"planejar", "executar", "auditar", "resolver-pendencia"}:
            if not caminho_texto or not Path(caminho_texto).is_file():
                messagebox.showwarning("Planilha necessária", "Selecione uma planilha válida.", parent=self)
                return None
            if self.var_conta.get() not in self._contas_por_rotulo:
                messagebox.showwarning(
                    "Escolha a conta",
                    "Escolha uma conta pagadora ou selecione explicitamente 'Sem conta pagadora'.",
                    parent=self,
                )
                return None
        try:
            inicio = interpretar_data(self.var_inicio.get())
            fim = interpretar_data(self.var_fim.get())
        except ValueError as erro:
            messagebox.showerror("Data inválida", str(erro), parent=self)
            return None
        if inicio and fim and inicio > fim:
            messagebox.showerror("Período inválido", "A data inicial não pode ser posterior à final.", parent=self)
            return None
        limite: int | None = None
        if self.var_limite.get().strip():
            try:
                limite = int(self.var_limite.get().strip())
                if limite < 1:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Limite inválido", "Informe um número inteiro maior que zero.", parent=self)
                return None
        conta = self._contas_por_rotulo.get(self.var_conta.get())
        return OpcoesPainel(
            comando=comando,
            planilha=Path(caminho_texto) if caminho_texto else None,
            inicio=inicio,
            fim=fim,
            conta=conta,
            fornecedor=self.var_fornecedor.get(),
            limite=limite,
            ids=ids,
        )

    # -- operações -----------------------------------------------------------

    def _planejar(self) -> None:
        opcoes = self._opcoes("planejar")
        if opcoes:
            self._iniciar(opcoes, "Planejando — este modo não abre o Ongsys")

    def _auditar(self) -> None:
        opcoes = self._opcoes("auditar")
        if not opcoes:
            return
        if not messagebox.askokcancel(
            "Auditoria somente leitura",
            "A auditoria abrirá títulos para leitura e comparação. Ela não salvará alterações.\n\nContinuar?",
            parent=self,
        ):
            return
        self._iniciar(opcoes, "Auditando títulos — modo somente leitura")

    def _executar(self) -> None:
        opcoes = self._opcoes("executar")
        if not opcoes or not self._confirmar_sim():
            return
        self._iniciar(
            opcoes,
            "Execução aberta — acompanhe os avisos e digite CONFIRMO somente após revisar a fila",
        )

    def _conferir_id(self, sugestao: str = "") -> None:
        """Mesmo que conferir_id.bat: lê títulos lançados à mão e registra."""
        texto = simpledialog.askstring(
            "Conferir ID",
            "IDs do Ongsys dos títulos lançados à mão (separe por espaço ou vírgula).\n"
            "O ID é o número da primeira coluna da lista, ex.: 00013500.\n\n"
            "Só leitura: nada é alterado no Ongsys.",
            initialvalue=sugestao, parent=self,
        )
        ids = tuple(i for i in re.split(r"[\s,;]+", texto or "") if i)
        if not ids:
            return
        opcoes = self._opcoes("auditar", ids=ids)
        if opcoes:
            self._iniciar(opcoes, f"Conferindo ID(s) {', '.join(ids)} — somente leitura")

    def _liberar_pendencia(self) -> None:
        if not messagebox.askokcancel(
            "Liberar pendência",
            "Use SOMENTE depois de confirmar no Ongsys que o título NÃO existe.\n"
            "Se você lançou à mão, use 'Conferir ID' em vez disto.\n\n"
            "O programa mostrará a pendência e pedirá que você digite LIBERAR.",
            parent=self,
        ):
            return
        opcoes = self._opcoes("resolver-pendencia")
        if opcoes:
            self._iniciar(opcoes, "Liberar pendência — nada é lançado; digite LIBERAR para confirmar")

    def _aceitar_divergencias(self) -> None:
        ids = tuple(
            item.id_ongsys for item in self._pendencias_selecionadas()
            if item.grupo == GRUPO_DIVERGENCIA and item.id_ongsys
        )
        if not ids:
            return
        if not messagebox.askokcancel(
            "Marcar como conferido",
            f"{len(ids)} título(s) serão marcados como conferidos no checkpoint.\n"
            "Faça isso só depois de revisar cada um no Ongsys.\n\n"
            "O programa mostrará a lista e pedirá que você digite ACEITO.",
            parent=self,
        ):
            return
        self._iniciar(
            OpcoesPainel(comando="aceitar-divergencia", ids=ids),
            "Marcar divergências como conferidas — digite ACEITO para confirmar",
        )

    def _situacao(self) -> None:
        self._iniciar(OpcoesPainel(comando="estado"), "Consultando o checkpoint local")

    def _login(self) -> None:
        if not messagebox.askokcancel(
            "Login manual",
            "O navegador será aberto para você entrar diretamente no Ongsys.\n"
            "A automação não lê nem armazena sua senha.",
            parent=self,
        ):
            return
        self._iniciar(
            OpcoesPainel(comando="login"),
            "Faça o login no navegador e depois clique em 'Login concluído' aqui no painel",
        )

    def _confirmar_sim(self) -> bool:
        dialogo = tk.Toplevel(self)
        dialogo.title("Abrir modo de lançamento")
        dialogo.transient(self)
        dialogo.grab_set()
        dialogo.resizable(False, False)
        dialogo.configure(bg=self.COR_PAINEL)
        resultado = {"confirmado": False}
        resposta = tk.StringVar()

        corpo = ttk.Frame(dialogo, style="Surface.TFrame", padding=22)
        corpo.pack(fill="both", expand=True)
        ttk.Label(corpo, text="Confirmação de segurança", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            corpo,
            text=(
                "Este modo pode criar ou atualizar títulos em Contas a Pagar.\n"
                "Ele não baixa, paga, remete ou envia para aprovação.\n\n"
                "Após revisar a fila, o programa ainda exigirá CONFIRMO."
            ),
            style="Body.TLabel", justify="left",
        ).pack(anchor="w", pady=(8, 14))
        ttk.Label(corpo, text="Digite SIM para abrir o modo de lançamento:", style="Section.TLabel").pack(anchor="w")
        entrada = ttk.Entry(corpo, textvariable=resposta, width=38)
        entrada.pack(fill="x", pady=(7, 16))
        botoes = ttk.Frame(corpo, style="Surface.TFrame")
        botoes.pack(fill="x")
        ttk.Button(botoes, text="Cancelar", command=dialogo.destroy, style="Secondary.TButton").pack(side="right")

        def aceitar() -> None:
            if resposta.get().strip().upper() != "SIM":
                messagebox.showwarning("Confirmação incorreta", "Digite exatamente SIM.", parent=dialogo)
                return
            resultado["confirmado"] = True
            dialogo.destroy()

        ttk.Button(botoes, text="Continuar", command=aceitar, style="Primary.TButton").pack(side="right", padx=(0, 8))
        entrada.bind("<Return>", lambda _evento: aceitar())
        entrada.focus_set()
        dialogo.wait_window()
        return resultado["confirmado"]

    # -- processo ------------------------------------------------------------

    def _iniciar(self, opcoes: OpcoesPainel, status: str) -> None:
        if self._processo and self._processo.poll() is None:
            messagebox.showwarning("Operação em andamento", "Aguarde a operação atual terminar.", parent=self)
            return
        comando = montar_comando(opcoes)
        ambiente = os.environ.copy()
        ambiente["PYTHONUTF8"] = "1"
        ambiente["PYTHONIOENCODING"] = "utf-8"
        argumentos: dict = {
            "cwd": str(BASE),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 0,
            "env": ambiente,
        }
        if sys.platform == "win32":
            argumentos["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            self._processo = subprocess.Popen(comando, **argumentos)
        except OSError as erro:
            messagebox.showerror("Não foi possível iniciar", str(erro), parent=self)
            return
        self._comando_em_execucao = opcoes.comando
        self._inicio_execucao = time.time()
        self._buffer_saida = ""
        self._pergunta_avisada = ""
        self.var_progresso.set("")
        self.barra_progresso.configure(value=0, maximum=1)
        self._definir_status(status)
        self.abas.select(self.aba_console)
        self._alterar_estado_execucao(True)
        self._escrever_console("\n" + "═" * 70 + "\n")
        self._escrever_console(f"{status}\n")
        self._escrever_console("═" * 70 + "\n\n")
        threading.Thread(target=self._ler_processo, daemon=True).start()

    def _ler_processo(self) -> None:
        processo = self._processo
        if not processo or not processo.stdout:
            return
        try:
            while True:
                caractere = processo.stdout.read(1)
                if caractere == "":
                    break
                self._fila_saida.put(caractere)
        finally:
            processo.wait()
            self._fila_saida.put(None)

    def _consumir_saida(self) -> None:
        terminou = False
        partes: list[str] = []
        try:
            while True:
                item = self._fila_saida.get_nowait()
                if item is None:
                    terminou = True
                else:
                    partes.append(item)
        except queue.Empty:
            pass
        if partes:
            trecho = "".join(partes)
            self._buffer_saida = (self._buffer_saida + trecho)[-100_000:]
            self._escrever_console(trecho)
            self._atualizar_metricas()
            self._avisar_pergunta()
        if terminou:
            self._ao_terminar()
        self.after(80, self._consumir_saida)

    def _ao_terminar(self) -> None:
        codigo = self._processo.returncode if self._processo else None
        comando = self._comando_em_execucao
        if codigo == 0:
            self._definir_status("Operação concluída")
        else:
            self._definir_status(
                "Operação encerrada — confira as mensagens no Acompanhamento", "StatusErro.TLabel"
            )
        self._alterar_estado_execucao(False)
        self._processo = None
        self._comando_em_execucao = ""
        self._carregar_pendencias()
        if comando in {"executar", "auditar"}:
            caminho = resultado_mais_recente(RELATORIOS, desde=self._inicio_execucao)
            if caminho is not None and self._carregar_resultado(caminho):
                self.abas.select(self.aba_resultado)

    def _definir_status(self, texto: str, estilo: str = "Status.TLabel") -> None:
        self.var_status.set(texto)
        self.rotulo_status.configure(style=estilo)

    def _avisar_pergunta(self) -> None:
        """Destaca quando o programa está parado esperando uma resposta."""
        pergunta = pergunta_pendente(self._buffer_saida)
        if not pergunta or pergunta == self._pergunta_avisada:
            return
        self._pergunta_avisada = pergunta
        self._definir_status(f"O programa aguarda sua resposta: {pergunta}", "StatusPergunta.TLabel")
        self.abas.select(self.aba_console)
        self.entrada_resposta.focus_set()
        self.bell()

    def _atualizar_metricas(self) -> None:
        resumo = extrair_resumo(self._buffer_saida)
        if "titulos" in resumo:
            self.var_titulos.set(resumo["titulos"])
        if "valor" in resumo:
            self.var_valor.set(resumo["valor"])
        if "avisos" in resumo:
            self.var_avisos.set(resumo["avisos"])
        progresso = extrair_progresso(self._buffer_saida)
        if progresso:
            atual, total, nome = progresso
            self.barra_progresso.configure(maximum=max(total, 1), value=atual)
            self.var_progresso.set(f"Título {atual} de {total} — {nome}")

    def _alterar_estado_execucao(self, executando: bool) -> None:
        estado = "disabled" if executando else "normal"
        for botao in self._botoes_operacao:
            botao.configure(state=estado)
        estado_resposta = "normal" if executando else "disabled"
        self.entrada_resposta.configure(state=estado_resposta)
        self.botao_responder.configure(state=estado_resposta)
        self.botao_recolher.configure(state=estado)
        if executando:
            self.botao_aceitar.configure(state="disabled")
            # Durante uma operação, a saída e as perguntas são mais importantes
            # que os filtros já escolhidos. Escondê-los também evita que a área
            # de acompanhamento seja comprimida em telas menores.
            self._recolher_dados()
        else:
            self._mostrar_dados()
            self._atualizar_acao_pendencia()
        if executando and self._comando_em_execucao == "login":
            self.rotulo_resposta.configure(text="Depois de entrar no Ongsys:")
            self.botao_responder.configure(text="Login concluído")
        else:
            self.rotulo_resposta.configure(text="Responder ao programa:")
            self.botao_responder.configure(text="Enviar")
        if executando:
            self.entrada_resposta.focus_set()

    def _enviar_resposta(self) -> None:
        if not self._processo or self._processo.poll() is not None or not self._processo.stdin:
            return
        resposta = self.var_resposta.get()
        try:
            self._processo.stdin.write(resposta + "\n")
            self._processo.stdin.flush()
        except (BrokenPipeError, OSError):
            return
        self._escrever_console(f"{resposta}\n")
        self._buffer_saida += f"{resposta}\n"
        self.var_resposta.set("")
        if self.rotulo_status.cget("style") == "StatusPergunta.TLabel":
            self._definir_status("Resposta enviada — acompanhe a execução")

    def _escrever_console(self, texto: str) -> None:
        """Insere o texto e colore cada linha tocada (falha, atenção, ok...)."""
        self.console.configure(state="normal")
        primeira = int(self.console.index("end-1c").split(".")[0])
        self.console.insert("end", texto)
        ultima = int(self.console.index("end-1c").split(".")[0])
        for numero in range(primeira, ultima + 1):
            inicio, fim = f"{numero}.0", f"{numero}.end"
            for tag in self.CORES_CONSOLE:
                self.console.tag_remove(tag, inicio, fim)
            cor = classificar_linha(self.console.get(inicio, fim))
            if cor:
                self.console.tag_add(cor, inicio, fim)
        self.console.see("end")
        self.console.configure(state="disabled")

    def _limpar_console(self) -> None:
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")
        self._buffer_saida = ""

    # -- aba Resultado -------------------------------------------------------

    def _carregar_ultimo_resultado(self) -> None:
        caminho = resultado_mais_recente(RELATORIOS) if RELATORIOS.exists() else None
        if caminho is not None:
            self._carregar_resultado(caminho)

    def _escolher_resultado(self) -> None:
        caminho = filedialog.askopenfilename(
            parent=self, title="Escolha um relatório de resultado",
            initialdir=str(RELATORIOS), filetypes=[("Resultado", "resultado_*.csv")],
        )
        if caminho:
            self._carregar_resultado(Path(caminho))

    def _carregar_resultado(self, caminho: Path) -> bool:
        try:
            linhas = ler_resultado(caminho)
        except Exception as erro:
            self.var_resultado_resumo.set(f"Não foi possível ler {caminho.name}: {erro}")
            return False
        self._resultado = linhas
        tabela = self.tabela_resultado
        tabela.delete(*tabela.get_children())
        for indice, linha in enumerate(linhas):
            partes = []
            if linha.pendencias:
                partes.append(f"{len(linha.pendencias)} pendência(s) para corrigir")
            if linha.situacao not in {"CONCLUIDO", "SALVO"} and linha.mensagem:
                partes.append(linha.mensagem)
            elif linha.avisos:
                partes.append(linha.avisos[0])
            tabela.insert(
                "", "end", iid=str(indice),
                values=(
                    SITUACOES.get(linha.situacao, (linha.situacao, ""))[0],
                    linha.fornecedor, linha.documento, linha.id_ongsys, linha.valor,
                    " · ".join(partes)[:220],
                ),
                tags=(linha.cor,),
            )
        resumo = resumir_resultado(linhas)
        data = time.strftime("%d/%m/%Y %H:%M", time.localtime(caminho.stat().st_mtime))
        self.var_resultado_resumo.set(
            f"{data} · {len(linhas)} título(s) — "
            f"{resumo['ok']} ok · {resumo['atencao']} com pendência/divergência · "
            f"{resumo['falha']} falha(s) · {resumo['info']} adiado(s)/já existentes"
        )
        self.var_resultado_detalhe.set(
            "Clique numa linha para ver o detalhe. Duplo clique abre a imagem da falha, quando houver."
        )
        self.botao_imagem.configure(state="disabled")
        return True

    def _linha_resultado_selecionada(self) -> LinhaResultado | None:
        selecao = self.tabela_resultado.selection()
        if not selecao:
            return None
        try:
            return self._resultado[int(selecao[0])]
        except (ValueError, IndexError):
            return None

    def _mostrar_detalhe_resultado(self) -> None:
        linha = self._linha_resultado_selecionada()
        if linha is None:
            return
        blocos = [f"{linha.fornecedor} / {linha.documento} — {linha.mensagem or linha.situacao}"]
        if linha.pendencias:
            blocos.append("CORRIGIR NO ONGSYS:\n  • " + "\n  • ".join(linha.pendencias))
        if linha.avisos:
            blocos.append("Informações:\n  • " + "\n  • ".join(linha.avisos))
        self.var_resultado_detalhe.set("\n".join(blocos))
        existe = bool(linha.screenshot) and Path(linha.screenshot).is_file()
        self.botao_imagem.configure(state="normal" if existe else "disabled")

    def _abrir_imagem_resultado(self) -> None:
        linha = self._linha_resultado_selecionada()
        if linha is None or not linha.screenshot or not Path(linha.screenshot).is_file():
            return
        try:
            os.startfile(linha.screenshot)  # type: ignore[attr-defined]
        except OSError as erro:
            messagebox.showerror("Não foi possível abrir a imagem", str(erro), parent=self)

    # -- aba Pendências ------------------------------------------------------

    def _carregar_pendencias(self) -> None:
        """Lê o checkpoint SÓ para exibir; nenhuma alteração é feita aqui."""
        from .checkpoint import Checkpoint

        tabela = self.tabela_pendencias
        tabela.delete(*tabela.get_children())
        self._pendencias_por_item = {}
        try:
            itens = Checkpoint(CHECKPOINT).itens.values() if CHECKPOINT.exists() else []
            pendencias = pendencias_do_checkpoint(itens)
        except Exception as erro:
            self.var_pendencia_acao.set(f"Não foi possível ler o checkpoint: {erro}")
            return
        cores = {GRUPO_SEM_ID: "falha", GRUPO_DIVERGENCIA: "atencao", GRUPO_FALHA: "info"}
        grupos: dict[str, str] = {}
        contagem: dict[str, int] = {}
        for pendencia in pendencias:
            contagem[pendencia.grupo] = contagem.get(pendencia.grupo, 0) + 1
        for grupo in (GRUPO_SEM_ID, GRUPO_DIVERGENCIA, GRUPO_FALHA):
            if contagem.get(grupo):
                grupos[grupo] = tabela.insert(
                    "", "end", text="", open=grupo != GRUPO_FALHA,
                    values=(f"{grupo} ({contagem[grupo]})", "", "", "", "", ACOES[grupo][:120]),
                    tags=("grupo",),
                )
        for pendencia in pendencias:
            iid = tabela.insert(
                grupos[pendencia.grupo], "end",
                values=(
                    pendencia.fornecedor, pendencia.documento, pendencia.valor,
                    pendencia.id_ongsys or "—", pendencia.data, pendencia.detalhe,
                ),
                tags=(cores[pendencia.grupo],),
            )
            self._pendencias_por_item[iid] = pendencia
        a_revisar = contagem.get(GRUPO_SEM_ID, 0) + contagem.get(GRUPO_DIVERGENCIA, 0)
        self.var_pendencias.set(str(a_revisar))
        self.abas.tab(
            self.aba_pendencias,
            text=f"Pendências ({a_revisar})" if a_revisar else "Pendências",
        )
        self._atualizar_acao_pendencia()

    def _pendencias_selecionadas(self) -> list:
        return [
            self._pendencias_por_item[iid]
            for iid in self.tabela_pendencias.selection()
            if iid in self._pendencias_por_item
        ]

    def _atualizar_acao_pendencia(self) -> None:
        selecionadas = self._pendencias_selecionadas()
        aceitaveis = [
            p for p in selecionadas if p.grupo == GRUPO_DIVERGENCIA and p.id_ongsys
        ]
        ocupado = bool(self._processo and self._processo.poll() is None)
        self.botao_aceitar.configure(
            state="normal" if aceitaveis and not ocupado else "disabled"
        )
        if not selecionadas:
            self.var_pendencia_acao.set(
                "Selecione uma ou mais linhas (Ctrl/Shift + clique) para ver o que fazer. "
                "'Marcar como conferido' vale só para títulos salvos com divergência."
            )
            return
        grupos = sorted({p.grupo for p in selecionadas})
        texto = [f"{grupo}: {ACOES[grupo]}" for grupo in grupos]
        if len(selecionadas) == 1:
            p = selecionadas[0]
            texto.insert(0, f"{p.fornecedor} / {p.documento} — {p.detalhe or p.estado}")
        self.var_pendencia_acao.set("\n".join(texto))

    # -- utilidades ----------------------------------------------------------

    def _abrir_relatorios(self) -> None:
        RELATORIOS.mkdir(exist_ok=True)
        try:
            os.startfile(RELATORIOS)  # type: ignore[attr-defined]
        except OSError as erro:
            messagebox.showerror("Não foi possível abrir a pasta", str(erro), parent=self)

    def _ao_fechar(self) -> None:
        if self._processo and self._processo.poll() is None:
            if not messagebox.askyesno(
                "Operação em andamento",
                "Fechar agora interromperá a automação. Um título em andamento poderá exigir auditoria.\n\n"
                "Deseja realmente interromper e fechar?",
                parent=self,
            ):
                return
            self._processo.terminate()
        self.destroy()


def iniciar_painel() -> None:
    aplicativo = PainelOngsys()
    aplicativo.mainloop()
