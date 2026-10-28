"""Confere, antes de enviar ao GitHub, se algum arquivo leva dado pessoal.

Procura nos arquivos que iriam para o repositório (os já versionados e os novos
que o .gitignore não exclui):
- CPF e CNPJ (com ou sem pontuação), e-mails e telefones;
- IDs de títulos reais do Ongsys;
- nomes de fornecedores reais e pedaços deles (duas palavras seguidas), lidos
  NA HORA dos dados locais (checkpoint, relatórios e config.json). Essa lista
  fica só na memória e nunca é gravada em arquivo.

Uso: python verificar_publicacao.py              (arquivos que iriam no commit)
     python verificar_publicacao.py --commit X   (arquivos de um commit já feito)
Sai com código 1 se encontrar algo. Não altera nenhum arquivo.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

BASE = Path(__file__).resolve().parent

#: Palavras genéricas: um nome feito só delas não identifica ninguém.
GENERICAS = {
    "LTDA", "ME", "EPP", "EIRELI", "MEI", "SA", "SS", "CIA", "COMPANHIA", "DE", "DA", "DO",
    "DOS", "DAS", "E", "EM", "SERVICOS", "SERVICO", "COMERCIO", "CONSULTORIA", "EDUCACAO",
    "EDUCACIONAL", "EDUCACIONAIS", "TECNOLOGIA", "BRASIL", "ASSESSORIA", "LICENCA", "USO",
    "OUTROS", "ESCOLA", "EE", "APM", "INSTITUTO", "ASSOCIACAO", "SOLUCOES", "GESTAO",
    "FINANCEIRO", "CONTAS", "PAGAR", "BANCO", "ITAU", "SANTANDER", "PREPAGO",
    "PRE PAGO", "CARTAO", "PRINCIPAL", "CAIXINHA", "GERAL", "IPTU", "RESTAURANTE",
    "LIMPEZA", "MATERIAL", "ESCRITORIO", "FORNECEDOR", "TESTE", "EXEMPLO", "NEGOCIOS",
    "PARA", "TODOS", "PROF", "CULTURA", "APRENDIZAGENS",
}

#: Empresas públicas e operações bancárias: não são dado pessoal e os testes
#: dependem desses nomes (apelidos e regras de exclusão).
PERMITIDOS_NOMES = {
    "TARIFA BANCARIA", "TRANSFERENCIA ENTRE CONTAS", "RECEBIMENTO DE RECURSOS",
    "APLICACAO CDB", "RENDIMENTO APLICACAO", "RESGATE APLICACAO", "RECARGA CARTAO PREPAGO",
    "IRRF FOLHA PGTO", "USE NOME NA PLANILHA NOME EXATO CADASTRADO NO ONGSYS",
    "LOJA VIRTUAL", "SUPERMERCADO BOM PRECO", "HORTIFRUTI DA VILA",
    "EMPRESA EXEMPLO DE BENEFICIOS BENEFIX", "EMPRESA EXEMPLO DE BENEFICIOS E PAGAMENTOS LTDA",
    "CIA DE SANEAMENTO BASICO REGIONAL AGUASUL",
    "NORTE VIDA COMPANHIA DE SEGUROS", "NORTE VIDA COMPANHIA DE SEGURO SAUDE",
    "TELNET TELECOM", "TELNET TELECOM S A", "MACRO LOJABILL", "MACRO COMPUTADORES LTDA",
    "NUVEMCO CLOUD", "NUVEMCO WEB SERVICES", "LICENCA DE USO NUVEMCO WEB SERVICES",
    "LICENCA DE USO MONITORA", "MEDPLUS SERVICOS DE SAUDE LTDA", "BOA FONTE SERVICOS S A",
    "HOSTIFY COM", "HOSTIFY COM IOF", "APITOOL TEAM", "CODEAI USAGE",
}

#: Pares de palavras comuns que aparecem em nomes de cadastro mas não
#: identificam ninguém (banco, guia de imposto, termos do sistema).
PARES_PERMITIDOS = {"BANCO DIGITAL", "FEITA POR", "PREVISAO PAGAMENTO", "GFR FGTS"}

PADROES = {
    "CPF": re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)"),
    "CNPJ": re.compile(r"(?<!\d)\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}(?!\d)"),
    "CPF/CNPJ sem pontuação": re.compile(r"(?<![\d.,/-])(?:\d{11}|\d{14})(?![\d.,/-])"),
    "e-mail": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "telefone": re.compile(r"\(\d{2}\)\s?9?\d{4}-\d{4}"),
}

#: Valores que podem aparecer: endereços públicos.
PERMITIDOS = {
    "e-mail": {"noreply@anthropic.com"},
}

BINARIOS = (".png", ".jpg", ".jpeg", ".xlsx", ".xlsm", ".xls", ".pptx", ".docx", ".pdf", ".zip")


def sem_acentos(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in texto if not unicodedata.combining(c)).upper()


def palavras(texto: str) -> list[str]:
    return [p for p in re.split(r"[^A-Z0-9]+", sem_acentos(texto)) if p]


def numero_ficticio(numero: str) -> bool:
    """CPF/CNPJ de exemplo: a parte principal é um dígito repetido (11.111.111/0001-11)."""
    digitos = re.sub(r"\D", "", numero)
    base = digitos[:8] if len(digitos) == 14 else digitos[:9]
    return len(set(base)) == 1


def _ler_checkpoint() -> list[dict]:
    checkpoint = BASE / "checkpoint.json"
    if not checkpoint.exists():
        return []
    return json.loads(checkpoint.read_text(encoding="utf-8")).get("itens", [])


def ids_reais() -> set[str]:
    """IDs de títulos reais do Ongsys, lidos do checkpoint local."""
    return {i["id_ongsys"] for i in _ler_checkpoint() if i.get("id_ongsys")}


def nomes_reais() -> set[str]:
    """Nomes de fornecedores dos dados locais, só em memória."""
    nomes: set[str] = {item.get("fornecedor", "") for item in _ler_checkpoint()}
    config = BASE / "config.json"
    if config.exists():
        apelidos = json.loads(config.read_text(encoding="utf-8")).get("apelidos_fornecedores", {})
        nomes.update(k for k in apelidos if not k.startswith("_"))
        nomes.update(apelidos.values())
    for arquivo in (BASE / "relatorios").glob("*.csv"):
        try:
            with open(arquivo, encoding="utf-8-sig", newline="") as f:
                for linha in csv.DictReader(f, delimiter=";"):
                    for campo in ("fornecedor", "fornecedor_selecionado"):
                        nomes.add(linha.get(campo) or "")
        except (OSError, csv.Error, UnicodeDecodeError):
            continue
    limpos = set()
    for nome in nomes:
        nome = re.sub(r"^\s*\d+\s+-\s+", "", nome)            # "3156 - "
        nome = re.split(r"\s+-\s+[\d./-]{11,}", nome)[0]     # " - 111.111.111-11"
        nome = re.sub(r"\b\d{6,}\b", " ", nome)               # CPF colado no nome
        p = [x for x in palavras(nome) if not x.isdigit()]
        distintas = [x for x in p if x not in GENERICAS and len(x) > 2]
        if len(distintas) >= 2 or (len(distintas) == 1 and len(p) >= 3):
            limpos.add(" ".join(p))
    return limpos


def pares_reais(nomes: set[str]) -> set[str]:
    """Duas palavras distintivas seguidas de um nome real ("DENISE CRISTINA").

    Pega pedaços de nome que a busca pelo nome completo não pega. Pares que
    aparecem nos nomes permitidos não contam.
    """
    pares = set()
    for nome in nomes - PERMITIDOS_NOMES:
        p = [x for x in nome.split() if x not in GENERICAS and len(x) > 2]
        for a, b in zip(p, p[1:]):
            pares.add(f"{a} {b}")
    return {
        par for par in pares - PARES_PERMITIDOS
        if not any(f" {par} " in f" {n} " for n in PERMITIDOS_NOMES)
    }


def arquivos(commit: str | None) -> list[str]:
    if commit:
        saida = subprocess.run(["git", "ls-tree", "-r", "--name-only", commit],
                               cwd=BASE, capture_output=True, text=True, check=True).stdout
        return [l for l in saida.splitlines() if l]
    rastreados = subprocess.run(["git", "ls-files"], cwd=BASE, capture_output=True,
                                text=True, check=True).stdout.splitlines()
    novos = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=BASE,
                           capture_output=True, text=True, check=True).stdout.splitlines()
    return sorted({l for l in rastreados + novos if l and (BASE / l).exists()})


def conteudo(caminho: str, commit: str | None) -> str:
    if commit:
        return subprocess.run(["git", "show", f"{commit}:{caminho}"], cwd=BASE,
                              capture_output=True, check=True).stdout.decode("utf-8", "replace")
    return (BASE / caminho).read_bytes().decode("utf-8", "replace")


def verificar(commit: str | None = None) -> list[str]:
    achados: list[str] = []
    nomes = nomes_reais()
    pares = pares_reais(nomes)
    ids = ids_reais()
    for caminho in arquivos(commit):
        proprio = caminho == Path(__file__).name  # tem nomes de empresas públicas,
        # mas os números (CPF, CNPJ, e-mail) dele também são conferidos
        if caminho.lower().endswith(BINARIOS):
            achados.append(f"{caminho}: planilha, imagem ou documento não deve ir para o GitHub")
            continue
        texto = conteudo(caminho, commit)
        for rotulo, padrao in PADROES.items():
            for m in padrao.finditer(texto):
                if m.group(0) in PERMITIDOS.get(rotulo, set()):
                    continue
                if rotulo.startswith(("CPF", "CNPJ")) and numero_ficticio(m.group(0)):
                    continue
                linha = texto.count("\n", 0, m.start()) + 1
                achados.append(f"{caminho}:{linha}: {rotulo}")
        for m in re.finditer(r"(?<!\d)\d{8}(?!\d)", texto):
            if m.group(0) in ids:
                linha = texto.count("\n", 0, m.start()) + 1
                achados.append(f"{caminho}:{linha}: ID de título real do Ongsys")
        if proprio:
            continue
        normalizado = " " + " ".join(palavras(texto)) + " "
        for nome in nomes - PERMITIDOS_NOMES:
            if f" {nome} " in normalizado:
                achados.append(f"{caminho}: nome de fornecedor real ({len(nome.split())} palavras)")
        for par in pares:
            if f" {par} " in normalizado:
                achados.append(f"{caminho}: parte de nome real")
    return achados


def main() -> int:
    commit = sys.argv[sys.argv.index("--commit") + 1] if "--commit" in sys.argv else None
    achados = verificar(commit)
    if not achados:
        print("Nenhum dado pessoal encontrado nos arquivos que iriam para o GitHub.")
        return 0
    print(f"ATENCAO: {len(achados)} ocorrencia(s) que NAO podem ir para o GitHub:")
    for achado in achados:
        print("  -", achado)
    print("\nNada foi enviado. Corrija os arquivos acima antes de publicar.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
