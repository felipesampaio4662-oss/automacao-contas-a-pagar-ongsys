from datetime import date
from pathlib import Path

from ongsys.painel import OpcoesPainel, extrair_resumo, montar_comando


def test_montar_comando_execucao_nao_responde_confirmacoes():
    opcoes = OpcoesPainel(
        comando="executar",
        planilha=Path("pagamentos.xlsx"),
        inicio=date(2026, 9, 1),
        fim=date(2026, 9, 2),
        conta="0",
        fornecedor="ASSINAFACIL",
        limite=1,
    )

    comando = montar_comando(opcoes, python="python-teste")

    assert comando[0:4] == [
        "python-teste", "-u", str(Path(__file__).resolve().parent.parent / "automacao_ongsys.py"), "executar"
    ]
    assert "--conta" in comando
    assert comando[comando.index("--conta") + 1] == "0"
    assert comando[comando.index("--limite") + 1] == "1"
    assert "SIM" not in comando
    assert "CONFIRMO" not in comando


def test_montar_comando_estado_nao_exige_planilha():
    assert montar_comando(OpcoesPainel(comando="estado"), python="py")[-1] == "estado"


def test_montar_comando_login_nao_recebe_confirmacao_automatica():
    comando = montar_comando(OpcoesPainel(comando="login"), python="py")
    assert comando[-1] == "login"
    assert "SIM" not in comando
    assert "CONFIRMO" not in comando


def test_extrair_resumo_compacto():
    resumo = extrair_resumo("Fila    : 29 titulo(s) | R$ 144.311,59\nAvisos  : 2")
    assert resumo == {"titulos": "29", "valor": "R$ 144.311,59", "avisos": "2"}


def test_extrair_resumo_do_planejar():
    # Saída real do modo Planejar (RESUMO DA CONFERENCIA): sem "Fila :".
    texto = (
        "====================================================================\n"
        "RESUMO DA CONFERENCIA\n"
        "====================================================================\n"
        "Titulos encontrados: 26\n"
        "  liberados        : 26  ->  R$ 145.708,83\n"
        "  bloqueados       : 0  ->  R$ 0,00\n"
        "Valor total        : R$ 111.624,03\n"
        "\n"
        "AVISOS (o lancamento continua permitido):\n"
        "  - ASSINAFACIL / NF 1520037: Vencimento ajustado.\n"
        "  - ASSINAFACIL / NF 1520039: Vencimento ajustado.\n"
        "  - AGUASUL / FAT 08.2026: Vencimento ajustado.\n"
        "====================================================================\n"
    )
    assert extrair_resumo(texto) == {
        "titulos": "26", "valor": "R$ 111.624,03", "avisos": "3",
    }
